# SPDX-License-Identifier: GPL-2.0-or-later
# SPDX-FileCopyrightText: 2026 Jack Tully

"""Read Aloud's player: sentence after sentence with a fake engine and source, pausing (in
the engine or not), stopping, skipping back and forward, the reader moving, word marks, the
end of the book, stale callbacks; the engines over a stand-in speechd module and a stand-in
spd-say (voices listed and chosen, SSML marks); and finding no engine."""

import os
import tempfile
import unittest
from unittest import mock

from tests import ROOT  # noqa: F401
from bookcase import speech
from tests.gtk import wait_for


class FakeEngine:
    def __init__(self, can_pause=False, marks=False):
        self.spoken = []  # (text, rate, language)
        self.voices = []  # the voice of each sentence
        self.pending = None  # the done callback of the sentence being spoken
        self.on_word = None
        self.stops = 0
        self.pauses = 0
        self.resumes = 0
        self.can_pause = can_pause
        self.marks = marks

    def speak(self, text, done, rate=0, language='', voice='', on_word=None):
        self.spoken.append((text, rate, language))
        self.voices.append(voice)
        self.pending = done
        self.on_word = on_word

    def pause(self):
        self.pauses += 1

    def resume(self):
        self.resumes += 1

    def finish(self):
        done, self.pending = self.pending, None
        done()

    def stop(self):
        self.stops += 1
        self.pending = None


class FakeSource:
    """The book's sentences; answers at once, or later when `deferred`."""

    def __init__(self, sentences, deferred=False):
        self.sentences = list(sentences)
        self.position = 0
        self.deferred = deferred
        self.waiting = []
        self.calls = []

    def _give(self, callback):
        text = self.sentences[self.position] if self.position < len(self.sentences) else None
        self.position += 1
        if self.deferred:
            self.waiting.append((callback, text))
        else:
            callback(text)

    def start(self, callback):
        self.calls.append('start')
        self.position = 0
        self._give(callback)

    def next(self, callback):
        self.calls.append('next')
        self._give(callback)

    def prev(self, callback):
        self.calls.append('prev')
        self.position = max(0, self.position - 2)
        self._give(callback)

    def word(self, offset):
        self.calls.append(('word', offset))

    def stop(self):
        self.calls.append('stop')

    def answer(self):
        callback, text = self.waiting.pop(0)
        callback(text)


class ReadAloudTest(unittest.TestCase):

    def setUp(self):
        self.engine = FakeEngine()
        self.source = FakeSource(['One.', 'Two.', '  ', 'Three.'])
        self.player = speech.ReadAloud(self.engine, self.source, rate=20, language='en')
        self.states = []
        self.player.on_state = self.states.append

    def texts(self):
        return [text for text, _rate, _language in self.engine.spoken]

    def test_reads_sentence_after_sentence_to_the_end(self):
        self.player.play()
        self.assertEqual(self.engine.spoken, [('One.', 20, 'en')])
        self.engine.finish()
        self.player.rate = -30  # from the next sentence on
        self.engine.finish()
        self.assertEqual(self.texts(), ['One.', 'Two.', 'Three.'])  # the blank one skipped
        self.assertEqual(self.engine.spoken[-1][1], -30)
        self.engine.finish()
        self.assertEqual(self.player.state, 'stopped')
        self.assertEqual(self.states, ['playing', 'stopped'])
        self.assertEqual(self.source.calls[0], 'start')
        self.assertEqual(self.source.calls[-1], 'stop')

    def test_pause_speaks_the_sentence_again(self):
        self.player.play()
        self.engine.finish()
        done = self.engine.pending
        self.player.pause()
        self.assertEqual(self.player.state, 'paused')
        self.assertEqual(self.engine.stops, 1)
        done()  # the engine's late word on the sentence it was stopped in: ignored
        self.assertEqual(self.texts(), ['One.', 'Two.'])
        self.player.toggle()
        self.assertEqual(self.texts(), ['One.', 'Two.', 'Two.'])
        self.assertEqual(self.states, ['playing', 'paused', 'playing'])

    def test_stop_and_play_starts_from_the_page_again(self):
        self.player.play()
        self.player.stop()
        self.assertEqual(self.player.state, 'stopped')
        self.player.stop()  # twice is harmless
        self.player.play()
        self.assertEqual(self.source.calls, ['start', 'stop', 'start'])

    def test_pause_while_the_page_finds_the_sentence(self):
        source = FakeSource(['One.', 'Two.'], deferred=True)
        player = speech.ReadAloud(self.engine, source)
        player.play()
        source.answer()
        self.engine.finish()  # asks for 'Two.'
        player.pause()
        source.answer()  # it comes after the pause: kept, not spoken
        self.assertEqual(self.texts(), ['One.'])
        player.play()
        self.assertEqual(self.texts(), ['One.', 'Two.'])
        self.assertEqual(source.calls, ['start', 'next'])

    def test_a_sentence_after_stop_is_ignored(self):
        source = FakeSource(['One.'], deferred=True)
        player = speech.ReadAloud(self.engine, source)
        player.play()
        player.stop()
        source.answer()
        self.assertEqual(self.engine.spoken, [])


