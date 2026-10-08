# SPDX-License-Identifier: GPL-2.0-or-later
# SPDX-FileCopyrightText: 2026 Jack Tully

"""Authors, Series or Tags: every group of the library with its count of books.

    page = GroupsPage('authors')       # or 'series', 'tags'
    page.search('lark')                # filters the groups by name
    page.focus_search()

Authors are a grid of avatars (their initials), series a grid of their first books' covers
stacked, tags a list. The filter entry narrows them by name as you type (a Gtk.StringFilter
over the names, folded for case and accents). Activating a group pushes its books
(window.show_books with the group's id: a series' page shows them in series order).
"""

import logging
from gettext import gettext as _
from gettext import ngettext

from gi.repository import Adw, Gio, GObject, Gtk, Pango

from ..widgets.cover import Cover
from ..widgets.util import connect_weak
from . import PageListener, app
from .books import count_text

log = logging.getLogger(__name__)

CHANGE_KINDS = ('books',)
AVATAR_SIZE = 96
SERIES_COVER = 100

KINDS = {
    'authors': (lambda: _('Authors'), 'avatar-default-symbolic', lambda: _('No Authors'),
                lambda: _('Filter authors…')),
    'series': (lambda: _('Series'), 'view-list-ordered-symbolic', lambda: _('No Series'),
               lambda: _('Filter series…')),
    'tags': (lambda: _('Tags'), 'tag-symbolic', lambda: _('No Tags'),
             lambda: _('Filter tags…')),
}


class GroupItem(GObject.Object):
    """A group (library.Group) in the page's model; `name` is a property for the filter."""

    __gtype_name__ = 'BookcaseGroupItem'

    name = GObject.Property(type=str, default='')

    def __init__(self, group):
        super().__init__(name=group.name)
        self.group = group


def group_count_text(kind, count):
    """'12 authors', '3 series', '40 tags'."""
    if kind == 'authors':
        text = ngettext('{n} author', '{n} authors', count)
    elif kind == 'series':
        text = ngettext('{n} series', '{n} series', count)
    else:
        text = ngettext('{n} tag', '{n} tags', count)
    return text.format(n=f'{count:n}')


def empty_description(kind):
    if kind == 'authors':
        return _('Authors appear here once the library has books')
    if kind == 'series':
        return _('Books that belong to a series are grouped here')
    return _('Give books tags in their details to group them here')


class _AuthorTile(Gtk.Box):
    __gtype_name__ = 'BookcaseAuthorTile'

    def __init__(self):
        super().__init__(orientation=Gtk.Orientation.VERTICAL, spacing=6,
                         halign=Gtk.Align.CENTER, width_request=AVATAR_SIZE + 24)
        self.add_css_class('group-tile')
        self.avatar = Adw.Avatar(size=AVATAR_SIZE, show_initials=True,
                                 accessible_role=Gtk.AccessibleRole.PRESENTATION)
        self.append(self.avatar)
        self.name = Gtk.Label(wrap=True, lines=2, ellipsize=Pango.EllipsizeMode.END,
                              justify=Gtk.Justification.CENTER, max_width_chars=1,
                              width_chars=1, wrap_mode=Pango.WrapMode.WORD_CHAR)
        self.name.add_css_class('heading')
        self.append(self.name)
        self.count = Gtk.Label()
        self.count.add_css_class('caption')
        self.count.add_css_class('dimmed')
        self.append(self.count)

    def show(self, group):
        self.avatar.set_text(group.name)
        self.name.set_text(group.name)
        self.count.set_text(count_text(group.count))


class _SeriesTile(Gtk.Box):
    """A series: its first three books' covers, stacked, the name and the count."""

    __gtype_name__ = 'BookcaseSeriesTile'

    OFFSET = 10

    def __init__(self):
        super().__init__(orientation=Gtk.Orientation.VERTICAL, spacing=6,
                         halign=Gtk.Align.CENTER)
        self.add_css_class('group-tile')
        width = SERIES_COVER + 2 * self.OFFSET
        self.fixed = Gtk.Fixed(width_request=width, height_request=round(SERIES_COVER * 1.5),
                               halign=Gtk.Align.CENTER,
                               accessible_role=Gtk.AccessibleRole.PRESENTATION)
        self.covers = []
        for depth in (2, 1, 0):
            # Each cover behind is smaller and shows a strip past the one in front of it.
            cover_width = SERIES_COVER - depth * 8
            cover = Cover(width=cover_width)
            cover.set_visible(False)
            self.fixed.put(cover, SERIES_COVER + depth * self.OFFSET - cover_width,
                           round(SERIES_COVER * 1.5) - round(cover_width * 1.5))
            self.covers.insert(0, cover)  # front first
            if depth:
                cover.add_css_class('behind')
        self.append(self.fixed)
        self.name = Gtk.Label(wrap=True, lines=2, ellipsize=Pango.EllipsizeMode.END,
                              justify=Gtk.Justification.CENTER, max_width_chars=1,
                              width_chars=1, wrap_mode=Pango.WrapMode.WORD_CHAR)
        self.name.add_css_class('heading')
        self.append(self.name)
        self.count = Gtk.Label()
        self.count.add_css_class('caption')
        self.count.add_css_class('dimmed')
        self.append(self.count)

    def show(self, group):
        books = app().library.books(series=group.id, sort='series', limit=3)
        for index, cover in enumerate(self.covers):
            book = books[index] if index < len(books) else None
            cover.set_book(book)
            cover.set_visible(book is not None)
        self.name.set_text(group.name)
        self.count.set_text(count_text(group.count))


