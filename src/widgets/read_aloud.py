# SPDX-License-Identifier: GPL-2.0-or-later
# SPDX-FileCopyrightText: 2026 Jack Tully

"""Read Aloud in the reader: a bar under the page with play and pause, stop, a sentence
back and forward, the voice and the speed.

    bar = ReadAloudBar(view, engine, settings, language='en')   # a Gtk.Revealer
    toolbar_view.add_bottom_bar(bar)
    bar.toggle()        # starts reading from the page shown, else pauses or plays again
    bar.skip(1)         # the next sentence (-1: the one before); False when not reading
    bar.moved()         # the reader went elsewhere: reading goes on from there
    bar.stop()          # stops and hides the bar
    bar.close()         # on closing the window

    ViewSource(view)    # speech.ReadAloud's source over a view's tts_* calls

The engine is speech.engine() (the reader window offers Read Aloud only when there is
one). The speed is the reader-speech-rate setting, applied from the next sentence on; the
voice is the engine's, chosen per language (reader-speech-voices: {language: voice name},
'' or missing for the engine's default), listed from the engine when the Voice menu first
opens. The page highlights the sentence spoken and turns to it, and underlines the word
being said when the engine reports words (speechd's marks); the bar hides when reading
stops, at the end of the book too.

A view reads aloud with tts_start(callback(ok)), tts_next(callback(text or None)) and
tts_stop(); tts_prev(callback) (skipping back) and tts_word(offset) (the word being said)
are optional: without them the back button is insensitive and no word is underlined.
"""

from gettext import gettext as _

from gi.repository import Gio, GLib, Gtk, Pango

from .. import speech
from .util import connect_weak

VOICES_KEY = 'reader-speech-voices'


class ViewSource:
    """The sentences of the book shown in `view` (widgets/book_view.py), from its page."""

    def __init__(self, view):
        self._ref = view.weak_ref()
        if not hasattr(view, 'tts_prev'):
            self.prev = None
        if not hasattr(view, 'tts_word'):
            self.word = None

    def start(self, callback):
        view = self._ref()
        if view is None:
            callback(None)
            return

        def started(ok):
            again = self._ref()
            if ok and again is not None:
                again.tts_next(callback)
            else:
                callback(None)

        view.tts_start(started)

    def next(self, callback):
        view = self._ref()
        if view is None:
            callback(None)
        else:
            view.tts_next(callback)

    def prev(self, callback):
        view = self._ref()
        if view is None:
            callback(None)
        else:
            view.tts_prev(callback)

    def word(self, offset):
        view = self._ref()
        if view is not None:
            view.tts_word(offset)

    def stop(self):
        view = self._ref()
        if view is not None:
            view.tts_stop()


def available(view):
    """Whether Read Aloud can be offered for `view`: an engine, and a view that reads."""
    return speech.engine() is not None and hasattr(view, 'tts_start')


def language_key(language):
    """The key a voice is saved under: the language's base ('en' for 'en-GB')."""
    return (language or '').replace('_', '-').split('-')[0].lower()


def saved_voice(settings, language):
    voices = settings.get_value(VOICES_KEY).unpack()
    return voices.get(language_key(language), '')


def save_voice(settings, language, voice):
    voices = dict(settings.get_value(VOICES_KEY).unpack())
    key = language_key(language)
    if voice:
        voices[key] = voice
    else:
        voices.pop(key, None)
    settings.set_value(VOICES_KEY, GLib.Variant('a{ss}', voices))


