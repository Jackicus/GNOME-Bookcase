# SPDX-License-Identifier: GPL-2.0-or-later
# SPDX-FileCopyrightText: 2026 Jack Tully

"""The reader's extras, as widgets with stand-ins (no WebKit, no speech engine, no network):
Read Aloud's skip buttons and Voice menu, Look Up's preferences (when, online, the
dictionaries' order), the custom paper's dialog, a fixed layout's zoom controls, the
printed book's page numbers in Go to Page, and the reader window's keys for them."""

import pathlib
import shutil
import tempfile
import unittest

from tests import ROOT  # noqa: F401
from tests.gtk import pump, requires_gtk, wait_for
from tests.test_lookup import WORDS, make_stardict

from bookcase import lookup, speech
from bookcase.formats import BookInfo
from bookcase.library import Library

SCHEMA_ID = 'io.github.jackicus.Bookcase'


class FakeEngine:
    def __init__(self, voices=()):
        self.spoken = []
        self.voices = []
        self._voices = list(voices)
        self.listed = 0

    def speak(self, text, done, rate=0, language='', voice='', on_word=None):
        self.spoken.append(text)
        self.voices.append(voice)

    def stop(self):
        pass

    def close(self):
        pass

    def list_voices(self, callback):
        from gi.repository import GLib

        self.listed += 1
        GLib.idle_add(lambda: (callback(self._voices), GLib.SOURCE_REMOVE)[1])


class FakeView:
    """A view's tts_* calls over a list of sentences; tts_prev only when `back`."""

    def __init__(self, sentences, back=True):
        self.sentences = list(sentences)
        self.position = -1
        self.words = []
        if back:
            self.tts_prev = self._prev

    def weak_ref(self):
        return lambda: self

    def tts_start(self, callback):
        self.position = -1
        callback(True)

    def tts_next(self, callback):
        self.position += 1
        callback(self.sentences[self.position] if self.position < len(self.sentences)
                 else None)

    def _prev(self, callback):
        self.position = max(0, self.position - 1)
        callback(self.sentences[self.position])

    def tts_word(self, offset):
        self.words.append(offset)

    def tts_stop(self):
        pass


def settings():
    from gi.repository import Gio

    return Gio.Settings.new(SCHEMA_ID)


