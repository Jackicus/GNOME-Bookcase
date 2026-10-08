# SPDX-License-Identifier: GPL-2.0-or-later
# SPDX-FileCopyrightText: 2026 Jack Tully

"""A grid of covers, or a list, of the books a filter finds: All Books, a reading state, a
shelf (a smart one runs its search), an author's, a series', a tag's or a search's books.

    page = BooksPage(key='all')                         # a root page (pages.make_root)
    page = BooksPage(title='Ada Lark', author=3)        # pushed (window.show_books)
    page.search('lark')                                 # types a query and runs it
    page.focus_search()                                 # Ctrl+F (win.search)
    page.selected_ids()

The books are a Gio.ListStore of BookItems under one Gtk.MultiSelection, shown by a
Gtk.GridView of BookTiles (covers load as tiles are bound) or a Gtk.ColumnView, as the
view-mode setting says (win.view-grid, win.view-list, the Sort and View menu); the order is
the sort-order setting's (library.SORTS; a series' page is always in series order and shows
each book's number), turned round by the sort-reversed setting (Reverse Order); the menu
also has the cover size (a slider on the cover-size setting). The search entry filters as
you type, through library.books(query=…). The filter bar (the funnel button) narrows the
books by format, reading state, least rating and language: search.filter_query() of its
choices joins what is typed; hiding the bar clears them. The Missing Files page (key
'missing', shown in the sidebar while there are any) is the books whose files are gone
(`has:missing`); a book's page has Locate….
A refresh asks the library again; when the same books come back in the same order, each
item only takes its new Book (the tiles follow), so neither the scroll position nor the
selection moves; otherwise the store is refilled and the selection kept by book id.

Clicking selects (Ctrl and Shift add, the rubber band too); a double click or Enter opens the
reader; a right click (or a long press) opens the book menu (pages/actions.py) on the
selection; the keys are shortcuts.GRID's (Ctrl+A selects all, Ctrl+Shift+A none, Delete
removes from the library with Undo). Books dragged out (the selection, or the book under
the pointer) drop on the sidebar's shelves and reading states.
"""

import datetime
import logging
from gettext import gettext as _
from gettext import ngettext

from gi.repository import Adw, Gdk, Gio, GLib, Gtk

from .. import search as search_syntax
from .. import shortcuts
from ..library import DESCENDING
from ..widgets.book_item import BookIds, BookItem
from ..widgets.book_tile import BookTile, CoverSize, format_index, progress_text
from ..widgets.cover import Cover
from ..widgets.rating import Rating
from ..widgets.util import connect_weak
from . import PageListener, app
from .actions import BookActions, book_menu, popup_menu

log = logging.getLogger(__name__)

CHANGE_KINDS = ('books', 'files', 'shelves', 'progress')
COMPACT_COVER = 112
LIST_COVER = 28
FILTER_NAMES = ('format', 'status', 'rating', 'language')


def filter_choices(name):
    """A filter chip's choices: [(value, menu label, chip label)]; '' is no filter. The
    languages are the library's."""
    if name == 'format':
        return [('', _('Any Format'), _('Format')), ('epub', 'EPUB', 'EPUB'),
                ('pdf', 'PDF', 'PDF'), ('comic', _('Comics (CBZ, CBR)'), _('Comics')),
                ('kindle', _('Kindle (MOBI, AZW3)'), _('Kindle')), ('fb2', 'FB2', 'FB2'),
                ('txt', _('Plain Text'), _('Text'))]
    if name == 'status':
        return [('', _('Any Status'), _('Status')), ('unread', _('Unread'), _('Unread')),
                ('reading', _('Reading'), _('Reading')),
                ('finished', _('Finished'), _('Finished'))]
    if name == 'rating':
        choices = [('', _('Any Rating'), _('Rating')), ('5', _('5 Stars'), _('5 Stars'))]
        for stars in (4, 3, 2, 1):
            text = ngettext('{n} Star and Up', '{n} Stars and Up', stars).format(n=stars)
            # Translators: a filter chip: books rated at least {n} stars ("4+ Stars").
            short = ngettext('{n}+ Star', '{n}+ Stars', stars).format(n=stars)
            choices.append((str(stars), text, short))
        return choices
    from .book import language_name

    choices = [('', _('Any Language'), _('Language'))]
    seen = set()
    for code in app().library.languages():
        base = code.lower().replace('_', '-').split('-')[0]
        if base and base not in seen:
            seen.add(base)
            choices.append((base, language_name(base), language_name(base)))
    return choices


