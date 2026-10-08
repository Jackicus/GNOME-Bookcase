# SPDX-License-Identifier: GPL-2.0-or-later
# SPDX-FileCopyrightText: 2026 Jack Tully

"""Read Aloud through the reader page (a real WebKit view, skipped without it): the page
gives the book a sentence at a time from the page shown, on into the next chapter, then None
at the end; and the player reads it all with a stand-in engine."""

import pathlib
import shutil
import tempfile
import unittest

from tests import ROOT  # noqa: F401
from tests.gtk import requires_gtk, wait_for
from tests.support import make_epub

from bookcase import reading, speech

WAIT = 15
STYLE = reading.build_style({
    'reader-theme': 'light', 'reader-font': 'publisher', 'reader-custom-font': '',
    'reader-font-size': 18, 'reader-line-height': 1.5, 'reader-margin': 8,
    'reader-max-width': 720, 'reader-justify': True, 'reader-hyphenate': True,
    'reader-publisher-styles': True, 'reader-scrolled': False, 'reader-two-pages': False,
    'reader-animate': False}.get, False, 'A Quiet Harbour')


class QuickEngine:
    """Says each sentence at once (on the main loop)."""

    def __init__(self):
        self.spoken = []

    def speak(self, text, done, rate=0, language=''):
        from gi.repository import GLib

        self.spoken.append(text)
        GLib.idle_add(lambda: (done(), GLib.SOURCE_REMOVE)[1])

    def stop(self):
        pass


@requires_gtk
class ReadAloudPageTest(unittest.TestCase):

    @classmethod
    def setUpClass(cls):
        from gi.repository import Gtk

        from bookcase.widgets import book_view

        if not book_view.available():
            raise unittest.SkipTest('WebKitGTK 6.0 is not available (or BOOKCASE_NO_WEBKIT)')
        cls.directory = pathlib.Path(tempfile.mkdtemp(prefix='bookcase-test-'))
        path = make_epub(cls.directory / 'A Quiet Harbour.epub', chapters=2)
        cls.window = Gtk.Window(default_width=800, default_height=600)
        cls.view = book_view.BookView()
        cls.window.set_child(cls.view)
        cls.window.present()
        cls.places = []
        cls.view.connect('relocated', lambda _view, place: cls.places.append(place))
        cls.view.open(str(path), 'epub', style=STYLE)
        if not wait_for(lambda: cls.view._ready, WAIT):
            cls.tearDownClass()
            raise unittest.SkipTest('the WebKit web process did not start')
        if not wait_for(lambda: any(p.get('fraction') is not None for p in cls.places), WAIT):
            raise AssertionError('the book never showed')

    @classmethod
    def tearDownClass(cls):
        cls.view.close()
        cls.window.destroy()
        shutil.rmtree(cls.directory, ignore_errors=True)

    def call(self, method):
        got = []
        method(got.append)
        self.assertTrue(wait_for(lambda: got, WAIT), 'the page never answered')
        return got[0]

    def test_sentences_from_the_page_to_the_end_of_the_book(self):
        view = self.view
        self.assertTrue(self.call(view.tts_start))
        sentences = []
        for _index in range(40):
            text = self.call(view.tts_next)
            if text is None:
                break
            sentences.append(text)
        self.assertEqual(sentences[0], 'Chapter 1')
        self.assertEqual(sentences[1],
                         'The lamps along the harbour wall were lit one by one as the tide '
                         'came in.')
        self.assertIn('Chapter 2', sentences)  # on into the next chapter
        self.assertEqual(len(sentences), 20)  # a heading and nine sentences a chapter
        self.assertEqual((self.places[-1].get('chapter') or {}).get('label'), 'Chapter 2')
        view.tts_stop()

    def test_the_player_reads_the_book_with_an_engine(self):
        from bookcase.widgets.read_aloud import ViewSource

        self.view.go_to_fraction(0)
        wait_for(lambda: False, 0.5)
        engine = QuickEngine()
        player = speech.ReadAloud(engine, ViewSource(self.view))
        player.play()
        self.assertTrue(wait_for(lambda: player.state == 'stopped', WAIT))
        self.assertEqual(len(engine.spoken), 20)


if __name__ == '__main__':
    unittest.main()