@requires_gtk
class ReadAloudBarTest(unittest.TestCase):

    def setUp(self):
        self.settings = settings()
        for key in ('reader-speech-voices', 'reader-speech-rate'):
            self.addCleanup(self.settings.reset, key)

    def test_skipping_back_and_forward(self):
        from bookcase.widgets.read_aloud import ReadAloudBar

        engine = FakeEngine()
        bar = ReadAloudBar(FakeView(['One.', 'Two.', 'Three.']), engine, self.settings, 'en')
        self.assertTrue(bar.back_button.get_sensitive())
        bar.toggle()
        bar.forward_button.emit('clicked')
        bar.forward_button.emit('clicked')
        bar.back_button.emit('clicked')
        self.assertEqual(engine.spoken, ['One.', 'Two.', 'Three.', 'Two.'])
        bar.close()

    def test_no_going_back_without_tts_prev(self):
        from bookcase.widgets.read_aloud import ReadAloudBar

        bar = ReadAloudBar(FakeView(['One.'], back=False), FakeEngine(), self.settings, 'en')
        self.assertFalse(bar.back_button.get_sensitive())
        bar.toggle()
        self.assertFalse(bar.skip(-1))
        bar.close()

    def test_the_voice_menu_lists_the_books_language_and_remembers_the_choice(self):
        from bookcase.widgets.read_aloud import ReadAloudBar, saved_voice

        engine = FakeEngine([speech.Voice('Inventa', 'fr'), speech.Voice('Ama', 'en-GB'),
                             speech.Voice('Bram', 'en-US', 'm2')])
        bar = ReadAloudBar(FakeView(['One.', 'Two.']), engine, self.settings, 'en-GB')
        bar._on_voice_menu(bar.voice_button.get_popover())  # the menu opened
        bar._on_voice_menu(bar.voice_button.get_popover())  # and again: listed once
        self.assertTrue(wait_for(lambda: bar.voice_stack.get_visible_child_name() == 'list',
                                 1))
        self.assertEqual(engine.listed, 1)
        self.assertEqual([row.voice for row in bar._voice_rows], ['', 'Ama', 'Bram'])
        self.assertFalse(bar.voice_note.get_visible())
        bar.voice_list.emit('row-activated', bar._voice_rows[2])
        self.assertEqual(saved_voice(self.settings, 'en'), 'Bram')
        self.assertEqual(self.settings.get_value('reader-speech-voices').unpack(),
                         {'en': 'Bram'})
        bar.toggle()
        self.assertEqual(engine.voices, ['Bram'])
        self.assertEqual(bar._voice_rows[2].check.get_opacity(), 1)
        bar.close()
        # another book in English starts with it; Default forgets it
        bar = ReadAloudBar(FakeView(['One.']), engine, self.settings, 'en')
        self.assertEqual(bar.player.voice, 'Bram')
        bar.choose_voice('')
        self.assertEqual(self.settings.get_value('reader-speech-voices').unpack(), {})
        bar.close()

    def test_a_language_without_voices_lists_them_all(self):
        from bookcase.widgets.read_aloud import ReadAloudBar

        engine = FakeEngine([speech.Voice('Inventa', 'fr')])
        bar = ReadAloudBar(FakeView([]), engine, self.settings, 'nl')
        bar.set_voices(engine._voices)
        self.assertTrue(bar.voice_note.get_visible())
        self.assertEqual([row.voice for row in bar._voice_rows], ['', 'Inventa'])
        bar.close()


@requires_gtk
class LookupPreferencesTest(unittest.TestCase):

    def setUp(self):
        self.settings = settings()
        for key in ('lookup-automatic', 'lookup-online', 'lookup-dictionary-order',
                    'lookup-dictionaries-off'):
            self.addCleanup(self.settings.reset, key)
        self._directory = tempfile.TemporaryDirectory()
        self.addCleanup(self._directory.cleanup)
        directory = self._directory.name
        self.first = make_stardict(directory, 'a', WORDS, types='m', bookname='Alpha')
        self.second = make_stardict(directory, 'b', WORDS, types='m', bookname='Beta')
        self.service = lookup.Lookup(fetch=lambda url: {}, dictionaries=[
            lookup.StarDict(self.first), lookup.StarDict(self.second)])

    def test_switches_and_dictionaries(self):
        from bookcase.dialogs import lookup_prefs

        when, dictionaries = lookup_prefs.preferences_groups(self.settings, self.service)
        when.automatic_row.set_active(False)
        self.assertFalse(self.settings.get_boolean('lookup-automatic'))
        when.online_row.set_active(False)
        self.assertFalse(self.settings.get_boolean('lookup-online'))
        rows = dictionaries.dictionary_rows
        self.assertEqual([row.get_title() for row in rows], ['Alpha', 'Beta'])
        self.assertEqual(rows[0].get_subtitle(), '3 words')
        rows[0].set_active(False)
        self.assertEqual(self.settings.get_strv('lookup-dictionaries-off'), [self.first])
        dictionaries.move(self.second, -1)
        self.assertEqual(self.settings.get_strv('lookup-dictionary-order'),
                         [self.second, self.first])
        pump()
        self.assertEqual([row.get_title() for row in dictionaries.dictionary_rows],
                         ['Beta', 'Alpha'])
        self.assertFalse(dictionaries.dictionary_rows[1].get_active())  # still off
        self.assertEqual([d.name for d in self.service.dictionaries()], ['Beta'])
        rows = dictionaries.dictionary_rows
        rows[1].set_active(True)
        self.assertEqual(self.settings.get_strv('lookup-dictionaries-off'), [])
        self.assertTrue(dictionaries.link_row.get_activatable())

    def test_no_dictionaries(self):
        from bookcase.dialogs import lookup_prefs

        group = lookup_prefs.DictionariesGroup(self.settings, lookup.Lookup(dictionaries=[]))
        self.assertEqual(group.dictionary_rows, [])

    def test_the_panel_offline_only(self):
        from bookcase.widgets.lookup_popover import LookupPanel

        self.settings.set_boolean('lookup-online', False)
        panel = LookupPanel(service=self.service, settings=self.settings)
        panel.show_text('absentword', 'en')
        self.assertFalse(panel.toggles.get_visible())
        self.assertFalse(panel.browser_button.get_visible())
        self.assertTrue(wait_for(lambda: panel.stack.get_visible_child_name() == 'message', 1))
        self.assertEqual(panel.message_title.get_label(), 'Not in Your Dictionaries')
        panel.show_text('Quillons', 'en')  # found offline, as its dictionary form
        self.assertTrue(wait_for(lambda: panel.answer is not None, 1))
        self.assertEqual(panel.answer.word, 'quillon')


