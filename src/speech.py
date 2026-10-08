# SPDX-License-Identifier: GPL-2.0-or-later
# SPDX-FileCopyrightText: 2026 Jack Tully

"""Reading aloud: a speech engine found at run time, its voices, and the sentence-by-sentence
player.

    engine = speech.find_engine()       # None when no engine is installed (the feature hides)
    engine.speak(text, done, rate=0, language='', voice='', on_word=None)
                                        # done() on the main loop when spoken; on_word(offset)
                                        # as each word starts, where the engine says (marks)
    engine.stop()                       # done is not called for what was being spoken
    engine.pause() / resume()           # where engine.can_pause: on from the word it was at
    engine.list_voices(callback)        # callback([Voice]) on the main loop
    engine.close()

    player = speech.ReadAloud(engine, source, rate=0, language='', voice='')
    player.play() / pause() / toggle() / stop()
    player.skip(1) / skip(-1)           # the next sentence, or the one before
    player.moved()                      # the reader went elsewhere: go on from there
    player.state                        # 'stopped', 'playing' or 'paused'
    player.on_state = callback          # callback(state) when it changes
    player.rate = 20                    # from the next sentence on (and player.voice)

    speech.parse_voices(text)           # `spd-say -L`'s table -> [Voice]
    speech.voices_for(voices, language) # the voices of a language ('en' finds 'en-GB')
    speech.word_ssml(text)              # the sentence as SSML, a mark before each word

The source gives the sentences: source.start(callback), source.next(callback), each calling
callback(text) with the next sentence's text (None at the end of the book), and
source.stop(); source.prev(callback) (the sentence before the one given last) and
source.word(offset) (show the word at that offset of the sentence being read) are optional.
The reader's source (widgets/read_aloud.py) is the page: it highlights the sentence it gives
and turns to it. The player speaks a sentence, asks for the next when it is spoken, and so
on; pausing pauses the engine where it can (speechd: playing again goes on from the word it
was at), else stops it and keeps the sentence, which playing again speaks from its start; a
callback from before a stop, a skip or a pause is ignored.

Engines: speech-dispatcher, through its Python module (speechd) when it can be imported,
else its `spd-say` command (one process per sentence, the text on its standard input). The
rate is speech-dispatcher's, from -100 (slowest) to 100; a voice is one of its synthesis
voices, by name. Through speechd a sentence goes as SSML with a mark before each word (its
offset in the sentence), and the marks the synthesizer reaches come back as on_word: the
word being said (synthesizers that ignore marks send none). BOOKCASE_NO_SPEECH=1 finds none.
"""

import dataclasses
import logging
import os
import re
import threading
from xml.sax.saxutils import escape

from gi.repository import Gio, GLib

log = logging.getLogger(__name__)

RATES = (-100, 100)
WORD = re.compile(r'\S+')


def _rate(rate):
    return max(RATES[0], min(RATES[1], int(rate or 0)))


@dataclasses.dataclass(frozen=True)
class Voice:
    name: str
    language: str = ''
    variant: str = ''


def parse_voices(text):
    """[Voice] from `spd-say -L`'s table (NAME LANGUAGE VARIANT, a header line first; a
    name may hold spaces, the language and variant do not)."""
    voices = []
    for line in (text or '').splitlines():
        parts = line.strip().rsplit(None, 2)
        if len(parts) != 3 or parts == ['NAME', 'LANGUAGE', 'VARIANT']:
            continue
        name, language, variant = parts
        voices.append(Voice(name, language, '' if variant == 'none' else variant))
    return voices


def _base(language):
    return (language or '').replace('_', '-').split('-')[0].lower()


def voices_for(voices, language):
    """The voices whose language is `language` ('en' matches 'en-GB' and 'en_US'), by name."""
    base = _base(language)
    return sorted((v for v in voices if base and _base(v.language) == base),
                  key=lambda v: v.name.casefold())


