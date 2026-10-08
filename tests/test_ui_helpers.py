# SPDX-License-Identifier: GPL-2.0-or-later
# SPDX-FileCopyrightText: 2026 Jack Tully

"""The library window's pure parts: the sidebar's model, the drawn covers' colours, how
ratings, progress and times read, and which dropped or opened files are books."""

import os
import pathlib
import shutil
import tempfile
import unittest

from tests import ROOT  # noqa: F401  (registers bookcase)
from tests.support import add_book, make_epub, temporary_library


class SidebarModelTest(unittest.TestCase):
    def test_sections_follow_the_library(self):
        from bookcase.sidebar import build_sections

        with temporary_library() as library:
            first = add_book(library, 'A Quiet Harbour')
            add_book(library, 'Lantern Hill', ('Ben Ross',))
            library.set_status([first], 'reading')
            sections = build_sections(library)
            self.assertEqual([section.key for section in sections],
                             ['main', 'browse', 'reading'])
            entries = {entry.key: entry for section in sections for entry in section.entries}
            self.assertEqual(entries['all'].count, 2)
            self.assertEqual(entries['status:reading'].count, 1)
            self.assertEqual(entries['status:unread'].count, 1)
            self.assertEqual(entries['status:finished'].count, 0)
            self.assertIsNone(entries['home'].count)

            shelf = library.add_shelf('Holiday')
            library.add_to_shelf(shelf, [first])
            smart = library.add_shelf('Ross', query='author:Ross')
            sections = build_sections(library)
            shelves = sections[-1]
            self.assertEqual(shelves.key, 'shelves')
            self.assertEqual([(e.key, e.count) for e in shelves.entries],
                             [(f'shelf:{shelf}', 1), (f'shelf:{smart}', 1)])
            self.assertNotEqual(shelves.entries[0].icon, shelves.entries[1].icon)
            self.assertIn('author:Ross', shelves.entries[1].tooltip)

    def test_devices_section_only_while_connected(self):
        from bookcase.sidebar import build_sections

        class Device:
            id = 'kobo-1'
            name = 'Kobo Libra'
            kind = 'kobo'

        with temporary_library() as library:
            sections = build_sections(library, [Device()])
            self.assertEqual(sections[-1].key, 'devices')
            self.assertEqual(sections[-1].entries[0].key, 'device:kobo-1')
            self.assertEqual(sections[-1].entries[0].title, 'Kobo Libra')
            self.assertNotIn('devices', [s.key for s in build_sections(library, [])])


class CoverTest(unittest.TestCase):
    def test_placeholder_colour_is_stable_and_from_the_palette(self):
        from bookcase.widgets.cover import PALETTE, cover_height, placeholder_colour

        self.assertEqual(placeholder_colour('A Quiet Harbour'),
                         placeholder_colour('  a quiet   HARBOUR '))
        self.assertIn(placeholder_colour(''), PALETTE)
        colours = {placeholder_colour(f'Book {n}') for n in range(60)}
        self.assertGreater(len(colours), len(PALETTE) // 2)
        self.assertEqual(cover_height(150), 225)


class RatingTextTest(unittest.TestCase):
    def test_stars(self):
        from bookcase.widgets.rating import EMPTY, HALF, STAR, rating_text, star_icons

        self.assertEqual(star_icons(0), [EMPTY] * 5)
        self.assertEqual(star_icons(7), [STAR, STAR, STAR, HALF, EMPTY])
        self.assertEqual(star_icons(10), [STAR] * 5)
        self.assertEqual(star_icons(42), [STAR] * 5)
        self.assertEqual(rating_text(0), 'No rating')
        self.assertEqual(rating_text(2), '1 star')
        self.assertEqual(rating_text(7), '3.5 stars')
        self.assertEqual(rating_text(8), '4 stars')


class ProgressTextTest(unittest.TestCase):
    def test_progress_and_time_left(self):
        from bookcase.library import Book
        from bookcase.widgets.book_tile import duration_text, format_index, progress_text

        book = Book(id=1, uuid='u', title='T', sort_title='t', status='reading', progress=0.42)
        self.assertEqual(progress_text(book), '42%')
        self.assertEqual(progress_text(book, 250), '42% · 4 h 10 min left')
        self.assertEqual(progress_text(Book(id=2, uuid='v', title='T', sort_title='t',
                                            status='finished')), 'Finished')
        self.assertEqual(progress_text(Book(id=3, uuid='w', title='T', sort_title='t')), 'New')
        self.assertEqual(progress_text(Book(id=4, uuid='x', title='T', sort_title='t',
                                            status='reading', progress=0.001)), '1%')
        self.assertEqual(duration_text(0.2), '1 min')
        self.assertEqual(duration_text(45), '45 min')
        self.assertEqual(duration_text(60), '1 h')
        self.assertEqual(duration_text(150), '2 h 30 min')
        self.assertEqual(duration_text(60 * 12 + 40), '12 h')
        self.assertEqual(format_index(2.0), '2')
        self.assertEqual(format_index(2.5), '2.5')
        self.assertEqual(format_index(0), '')


class BookFilesTest(unittest.TestCase):
    def test_books_among_files_and_folders(self):
        from gi.repository import Gio

        from bookcase.main import book_files

        directory = pathlib.Path(tempfile.mkdtemp(prefix='bookcase-test-files-'))
        self.addCleanup(shutil.rmtree, directory, True)
        (directory / 'shelf' / '.hidden').mkdir(parents=True)
        one = make_epub(directory / 'one.epub')
        two = make_epub(directory / 'shelf' / 'two.epub', title='Two')
        make_epub(directory / 'shelf' / '.hidden' / 'three.epub', title='Three')
        (directory / 'notes.odt').write_bytes(b'not a book')
        files = [Gio.File.new_for_path(str(path))
                 for path in (one, directory / 'shelf', directory / 'notes.odt')]
        paths, refused = book_files(files)
        self.assertEqual(paths, [str(one), str(two)])
        self.assertEqual(refused, ['notes.odt'])
        self.assertTrue(all(os.path.isabs(path) for path in paths))


if __name__ == '__main__':
    unittest.main()