def root_title(key, library=None):
    """The title of a root key's page."""
    if key == 'all':
        return _('All Books')
    if key == 'status:reading':
        return _('Currently Reading')
    if key == 'status:unread':
        return _('Unread')
    if key == 'status:finished':
        return _('Finished')
    if key == 'missing':
        return _('Missing Files')
    if key and key.startswith('shelf:') and library is not None:
        shelf = library.shelf(int(key[6:]))
        if shelf is not None:
            return shelf.name
    return _('Books')


def count_text(count):
    return ngettext('{n} book', '{n} books', count).format(n=f'{count:n}')


def empty_state(key, filters, shelf=None):
    """(icon, title, description) of the page with no books."""
    if key == 'status:reading':
        return ('book-open-symbolic', _('Nothing in Progress'),
                _('Books you start reading appear here'))
    if key == 'status:unread':
        return ('book-closed-symbolic', _('No Unread Books'),
                _('Every book in the library has been started'))
    if key == 'status:finished':
        return ('object-select-symbolic', _('No Finished Books'),
                _('Books you finish, or mark as finished, appear here'))
    if key == 'missing':
        return ('object-select-symbolic', _('No Missing Files'),
                _('Every book’s file is where Bookcase last saw it'))
    if shelf is not None and shelf.query is not None:
        return ('folder-saved-search-symbolic', _('No Matching Books'),
                _('No book matches this shelf’s search'))
    if shelf is not None:
        return ('folder-symbolic', _('This Shelf Is Empty'),
                _('Add books to it from their menu, or drag them onto it'))
    if any(filters.get(name) is not None for name in ('author', 'series', 'tag')):
        return ('library-symbolic', _('No Books'), _('These books have left the library'))
    return ('library-symbolic', _('No Books Yet'),
            _('Add books, or a folder of books, to fill the library'))


def added_text(timestamp):
    if not timestamp:
        return ''
    date = datetime.date.fromtimestamp(timestamp)
    # Translators: a date in the list's Added column (strftime): "8 Oct 2026".
    return date.strftime(_('%-d %b %Y'))