def word_ssml(text):
    """The sentence as SSML, `<mark name="N"/>` before each word (N its offset in `text`)."""
    parts = ['<speak>']
    position = 0
    for match in WORD.finditer(text):
        parts.append(escape(text[position:match.start()]))
        parts.append(f'<mark name="{match.start()}"/>')
        parts.append(escape(match.group()))
        position = match.end()
    parts.append(escape(text[position:]))
    parts.append('</speak>')
    return ''.join(parts)


def _later(callback, *args):
    def run():
        callback(*args)
        return GLib.SOURCE_REMOVE
    GLib.idle_add(run)


class SpeechdEngine:
    """speech-dispatcher through its Python client."""

    name = 'speechd'
    can_pause = True
    marks = True  # on_word, from SSML marks

    def __init__(self, module):
        self._speechd = module
        self._client = None
        self._token = 0
        self._voice = None  # the voice set on the client, None for its default

    def _connect(self):
        if self._client is None:
            self._client = self._speechd.SSIPClient('bookcase')
            mode = getattr(getattr(self._speechd, 'DataMode', None), 'SSML', None)
            if mode is not None:
                try:
                    self._client.set_data_mode(mode)
                except Exception:  # noqa: BLE001  (an old server: plain text, no marks)
                    log.info('speech-dispatcher takes no SSML: no word marks')
                    self.marks = False
            else:
                self.marks = False
        return self._client

    def speak(self, text, done, rate=0, language='', voice='', on_word=None):
        client = self._connect()
        self._token += 1
        token = self._token
        client.set_rate(_rate(rate))
        if language:
            client.set_language(language)
        if voice and voice != self._voice:
            client.set_synthesis_voice(voice)
            self._voice = voice
        kinds = self._speechd.CallbackType

        def finished():
            if token == self._token:
                done()
            return GLib.SOURCE_REMOVE

        def marked(name):
            if token == self._token and on_word is not None:
                try:
                    on_word(int(name))
                except (TypeError, ValueError):
                    pass
            return GLib.SOURCE_REMOVE

        def event(kind, *_args, **kwargs):  # in the client's thread
            if kind == kinds.END:
                GLib.idle_add(finished)
            elif kind == getattr(kinds, 'INDEX_MARK', None):
                GLib.idle_add(marked, kwargs.get('index_mark'))

        types = [kinds.END, kinds.CANCEL]
        if hasattr(kinds, 'INDEX_MARK'):
            types.append(kinds.INDEX_MARK)
        client.speak(word_ssml(text) if self.marks else text, callback=event,
                     event_types=tuple(types))

    def pause(self):
        if self._client is not None:
            self._client.pause()

    def resume(self):
        if self._client is not None:
            self._client.resume()

    def stop(self):
        self._token += 1
        if self._client is not None:
            self._client.stop()

    def list_voices(self, callback):
        """callback([Voice]) on the main loop, asked in a thread (a client of its own)."""
        module = self._speechd

        def work():
            voices = []
            try:
                client = module.SSIPClient('bookcase-voices')
                try:
                    for item in client.list_synthesis_voices() or ():
                        name, language, variant = (tuple(item) + ('', '', ''))[:3]
                        if name:
                            voices.append(Voice(str(name), str(language or ''),
                                                '' if variant in (None, 'none')
                                                else str(variant)))
                finally:
                    client.close()
            except Exception as error:  # noqa: BLE001  (no server: no voices to offer)
                log.warning('listing the voices: %s', error)
            _later(callback, voices)

        threading.Thread(target=work, name='bookcase-voices', daemon=True).start()

    def close(self):
        self.stop()
        if self._client is not None:
            self._client.close()
            self._client = None


