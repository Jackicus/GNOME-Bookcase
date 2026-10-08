# SPDX-License-Identifier: GPL-2.0-or-later
# SPDX-FileCopyrightText: 2026 Jack Tully

"""What can be done to books, wherever they are shown: the `book.*` action group a page puts
on itself, and the menu that names them (a grid's context menu, the details page's More menu).

    actions = BookActions(page, get_ids, shelf_id=None)   # inserted as 'book' on the page
    actions.update()                       # enable what fits the books get_ids() returns
    add_to_shelf(shelf_id, ids); set_status(ids, status)   # with their toasts
    book_menu(app.library, shelf_id=None, details=True)   # a Gio.Menu of the actions
    popup_menu(widget, model, x, y)        # a context menu at a point of a widget

Actions: read, details, edit, add-to-shelf (a shelf id), new-shelf, remove-from-shelf (on a
manual shelf's page), mark-reading, mark-finished, mark-unread, send, export, show-in-files,
remove, trash, select-all (when the page gives `select_all`). Each change goes through the
library's undoable methods and toasts with Undo; Move to Trash asks first (and trashes the
books' files through Gio, recoverable, then removes the books); the books of a linked Calibre
library are never trashed (their files are Calibre's).
"""

import logging
import threading
from gettext import gettext as _
from gettext import ngettext

from gi.repository import Adw, Gdk, Gio, GLib, Gtk

from . import app

log = logging.getLogger(__name__)

MAX_OPEN = 8  # readers opened at once from a selection


def _title(library, book_ids):
    book = library.book(book_ids[0]) if len(book_ids) == 1 else None
    return book.title if book is not None else None


def book_menu(library, shelf_id=None, details=True, read=True):
    """The book actions as a menu model: Read, Details; Edit Details…, Add to Shelf ▸; the
    reading state; Send to Device…, Export…, Show in Files; Remove from Library, Move to
    Trash…. Add to Shelf lists the manual shelves now."""
    menu = Gio.Menu()
    first = Gio.Menu()
    if read:
        first.append(_('_Read'), 'book.read')
    if details:
        first.append(_('_Details'), 'book.details')
    if first.get_n_items():
        menu.append_section(None, first)
    edit = Gio.Menu()
    edit.append(_('_Edit Details…'), 'book.edit')
    shelves = Gio.Menu()
    for shelf in library.shelves():
        if shelf.query is None and shelf.id != shelf_id:
            item = Gio.MenuItem.new(shelf.name.replace('_', '__'), None)
            item.set_action_and_target_value('book.add-to-shelf', GLib.Variant('x', shelf.id))
            shelves.append_item(item)
    new = Gio.Menu()
    new.append(_('_New Shelf…'), 'book.new-shelf')
    shelves.append_section(None, new)
    edit.append_submenu(_('Add to _Shelf'), shelves)
    if shelf_id is not None:
        edit.append(_('Remove from S_helf'), 'book.remove-from-shelf')
    menu.append_section(None, edit)
    status = Gio.Menu()
    status.append(_('Mark as _Reading'), 'book.mark-reading')
    status.append(_('Mark as _Finished'), 'book.mark-finished')
    status.append(_('Mark as _Unread'), 'book.mark-unread')
    menu.append_section(None, status)
    out = Gio.Menu()
    out.append(_('Send to _Device…'), 'book.send')
    out.append(_('E_xport…'), 'book.export')
    out.append(_('Show in _Files'), 'book.show-in-files')
    menu.append_section(None, out)
    remove = Gio.Menu()
    remove.append(_('Re_move from Library'), 'book.remove')
    remove.append(_('Move to _Trash…'), 'book.trash')
    menu.append_section(None, remove)
    return menu


