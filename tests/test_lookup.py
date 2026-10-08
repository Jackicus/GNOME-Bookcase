# SPDX-License-Identifier: GPL-2.0-or-later
# SPDX-FileCopyrightText: 2026 Jack Tully

"""Looking up words: Wiktionary's and Wikipedia's JSON parsed (canned, invented answers),
the fallbacks, the cache, offline errors, and StarDict dictionaries generated here (plain,
dictzip'd, gzipped), with no network."""

import gzip
import os
import struct
import tempfile
import unittest
import zlib

from tests import ROOT  # noqa: F401
from bookcase import lookup
from bookcase.online import OnlineError
from tests.gtk import pump, wait_for

DEFINITION = {
    'en': [
        {'partOfSpeech': 'Noun', 'language': 'English', 'definitions': [
            {'definition': 'A <a href="/wiki/sheltered">sheltered</a> place where '
                           '<b>boats</b> rest&nbsp;at night.<style>.x{}</style>',
             'parsedExamples': [{'example': 'The <i>quillon</i> was full of skiffs.'}]},
            {'definition': '', 'examples': ['ignored']},
            {'definition': '(<i>figurative</i>) A refuge.', 'examples': ['A quillon of calm.']},
        ]},
        {'partOfSpeech': 'Verb', 'language': 'English', 'definitions': [
            {'definition': 'To moor a boat.'},
        ]},
    ],
    'fr': [
        {'partOfSpeech': 'Nom', 'language': 'French', 'definitions': [
            {'definition': 'Un abri pour bateaux.'},
        ]},
    ],
}

SUMMARY = {
    'type': 'standard', 'title': 'Quillon Bay', 'displaytitle': '<i>Quillon</i> Bay',
    'description': 'Invented bay', 'extract': 'Quillon Bay is an  invented bay.',
    'content_urls': {'desktop': {'page': 'https://en.wikipedia.org/wiki/Quillon_Bay'}},
}


def not_found_error():
    error = OnlineError('Nothing found')
    error.not_found = True
    return error


class FakeFetch:
    def __init__(self, answers):
        self.answers = answers  # url fragment -> JSON, or an exception to raise
        self.urls = []

    def __call__(self, url):
        self.urls.append(url)
        for fragment, answer in self.answers.items():
            if fragment in url:
                if isinstance(answer, Exception):
                    raise answer
                return answer
        raise not_found_error()


class WordsTest(unittest.TestCase):

    def test_words_and_phrases(self):
        self.assertEqual(lookup.clean_word(' “Quillon,” '), 'Quillon')
        self.assertTrue(lookup.is_word('quillon.'))
        self.assertTrue(lookup.is_word("sea-wall's"))
        self.assertFalse(lookup.is_word('the bay'))
        self.assertFalse(lookup.is_word('1984'))
        self.assertFalse(lookup.is_word('…'))

    def test_urls(self):
        self.assertEqual(lookup.definition_url('a/b', 'de'),
                         'https://en.wiktionary.org/api/rest_v1/page/definition/a%2Fb')
        self.assertEqual(lookup.summary_url('Quillon Bay', 'fr'),
                         'https://fr.wikipedia.org/api/rest_v1/page/summary/Quillon_Bay')
        self.assertIn('search=the+bay', lookup.wikipedia_page('the bay', 'en'))


class ParseTest(unittest.TestCase):

    def test_definitions_plain_text_in_the_books_language_first(self):
        article = lookup.parse_definitions(DEFINITION, 'quillon', 'fr')
        self.assertEqual(article.source, 'wiktionary')
        self.assertEqual([e.heading for e in article.entries], ['Nom', 'Noun', 'Verb'])
        noun = article.entries[1]
        self.assertEqual(noun.language, 'English')
        self.assertEqual(len(noun.senses), 2)  # the empty one dropped
        self.assertEqual(noun.senses[0].text, 'A sheltered place where boats rest at night.')
        self.assertEqual(noun.senses[0].examples, ('The quillon was full of skiffs.',))
        self.assertEqual(noun.senses[1].examples, ('A quillon of calm.',))
        self.assertNotIn('<', ''.join(s.text for e in article.entries for s in e.senses))
        self.assertEqual(article.url, 'https://en.wiktionary.org/wiki/quillon')

    def test_definitions_of_junk(self):
        self.assertEqual(lookup.parse_definitions({}, 'x').entries, ())
        self.assertEqual(lookup.parse_definitions({'en': 'oops', 'de': [None, {}]}, 'x').entries,
                         ())

    def test_summary(self):
        summary = lookup.parse_summary(SUMMARY)
        self.assertEqual(summary.title, 'Quillon Bay')
        self.assertEqual(summary.description, 'Invented bay')
        self.assertEqual(summary.extract, 'Quillon Bay is an invented bay.')
        self.assertEqual(summary.url, 'https://en.wikipedia.org/wiki/Quillon_Bay')
        self.assertIsNone(lookup.parse_summary({'title': 'Empty', 'extract': ''}))
        unsafe = dict(SUMMARY, content_urls={'desktop': {'page': 'javascript:alert(1)'}})
        self.assertEqual(lookup.parse_summary(unsafe).url, '')