class SkipAndPauseTest(unittest.TestCase):

    def setUp(self):
        self.source = FakeSource(['One.', 'Two.', 'Three.', 'Four.'])
        self.states = []

    def player(self, engine, **kwargs):
        player = speech.ReadAloud(engine, self.source, **kwargs)
        player.on_state = self.states.append
        return player

    def test_skipping_forward_and_back(self):
        engine = FakeEngine()
        player = self.player(engine)
        self.assertFalse(player.skip(1))  # not reading: nothing to skip
        player.play()
        stale = engine.pending
        self.assertTrue(player.skip(1))
        self.assertEqual(engine.stops, 1)
        stale()  # the engine's word on the sentence it was stopped in: ignored
        self.assertTrue(player.skip(1))
        self.assertEqual([t for t, _r, _l in engine.spoken], ['One.', 'Two.', 'Three.'])
        self.assertTrue(player.skip(-1))
        self.assertEqual(engine.spoken[-1][0], 'Two.')
        engine.finish()
        self.assertEqual(engine.spoken[-1][0], 'Three.')  # and on from there
        self.assertEqual(player.state, 'playing')

    def test_skipping_while_paused_keeps_the_new_sentence_for_playing(self):
        engine = FakeEngine()
        player = self.player(engine)
        player.play()
        player.pause()
        player.skip(1)
        self.assertEqual(len(engine.spoken), 1)  # nothing said while paused
        player.play()
        self.assertEqual(engine.spoken[-1][0], 'Two.')

    def test_a_source_that_cannot_go_back(self):
        source = FakeSource(['One.', 'Two.'])
        source.prev = None
        player = speech.ReadAloud(FakeEngine(), source)
        player.play()
        self.assertFalse(player.skip(-1))

    def test_pausing_in_the_engine_goes_on_from_the_word(self):
        engine = FakeEngine(can_pause=True)
        player = self.player(engine)
        player.play()
        done = engine.pending
        player.pause()
        self.assertEqual((engine.pauses, engine.stops), (1, 0))
        player.play()
        self.assertEqual(engine.resumes, 1)
        self.assertEqual(len(engine.spoken), 1)  # not said again from its start
        done()
        self.assertEqual(engine.spoken[-1][0], 'Two.')
        self.assertEqual(self.states, ['playing', 'paused', 'playing'])

    def test_a_sentence_ending_as_it_pauses_is_not_lost(self):
        engine = FakeEngine(can_pause=True)
        player = self.player(engine)
        player.play()
        done = engine.pending
        player.pause()
        done()  # the end came in just after the pause
        self.assertEqual(len(engine.spoken), 1)
        player.play()
        self.assertEqual(engine.resumes, 0)
        self.assertEqual(engine.spoken[-1][0], 'Two.')

    def test_moving_goes_on_from_the_new_page(self):
        engine = FakeEngine()
        player = self.player(engine)
        player.play()
        stale = engine.pending
        player.moved()
        self.assertEqual(engine.stops, 1)
        self.assertEqual(self.source.calls, ['start', 'next'])
        stale()
        self.assertEqual(len(engine.spoken), 2)
        player.pause()
        player.moved()  # paused: the kept sentence is dropped, the page asked on playing
        player.play()
        self.assertEqual(self.source.calls[-1], 'next')
        self.assertEqual(engine.spoken[-1][0], 'Three.')
        player.stop()
        player.moved()  # stopped: nothing
        self.assertEqual(self.source.calls[-1], 'stop')

    def test_the_voice_and_the_words(self):
        engine = FakeEngine(marks=True)
        player = self.player(engine, voice='Invented Voice')
        player.play()
        self.assertEqual(engine.voices, ['Invented Voice'])
        engine.on_word(4)
        self.assertIn(('word', 4), self.source.calls)
        on_word = engine.on_word
        player.skip(1)
        on_word(9)  # a mark from the sentence before: ignored
        self.assertNotIn(('word', 9), self.source.calls)
        player.voice = ''
        engine.finish()
        self.assertEqual(engine.voices[-1], '')

    def test_no_word_marks_from_an_engine_without_them(self):
        engine = FakeEngine(marks=False)
        self.player(engine).play()
        self.assertIsNone(engine.on_word)