@requires_gtk
class CustomThemeTest(unittest.TestCase):

    def test_the_dialog_sets_the_colours_and_the_theme(self):
        from gi.repository import Adw, Gdk

        from bookcase import reading
        from bookcase.widgets import reader_theme

        config = settings()
        self.addCleanup(config.reset, 'reader-theme')
        self.addCleanup(config.reset, 'reader-custom-theme')
        window = Adw.Window(default_width=500, default_height=600)
        window.dialog_open = []
        window.set_dialog_open = window.dialog_open.append
        window.present()
        dialog = reader_theme.present(window, config)
        rgba = Gdk.RGBA()
        rgba.parse('#102030')
        dialog.buttons['bg'].set_rgba(rgba)
        self.assertEqual(config.get_string('reader-theme'), 'custom')
        theme = reading.custom_theme(config.get_string('reader-custom-theme'))
        self.assertEqual(theme['bg'], '#102030')
        self.assertTrue(theme['dark'])
        self.assertIn('--reader-bg: #102030', reader_theme._provider[0].to_string())
        dialog.close()
        pump(100)
        self.assertEqual(window.dialog_open, [True, False])
        window.destroy()
        pump(50)


_apps = []


@requires_gtk
class ReaderWindowExtrasTest(unittest.TestCase):

    def setUp(self):
        from gi.repository import Adw, Gio

        self.directory = pathlib.Path(tempfile.mkdtemp(prefix='bookcase-test-'))
        self.library = Library(self.directory / 'library.sqlite')
        if not _apps:
            app = Adw.Application(application_id='io.github.jackicus.Bookcase.ExtrasTest',
                                  flags=Gio.ApplicationFlags.NON_UNIQUE)
            app.settings = Gio.Settings.new(SCHEMA_ID)
            app.register(None)
            _apps.append(app)
        self.app = _apps[0]
        self.app.library = self.library
        self.settings = self.app.settings
        info = BookInfo(title='A Quiet Harbour', authors=['Ada Lark'], format='epub',
                        language='en')
        self.book_id = self.library.add_book(info, str(self.directory / 'missing.epub'),
                                             hash='x', size=1)
        self.saved_engine = list(speech._engine)
        self.windows = []

    def tearDown(self):
        for window in self.windows:
            window._selection_popover.popdown()
            window.close()
            window.destroy()
        pump(100)
        speech._engine[:] = self.saved_engine
        for key in ('lookup-automatic', 'reader-theme', 'reader-two-pages'):
            self.settings.reset(key)
        self.library.close()
        shutil.rmtree(self.directory, ignore_errors=True)

    def open(self):
        from bookcase import reader_window

        window = reader_window.ReaderWindow(self.app, self.book_id)
        self.windows.append(window)
        return window

    def test_look_up_only_when_asked(self):
        from unittest import mock

        from bookcase.widgets import lookup_popover

        speech._engine[:] = [None]
        window = self.open()
        self.settings.set_boolean('lookup-automatic', False)
        with mock.patch.object(lookup_popover, 'inline', return_value=True):
            window._show_lookup('quillon')
            self.assertFalse(window._lookup_panel.get_visible())
            self.assertTrue(window._selection_buttons['lookup'].get_visible())
            window._show_lookup('quillon', force=True)  # Look Up chosen
            self.assertTrue(window._lookup_panel.get_visible())
        window._lookup_panel.cancel()

    def test_the_custom_paper_chip(self):
        speech._engine[:] = [None]
        window = self.open()
        self.assertIn('custom', window._theme_chips)
        window._theme_chips['custom'].set_active(True)
        self.assertEqual(self.settings.get_string('reader-theme'), 'custom')
        pump()
        self.assertTrue(window.toolbar_view.has_css_class('theme-custom'))
        self.assertEqual(window._style()['theme']['name'], 'custom')
        window._theme_chips['light'].set_active(True)
        pump()
        self.assertFalse(window.toolbar_view.has_css_class('theme-custom'))

    def test_read_aloud_keys_only_while_reading(self):
        speech._engine[:] = [FakeEngine()]
        window = self.open()
        self.assertIsNot(window._run_key('read-aloud-next', 0), True)
        bar = window._read_aloud
        bar.player.source = speech_source = FakeView(['One.', 'Two.'])
        speech_source.start = lambda callback: callback('One.')
        speech_source.next = speech_source.tts_next
        speech_source.prev = speech_source.tts_prev
        speech_source.stop = lambda: None
        bar.toggle()
        self.assertTrue(window._run_key('read-aloud-next', 0))
        self.assertEqual(bar.engine.spoken[-1], 'One.')  # the view's first, after start's
        window._on_relocated(window.view, {'fraction': 0.2, 'ttsMoved': True})
        self.assertEqual(bar.state, 'playing')
        bar.stop()

    def test_a_fixed_layout_zooms(self):
        speech._engine[:] = [None]
        window = self.open()
        zooms = []
        window.view.zoom = lambda action, callback=None: (
            zooms.append(action),
            callback({'fit': None, 'percent': 125} if action == 'in'
                     else {'fit': 'page', 'percent': 100}))
        window._on_loaded(window.view, {'dir': 'ltr', 'fixedLayout': True,
                                        'zoom': {'fit': 'page', 'percent': 100}})
        self.assertTrue(window.smaller_button.get_parent().get_visible())
        self.assertTrue(window.two_pages_row.get_visible())
        self.assertFalse(window.justify_row.get_visible())
        self.assertEqual(window.size_label.get_label(), '100%')
        self.assertFalse(window.smaller_button.get_sensitive())
        self.assertEqual(window._fit_group.get_active_name(), 'page')
        window._run_key('bigger', 0)
        self.assertEqual(window.size_label.get_label(), '125%')
        self.assertTrue(window.smaller_button.get_sensitive())
        self.assertEqual(window._fit_group.get_active(), 4294967295)  # none
        window._run_key('reset-size', 0)
        self.assertEqual(zooms, ['in', 'fit-page'])
        self.assertEqual(window._fit_group.get_active_name(), 'page')
        window._fit_group.set_active_name('width')
        self.assertEqual(zooms[-1], 'fit-width')

    def test_go_to_a_printed_page(self):
        speech._engine[:] = [None]
        window = self.open()
        gone = []
        window.view.go_to = gone.append
        window.view.go_to_fraction = gone.append
        window._loaded = {'pageItems': [{'label': 'iv', 'href': 'front.xhtml#p4'},
                                        {'label': '12', 'href': 'ch1.xhtml#p12'}]}
        items = window._page_items()
        self.assertTrue(window._go_to_entered(' IV ', items))
        self.assertTrue(window._go_to_entered('12', items))
        self.assertFalse(window._go_to_entered('13', items))
        self.assertTrue(window._go_to_entered('50%', items))
        self.assertEqual(gone, ['front.xhtml#p4', 'ch1.xhtml#p12', 0.5])


