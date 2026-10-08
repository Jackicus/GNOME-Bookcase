# SPDX-License-Identifier: GPL-2.0-or-later
# SPDX-FileCopyrightText: 2026 Jack Tully

from tests import ROOT  # noqa: F401  (registers src/ as bookcase)

import os
import pathlib
import shutil
import tempfile
import unittest
import zipfile
from unittest import mock

from bookcase import devices, kepub
from bookcase.formats import BookInfo
from tests.support import make_epub, temporary_library


class FakeCovers:
    """A cover store with no covers."""

    def __getattr__(self, name):
        return lambda *args, **kwargs: None


class TreeTest(unittest.TestCase):
    def setUp(self):
        self.directory = pathlib.Path(tempfile.mkdtemp(prefix='bookcase-test-device-'))
        self.addCleanup(shutil.rmtree, self.directory, True)

    def tree(self, kind, **kwargs):
        return str(devices.make_test_tree(self.directory / kind, kind, **kwargs))


class DetectTest(TreeTest):
    def test_kobo(self):
        root = self.tree('kobo', model=388)
        kind, name, books_dir = devices.detect(root, 'KOBOeReader')
        self.assertEqual((kind, name), ('kobo', 'Kobo Libra 2'))
        self.assertEqual(books_dir, os.path.join(root, 'Bookcase'))

    def test_kobo_of_unknown_model(self):
        root = self.tree('kobo', model=999)
        self.assertEqual(devices.detect(root, 'KOBOeReader')[1], 'Kobo eReader')
        self.assertEqual(devices.detect(root, 'My Reader')[1], 'My Reader')

    def test_kobo_by_its_database(self):
        root = self.directory / 'other'
        (root / '.kobo').mkdir(parents=True)
        (root / '.kobo' / 'KoboReader.sqlite').write_bytes(b'')
        self.assertEqual(devices.detect(str(root))[0], 'kobo')

    def test_kindle(self):
        root = self.tree('kindle')
        kind, name, books_dir = devices.detect(root, 'Kindle')
        self.assertEqual((kind, name), ('kindle', 'Kindle'))
        self.assertEqual(books_dir, os.path.join(root, 'documents'))

    def test_generic_only_when_removable(self):
        root = self.tree('generic')
        kind, name, books_dir = devices.detect(root, 'PB632')
        self.assertEqual((kind, name, books_dir), ('generic', 'PB632',
                                                   os.path.join(root, 'Books')))
        self.assertIsNone(devices.detect(root, 'Disk', removable=False))

    def test_nothing(self):
        (self.directory / 'Photos').mkdir()
        self.assertIsNone(devices.detect(str(self.directory)))

    def test_safe_name(self):
        self.assertEqual(devices.safe_name('What? A: "Tale"/2'), 'What_ A_ _Tale__2')
        self.assertFalse(devices.safe_name('  ...  ').startswith('.'))
        self.assertTrue(devices.safe_name(''))
        self.assertLessEqual(len(devices.safe_name('x' * 300)), 80)

    def test_destinations(self):
        kobo = devices.Device(self.tree('kobo'), 'kobo', 'Kobo', None)
        kobo.books_dir = os.path.join(kobo.root, 'Bookcase')
        self.assertEqual(kobo.destination('A Quiet Harbour', 'Ada Lark', 'kepub'),
                         os.path.join(kobo.root, 'Bookcase', 'Ada Lark',
                                      'A Quiet Harbour.kepub.epub'))
        kindle = devices.Device(self.tree('kindle'), 'kindle', 'Kindle', None)
        kindle.books_dir = os.path.join(kindle.root, 'documents')
        self.assertEqual(kindle.destination('A Quiet Harbour', 'Ada Lark', 'azw3'),
                         os.path.join(kindle.root, 'documents',
                                      'A Quiet Harbour - Ada Lark.azw3'))


