# SPDX-License-Identifier: GPL-2.0-or-later
# SPDX-FileCopyrightText: 2026 Jack Tully

"""online.py on canned answers (invented books, ids and numbers); no network."""

import json
import unittest
import urllib.parse

from tests import ROOT  # noqa: F401  (registers bookcase)

from bookcase import online

ISBN = '9780000000002'  # a valid invented ISBN-13
ISBN10 = '0000000000'

OL_BOOKS = {
    f'ISBN:{ISBN}': {
        'key': '/books/OL1000001M',
        'title': 'A Quiet Harbour',
        'subtitle': 'A Novel',
        'authors': [{'name': 'Ada Lark', 'url': 'https://openlibrary.org/authors/OL1A'}],
        'publishers': [{'name': 'Tidewater Press'}],
        'publish_date': 'May 3, 2011',
        'number_of_pages': 312,
        'identifiers': {'isbn_13': [ISBN], 'openlibrary': ['OL1000001M']},
        'subjects': [{'name': 'Harbours'}, {'name': 'Fiction'}],
        'cover': {'small': 'https://covers.example/1-S.jpg',
                  'medium': 'https://covers.example/1-M.jpg',
                  'large': 'https://covers.example/1-L.jpg'},
    },
}

OL_SEARCH = {
    'numFound': 3,
    'docs': [
        {'key': '/works/OL2W', 'title': 'Harbour Lights', 'author_name': ['Ben Ross'],
         'first_publish_year': 1999, 'cover_i': 222},
        {'key': '/works/OL1W', 'title': 'A Quiet Harbour', 'author_name': ['Ada Lark'],
         'first_publish_year': 2011, 'language': ['eng'], 'cover_i': 111,
         'publisher': ['Tidewater Press', 'Other House'], 'isbn': ['bad', ISBN10],
         'editions': {'numFound': 1, 'docs': [
             {'key': '/books/OL1000001M', 'title': 'A Quiet Harbour',
              'publish_date': ['2011-05-03'], 'language': ['eng'], 'cover_i': 112,
              'isbn': [ISBN], 'publisher': ['Tidewater Press']}]}},
        {'title': ''},  # no title: dropped
        'junk',
    ],
}

OL_EDITION = {'key': '/books/OL1000001M', 'series': ['The Harbour Books ; 2'],
              'languages': [{'key': '/languages/eng'}], 'works': [{'key': '/works/OL1W'}]}
OL_WORK = {'key': '/works/OL1W',
           'description': {'type': '/type/text',
                           'value': 'The tide comes in.\r\n\r\nSee [the map](https://x.example)'
                                    '.\n\n----------\nEdition notes'},
           'subjects': ['Sea']}

GOOGLE = {
    'items': [
        {'id': 'gVol1', 'volumeInfo': {
            'title': 'A Quiet Harbour', 'authors': ['Ada Lark'],
            'publishedDate': '2011', 'language': 'en',
            'description': '<p>A <b>calm</b> book.</p>',
            'industryIdentifiers': [{'type': 'ISBN_13', 'identifier': ISBN}],
            'imageLinks': {'thumbnail': 'http://books.example/t?id=1&zoom=1&edge=curl'},
            'seriesInfo': {'bookDisplayNumber': '2'}}},
        {'id': 'gVol2', 'volumeInfo': {'title': 'Quiet Waters', 'authors': ['Cy Moss'],
                                        'publishedDate': 'not a date'}},
        {'id': 'gVol3'},  # no volumeInfo: dropped
    ],
}


class Server:
    """A fetch function answering canned JSON by URL path, recording the URLs asked."""

    def __init__(self, answers, fail=()):
        self.answers = answers
        self.fail = fail
        self.urls = []

    def __call__(self, url):
        self.urls.append(url)
        parts = urllib.parse.urlsplit(url)
        for host in self.fail:
            if parts.hostname == host:
                raise online.OnlineError(f'cannot reach {host}', offline=True)
        if parts.path not in self.answers:
            raise online.OnlineError('Not found')
        answer = self.answers[parts.path]
        return answer if isinstance(answer, bytes) else json.dumps(answer).encode()


