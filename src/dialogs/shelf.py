# SPDX-License-Identifier: GPL-2.0-or-later
# SPDX-FileCopyrightText: 2026 Jack Tully

"""New Shelf, New Smart Shelf and Edit Shelf.

    present_new(app, parent, book_ids=None, smart=False)   # creates; book_ids go on it
    present_edit(app, parent, shelf_id)                     # renames; a smart shelf's search
    EXAMPLES                                                # the example chips' searches

A shelf needs a name no other shelf has (an inline error says so). A smart shelf has a
search (search.py's syntax) instead of books: the count beside it says how many books it
finds as it is typed, and the example chips add a term to it. Creating is one undo step
(library.add_shelf, then add_to_shelf for book_ids) with a toast; a new shelf made without
books is shown in the window. Editing is library.update_shelf, also with a toast.
"""

import logging
from gettext import gettext as _
from gettext import ngettext

from gi.repository import Adw, GLib, Gtk

from ..library import LibraryError
from ..widgets.util import connect_weak, connect_weak_call
from . import watch_dialog

log = logging.getLogger(__name__)

# Searches from search.py's syntax, offered as chips; each adds itself to the search.
EXAMPLES = ('status:unread', 'status:reading', 'rating:>=4', 'added:<30d', 'read:<7d',
            'has:series', '-tag:poetry', 'format:pdf')
COUNT_DELAY = 150  # ms after typing stops


