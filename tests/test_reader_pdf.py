# SPDX-License-Identifier: GPL-2.0-or-later
# SPDX-FileCopyrightText: 2026 Jack Tully

"""A PDF in the reader window (reader_pdf.py): its zoom and layout kept per book, the
right-to-left and cover switches, Print… and Read Aloud from the PdfView."""

import pathlib
import shutil
import tempfile
import unittest
from unittest import mock

from tests import ROOT  # noqa: F401  (registers src/ as bookcase)
from tests.gtk import pump, requires_gtk, wait_for
from tests.support import make_paged_pdf

from bookcase.formats import BookInfo
from bookcase.library import Library

WAIT = 3

_apps = []


def _app(library):
    """This module's application (its own ID: test_reader_window registers another),
    made once, given this test's library."""
    from gi.repository import Adw, Gio

    if not _apps:
        app = Adw.Application(application_id='io.github.jackicus.Bookcase.TestPdf',
                              flags=Gio.ApplicationFlags.NON_UNIQUE)
        app.settings = Gio.Settings.new('io.github.jackicus.Bookcase')
        app.register(None)
        _apps.append(app)
    app = _apps[0]
    app.library = library
    return app


class QuietEngine:
    """A speech engine that says each sentence at once, silently."""

    def __init__(self):
        self.spoken = []

    def speak(self, text, done, rate=0, language='', **_kwargs):
        self.spoken.append(text)

    def stop(self):
        pass

    def close(self):
        pass


@requires_gtk
class ReaderPdfTest(unittest.TestCase):

    def setUp(self):
        from bookcase.widgets import pdf_view

        if not pdf_view.available():
            self.skipTest('Poppler or pycairo is not available')
        self.directory = pathlib.Path(tempfile.mkdtemp(prefix='bookcase-test-'))
        self.library = Library(self.directory / 'library.sqlite')
        self.app = _app(self.library)
        self.app.settings.set_boolean('reader-pdf-scrolled', True)
        self.windows = []
        path = make_paged_pdf(self.directory / 'A Quiet Harbour.pdf', pages=6)
        info = BookInfo(title='A Quiet Harbour', authors=['Ada Lark'], format='pdf')
        self.book_id = self.library.add_book(info, str(path), hash='pdf', size=100)

    def tearDown(self):
        for window in self.windows:
            window.close()
        pump()
        self.library.close()
        self.app.settings.reset('reader-pdf-scrolled')
        shutil.rmtree(self.directory, ignore_errors=True)

    def open(self):
        from bookcase import reader_window

        window = reader_window.open(self.app, self.book_id)
        self.windows.append(window)
        self.assertTrue(wait_for(lambda: window._place is not None, WAIT))
        return window

    def kept(self):
        return self.library.book_state(self.book_id).get('pdf', {})

    def test_the_zoom_and_layout_are_kept_per_book(self):
        window = self.open()
        self.assertEqual(self.kept(), {})  # opening keeps nothing
        window.view.set_fit('width')
        window._pdf.save()
        self.assertEqual(self.kept(), {'fit': 'width', 'flow': 'scrolled'})
        window.view.zoom_in()
        percent = window.view.zoom_percent
        window.layout_group.set_active_name('paginated')
        self.assertEqual(self.kept(), {'zoom': percent, 'flow': 'paginated'})
        self.assertFalse(self.app.settings.get_boolean('reader-pdf-scrolled'))
        # another window's change of the setting leaves this book as it is
        self.app.settings.set_boolean('reader-pdf-scrolled', True)
        pump()
        self.assertFalse(window._layout_key_value())
        self.assertEqual(window.layout_group.get_active_name(), 'paginated')
        window.close()
        self.windows.remove(window)
        pump()
        window = self.open()
        self.assertEqual(window.view.zoom_percent, percent)
        self.assertIsNone(window.view.fit)
        self.assertFalse(window.view._continuous)
        self.assertTrue(wait_for(lambda: window.size_label.get_label() == f'{percent}%',
                                 WAIT))

    def test_right_to_left_and_the_cover(self):
        window = self.open()
        pdf = window._pdf
        window.layout_group.set_active_name('paginated')
        self.assertTrue(pdf.rtl_row.get_sensitive())
        self.assertFalse(pdf.rtl_row.get_active())
        pdf.rtl_row.set_active(True)
        self.assertTrue(window.view.rtl)
        self.assertTrue(window.progress_scale.get_inverted())
        self.assertEqual(window.prev_button.get_tooltip_text(), 'Next Page')
        self.assertTrue(self.kept()['rtl'])
        window.two_pages_row.set_active(True)
        self.assertTrue(pdf.cover_row.get_sensitive())
        pdf.cover_row.set_active(False)
        self.assertFalse(window.view.cover)
        self.assertIs(self.kept()['cover'], False)
        window.layout_group.set_active_name('scrolled')
        self.assertFalse(pdf.rtl_row.get_sensitive())

    def test_print(self):
        from gi.repository import Gdk

        window = self.open()
        menu = window.menu_button.get_menu_model().get_item_link(0, 'section')
        labels = [menu.get_item_attribute_value(n, 'label').unpack()
                  for n in range(menu.get_n_items())]
        self.assertIn('Print…', labels)
        with mock.patch.object(window.view, 'print_document') as print_document:
            control = Gdk.ModifierType.CONTROL_MASK
            self.assertTrue(window._on_key(None, Gdk.KEY_p, 0, control))
            window.activate_action('win.print', None)
        self.assertEqual(print_document.call_count, 2)
        print_document.assert_called_with(window)

    def test_read_aloud_reads_the_pdf(self):
        from bookcase import speech

        engine = QuietEngine()
        with mock.patch.object(speech, '_engine', [engine]):
            window = self.open()
            self.assertIsNotNone(window._read_aloud)
            self.assertTrue(window.lookup_action('read-aloud').get_enabled())
            self.assertTrue(window._toggle_read_aloud())
            self.assertTrue(wait_for(lambda: engine.spoken, WAIT))
            self.assertEqual(engine.spoken[0], 'Page 1 of the harbour')
            self.assertTrue(window.view._tts_parts)
            window._read_aloud.stop()
            self.assertIsNone(window.view._tts_parts)


if __name__ == '__main__':
    unittest.main()