class PlanTest(TreeTest):
    def device(self, kind):
        root = self.tree(kind)
        return devices.Device(root, kind, kind, root)

    def test_kobo(self):
        kobo = self.device('kobo')
        plan = kobo.plan(['epub', 'pdf'])
        self.assertEqual((plan.source, plan.target, plan.convert), ('epub', 'kepub', 'kepub'))
        self.assertEqual(kobo.plan(['epub'], kepub=False).target, 'epub')
        self.assertEqual(kobo.plan(['kepub', 'epub']).target, 'kepub')
        self.assertEqual(kobo.plan(['kepub', 'epub'], kepub=False).target, 'epub')
        self.assertEqual(kobo.plan(['pdf']).target, 'pdf')
        self.assertIsNone(kobo.plan(['azw3']))

    def test_kindle(self):
        kindle = self.device('kindle')
        self.assertEqual(kindle.plan(['epub', 'mobi']).target, 'mobi')
        self.assertEqual(kindle.plan(['azw3', 'mobi']).target, 'azw3')
        with mock.patch.object(devices, 'ebook_convert', return_value=None):
            self.assertIsNone(kindle.plan(['epub']))
            self.assertIn('Send to Kindle', kindle.why_not(['epub']))
        with mock.patch.object(devices, 'ebook_convert', return_value='/bin/ebook-convert'):
            plan = kindle.plan(['epub'])
            self.assertEqual((plan.target, plan.convert), ('azw3', 'ebook-convert'))

    def test_generic(self):
        reader = self.device('generic')
        self.assertEqual(reader.plan(['pdf', 'epub']).target, 'epub')
        self.assertEqual(reader.plan(['fb2']).target, 'fb2')


class BooksTest(TreeTest):
    def setUp(self):
        super().setUp()
        root = self.tree('kobo')
        self.device = devices.Device(root, 'kobo', 'Kobo', os.path.join(root, 'Bookcase'))

    def test_list_books_walks_and_skips_hidden(self):
        make_epub(pathlib.Path(self.device.root) / 'Loose Book.epub', title='Loose Book',
                  authors=('Ben Ross',))
        os.makedirs(os.path.join(self.device.books_dir, 'Ada Lark'))
        make_epub(os.path.join(self.device.books_dir, 'Ada Lark', 'Harbour.kepub.epub'),
                  title='A Quiet Harbour')
        make_epub(os.path.join(self.device.root, '.kobo', 'hidden.epub'))
        (pathlib.Path(self.device.root) / 'notes.doc').write_text('not a book')
        books = self.device.list_books()
        self.assertEqual([(book.title, book.format) for book in books],
                         [('A Quiet Harbour', 'kepub'), ('Loose Book', 'epub')])
        self.assertEqual(books[1].authors, ('Ben Ross',))
        self.assertEqual(len(books[0].hash), 32)
        self.assertGreater(books[0].size, 0)
        self.assertEqual(self.device.list_books(), books)  # the cache answers the same

    def test_remove_cleans_empty_folders(self):
        folder = os.path.join(self.device.books_dir, 'Ada Lark')
        os.makedirs(folder)
        path = make_epub(os.path.join(folder, 'Harbour.epub'))
        self.device.remove(path)
        self.assertFalse(os.path.exists(path))
        self.assertFalse(os.path.exists(folder))
        self.assertTrue(os.path.isdir(self.device.root))

    def test_remove_refuses_paths_off_the_device(self):
        outside = make_epub(self.directory / 'outside.epub')
        with self.assertRaises(devices.DeviceError):
            self.device.remove(outside)
        self.assertTrue(os.path.exists(outside))

    def test_kindle_remove_takes_its_sdr_folder(self):
        root = self.tree('kindle')
        kindle = devices.Device(root, 'kindle', 'Kindle', os.path.join(root, 'documents'))
        book = pathlib.Path(root) / 'documents' / 'Harbour - Ada Lark.azw3'
        book.write_bytes(b'x')
        (pathlib.Path(root) / 'documents' / 'Harbour - Ada Lark.sdr').mkdir()
        kindle.remove(str(book))
        self.assertEqual(os.listdir(os.path.join(root, 'documents')), [])