class VoicesTest(unittest.TestCase):

    def test_spd_say_lists(self):
        table = ("""                     NAME                 LANGUAGE                  VARIANT
            English (Great Britain)                    en-GB                     none
                          Inventa                       fr                   female
                         Leftover
""")
        voices = speech.parse_voices(table)
        self.assertEqual(voices, [speech.Voice('English (Great Britain)', 'en-GB', ''),
                                  speech.Voice('Inventa', 'fr', 'female')])
        self.assertEqual(speech.voices_for(voices + [speech.Voice('Ama', 'en_US')], 'EN'),
                         [speech.Voice('Ama', 'en_US'), voices[0]])
        self.assertEqual(speech.voices_for(voices, ''), [])

    def test_word_ssml_marks_each_word_by_its_offset(self):
        self.assertEqual(speech.word_ssml('Fish & chips, <now>'),
                         '<speak><mark name="0"/>Fish <mark name="5"/>&amp; '
                         '<mark name="7"/>chips, <mark name="14"/>&lt;now&gt;</speak>')


class FakeSpeechd:
    """A stand-in for python-speechd's module: one client, events delivered by hand."""

    class CallbackType:
        BEGIN, END, CANCEL, PAUSE, RESUME, INDEX_MARK = range(6)

    class DataMode:
        TEXT, SSML = 'text', 'ssml'

    clients = []

    class SSIPClient:
        def __init__(self, name):
            self.name = name
            self.calls = []
            self.callback = None
            FakeSpeechd.clients.append(self)

        def __getattr__(self, name):
            def call(*args, **kwargs):
                self.calls.append((name, args))
                if name == 'speak':
                    self.callback = kwargs.get('callback')
                    self.types = kwargs.get('event_types')
                if name == 'list_synthesis_voices':
                    return (('Inventa', 'fr', 'none'), ('Ama', 'en-GB', 'f1'))
            return call


class SpeechdEngineTest(unittest.TestCase):

    def setUp(self):
        FakeSpeechd.clients = []
        self.engine = speech.SpeechdEngine(FakeSpeechd)

    def test_speaks_ssml_and_reports_words_and_the_end(self):
        done, words = [], []
        self.engine.speak('A quiet bay.', lambda: done.append(True), rate=30, language='en',
                          voice='Ama', on_word=words.append)
        client = FakeSpeechd.clients[0]
        self.assertIn(('set_data_mode', ('ssml',)), client.calls)
        self.assertIn(('set_synthesis_voice', ('Ama',)), client.calls)
        self.assertIn(('set_rate', (30,)), client.calls)
        spoken = dict(client.calls)['speak'][0]
        self.assertTrue(spoken.startswith('<speak><mark name="0"/>A '))
        self.assertIn(FakeSpeechd.CallbackType.INDEX_MARK, client.types)
        client.callback(FakeSpeechd.CallbackType.INDEX_MARK, index_mark='2')
        client.callback(FakeSpeechd.CallbackType.END)
        self.assertTrue(wait_for(lambda: done, 1))
        self.assertEqual(words, [2])
        self.engine.speak('Again.', lambda: None, voice='Ama')
        names = [name for name, _args in client.calls]
        self.assertEqual(names.count('set_synthesis_voice'), 1)  # set once
        self.engine.pause()
        self.engine.resume()
        self.assertEqual([n for n, _a in client.calls][-2:], ['pause', 'resume'])

    def test_a_stopped_sentence_reports_nothing(self):
        done = []
        self.engine.speak('A quiet bay.', lambda: done.append(True))
        client = FakeSpeechd.clients[0]
        self.engine.stop()
        client.callback(FakeSpeechd.CallbackType.END)
        wait_for(lambda: False, 0.05)
        self.assertEqual(done, [])

    def test_lists_voices_in_a_thread(self):
        got = []
        self.engine.list_voices(got.append)
        self.assertTrue(wait_for(lambda: got, 2))
        self.assertEqual(got[0], [speech.Voice('Inventa', 'fr', ''),
                                  speech.Voice('Ama', 'en-GB', 'f1')])


