# SPDX-License-Identifier: GPL-2.0-or-later
# SPDX-FileCopyrightText: 2026 Jack Tully

"""Reading aloud: a speech engine found at run time, and the sentence-by-sentence player.

    engine = speech.find_engine()       # None when no engine is installed (the feature hides)
    engine.speak(text, done, rate=0, language='')   # done() on the main loop when spoken
    engine.stop()                       # done is not called for what was being spoken
    engine.close()

    player = speech.ReadAloud(engine, source, rate=0, language='')
    player.play() / pause() / toggle() / stop()
    player.state                        # 'stopped', 'playing' or 'paused'
    player.on_state = callback          # callback(state) when it changes
    player.rate = 20                    # from the next sentence on

The source gives the sentences: source.start(callback), source.next(callback), each calling
callback(text) with the next sentence's text (None at the end of the book), and
source.stop(). The reader's source (widgets/read_aloud.py) is the page: it highlights the
sentence it gives and turns to it. The player speaks a sentence, asks for the next when it is
spoken, and so on; pausing stops the engine and keeps the sentence, which playing again
speaks from its start; a callback from before a stop or a pause is ignored.

Engines: speech-dispatcher, through its Python module (speechd) when it can be imported,
else its `spd-say` command (one process per sentence, the text on its standard input). The
rate is speech-dispatcher's, from -100 (slowest) to 100. BOOKCASE_NO_SPEECH=1 finds none.
"""

import logging
import os

from gi.repository import Gio, GLib

log = logging.getLogger(__name__)

RATES = (-100, 100)


def _rate(rate):
    return max(RATES[0], min(RATES[1], int(rate or 0)))


class SpeechdEngine:
    """speech-dispatcher through its Python client."""

    name = 'speechd'

    def __init__(self, module):
        self._speechd = module
        self._client = None
        self._token = 0

    def _connect(self):
        if self._client is None:
            self._client = self._speechd.SSIPClient('bookcase')
        return self._client

    def speak(self, text, done, rate=0, language=''):
        client = self._connect()
        self._token += 1
        token = self._token
        client.set_rate(_rate(rate))
        if language:
            client.set_language(language)
        kinds = self._speechd.CallbackType

        def finished():
            if token == self._token:
                done()
            return GLib.SOURCE_REMOVE

        def event(kind, *_args, **_kwargs):  # in the client's thread
            if kind == kinds.END:
                GLib.idle_add(finished)

        client.speak(text, callback=event, event_types=(kinds.END, kinds.CANCEL))

    def stop(self):
        self._token += 1
        if self._client is not None:
            self._client.stop()

    def close(self):
        self.stop()
        if self._client is not None:
            self._client.close()
            self._client = None


class SpdSayEngine:
    """speech-dispatcher through `spd-say --wait --pipe-mode`, the text on standard input."""

    name = 'spd-say'

    def __init__(self, program):
        self._program = program
        self._process = None

    def speak(self, text, done, rate=0, language=''):
        self.stop()
        argv = [self._program, '--wait', '--pipe-mode', '--application-name', 'Bookcase',
                '--rate', str(_rate(rate))]
        if language:
            argv += ['--language', language]
        try:
            process = Gio.Subprocess.new(argv, Gio.SubprocessFlags.STDIN_PIPE
                                         | Gio.SubprocessFlags.STDOUT_SILENCE
                                         | Gio.SubprocessFlags.STDERR_SILENCE)
        except GLib.Error as error:
            log.warning('starting spd-say: %s', error.message)
            GLib.idle_add(lambda: (done(), GLib.SOURCE_REMOVE)[1])
            return
        self._process = process
        line = ' '.join(text.split()) + '\n'

        def written(proc, result):
            try:
                proc.communicate_utf8_finish(result)
            except GLib.Error as error:
                if not error.matches(Gio.io_error_quark(), Gio.IOErrorEnum.CANCELLED):
                    log.warning('spd-say: %s', error.message)
            if self._process is proc:
                self._process = None
                done()

        process.communicate_utf8_async(line, None, written)

    def stop(self):
        process, self._process = self._process, None
        if process is not None:
            process.force_exit()
            try:  # the message being spoken stops with its client only when told to
                Gio.Subprocess.new([self._program, '--stop'], Gio.SubprocessFlags.NONE)
            except GLib.Error:
                pass

    def close(self):
        self.stop()


def find_engine():
    """The speech engine installed, or None."""
    if os.environ.get('BOOKCASE_NO_SPEECH'):
        return None
    try:
        import speechd  # noqa: PLC0415  (optional: python-speechd)
    except ImportError:
        speechd = None
    if speechd is not None and hasattr(speechd, 'SSIPClient'):
        return SpeechdEngine(speechd)
    program = GLib.find_program_in_path('spd-say')
    if program:
        return SpdSayEngine(program)
    return None


_engine = []


def engine():
    """The engine found once per process (None when there is none)."""
    if not _engine:
        _engine.append(find_engine())
    return _engine[0]


class ReadAloud:
    """Speaks what `source` gives, a sentence at a time, with `engine`."""

    def __init__(self, engine, source, rate=0, language=''):
        self.engine = engine
        self.source = source
        self.rate = rate
        self.language = language
        self.state = 'stopped'
        self.on_state = None
        self._text = None  # the sentence being spoken, or paused on
        self._generation = 0  # bumped by stop and pause: older callbacks are ignored
        self._started = False  # the source has been started since the last stop
        self._paused_from = None  # the generation a pause ended

    def _set_state(self, state):
        if state != self.state:
            self.state = state
            if self.on_state is not None:
                self.on_state(state)

    def play(self):
        if self.state == 'playing':
            return
        self._set_state('playing')
        self._generation += 1
        if self._text is not None:
            self._speak(self._text, self._generation)
        elif not self._started:
            self._started = True
            self.source.start(self._sentence_callback(self._generation))
        else:
            self.source.next(self._sentence_callback(self._generation))

    def pause(self):
        if self.state != 'playing':
            return
        self._paused_from = self._generation
        self._generation += 1
        self.engine.stop()
        self._set_state('paused')

    def toggle(self):
        if self.state == 'playing':
            self.pause()
        else:
            self.play()

    def stop(self):
        if self.state == 'stopped':
            return
        self._generation += 1
        self.engine.stop()
        self._text = None
        self._started = False
        self.source.stop()
        self._set_state('stopped')

    def _sentence_callback(self, generation):
        def got(text):
            if self.state == 'paused' and generation == self._paused_from and text:
                self._text = text  # asked for before the pause: spoken on playing again
                return
            if generation != self._generation or self.state != 'playing':
                return
            if text is None:
                self.stop()  # the end of the book
                return
            if not text.strip():
                self.source.next(self._sentence_callback(generation))
                return
            self._speak(text, generation)
        return got

    def _speak(self, text, generation):
        self._text = text

        def spoken():
            if generation != self._generation or self.state != 'playing':
                return
            self._text = None
            self.source.next(self._sentence_callback(generation))

        self.engine.speak(text, spoken, rate=self.rate, language=self.language)
