# SPDX-License-Identifier: GPL-2.0-or-later
# SPDX-FileCopyrightText: 2026 Jack Tully

"""covers.py: versioned cover files, undo putting the old cover back, removing, pruning,
and thumbnails (made now, and in the pool with the callback on the main loop)."""

import os
import pathlib
import struct
import unittest
import zlib

from gi.repository import GLib

from tests import ROOT  # noqa: F401
from tests.support import add_book, make_png, temporary_library
from bookcase.covers import CoverStore

JPEG = b'\xff\xd8\xff\xe0' + bytes(range(32))


def png_size(path):
    return struct.unpack('>II', pathlib.Path(path).read_bytes()[16:24])


class CoversTestCase(unittest.TestCase):

    def setUp(self):
        self._context = temporary_library()
        self.library = self._context.__enter__()
        self.root = pathlib.Path(self.library.path).parent
        self.covers = CoverStore(self.root, self.library)
        self.book_id = add_book(self.library, 'A Quiet Harbour')

    def tearDown(self):
        self.covers.shutdown()
        self._context.__exit__(None, None, None)


class TestStore(CoversTestCase):

    def test_save_undo_remove(self):
        self.assertIsNone(self.covers.path(self.book_id))
        first = make_png(4, 6)
        path = self.covers.save(self.book_id, first)
        book = self.library.book(self.book_id)
        self.assertTrue(book.has_cover)
        self.assertEqual(path, self.covers.path(book))
        self.assertTrue(path.endswith(f'{self.book_id}-{book.cover_version}.png'))
        self.assertEqual(self.covers.data(book), first)

        second = self.covers.save(self.book_id, JPEG)
        self.assertTrue(second.endswith('.jpg'))
        self.assertEqual(self.covers.data(self.book_id), JPEG)
        self.assertEqual(self.library.undo(), 'Set Cover')
        self.assertEqual(self.covers.data(self.book_id), first)

        self.covers.remove(self.book_id)
        self.assertIsNone(self.covers.path(self.book_id))
        self.assertEqual(self.library.undo(), 'Remove Cover')
        self.assertEqual(self.covers.data(self.book_id), first)

        with self.assertRaises(ValueError):
            self.covers.save(self.book_id, b'not an image')

    def test_prune(self):
        self.covers.save(self.book_id, make_png(2, 2))
        self.covers.thumbnail_path(self.book_id, 1)
        self.covers.save(self.book_id, make_png(3, 3))
        self.covers.thumbnail_path(self.book_id, 1)
        self.covers.prune()
        names = os.listdir(self.root / 'covers')
        version = self.library.book(self.book_id).cover_version
        self.assertEqual(names, [f'{self.book_id}-{version}.png'])
        self.assertEqual(os.listdir(self.root / 'thumbnails' / '1'),
                         [f'{self.book_id}-{version}.png'])


class TestThumbnails(CoversTestCase):

    def test_thumbnail_path(self):
        self.assertIsNone(self.covers.thumbnail_path(self.book_id, 100))
        self.covers.save(self.book_id, make_png(40, 60))
        path = self.covers.thumbnail_path(self.book_id, 20)
        book = self.library.book(self.book_id)
        self.assertEqual(path, str(self.root / 'thumbnails' / '20'
                                   / f'{self.book_id}-{book.cover_version}.png'))
        self.assertEqual(png_size(path), (20, 30))
        # Never wider than the cover.
        self.assertEqual(png_size(self.covers.thumbnail_path(self.book_id, 400)), (40, 60))

    def test_a_huge_cover_gets_no_thumbnail(self):
        # A valid one-bit PNG of 20000 x 20000 pixels: 6 MB of zeros to zlib, 1.6 GB once
        # decoded. Its header is enough to refuse it.
        def chunk(kind, data):
            body = kind + data
            return struct.pack('>I', len(data)) + body + struct.pack('>I', zlib.crc32(body))

        side = 20000
        row = b'\x00' + bytes((side + 7) // 8)
        bomb = (b'\x89PNG\r\n\x1a\n'
                + chunk(b'IHDR', struct.pack('>IIBBBBB', side, side, 1, 0, 0, 0, 0))
                + chunk(b'IDAT', zlib.compress(row * side, 9)) + chunk(b'IEND', b''))
        self.assertLess(len(bomb), 1_000_000)
        self.covers.save(self.book_id, bomb)
        self.assertIsNone(self.covers.thumbnail_path(self.book_id, 100))
        self.assertFalse((self.root / 'thumbnails' / '100').exists())

    def test_a_thumbnail_made_after_the_store_is_gone_is_quiet(self):
        # A pool thread still making a thumbnail when the data folder is removed (a test's
        # teardown, the app's shutdown): no error, no traceback, the folder not made again.
        self.covers.save(self.book_id, make_png(40, 60))
        book = self.library.book(self.book_id)
        source = self.covers.path(book)
        directory = self.root / 'gone'
        store = CoverStore(directory, self.library)
        store.path = lambda _book: source  # the cover read before the folder went
        os.rmdir(store.covers_dir)
        os.rmdir(directory)
        with self.assertLogs('bookcase.covers', 'DEBUG') as logs:
            store._make_thumbnail(book, 20, (book.id, book.cover_version, 20))
        self.assertFalse(directory.exists())
        self.assertEqual([record.levelname for record in logs.records], ['DEBUG'])

    def test_load_thumbnail(self):
        self.covers.save(self.book_id, make_png(40, 60))
        book = self.library.book(self.book_id)
        loop = GLib.MainLoop()
        results = []

        def callback(path):
            results.append(path)
            if len(results) == 2:
                loop.quit()

        self.covers.load_thumbnail(book, 10, callback)
        self.covers.load_thumbnail(book, 10, callback)
        self.assertEqual(results, [])  # made in the pool, delivered on the main loop
        GLib.timeout_add_seconds(10, loop.quit)
        loop.run()
        self.assertEqual(len(results), 2)
        self.assertEqual(results[0], results[1])
        self.assertEqual(png_size(results[0]), (10, 15))
        # Cached: at once.
        self.covers.load_thumbnail(book, 10, results.append)
        self.assertEqual(len(results), 3)
        # No cover: None at once.
        other = add_book(self.library, 'Bare')
        self.covers.load_thumbnail(other, 10, results.append)
        self.assertIsNone(results[-1])


if __name__ == '__main__':
    unittest.main()