def popup_menu(widget, model, x, y):
    """A context menu of `model` pointing at (x, y) in `widget`; returns the popover (made
    once per widget, kept as widget.context_popover)."""
    popover = getattr(widget, 'context_popover', None)
    if popover is None:
        popover = Gtk.PopoverMenu(has_arrow=False, halign=Gtk.Align.START)
        popover.set_parent(widget)
        widget.context_popover = popover
    popover.set_menu_model(model)
    rectangle = Gdk.Rectangle()
    rectangle.x, rectangle.y, rectangle.width, rectangle.height = int(x), int(y), 1, 1
    popover.set_pointing_to(rectangle)
    popover.popup()
    return popover


class BookActions:
    """The `book` action group on `widget`, acting on the books `get_ids()` returns."""

    NAMES = ('read', 'details', 'edit', 'new-shelf', 'remove-from-shelf', 'mark-reading',
             'mark-finished', 'mark-unread', 'send', 'export', 'show-in-files', 'remove',
             'trash')

    def __init__(self, widget, get_ids, shelf_id=None, select_all=None, details=None):
        self._widget = widget.weak_ref()
        # get_ids is usually a method of the widget, which holds this through its action
        # group: held weakly, so the widget can go.
        owner = getattr(get_ids, '__self__', None)
        if owner is not None and hasattr(owner, 'weak_ref'):
            owner_ref, function = owner.weak_ref(), get_ids.__func__

            def get_ids():
                instance = owner_ref()
                return function(instance) if instance is not None else []

        self.get_ids = get_ids
        self.shelf_id = shelf_id
        self.group = Gio.SimpleActionGroup()
        for name in self.NAMES:
            action = Gio.SimpleAction.new(name, None)
            action.connect('activate', self._activate, name.replace('-', '_'))
            self.group.add_action(action)
        add = Gio.SimpleAction.new('add-to-shelf', GLib.VariantType.new('x'))
        add.connect('activate', lambda _action, value: self.add_to_shelf(value.get_int64()))
        self.group.add_action(add)
        if select_all is not None:
            action = Gio.SimpleAction.new('select-all', None)
            action.connect('activate', lambda *_args: select_all())
            self.group.add_action(action)
        self._details = details
        widget.insert_action_group('book', self.group)
        self.update()

    def _activate(self, _action, _value, name):
        ids = list(self.get_ids())
        if ids:
            getattr(self, name)(ids)

    @property
    def widget(self):
        return self._widget()

    def _window(self):
        widget = self.widget
        root = widget.get_root() if widget is not None else None
        return root if root is not None else app().window()

    def update(self):
        ids = list(self.get_ids())
        library = app().library
        books = [book for book in (library.book(book_id) for book_id in ids[:200]) if book]
        some = bool(books)
        for name in self.NAMES:
            self.group.lookup_action(name).set_enabled(some)
        self.group.lookup_action('add-to-shelf').set_enabled(some)
        self.group.lookup_action('remove-from-shelf').set_enabled(
            some and self.shelf_id is not None)
        self.group.lookup_action('details').set_enabled(len(ids) == 1)
        self.group.lookup_action('show-in-files').set_enabled(
            len(ids) == 1 and not books[0].missing)
        self.group.lookup_action('read').set_enabled(some and not all(b.missing for b in books))
        statuses = {book.status for book in books}
        self.group.lookup_action('mark-reading').set_enabled(some and statuses != {'reading'})
        self.group.lookup_action('mark-finished').set_enabled(some and statuses != {'finished'})
        self.group.lookup_action('mark-unread').set_enabled(some and statuses != {'unread'})
        self.group.lookup_action('trash').set_enabled(
            some and all(book.source != 'calibre' and not book.missing for book in books))

    # -- the actions -------------------------------------------------------------------------

    def read(self, ids):
        for book_id in ids[:MAX_OPEN]:
            app().open_book(book_id)

    def details(self, ids):
        if self._details is not None:
            self._details(ids[0])
            return
        window = app().window()
        if window is not None:
            window.show_book(ids[0])

    def edit(self, ids):
        from ..dialogs import edit_metadata

        siblings = getattr(self.widget, 'sibling_ids', None)
        edit_metadata.present(app(), self._window(), ids,
                              siblings=siblings() if siblings is not None else None)

    def add_to_shelf(self, shelf_id, ids=None):
        add_to_shelf(shelf_id, list(self.get_ids()) if ids is None else ids)

    def new_shelf(self, ids):
        from ..dialogs import shelf

        shelf.present_new(app(), self._window(), book_ids=ids)

    def remove_from_shelf(self, ids):
        library = app().library
        shelf = next((shelf for shelf in library.shelves() if shelf.id == self.shelf_id), None)
        if shelf is None:
            return
        library.remove_from_shelf(self.shelf_id, ids)
        title = _title(library, ids)
        if title is not None:
            text = _('Removed “{title}” from {shelf}').format(title=title, shelf=shelf.name)
        else:
            text = ngettext('Removed {n} book from {shelf}', 'Removed {n} books from {shelf}',
                            len(ids)).format(n=len(ids), shelf=shelf.name)
        app().toast(text, undo=True)

    def mark_reading(self, ids):
        set_status(ids, 'reading')

    def mark_finished(self, ids):
        set_status(ids, 'finished')

    def mark_unread(self, ids):
        set_status(ids, 'unread')

    def send(self, ids):
        from ..dialogs import send

        send.present(app(), self._window(), ids)

    def export(self, ids):
        export_books(self._window(), ids)

    def show_in_files(self, ids):
        show_in_files(self._window(), ids[0])

    def remove(self, ids):
        library = app().library
        title = _title(library, ids)
        library.remove_books(ids)
        if title is not None:
            text = _('Removed “{title}” from the library').format(title=title)
        else:
            text = ngettext('Removed {n} book from the library',
                            'Removed {n} books from the library', len(ids)).format(n=len(ids))
        app().toast(text, undo=True)

    def trash(self, ids):
        confirm_trash(self._window(), ids)


