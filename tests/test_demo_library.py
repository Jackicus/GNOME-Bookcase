# SPDX-License-Identifier: GPL-2.0-or-later
# SPDX-FileCopyrightText: 2026 Jack Tully

"""scripts/demo_library.py: the invented library's files are valid books, the same on every
run, and import through the app's own Importer with their history."""

import importlib.util
import os
import shutil
import tempfile
import unittest
import zipfile

from tests import ROOT

try:
    import cairo  # noqa: F401
    import gi

    gi.require_version('PangoCairo', '1.0')
    from gi.repository import PangoCairo  # noqa: F401
except (ImportError, ValueError):
    demo = None
else:
    _spec = importlib.util.spec_from_file_location('demo_library',
                                                   ROOT / 'scripts' / 'demo_library.py')
    demo = importlib.util.module_from_spec(_spec)
    _spec.loader.exec_module(demo)


@unittest.skipIf(demo is None, 'pycairo or PangoCairo is not installed')
class DemoLibraryTest(unittest.TestCase):

    def setUp(self):
        self.directory = tempfile.mkdtemp(prefix='bookcase-demo-test-')
        self.environ = os.environ.get('BOOKCASE_DATA_DIR')

    def tearDown(self):
        shutil.rmtree(self.directory, ignore_errors=True)
        if self.environ is not None:
            os.environ['BOOKCASE_DATA_DIR'] = self.environ

    def test_the_data_is_invented_and_complete(self):
        self.assertEqual(len(demo.BOOKS), 40)
        titles = [book['title'] for book in demo.BOOKS]
        self.assertEqual(len(set(titles)), len(titles))
        formats = {book.get('format', 'epub') for book in demo.BOOKS}
        self.assertEqual(formats, {'epub', 'pdf', 'cbz'})
        for book in demo.BOOKS:
            self.assertIn(book['style'], set(demo.COVER_STYLES) | {'comic'})

    def test_the_files_are_books_and_the_same_every_time(self):
        from bookcase import formats

        first = demo.build(os.path.join(self.directory, 'a'), count=3, files_only=True)
        second = demo.build(os.path.join(self.directory, 'b'), count=3, files_only=True)
        self.assertEqual(len(first), 3)
        for one, other in zip(first, second, strict=True):
            self.assertEqual(one.read_bytes(), other.read_bytes(), one.name)
        info = formats.read(first[0])
        self.assertEqual(info.title, 'The Glass Estuary')
        self.assertEqual(info.authors, ['Imogen Vale'])
        self.assertEqual((info.series, info.series_index), ('The Saltmarsh Chronicles', 3))
        self.assertEqual(formats.image_type(info.cover), 'jpeg')
        with zipfile.ZipFile(first[0]) as archive:
            self.assertEqual(archive.namelist()[0], 'mimetype')
            chapter = archive.read('OEBPS/chapter1.xhtml').decode('utf-8')
        self.assertGreater(chapter.count('<p>'), 10)

    def test_a_cover_is_two_by_three(self):
        surface = demo.draw_cover(demo.BOOKS[0])
        self.assertEqual((surface.get_width(), surface.get_height()), (600, 900))

    def test_the_library_gets_its_history(self):
        from bookcase.library import Library

        data_dir = os.path.join(self.directory, 'demo')
        demo.build(data_dir, count=8)
        library = Library(os.path.join(data_dir, 'library.sqlite'))
        try:
            self.assertEqual(library.count(), 8)
            reading = library.continue_reading()
            self.assertEqual(reading[0].title, 'The Glass Estuary')
            self.assertAlmostEqual(reading[0].progress, 0.42)
            self.assertTrue(all(book.has_cover for book in library.books()))
            self.assertEqual(len(library.annotations(reading[0].id, kind='highlight')), 3)
            self.assertEqual([shelf.name for shelf in library.shelves()],
                             ['Holiday Reading', 'Five Stars'])
            self.assertTrue(library.sessions(reading[0].id))
        finally:
            library.close()
        books = [name for _dir, _dirs, names in os.walk(os.path.join(data_dir, 'Books'))
                 for name in names]
        self.assertEqual(len(books), 8)


if __name__ == '__main__':
    unittest.main()
