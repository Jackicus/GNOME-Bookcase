# SPDX-License-Identifier: GPL-2.0-or-later
# SPDX-FileCopyrightText: 2026 Jack Tully

"""The library window's pages and widgets over a temporary library: the grid of books
(filters, search, refreshes that keep items and the selection, empty states, the book
actions), a book's details, the home page, the groups, the covers and the rating, and the
window itself (its places, pushed pages, the sidebar)."""

import time
import unittest
import uuid

from tests import ROOT  # noqa: F401  (registers bookcase)
from tests.gtk import pump, requires_gtk, wait_for
from tests.support import add_book, temporary_library

SCHEMA_ID = 'io.github.jackicus.Bookcase'
_stand_ins = {}


def stand_ins():
    """The stand-in Application (one per process) and Window class."""
    if _stand_ins:
        return _stand_ins
    from gi.repository import Adw, Gio

    class App(Adw.Application):
        def __init__(self):
            # An ID of its own each time: two apps with one ID in a process clash on D-Bus.
            super().__init__(application_id='io.github.jackicus.Bookcase.PagesTest_'
                             + uuid.uuid4().hex,
                             flags=Gio.ApplicationFlags.NON_UNIQUE)
            self.settings = Gio.Settings.new(SCHEMA_ID)
            self.profile = 'default'
            self.library = None
            self.covers = None
            self.devices = None
            self.toasts = []
            self.opened = []
            self.the_window = None

        def toast(self, text, undo=False):
            self.toasts.append((text, undo))

        def report(self, error, context=None):
            raise AssertionError(f'{context}: {error}')

        def open_book(self, book_id):
            self.opened.append(book_id)

        def window(self):
            return self.the_window

    class Window(Adw.Window):
        def __init__(self):
            super().__init__(default_width=900, default_height=700)
            self.navigation_view = Adw.NavigationView()
            self.set_content(self.navigation_view)
            self.roots, self.books, self.lists = [], [], []

        def show_root(self, key):
            self.roots.append(key)

        def show_book(self, book_id):
            self.books.append(book_id)

        def show_books(self, title, **filters):
            self.lists.append((title, filters))

        def set_dialog_open(self, _open):
            pass

    app = App()
    app.register(None)  # started: windows can be added
    _stand_ins.update(app=app, Window=Window)
    return _stand_ins


class Covers:
    """A cover store with no covers."""

    def load_thumbnail(self, _book, _width, callback):
        callback(None)


