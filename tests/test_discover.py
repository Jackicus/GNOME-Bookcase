# SPDX-License-Identifier: GPL-2.0-or-later
# SPDX-FileCopyrightText: 2026 Jack Tully

"""The Discover page, a catalogue's page, a book's sheet and the Add Catalog dialog, over a
temporary library and invented feeds given to them directly (nothing is fetched)."""

import unittest

from tests import ROOT  # noqa: F401  (registers bookcase)
from tests import opds_fixtures as fx
from tests.gtk import pump, requires_gtk
from tests.support import add_book, temporary_library
from bookcase import opds

SCHEMA_ID = 'io.github.jackicus.Bookcase'


class FakeApp:
    """What the Discover pages ask of the application (pages.app() is patched to return it,
    so no Gio.Application is registered and none left by another test module is used)."""

    def __init__(self, library):
        from gi.repository import Gio

        self.library = library
        self.settings = Gio.Settings.new(SCHEMA_ID)
        self.importer = None
        self.toasts = []
        self.opened = []

    def toast(self, text, undo=False):
        self.toasts.append((text, undo))

    def open_book(self, book_id):
        self.opened.append(book_id)

    def window(self):
        return None


@requires_gtk
class TestDiscover(unittest.TestCase):

    def setUp(self):
        self._context = temporary_library()
        self.library = self._context.__enter__()
        from gi.repository import Adw

        from bookcase import pages
        from bookcase.pages import catalog, discover

        self.app = FakeApp(self.library)
        self.app.settings.reset('catalogs')
        self._patched = [(module, module.app) for module in (pages, catalog, discover)]
        for module, _original in self._patched:
            module.app = lambda: self.app
        catalog._downloads = None
        self.window = Adw.Window(default_width=900, default_height=700)
        self.window.navigation_view = Adw.NavigationView()
        self.window.set_content(self.window.navigation_view)

    def tearDown(self):
        from bookcase.pages import catalog

        self.window.destroy()
        self.app.settings.reset('catalogs')
        for module, original in self._patched:
            module.app = original
        catalog._downloads = None
        self._context.__exit__(None, None, None)

    def test_cards(self):
        from bookcase.pages.discover import DiscoverPage, remove_catalog

        page = DiscoverPage()
        cards = list(page.flow_box)
        self.assertEqual([c.catalog.id for c in cards],
                         [c.id for c in opds.builtin_catalogs()])
        remove_catalog('gutenberg')
        pump()
        self.assertNotIn('gutenberg', [c.catalog.id for c in page.flow_box])
        self.assertEqual(self.app.toasts[-1][0], 'Removed “Project Gutenberg”')
        for catalog in opds.load_catalogs(self.app.settings.get_string('catalogs')):
            remove_catalog(catalog.id)
        pump()
        self.assertEqual(page.stack.get_visible_child_name(), 'empty')
        page.activate_action('discover.restore', None)
        pump()
        self.assertEqual(len(list(page.flow_box)), len(opds.builtin_catalogs()))

    def test_catalog_page(self):
        from bookcase.pages.catalog import CatalogPage

        add_book(self.library, 'The Lantern Keeper', authors=('Ada Lark',))
        catalog = opds.Catalog('x', 'Harbour Lane', 'https://books.example/opds')
        feed = opds.parse(fx.ATOM_BOOKS, 'https://books.example/opds/new')
        page = CatalogPage(catalog, url=feed.url, feed=feed, client=opds.Client())
        self.window.navigation_view.push(page)
        self.window.present()
        pump()
        self.assertEqual(page.stack.get_visible_child_name(), 'feed')
        self.assertEqual(page.window_title.get_title(), 'New Arrivals')
        self.assertEqual([t.entry.title for t in page._tiles],
                         ['The Lantern Keeper', 'Salt and Cedar', 'The Borrowed Map'])
        self.assertEqual([t.mark.get_visible() for t in page._tiles], [True, False, False])
        self.assertTrue(page.search_button.get_visible())
        self.assertTrue(page.facet_scroller.get_visible())
        self.assertEqual(page.facet_box.get_first_child().get_label(), 'Sort: Title')
        self.assertTrue(page.more_box.get_visible())
        self.assertFalse(page.nav_clamp.get_visible())
        page._append(opds.parse(fx.ATOM_BOOKS_PAGE_2, 'https://books.example/opds/new'))
        self.assertEqual(len(page._tiles), 4)
        self.assertFalse(page.more_box.get_visible())
        page.show_error(opds.AuthError('books.example asks for a user name and password'))
        self.assertEqual(page.stack.get_visible_child_name(), 'message')
        self.assertEqual(page.status_page.get_title(), 'Sign In Required')

    def test_sections(self):
        from bookcase.pages.catalog import CatalogPage

        catalog = opds.Catalog('x', 'Harbour Lane', 'https://books.example/opds')
        feed = opds.parse(fx.ATOM_NAVIGATION, catalog.url)
        page = CatalogPage(catalog, feed=feed, client=opds.Client())
        self.assertTrue(page.nav_clamp.get_visible())
        self.assertFalse(page.book_box.get_visible())
        self.assertEqual(page.nav_list.get_row_at_index(0).entry.title, 'New Arrivals')
        empty = opds.Feed(url=catalog.url, title='Nothing')
        page = CatalogPage(catalog, feed=empty, client=opds.Client())
        self.assertEqual(page.stack.get_visible_child_name(), 'message')

    def test_entry_sheet(self):
        from bookcase.dialogs import catalog_entry

        catalog = opds.Catalog('x', 'Harbour Lane', 'https://books.example/opds')
        lantern, salt, borrowed = opds.parse(fx.ATOM_BOOKS, BASE).books
        dialog = catalog_entry.CatalogEntryDialog(self.app, catalog, lantern, opds.Client())
        self.assertEqual(dialog.action_stack.get_visible_child_name(), 'download')
        self.assertEqual(dialog.download_button.get_label(), '_Download EPUB')
        self.assertEqual(dialog.series_label.get_text(), 'Coastal Tales, book 2')
        self.assertEqual(dialog.facts_label.get_text(), '2019 · English · Gull Press')
        book_id = add_book(self.library, 'The Lantern Keeper', authors=('Tomas Wren',))
        dialog.update_state()
        self.assertEqual(dialog.action_stack.get_visible_child_name(), 'library')
        self.assertEqual(dialog.book_id, book_id)
        dialog.read_button.emit('clicked')
        self.assertEqual(self.app.opened[-1], book_id)
        paid = catalog_entry.CatalogEntryDialog(self.app, catalog, salt, opds.Client())
        self.assertEqual(paid.action_stack.get_visible_child_name(), 'none')
        self.assertEqual(paid.none_label.get_text(), 'Buy · 4.99 EUR')
        drm = catalog_entry.CatalogEntryDialog(self.app, catalog, borrowed, opds.Client())
        self.assertEqual(drm.none_label.get_text(), 'Protected by DRM')

    def test_add_catalog_dialog(self):
        from bookcase.dialogs import add_catalog

        dialog = add_catalog.CatalogDialog(self.app)
        dialog.present(self.window)
        self.assertFalse(dialog.save_button.get_sensitive())
        dialog.url_row.set_text('books.example/opds')
        self.assertTrue(dialog.save_button.get_sensitive())
        dialog._show_error(opds.AuthError('x'), '')
        self.assertEqual(dialog.message_label.get_text(),
                         'This catalogue asks for a user name and password')
        feed = opds.Feed(url='https://books.example/opds', title='Harbour Lane')
        dialog._save('https://books.example/opds', 'reader', 'pw', feed)
        catalogs = opds.load_catalogs(self.app.settings.get_string('catalogs'))
        self.assertEqual((catalogs[-1].title, catalogs[-1].username),
                         ('Harbour Lane', 'reader'))
        self.assertNotIn('pw', self.app.settings.get_string('catalogs'))
        edit = add_catalog.CatalogDialog(self.app, catalogs[-1])
        self.assertEqual(edit.url_row.get_text(), 'https://books.example/opds')


BASE = 'https://books.example/opds/new?page=1'


if __name__ == '__main__':
    unittest.main()
