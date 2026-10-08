# SPDX-License-Identifier: GPL-2.0-or-later
# SPDX-FileCopyrightText: 2026 Jack Tully

"""A connected e-reader's page: its storage, Eject, and the books on it.

    page = DevicePage(device_id)        # a devices.Device's id (the sidebar's device:ID)
    page.refresh()                      # read the device again (in a thread), then show it
    page.set_selection_mode(True)       # check boxes and the Remove… bar

The books are read off the device in a thread (devices.Device.list_books(), cached by file)
and matched to the library on the main thread (Device.match()): those not in the library are
listed first, each with Add (a copy imported through app.add_files) and Add All in the
group's header; those in the library open the book's page. The page reads the device again
on map, when the monitor says its books changed (a send, a removal), and re-matches when the
library's books or shelves change. A Kindle with a documents/My Clippings.txt (over USB or
MTP) offers Import Highlights… (dialogs/highlights.py). Removing books from the device asks
first (they are deleted from it, not trashed), runs in a thread and toasts. Adding a book
from an MTP reader fetches a copy first (into the cache), then imports it.

A Kobo's database is read with the books (kobo.reading_states, read only): a book the Kobo
has opened says so ("Read 45% on Kobo", "Finished on Kobo"), and Bring Reading Progress From
Kobo sets the library's progress (and Finished, an undo step) for the books the Kobo is
further on with. "Sync Shelves as Kobo Collections" (off; per Kobo, in the
`kobo-collections` setting) makes kobo.write_collections keep a collection per shelf, in a
thread, each time the page reads the device or the shelves change while it shows.

"Send Unsent Books on a Shelf" opens Send to Device (dialogs/send.py) with the shelf's books
the device does not hold. The storage card warns when the device is almost full. Eject shows
a spinner, and leaves the page untouchable, until the device has gone or refused.
"""

import logging
import os
import shutil
import tempfile
import threading
import time
from gettext import gettext as _
from gettext import ngettext

from gi.repository import Adw, Gio, GLib, Gtk

from .. import kobo

from ..widgets.cover import Cover
from ..widgets.util import connect_weak
from . import PageListener, app

log = logging.getLogger(__name__)

COVER_WIDTH = 32
KIND_NAMES = {'kobo': 'Kobo', 'kindle': 'Kindle'}
ALMOST_FULL = 200 * 1000 ** 2  # bytes; or a twentieth of the device, whichever is more
COPIES_MAX_AGE = 24 * 3600  # seconds a copy fetched from an MTP reader is kept


class StandIn:
    """What widgets.cover.Cover needs of a book, for a book only on the device: a drawn
    cover with its title and author."""

    def __init__(self, number, title, authors):
        self.id = -1 - number
        self.title = title
        self.authors = authors
        self.has_cover = False
        self.cover_version = 0


def describe(book):
    """'Ada Lark · EPUB · 1.2 MB'."""
    from ..devices import LABELS

    parts = [book.author] if book.author else []
    parts.append(LABELS.get(book.format, book.format.upper()))
    parts.append(GLib.format_size(book.size))
    return ' · '.join(parts)