@requires_gtk
class PagesTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        found = stand_ins()
        cls.app = found['app']
        cls.window = found['Window']()
        cls.window.present()
        deadline = time.monotonic() + 2
        while not cls.window.get_mapped() and time.monotonic() < deadline:
            pump(20)

    @classmethod
    def tearDownClass(cls):
        cls.window.destroy()
        pump()

    def setUp(self):
        self.app.set_default()
        self.app.toasts = []
        self.app.opened = []
        self.app.settings.reset('view-mode')
        self.app.settings.reset('sort-order')
        self.window.roots, self.window.books, self.window.lists = [], [], []
        context = temporary_library()
        self.library = context.__enter__()
        self.addCleanup(context.__exit__, None, None, None)
        self.app.library = self.library
        self.app.covers = Covers()
        self.harbour = add_book(self.library, 'A Quiet Harbour', ('Ada Lark',),
                                series='Saltmarsh', series_index=2.0, tags=['Sea'],
                                description='<p>The <b>tide</b> came in.</p>',
                                publisher='Lantern House', published='2022-04-07',
                                language='en', identifiers={'isbn': '9780000000002'})
        self.hill = add_book(self.library, 'Lantern Hill', ('Ben Ross',), series='Saltmarsh',
                             series_index=1.0)
        self.moor = add_book(self.library, 'Salt Roads', ('Cy Moor', 'Ada Lark'))

    def tearDown(self):
        # Unmap the page before its library closes (the cleanups run after this).
        from gi.repository import Adw

        self.window.navigation_view.replace([Adw.NavigationPage(title='Blank')])
        pump()

    def show(self, page):
        self.window.navigation_view.replace([page])
        self.assertTrue(wait_for(page.get_mapped))
        pump()
        return page

    def books_page(self, **kwargs):
        from bookcase.pages.books import BooksPage

        return self.show(BooksPage(**kwargs))

    def titles(self, page):
        return [page._store.get_item(n).book.title for n in range(page._store.get_n_items())]

    # -- the grid of books -------------------------------------------------------------------

    def test_all_books_newest_first_then_by_setting(self):
        page = self.books_page(key='all')
        self.assertEqual(self.titles(page), ['Salt Roads', 'Lantern Hill', 'A Quiet Harbour'])
        self.assertEqual(page.window_title.get_subtitle(), '3 books')
        self.assertEqual(page.stack.get_visible_child_name(), 'grid')
        self.app.settings.set_string('sort-order', 'title')
        # Titles sort without their articles (titles.py).
        self.assertEqual(self.titles(page), ['Lantern Hill', 'A Quiet Harbour', 'Salt Roads'])
        self.app.settings.set_string('view-mode', 'list')
        self.assertEqual(page.stack.get_visible_child_name(), 'list')

    def test_search_as_you_type(self):
        page = self.books_page(key='all')
        page.search('lark')
        self.assertEqual(sorted(self.titles(page)), ['A Quiet Harbour', 'Salt Roads'])
        page.search('nothing like it')
        self.assertEqual(page.stack.get_visible_child_name(), 'empty')
        self.assertEqual(page.empty_page.get_title(), 'No Results Found')
        page.on_stop_search(page.search_entry)
        self.assertEqual(len(self.titles(page)), 3)

    def test_refresh_keeps_items_and_selection(self):
        page = self.books_page(key='all')
        first = page._store.get_item(0)
        page.selection.select_item(1, True)
        self.library.update_book(self.moor, title='Salt Roads Again')
        page.refresh()
        self.assertIs(page._store.get_item(0), first)
        self.assertEqual(first.book.title, 'Salt Roads Again')
        self.assertEqual(page.selected_ids(), [self.hill])
        add_book(self.library, 'Newest')
        page.refresh()
        self.assertEqual(self.titles(page)[0], 'Newest')
        self.assertEqual(page.selected_ids(), [self.hill])

    def test_series_page_in_series_order_with_numbers(self):
        group = next(group for group in self.library.series() if group.name == 'Saltmarsh')
        page = self.books_page(title='Saltmarsh', series=group.id)
        self.assertEqual(self.titles(page), ['Lantern Hill', 'A Quiet Harbour'])
        self.assertEqual(page.get_title(), 'Saltmarsh')
        self.assertTrue(page.series_page)

    def test_empty_states(self):
        page = self.books_page(key='status:finished', status='finished')
        self.assertEqual(page.stack.get_visible_child_name(), 'empty')
        self.assertEqual(page.empty_page.get_title(), 'No Finished Books')
        self.assertFalse(page.empty_button.get_visible())
        shelf = self.library.add_shelf('Holiday')
        page = self.books_page(key=f'shelf:{shelf}', shelf=shelf)
        self.assertEqual(page.get_title(), 'Holiday')
        self.assertEqual(page.empty_page.get_title(), 'This Shelf Is Empty')

    def test_book_actions_on_the_selection(self):
        page = self.books_page(key='all')
        page.selection.select_item(0, True)
        page.selection.select_item(1, False)
        group = page.book_actions.group
        self.assertFalse(group.lookup_action('details').get_enabled())  # two books
        group.activate_action('mark-finished', None)
        self.assertEqual(self.library.book(self.moor).status, 'finished')
        self.assertEqual(self.app.toasts[-1], ('2 books marked as finished', True))
        shelf = self.library.add_shelf('Holiday')
        page.book_actions.update()
        from gi.repository import GLib

        group.activate_action('add-to-shelf', GLib.Variant('x', shelf))
        self.assertEqual(self.library.count(shelf=shelf), 2)
        group.activate_action('remove', None)
        self.assertEqual(self.library.count(), 1)
        self.assertEqual(self.app.toasts[-1], ('Removed 2 books from the library', True))
        self.library.undo()
        self.assertEqual(self.library.count(), 3)

    def test_group_series_stacks(self):
        from bookcase.widgets.series_stack import SeriesItem, SeriesStackTile, collapse

        self.addCleanup(self.app.settings.reset, 'group-series')
        page = self.books_page(key='all')
        self.assertEqual(page._store.get_n_items(), 3)
        self.app.settings.set_boolean('group-series', True)
        page.refresh()
        items = [page._store.get_item(n) for n in range(page._store.get_n_items())]
        self.assertEqual(len(items), 2)
        stack = next(item for item in items if isinstance(item, SeriesItem))
        self.assertEqual(stack.name, 'Saltmarsh')
        self.assertEqual(stack.ids, [self.hill, self.harbour])  # in series order
        self.assertEqual(page.window_title.get_subtitle(), '3 books')
        # The stack's tile, bound by the grid.
        pump()
        tiles = [tile for tile in _descendants(page.grid_view)
                 if isinstance(tile, SeriesStackTile) and tile.get_visible()]
        self.assertEqual(len(tiles), 1)
        self.assertEqual(tiles[0].subtitle.get_text(), '2 books')
        # A stack selected stands for its books; the selection survives a refresh.
        position = items.index(stack)
        page.selection.select_item(position, True)
        self.assertEqual(sorted(page.selected_ids()), sorted([self.hill, self.harbour]))
        self.library.set_progress(self.hill, 0.5, 'epubcfi(/6/2)')
        page.refresh()
        self.assertIs(page._store.get_item(position), stack)
        self.assertAlmostEqual(stack.fraction, 0.25)
        self.assertEqual(sorted(page.selected_ids()), sorted([self.hill, self.harbour]))
        page.on_activate(page.grid_view, position)
        group = next(group for group in self.library.series() if group.name == 'Saltmarsh')
        self.assertEqual(self.window.lists[-1], ('Saltmarsh', {'series': group.id}))
        # A search shows every book; so does the list.
        page.search('lantern')
        self.assertFalse(any(isinstance(page._store.get_item(n), SeriesItem)
                             for n in range(page._store.get_n_items())))
        page.on_stop_search(page.search_entry)
        self.assertEqual(page._store.get_n_items(), 2)
        self.app.settings.set_string('view-mode', 'list')
        self.assertEqual(page._store.get_n_items(), 3)
        # Other pages never group.
        self.app.settings.reset('view-mode')
        shelf_page = self.books_page(key='status:unread', status='unread')
        self.assertEqual(shelf_page._store.get_n_items(), 2)
        self.assertEqual(collapse([]), [])

    def test_recently_opened_on_home(self):
        from bookcase.formats import BookInfo
        from bookcase.pages.home import HomePage

        opened = self.library.add_opened(BookInfo(title='A Borrowed Map', authors=['Ned Quill']),
                                         '/invented/map.epub', hash='map', size=1)
        page = self.show(HomePage())
        self.assertEqual([key for key, _row in page.rows], ['opened', 'recent'])
        row = page.rows[0][1]
        self.assertEqual([item.book_id for item in row.opened_rows], [opened])
        kept = []
        self.app.keep_book = kept.append
        self.addCleanup(delattr, self.app, 'keep_book')
        row.opened_rows[0].add_button.emit('clicked')
        self.assertEqual(kept, [opened])
        row.opened_rows[0].forget_button.emit('clicked')
        self.assertIsNone(self.library.book(opened))
        self.assertEqual(self.app.toasts[-1], ('Forgot “A Borrowed Map”', True))
        page.refresh()
        self.assertEqual([key for key, _row in page.rows], ['recent'])
        self.library.undo()
        page.refresh()
        self.assertEqual([key for key, _row in page.rows], ['opened', 'recent'])

    def test_activating_a_book_opens_the_reader(self):
        page = self.books_page(key='all')
        page.on_activate(page.grid_view, 2)
        self.assertEqual(self.app.opened, [self.harbour])

    def test_book_menu_lists_manual_shelves(self):
        from bookcase.pages.actions import book_menu

        self.library.add_shelf('Holiday')
        self.library.add_shelf('Smart', query='tag:Sea')
        menu = book_menu(self.library)
        labels = []
        for section in range(menu.get_n_items()):
            links = menu.get_item_link(section, 'section')
            for item in range(links.get_n_items()):
                labels.append(links.get_item_attribute_value(item, 'label').get_string())
                submenu = links.get_item_link(item, 'submenu')
                if submenu is not None:
                    names = [submenu.get_item_attribute_value(n, 'label')
                             for n in range(submenu.get_n_items())]
                    labels.append([name.get_string() for name in names if name is not None])
        self.assertIn(['Holiday'], labels)
        self.assertIn('Move to _Trash…', labels)

    # -- a book's details ----------------------------------------------------------------

    def test_book_page(self):
        from bookcase.pages.book import BookPage

        self.library.set_progress(self.harbour, 0.4, 'epubcfi(/6/4)')
        page = self.show(BookPage(self.harbour))
        self.assertEqual(page.title_label.get_text(), 'A Quiet Harbour')
        self.assertIn('Ada Lark', page.authors_label.get_text())
        self.assertEqual(page.series_label.get_text(), 'Book 2 of Saltmarsh')
        self.assertEqual(page.read_button.get_label(), '_Continue Reading')
        self.assertEqual(page.description_label.get_text(), 'The tide came in.')
        self.assertTrue(page.tags_box.get_visible())
        titles = [row.get_title() for group, row in page._rows]
        self.assertIn('Publisher', titles)
        self.assertIn('ISBN', titles)
        page.rating.set_value(8)
        self.assertEqual(self.library.book(self.harbour).rating, 8)
        self.assertEqual(self.app.toasts[-1], ('Rating changed to 4 stars', True))
        page._on_activate_link(page.series_label, 'series:')
        self.assertEqual(self.window.lists[-1][0], 'Saltmarsh')
        page._on_activate_link(page.authors_label, 'author:0')
        self.assertEqual(self.window.lists[-1][0], 'Ada Lark')

    def test_book_page_helpers(self):
        from bookcase.library import Book
        from bookcase.pages.book import (annotations_text, authors_markup, language_name,
                                         published_text, status_text)

        self.assertEqual(published_text('2019'), '2019')
        self.assertEqual(published_text('2019-03'), 'March 2019')
        self.assertEqual(published_text('2019-03-04'), '4 March 2019')
        self.assertEqual(published_text('2019-13-40'), '2019')
        self.assertEqual(language_name('de'), 'Deutsch')
        self.assertEqual(language_name('en-GB'), 'English')
        self.assertEqual(language_name('xx'), 'xx')
        book = Book(id=1, uuid='u', title='T', sort_title='t', authors=('A & B', 'C'))
        self.assertEqual(authors_markup(book), '<a href="author:0">A &amp; B</a> and '
                                               '<a href="author:1">C</a>')
        self.assertEqual(status_text(book), 'Not started')
        self.assertEqual(annotations_text([]), 'None yet')

    # -- home, groups ------------------------------------------------------------------------

    def test_home_rows(self):
        from bookcase.pages.home import HomePage

        page = self.show(HomePage())
        self.assertEqual([key for key, _row in page.rows], ['recent'])
        self.library.set_progress(self.hill, 0.5, 'epubcfi(/6/2)')
        shelf = self.library.add_shelf('Holiday')
        self.library.add_to_shelf(shelf, [self.moor])
        page.refresh()
        self.assertEqual([key for key, _row in page.rows],
                         ['reading', 'recent', f'shelf:{shelf}'])
        reading = page.rows[0][1]
        self.assertEqual(reading.tiles[0].book_id, self.hill)
        reading.tiles[0].emit('clicked')
        self.assertEqual(self.app.opened, [self.hill])
        page.rows[1][1].tiles[0].emit('clicked')
        self.assertEqual(self.window.books, [self.moor])

    def test_home_empty(self):
        from bookcase.pages.home import HomePage

        self.library.remove_books([self.harbour, self.hill, self.moor])
        page = self.show(HomePage())
        self.assertEqual(page.stack.get_visible_child_name(), 'empty')

    def test_groups(self):
        from bookcase.pages.groups import GroupsPage

        page = self.show(GroupsPage('authors'))
        self.assertEqual(page._store.get_n_items(), 3)
        self.assertEqual(page.window_title.get_subtitle(), '3 authors')
        page.search('lark')
        self.assertEqual(page._filtered.get_n_items(), 1)
        page.on_activate(page.view, 0)
        self.assertEqual(self.window.lists[-1][0], 'Ada Lark')
        self.assertIn('author', self.window.lists[-1][1])
        page.search('nobody at all')
        self.assertEqual(page.stack.get_visible_child_name(), 'empty')
        tags = self.show(GroupsPage('tags'))
        self.assertEqual(tags.stack.get_visible_child_name(), 'list')

    # -- widgets -------------------------------------------------------------------------------

    def test_cover_and_tile(self):
        from gi.repository import Gtk

        from bookcase.widgets.book_tile import BookTile

        book = self.library.book(self.harbour)
        tile = BookTile(width=120)
        tile.show_series_index = True
        tile.set_book(book)
        self.assertEqual(tile.cover.measure(Gtk.Orientation.HORIZONTAL, -1)[1], 120)
        self.assertEqual(tile.cover.measure(Gtk.Orientation.VERTICAL, -1)[1], 180)
        self.assertEqual(tile.badge.get_text(), '2')
        self.assertFalse(tile.progress.get_visible())
        self.library.set_progress(self.harbour, 0.3, 'x')
        tile.set_book(self.library.book(self.harbour))
        self.assertTrue(tile.progress.get_visible())
        paintable = Gtk.WidgetPaintable(widget=tile.cover)
        snapshot = Gtk.Snapshot()
        paintable.snapshot(snapshot, 120, 180)  # draws the placeholder without failing

    def test_rating_keys(self):
        from gi.repository import Gdk

        from bookcase.widgets.rating import Rating

        rating = Rating(value=4, editable=True)
        rating._on_key(None, Gdk.KEY_Right, 0, 0)
        self.assertEqual(rating.get_value(), 5)
        rating._on_key(None, Gdk.KEY_3, 0, 0)
        self.assertEqual(rating.get_value(), 6)
        rating._on_key(None, Gdk.KEY_Delete, 0, 0)
        self.assertEqual(rating.get_value(), 0)
        read_only = Rating(value=4)
        self.assertFalse(read_only._on_key(None, Gdk.KEY_Right, 0, 0))
        self.assertEqual(read_only.get_value(), 4)

    # -- the filter bar, sorting, missing files, duplicates, the welcome, Go To -----------------

    def test_filter_bar(self):
        comic = add_book(self.library, 'Moth Lamp', ('Di Fen',), fmt='cbz', language='de')
        self.library.update_book(self.moor, rating=8)
        page = self.books_page(key='all')
        page.set_filter('format', 'comic')
        self.assertEqual(self.titles(page), ['Moth Lamp'])
        self.assertTrue(page.filter_revealer.get_reveal_child())
        self.assertEqual(page.format_chip.get_label(), 'Comics')
        self.assertTrue(page.format_chip.has_css_class('active'))
        page.set_filter('format', '')
        page.set_filter('rating', '4')
        self.assertEqual(self.titles(page), ['Salt Roads'])
        page.search('harbour')
        self.assertEqual(page.stack.get_visible_child_name(), 'empty')
        self.assertEqual(page.empty_page.get_title(), 'No Results Found')
        page.search('')
        page.set_filter('rating', '')
        page.set_filter('language', 'de')
        self.assertEqual(self.titles(page), ['Moth Lamp'])
        page.filter_revealer.set_reveal_child(False)  # hiding the bar clears it
        self.assertEqual(len(self.titles(page)), 4)
        self.assertFalse(any(page.chosen.values()))
        status = self.books_page(key='status:unread', status='unread')
        self.assertFalse(status.status_chip.get_visible())
        self.assertIn(comic, [item.id for item in (status._store.get_item(n) for n in
                                                   range(status._store.get_n_items()))])

    def test_reverse_order_and_cover_size(self):
        self.addCleanup(self.app.settings.reset, 'sort-reversed')
        self.addCleanup(self.app.settings.reset, 'cover-size')
        self.app.settings.set_string('sort-order', 'title')
        page = self.books_page(key='all')
        self.assertEqual(self.titles(page), ['Lantern Hill', 'A Quiet Harbour', 'Salt Roads'])
        self.app.settings.set_boolean('sort-reversed', True)
        self.assertEqual(self.titles(page), ['Salt Roads', 'A Quiet Harbour', 'Lantern Hill'])
        page.cover_scale.set_value(200)
        self.assertEqual(self.app.settings.get_int('cover-size'), 200)
        self.app.settings.set_int('cover-size', 120)
        self.assertEqual(page.cover_scale.get_value(), 120)

    def test_missing_files_page(self):
        from bookcase.pages import make_root

        page = self.show(make_root('missing'))
        self.assertEqual(page.get_title(), 'Missing Files')
        self.assertEqual(page.stack.get_visible_child_name(), 'empty')
        self.library.set_missing([self.library.files(self.hill)[0].id])
        page.refresh()
        self.assertEqual(self.titles(page), ['Lantern Hill'])

    def test_duplicates_page(self):
        from bookcase.pages.duplicates import DuplicatesPage

        copy = add_book(self.library, 'A Quiet Harbour', ('Ada Lark',), fmt='pdf')
        page = self.show(DuplicatesPage())
        self.assertEqual([group.ids for group in page.groups], [[self.harbour, copy]])
        self.assertEqual(page.groups[0].keep, self.harbour)  # the richer one
        page.groups[0].choose(copy)
        self.assertEqual(page.merge(page.groups[0]), 1)
        self.assertIsNone(self.library.book(self.harbour))
        self.assertEqual(self.library.book(copy).formats, ('epub', 'pdf'))
        self.assertTrue(self.app.toasts[-1][1])
        page.refresh()
        self.assertEqual(page.stack.get_visible_child_name(), 'empty')
        self.library.undo()
        page.refresh()
        self.assertEqual(len(page.groups), 1)

    def test_welcome_offers_what_it_finds(self):
        import os
        import pathlib
        import tempfile
        from unittest import mock

        from bookcase.pages.home import HomePage

        home = pathlib.Path(tempfile.mkdtemp(prefix='bookcase-home-'))
        self.addCleanup(__import__('shutil').rmtree, home, ignore_errors=True)
        (home / 'Books').mkdir()
        (home / 'Books' / 'a.epub').write_bytes(b'')
        (home / 'Downloads').mkdir()
        (home / 'Downloads' / 'b.mobi').write_bytes(b'')
        patcher = mock.patch.dict(os.environ, {'BOOKCASE_HOME_HINTS': str(home)})
        patcher.start()
        self.addCleanup(patcher.stop)
        self.library.remove_books([self.harbour, self.hill, self.moor])
        page = self.show(HomePage())
        self.assertTrue(wait_for(lambda: page.sources is not None))
        pump()
        self.assertEqual([row.get_title() for row in page._source_rows], ['Books', 'Downloads'])
        self.assertTrue(page.sources_box.get_visible())
        self.assertTrue(page._source_rows[0].button.has_css_class('suggested-action'))
        self.assertFalse(page.add_button.has_css_class('suggested-action'))
        # Books arriving after the welcome: the tip, once.
        self.addCleanup(self.app.settings.reset, 'welcome-tip-shown')
        add_book(self.library, 'Lantern Hill')
        page.refresh()
        self.assertTrue(page.tip_banner.get_revealed())
        self.assertTrue(self.app.settings.get_boolean('welcome-tip-shown'))
        page.tip_banner.emit('button-clicked')
        self.assertFalse(page.tip_banner.get_revealed())

    def test_book_page_series_and_more(self):
        from bookcase.pages.book import BookPage

        page = self.show(BookPage(self.harbour))
        self.assertTrue(page.series_nav.get_visible())
        self.assertTrue(page.previous_button.get_visible())
        self.assertFalse(page.next_button.get_visible())
        self.assertEqual(page.previous_label.get_text(), 'Lantern Hill')
        page.previous_button.emit('clicked')
        self.assertEqual(self.window.books, [self.hill])
        # Ada Lark's other book (Salt Roads) in a row of covers.
        self.assertEqual(len(page._more_rows), 1)
        page._more_rows[0].tiles[0].emit('clicked')
        self.assertEqual(self.window.books[-1], self.moor)

    def test_go_to_results(self):
        from bookcase.dialogs.quick_open import results

        self.assertEqual(results(self.library, '  '), [])
        found = results(self.library, 'lark')
        self.assertEqual([(r.kind, r.title) for r in found],
                         [('author', 'Ada Lark'), ('book', 'Salt Roads'),
                          ('book', 'A Quiet Harbour')])
        shelf = self.library.add_shelf('Salt Marsh Reads')
        found = results(self.library, 'salt')
        self.assertEqual(found[0].kind, 'series')
        self.assertIn(('shelf', shelf), [(r.kind, r.id) for r in found])


