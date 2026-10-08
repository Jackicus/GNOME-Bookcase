# SPDX-License-Identifier: GPL-2.0-or-later
# SPDX-FileCopyrightText: 2026 Jack Tully

"""Look Up's panel and sheet, and Read Aloud's bar, with stand-in services (no network, no
speech engine): they build, show an answer, say when offline; the reader window offers Read
Aloud only with an engine, and shows a selected word's definition in its popover."""

import pathlib
import shutil
import tempfile
import time
import unittest
from unittest import mock

from tests import ROOT  # noqa: F401
from tests.gtk import pump, requires_gtk, wait_for

from bookcase import lookup, speech
from bookcase.formats import BookInfo
from bookcase.library import Library
from bookcase.online import OnlineError

ARTICLE = lookup.Article('quillon', 'wiktionary', (
    lookup.Entry('Noun', 'English', (
        lookup.Sense('A small invented harbour.', ('The quillon was full.',)),
        lookup.Sense('A refuge.'),
    )),
), 'https://en.wiktionary.org/wiki/quillon')
SUMMARY = lookup.Summary('Quillon Bay', 'Invented bay', 'Quillon Bay is an invented bay.',
                         'https://en.wikipedia.org/wiki/Quillon_Bay')


class FakeService:
    """Answers on the main loop like lookup.Lookup; `error` instead when set."""

    def __init__(self, error=None):
        self.error = error
        self.asked = []

    def _answer(self, value, callback):
        from gi.repository import GLib

        task = lookup._DoneTask()

        def deliver():
            if not task.cancelled:
                callback(None, self.error) if self.error else callback(value, None)
            return GLib.SOURCE_REMOVE

        GLib.idle_add(deliver)
        return task

    def define(self, word, language, callback):
        self.asked.append(('define', word, language))
        return self._answer(ARTICLE, callback)

    def summarize(self, text, language, callback):
        self.asked.append(('summarize', text, language))
        return self._answer(SUMMARY, callback)


def labels(widget):
    found = []
    child = widget.get_first_child()
    while child is not None:
        if child.get_visible():
            if hasattr(child, 'get_label') and child.__class__.__name__ == 'Label':
                found.append(child.get_label())
            found += labels(child)
        child = child.get_next_sibling()
    return found


@requires_gtk
class LookupPanelTest(unittest.TestCase):

    def test_a_word_shows_its_definition_then_wikipedia(self):
        from bookcase.widgets.lookup_popover import LookupPanel

        started = time.monotonic()
        searched = []
        service = FakeService()
        panel = LookupPanel(on_search=searched.append, service=service)
        panel.show_text('Quillon,', 'en')
        self.assertTrue(panel.toggles.get_visible())
        self.assertEqual(panel.stack.get_visible_child_name(), 'loading')
        self.assertTrue(wait_for(lambda: panel.stack.get_visible_child_name() == 'result', 1))
        self.assertEqual(service.asked, [('define', 'Quillon', 'en')])
        shown = labels(panel.result)
        self.assertIn('quillon', shown)
        self.assertIn('Noun · English', shown)
        self.assertIn('1. A small invented harbour.', shown)
        self.assertIn('“The quillon was full.”', shown)
        self.assertEqual(panel.page_url(), ARTICLE.url)

        panel.toggles.set_active_name('wikipedia')
        self.assertTrue(wait_for(lambda: panel.answer is SUMMARY, 1))
        self.assertIn('Quillon Bay is an invented bay.', labels(panel.result))
        self.assertEqual(panel.page_url(), SUMMARY.url)
        panel.search_button.emit('clicked')
        self.assertEqual(searched, ['Quillon,'])
        self.assertLess(time.monotonic() - started, 1)

    def test_a_phrase_goes_to_wikipedia(self):
        from bookcase.widgets.lookup_popover import LookupPanel

        service = FakeService()
        panel = LookupPanel(service=service)
        panel.show_text('the  bay of quillon', 'fr')
        self.assertFalse(panel.toggles.get_visible())
        self.assertTrue(wait_for(lambda: panel.answer is not None, 1))
        self.assertEqual(service.asked, [('summarize', 'the bay of quillon', 'fr')])

    def test_offline_says_so_and_cancel_drops_the_answer(self):
        from bookcase.widgets.lookup_popover import LookupPanel

        panel = LookupPanel(service=FakeService(OnlineError('Could not reach', offline=True)))
        panel.show_text('quillon')
        self.assertTrue(wait_for(lambda: panel.stack.get_visible_child_name() == 'message', 1))
        self.assertEqual(panel.message_title.get_label(), 'No Connection')

        panel = LookupPanel(service=FakeService())
        panel.show_text('quillon')
        panel.cancel()
        pump(50)
        self.assertIsNone(panel.answer)

    def test_the_bottom_sheet(self):
        from gi.repository import Adw

        from bookcase.widgets.lookup_popover import present_sheet

        # An Adw.Window shows the sheet inside it (a Gtk.Window would get a second toplevel).
        window = Adw.Window(default_width=360, default_height=640)
        window.dialog_open = []
        window.set_dialog_open = window.dialog_open.append
        window.present()
        dialog = present_sheet(window, 'quillon', 'en', service=FakeService())
        self.assertEqual(dialog.get_presentation_mode(), Adw.DialogPresentationMode.BOTTOM_SHEET)
        self.assertTrue(wait_for(lambda: dialog.panel.answer is ARTICLE, 1))
        dialog.close()
        pump(100)
        self.assertEqual(window.dialog_open, [True, False])
        window.destroy()
        pump(100)