class ServiceTest(unittest.TestCase):

    def test_define_falls_back_to_lower_case_and_caches(self):
        fetch = FakeFetch({'definition/quillon': DEFINITION})
        service = lookup.Lookup(fetch=fetch, dictionaries=[])
        article = service.define_now('Quillon,', 'en')
        self.assertEqual(article.word, 'quillon')
        self.assertEqual(len(fetch.urls), 2)
        service.define_now('Quillon', 'en')
        self.assertEqual(len(fetch.urls), 2)  # cached

    def test_define_nothing_found(self):
        service = lookup.Lookup(fetch=FakeFetch({}), dictionaries=[])
        with self.assertRaises(OnlineError) as caught:
            service.define_now('zzyzx')
        self.assertTrue(lookup.not_found(caught.exception))

    def test_offline(self):
        offline = OnlineError('Could not reach', offline=True)
        service = lookup.Lookup(fetch=FakeFetch({'wik': offline}), dictionaries=[])
        with self.assertRaises(OnlineError) as caught:
            service.define_now('quillon')
        self.assertTrue(caught.exception.offline)

    def test_summary_searches_for_a_phrase(self):
        fetch = FakeFetch({'summary/Quillon_Bay': SUMMARY,
                           'search/title': {'pages': [{'title': 'Quillon Bay'}]}})
        service = lookup.Lookup(fetch=fetch, dictionaries=[])
        summary = service.summarize_now('the bay of quillon', 'en')
        self.assertEqual(summary.title, 'Quillon Bay')
        self.assertIn('search/title', fetch.urls[1])

    def test_async_delivery_and_cancel(self):
        service = lookup.Lookup(fetch=FakeFetch({'definition/quillon': DEFINITION}),
                                dictionaries=[])
        got = []
        service.define('quillon', 'en', lambda article, error: got.append((article, error)))
        cancelled = service.define('quillon', 'en', lambda *args: got.append('cancelled'))
        cancelled.cancel()
        wait_for(lambda: got, 3)
        pump(100)
        self.assertEqual(len(got), 1)
        self.assertEqual(got[0][0].word, 'quillon')

    def test_offline_dictionary_first(self):
        with tempfile.TemporaryDirectory() as directory:
            make_stardict(directory, 'tiny', {'quillon': 'm' + 'An invented harbour.'})
            service = lookup.Lookup(fetch=FakeFetch({}),
                                    dictionaries=lookup.find_dictionaries([directory]))
            article = service.define_now('Quillon')
        self.assertEqual(article.source, 'Tiny Invented Dictionary')
        self.assertEqual(article.entries[0].senses[0].text, 'An invented harbour.')
        self.assertEqual(article.url, '')


# -- StarDict ----------------------------------------------------------------------------------

def dictzip(data, chunk_length):
    """A dictzip file's bytes: gzip with an RA field, each chunk flushed on its own."""
    chunks = []
    compressor = zlib.compressobj(9, zlib.DEFLATED, -15)
    for start in range(0, len(data), chunk_length):
        piece = compressor.compress(data[start:start + chunk_length])
        piece += compressor.flush(zlib.Z_FULL_FLUSH)
        chunks.append(piece)
    tail = compressor.flush(zlib.Z_FINISH)
    body = struct.pack('<HHH', 1, chunk_length, len(chunks))
    body += b''.join(struct.pack('<H', len(c)) for c in chunks)
    extra = b'RA' + struct.pack('<H', len(body)) + body
    header = b'\x1f\x8b\x08\x04' + b'\x00' * 4 + b'\x02\x03' + struct.pack('<H', len(extra))
    trailer = struct.pack('<II', zlib.crc32(data), len(data) & 0xffffffff)
    return header + extra + b''.join(chunks) + tail + trailer