class TestHelpers(unittest.TestCase):
    def test_isbn(self):
        self.assertEqual(online.normalize_isbn('978-0-00-000000-2'), ISBN)
        self.assertEqual(online.normalize_isbn(ISBN10), ISBN)  # ISBN-10 made 13
        self.assertEqual(online.normalize_isbn('9780000000003'), '')  # bad check digit
        self.assertEqual(online.normalize_isbn('hello'), '')

    def test_dates(self):
        self.assertEqual(online.parse_date('2004'), '2004')
        self.assertEqual(online.parse_date('2004-05-01'), '2004-05-01')
        self.assertEqual(online.parse_date('2004-05'), '2004-05')
        self.assertEqual(online.parse_date('May 1, 2004'), '2004-05-01')
        self.assertEqual(online.parse_date('1 May 2004'), '2004-05-01')
        self.assertEqual(online.parse_date('May 2004'), '2004-05')
        self.assertEqual(online.parse_date('c2004'), '2004')
        self.assertEqual(online.parse_date('someday'), '')
        self.assertEqual(online.parse_date(None), '')

    def test_languages(self):
        self.assertEqual(online.language_code('/languages/eng'), 'en')
        self.assertEqual(online.language_code('ger'), 'de')
        self.assertEqual(online.language_code('pt-BR'), 'pt')
        self.assertEqual(online.language_code(''), '')

    def test_html(self):
        self.assertEqual(online.plain_to_html('One <two>\nline\n\n\nThree'),
                         '<p>One &lt;two&gt;<br>line</p><p>Three</p>')
        self.assertEqual(online.html_to_plain('<p>One&amp;<br/>two</p><p>Three</p>'),
                         'One&\ntwo\n\nThree')
        self.assertEqual(online.html_to_plain('<ul><li>a</li><li>b</li></ul>'), '• a\n• b')
        self.assertEqual(online.html_to_plain('Just text, 1 < 2'), 'Just text, 1 < 2')
        text = 'First paragraph.\n\nSecond, with a\nbreak.'
        self.assertEqual(online.html_to_plain(online.plain_to_html(text)), text)

    def test_similarity(self):
        self.assertEqual(online.similarity('The Quiet Harbour', 'quiet harbour!'), 1.0)
        self.assertEqual(online.similarity('Éclair', 'eclair'), 1.0)
        self.assertLess(online.similarity('A Quiet Harbour', 'Loud Mountains'), 0.5)
        self.assertEqual(online.similarity('', 'x'), 0.0)


class TestParsing(unittest.TestCase):
    def test_books_api(self):
        [found] = online.parse_open_library_books(OL_BOOKS)
        self.assertEqual(found.title, 'A Quiet Harbour: A Novel')
        self.assertEqual(found.authors, ('Ada Lark',))
        self.assertEqual(found.publisher, 'Tidewater Press')
        self.assertEqual(found.published, '2011-05-03')
        self.assertEqual(found.identifiers, {'isbn': ISBN, 'openlibrary': 'OL1000001M'})
        self.assertEqual(found.cover_url, 'https://covers.example/1-L.jpg')
        self.assertEqual(found.edition_key, '/books/OL1000001M')
        self.assertEqual(found.pages, 312)

    def test_robust_to_junk(self):
        for junk in (None, [], 'x', {'docs': None}, {'docs': [None, 3]}, {'items': 'x'},
                     {'ISBN:1': {'authors': 'x', 'identifiers': [], 'cover': 'x'}}):
            self.assertEqual(online.parse_open_library_search(junk), [])
            self.assertEqual(online.parse_google(junk), [])
            self.assertEqual(online.parse_open_library_books(junk), [])

    def test_search_api_prefers_the_edition(self):
        found = online.parse_open_library_search(OL_SEARCH)
        self.assertEqual([c.title for c in found], ['Harbour Lights', 'A Quiet Harbour'])
        harbour = found[1]
        self.assertEqual(harbour.published, '2011-05-03')
        self.assertEqual(harbour.language, 'en')
        self.assertEqual(harbour.identifiers['isbn'], ISBN)
        self.assertEqual(harbour.edition_key, '/books/OL1000001M')
        self.assertIn('/b/id/112-L.jpg', harbour.cover_url)
        self.assertEqual(found[0].published, '1999')
        self.assertEqual(found[0].identifiers, {})

    def test_google(self):
        found = online.parse_google(GOOGLE)
        self.assertEqual(len(found), 2)
        first = found[0]
        self.assertEqual(first.identifiers, {'isbn': ISBN, 'google': 'gVol1'})
        self.assertEqual(first.description, '<p>A <b>calm</b> book.</p>')
        self.assertEqual(first.thumbnail_url, 'https://books.example/t?id=1&zoom=1')
        self.assertEqual(first.cover_url, 'https://books.example/t?id=1&zoom=0')
        self.assertEqual(first.series_index, 2.0)
        self.assertEqual(found[1].published, '')