class SpdSayEngine:
    """speech-dispatcher through `spd-say --wait --pipe-mode`, the text on standard input."""

    name = 'spd-say'
    can_pause = False
    marks = False

    def __init__(self, program):
        self._program = program
        self._process = None

    def speak(self, text, done, rate=0, language='', voice='', on_word=None):
        self.stop()
        argv = [self._program, '--wait', '--pipe-mode', '--application-name', 'Bookcase',
                '--rate', str(_rate(rate))]
        if language:
            argv += ['--language', language]
        if voice:
            argv += ['--synthesis-voice', voice]
        try:
            process = Gio.Subprocess.new(argv, Gio.SubprocessFlags.STDIN_PIPE
                                         | Gio.SubprocessFlags.STDOUT_SILENCE
                                         | Gio.SubprocessFlags.STDERR_SILENCE)
        except GLib.Error as error:
            log.warning('starting spd-say: %s', error.message)
            _later(done)
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

    def list_voices(self, callback):
        """callback([Voice]) from `spd-say -L`, on the main loop."""
        try:
            process = Gio.Subprocess.new([self._program, '--list-synthesis-voices'],
                                         Gio.SubprocessFlags.STDOUT_PIPE
                                         | Gio.SubprocessFlags.STDERR_SILENCE)
        except GLib.Error as error:
            log.warning('listing the voices: %s', error.message)
            _later(callback, [])
            return

        def listed(proc, result):
            try:
                _ok, out, _err = proc.communicate_utf8_finish(result)
            except GLib.Error as error:
                log.warning('listing the voices: %s', error.message)
                out = ''
            callback(parse_voices(out or ''))

        process.communicate_utf8_async(None, None, listed)

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

    def __init__(self, engine, source, rate=0, language='', voice=''):
        self.engine = engine
        self.source = source
        self.rate = rate
        self.language = language
        self.voice = voice
        self.state = 'stopped'
        self.on_state = None
        self._text = None  # the sentence being spoken, or paused on
        self._generation = 0  # bumped by stop, skip and pause: older callbacks are ignored
        self._started = False  # the source has been started since the last stop
        self._paused_from = None  # the generation a pause ended
        self._engine_paused = False  # paused in the engine, which resumes mid-sentence
        self._spoken_while_paused = False  # the engine finished the sentence as it paused

    def _set_state(self, state):
        if state != self.state:
            self.state = state
            if self.on_state is not None:
                self.on_state(state)

    def play(self):
        if self.state == 'playing':
            return
        self._set_state('playing')
        if self._engine_paused:
            self._engine_paused = False
            if self._spoken_while_paused:
                self._spoken_while_paused = False
                self._text = None
                self.source.next(self._sentence_callback(self._generation))
            else:
                self.engine.resume()
            return
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
        if getattr(self.engine, 'can_pause', False) and self._text is not None:
            self._engine_paused = True  # the generation stays: the sentence goes on
            self.engine.pause()
        else:
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
        self._reset()
        self._started = False
        self.source.stop()
        self._set_state('stopped')

    def _reset(self):
        self._text = None
        self._engine_paused = False
        self._spoken_while_paused = False
        self._paused_from = None

    def skip(self, direction):
        """The next sentence (direction 1) or the one before (-1), at once; paused, it is
        the one playing goes on from. False when the source cannot go back."""
        if self.state == 'stopped':
            return False
        prev = getattr(self.source, 'prev', None)
        if direction < 0 and prev is None:
            return False
        self._generation += 1
        self.engine.stop()
        self._reset()
        generation = self._generation
        if self.state == 'paused':
            self._paused_from = generation  # the sentence is kept for playing again
            self._generation += 1
        ask = prev if direction < 0 else self.source.next
        ask(self._sentence_callback(generation))
        return True

    def moved(self):
        """The reader went elsewhere (a page turned, a jump): reading goes on from the page
        shown, at once (the page's source starts there when asked for a sentence)."""
        if self.state == 'stopped':
            return
        self._generation += 1
        self.engine.stop()
        self._reset()
        if self.state == 'playing':
            self.source.next(self._sentence_callback(self._generation))

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
            if generation != self._generation:
                return
            if self.state == 'paused' and self._engine_paused:
                self._spoken_while_paused = True
                return
            if self.state != 'playing':
                return
            self._text = None
            self.source.next(self._sentence_callback(generation))

        kwargs = {'rate': self.rate, 'language': self.language}
        if self.voice:
            kwargs['voice'] = self.voice
        word = getattr(self.source, 'word', None)
        if word is not None and getattr(self.engine, 'marks', False):
            def on_word(offset):
                if generation == self._generation:
                    word(offset)
            kwargs['on_word'] = on_word
        self.engine.speak(text, spoken, **kwargs)