def almost_full(free, total):
    return bool(total) and free < max(ALMOST_FULL, total // 20)


def space_text(free, total):
    if not total:
        return _('Free space unknown')
    if almost_full(free, total):
        return _('Almost full: {free} free of {total}').format(
            free=GLib.format_size(free), total=GLib.format_size(total))
    return _('{free} free of {total}').format(free=GLib.format_size(free),
                                              total=GLib.format_size(total))


def kobo_text(state):
    """'Read 45% on Kobo', 'Finished on Kobo', or '' for a book the Kobo has not opened."""
    if state is None or state.status == 'unread':
        return ''
    if state.status == 'finished':
        return _('Finished on Kobo')
    # Translators: how far a book is read on a Kobo e-reader.
    return _('Read {percent}% on Kobo').format(percent=state.percent)


def copies_folder():
    """A fresh folder in the cache for books fetched from an MTP reader; folders of earlier
    fetches older than a day go."""
    base = os.path.join(GLib.get_user_cache_dir(), 'bookcase', 'from-device')
    os.makedirs(base, exist_ok=True)
    for name in os.listdir(base):
        path = os.path.join(base, name)
        try:
            if time.time() - os.path.getmtime(path) > COPIES_MAX_AGE:
                shutil.rmtree(path, ignore_errors=True)
        except OSError:
            continue
    return tempfile.mkdtemp(dir=base)


@Gtk.Template(resource_path='/io/github/jackicus/Bookcase/device.ui')
class DevicePage(Adw.NavigationPage):
    __gtype_name__ = 'BookcaseDevicePage'

    eject_button = Gtk.Template.Child()
    select_button = Gtk.Template.Child()
    stack = Gtk.Template.Child()
    gone_status = Gtk.Template.Child()
    kind_image = Gtk.Template.Child()
    name_label = Gtk.Template.Child()
    summary_label = Gtk.Template.Child()
    space_bar = Gtk.Template.Child()
    space_label = Gtk.Template.Child()
    empty_status = Gtk.Template.Child()
    clippings_group = Gtk.Template.Child()
    clippings_row = Gtk.Template.Child()
    kobo_group = Gtk.Template.Child()
    collections_row = Gtk.Template.Child()
    progress_row = Gtk.Template.Child()
    progress_button = Gtk.Template.Child()
    send_group = Gtk.Template.Child()
    shelf_row = Gtk.Template.Child()
    shelf_button = Gtk.Template.Child()
    outside_group = Gtk.Template.Child()
    add_all_button = Gtk.Template.Child()
    outside_list = Gtk.Template.Child()
    inside_group = Gtk.Template.Child()
    inside_list = Gtk.Template.Child()
    selection_bar = Gtk.Template.Child()
    selection_label = Gtk.Template.Child()
    remove_button = Gtk.Template.Child()

    def __init__(self, device_id):
        self.device_id = device_id
        super().__init__(tag=f'device-{device_id}')
        self.books = []  # [devices.DeviceBook], as last read
        self.matches = {}  # path -> library book id or None
        self.selected = set()  # paths
        self.kobo_states = {}  # ContentID -> kobo.State, as last read
        self.kobo_db = None  # the Kobo's database path, when it has one
        self.has_clippings = False
        self.collections_status = ''  # what the last collections sync did, for the row
        self._generation = 0
        self._gone = False
        self._ejecting = False
        self._read_afresh = False
        self._syncing = False  # a collections sync running
        self._sync_again = False  # asked for while one ran
        self._setting_switch = False
        self._settings_handler = None
        actions = Gio.SimpleActionGroup()
        send_shelf = Gio.SimpleAction(name='send-shelf', parameter_type=GLib.VariantType('x'))
        connect_weak(send_shelf, 'activate', self._on_send_shelf)
        actions.add_action(send_shelf)
        self.insert_action_group('device', actions)
        connect_weak(self.collections_row, 'notify::active', self._on_collections_toggled)
        connect_weak(self.progress_button, 'clicked', self._on_bring_progress)
        connect_weak(self.eject_button, 'clicked', self._on_eject)
        connect_weak(self.select_button, 'toggled', self._on_select_toggled)
        connect_weak(self.add_all_button, 'clicked', self._on_add_all)
        connect_weak(self.remove_button, 'clicked', self._on_remove)
        connect_weak(self.clippings_row, 'activated', self._on_import_clippings)
        connect_weak(self.inside_list, 'row-activated', self._on_row_activated)
        connect_weak(self.outside_list, 'row-activated', self._on_row_activated)
        monitor = getattr(app(), 'devices', None)
        if monitor is not None:
            connect_weak(monitor, 'removed', self._on_device_removed)
            connect_weak(monitor, 'changed', self._on_device_changed)
        self._listener = PageListener(
            self, ('books', 'files', 'progress', 'shelves', 'shelf_books'),
            self._on_library_changed)
        self.connect('map', self._on_map)
        self.stack.set_visible_child_name('loading')
        self._show_device()

    # -- the device --------------------------------------------------------------------------

    @property
    def device(self):
        monitor = getattr(app(), 'devices', None)
        return monitor.device(self.device_id) if monitor is not None else None

    def _show_device(self):
        device = self.device
        if device is None:
            self._show_gone()
            return
        self.set_title(device.name)
        self.name_label.set_text(device.name)
        free, total = device.space()
        self.space_bar.set_value((total - free) / total if total else 0)
        self.space_bar.set_visible(bool(total))
        self.space_label.set_text(space_text(free, total))
        if almost_full(free, total):
            self.space_label.add_css_class('warning')
        else:
            self.space_label.remove_css_class('warning')
        self.clippings_group.set_visible(self.has_clippings)
        self._show_collections_switch()

    def _show_collections_switch(self):
        device = self.device
        settings = getattr(app(), 'settings', None)
        on = (device is not None and settings is not None
              and device.key in settings.get_strv('kobo-collections'))
        if self.collections_row.get_active() != on:
            self._setting_switch = True
            self.collections_row.set_active(on)
            self._setting_switch = False

    def _show_gone(self):
        self._gone = True
        self.set_selection_mode(False)
        self.select_button.set_sensitive(False)
        self.eject_button.set_sensitive(False)
        self.eject_button.set_icon_name('media-eject-symbolic')
        self.stack.set_visible_child_name('gone')

    def _on_device_removed(self, _monitor, device_id):
        if device_id != self.device_id:
            return
        self._show_gone()
        window = self.get_root()
        if self.get_mapped() and window is not None and hasattr(window, 'show_root'):
            window.show_root('home')

    def _on_device_changed(self, _monitor, device_id):
        if device_id == self.device_id and self.get_mapped():
            self.refresh()

    def _on_import_clippings(self, *_args):
        device = self.device
        if device is None:
            return
        from ..dialogs import highlights

        text = device.read_clippings(highlights.MAX_CLIPPINGS_BYTES)
        if text is None:
            app().toast(_('Could not read the highlights'))
            return
        highlights.present_import(app(), self, text)

    # -- reading the books -------------------------------------------------------------------

    def _on_map(self, *_args):
        self.refresh()

    def refresh(self):
        device = self.device
        if device is None:
            self._show_gone()
            return
        self._show_device()
        self._generation += 1
        generation = self._generation
        ref = self.weak_ref()

        def work():
            from .. import devices

            try:
                books = device.list_books()
                error = None
            except (OSError, devices.DeviceError) as failure:
                books, error = [], failure
            extras = {'clippings': device.has_clippings(), 'kobo_db': device.kobo_database(),
                      'states': {}}
            if extras['kobo_db']:
                try:
                    extras['states'] = kobo.reading_states(extras['kobo_db'])
                except kobo.KoboError as failure:
                    log.info('reading the Kobo: %s', failure)
            GLib.idle_add(done, books, error, extras)

        def done(books, error, extras):
            page = ref()
            if page is not None and generation == page._generation:
                page._loaded(books, error, extras)
            return GLib.SOURCE_REMOVE

        threading.Thread(target=work, name='bookcase-device-books', daemon=True).start()

    def _loaded(self, books, error, extras=None):
        if self._gone:
            return
        if error is not None:
            log.warning('reading %s: %s', self.device_id, error)
        extras = extras or {}
        self.has_clippings = extras.get('clippings', False)
        self.kobo_db = extras.get('kobo_db')
        self.kobo_states = extras.get('states', {})
        self.clippings_group.set_visible(self.has_clippings)
        self.books = books
        self._read_afresh = True
        paths = {book.path for book in books}
        self.selected &= paths
        self._match_and_fill()

    def _on_library_changed(self):
        if self.books and not self._gone:
            self._match_and_fill()
            self.sync_collections()

    def _match_and_fill(self):
        device = self.device
        if device is None:
            self._show_gone()
            return
        library = app().library
        self.matches = device.match(library, self.books)
        outside = [book for book in self.books if self.matches.get(book.path) is None]
        inside = [book for book in self.books if self.matches.get(book.path) is not None]
        self._fill(self.outside_list, outside, library, in_library=False)
        self._fill(self.inside_list, inside, library, in_library=True)
        self.outside_group.set_visible(bool(outside))
        self.inside_group.set_visible(bool(inside))
        self.add_all_button.set_visible(len(outside) > 1)
        self.empty_status.set_visible(not self.books)
        self.select_button.set_sensitive(bool(self.books))
        if not self.books:
            self.set_selection_mode(False)
        free, total = device.space()
        kind = KIND_NAMES.get(device.kind, _('E-reader'))
        count = ngettext('{count} book', '{count} books', len(self.books)).format(
            count=len(self.books))
        self.summary_label.set_text(_('Ejecting…') if self._ejecting else f'{kind} · {count}')
        self._update_selection()
        self._update_kobo(device, library)
        self._update_shelves(library)
        self.stack.set_visible_child_name('books')
        if self._read_afresh:  # the books were just read off the device
            self._read_afresh = False
            self.sync_collections()

    def _fill(self, listbox, books, library, in_library):
        listbox.remove_all()
        for number, book in enumerate(books):
            book_id = self.matches.get(book.path)
            listbox.append(self._make_row(number, book, book_id, library, in_library))

    def _state(self, book):
        device = self.device
        if not self.kobo_states or device is None:
            return None
        return self.kobo_states.get(device.content_id(book))

    def _make_row(self, number, book, book_id, library, in_library):
        subtitle = describe(book)
        progress = kobo_text(self._state(book))
        if progress:
            subtitle = f'{subtitle}\n{progress}'
        row = Adw.ActionRow(title=book.title, subtitle=subtitle, use_markup=False,
                            title_lines=2, subtitle_lines=2)
        row.path = book.path
        row.book_id = book_id
        check = Gtk.CheckButton(valign=Gtk.Align.CENTER, visible=self._selecting())
        check.set_active(book.path in self.selected)
        check.update_property([Gtk.AccessibleProperty.LABEL], [_('Select')])
        check.path = book.path
        connect_weak(check, 'toggled', self._on_check_toggled)
        row.check = check
        row.add_prefix(check)
        cover = Cover(width=COVER_WIDTH, valign=Gtk.Align.CENTER)
        cover.set_margin_top(6)
        cover.set_margin_bottom(6)
        library_book = library.book(book_id) if book_id is not None else None
        cover.set_book(library_book or StandIn(number, book.title, book.authors))
        row.add_prefix(cover)
        if in_library:
            arrow = Gtk.Image(icon_name='go-next-symbolic', accessible_role=(
                Gtk.AccessibleRole.PRESENTATION))
            row.add_suffix(arrow)
            row.arrow = arrow
            row.set_activatable(True)
        else:
            add = Gtk.Button(label=_('Add'), valign=Gtk.Align.CENTER,
                             tooltip_text=_('Add to Library'))
            add.add_css_class('flat')
            add.path = book.path
            connect_weak(add, 'clicked', self._on_add)
            row.add_suffix(add)
            row.add_button = add
            row.set_activatable(self._selecting())
        self._row_mode(row)
        return row

    def _row_mode(self, row):
        selecting = self._selecting()
        row.check.set_visible(selecting)
        if hasattr(row, 'arrow'):
            row.arrow.set_visible(not selecting)
        if hasattr(row, 'add_button'):
            row.add_button.set_visible(not selecting)
            row.set_activatable(selecting)

    # -- selection ---------------------------------------------------------------------------

    def _selecting(self):
        return self.select_button.get_active()

    def set_selection_mode(self, on):
        if self.select_button.get_active() != on:
            self.select_button.set_active(on)  # _on_select_toggled follows

    def _on_select_toggled(self, *_args):
        if not self._selecting():
            self.selected.clear()
        for listbox in (self.outside_list, self.inside_list):
            row = listbox.get_first_child()
            while row is not None:
                if hasattr(row, 'check'):
                    row.check.set_active(row.path in self.selected)
                    self._row_mode(row)
                row = row.get_next_sibling()
        self._update_selection()

    def _on_check_toggled(self, check):
        path = check.path
        if check.get_active():
            self.selected.add(path)
        else:
            self.selected.discard(path)
        self._update_selection()

    def _update_selection(self):
        selecting = self._selecting()
        self.selection_bar.set_revealed(selecting)
        count = len(self.selected)
        self.selection_label.set_text(
            ngettext('{count} selected', '{count} selected', count).format(count=count)
            if count else _('Select books to remove'))
        self.remove_button.set_sensitive(count > 0)

    def _on_row_activated(self, _listbox, row):
        if self._selecting():
            row.check.set_active(not row.check.get_active())
            return
        window = self.get_root()
        if row.book_id is not None and hasattr(window, 'show_book'):
            window.show_book(row.book_id)

    # -- adding ------------------------------------------------------------------------------

    def _add_paths(self, paths):
        application = app()
        if not hasattr(application, 'add_files'):
            log.warning('the application cannot add files')
            return
        device = self.device
        if device is None or device.local:
            application.add_files([Gio.File.new_for_path(path) for path in paths])
            return

        def work():
            from .. import devices

            copies, errors = [], []
            folder = copies_folder()
            for path in paths:
                try:
                    copies.append(device.fetch(path, folder))
                except devices.DeviceError as error:
                    errors.append(error)
            GLib.idle_add(done, copies, errors)

        def done(copies, errors):
            if errors:
                app().toast(_('Could not copy {count} of the books from {device}: {error}')
                            .format(count=len(errors), device=device.name, error=errors[0]))
            if copies:
                app().add_files([Gio.File.new_for_path(path) for path in copies])
            return GLib.SOURCE_REMOVE

        threading.Thread(target=work, name='bookcase-device-fetch', daemon=True).start()

    def _on_add(self, button):
        button.set_sensitive(False)
        self._add_paths([button.path])

    def _on_add_all(self, *_args):
        paths = [book.path for book in self.books if self.matches.get(book.path) is None]
        if paths:
            self._add_paths(paths)

    # -- removing ----------------------------------------------------------------------------

    def _on_remove(self, *_args):
        self.present_remove_dialog()

    def present_remove_dialog(self):
        """Ask before deleting the selected books from the device; returns the dialog."""
        device = self.device
        paths = sorted(self.selected)
        if device is None or not paths:
            return None
        count = len(paths)
        heading = ngettext('Remove {count} Book From {device}?',
                           'Remove {count} Books From {device}?', count).format(
            count=count, device=device.name)
        dialog = Adw.AlertDialog(
            heading=heading,
            body=_('They are deleted from the e-reader. Your library is not changed.'))
        dialog.add_response('cancel', _('_Cancel'))
        dialog.add_response('remove', _('_Remove'))
        dialog.set_response_appearance('remove', Adw.ResponseAppearance.DESTRUCTIVE)
        dialog.set_default_response('cancel')
        dialog.set_close_response('cancel')
        dialog.paths = paths
        connect_weak(dialog, 'response', self._on_remove_response)
        window = self.get_root()
        if hasattr(window, 'set_dialog_open'):
            window.set_dialog_open(True)
            dialog.connect('closed', lambda *_args: window.set_dialog_open(False))
        dialog.present(window)
        return dialog

    def _on_remove_response(self, dialog, response):
        if response == 'remove':
            self.remove_books(dialog.paths)

    def remove_books(self, paths):
        device = self.device
        if device is None:
            return
        self.remove_button.set_sensitive(False)
        name = device.name
        device_id = self.device_id
        ref = self.weak_ref()

        def work():
            removed, errors = 0, []
            for path in paths:
                try:
                    device.remove(path)
                    removed += 1
                except Exception as error:  # each is reported
                    errors.append(error)
            GLib.idle_add(done, removed, errors)

        def done(removed, errors):
            application = app()
            if errors:
                log.warning('removing from %s: %s', name, errors)
                application.toast(_('Could not remove {count} of the books: {error}').format(
                    count=len(errors), error=errors[0]))
            elif removed:
                application.toast(ngettext('Removed {count} book from {device}',
                                           'Removed {count} books from {device}',
                                           removed).format(count=removed, device=name))
            page = ref()
            if page is not None:
                page.set_selection_mode(False)
            monitor = getattr(application, 'devices', None)
            if monitor is not None:
                monitor.books_changed(device_id)
            return GLib.SOURCE_REMOVE

        threading.Thread(target=work, name='bookcase-device-remove', daemon=True).start()

    # -- ejecting ----------------------------------------------------------------------------

    def _on_eject(self, *_args):
        self.eject()

    def eject(self):
        device = self.device
        if device is None or self._ejecting:
            return
        self._ejecting = True
        self.eject_button.set_sensitive(False)
        self.eject_button.set_child(Adw.Spinner())
        self.eject_button.set_tooltip_text(_('Ejecting…'))
        self.summary_label.set_text(_('Ejecting…'))
        self.set_selection_mode(False)
        for widget in (self.select_button, self.kobo_group, self.send_group,
                       self.outside_group, self.inside_group):
            widget.set_sensitive(False)
        name = device.name
        window = self.get_root()
        operation = Gtk.MountOperation(parent=window if isinstance(window, Gtk.Window)
                                       else None)
        ref = self.weak_ref()

        def done(error):
            page = ref()
            if page is not None:
                page._ejecting = False
                page.eject_button.set_icon_name('media-eject-symbolic')
                page.eject_button.set_tooltip_text(_('Eject'))
                page.eject_button.set_sensitive(not page._gone)
                if not page._gone:
                    for widget in (page.select_button, page.kobo_group, page.send_group,
                                   page.outside_group, page.inside_group):
                        widget.set_sensitive(True)
                    page.select_button.set_sensitive(bool(page.books))
                    page._match_and_fill()
            if error is not None:
                if not error.matches(Gio.io_error_quark(), Gio.IOErrorEnum.FAILED_HANDLED):
                    app().toast(_('Could not eject {device}: {error}').format(
                        device=name, error=error.message))
            else:
                app().toast(_('{device} can be unplugged').format(device=name))

        device.eject(done, operation)


    # -- a Kobo's database -------------------------------------------------------------------

    def _update_kobo(self, device, library):
        is_kobo = device.kind == 'kobo' and device.local
        self.kobo_group.set_visible(is_kobo and self.kobo_db is not None)
        if not is_kobo:
            return
        self.collections_row.set_subtitle(
            self.collections_status
            or _('Writes the Kobo’s database, with a backup beside it'))
        count = len(self.progress_changes(library))
        self.progress_row.set_visible(count > 0)
        self.progress_row.set_subtitle(ngettext(
            '{count} book is further along on the Kobo',
            '{count} books are further along on the Kobo', count).format(count=count))

    def progress_changes(self, library):
        """{book id: (status, fraction)} for the library books the Kobo is further on with."""
        changes = {}
        for book in self.books:
            book_id = self.matches.get(book.path)
            state = self._state(book)
            if book_id is None or state is None:
                continue
            library_book = library.book(book_id)
            change = kobo.ahead(library_book, state) if library_book is not None else None
            if change is not None and (book_id not in changes
                                       or change[1] > changes[book_id][1]):
                changes[book_id] = change
        return changes

    def _on_bring_progress(self, *_args):
        self.bring_progress()

    def bring_progress(self):
        """Set the library's progress (and Finished) from the Kobo's, for the books it is
        further on with; a toast says how many."""
        device = self.device
        library = app().library
        changes = self.progress_changes(library)
        if device is None or not changes:
            return
        finished = [book_id for book_id, (status, _f) in changes.items() if status == 'finished']
        for book_id, (status, fraction) in changes.items():
            if status != 'finished':
                library.set_progress(book_id, fraction, '')  # '' opens at the fraction
        if finished:
            library.set_status(finished, 'finished')
        app().toast(ngettext('Brought reading progress for {count} book from {device}',
                             'Brought reading progress for {count} books from {device}',
                             len(changes)).format(count=len(changes), device=device.name),
                    undo=bool(finished))
        self._match_and_fill()

    def _on_collections_toggled(self, *_args):
        if self._setting_switch:
            return
        device = self.device
        settings = getattr(app(), 'settings', None)
        if device is None or settings is None:
            return
        keys = [key for key in settings.get_strv('kobo-collections') if key != device.key]
        if self.collections_row.get_active():
            keys.append(device.key)
        settings.set_strv('kobo-collections', keys)
        self.collections_status = ''
        if self.collections_row.get_active():
            self.sync_collections()
        else:
            self._update_kobo(device, app().library)

    def collections_wanted(self, library):
        """(shelf names, {name: ContentIDs on it}, ContentIDs of the books Bookcase knows)
        for the books on this Kobo."""
        device = self.device
        by_id = {}
        for book in self.books:
            book_id = self.matches.get(book.path)
            if book_id is not None:
                by_id.setdefault(book_id, []).append(device.content_id(book))
        known = {content for contents in by_id.values() for content in contents}
        names, wanted = [], {}
        for shelf in library.shelves():
            names.append(shelf.name)
            on_shelf = set(library.book_ids(shelf=shelf.id)) & by_id.keys()
            wanted[shelf.name] = {content for book_id in on_shelf
                                  for content in by_id[book_id]}
        return names, wanted, known

    def sync_collections(self):
        """Write the shelves as the Kobo's collections, in a thread, when this Kobo has it
        on; runs again after the one running when asked meanwhile."""
        device = self.device
        if (device is None or self._gone or self._ejecting or self.kobo_db is None
                or not self.collections_row.get_active() or not self.books):
            return
        if self._syncing:
            self._sync_again = True
            return
        names, wanted, known = self.collections_wanted(app().library)
        path = self.kobo_db
        name = device.name
        self._syncing = True
        ref = self.weak_ref()

        def work():
            try:
                changes, error = kobo.write_collections(path, names, wanted, known), None
            except kobo.KoboError as failure:
                changes, error = None, failure
            except Exception as failure:  # reported, never left running
                log.exception('Kobo collections')
                changes, error = None, failure
            GLib.idle_add(done, changes, error)

        def done(changes, error):
            page = ref()
            if error is not None:
                app().toast(_('Could not update the collections on {device}: {error}').format(
                    device=name, error=error))
            elif changes:
                app().toast(_('Updated the collections on {device}').format(device=name))
            if page is not None:
                page._synced(changes, error)
            return GLib.SOURCE_REMOVE

        threading.Thread(target=work, name='bookcase-kobo-collections', daemon=True).start()

    def _synced(self, changes, error):
        self._syncing = False
        if error is not None:
            self.collections_status = str(error)
        elif changes is not None and changes.waiting:
            count = len(changes.waiting)
            self.collections_status = ngettext(
                '{count} book joins its collections once the Kobo has added it: eject the '
                'Kobo, let it finish, and connect it again',
                '{count} books join their collections once the Kobo has added them: eject '
                'the Kobo, let it finish, and connect it again', count).format(count=count)
        else:
            self.collections_status = _('Each shelf is a collection on this Kobo')
        device = self.device
        if device is not None and not self._gone:
            self._update_kobo(device, app().library)
        if self._sync_again:
            self._sync_again = False
            self.sync_collections()

    # -- a shelf's unsent books --------------------------------------------------------------

    def unsent(self, library):
        """[(Shelf, [book ids not on the device])] for the library's shelves."""
        device = self.device
        on_device = device.book_ids if device is not None else set()
        return [(shelf, [book_id for book_id in library.book_ids(shelf=shelf.id)
                         if book_id not in on_device])
                for shelf in library.shelves()]

    def _update_shelves(self, library):
        shelves = self.unsent(library) if hasattr(library, 'shelves') else []
        self.send_group.set_visible(bool(shelves))
        menu = Gio.Menu()
        for shelf, book_ids in shelves:
            if book_ids:
                # Translators: a shelf's name and how many of its books are not on the device.
                item = Gio.MenuItem.new(_('{shelf} ({count})').format(
                    shelf=shelf.name, count=len(book_ids)), None)
                item.set_action_and_target_value('device.send-shelf',
                                                 GLib.Variant('x', shelf.id))
                menu.append_item(item)
        self.shelf_button.set_menu_model(menu)
        self.shelf_button.set_sensitive(menu.get_n_items() > 0)
        self.shelf_row.set_subtitle(
            _('Copies the books of a shelf that are not on this device')
            if menu.get_n_items() else _('Every shelved book is on this device'))

    def _on_send_shelf(self, _action, parameter):
        self.send_shelf(parameter.get_int64())

    def send_shelf(self, shelf_id):
        """Open Send to Device with the shelf's books this device does not hold; returns the
        dialog, or None when there are none."""
        library = app().library
        book_ids = next((ids for shelf, ids in self.unsent(library) if shelf.id == shelf_id),
                        [])
        if not book_ids:
            return None
        from ..dialogs import send

        dialog = send.present(app(), self.get_root(), book_ids)
        device = self.device
        if device is not None and device in dialog.devices:
            dialog.choose(device)
        return dialog