class ReadAloudBar(Gtk.Revealer):
    __gtype_name__ = 'BookcaseReadAloudBar'

    def __init__(self, view, engine, settings, language=''):
        super().__init__(transition_type=Gtk.RevealerTransitionType.SLIDE_UP,
                         reveal_child=False)
        self.add_css_class('read-aloud-bar')
        self.settings = settings
        self.engine = engine
        self.language = language
        self.source = ViewSource(view)
        self.player = speech.ReadAloud(engine, self.source,
                                       rate=settings.get_int('reader-speech-rate'),
                                       language=language,
                                       voice=saved_voice(settings, language))
        self.voices = None  # [speech.Voice] once listed
        ref = self.weak_ref()

        def on_state(state):
            bar = ref()
            if bar is not None:
                bar._on_state(state)

        self.player.on_state = on_state

        box = Gtk.Box(spacing=4, margin_start=6, margin_end=6, margin_top=6, margin_bottom=6)
        self.play_button = Gtk.Button(icon_name='media-playback-pause-symbolic',
                                      tooltip_text=_('Pause'))
        self.play_button.add_css_class('circular')
        self.play_button.connect('clicked', lambda _button: (ref() and ref().toggle()))
        box.append(self.play_button)
        self.back_button = Gtk.Button(icon_name='media-skip-backward-symbolic',
                                      tooltip_text=_('Previous Sentence'),
                                      sensitive=self.source.prev is not None)
        self.back_button.add_css_class('flat')
        self.back_button.connect('clicked', lambda _button: (ref() and ref().skip(-1)))
        box.append(self.back_button)
        self.forward_button = Gtk.Button(icon_name='media-skip-forward-symbolic',
                                         tooltip_text=_('Next Sentence'))
        self.forward_button.add_css_class('flat')
        self.forward_button.connect('clicked', lambda _button: (ref() and ref().skip(1)))
        box.append(self.forward_button)
        self.stop_button = Gtk.Button(icon_name='media-playback-stop-symbolic',
                                      tooltip_text=_('Stop Reading Aloud'))
        self.stop_button.add_css_class('flat')
        self.stop_button.connect('clicked', lambda _button: (ref() and ref().stop()))
        box.append(self.stop_button)
        self.status = Gtk.Label(label=_('Reading Aloud'), hexpand=True, xalign=0,
                                ellipsize=Pango.EllipsizeMode.END, margin_start=4)
        self.status.add_css_class('dimmed')
        box.append(self.status)

        box.append(self._build_voice_button())

        speed = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=6, margin_start=6,
                        margin_end=6, margin_top=6, margin_bottom=6)
        title = Gtk.Label(label=_('Speed'), xalign=0)
        title.add_css_class('heading')
        speed.append(title)
        self.rate_scale = Gtk.Scale.new_with_range(Gtk.Orientation.HORIZONTAL, -60, 100, 10)
        self.rate_scale.set_size_request(220, -1)
        self.rate_scale.set_draw_value(False)
        self.rate_scale.add_mark(-60, Gtk.PositionType.BOTTOM, _('Slower'))
        self.rate_scale.add_mark(0, Gtk.PositionType.BOTTOM, _('Normal'))
        self.rate_scale.add_mark(100, Gtk.PositionType.BOTTOM, _('Faster'))
        self.rate_scale.update_property([Gtk.AccessibleProperty.LABEL], [_('Speed')])
        speed.append(self.rate_scale)
        settings.bind('reader-speech-rate', self.rate_scale.get_adjustment(), 'value',
                      Gio.SettingsBindFlags.DEFAULT)
        speed_button = Gtk.MenuButton(label=_('Speed'), always_show_arrow=True,
                                      direction=Gtk.ArrowType.UP,
                                      popover=Gtk.Popover(child=speed))
        speed_button.add_css_class('flat')
        box.append(speed_button)
        self.set_child(box)
        self._settings_handler = connect_weak(settings, 'changed::reader-speech-rate',
                                              self._on_rate_changed)

    # -- the voice menu ----------------------------------------------------------------------

    def _build_voice_button(self):
        content = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=6, margin_start=6,
                          margin_end=6, margin_top=6, margin_bottom=6, width_request=240)
        title = Gtk.Label(label=_('Voice'), xalign=0)
        title.add_css_class('heading')
        content.append(title)
        self.voice_stack = Gtk.Stack(vhomogeneous=False)
        self.voice_stack.add_named(Gtk.Label(label=_('Finding voices…'), xalign=0,
                                             margin_top=6, margin_bottom=6), 'loading')
        self.voice_list = Gtk.ListBox(selection_mode=Gtk.SelectionMode.NONE)
        self.voice_list.add_css_class('boxed-list')
        connect_weak(self.voice_list, 'row-activated', self._on_voice_row)
        self.voice_stack.add_named(Gtk.ScrolledWindow(
            hscrollbar_policy=Gtk.PolicyType.NEVER, propagate_natural_height=True,
            max_content_height=320, child=self.voice_list), 'list')
        content.append(self.voice_stack)
        self.voice_note = Gtk.Label(xalign=0, wrap=True, max_width_chars=30, visible=False)
        self.voice_note.add_css_class('caption')
        self.voice_note.add_css_class('dimmed')
        content.append(self.voice_note)
        popover = Gtk.Popover(child=content)
        connect_weak(popover, 'show', self._on_voice_menu)
        self.voice_button = Gtk.MenuButton(icon_name='audio-speakers-symbolic',
                                           tooltip_text=_('Voice'), popover=popover,
                                           direction=Gtk.ArrowType.UP)
        self.voice_button.add_css_class('flat')
        self.voice_button.set_visible(hasattr(self.engine, 'list_voices'))
        return self.voice_button

    def _on_voice_menu(self, _popover):
        if self.voices is not None:
            return
        self.voices = []
        ref = self.weak_ref()

        def listed(voices):
            bar = ref()
            if bar is not None:
                bar.set_voices(voices)

        self.engine.list_voices(listed)

    def set_voices(self, voices):
        """Fill the Voice menu: the default, then the book's language's voices (every voice
        when it has none)."""
        self.voices = list(voices)
        while (row := self.voice_list.get_first_child()) is not None:
            self.voice_list.remove(row)
        mine = speech.voices_for(self.voices, self.language)
        shown = mine or sorted(self.voices, key=lambda v: v.name.casefold())
        self.voice_note.set_visible(not mine)
        self.voice_note.set_label(_('No voice is installed for this book’s language') if
                                  self.voices else _('The speech engine lists no voices'))
        self._voice_rows = []
        for voice in [None] + shown:
            name = voice.name if voice else ''
            texts = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, hexpand=True,
                            valign=Gtk.Align.CENTER)
            texts.append(Gtk.Label(label=name or _('Default'), xalign=0, wrap=True,
                                   max_width_chars=22))
            box = Gtk.Box(spacing=6, margin_start=10, margin_end=10, margin_top=8,
                          margin_bottom=8)
            box.append(texts)
            if voice is not None and (voice.variant or not mine):
                detail = Gtk.Label(label=' '.join(x for x in (voice.language, voice.variant)
                                                  if x), xalign=0)
                detail.add_css_class('dimmed')
                detail.add_css_class('caption')
                texts.append(detail)
            check = Gtk.Image(icon_name='object-select-symbolic',
                              accessible_role=Gtk.AccessibleRole.PRESENTATION)
            box.append(check)
            row = Gtk.ListBoxRow(child=box)
            row.voice = name
            row.check = check
            self.voice_list.append(row)
            self._voice_rows.append(row)
        self._update_voice_checks()
        self.voice_stack.set_visible_child_name('list')

    def _update_voice_checks(self):
        current = self.player.voice or ''
        for row in getattr(self, '_voice_rows', ()):
            row.check.set_opacity(1 if row.voice == current else 0)

    def _on_voice_row(self, _list, row):
        self.choose_voice(row.voice)

    def choose_voice(self, name):
        """The voice for this book's language from the next sentence on ('' the default),
        kept for every book in it."""
        self.player.voice = name
        save_voice(self.settings, self.language, name)
        self._update_voice_checks()

    # -- playing -----------------------------------------------------------------------------

    @property
    def state(self):
        return self.player.state

    def toggle(self):
        """Start reading (the bar shown), or pause, or play again."""
        self.set_reveal_child(True)
        self.player.toggle()

    def skip(self, direction):
        """A sentence forward (1) or back (-1) while reading; False when not reading."""
        return self.player.skip(direction)

    def moved(self):
        self.player.moved()

    def stop(self):
        self.player.stop()
        self.set_reveal_child(False)

    def close(self):
        self.player.on_state = None
        self.player.stop()
        if self.settings.handler_is_connected(self._settings_handler):
            self.settings.disconnect(self._settings_handler)

    def _on_rate_changed(self, settings, key):
        self.player.rate = settings.get_int(key)

    def _on_state(self, state):
        playing = state == 'playing'
        self.play_button.set_icon_name('media-playback-pause-symbolic' if playing
                                       else 'media-playback-start-symbolic')
        self.play_button.set_tooltip_text(_('Pause') if playing else _('Read Aloud'))
        self.status.set_label(_('Reading Aloud') if playing else _('Paused'))
        if state == 'stopped':
            self.set_reveal_child(False)