class FakeEngine:
    def __init__(self):
        self.spoken = []

    def speak(self, text, done, rate=0, language=''):
        self.spoken.append(text)

    def stop(self):
        pass

    def close(self):
        pass


class FakeView:
    """A BookView's tts_* calls, from a list of sentences."""

    def __init__(self, sentences):
        self.sentences = list(sentences)
        self.stopped = 0

    def weak_ref(self):
        return lambda: self

    def tts_start(self, callback):
        callback(True)

    def tts_next(self, callback):
        callback(self.sentences.pop(0) if self.sentences else None)

    def tts_stop(self):
        self.stopped += 1


@requires_gtk
class ReadAloudBarTest(unittest.TestCase):

    def test_the_bar_plays_pauses_and_hides_when_stopped(self):
        from gi.repository import Gio

        from bookcase.widgets.read_aloud import ReadAloudBar

        settings = Gio.Settings.new('io.github.jackicus.Bookcase')
        settings.set_int('reader-speech-rate', 30)
        engine = FakeEngine()
        view = FakeView(['One.', 'Two.'])
        bar = ReadAloudBar(view, engine, settings, 'en')
        self.assertEqual(bar.player.rate, 30)
        bar.toggle()
        self.assertTrue(bar.get_reveal_child())
        self.assertEqual(engine.spoken, ['One.'])
        self.assertEqual(bar.status.get_label(), 'Reading Aloud')
        bar.toggle()
        self.assertEqual(bar.state, 'paused')
        self.assertEqual(bar.play_button.get_icon_name(), 'media-playback-start-symbolic')
        settings.set_int('reader-speech-rate', -20)
        self.assertEqual(bar.player.rate, -20)
        bar.stop()
        self.assertFalse(bar.get_reveal_child())
        self.assertEqual(view.stopped, 1)
        bar.close()
        settings.reset('reader-speech-rate')


_apps = []


@requires_gtk
class ReaderWindowLookupTest(unittest.TestCase):

    def setUp(self):
        from gi.repository import Adw, Gio

        self.directory = pathlib.Path(tempfile.mkdtemp(prefix='bookcase-test-'))
        self.library = Library(self.directory / 'library.sqlite')
        if not _apps:
            app = Adw.Application(application_id='io.github.jackicus.Bookcase.LookupTest',
                                  flags=Gio.ApplicationFlags.NON_UNIQUE)
            app.settings = Gio.Settings.new('io.github.jackicus.Bookcase')
            app.register(None)
            _apps.append(app)
        self.app = _apps[0]
        self.app.library = self.library
        path = self.directory / 'missing.epub'
        info = BookInfo(title='A Quiet Harbour', authors=['Ada Lark'], format='epub',
                        language='en')
        self.book_id = self.library.add_book(info, str(path), hash='x', size=1)
        self.saved_engine = list(speech._engine)
        self.windows = []

    def tearDown(self):
        for window in self.windows:
            window._selection_popover.popdown()
            window.close()  # never shown: close-request unparents the popover, stops the bar
            window.destroy()
        self.windows = []
        pump(100)
        speech._engine[:] = self.saved_engine
        self.library.close()
        shutil.rmtree(self.directory, ignore_errors=True)

    def open(self):
        from bookcase import reader_window

        window = reader_window.ReaderWindow(self.app, self.book_id)
        self.windows.append(window)
        return window

    def menu_labels(self, window):
        from gi.repository import Gio

        menu = window.menu_button.get_menu_model()
        section = menu.get_item_link(0, Gio.MENU_LINK_SECTION)
        return [section.get_item_attribute_value(i, 'label').unpack()
                for i in range(section.get_n_items())]

    def test_read_aloud_only_with_an_engine(self):
        speech._engine[:] = [None]
        window = self.open()
        self.assertIsNone(window.lookup_action('read-aloud'))
        self.assertNotIn('Read Aloud', self.menu_labels(window))
        speech._engine[:] = [FakeEngine()]
        window = self.open()
        self.assertIsNotNone(window.lookup_action('read-aloud'))
        self.assertIn('Read Aloud', self.menu_labels(window))

    def test_a_selected_word_is_defined_in_the_popover(self):
        from bookcase.widgets import lookup_popover

        speech._engine[:] = [None]
        lookup.service().remember('define', 'quillon', 'en', ARTICLE)
        window = self.open()  # never shown: the test asks for the wide layout
        window._selection = {'cfi': 'epubcfi(/6/2!/4/2)', 'text': 'quillon', 'fraction': 0.1,
                             'annotation_id': None}
        with mock.patch.object(lookup_popover, 'inline', return_value=True):
            window._show_lookup('quillon')
        self.assertTrue(window._lookup_panel.get_visible())
        self.assertFalse(window._selection_buttons['lookup'].get_visible())
        self.assertTrue(wait_for(lambda: window._lookup_panel.answer is ARTICLE, 1))
        with mock.patch.object(lookup_popover, 'inline', return_value=True):
            window._show_lookup('the bay at dusk')
        self.assertFalse(window._lookup_panel.get_visible())
        self.assertTrue(window._selection_buttons['lookup'].get_visible())


if __name__ == '__main__':
    unittest.main()
