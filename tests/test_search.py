# SPDX-License-Identifier: GPL-2.0-or-later
# SPDX-FileCopyrightText: 2026 Jack Tully

"""The search syntax, run against a small invented library."""

import datetime
import time
import unittest

from tests import ROOT  # noqa: F401
from bookcase import search
from tests.support import add_book, temporary_library

DAY = 86400


class SearchTest(unittest.TestCase):

    @classmethod
    def setUpClass(cls):
        cls.context = temporary_library()
        library = cls.library = cls.context.__enter__()
        cls.now = time.time()
        cls.harbour = add_book(library, 'The Quiet Harbour', ('Ada Lark',),
                               tags=['Sea', 'Urban Fantasy'], series='Harbour Tales',
                               publisher='Gull Press', published='2001-05-02', language='en',
                               identifiers={'isbn': '978-0-00-000000-2'})
        cls.fog = add_book(library, 'Fog Over Ashby', ('Ben Ross', 'Ada Lark'),
                           tags=['Mystery', 'Fantasy'], published='1999', language='en-GB',
                           fmt='pdf')
        cls.zola = add_book(library, 'Les Mouettes', ('Émile Ãrdent',), tags=['Sea'],
                            published='2020-01', language='fr')
        cls.percent = add_book(library, '100% Night_Train', ('Cara Moss',))
        library.update_book(cls.harbour, rating=8)
        library.update_book(cls.fog, rating=5)
        library.set_status([cls.fog], 'finished')
        library.set_progress(cls.zola, 0.1, 'cfi')
        library.db.execute('UPDATE books SET added = ? WHERE id = ?',
                           (cls.now - 100 * DAY, cls.harbour))
        library.db.execute('UPDATE books SET added = ?, last_read = ? WHERE id = ?',
                           (datetime.datetime(2024, 6, 1, 12).timestamp(),
                            cls.now - 20 * DAY, cls.fog))
        library.add_annotation(cls.zola, 'highlight', 'cfi', note='a thought')
        shelf = library.add_shelf('Summer Reading')
        library.add_to_shelf(shelf, [cls.percent])
        library.update_book(cls.percent, has_cover=True)

    @classmethod
    def tearDownClass(cls):
        cls.context.__exit__(None, None, None)

    def find(self, query):
        where, params = search.to_sql(query, now=self.now)
        rows = self.library.db.execute(f'SELECT b.id FROM books b WHERE {where}', params)
        return {row[0] for row in rows}

    def test_empty(self):
        self.assertEqual(search.to_sql(''), ('1', []))
        self.assertEqual(search.to_sql('   '), ('1', []))
        self.assertEqual(len(self.find('')), 4)

    def test_words(self):
        self.assertEqual(self.find('harbour'), {self.harbour})
        self.assertEqual(self.find('HARB lark'), {self.harbour})
        self.assertEqual(self.find('lark'), {self.harbour, self.fog})
        self.assertEqual(self.find('gull'), {self.harbour})  # the publisher
        self.assertEqual(self.find('mystery'), {self.fog})  # a tag
        self.assertEqual(self.find('tales'), {self.harbour})  # the series
        self.assertEqual(self.find('emile ardent'), {self.zola})  # accents
        self.assertEqual(self.find('ÉMILE'), {self.zola})

    def test_phrases_and_likes_specials(self):
        self.assertEqual(self.find('"quiet harbour"'), {self.harbour})
        self.assertEqual(self.find('"harbour quiet"'), set())
        self.assertEqual(self.find('100%'), {self.percent})
        self.assertEqual(self.find('night_train'), {self.percent})
        self.assertEqual(self.find('t_ain'), set())
        self.assertEqual(self.find('"unclosed phrase'), set())

    def test_negation_or_and_groups(self):
        self.assertEqual(self.find('lark -ross'), {self.harbour})
        self.assertEqual(self.find('-lark'), {self.zola, self.percent})
        self.assertEqual(self.find('-"quiet harbour" lark'), {self.fog})
        self.assertEqual(self.find('harbour or mouettes'), {self.harbour, self.zola})
        self.assertEqual(self.find('harbour OR mouettes and sea'), {self.harbour, self.zola})
        self.assertEqual(self.find('(harbour or fog) -tag:mystery'), {self.harbour})
        self.assertEqual(self.find('-(harbour or fog)'), {self.zola, self.percent})

    def test_fields(self):
        self.assertEqual(self.find('title:fog'), {self.fog})
        self.assertEqual(self.find('title:lark'), set())
        self.assertEqual(self.find('author:lark'), {self.harbour, self.fog})
        self.assertEqual(self.find('author:"ben ross"'), {self.fog})
        self.assertEqual(self.find('by:ross'), {self.fog})
        self.assertEqual(self.find('series:harbour'), {self.harbour})
        self.assertEqual(self.find('tag:fantasy'), {self.harbour, self.fog})
        self.assertEqual(self.find('tag:=fantasy'), {self.fog})
        self.assertEqual(self.find('tag:="urban fantasy"'), {self.harbour})
        self.assertEqual(self.find('publisher:gull'), {self.harbour})
        self.assertEqual(self.find('shelf:summer'), {self.percent})
        self.assertEqual(self.find('language:en'), {self.harbour, self.fog})
        self.assertEqual(self.find('lang:fr'), {self.zola})
        self.assertEqual(self.find('format:pdf'), {self.fog})
        self.assertEqual(self.find('format:EPUB'), {self.harbour, self.zola, self.percent})
        self.assertEqual(self.find('isbn:9780000000002'), {self.harbour})
        self.assertEqual(self.find('isbn:000000'), {self.harbour})
        self.assertEqual(self.find('isbn:none'), set())

    def test_status_and_rating(self):
        self.assertEqual(self.find('status:finished'), {self.fog})
        self.assertEqual(self.find('status:read'), {self.fog})
        self.assertEqual(self.find('status:reading'), {self.zola})
        self.assertEqual(self.find('status:unread'), {self.harbour, self.percent})
        self.assertEqual(self.find('status:lost'), set())
        self.assertEqual(self.find('rating:4'), {self.harbour})
        self.assertEqual(self.find('rating:>=2.5'), {self.harbour, self.fog})
        self.assertEqual(self.find('rating:<3'), {self.fog, self.zola, self.percent})
        self.assertEqual(self.find('rating:0'), {self.zola, self.percent})
        self.assertEqual(self.find('rating:many'), set())
        self.assertEqual(self.find('rating:nan'), set())

    def test_times(self):
        self.assertEqual(self.find('added:<30d'), {self.zola, self.percent})
        self.assertEqual(self.find('added:>30d'), {self.harbour, self.fog})
        self.assertEqual(self.find('added:>1y'), {self.fog} if self.now - datetime.datetime(
            2024, 6, 1, 12).timestamp() > 365 * DAY else set())
        self.assertEqual(self.find('added:2024-06'), {self.fog})
        self.assertEqual(self.find('added:2024-06-01'), {self.fog})
        self.assertEqual(self.find('added:<2024-06-01'), set())
        self.assertEqual(self.find('added:<=2024-06-01'), {self.fog})
        self.assertEqual(self.find('added:>=2024-06-02') & {self.fog}, set())
        self.assertEqual(self.find('read:<7d'), {self.zola})
        self.assertEqual(self.find('read:<30d'), {self.zola, self.fog})
        self.assertEqual(self.find('read:>7d'), {self.fog})
        self.assertEqual(self.find('read:today'), {self.zola})
        self.assertEqual(self.find('added:2024-13'), set())
        self.assertEqual(self.find('added:soon'), set())

    def test_published(self):
        self.assertEqual(self.find('published:2001'), {self.harbour})
        self.assertEqual(self.find('published:>=2000'), {self.harbour, self.zola})
        self.assertEqual(self.find('published:<2000'), {self.fog})
        self.assertEqual(self.find('year:2020-1'), {self.zola})
        self.assertEqual(self.find('published:<=2001-05'), {self.harbour, self.fog})
        self.assertEqual(self.find('published:old'), set())

    def test_has(self):
        self.assertEqual(self.find('has:cover'), {self.percent})
        self.assertEqual(self.find('has:series'), {self.harbour})
        self.assertEqual(self.find('has:rating'), {self.harbour, self.fog})
        self.assertEqual(self.find('has:notes'), {self.zola})
        self.assertEqual(self.find('has:annotations'), {self.zola})
        self.assertEqual(self.find('-has:tags'), {self.percent})
        self.assertEqual(self.find('has:wings'), set())

    def test_unknown_fields_are_words(self):
        self.assertEqual(self.find('fog:'), set())
        self.assertEqual(self.find('colour:red'), set())
        self.assertEqual(self.find('title:'), self.find(''))

    def test_never_raises(self):
        nasty = ['(((', ')))', '-', '- -', 'or', 'or or', '(or)', '"', '""', 'title:"',
                 'author:="', ':', '::', '-(', '(a or', 'a or )', '\\', "'", '%_%', 'rating:>',
                 'rating:>=x', 'added:<', 'added:<9999999999d', 'added:1e999d', '(' * 5000,
                 'published:>=', 'isbn:', 'tag:=', 'a\x00b', 'status:', 'has:']
        for query in nasty:
            with self.subTest(query=query):
                where, params = search.to_sql(query, now=self.now)
                self.library.db.execute(f'SELECT b.id FROM books b WHERE {where}',
                                        params).fetchall()

    def test_plain_words(self):
        self.assertEqual(search.words('harbour "quiet sea" -fog tag:x or lark'),
                         ['harbour', 'quiet sea', 'lark'])


class LibrarySearchTest(unittest.TestCase):

    def test_search_text_follows_edits(self):
        with temporary_library() as library:
            book_id = add_book(library, 'T', ('Ada Lark',))
            library.update_book(book_id, tags=['Lighthouse'])
            self.assertEqual(library.count(query='lighthouse'), 1)
            library.undo()
            self.assertEqual(library.count(query='lighthouse'), 0)


if __name__ == '__main__':
    unittest.main()
