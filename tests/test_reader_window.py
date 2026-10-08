# SPDX-License-Identifier: GPL-2.0-or-later
# SPDX-FileCopyrightText: 2026 Jack Tully

"""The reader window: one per book, its status pages (a missing file, a PDF, no WebKit),
the keys it binds, and with WebKit a book read: the place saved, a bookmark and a highlight
added, the book marked finished at its end."""

import pathlib
import shutil
import tempfile
import unittest
from unittest import mock

from tests import ROOT  # noqa: F401  (registers src/ as bookcase)
from tests.gtk import pump, requires_gtk, wait_for
from tests.support import make_epub

from bookcase.formats import BookInfo
from bookcase.library import Library

WAIT = 15


_apps = []


def _app(library):
    """The one application of the test process (an application registers once), given
    this test's library."""
    from gi.repository import Adw, Gio

    class FakeApp(Adw.Application):
        """The seams the reader window uses: library, settings, undo()."""

        def undo(self):
            self.undone.append(self.library.undo())

    if not _apps:
        app = FakeApp(application_id='io.github.jackicus.Bookcase.Test',
                      flags=Gio.ApplicationFlags.NON_UNIQUE)
        app.settings = Gio.Settings.new('io.github.jackicus.Bookcase')
        app.register(None)
        _apps.append(app)
    app = _apps[0]
    app.library = library
    app.undone = []
    return app


@requires_gtk
class ReaderWindowTest(unittest.TestCase):

    def setUp(self):
        self.directory = pathlib.Path(tempfile.mkdtemp(prefix='bookcase-test-'))
        self.library = Library(self.directory / 'library.sqlite')
        self.app = _app(self.library)
        self.windows = []

    def tearDown(self):
        for window in self.windows:
            window.close()
        pump()
        self.library.close()
        shutil.rmtree(self.directory, ignore_errors=True)

    def add(self, name='A Quiet Harbour.epub', write=True):
        path = self.directory / name
        if write and path.suffix == '.epub':
            make_epub(path, chapters=4)
        elif write:
            path.write_bytes(b'%PDF-1.4\n%invented\n')
        info = BookInfo(title='A Quiet Harbour', authors=['Ada Lark'], format=path.suffix[1:])
        return self.library.add_book(info, str(path), hash=name, size=100)

    def open(self, book_id):
        from bookcase import reader_window

        window = reader_window.open(self.app, book_id)
        self.windows.append(window)
        return window

    def test_one_window_per_book(self):
        book_id = self.add('missing.epub', write=False)
        window = self.open(book_id)
        self.assertIs(self.open(book_id), window)
        self.assertEqual(window.get_title(), 'A Quiet Harbour')

    def test_a_missing_file_can_be_located(self):
        window = self.open(self.add('missing.epub', write=False))
        self.assertEqual(window.content_stack.get_visible_child_name(), 'status')
        self.assertEqual(window.status_page.get_title(), 'File Not Found')
        button = window.status_buttons.get_first_child()
        self.assertEqual(button.get_label(), 'Locate File…')
        self.assertFalse(window.bookmark_button.get_sensitive())

    def test_a_pdf_opens_elsewhere(self):
        window = self.open(self.add('A Quiet Harbour.pdf'))
        self.assertEqual(window.content_stack.get_visible_child_name(), 'status')
        self.assertEqual(window.status_buttons.get_first_child().get_label(),
                         'Open in Document Viewer')

    def test_without_webkit_a_status_page_says_so(self):
        from bookcase.widgets import book_view

        with mock.patch.object(book_view, 'available', return_value=False):
            window = self.open(self.add())
        self.assertEqual(window.status_page.get_title(), 'Reading Needs WebKitGTK')

    def test_the_keys_of_the_reader_table(self):
        from gi.repository import Gdk

        from bookcase import reader_window

        keys = reader_window._keymap()
        control = int(Gdk.ModifierType.CONTROL_MASK)
        self.assertEqual(keys[(Gdk.KEY_l, 0)], 'next')
        self.assertEqual(keys[(Gdk.KEY_space, int(Gdk.ModifierType.SHIFT_MASK))], 'previous')
        self.assertEqual(keys[(Gdk.KEY_d, control)], 'bookmark')
        self.assertEqual(keys[(Gdk.KEY_g, control | int(Gdk.ModifierType.SHIFT_MASK))],
                         'search-previous')

    def test_typography_follows_the_settings(self):
        window = self.open(self.add('missing.epub', write=False))
        settings = self.app.settings
        self.addCleanup(settings.reset, 'reader-font-size')
        self.addCleanup(settings.reset, 'reader-scrolled')
        self.addCleanup(settings.reset, 'reader-theme')
        settings.set_int('reader-font-size', 22)
        self.assertEqual(window.size_label.get_label(), '22 px')
        window._change_font_size(1)
        self.assertEqual(settings.get_int('reader-font-size'), 23)
        settings.set_boolean('reader-scrolled', True)
        self.assertEqual(window.layout_group.get_active_name(), 'scrolled')
        window._theme_chips['sepia'].set_active(True)
        self.assertEqual(settings.get_string('reader-theme'), 'sepia')
        pump()
        self.assertTrue(window.toolbar_view.has_css_class('theme-sepia'))