def make_stardict(directory, name, articles, types='', dz=False, gz_idx=False, wide=False,
                  synonyms=None, chunk_length=16, bookname='Tiny Invented Dictionary'):
    """A StarDict dictionary: articles {word: raw article str}; with `types` the
    sametypesequence, else each article's fields carry their own type characters."""
    data = b''
    index = b''
    words = sorted(articles, key=lambda w: w.lower())
    for word in words:
        raw = articles[word].encode('utf-8')
        index += word.encode('utf-8') + b'\x00'
        index += struct.pack('>QI' if wide else '>II', len(data), len(raw))
        data += raw
    base = os.path.join(directory, name)
    if dz:
        with open(base + '.dict.dz', 'wb') as stream:
            stream.write(dictzip(data, chunk_length))
    else:
        with open(base + '.dict', 'wb') as stream:
            stream.write(data)
    if gz_idx:
        with open(base + '.idx.gz', 'wb') as stream:
            stream.write(gzip.compress(index))
    else:
        with open(base + '.idx', 'wb') as stream:
            stream.write(index)
    if synonyms:
        syn = b''
        for synonym, word in synonyms.items():
            syn += synonym.encode('utf-8') + b'\x00' + struct.pack('>I', words.index(word))
        with open(base + '.syn', 'wb') as stream:
            stream.write(syn)
    lines = ["StarDict's dict ifo file", 'version=3.0.0', f'bookname={bookname}',
             f'wordcount={len(words)}', f'idxfilesize={len(index)}']
    if types:
        lines.append(f'sametypesequence={types}')
    if wide:
        lines.append('idxoffsetbits=64')
    with open(base + '.ifo', 'w', encoding='utf-8') as stream:
        stream.write('\n'.join(lines) + '\n')
    return base + '.ifo'


WORDS = {
    'quillon': 'A small invented harbour, where the boats of the story rest.',
    'Brindle': 'Streaked with a darker colour; said of invented cats.',
    'zephyrine': 'Of a soft west wind, in this invented dictionary only.',
}


class StarDictTest(unittest.TestCase):

    def setUp(self):
        self._directory = tempfile.TemporaryDirectory()
        self.directory = self._directory.name

    def tearDown(self):
        self._directory.cleanup()

    def test_same_type_sequence_plain(self):
        path = make_stardict(self.directory, 'plain', WORDS, types='m')
        dictionary = lookup.StarDict(path)
        self.assertEqual(dictionary.name, 'Tiny Invented Dictionary')
        self.assertEqual(dictionary.lookup('QUILLON'), [WORDS['quillon']])
        self.assertEqual(dictionary.lookup('brindle'), [WORDS['Brindle']])
        self.assertEqual(dictionary.lookup('absent'), [])

    def test_dictzip_across_chunks_with_gzipped_index_and_64_bit_offsets(self):
        path = make_stardict(self.directory, 'zipped', WORDS, types='m', dz=True, gz_idx=True,
                             wide=True, chunk_length=7)
        dictionary = lookup.StarDict(path)
        for word, text in WORDS.items():
            self.assertEqual(dictionary.lookup(word), [text])

    def test_plain_gzip_dict_without_chunks(self):
        path = make_stardict(self.directory, 'gz', WORDS, types='m')
        with open(path[:-4] + '.dict', 'rb') as stream:
            data = stream.read()
        os.remove(path[:-4] + '.dict')
        with open(path[:-4] + '.dict.dz', 'wb') as stream:
            stream.write(gzip.compress(data))
        self.assertEqual(lookup.StarDict(path).lookup('zephyrine'), [WORDS['zephyrine']])

    def test_typed_fields_and_html(self):
        articles = {'quillon': 'h<b>A harbour</b>&amp; a rest\x00m second field\x00'}
        path = make_stardict(self.directory, 'typed', articles)
        text = lookup.StarDict(path).lookup('quillon')[0]
        self.assertEqual(text, 'A harbour& a rest\nsecond field')

    def test_synonyms(self):
        path = make_stardict(self.directory, 'syn', WORDS, types='m',
                             synonyms={'quillons': 'quillon'})
        self.assertEqual(lookup.StarDict(path).lookup('Quillons'), [WORDS['quillon']])

    def test_find_dictionaries_skips_broken_ones(self):
        make_stardict(self.directory, 'good', WORDS, types='m')
        nested = os.path.join(self.directory, 'nested')
        os.mkdir(nested)
        make_stardict(nested, 'inner', WORDS, types='m')
        with open(os.path.join(self.directory, 'broken.ifo'), 'w', encoding='utf-8') as stream:
            stream.write('not a dictionary\n')
        with self.assertLogs('bookcase.lookup', 'WARNING'):
            found = lookup.find_dictionaries([self.directory, '/nonexistent/bookcase'])
        self.assertEqual(len(found), 2)