# -- the actions, for any caller ---------------------------------------------------------------

def add_to_shelf(shelf_id, ids):
    """Put books on a manual shelf (an undo step) and say so."""
    library = app().library
    shelf = library.shelf(shelf_id)
    if shelf is None or shelf.query is not None or not ids:
        return
    library.add_to_shelf(shelf_id, ids)
    title = _title(library, ids)
    if title is not None:
        text = _('Added “{title}” to {shelf}').format(title=title, shelf=shelf.name)
    else:
        text = ngettext('Added {n} book to {shelf}', 'Added {n} books to {shelf}',
                        len(ids)).format(n=len(ids), shelf=shelf.name)
    app().toast(text, undo=True)


def set_status(ids, status):
    """Mark books reading, finished or unread (an undo step) and say so."""
    library = app().library
    library.set_status(ids, status)
    title = _title(library, ids)
    if title is not None:
        text = {'reading': _('“{title}” marked as reading'),
                'finished': _('“{title}” marked as finished'),
                'unread': _('“{title}” marked as unread')}[status].format(title=title)
    else:
        text = {'reading': ngettext('{n} book marked as reading',
                                    '{n} books marked as reading', len(ids)),
                'finished': ngettext('{n} book marked as finished',
                                     '{n} books marked as finished', len(ids)),
                'unread': ngettext('{n} book marked as unread',
                                   '{n} books marked as unread', len(ids))}[status]
        text = text.format(n=len(ids))
    app().toast(text, undo=True)


# -- the actions that are more than a call ---------------------------------------------------

def show_in_files(window, book_id):
    """Open the folder of a book's file in the file manager, the file selected."""
    book_file = app().library.reading_file(book_id)
    if book_file is None:
        app().toast(_('The book’s file cannot be found'))
        return
    launcher = Gtk.FileLauncher(file=Gio.File.new_for_path(book_file.path))

    def done(launcher, result):
        try:
            launcher.open_containing_folder_finish(result)
        except GLib.Error as error:
            if not error.matches(Gtk.dialog_error_quark(), Gtk.DialogError.DISMISSED):
                app().report(error, _('Could not show the file'))

    launcher.open_containing_folder(window, None, done)