class MonitorTest(TreeTest):
    def test_test_root_from_the_environment(self):
        kobo = self.tree('kobo')
        kindle = self.tree('kindle')
        with mock.patch.dict(os.environ, {'BOOKCASE_TEST_DEVICE': f'{kobo}:{kindle}'}):
            monitor = devices.DeviceMonitor(watch_mounts=False)
        found = monitor.devices()
        self.assertEqual([device.kind for device in found], ['kobo', 'kindle'])
        self.assertIs(monitor.device(found[0].id), found[0])
        self.assertEqual(found[0].id, devices.device_id(kobo))

    def test_add_and_remove_signals(self):
        monitor = devices.DeviceMonitor(watch_mounts=False)
        added, removed = [], []
        monitor.connect('added', lambda _monitor, device: added.append(device))
        monitor.connect('removed', lambda _monitor, device_id: removed.append(device_id))
        device = monitor.add_test_root(str(self.directory), name='Test Reader')
        self.assertEqual((device.kind, device.name), ('generic', 'Test Reader'))
        self.assertEqual(device.books_dir, str(self.directory))
        monitor.add_test_root(str(self.directory))  # once only
        self.assertEqual(added, [device])
        monitor.remove_test_root(str(self.directory))
        self.assertEqual(removed, [device.id])
        self.assertEqual(monitor.devices(), [])

    def test_space(self):
        device = devices.Device(str(self.directory), 'generic', 'X', str(self.directory))
        free, total = device.space()
        self.assertGreater(total, 0)
        self.assertLessEqual(free, total)


class SendTest(TreeTest):
    def setUp(self):
        super().setUp()
        self.library = self.enterContext(temporary_library())
        source = make_epub(self.directory / 'harbour.epub', title='A Quiet Harbour',
                           authors=('Ada Lark',))
        from bookcase import importing

        info = BookInfo(title='A Quiet Harbour', authors=['Ada Lark'], format='epub')
        self.book_id = self.library.add_book(info, str(source),
                                             hash=importing.partial_md5(str(source)),
                                             size=os.path.getsize(source))
        self.source = source

    def test_send_to_kobo_as_kepub_and_match(self):
        root = self.tree('kobo')
        kobo = devices.Device(root, 'kobo', 'Kobo', os.path.join(root, 'Bookcase'))
        fractions = []
        path = kobo.send(self.library, FakeCovers(), self.book_id, progress=fractions.append)
        self.assertEqual(path, os.path.join(root, 'Bookcase', 'Ada Lark',
                                            'A Quiet Harbour.kepub.epub'))
        self.assertTrue(kepub.is_kepub(path))
        with zipfile.ZipFile(path) as archive:
            self.assertEqual(archive.infolist()[0].filename, 'mimetype')
        self.assertEqual(fractions[0], 0.0)
        self.assertEqual(fractions[-1], 1.0)
        self.assertEqual(fractions, sorted(fractions))
        self.assertEqual([name for name in os.listdir(os.path.dirname(path))],
                         ['A Quiet Harbour.kepub.epub'])  # no partial file left
        books = kobo.list_books()
        self.assertEqual(kobo.match(self.library, books), {path: self.book_id})
        self.assertEqual(kobo.book_ids, {self.book_id})

    def test_exact_copy_matches_by_hash(self):
        root = self.tree('generic')
        reader = devices.Device(root, 'generic', 'Reader', os.path.join(root, 'Books'))
        copy = os.path.join(reader.books_dir, 'renamed.epub')
        shutil.copyfile(self.source, copy)
        with mock.patch.object(self.library, 'find_similar', return_value=[]):
            self.assertEqual(reader.match(self.library, reader.list_books()),
                             {copy: self.book_id})

    def test_unknown_book_does_not_match(self):
        root = self.tree('generic')
        reader = devices.Device(root, 'generic', 'Reader', os.path.join(root, 'Books'))
        other = make_epub(os.path.join(reader.books_dir, 'other.epub'), title='Salt Roads',
                          authors=('Cy Moor',))
        self.assertEqual(reader.match(self.library, reader.list_books()), {str(other): None})

    def test_send_without_a_readable_format(self):
        root = self.tree('kindle')
        kindle = devices.Device(root, 'kindle', 'Kindle', os.path.join(root, 'documents'))
        with mock.patch.object(devices, 'ebook_convert', return_value=None):
            with self.assertRaises(devices.DeviceError):
                kindle.send(self.library, FakeCovers(), self.book_id)

    def test_cancel(self):
        root = self.tree('generic')
        reader = devices.Device(root, 'generic', 'Reader', os.path.join(root, 'Books'))
        from gi.repository import Gio

        cancellable = Gio.Cancellable()
        cancellable.cancel()
        with self.assertRaises(devices.Cancelled):
            reader.send(self.library, FakeCovers(), self.book_id, cancellable=cancellable)
        self.assertEqual(os.listdir(reader.books_dir), [])


if __name__ == '__main__':
    unittest.main()
