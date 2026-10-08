# SPDX-License-Identifier: GPL-2.0-or-later
# SPDX-FileCopyrightText: 2026 Jack Tully

"""Read Aloud in the reader: a bar under the page with play and pause, stop and the speed.

    bar = ReadAloudBar(view, engine, settings, language='en')   # a Gtk.Revealer
    toolbar_view.add_bottom_bar(bar)
    bar.toggle()        # starts reading from the page shown, else pauses or plays again
    bar.stop()          # stops and hides the bar
    bar.close()         # on closing the window

    ViewSource(view)    # speech.ReadAloud's source over a BookView's tts_* calls

The engine is speech.engine() (the reader window offers Read Aloud only when there is
one). The speed is the reader-speech-rate setting, applied from the next sentence on. The
page highlights the sentence spoken and turns to it; the bar hides when reading stops, at
the end of the book too.
"""

from gettext import gettext as _

from gi.repository import Gio, Gtk, Pango

from .. import speech
from .util import connect_weak


class ViewSource:
    """The sentences of the book shown in `view` (widgets/book_view.py), from its page."""

    def __init__(self, view):
        self._ref = view.weak_ref()

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

    def stop(self):
        view = self._ref()
        if view is not None:
            view.tts_stop()


def available(view):
    """Whether Read Aloud can be offered for `view`: an engine, and a view that reads."""
    return speech.engine() is not None and hasattr(view, 'tts_start')


class ReadAloudBar(Gtk.Revealer):
    __gtype_name__ = 'BookcaseReadAloudBar'

    def __init__(self, view, engine, settings, language=''):
        super().__init__(transition_type=Gtk.RevealerTransitionType.SLIDE_UP,
                         reveal_child=False)
        self.add_css_class('read-aloud-bar')
        self.settings = settings
        self.player = speech.ReadAloud(engine, ViewSource(view),
                                       rate=settings.get_int('reader-speech-rate'),
                                       language=language)
        ref = self.weak_ref()

        def on_state(state):
            bar = ref()
            if bar is not None:
                bar._on_state(state)

        self.player.on_state = on_state

        box = Gtk.Box(spacing=6, margin_start=6, margin_end=6, margin_top=6, margin_bottom=6)
        self.play_button = Gtk.Button(icon_name='media-playback-pause-symbolic',
                                      tooltip_text=_('Pause'))
        self.play_button.add_css_class('circular')
        self.play_button.connect('clicked', lambda _button: (ref() and ref().toggle()))
        box.append(self.play_button)
        self.stop_button = Gtk.Button(icon_name='media-playback-stop-symbolic',
                                      tooltip_text=_('Stop Reading Aloud'))
        self.stop_button.add_css_class('flat')
        self.stop_button.connect('clicked', lambda _button: (ref() and ref().stop()))
        box.append(self.stop_button)
        self.status = Gtk.Label(label=_('Reading Aloud'), hexpand=True, xalign=0,
                                ellipsize=Pango.EllipsizeMode.END)
        self.status.add_css_class('dimmed')
        box.append(self.status)

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
                                      popover=Gtk.Popover(child=speed))
        speed_button.add_css_class('flat')
        box.append(speed_button)
        self.set_child(box)
        self._settings_handler = connect_weak(settings, 'changed::reader-speech-rate',
                                              self._on_rate_changed)

    @property
    def state(self):
        return self.player.state

    def toggle(self):
        """Start reading (the bar shown), or pause, or play again."""
        self.set_reveal_child(True)
        self.player.toggle()

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