class FindEngineTest(unittest.TestCase):

    def test_no_engine(self):
        with mock.patch.dict(os.environ, {'BOOKCASE_NO_SPEECH': '1'}):
            self.assertIsNone(speech.find_engine())
        with mock.patch.dict('sys.modules', {'speechd': None}), \
                mock.patch.object(speech.GLib, 'find_program_in_path', return_value=None):
            self.assertIsNone(speech.find_engine())

    def test_spd_say_when_found(self):
        with mock.patch.dict('sys.modules', {'speechd': None}), \
                mock.patch.object(speech.GLib, 'find_program_in_path',
                                  return_value='/usr/bin/spd-say'):
            engine = speech.find_engine()
        self.assertIsInstance(engine, speech.SpdSayEngine)


class SpdSayTest(unittest.TestCase):
    """SpdSayEngine with a stand-in spd-say script that writes down what it is given."""

    def test_speaks_through_standard_input(self):
        with tempfile.TemporaryDirectory() as directory:
            log = os.path.join(directory, 'said')
            program = os.path.join(directory, 'spd-say')
            with open(program, 'w', encoding='utf-8') as stream:
                stream.write(f'#!/bin/sh\necho "$@" >> {log}\ncat >> {log}\n')
            os.chmod(program, 0o755)
            engine = speech.SpdSayEngine(program)
            done = []
            engine.speak('The  quay\nwas quiet.', lambda: done.append(True), rate=150,
                         language='en')
            self.assertTrue(wait_for(lambda: done, 5))
            with open(log, encoding='utf-8') as stream:
                said = stream.read()
        self.assertIn('--wait --pipe-mode', said)
        self.assertIn('--rate 100', said)  # clamped
        self.assertIn('--language en', said)
        self.assertIn('The quay was quiet.', said)

    def test_a_voice_and_the_voices(self):
        with tempfile.TemporaryDirectory() as directory:
            log = os.path.join(directory, 'said')
            program = os.path.join(directory, 'spd-say')
            with open(program, 'w', encoding='utf-8') as stream:
                stream.write('#!/bin/sh\n'
                             'if [ "$1" = --list-synthesis-voices ]; then\n'
                             '  printf "%25s%25s%25s\\n" NAME LANGUAGE VARIANT\n'
                             '  printf "%25s%25s%25s\\n" "Inventa Two" fr none\n'
                             '  exit 0\n'
                             f'fi\necho "$@" >> {log}\ncat >> {log}\n')
            os.chmod(program, 0o755)
            engine = speech.SpdSayEngine(program)
            voices = []
            engine.list_voices(voices.append)
            self.assertTrue(wait_for(lambda: voices, 5))
            self.assertEqual(voices[0], [speech.Voice('Inventa Two', 'fr', '')])
            done = []
            engine.speak('Bonjour.', lambda: done.append(True), voice='Inventa Two')
            self.assertTrue(wait_for(lambda: done, 5))
            with open(log, encoding='utf-8') as stream:
                self.assertIn('--synthesis-voice Inventa Two', stream.read())
        self.assertFalse(engine.can_pause)


if __name__ == '__main__':
    unittest.main()