class LemmaTest(unittest.TestCase):

    def test_english_inflections(self):
        cases = {'quillons': 'quillon', 'boxes': 'box', 'stories': 'story',
                 'churches': 'church', 'stopped': 'stop', 'hoped': 'hope', 'walked': 'walk',
                 'carried': 'carry', 'running': 'run', 'making': 'make', 'singing': 'sing',
                 'bigger': 'big', 'later': 'late', 'happiest': 'happy', 'darkest': 'dark',
                 'Brindles,': 'brindle'}
        for word, lemma in cases.items():
            self.assertEqual(lookup.lemmas(word)[0], lemma, word)
        self.assertIn('spell', lookup.lemmas('spelled'))
        self.assertEqual(lookup.lemmas('glass'), [])  # -ss is no plural
        self.assertEqual(lookup.lemmas('is'), [])
        self.assertEqual(lookup.lemmas('maisons', 'fr'), [])  # English only
        self.assertNotIn('quillons', lookup.lemmas('quillons'))


class PreferencesTest(unittest.TestCase):
    """The service under the Look Up preferences: lemmas offline, online off, the
    dictionaries' order and those turned off."""

    def setUp(self):
        self._directory = tempfile.TemporaryDirectory()
        directory = self._directory.name
        self.first = make_stardict(directory, 'first', WORDS, types='m', bookname='First')
        self.second = make_stardict(directory, 'second', {'quillon': 'A second meaning.'},
                                    types='m', bookname='Second')
        self.fetch = FakeFetch({'definition/': DEFINITION, 'summary/': SUMMARY})
        self.service = lookup.Lookup(fetch=self.fetch,
                                     dictionaries=[lookup.StarDict(self.second),
                                                   lookup.StarDict(self.first)])

    def tearDown(self):
        self._directory.cleanup()

    def test_an_inflected_word_is_found_as_its_dictionary_form(self):
        article = self.service.define_now('Quillons', 'en')
        self.assertEqual(article.word, 'quillon')
        self.assertEqual(self.fetch.urls, [])
        article = self.service.define_now('brindled', 'en')
        self.assertEqual(article.entries[0].senses[0].text, WORDS['Brindle'])

    def test_order_and_disabled(self):
        names = [d.name for d in self.service.dictionaries()]
        self.assertEqual(names, ['First', 'Second'])  # by name when no order is set
        self.service.configure(order=[self.second, self.first])
        self.assertEqual([d.name for d in self.service.dictionaries()], ['Second', 'First'])
        article = self.service.define_now('quillon')
        self.assertEqual([e.heading for e in article.entries], ['Second', 'First'])
        self.service.configure(order=[self.second], disabled=[self.second])
        self.assertEqual([d.name for d in self.service.dictionaries()], ['First'])
        self.assertEqual(len(self.service.all_dictionaries()), 2)
        article = self.service.define_now('quillon')  # the cache was emptied
        self.assertEqual([e.heading for e in article.entries], ['First'])

    def test_offline_only_never_fetches(self):
        self.service.configure(online=False)
        with self.assertRaises(OnlineError) as caught:
            self.service.define_now('absentword')
        self.assertTrue(caught.exception.offline_only)
        self.assertTrue(lookup.not_found(caught.exception))
        with self.assertRaises(OnlineError) as caught:
            self.service.summarize_now('Quillon Bay')
        self.assertTrue(caught.exception.offline_only)
        self.assertEqual(self.fetch.urls, [])
        self.assertEqual(self.service.define_now('zephyrine').source, 'First')
        self.service.configure(online=True)
        self.assertEqual(self.service.define_now('absentword').source, 'wiktionary')

    def test_the_link_to_free_dictionaries(self):
        self.assertTrue(lookup.FREE_DICTIONARIES.startswith('https://'))


if __name__ == '__main__':
    unittest.main()