@Gtk.Template(resource_path='/io/github/jackicus/Bookcase/shelf.ui')
class ShelfDialog(Adw.Dialog):
    __gtype_name__ = 'BookcaseShelfDialog'

    cancel_button = Gtk.Template.Child()
    apply_button = Gtk.Template.Child()
    name_row = Gtk.Template.Child()
    error_label = Gtk.Template.Child()
    query_group = Gtk.Template.Child()
    query_row = Gtk.Template.Child()
    count_label = Gtk.Template.Child()
    example_chips = Gtk.Template.Child()

    def __init__(self, app, shelf=None, smart=False, book_ids=None):
        super().__init__()
        self.app = app
        self.library = app.library
        self.shelf = shelf
        self.smart = smart if shelf is None else shelf.query is not None
        self.book_ids = list(book_ids or [])
        self.shelf_id = shelf.id if shelf else None
        self._count_source = None
        if shelf is not None:
            self.set_title(_('Edit Smart Shelf') if self.smart else _('Rename Shelf'))
            self.apply_button.set_label(_('_Save'))
            self.name_row.set_text(shelf.name)
            self.query_row.set_text(shelf.query or '')
        else:
            self.set_title(_('New Smart Shelf') if self.smart else _('New Shelf'))
            self.apply_button.set_label(_('C_reate'))
        self.query_group.set_visible(self.smart)
        for example in EXAMPLES:
            chip = Gtk.Button(label=example)
            chip.add_css_class('pill')
            chip.add_css_class('shelf-example')
            chip.set_tooltip_text(_('Add “{example}” to the search').format(example=example))
            connect_weak(chip, 'clicked', self._on_example, example)
            self.example_chips.append(chip)
        connect_weak_call(self.cancel_button, 'clicked', self.close)
        connect_weak_call(self.apply_button, 'clicked', self.apply)
        connect_weak(self.name_row, 'changed', self._on_changed)
        connect_weak_call(self.name_row, 'entry-activated', self.apply)
        connect_weak(self.query_row, 'changed', self._on_query_changed)
        connect_weak_call(self.query_row, 'entry-activated', self.apply)
        self.connect('closed', self._on_closed)
        self.set_default_widget(self.apply_button)
        self._on_changed()
        if self.smart:
            self.update_count()

    # -- validation --------------------------------------------------------------------------

    def _name_taken(self, name):
        folded = name.casefold()
        return any(shelf.name.casefold() == folded and shelf.id != self.shelf_id
                   for shelf in self.library.shelves())

    def problem(self):
        """Why Create/Save cannot go ahead, as a sentence, or '' (None when the name is
        just empty)."""
        name = self.name_row.get_text().strip()
        if not name:
            return None
        if self._name_taken(name):
            return _('A shelf called “{name}” already exists').format(name=name)
        if self.smart and not self.query_row.get_text().strip():
            return None
        return ''

    def _on_changed(self, *_args):
        problem = self.problem()
        self.apply_button.set_sensitive(problem == '')
        self.error_label.set_text(problem or '')
        self.error_label.set_visible(bool(problem))
        if problem:
            self.name_row.add_css_class('error')
        else:
            self.name_row.remove_css_class('error')

    # -- the search --------------------------------------------------------------------------

    def _on_example(self, _button, example):
        text = self.query_row.get_text().rstrip()
        self.query_row.set_text(f'{text} {example}'.strip())
        self.query_row.grab_focus()
        self.query_row.set_position(-1)

    def _on_query_changed(self, _row):
        self._on_changed()
        if self._count_source is not None:
            GLib.source_remove(self._count_source)
        self._count_source = GLib.timeout_add(COUNT_DELAY, self._on_count_due)

    def _on_count_due(self):
        self._count_source = None
        self.update_count()
        return GLib.SOURCE_REMOVE

    def update_count(self):
        query = self.query_row.get_text().strip()
        if not query:
            self.count_label.set_text('')
            return None
        try:
            count = self.library.count(query=query)
        except Exception:  # search.to_sql never raises; a broken library should not either
            log.exception('counting %r', query)
            self.count_label.set_text('')
            return None
        self.count_label.set_text(ngettext('{n} book', '{n} books', count).format(n=count))
        return count

    def _on_closed(self, _dialog):
        if self._count_source is not None:
            GLib.source_remove(self._count_source)
            self._count_source = None

    # -- applying ----------------------------------------------------------------------------

    def apply(self):
        """Create or save the shelf; True when done (the dialog closes)."""
        if self.problem() != '':
            return False
        name = self.name_row.get_text().strip()
        query = self.query_row.get_text().strip() if self.smart else None
        try:
            if self.shelf is None:
                self._create(name, query)
            else:
                self.library.update_shelf(self.shelf.id, name=name,
                                          query=query if self.smart else None)
                self.app.toast(_('Shelf “{name}” saved').format(name=name), undo=True)
        except LibraryError as error:
            self.error_label.set_text(str(error))
            self.error_label.set_visible(True)
            return False
        if self.get_root() is not None:  # presented (tests build it without)
            self.close()
        return True

    def _create(self, name, query):
        with self.library.undoable(_('New Shelf')):
            self.shelf_id = self.library.add_shelf(name, query)
            if self.book_ids and query is None:
                self.library.add_to_shelf(self.shelf_id, self.book_ids)
        if self.book_ids and query is None:
            count = len(self.book_ids)
            self.app.toast(ngettext('Added {count} book to “{name}”',
                                    'Added {count} books to “{name}”', count).format(
                count=count, name=name), undo=True)
            return
        self.app.toast(_('Shelf “{name}” created').format(name=name), undo=True)
        window = getattr(self.app, 'window', None)
        window = window() if callable(window) else None
        if window is not None and hasattr(window, 'show_root'):
            window.show_root(f'shelf:{self.shelf_id}')


def _present(dialog, parent):
    watch_dialog(dialog, parent)
    dialog.present(parent)
    dialog.name_row.grab_focus()
    return dialog


def present_new(app, parent, book_ids=None, smart=False):
    """A new shelf (with book_ids on it) or a new smart shelf."""
    return _present(ShelfDialog(app, smart=smart, book_ids=book_ids), parent)


def present_edit(app, parent, shelf_id):
    """Rename a shelf, or change a smart shelf's name and search; None for an unknown id."""
    shelf = app.library.shelf(shelf_id)
    if shelf is None:
        return None
    return _present(ShelfDialog(app, shelf=shelf), parent)