@requires_gtk
class WindowTest(unittest.TestCase):
    """The real library window, on the stand-in application."""

    def setUp(self):
        self.app = stand_ins()['app']
        self.app.set_default()
        self.app.settings.reset('last-page')
        context = temporary_library()
        self.library = context.__enter__()
        self.addCleanup(context.__exit__, None, None, None)
        self.app.library = self.library
        self.app.covers = Covers()
        self.book = add_book(self.library, 'A Quiet Harbour', ('Ada Lark',))

    def test_places_pages_and_shelves(self):
        from bookcase.window import Window

        window = Window(application=self.app)
        self.addCleanup(window.destroy)
        self.addCleanup(window.sidebar_controller.cancel_refresh)
        self.app.the_window = window
        self.addCleanup(setattr, self.app, 'the_window', None)
        self.assertEqual(window.current_key, 'home')
        for key in ('all', 'authors', 'series', 'tags', 'status:reading'):
            page = window.show_root(key)
            self.assertIs(window.navigation_view.get_visible_page(), page)
        self.assertIs(window.show_root('all'), page := window._roots['all'])
        window.show_book(self.book)
        self.assertIsNot(window.navigation_view.get_visible_page(), page)
        window.on_back()
        self.assertIs(window.navigation_view.get_visible_page(), page)
        self.assertEqual(window.search('harbour'), page)

        shelf = self.library.add_shelf('Holiday')
        window.sidebar_controller.refresh_now()
        self.assertTrue(window.sidebar_controller.has_key(f'shelf:{shelf}'))
        window.show_root(f'shelf:{shelf}')
        window.remove_shelf(shelf)  # empty: at once, with Undo
        self.assertIsNone(self.library.shelf(shelf))
        self.assertEqual(self.app.toasts[-1][1], True)
        pump()
        self.assertEqual(window.current_key, 'home')



def _descendants(widget):
    child = widget.get_first_child()
    while child is not None:
        yield child
        yield from _descendants(child)
        child = child.get_next_sibling()


if __name__ == '__main__':
    unittest.main()