@Gtk.Template(resource_path='/io/github/jackicus/Bookcase/books.ui')
class BooksPage(Adw.NavigationPage):
    __gtype_name__ = 'BookcaseBooksPage'

    breakpoint_bin = Gtk.Template.Child()
    compact_breakpoint = Gtk.Template.Child()
    window_title = Gtk.Template.Child()
    sort_button = Gtk.Template.Child()
    search_button = Gtk.Template.Child()
    search_bar = Gtk.Template.Child()
    search_entry = Gtk.Template.Child()
    stack = Gtk.Template.Child()
    grid_scroller = Gtk.Template.Child()
    grid_view = Gtk.Template.Child()
    column_view = Gtk.Template.Child()
    title_column = Gtk.Template.Child()
    author_column = Gtk.Template.Child()
    series_column = Gtk.Template.Child()
    added_column = Gtk.Template.Child()
    rating_column = Gtk.Template.Child()
    format_column = Gtk.Template.Child()
    progress_column = Gtk.Template.Child()
    empty_page = Gtk.Template.Child()
    empty_button = Gtk.Template.Child()
    filter_button = Gtk.Template.Child()
    filter_revealer = Gtk.Template.Child()
    format_chip = Gtk.Template.Child()
    status_chip = Gtk.Template.Child()
    rating_chip = Gtk.Template.Child()
    language_chip = Gtk.Template.Child()
    clear_filters_button = Gtk.Template.Child()

    def __init__(self, key=None, title=None, query='', shelf=None, status=None, author=None,
                 series=None, tag=None):
        super().__init__()
        self.key = key
        self.filters = {'query': query, 'shelf': shelf, 'status': status, 'author': author,
                        'series': series, 'tag': tag}
        self.shelf_id = shelf
        self.series_page = series is not None
        self._fixed_title = title
        if key is not None:
            self.set_tag(key)
        self.settings = app().settings
        self._store = Gio.ListStore(item_type=BookItem)
        self._ids = []
        self.selection = Gtk.MultiSelection(model=self._store)
        self._size = CoverSize(width=self.settings.get_int('cover-size'))
        self._compact = False
        self._last_query = None
        self._loaded = False
        self._first_fill = None  # the idle that fills a page shown before its layout
        self.chosen = dict.fromkeys(FILTER_NAMES, '')  # the filter bar's choices

        self._setup_grid()
        self._setup_list()
        self._add_actions()
        self._add_keys()
        self._setup_filters()
        self._setup_cover_size()
        connect_weak(self.selection, 'selection-changed', self._on_selection_changed)
        # Connected weakly, not as template callbacks: a pushed page must be able to go.
        connect_weak(self.search_entry, 'search-changed', self.on_search_changed)
        connect_weak(self.search_entry, 'activate', self.on_search_activate)
        connect_weak(self.search_entry, 'stop-search', self.on_stop_search)
        connect_weak(self.grid_view, 'activate', self.on_activate)
        connect_weak(self.column_view, 'activate', self.on_activate)
        connect_weak(self.compact_breakpoint, 'apply', self._on_compact_apply)
        connect_weak(self.compact_breakpoint, 'unapply', self._on_compact_unapply)
        for name in ('sort-order', 'sort-reversed', 'view-mode', 'cover-size'):
            connect_weak(self.settings, f'changed::{name}', self._on_setting_changed)
        self.listener = PageListener(self, CHANGE_KINDS, BooksPage.refresh)
        self._update_title()
        self._apply_view_mode()

    # -- the views ---------------------------------------------------------------------------

    def _setup_grid(self):
        factory = Gtk.SignalListItemFactory()
        size = self._size
        series_page = self.series_page

        def setup(_factory, list_item):
            tile = BookTile(size)
            tile.show_series_index = series_page
            list_item.set_child(tile)

        def bind(_factory, list_item):
            list_item.get_child().set_item(list_item.get_item())
            item = list_item.get_item()
            list_item.set_accessible_label(f'{item.book.title}, {item.book.author}')

        def unbind(_factory, list_item):
            list_item.get_child().set_item(None)

        factory.connect('setup', setup)
        factory.connect('bind', bind)
        factory.connect('unbind', unbind)
        self.grid_view.set_factory(factory)
        self._add_context_menu(self.grid_view)

    def _setup_list(self):
        self.title_column.set_factory(_cell_factory(_TitleCell))
        self.author_column.set_factory(_cell_factory(
            lambda: _TextCell(lambda book: book.author if book.authors else '')))
        self.series_column.set_factory(_cell_factory(lambda: _TextCell(_series_text)))
        self.added_column.set_factory(_cell_factory(
            lambda: _TextCell(lambda book: added_text(book.added), numeric=True)))
        self.rating_column.set_factory(_cell_factory(_RatingCell))
        self.format_column.set_factory(_cell_factory(
            lambda: _TextCell(lambda book: ', '.join(fmt.upper() for fmt in book.formats))))
        self.progress_column.set_factory(_cell_factory(
            lambda: _TextCell(progress_text, numeric=True)))
        self._add_context_menu(self.column_view)

    def _add_context_menu(self, view):
        click = Gtk.GestureClick(button=Gdk.BUTTON_SECONDARY)
        connect_weak(click, 'pressed', self._on_secondary_click)
        view.add_controller(click)
        press = Gtk.GestureLongPress(touch_only=True)
        connect_weak(press, 'pressed', self._on_long_press)
        view.add_controller(press)
        drag = Gtk.DragSource(actions=Gdk.DragAction.COPY)
        connect_weak(drag, 'prepare', self._on_drag_prepare)
        view.add_controller(drag)

    def _add_actions(self):
        group = Gio.SimpleActionGroup()
        group.add_action(self.settings.create_action('sort-order'))
        group.add_action(self.settings.create_action('view-mode'))
        group.add_action(self.settings.create_action('sort-reversed'))
        self.insert_action_group('books', group)
        self.book_actions = BookActions(self, self.selected_ids, shelf_id=self._manual_shelf(),
                                        select_all=self.selection.select_all)

    def _add_keys(self):
        """The views' keys (shortcuts.GRID): Return is the views' own activate."""
        ref = self.weak_ref()

        def on_menu_key(widget, _args, *_data):
            page = ref()
            return page._on_menu_key(widget) if page is not None else False

        def on_select_none(_widget, _args, *_data):
            page = ref()
            if page is not None:
                page.selection.unselect_all()
            return True

        for view in (self.grid_view, self.column_view):
            controller = Gtk.ShortcutController(scope=Gtk.ShortcutScope.LOCAL)
            for key, action in (('details', 'book.details'), ('edit', 'book.edit'),
                                ('remove', 'book.remove'), ('select-all', 'book.select-all')):
                controller.add_shortcut(Gtk.Shortcut.new(
                    Gtk.ShortcutTrigger.parse_string(shortcuts.GRID[key][0]),
                    Gtk.NamedAction.new(action)))
            controller.add_shortcut(Gtk.Shortcut.new(
                Gtk.ShortcutTrigger.parse_string('Menu|<Shift>F10'),
                Gtk.CallbackAction.new(on_menu_key)))
            controller.add_shortcut(Gtk.Shortcut.new(
                Gtk.ShortcutTrigger.parse_string(shortcuts.GRID['select-none'][0]),
                Gtk.CallbackAction.new(on_select_none)))
            view.add_controller(controller)

    # -- the filter bar --------------------------------------------------------------------

    def _setup_filters(self):
        group = Gio.SimpleActionGroup()
        ref = self.weak_ref()
        for name in FILTER_NAMES:
            action = Gio.SimpleAction.new_stateful(name, GLib.VariantType.new('s'),
                                                   GLib.Variant('s', ''))

            def change(action, value, name=name):
                page = ref()
                action.set_state(value)
                if page is not None:
                    page.set_filter(name, value.get_string())

            action.connect('change-state', change)
            group.add_action(action)
            chip = self._chip(name)
            if name == 'language':
                def rebuild(chip, *_args):
                    chip.set_menu_model(_filter_menu('language'))
                chip.set_create_popup_func(rebuild)
            chip.set_menu_model(_filter_menu(name))
        self.insert_action_group('filter', group)
        self._filter_group = group
        # The reading state's chip says nothing on a reading state's page.
        self.status_chip.set_visible(self.filters['status'] is None)
        connect_weak(self.clear_filters_button, 'clicked', self._on_clear_filters)
        connect_weak(self.filter_revealer, 'notify::reveal-child', self._on_filter_revealed)

    def _chip(self, name):
        return getattr(self, f'{name}_chip')

    def set_filter(self, name, value):
        """Choose a filter (FILTER_NAMES; '' for none), as the chip's menu does."""
        if self.chosen.get(name) == value:
            return
        self.chosen[name] = value
        action = self._filter_group.lookup_action(name)
        if action.get_state().get_string() != value:
            action.set_state(GLib.Variant('s', value))
        chip = self._chip(name)
        label = next((short for choice, _text, short in filter_choices(name)
                      if choice == value), value)
        chip.set_label(label)
        if value:
            chip.add_css_class('active')
        else:
            chip.remove_css_class('active')
        self.clear_filters_button.set_visible(any(self.chosen.values()))
        if value and not self.filter_revealer.get_reveal_child():
            self.filter_revealer.set_reveal_child(True)
        if self._loaded:
            self.refresh()

    def _on_clear_filters(self, *_args):
        for name in FILTER_NAMES:
            self.set_filter(name, '')

    def _on_filter_revealed(self, revealer, _pspec):
        if not revealer.get_reveal_child():
            self._on_clear_filters()

    def _filter_query(self):
        rating = self.chosen['rating']
        return search_syntax.filter_query(format=self.chosen['format'],
                                          status=self.chosen['status'],
                                          rating=int(rating) if rating else None,
                                          language=self.chosen['language'])

    # -- the cover size ----------------------------------------------------------------------

    def _setup_cover_size(self):
        """The Sort and View menu's cover size slider (the cover-size setting)."""
        key = self.settings.props.settings_schema.get_key('cover-size')
        _kind, limits = key.get_range().unpack()
        low, high = limits
        scale = Gtk.Scale.new_with_range(Gtk.Orientation.HORIZONTAL, low, high, 8)
        scale.set_draw_value(False)
        scale.set_size_request(200, -1)
        scale.set_margin_start(12)
        scale.set_margin_end(12)
        scale.update_property([Gtk.AccessibleProperty.LABEL], [_('Cover Size')])
        scale.set_value(self.settings.get_int('cover-size'))
        settings = self.settings

        def changed(scale):
            value = int(round(scale.get_value()))
            if settings.get_int('cover-size') != value:
                settings.set_int('cover-size', value)

        scale.connect('value-changed', changed)
        self.cover_scale = scale
        self.sort_button.get_popover().add_child(scale, 'cover-size')

    def _manual_shelf(self):
        if self.shelf_id is None:
            return None
        shelf = app().library.shelf(self.shelf_id)
        return self.shelf_id if shelf is not None and shelf.query is None else None

    # -- settings ----------------------------------------------------------------------------

    def _on_setting_changed(self, _settings, key):
        if key == 'view-mode':
            self._apply_view_mode()
        elif key == 'cover-size':
            self._apply_cover_size()
        elif self.get_mapped():
            self.refresh()  # an unmapped page refreshes when it is shown again

    def _apply_view_mode(self):
        mode = self.settings.get_string('view-mode')
        # Only the view shown has the model: a hidden one would make and bind its rows too.
        shown, hidden = ((self.column_view, self.grid_view) if mode == 'list'
                         else (self.grid_view, self.column_view))
        if shown.get_model() is not self.selection:
            hidden.set_model(None)
            shown.set_model(self.selection)
        if self.stack.get_visible_child_name() == 'empty':
            return
        self.stack.set_visible_child_name('list' if mode == 'list' else 'grid')

    def _on_compact_apply(self, _breakpoint):
        self._compact = True
        self._apply_cover_size()
        self._apply_compact_columns()

    def _on_compact_unapply(self, _breakpoint):
        self._compact = False
        self._apply_cover_size()
        self._apply_compact_columns()

    def _apply_compact_columns(self):
        for column in (self.series_column, self.added_column, self.rating_column,
                       self.format_column):
            column.set_visible(not self._compact)

    def _apply_cover_size(self):
        width = self.settings.get_int('cover-size')
        if int(self.cover_scale.get_value()) != width:
            self.cover_scale.set_value(width)
        if self._compact:
            width = min(width, COMPACT_COVER)
        self._size.props.width = width

    # -- the books ---------------------------------------------------------------------------

    def _sort(self):
        if self.series_page:
            return 'series'
        return self.settings.get_string('sort-order')

    def _descending(self):
        """The sort's direction: its own (None), or turned round by Reverse Order."""
        if self.series_page or not self.settings.get_boolean('sort-reversed'):
            return None
        return not DESCENDING.get(self._sort(), False)

    def _query(self):
        parts = [self.filters['query'] or '', self.search_entry.get_text().strip(),
                 self._filter_query()]
        return ' '.join(part for part in parts if part)

    def refresh(self):
        library = app().library
        self._update_title()
        if self.shelf_id is not None and library.shelf(self.shelf_id) is None:
            return  # gone: the window drops the page
        if not self._loaded and self.get_height() == 0:
            # Not laid out yet: a grid with no height would make a tile for every book it
            # might show (a thousand), so the books come once it has its size.
            if self._first_fill is None:
                ref = self.weak_ref()

                def fill():
                    page = ref()
                    if page is not None:
                        page._first_fill = False
                        if page.get_mapped():
                            page.refresh()
                    return GLib.SOURCE_REMOVE

                self._first_fill = GLib.idle_add(fill)
            if self._first_fill is not False:
                return
        filters = dict(self.filters)
        filters['query'] = self._query()
        self._last_query = filters['query']
        try:
            books = library.books(sort=self._sort(), descending=self._descending(),
                                  **filters)
        except Exception as error:
            log.exception('listing books')
            app().report(error)
            return
        self._show(books)
        self._loaded = True
        self._update_state()

    def _show(self, books):
        ids = [book.id for book in books]
        if ids == self._ids:
            for position, book in enumerate(books):
                self._store.get_item(position).set_book(book)
            return
        keep = set(self.selected_ids())
        old = {}
        for position in range(self._store.get_n_items()):
            item = self._store.get_item(position)
            old[item.id] = item
        items = []
        for book in books:
            item = old.get(book.id)
            if item is None:
                item = BookItem(book)
            else:
                item.set_book(book)
            items.append(item)
        self._ids = ids
        self._store.splice(0, self._store.get_n_items(), items)
        if keep:
            selected = Gtk.Bitset.new_empty()
            for position, book_id in enumerate(ids):
                if book_id in keep:
                    selected.add(position)
            mask = Gtk.Bitset.new_range(0, len(ids)) if ids else Gtk.Bitset.new_empty()
            self.selection.set_selection(selected, mask)

    def _update_title(self):
        library = app().library
        title = self._fixed_title or root_title(self.key, library)
        self.set_title(title)
        self.window_title.set_title(title)

    def _update_state(self):
        count = len(self._ids)
        searching = bool(self.search_entry.get_text().strip()) or any(self.chosen.values())
        if count:
            self._apply_view_mode()
        else:
            shelf = app().library.shelf(self.shelf_id) if self.shelf_id is not None else None
            if searching:
                icon, title, description = ('edit-find-symbolic', _('No Results Found'),
                                            _('Try a different search or filter'))
            else:
                icon, title, description = empty_state(self.key, self.filters, shelf)
            self.empty_page.set_icon_name(icon)
            self.empty_page.set_title(title)
            self.empty_page.set_description(description)
            self.empty_button.set_visible(not searching and self.key == 'all')
            self.stack.set_visible_child_name('empty')
        self._update_subtitle()
        self.book_actions.update()

    def _update_subtitle(self):
        selected = self.selection.get_selection().get_size()
        if selected > 1:
            text = ngettext('{n} selected', '{n} selected', selected).format(n=f'{selected:n}')
        elif self._ids:
            text = count_text(len(self._ids))
        else:
            text = ''
        self.window_title.set_subtitle(text)

    def sibling_ids(self):
        """The books shown, in order (Edit Details steps through them)."""
        return list(self._ids)

    def selected_ids(self):
        bitset = self.selection.get_selection()
        return [self._store.get_item(bitset.get_nth(index)).id
                for index in range(bitset.get_size())]

    def _on_selection_changed(self, *_args):
        self._update_subtitle()
        self.book_actions.update()

    # -- search ------------------------------------------------------------------------------

    def focus_search(self):
        self.search_bar.set_search_mode(True)
        self.search_entry.grab_focus()
        self.search_entry.select_region(0, -1)

    def search(self, query):
        self.search_bar.set_search_mode(True)
        self.search_entry.set_text(query)
        self.search_entry.set_position(-1)
        self.refresh()

    def on_search_changed(self, _entry):
        if self._loaded and self._query() != self._last_query:
            self.refresh()

    def on_search_activate(self, _entry):
        self.refresh()
        if self._ids:
            view = self.column_view if self.stack.get_visible_child_name() == 'list' else \
                self.grid_view
            self.selection.select_item(0, True)
            view.grab_focus()

    def on_stop_search(self, _entry):
        self.search_entry.set_text('')
        self.search_bar.set_search_mode(False)
        self.refresh()

    # -- activation and the menu -------------------------------------------------------------

    def on_activate(self, _view, position):
        item = self._store.get_item(position)
        if item is not None:
            if item.book.missing:
                window = self.get_root()
                if window is not None and hasattr(window, 'show_book'):
                    window.show_book(item.id)
                return
            app().open_book(item.id)

    def _position_at(self, view, x, y):
        """The position of the item under (x, y) of the view, or None."""
        widget = view.pick(x, y, Gtk.PickFlags.DEFAULT)
        while widget is not None and widget is not view:
            if isinstance(widget, (BookTile, _Cell)):
                book = widget.cover.book if isinstance(widget, BookTile) else widget.book
                if book is None:
                    return None
                try:
                    return self._ids.index(book.id)
                except ValueError:
                    return None
            if widget.get_css_name() == 'row':
                cell = widget.get_first_child()
                child = cell.get_first_child() if cell is not None else None
                if isinstance(child, _Cell) and child.book is not None:
                    return self._ids.index(child.book.id) if child.book.id in self._ids \
                        else None
            widget = widget.get_parent()
        return None

    def _on_secondary_click(self, gesture, _n_press, x, y):
        gesture.set_state(Gtk.EventSequenceState.CLAIMED)
        self._open_menu(gesture.get_widget(), x, y)

    def _on_long_press(self, gesture, x, y):
        self._open_menu(gesture.get_widget(), x, y)

    def _open_menu(self, view, x, y):
        position = self._position_at(view, x, y)
        if position is None:
            return
        if not self.selection.is_selected(position):
            self.selection.select_item(position, True)
        self._popup(view, x, y)

    def _on_drag_prepare(self, source, x, y):
        """Books dragged (to a shelf in the sidebar): the one under the pointer, with the
        rest of the selection when it is selected."""
        view = source.get_widget()
        position = self._position_at(view, x, y)
        if position is None:
            return None
        if not self.selection.is_selected(position):
            self.selection.select_item(position, True)
        ids = self.selected_ids()
        item = self._store.get_item(position)
        cover = Cover(width=64)
        cover.set_book(item.book)
        source.set_icon(Gtk.WidgetPaintable(widget=cover), 32, 48)
        self._drag_cover = cover  # kept while the drag lasts
        return Gdk.ContentProvider.new_for_value(BookIds(ids))

    def _on_menu_key(self, view):
        if not self.selected_ids():
            return False
        focus = self.get_root().get_focus() if self.get_root() else None
        x, y = view.get_width() / 2, view.get_height() / 3
        if focus is not None and focus.is_ancestor(view):
            ok, bounds = focus.compute_bounds(view)
            if ok:
                x = bounds.get_x() + bounds.get_width() / 2
                y = bounds.get_y() + bounds.get_height() / 2
        self._popup(view, x, y)
        return True

    def _popup(self, view, x, y):
        self.book_actions.update()
        single = len(self.selected_ids()) == 1
        model = book_menu(app().library, shelf_id=self._manual_shelf(), details=single)
        popup_menu(view, model, x, y)