class _TagRow(Gtk.Box):
    __gtype_name__ = 'BookcaseTagRow'

    def __init__(self):
        super().__init__(spacing=12, margin_top=12, margin_bottom=12, margin_start=12,
                         margin_end=12)
        self.append(Gtk.Image(icon_name='tag-symbolic',
                              accessible_role=Gtk.AccessibleRole.PRESENTATION))
        self.name = Gtk.Label(xalign=0, hexpand=True, ellipsize=Pango.EllipsizeMode.END)
        self.append(self.name)
        self.count = Gtk.Label()
        self.count.add_css_class('dimmed')
        self.count.add_css_class('numeric')
        self.append(self.count)
        self.append(Gtk.Image(icon_name='go-next-symbolic',
                              accessible_role=Gtk.AccessibleRole.PRESENTATION))

    def show(self, group):
        self.name.set_text(group.name)
        self.count.set_text(f'{group.count:n}')


def _factory(make):
    factory = Gtk.SignalListItemFactory()
    factory.connect('setup', lambda _factory, cell: cell.set_child(make()))

    def bind(_factory, cell):
        group = cell.get_item().group
        cell.get_child().show(group)
        cell.set_accessible_label(
            ngettext('{name}, {n} book', '{name}, {n} books', group.count).format(
                name=group.name, n=group.count))

    factory.connect('bind', bind)
    return factory


@Gtk.Template(resource_path='/io/github/jackicus/Bookcase/groups.ui')
class GroupsPage(Adw.NavigationPage):
    __gtype_name__ = 'BookcaseGroupsPage'

    window_title = Gtk.Template.Child()
    search_bar = Gtk.Template.Child()
    search_entry = Gtk.Template.Child()
    stack = Gtk.Template.Child()
    grid_view = Gtk.Template.Child()
    list_view = Gtk.Template.Child()
    empty_page = Gtk.Template.Child()

    def __init__(self, kind):
        super().__init__(tag=kind)
        self.kind = kind
        title, icon, empty, placeholder = KINDS[kind]
        self.set_title(title())
        self.window_title.set_title(title())
        self.search_entry.set_placeholder_text(placeholder())
        self.empty_page.set_icon_name(icon)
        self.empty_page.set_title(empty())
        self.empty_page.set_description(empty_description(kind))
        self.search_bar.set_key_capture_widget(self)
        self._names = []
        self._store = Gio.ListStore(item_type=GroupItem)
        expression = Gtk.PropertyExpression.new(GroupItem, None, 'name')
        self._filter = Gtk.StringFilter(expression=expression, ignore_case=True,
                                        match_mode=Gtk.StringFilterMatchMode.SUBSTRING)
        self._filtered = Gtk.FilterListModel(model=self._store, filter=self._filter)
        selection = Gtk.NoSelection(model=self._filtered)
        self.view = self.list_view if kind == 'tags' else self.grid_view
        make = {'authors': _AuthorTile, 'series': _SeriesTile, 'tags': _TagRow}[kind]
        self.view.set_factory(_factory(make))
        self.view.set_model(selection)
        connect_weak(self._filtered, 'items-changed', self._on_items_changed)
        self.listener = PageListener(self, CHANGE_KINDS, GroupsPage.refresh)

    def refresh(self):
        library = app().library
        groups = {'authors': library.authors, 'series': library.series,
                  'tags': library.tags}[self.kind]()
        key = [(group.id, group.name, group.count) for group in groups]
        if key != self._names:
            self._names = key
            self._store.splice(0, self._store.get_n_items(),
                               [GroupItem(group) for group in groups])
        self._update_state()

    def _update_state(self):
        shown = self._filtered.get_n_items()
        total = self._store.get_n_items()
        if shown:
            self.stack.set_visible_child_name('list' if self.kind == 'tags' else 'grid')
        else:
            if total:
                self.empty_page.set_icon_name('edit-find-symbolic')
                self.empty_page.set_title(_('No Results Found'))
                self.empty_page.set_description(_('Try a different search'))
            else:
                _title, icon, empty, _placeholder = KINDS[self.kind]
                self.empty_page.set_icon_name(icon)
                self.empty_page.set_title(empty())
                self.empty_page.set_description(empty_description(self.kind))
            self.stack.set_visible_child_name('empty')
        self.window_title.set_subtitle(group_count_text(self.kind, total) if total else '')

    def _on_items_changed(self, *_args):
        self._update_state()

    # -- search ------------------------------------------------------------------------------

    def focus_search(self):
        self.search_bar.set_search_mode(True)
        self.search_entry.grab_focus()

    def search(self, query):
        self.search_bar.set_search_mode(True)
        self.search_entry.set_text(query)
        self._filter.set_search(query.strip())

    @Gtk.Template.Callback()
    def on_search_changed(self, entry):
        self._filter.set_search(entry.get_text().strip())

    @Gtk.Template.Callback()
    def on_stop_search(self, entry):
        entry.set_text('')
        self.search_bar.set_search_mode(False)

    @Gtk.Template.Callback()
    def on_search_activate(self, _entry):
        if self._filtered.get_n_items():
            self.on_activate(self.view, 0)

    # -- opening a group ---------------------------------------------------------------------

    @Gtk.Template.Callback()
    def on_activate(self, _view, position):
        item = self._filtered.get_item(position)
        window = self.get_root()
        if item is None or window is None or not hasattr(window, 'show_books'):
            return
        group = item.group
        window.show_books(group.name, **{
            'authors': {'author': group.id}, 'series': {'series': group.id},
            'tags': {'tag': group.id}}[self.kind])
