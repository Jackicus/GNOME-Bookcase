# SPDX-License-Identifier: GPL-2.0-or-later
# SPDX-FileCopyrightText: 2026 Jack Tully

"""Highlights to Markdown, and an invented My Clippings.txt in."""

import datetime
import unittest

from tests import ROOT  # noqa: F401
from bookcase import annotations
from tests.support import add_book, temporary_library

CLIPPINGS = """\ufeffThe Quiet Harbour (Lark, Ada)
- Your Highlight on page 12 | Location 180-183 | Added on Monday, 3 March 2025 10:12:01

The lamps along the harbour wall were lit one by one.
==========
The Quiet Harbour (Lark, Ada)
- Your Note on page 12 | Location 183 | Added on Monday, 3 March 2025 10:12:30

Who lit them?
==========
Fog Over Ashby: A Mystery (Ben Ross)
- Your Bookmark on Location 900 | Added on Tuesday, March 4, 2025 9:05:00 PM


==========
Notes (Unattached) (Cara Moss)
- Your Note on Location 40 | Added on Wednesday, 5 March 2025 08:00:00

A note on its own.
==========
Garbage without the second line
==========
The Quiet Harbour (Lark, Ada)
- Highlight Loc. 1180-83 | Added on Thursday, 6 March 2025 12:00:00 AM

Later words.
==========
Unknown Book (Nobody)
- Your Highlight on Location 5-6 | Hinzugefügt am Freitag, 7. März 2025 10:00:00

Text.
==========
"""


class MarkdownTest(unittest.TestCase):

    def test_highlights_and_bookmarks(self):
        with temporary_library() as library:
            book_id = add_book(library, 'The Quiet Harbour', ('Ada Lark', 'Ben Ross'))
            library.add_annotation(book_id, 'highlight', 'c2', text='Second\nline two',
                                   position=0.5, color='green')
            library.add_annotation(book_id, 'highlight', 'c1', text='First', note='Mine',
                                   position=0.125)
            library.add_annotation(book_id, 'bookmark', 'c3', position=0.9)
            text = annotations.to_markdown(library.book(book_id),
                                           library.annotations(book_id))
            self.assertEqual(text, (
                '# The Quiet Harbour\n\nAda Lark and Ben Ross\n\n## Highlights\n\n'
                '> First\n\nMine\n\n*Yellow · 12%*\n\n'
                '> Second\n> line two\n\n*Green · 50%*\n\n'
                '## Bookmarks\n\n- 90%\n'))

    def test_nothing(self):
        with temporary_library() as library:
            book_id = add_book(library, 'Empty', ())
            self.assertEqual(annotations.to_markdown(library.book(book_id), []), '# Empty\n')


class ClippingsTest(unittest.TestCase):

    def test_parse(self):
        clippings = annotations.parse_kindle_clippings(CLIPPINGS)
        self.assertEqual(len(clippings), 5)
        first, bookmark, lone, old, unknown = clippings
        self.assertEqual(first.title, 'The Quiet Harbour')
        self.assertEqual(first.author, 'Lark, Ada')
        self.assertEqual(first.kind, 'highlight')
        self.assertEqual(first.location_text, 'page 12, Location 180-183')
        self.assertEqual(first.location, (180, 183))
        self.assertEqual(first.text, 'The lamps along the harbour wall were lit one by one.')
        self.assertEqual(first.note, 'Who lit them?')
        self.assertEqual(first.added, datetime.datetime(2025, 3, 3, 10, 12, 1).timestamp())
        title, author, kind, location_text, text, note, added = first
        self.assertEqual((kind, note), ('highlight', 'Who lit them?'))

        self.assertEqual((bookmark.kind, bookmark.text, bookmark.location), ('bookmark', '',
                                                                             (900, 900)))
        self.assertEqual(bookmark.title, 'Fog Over Ashby: A Mystery')
        self.assertEqual(bookmark.added, datetime.datetime(2025, 3, 4, 21, 5).timestamp())
        self.assertEqual((lone.kind, lone.title, lone.note, lone.text),
                         ('note', 'Notes (Unattached)', 'A note on its own.', ''))
        self.assertEqual(old.location, (1180, 1183))
        self.assertEqual(old.added, datetime.datetime(2025, 3, 6, 0, 0).timestamp())
        self.assertEqual(unknown.added, 0.0)  # a date in a language not understood

    def test_rubbish(self):
        self.assertEqual(annotations.parse_kindle_clippings(''), [])
        self.assertEqual(annotations.parse_kindle_clippings('=========='), [])
        self.assertEqual(annotations.parse_kindle_clippings('just text\nmore'), [])

    def test_match(self):
        with temporary_library() as library:
            harbour = add_book(library, 'The Quiet Harbour', ('Ada Lark',))
            fog = add_book(library, 'Fog Over Ashby', ('Ben Ross',))
            add_book(library, 'The Quiet Harbour', ('Someone Else',))
            clippings = annotations.parse_kindle_clippings(CLIPPINGS)
            matches = annotations.match_clippings(library, clippings)
            self.assertEqual([match.book_id for match in matches],
                             [harbour, fog, None, harbour, None])
            self.assertEqual(matches[0].clipping, clippings[0])
            self.assertAlmostEqual(matches[0].position, 180 / 1183)
            self.assertEqual(matches[3].position, 1180 / 1183)
            self.assertEqual(matches[1].position, 1.0)


if __name__ == '__main__':
    unittest.main()