class TestSearch(unittest.TestCase):
    def server(self, **kwargs):
        return Server({'/api/books': OL_BOOKS, '/search.json': OL_SEARCH,
                       '/books/v1/volumes': GOOGLE, '/books/OL1000001M.json': OL_EDITION,
                       '/works/OL1W.json': OL_WORK}, **kwargs)

    def test_by_isbn_merges_sources(self):
        server = self.server()
        found = online.search('A Quiet Harbour', ['Ada Lark'], isbn=ISBN10, google_key='k',
                              fetch=server)
        self.assertTrue(any('bibkeys=ISBN%3A' + ISBN in url for url in server.urls))
        self.assertTrue(any('key=k' in url and 'isbn%3A' in url for url in server.urls))
        first = found[0]
        self.assertEqual(first.sources, ('openlibrary', 'google'))
        self.assertEqual(first.source_name, 'Open Library + Google Books')
        self.assertEqual(first.title, 'A Quiet Harbour: A Novel')  # Open Library's
        self.assertEqual(first.description, '<p>A <b>calm</b> book.</p>')  # Google's
        self.assertEqual(first.identifiers['google'], 'gVol1')
        self.assertFalse(any('search.json' in url for url in server.urls))

    def test_without_key_google_is_not_asked(self):
        server = self.server()
        online.search('A Quiet Harbour', ['Ada Lark'], fetch=server)
        self.assertFalse(any('googleapis' in url for url in server.urls))
        url = next(url for url in server.urls if 'search.json' in url)
        query = urllib.parse.parse_qs(urllib.parse.urlsplit(url).query)
        self.assertEqual(query['title'], ['A Quiet Harbour'])
        self.assertEqual(query['author'], ['Ada Lark'])

    def test_ranked_by_similarity(self):
        found = online.search('A Quiet Harbour', ['Ada Lark'], fetch=self.server())
        self.assertEqual(found[0].title, 'A Quiet Harbour')
        self.assertGreater(found[0].score, found[1].score)

    def test_unknown_isbn_falls_back_on_the_title(self):
        server = self.server()
        server.answers['/api/books'] = {}
        found = online.search('A Quiet Harbour', ['Ada Lark'], isbn=ISBN, fetch=server)
        self.assertEqual(found[0].title, 'A Quiet Harbour')

    def test_offline(self):
        server = self.server(fail=('openlibrary.org', 'www.googleapis.com'))
        with self.assertRaises(online.OnlineError) as caught:
            online.search('A Quiet Harbour', fetch=server, google_key='k')
        self.assertTrue(caught.exception.offline)

    def test_one_source_failing_is_enough(self):
        server = self.server(fail=('www.googleapis.com',))
        self.assertTrue(online.search('A Quiet Harbour', fetch=server, google_key='k'))

    def test_nothing_to_search(self):
        with self.assertRaises(ValueError):
            online.search('  ', [''], fetch=self.server())

    def test_complete(self):
        found = online.search('A Quiet Harbour', ['Ada Lark'], fetch=self.server())[0]
        done = online.complete(found, fetch=self.server())
        self.assertEqual(done.series, 'The Harbour Books')
        self.assertEqual(done.series_index, 2.0)
        self.assertEqual(done.description, '<p>The tide comes in.</p><p>See the map.</p>')
        self.assertEqual(done.tags, ('Sea',))
        self.assertEqual(found.description, '')  # a copy

    def test_complete_survives_failures(self):
        found = online.search('A Quiet Harbour', ['Ada Lark'], fetch=self.server())[0]
        done = online.complete(found, fetch=Server({}))
        self.assertEqual(done.title, found.title)

    def test_series_forms(self):
        self.assertEqual(online._series('Harbour, #3'), ('Harbour', 3.0))
        self.assertEqual(online._series('Harbour (4)'), ('Harbour', 4.0))
        self.assertEqual(online._series('Harbour'), ('Harbour', 0.0))


class TestCovers(unittest.TestCase):
    def test_image(self):
        png = b'\x89PNG\r\n\x1a\n' + bytes(300)
        self.assertEqual(online.fetch_cover('https://x/c', fetch=lambda url: png), png)

    def test_placeholder(self):
        gif = b'GIF89a' + bytes(30)
        with self.assertRaises(online.OnlineError):
            online.fetch_cover('https://x/c', fetch=lambda url: gif)
        with self.assertRaises(online.OnlineError):
            online.fetch_cover('https://x/c', fetch=lambda url: b'<html>' + bytes(300))


class TestAsync(unittest.TestCase):
    def wait(self, predicate):
        from tests.gtk import wait_for

        wait_for(predicate, timeout=2)

    def test_delivers_on_the_main_loop(self):
        results = []
        online.run_async(lambda a, b=0: a + b, lambda r, e: results.append((r, e)), 1, b=2)
        self.wait(lambda: results)
        self.assertEqual(results, [(3, None)])

    def test_error(self):
        results = []

        def fail():
            raise online.OnlineError('nope')

        online.run_async(fail, lambda r, e: results.append((r, e)))
        self.wait(lambda: results)
        self.assertIsNone(results[0][0])
        self.assertIsInstance(results[0][1], online.OnlineError)

    def test_cancelled(self):
        import threading

        from gi.repository import GLib

        go = threading.Event()
        results = []
        task = online.run_async(go.wait, lambda r, e: results.append(r))
        task.cancel()
        go.set()
        finished = []
        GLib.timeout_add(50, lambda: finished.append(True))
        self.wait(lambda: finished)
        self.assertEqual(results, [])
        self.assertTrue(task.cancelled)


if __name__ == '__main__':
    unittest.main()