@requires_gtk
class ReadingTest(unittest.TestCase):
    """A book read in a real WebKit view (skipped without one)."""

    def setUp(self):
        from bookcase.widgets import book_view

        if not book_view.available():
            self.skipTest('WebKitGTK 6.0 is not available (or BOOKCASE_NO_WEBKIT)')
        self.directory = pathlib.Path(tempfile.mkdtemp(prefix='bookcase-test-'))
        self.library = Library(self.directory / 'library.sqlite')
        self.app = _app(self.library)
        path = make_epub(self.directory / 'A Quiet Harbour.epub', chapters=4)
        info = BookInfo(title='A Quiet Harbour', authors=['Ada Lark'], format='epub')
        self.book_id = self.library.add_book(info, str(path), hash='h', size=100)

        from bookcase import reader_window

        self.window = reader_window.open(self.app, self.book_id)
        if not wait_for(lambda: self.window.book_view._ready, WAIT):
            self.window.close()
            self.skipTest('the WebKit web process did not start')
        self.assertTrue(wait_for(lambda: self.window._place is not None, WAIT))

    def tearDown(self):
        self.window.close()
        pump()
        self.library.close()
        shutil.rmtree(self.directory, ignore_errors=True)

    def test_reading_saves_the_place_bookmarks_and_highlights(self):
        window = self.window
        self.assertEqual(window.window_title.get_subtitle(), 'Chapter 1')
        self.assertTrue(window.toc_list.get_selected_row() is not None)
        window.book_view.next_section()
        self.assertTrue(wait_for(lambda: (window._place.get('chapter') or {}).get('label')
                                 == 'Chapter 2', WAIT))

        window._toggle_bookmark()
        bookmarks = self.library.annotations(self.book_id, 'bookmark')
        self.assertEqual(len(bookmarks), 1)
        self.assertEqual(bookmarks[0].text, 'Chapter 2')
        self.assertTrue(wait_for(lambda: window.bookmark_button.get_active(), 5))

        window._on_selection(window.book_view, {
            'cfi': window._place['cfi'], 'text': 'the gulls', 'fraction': 0.3,
            'rect': {'x': 10, 'y': 200, 'width': 50, 'height': 20}})
        window._on_color('green')
        highlights = self.library.annotations(self.book_id, 'highlight')
        self.assertEqual([(h.text, h.color) for h in highlights], [('the gulls', 'green')])
        self.assertEqual(window.annotations_stack.get_visible_child_name(), 'list')

        window._remove_annotation_of(highlights[0].id)
        self.assertEqual(self.library.annotations(self.book_id, 'highlight'), [])

        place = window._place
        window.close()
        book = self.library.book(self.book_id)
        self.assertEqual(book.location, place['cfi'])
        self.assertAlmostEqual(book.progress, place['fraction'], places=3)
        self.assertEqual(book.status, 'reading')

    def test_the_end_marks_the_book_finished(self):
        window = self.window
        window.book_view.end()
        self.assertTrue(wait_for(lambda: self.library.book(self.book_id).status == 'finished',
                                 WAIT))


if __name__ == '__main__':
    unittest.main()
