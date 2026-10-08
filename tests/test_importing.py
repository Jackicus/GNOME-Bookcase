# SPDX-License-Identifier: GPL-2.0-or-later
# SPDX-FileCopyrightText: 2026 Jack Tully

"""importing.py: KOReader's partial MD5, library paths, adding (copies, duplicates, merged
formats, broken files, cancelling), scanning a watched folder (new, moved, missing, back)
and the threaded helper. Every book is invented and built here."""

import hashlib
import os
import pathlib
import shutil
import tempfile
import unittest
import zipfile

from gi.repository import GLib

from tests import ROOT  # noqa: F401
from tests.support import make_epub, make_png, temporary_library
from tests.test_formats import make_mobi
from bookcase import importing
from bookcase.covers import CoverStore
from bookcase.formats import BookInfo
from bookcase.importing import Importer, library_path, partial_md5


class TestPartialMd5(unittest.TestCase):

    def setUp(self):
        self.directory = tempfile.mkdtemp(prefix='bookcase-md5-')

    def tearDown(self):
        shutil.rmtree(self.directory, ignore_errors=True)

    def write(self, size):
        path = os.path.join(self.directory, f'{size}.bin')
        data = bytes((i * 7 + i // 251) % 256 for i in range(size))
        pathlib.Path(path).write_bytes(data)
        return path, data

    def test_samples(self):
        # Offsets 0, 1024, 4096, 16384, 65536 (a short last sample), then past the end.
        path, data = self.write(66000)
        expected = hashlib.md5(data[0:1024] + data[1024:2048] + data[4096:5120]
                               + data[16384:17408] + data[65536:66560]).hexdigest()
        self.assertEqual(partial_md5(path), expected)

    def test_known_values(self):
        # Values worked out by hand from KOReader's util.partialMD5 for these contents.
        path, _data = self.write(0)
        self.assertEqual(partial_md5(path), 'd41d8cd98f00b204e9800998ecf8427e')
        path = os.path.join(self.directory, 'small')
        pathlib.Path(path).write_bytes(b'a' * 1500)
        # Samples: 1024 'a's at 0, then 476 'a's at 1024; 4096 is past the end.
        self.assertEqual(partial_md5(path), hashlib.md5(b'a' * 1500).hexdigest())
        path = os.path.join(self.directory, 'exact')
        pathlib.Path(path).write_bytes(b'b' * 1024)
        self.assertEqual(partial_md5(path), hashlib.md5(b'b' * 1024).hexdigest())

    def test_large_offsets(self):
        # A sparse 2 GiB file: the last sample is at 1024 * 4**10 (1 GiB).
        path = os.path.join(self.directory, 'sparse')
        with open(path, 'wb') as file:
            file.seek(1024 * 4 ** 10)
            file.write(b'z' * 1024)
            file.truncate(2 * 1024 ** 3)
        samples = [b'\0' * 1024] * 11 + [b'z' * 1024]  # i = -1 … 9, then i = 10
        self.assertEqual(partial_md5(path), hashlib.md5(b''.join(samples)).hexdigest())


class TestLibraryPath(unittest.TestCase):

    def test_paths(self):
        with tempfile.TemporaryDirectory() as folder:
            info = BookInfo(title='What? A/B: "Story"', authors=['.Ada Lark'])
            path = library_path(folder, info, '.epub')
            self.assertEqual(path, os.path.join(folder, 'Ada Lark', 'What_ A_B_ _Story_.epub'))
            os.makedirs(os.path.dirname(path))
            pathlib.Path(path).touch()
            self.assertTrue(library_path(folder, info, '.epub').endswith('_Story_ (2).epub'))
            nameless = library_path(folder, BookInfo(), '.kepub.epub')
            self.assertTrue(nameless.endswith('.kepub.epub'))
            long = library_path(folder, BookInfo(title='é' * 300, authors=['x']), '.pdf')
            self.assertLessEqual(len(os.path.basename(long).encode()), 130)


class ImporterTestCase(unittest.TestCase):

    def setUp(self):
        self._context = temporary_library()
        self.library = self._context.__enter__()
        self.root = pathlib.Path(self.library.path).parent
        self.covers = CoverStore(self.root, self.library)
        self.books = self.root / 'Books'
        self.source = self.root / 'source'
        self.source.mkdir()
        self.importer = Importer(self.library, self.covers, self.books)

    def tearDown(self):
        self.covers.shutdown()
        self._context.__exit__(None, None, None)


class TestAdd(ImporterTestCase):

    def test_add_copies(self):
        cover = make_png(3, 4)
        source = make_epub(self.source / 'harbour.epub', cover=cover)
        before = source.read_bytes()
        calls = []
        report = self.importer.add([source], progress=lambda *a: calls.append(a))
        self.assertEqual(len(report.added), 1)
        self.assertEqual(calls, [(1, 1, str(source))])
        book = self.library.book(report.added[0])
        self.assertEqual(book.title, 'A Quiet Harbour')
        self.assertEqual(book.source, 'library')
        file = self.library.files(book.id)[0]
        self.assertEqual(file.path, str(self.books / 'Ada Lark' / 'A Quiet Harbour.epub'))
        self.assertEqual(file.hash, partial_md5(source))
        self.assertEqual(pathlib.Path(file.path).read_bytes(), before)
        self.assertEqual(source.read_bytes(), before)
        self.assertTrue(book.has_cover)
        self.assertEqual(pathlib.Path(self.covers.path(book)).read_bytes(), cover)
        self.assertIn('library', [folder.kind for folder in self.library.folders()])

        again = self.importer.add([source])
        self.assertEqual(again.added, [])
        self.assertEqual(again.duplicates, [(str(source), book.id)])

    def test_merge_and_same_format_duplicate(self):
        epub_report = self.importer.add([make_epub(self.source / 'a.epub')])
        book_id = epub_report.added[0]
        mobi = make_mobi(self.source / 'a.mobi', title='A Quiet Harbour')
        report = self.importer.add([mobi])
        self.assertEqual(report.merged, [book_id])
        self.assertEqual(set(self.library.book(book_id).formats), {'epub', 'mobi'})
        mobi_file = [f for f in self.library.files(book_id) if f.format == 'mobi'][0]
        self.assertEqual(mobi_file.path, str(self.books / 'Ada Lark' / 'A Quiet Harbour.mobi'))
        # Another EPUB of the same book (different bytes): already there in that format.
        other = make_epub(self.source / 'b.epub', description='Another edition.')
        report = self.importer.add([other])
        self.assertEqual(report.duplicates, [(str(other), book_id)])
        self.assertEqual(report.added, [])

    def test_folders_broken_files_and_in_place(self):
        make_epub(self.source / 'one.epub', title='One')
        (self.source / 'sub').mkdir()
        make_epub(self.source / 'sub' / 'two.epub', title='Two')
        (self.source / 'broken.epub').write_bytes(b'not a zip')
        (self.source / 'notes.doc').write_bytes(b'ignored')
        (self.source / '.hidden.epub').write_bytes(b'ignored')
        report = self.importer.add([self.source], copy=False)
        self.assertEqual(len(report.added), 2)
        self.assertEqual([path for path, _message in report.failed],
                         [str(self.source / 'broken.epub')])
        paths = {self.library.files(i)[0].path for i in report.added}
        self.assertEqual(paths, {str(self.source / 'one.epub'),
                                 str(self.source / 'sub' / 'two.epub')})
        self.assertFalse(self.books.exists())
        self.assertIn('could not be read', importing.describe(report))

    @unittest.skipUnless(shutil.which('bsdtar'), 'needs bsdtar')
    def test_cbr_becomes_cbz(self):
        # bsdtar cannot write RAR; a zip named .cbr goes through the same conversion.
        path = self.source / 'Night Ferry.cbr'
        with zipfile.ZipFile(path, 'w') as archive:
            archive.writestr('01.png', make_png(2, 3))
        report = self.importer.add([path])
        file = self.library.files(report.added[0])[0]
        self.assertEqual(file.format, 'cbz')
        self.assertTrue(file.path.endswith('Night Ferry.cbz'))
        self.assertEqual(file.hash, partial_md5(file.path))
        self.assertEqual(self.importer.add([path]).duplicates[0][1], report.added[0])

    def test_cancel(self):
        paths = [make_epub(self.source / f'{n}.epub', title=f'Book {n}') for n in range(3)]
        seen = []
        report = self.importer.add(paths, progress=lambda *a: seen.append(a),
                                   cancelled=lambda: len(seen) >= 1)
        self.assertTrue(report.cancelled)
        self.assertEqual(len(report.added), 1)

    def test_add_async(self):
        paths = [make_epub(self.source / f'{n}.epub', title=f'Book {n}') for n in range(2)]
        loop = GLib.MainLoop()
        progress, result, changed = [], [], []
        self.library.connect('changed', lambda _library, kind: changed.append(kind))

        def done(report):
            result.append(report)
            loop.quit()

        self.importer.add_async(paths, progress=lambda *a: progress.append(a), done=done)
        GLib.timeout_add_seconds(20, loop.quit)
        loop.run()
        self.assertEqual(len(result[0].added), 2)
        self.assertEqual([p[:2] for p in progress], [(1, 2), (2, 2)])
        self.assertIn('books', changed)
        self.assertEqual(self.library.count(), 2)
        self.assertTrue(all(self.library.book(i).has_cover is False for i in result[0].added))


class TestScan(ImporterTestCase):

    def test_scan(self):
        watched = self.root / 'watched'
        (watched / 'deep').mkdir(parents=True)
        one = make_epub(watched / 'one.epub', title='One')
        two = make_epub(watched / 'two.epub', title='Two')
        report = self.importer.scan(watched)
        self.assertEqual(len(report.added), 2)
        self.assertEqual({self.library.book(i).source for i in report.added}, {'watched'})
        self.assertEqual(self.importer.scan(watched).added, [])

        moved = watched / 'deep' / 'one.epub'
        one.rename(moved)
        two_bytes = two.read_bytes()
        two.unlink()
        report = self.importer.scan(watched)
        self.assertEqual(report.added, [])
        self.assertEqual(len(report.moved), 1)
        self.assertEqual(len(report.missing), 1)
        self.assertEqual(self.library.find_file(moved).missing, False)
        self.assertTrue(self.library.find_file(two).missing)

        two.write_bytes(two_bytes)
        report = self.importer.scan(watched)
        self.assertFalse(self.library.find_file(two).missing)
        self.assertEqual(report.missing, [])

    def test_moved_from_elsewhere(self):
        report = self.importer.add([make_epub(self.source / 'x.epub', title='Moving')],
                                   copy=False)
        book_id = report.added[0]
        watched = self.root / 'watched'
        watched.mkdir()
        (self.source / 'x.epub').rename(watched / 'x.epub')
        report = self.importer.scan(watched)
        self.assertEqual(report.added, [])
        self.assertEqual(self.library.files(book_id)[0].path, str(watched / 'x.epub'))


class TestOpenInPlace(ImporterTestCase):

    def test_open_then_add(self):
        source = make_epub(self.source / 'harbour.epub', cover=make_png(3, 4))
        before = source.read_bytes()
        book_id = self.importer.open_in_place(source)
        book = self.library.book(book_id)
        self.assertEqual(book.source, 'opened')
        self.assertTrue(book.has_cover)
        self.assertFalse(self.library.can_undo())
        self.assertEqual(self.library.count(), 0)
        self.assertEqual(self.library.files(book_id)[0].path, str(source))
        self.assertEqual(self.importer.open_in_place(source), book_id)  # the same again
        self.assertFalse(self.books.exists())  # nothing copied

        self.library.set_progress(book_id, 0.25, 'epubcfi(/6/2)')
        report = self.importer.add([source])  # Add to Library: copied in
        self.assertEqual(report.added, [book_id])
        book = self.library.book(book_id)
        self.assertEqual((book.source, book.progress), ('library', 0.25))
        path = self.library.files(book_id)[0].path
        self.assertTrue(path.startswith(str(self.books)))
        self.assertEqual(source.read_bytes(), before)
        self.assertEqual(self.library.count(), 1)

    def test_open_a_book_in_the_library(self):
        source = make_epub(self.source / 'harbour.epub')
        report = self.importer.add([source])
        self.assertEqual(self.importer.open_in_place(source), report.added[0])

    def test_open_then_watch(self):
        watched = self.root / 'watched'
        watched.mkdir()
        source = make_epub(watched / 'harbour.epub')
        book_id = self.importer.open_in_place(source)
        report = self.importer.scan(watched)
        self.assertEqual(report.added, [book_id])
        self.assertEqual(self.library.book(book_id).source, 'watched')

    def test_not_a_book(self):
        path = self.source / 'notes.odt'
        path.write_bytes(b'x')
        with self.assertRaises(importing.FormatError):
            self.importer.open_in_place(path)


if __name__ == '__main__':
    unittest.main()
