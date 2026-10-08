# SPDX-License-Identifier: GPL-2.0-or-later
# SPDX-FileCopyrightText: 2026 Jack Tully

"""existing_books.py: finding Calibre libraries and folders of books in an invented home."""

import json
import os
import pathlib
import shutil
import sqlite3
import tempfile
import time
import unittest
from unittest import mock

from tests import ROOT  # noqa: F401  (registers bookcase)

from bookcase import existing_books


def touch(path):
    path = pathlib.Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(b'x')


def make_calibre(folder, books=3):
    folder = pathlib.Path(folder)
    folder.mkdir(parents=True, exist_ok=True)
    db = sqlite3.connect(folder / 'metadata.db')
    db.execute('CREATE TABLE books (id INTEGER PRIMARY KEY, title TEXT)')
    db.executemany('INSERT INTO books (title) VALUES (?)', [(f'Book {n}',) for n in range(books)])
    db.commit()
    db.close()
    touch(folder / 'Ada Lark' / 'A Quiet Harbour (1)' / 'A Quiet Harbour - Ada Lark.epub')


class DiscoverTest(unittest.TestCase):

    def setUp(self):
        self.home = pathlib.Path(tempfile.mkdtemp(prefix='bookcase-home-'))
        self.addCleanup(shutil.rmtree, self.home, ignore_errors=True)
        patcher = mock.patch.dict(os.environ, {'BOOKCASE_HOME_HINTS': str(self.home)})
        patcher.start()
        self.addCleanup(patcher.stop)

    def test_nothing_in_an_empty_home(self):
        self.assertEqual(existing_books.find(), [])

    def test_calibre_libraries_in_the_usual_places_and_the_configured_one(self):
        make_calibre(self.home / 'Calibre Library', books=12)
        make_calibre(self.home / 'Shelves' / 'Elsewhere', books=2)
        config = self.home / '.config' / 'calibre'
        config.mkdir(parents=True)
        (config / 'global.py.json').write_text(json.dumps(
            {'library_path': str(self.home / 'Shelves' / 'Elsewhere')}))
        sources = existing_books.find()
        self.assertEqual([(s.kind, os.path.basename(s.path), s.count) for s in sources],
                         [('calibre', 'Elsewhere', 2), ('calibre', 'Calibre Library', 12)])

    def test_a_broken_configuration_is_ignored(self):
        config = self.home / '.config' / 'calibre'
        config.mkdir(parents=True)
        (config / 'global.py.json').write_text('{not json')
        make_calibre(self.home / 'calibre', books=1)
        self.assertEqual([s.kind for s in existing_books.find()], ['calibre'])

    def test_unreadable_calibre_count_is_none(self):
        folder = self.home / 'Calibre Library'
        folder.mkdir()
        (folder / 'metadata.db').write_bytes(b'not a database at all, not even close......')
        sources = existing_books.find()
        self.assertEqual(len(sources), 1)
        self.assertIsNone(sources[0].count)

    def test_folders_with_books(self):
        for name in ('a.epub', 'b.pdf', 'deep/er/c.cbz', 'notes.txt', '.hidden/d.epub'):
            touch(self.home / 'Books' / name)
        for name in ('e.epub', 'invoice.pdf', 'readme.txt', 'f.mobi', 'photo.jpg'):
            touch(self.home / 'Downloads' / name)
        touch(self.home / 'Documents' / 'letter.odt')
        sources = existing_books.find()
        by_name = {os.path.basename(s.path): s for s in sources}
        self.assertEqual(set(by_name), {'Books', 'Downloads'})
        # A folder of books counts PDFs and text; Downloads only e-book formats.
        self.assertEqual(by_name['Books'].count, 4)
        self.assertEqual(by_name['Books'].kind, 'folder')
        self.assertEqual(by_name['Downloads'].count, 2)
        self.assertEqual(by_name['Downloads'].kind, 'files')
        self.assertEqual(sorted(os.path.basename(p) for p in by_name['Downloads'].paths),
                         ['e.epub', 'f.mobi'])
        self.assertEqual([s.path for s in sources][0], str(self.home / 'Books'))

    def test_nested_places_and_calibre_libraries_are_counted_once(self):
        touch(self.home / 'Documents' / 'Books' / 'a.epub')
        touch(self.home / 'Documents' / 'Books' / 'b.epub')
        touch(self.home / 'Documents' / 'c.epub')
        make_calibre(self.home / 'Documents' / 'Calibre Library')
        sources = existing_books.find()
        counts = {(s.kind, os.path.relpath(s.path, self.home)): s.count for s in sources}
        self.assertEqual(counts, {('calibre', 'Documents/Calibre Library'): 3,
                                  ('folder', 'Documents/Books'): 2,
                                  ('files', 'Documents'): 1})

    def test_depth_and_deadline_limit_the_count(self):
        touch(self.home / 'Books' / '1' / '2' / '3' / 'deep.epub')
        touch(self.home / 'Books' / '1' / '2' / '3' / '4' / 'deeper.epub')
        counted = existing_books.count_books(str(self.home / 'Books'), max_depth=3)
        self.assertEqual(counted.count, 1)
        self.assertFalse(counted.complete)
        for n in range(300):
            touch(self.home / 'Many' / f'{n}.epub')
        late = existing_books.count_books(str(self.home / 'Many'), deadline=time.monotonic() - 1)
        self.assertFalse(late.complete)
        self.assertLess(late.count, 300)

    def test_symbolic_links_are_not_followed(self):
        touch(self.home / 'Elsewhere' / 'a.epub')
        (self.home / 'Books').mkdir()
        os.symlink(self.home / 'Elsewhere', self.home / 'Books' / 'link')
        self.assertEqual(existing_books.count_books(str(self.home / 'Books')).count, 0)


if __name__ == '__main__':
    unittest.main()