def export_books(window, ids):
    """Ask for a folder, then write a copy of each book there (exporting.py: the edited
    metadata written into the copy), in a thread; a toast says how it went."""
    dialog = Gtk.FileDialog(title=_('Export To'), accept_label=_('_Export'), modal=True)

    def chosen(dialog, result):
        try:
            folder = dialog.select_folder_finish(result)
        except GLib.Error:
            return  # dismissed
        if folder is None or folder.get_path() is None:
            return
        _run_export(ids, folder.get_path())

    dialog.select_folder(window, None, chosen)


def _run_export(ids, folder):
    from .. import exporting

    application = app()

    def work():
        worker = application.library.open_worker()
        covers = application.covers.with_library(worker)
        written, errors = [], []
        try:
            for book_id in ids:
                try:
                    written.append(exporting.export_copy(worker, covers, book_id,
                                                         folder))
                except Exception as error:
                    log.warning('exporting book %s: %s', book_id, error)
                    errors.append(error)
        finally:
            worker.close()
        GLib.idle_add(finish, written, errors)

    def finish(written, errors):
        if errors and not written:
            application.report(errors[0], _('Could not export'))
        elif errors:
            application.toast(ngettext('Exported {n} book; {failed} could not be exported',
                                       'Exported {n} books; {failed} could not be exported',
                                       len(written)).format(n=len(written), failed=len(errors)))
        else:
            application.toast(ngettext('Exported {n} book', 'Exported {n} books',
                                       len(written)).format(n=len(written)))
        return GLib.SOURCE_REMOVE

    threading.Thread(target=work, name='bookcase-export', daemon=True).start()


def confirm_trash(window, ids):
    """Move to Trash…: ask, then trash every file of the books and remove them."""
    library = app().library
    title = _title(library, ids)
    if title is not None:
        heading = _('Move “{title}” to the Trash?').format(title=title)
    else:
        heading = ngettext('Move {n} Book to the Trash?', 'Move {n} Books to the Trash?',
                           len(ids)).format(n=len(ids))
    dialog = Adw.AlertDialog(
        heading=heading,
        body=ngettext('Its files are moved to the trash, where they can be restored from, '
                      'and it leaves the library.',
                      'Their files are moved to the trash, where they can be restored from, '
                      'and they leave the library.', len(ids)))
    dialog.add_response('cancel', _('_Cancel'))
    dialog.add_response('trash', _('_Move to Trash'))
    dialog.set_response_appearance('trash', Adw.ResponseAppearance.DESTRUCTIVE)
    dialog.set_default_response('cancel')
    dialog.set_close_response('cancel')

    def on_response(_dialog, response):
        if response == 'trash':
            trash_books(ids)

    dialog.connect('response', on_response)
    present_dialog(dialog, window)
    return dialog


def trash_books(ids):
    library = app().library
    failed = 0
    with library.undoable(_('Move to Trash')):
        trashed = []
        for book_id in ids:
            ok = True
            for book_file in library.files(book_id):
                if book_file.missing:
                    continue
                try:
                    Gio.File.new_for_path(book_file.path).trash(None)
                except GLib.Error as error:
                    if not error.matches(Gio.io_error_quark(), Gio.IOErrorEnum.NOT_FOUND):
                        log.warning('trashing %s: %s', book_file.path, error.message)
                        ok = False
            if ok:
                trashed.append(book_id)
            else:
                failed += 1
        if trashed:
            library.remove_books(trashed)
    if failed:
        app().toast(ngettext('{n} book could not be moved to the trash',
                             '{n} books could not be moved to the trash', failed).format(n=failed))
    elif trashed:
        app().toast(ngettext('Moved {n} book to the trash', 'Moved {n} books to the trash',
                             len(trashed)).format(n=len(trashed)))


def present_dialog(dialog, window):
    """Present an Adw.Dialog over the window, its keyed actions stepping aside meanwhile."""
    if window is not None and hasattr(window, 'set_dialog_open'):
        window.set_dialog_open(True)
        dialog.connect('closed', lambda *_args: window.set_dialog_open(False))
    dialog.present(window)
    return dialog
