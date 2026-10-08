# SPDX-License-Identifier: GPL-2.0-or-later
# SPDX-FileCopyrightText: 2026 Jack Tully

"""Go To (Ctrl+K in the library window): one search over the books, authors, series, tags
and shelves, to jump to any of them from the keyboard.

    present(app, window)                   # the dialog; returns it
    results(library, text, limit=LIMIT)    # [Result(kind, id, title, subtitle)], no GTK

Typing narrows the list at once (the books by library.books(query=…), most recently read
first; the groups and shelves whose names hold the words); Up and Down move through it
without leaving the entry, Enter goes to the chosen result (the first at first): a book's
details, an author's, a series' or a tag's books, a shelf's page. Escape closes.
"""

import dataclasses
from gettext import gettext as _

from gi.repository import Adw, Gdk, GLib, Gtk, Pango

from ..titles import fold

LIMIT = 8
ICONS = {'book': 'library-symbolic', 'author': 'avatar-default-symbolic',
         'series': 'view-list-ordered-symbolic', 'tag': 'tag-symbolic',
         'shelf': 'folder-symbolic'}


@dataclasses.dataclass(frozen=True)
class Result:
    kind: str  # 'book', 'author', 'series', 'tag', 'shelf'
    id: int
    title: str
    subtitle: str


def results(library, text, limit=LIMIT):
    """What Go To offers for `text`: the groups and shelves whose names hold every word
    first, then the books a search for it finds; nothing for no text."""
    words = fold(text).split()
    if not words:
        return []

    def matches(name):
        folded = fold(name)
        return all(word in folded for word in words)

    found = []
    for kind, groups, label in (('author', library.authors(), _('Author')),
                                ('series', library.series(), _('Series')),
                                ('tag', library.tags(), _('Tag'))):
        for group in groups:
            if matches(group.name):
                found.append(Result(kind, group.id, group.name, label))
    for shelf in library.shelves():
        if matches(shelf.name):
            found.append(Result('shelf', shelf.id, shelf.name,
                                _('Smart Shelf') if shelf.query is not None else _('Shelf')))
    # The names that start with what was typed first.
    found.sort(key=lambda result: not fold(result.title).startswith(words[0]))
    groups = found[:limit // 2] if len(found) > limit // 2 else found
    books = library.books(query=text, sort='last-read', limit=limit - len(groups))
    return groups + [Result('book', book.id, book.title, book.author) for book in books]


class QuickOpenDialog(Adw.Dialog):
    __gtype_name__ = 'BookcaseQuickOpenDialog'

    def __init__(self, app, window):
        super().__init__(title=_('Go To'), content_width=480, content_height=440)
        self.app = app
        self._window = window.weak_ref()
        self.results = []
        self.entry = Gtk.SearchEntry(hexpand=True,
                                     placeholder_text=_('Books, authors, series, shelves…'))
        self.entry.update_property([Gtk.AccessibleProperty.LABEL], [_('Go To')])
        header = Adw.HeaderBar(title_widget=self.entry)
        self.list = Gtk.ListBox(selection_mode=Gtk.SelectionMode.SINGLE)
        self.list.add_css_class('navigation-sidebar')
        self.list.update_property([Gtk.AccessibleProperty.LABEL], [_('Results')])
        self.list.connect('row-activated', lambda _list, row: self._go(row.result))
        self.scrolled = scrolled = Gtk.ScrolledWindow(child=self.list, vexpand=True,
                                                      hscrollbar_policy=Gtk.PolicyType.NEVER)
        self.empty = Adw.StatusPage(icon_name='edit-find-symbolic', title=_('Go To'),
                                    description=_('Type the name of a book, an author, a '
                                                  'series, a tag or a shelf'))
        self.empty.add_css_class('compact')
        self.stack = Gtk.Stack()
        self.stack.add_named(self.empty, 'empty')
        self.stack.add_named(scrolled, 'list')
        view = Adw.ToolbarView(content=self.stack)
        view.add_top_bar(header)
        self.set_child(view)
        self.set_focus(self.entry)
        self.entry.connect('search-changed', lambda _entry: self.update())
        self.entry.connect('activate', lambda _entry: self.activate_selected())
        self.entry.connect('stop-search', lambda _entry: self.close())
        keys = Gtk.EventControllerKey()
        keys.connect('key-pressed', self._on_key)
        self.entry.add_controller(keys)

    def update(self):
        text = self.entry.get_text()
        self.results = results(self.app.library, text)
        self.list.remove_all()
        for result in self.results:
            self.list.append(_row(result))
        if self.results:
            self.list.select_row(self.list.get_row_at_index(0))
            self.stack.set_visible_child_name('list')
        else:
            self.empty.set_title(_('No Results Found') if text.strip() else _('Go To'))
            self.stack.set_visible_child_name('empty')

    def _on_key(self, _controller, keyval, _keycode, _state):
        if keyval in (Gdk.KEY_Down, Gdk.KEY_Up):
            self.move(1 if keyval == Gdk.KEY_Down else -1)
            return True
        return False

    def move(self, step):
        row = self.list.get_selected_row()
        index = row.get_index() + step if row is not None else 0
        index = max(0, min(index, len(self.results) - 1))
        target = self.list.get_row_at_index(index)
        if target is not None:
            self.list.select_row(target)  # the entry keeps the focus
            ok, bounds = target.compute_bounds(self.list)
            if ok:
                self.scrolled.get_vadjustment().clamp_page(
                    bounds.get_y(), bounds.get_y() + bounds.get_height())

    def activate_selected(self):
        row = self.list.get_selected_row()
        if row is not None:
            self._go(row.result)

    def _go(self, result):
        window = self._window()
        self.close()
        if window is None:
            return
        if result.kind == 'book':
            window.show_book(result.id)
        elif result.kind == 'shelf':
            window.show_root(f'shelf:{result.id}')
        else:
            window.show_books(result.title, **{result.kind: result.id})


def _row(result):
    row = Gtk.ListBoxRow()
    row.result = result
    box = Gtk.Box(spacing=12, margin_top=6, margin_bottom=6, margin_start=6, margin_end=6)
    box.append(Gtk.Image(icon_name=ICONS[result.kind],
                         accessible_role=Gtk.AccessibleRole.PRESENTATION))
    labels = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, hexpand=True)
    title = Gtk.Label(label=result.title, xalign=0, ellipsize=Pango.EllipsizeMode.END)
    labels.append(title)
    subtitle = Gtk.Label(label=result.subtitle, xalign=0, ellipsize=Pango.EllipsizeMode.END)
    subtitle.add_css_class('caption')
    subtitle.add_css_class('dimmed')
    labels.append(subtitle)
    box.append(labels)
    row.set_child(box)
    row.update_property([Gtk.AccessibleProperty.LABEL],
                        [f'{result.title}, {result.subtitle}'])
    return row


def present(app, window):
    dialog = QuickOpenDialog(app, window)
    if hasattr(window, 'set_dialog_open'):
        window.set_dialog_open(True)
        dialog.connect('closed', lambda *_args: window.set_dialog_open(False))
    dialog.present(window)
    GLib.idle_add(lambda: (dialog.entry.grab_focus(), GLib.SOURCE_REMOVE)[1])
    return dialog
