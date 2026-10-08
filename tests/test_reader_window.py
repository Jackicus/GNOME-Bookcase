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
from tests.support import make_epub, make_paged_pdf

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
        elif write and path.suffix == '.pdf':
            make_paged_pdf(path, pages=4)
        elif write and path.suffix == '.txt':
            path.write_text('CHAPTER I\n\nThe lamps along the harbour wall.\n')
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

    def test_a_pdf_opens_in_the_reader(self):
        from bookcase import pdf_location
        from bookcase.widgets import pdf_view

        if not pdf_view.available():
            self.skipTest('Poppler or pycairo is not available')
        window = self.open(self.add('A Quiet Harbour.pdf'))
        self.assertTrue(window.is_pdf)
        self.assertIsInstance(window.view, pdf_view.PdfView)
        self.assertEqual(window.content_stack.get_visible_child_name(), 'book')
        self.assertFalse(window.font_group.get_visible())  # zoom and layout, not typefaces
        self.assertTrue(wait_for(lambda: window._place is not None, 3))
        self.assertEqual(window.window_title.get_subtitle(), 'Part 1')
        window._change_font_size(1)  # Ctrl+plus: zoom in
        self.assertIsNone(window.view.fit)
        self.assertTrue(window.size_label.get_label().endswith('%'))
        window.view.go_right()
        self.assertTrue(wait_for(lambda: window._place['page'] == 2, 3))
        window._toggle_bookmark()
        self.assertEqual([a.location for a in self.library.annotations(window.book_id)],
                         ['page:2'])
        place = window._place
        window.close()
        book = self.library.book(window.book_id)
        self.assertEqual(pdf_location.parse(book.location).page, 2)
        self.assertAlmostEqual(book.progress, place['fraction'], places=3)

    def test_a_text_is_converted_and_opened(self):
        from bookcase.widgets import book_view

        window = self.open(self.add('A Quiet Harbour.txt'))
        if not book_view.available():
            self.assertEqual(window.status_page.get_title(), 'Reading Needs WebKitGTK')
            return
        self.assertEqual(window.content_stack.get_visible_child_name(), 'loading')
        self.assertTrue(wait_for(
            lambda: window.content_stack.get_visible_child_name() == 'book', 3))
        self.assertFalse(window.is_pdf)

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

    def open_pdf(self, pages=None, location=None, progress=None, status=None):
        from bookcase.widgets import pdf_view

        if not pdf_view.available():
            self.skipTest('Poppler or pycairo is not available')
        book_id = self.add('A Quiet Harbour.pdf')
        if location is not None:
            self.library.set_progress(book_id, progress, location)
        if status is not None:
            self.library.set_status([book_id], status)
        window = self.open(book_id)
        self.assertTrue(wait_for(lambda: window._place is not None, 3))
        return window

    def test_closing_lets_go_of_the_destroy_signal(self):
        # A Python handler run again when the window is finalized (by the garbage
        # collector) crashed the app.
        window = self.open(self.add('missing.epub', write=False))
        self.assertTrue(window.handler_is_connected(window._destroy_handler))
        handler = window._destroy_handler
        window.close()
        self.assertEqual(window._destroy_handler, 0)
        self.assertFalse(window.handler_is_connected(handler))

    def test_nothing_is_saved_after_closing(self):
        window = self.open_pdf()
        window.close()
        window._on_relocated(window.view, {'fraction': 0.5, 'cfi': 'page:3'})
        self.assertEqual(window._save_source, 0)
        self.assertNotEqual(self.library.book(window.book_id).location, 'page:3')

    def test_opening_at_the_end_does_not_mark_the_book_finished(self):
        # read to the last page, then marked as reading again by the user
        window = self.open_pdf(location='page:4', progress=0.9, status='reading')
        self.assertTrue(window._place['atEnd'])
        pump()
        self.assertEqual(self.library.book(window.book_id).status, 'reading')
        window.view.start()
        self.assertTrue(wait_for(lambda: window._place['page'] == 1, 3))
        window.view.end()  # reaching the end does
        self.assertTrue(wait_for(
            lambda: self.library.book(window.book_id).status == 'finished', 3))

    def test_a_book_read_to_its_end_and_marked_unread_opens_at_its_start(self):
        window = self.open_pdf(location='page:4', progress=1.0, status='unread')
        self.assertEqual(window._place['page'], 1)
        pump()
        self.assertNotEqual(self.library.book(window.book_id).status, 'finished')

    def test_a_finished_book_opens_where_it_was_left(self):
        window = self.open_pdf(location='page:4', progress=1.0, status='finished')
        self.assertEqual(window._place['page'], 4)

    def test_a_pdf_goes_to_a_page_and_a_failed_book_logs_no_session(self):
        window = self.open_pdf()
        self.assertTrue(window._go_to_entered('3', pages=4))
        self.assertTrue(wait_for(lambda: window._place['page'] == 3, 3))
        self.assertTrue(window._go_to_entered('99', pages=4))  # the last page
        self.assertTrue(wait_for(lambda: window._place['page'] == 4, 3))
        self.assertFalse(window._go_to_entered('three', pages=4))
        window._on_view_error(window.view, 'broken')
        self.assertIsNone(window._clock)

    def test_arrows_move_through_the_sidebar_reached_by_tab(self):
        from gi.repository import Gdk

        window = self.open_pdf()
        window._show_sidebar('contents')
        row = window.toc_list.get_row_at_index(0)
        row.grab_focus()
        window.set_focus_visible(False)  # a click left it there: the arrows read on
        self.assertTrue(window._on_key(None, Gdk.KEY_Down, 0, 0))
        window.set_focus_visible(True)  # Tab took it there: the list's
        self.assertFalse(window._on_key(None, Gdk.KEY_Down, 0, 0))
        self.assertFalse(window._on_key(None, Gdk.KEY_space, 0, 0))
        control = Gdk.ModifierType.CONTROL_MASK
        self.assertTrue(window._on_key(None, Gdk.KEY_d, 0, control))  # shortcuts still work

    def test_right_to_left_books_and_fixed_layouts(self):
        window = self.open(self.add('missing.epub', write=False))
        window._on_loaded(window.view, {'dir': 'rtl', 'sectionFractions': []})
        self.assertTrue(window.progress_scale.get_inverted())
        self.assertEqual(window.prev_button.get_tooltip_text(), 'Next Page')
        self.assertEqual(window.next_button.get_tooltip_text(), 'Previous Page')

        settings = self.app.settings
        self.addCleanup(settings.reset, 'reader-font-size')
        window._on_loaded(window.view, {'dir': 'ltr', 'fixedLayout': True})
        self.assertFalse(window.font_group.get_visible())
        size = settings.get_int('reader-font-size')
        window._change_font_size(1)  # a comic has no text size: other books keep theirs
        self.assertEqual(settings.get_int('reader-font-size'), size)

    def test_a_comic_names_its_pages_not_its_files(self):
        from bookcase.widgets import book_view

        with mock.patch.object(book_view, 'available', return_value=False):
            window = self.open(self.add('A Quiet Harbour.cbz'))
        toc = [{'label': '001.png', 'href': '001.png'}, {'label': '002.png', 'href': '002.png'}]
        window._on_toc(window.view, toc)
        self.assertEqual(window.toc_list.get_row_at_index(1).get_child().get_label(), 'Page 2')
        window._place = {'fraction': 0.5, 'chapter': {'label': '002.png', 'href': '002.png'}}
        window._update_title()
        self.assertEqual(window.window_title.get_subtitle(), 'Page 2')

    def test_add_to_library_goes_at_once(self):
        from bookcase.library import OPENED

        path = self.directory / 'opened.epub'
        info = BookInfo(title='A Quiet Harbour', authors=['Ada Lark'], format='epub')
        book_id = self.library.add_opened(info, str(path), hash='o', size=1)
        self.assertEqual(self.library.book(book_id).source, OPENED)
        kept = []
        self.app.keep_book = kept.append
        self.addCleanup(delattr, self.app, 'keep_book')
        window = self.open(book_id)
        self.assertTrue(window._keep_banner.get_revealed())
        window._keep_book()
        self.assertEqual(kept, [book_id])
        self.assertFalse(window._keep_banner.get_revealed())
        window._on_library_changed(self.library, 'books')  # not back while it is copied
        self.assertFalse(window._keep_banner.get_revealed())

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
        self.assertFalse(window.two_pages_row.get_sensitive())  # one column when scrolling
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

    def test_imported_highlights_find_their_place(self):
        window = self.window
        found = self.library.add_annotation(
            self.book_id, 'highlight', '', text='the gulls said enough for everyone',
            position=0.5)
        lost = self.library.add_annotation(self.book_id, 'highlight', '',
                                           text='never in this book')
        window._find_imported_highlights()
        self.assertTrue(wait_for(lambda: self.library.annotation(found).location, WAIT))
        self.assertTrue(self.library.annotation(found).location.startswith('epubcfi('))
        self.assertEqual(self.library.annotation(lost).location, '')
        self.assertEqual(self.library.undo(), 'Add Highlight')  # finding it is no undo step

    def test_the_end_marks_the_book_finished(self):
        window = self.window
        window.book_view.end()
        self.assertTrue(wait_for(lambda: self.library.book(self.book_id).status == 'finished',
                                 WAIT))


if __name__ == '__main__':
    unittest.main()