def make_cbz(path, pages=4):
    import zipfile

    from tests.support import make_png

    with zipfile.ZipFile(path, 'w') as archive:
        for number in range(1, pages + 1):
            archive.writestr(f'{number:03}.png', make_png(300, 450, (40 * number, 90, 160)))
    return path


@requires_gtk
class ComicZoomTest(unittest.TestCase):
    """A comic in the real page (WebKit; skipped without it): zoomed in, out, fitted; one
    page or two."""

    @classmethod
    def setUpClass(cls):
        from gi.repository import Gtk

        from bookcase import reading
        from bookcase.widgets import book_view

        if not book_view.available():
            raise unittest.SkipTest('WebKitGTK 6.0 is not available (or BOOKCASE_NO_WEBKIT)')
        cls.directory = pathlib.Path(tempfile.mkdtemp(prefix='bookcase-test-'))
        path = make_cbz(cls.directory / 'Harbour Lights.cbz')
        cls.window = Gtk.Window(default_width=900, default_height=600)
        cls.view = book_view.BookView()
        cls.window.set_child(cls.view)
        cls.window.present()
        cls.places = []
        cls.loaded = []
        cls.view.connect('relocated', lambda _view, place: cls.places.append(place))
        cls.view.connect('loaded', lambda _view, loaded: cls.loaded.append(loaded))
        cls.values = {'reader-theme': 'light', 'reader-font': 'publisher',
                      'reader-custom-font': '', 'reader-font-size': 18,
                      'reader-line-height': 1.5, 'reader-margin': 8, 'reader-max-width': 720,
                      'reader-justify': True, 'reader-hyphenate': True,
                      'reader-publisher-styles': True, 'reader-scrolled': False,
                      'reader-two-pages': True, 'reader-animate': False}
        cls.reading = reading
        cls.view.open(str(path), 'cbz', style=reading.build_style(cls.values.get, False))
        if not wait_for(lambda: cls.view._ready, 15):
            cls.tearDownClass()
            raise unittest.SkipTest('the WebKit web process did not start')
        if not wait_for(lambda: any(p.get('fraction') is not None for p in cls.places), 15):
            raise AssertionError('the comic never showed')

    @classmethod
    def tearDownClass(cls):
        cls.view.close()
        cls.window.destroy()
        shutil.rmtree(cls.directory, ignore_errors=True)

    def call(self, *args):
        got = []
        self.view.zoom(*args, got.append)
        self.assertTrue(wait_for(lambda: got, 15))
        return got[0]

    def frames(self):
        got = []
        self.view.evaluate('return globalThis.reader._contents() && document.querySelector('
                           '"foliate-view").renderer.getContents().length', got.append)
        self.assertTrue(wait_for(lambda: got, 15))
        return got[0]

    def test_zoom_and_spreads(self):
        self.assertEqual(self.loaded[0]['zoom'], {'fit': 'page', 'percent': 100})
        self.assertEqual(self.call('fit-page'), {'fit': 'page', 'percent': 100})
        zoomed = self.call('in')
        self.assertEqual(zoomed, {'fit': None, 'percent': 125})
        self.assertEqual(self.call('out'), {'fit': 'page', 'percent': 100})
        self.assertEqual(self.call('out')['fit'], 'page')  # not below fitting the page
        self.assertEqual(self.call('fit-width')['fit'], 'width')
        self.call('fit-page')
        self.view.next()
        wait_for(lambda: False, 0.5)
        self.assertEqual(self.frames(), 2)  # wider than tall: two pages
        values = dict(self.values, **{'reader-two-pages': False})
        self.view.set_style(self.reading.build_style(values.get, False))
        self.assertTrue(wait_for(lambda: self.frames() == 1, 5))


if __name__ == '__main__':
    unittest.main()