# -- the list's cells ------------------------------------------------------------------------

def _series_text(book):
    if not book.series:
        return ''
    index = format_index(book.series_index)
    return f'{book.series} {index}' if index else book.series


class _Cell:
    """A list cell bound to a BookItem; it follows the item's notify::book."""

    book = None

    def bind_item(self, item):
        self.unbind_item()
        self._item = item
        self._handler = item.connect('notify::book', lambda item, _pspec: self.show(item.book))
        self.book = item.book
        self.show(item.book)

    def unbind_item(self):
        item = getattr(self, '_item', None)
        if item is not None:
            item.disconnect(self._handler)
        self._item = None
        self.book = None

    def show(self, book):
        self.book = book


class _TextCell(Gtk.Inscription, _Cell):
    __gtype_name__ = 'BookcaseBooksTextCell'

    def __init__(self, text, numeric=False):
        super().__init__(xalign=0.0, valign=Gtk.Align.CENTER, hexpand=True,
                         text_overflow=Gtk.InscriptionOverflow.ELLIPSIZE_END)
        if numeric:
            self.add_css_class('numeric')
        self._text = text

    def show(self, book):
        self.book = book
        self.set_text(self._text(book))


class _TitleCell(Gtk.Box, _Cell):
    __gtype_name__ = 'BookcaseBooksTitleCell'

    def __init__(self):
        super().__init__(spacing=10, valign=Gtk.Align.CENTER)
        self.cover = Cover(width=LIST_COVER, valign=Gtk.Align.CENTER)
        self.cover.add_css_class('small')
        self.append(self.cover)
        self.label = Gtk.Inscription(hexpand=True, valign=Gtk.Align.CENTER,
                                     text_overflow=Gtk.InscriptionOverflow.ELLIPSIZE_END)
        self.append(self.label)

    def show(self, book):
        self.book = book
        self.cover.set_book(book)
        self.label.set_text(book.title)
        if book.missing:
            self.label.add_css_class('dimmed')
        else:
            self.label.remove_css_class('dimmed')

    def unbind_item(self):
        _Cell.unbind_item(self)
        self.cover.set_book(None)


class _RatingCell(Gtk.Box, _Cell):
    __gtype_name__ = 'BookcaseBooksRatingCell'

    def __init__(self):
        super().__init__(valign=Gtk.Align.CENTER)
        self.rating = Rating(pixel_size=12)
        self.append(self.rating)

    def show(self, book):
        self.book = book
        self.rating.set_value(book.rating)
        self.rating.set_visible(book.rating > 0)


def _filter_menu(name):
    menu = Gio.Menu()
    for value, text, _short in filter_choices(name):
        item = Gio.MenuItem.new(text.replace('_', '__'), None)
        item.set_action_and_target_value(f'filter.{name}', GLib.Variant('s', value))
        menu.append_item(item)
    return menu


def _cell_factory(make):
    factory = Gtk.SignalListItemFactory()
    factory.connect('setup', lambda _factory, cell: cell.set_child(make()))
    factory.connect('bind', lambda _factory, cell: cell.get_child().bind_item(cell.get_item()))
    factory.connect('unbind', lambda _factory, cell: cell.get_child().unbind_item())
    return factory
