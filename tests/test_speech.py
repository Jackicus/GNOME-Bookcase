# SPDX-License-Identifier: GPL-2.0-or-later
# SPDX-FileCopyrightText: 2026 Jack Tully

"""Read Aloud's player: sentence after sentence with a fake engine and source, pausing,
stopping, the end of the book, stale callbacks; and finding no engine."""

import os
import tempfile
import unittest
from unittest import mock

from tests import ROOT  # noqa: F401
from bookcase import speech
from tests.gtk import wait_for


class FakeEngine:
    def __init__(self):
        self.spoken = []  # (text, rate, language)
        self.pending = None  # the done callback of the sentence being spoken
        self.stops = 0

    def speak(self, text, done, rate=0, language=''):
        self.spoken.append((text, rate, language))
        self.pending = done

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


if __name__ == '__main__':
    unittest.main()
