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
library's books change. Removing books from the device asks first (they are deleted from
it, not trashed), runs in a thread and toasts. When the device goes, the page shows Device
Disconnected and asks the window for Home.
"""

import logging
import threading
from gettext import gettext as _
from gettext import ngettext

from gi.repository import Adw, Gio, GLib, Gtk

from ..widgets.cover import Cover
from ..widgets.util import connect_weak
from . import PageListener, app

log = logging.getLogger(__name__)

COVER_WIDTH = 32
KIND_NAMES = {'kobo': 'Kobo', 'kindle': 'Kindle'}


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


def space_text(free, total):
    if not total:
        return _('Free space unknown')
    return _('{free} free of {total}').format(free=GLib.format_size(free),
                                              total=GLib.format_size(total))


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
        self._generation = 0
        self._gone = False
        self._ejecting = False
        connect_weak(self.eject_button, 'clicked', self._on_eject)
        connect_weak(self.select_button, 'toggled', self._on_select_toggled)
        connect_weak(self.add_all_button, 'clicked', self._on_add_all)
        connect_weak(self.remove_button, 'clicked', self._on_remove)
        connect_weak(self.inside_list, 'row-activated', self._on_row_activated)
        connect_weak(self.outside_list, 'row-activated', self._on_row_activated)
        monitor = getattr(app(), 'devices', None)
        if monitor is not None:
            connect_weak(monitor, 'removed', self._on_device_removed)
            connect_weak(monitor, 'changed', self._on_device_changed)
        self._listener = PageListener(self, ('books', 'files'), self._on_library_changed)
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

    def _show_gone(self):
        self._gone = True
        self.set_selection_mode(False)
        self.select_button.set_sensitive(False)
        self.eject_button.set_sensitive(False)
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
            try:
                books = device.list_books()
                error = None
            except OSError as failure:
                books, error = [], failure
            GLib.idle_add(done, books, error)

        def done(books, error):
            page = ref()
            if page is not None and generation == page._generation:
                page._loaded(books, error)
            return GLib.SOURCE_REMOVE

        threading.Thread(target=work, name='bookcase-device-books', daemon=True).start()

    def _loaded(self, books, error):
        if self._gone:
            return
        if error is not None:
            log.warning('reading %s: %s', self.device_id, error)
        self.books = books
        paths = {book.path for book in books}
        self.selected &= paths
        self._match_and_fill()

    def _on_library_changed(self):
        if self.books and not self._gone:
            self._match_and_fill()

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
        self.summary_label.set_text(f'{kind} · {count}')
        self._update_selection()
        self.stack.set_visible_child_name('books')

    def _fill(self, listbox, books, library, in_library):
        listbox.remove_all()
        for number, book in enumerate(books):
            book_id = self.matches.get(book.path)
            listbox.append(self._make_row(number, book, book_id, library, in_library))

    def _make_row(self, number, book, book_id, library, in_library):
        row = Adw.ActionRow(title=book.title, subtitle=describe(book), use_markup=False,
                            title_lines=2, subtitle_lines=1)
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
        files = [Gio.File.new_for_path(path) for path in paths]
        if hasattr(application, 'add_files'):
            application.add_files(files)
        else:
            log.warning('the application cannot add files')

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
        name = device.name
        window = self.get_root()
        operation = Gtk.MountOperation(parent=window if isinstance(window, Gtk.Window)
                                       else None)
        ref = self.weak_ref()

        def done(error):
            page = ref()
            if page is not None:
                page._ejecting = False
                page.eject_button.set_sensitive(not page._gone)
            if error is not None:
                if not error.matches(Gio.io_error_quark(), Gio.IOErrorEnum.FAILED_HANDLED):
                    app().toast(_('Could not eject {device}: {error}').format(
                        device=name, error=error.message))
            else:
                app().toast(_('{device} can be unplugged').format(device=name))

        device.eject(done, operation)

