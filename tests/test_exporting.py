# SPDX-License-Identifier: GPL-2.0-or-later
# SPDX-FileCopyrightText: 2026 Jack Tully

"""exporting.py: a copy named 'Title - Author.ext' carrying the library's edited metadata
and cover (EPUB), other formats copied as they are, free names, the library's file left
alone."""

import pathlib
import tempfile
import unittest

from tests import ROOT  # noqa: F401
from tests.support import make_epub, make_png, temporary_library
from tests.test_formats import make_mobi
from bookcase import exporting, formats
from bookcase.covers import CoverStore
from bookcase.importing import Importer


class TestExport(unittest.TestCase):

    def setUp(self):
        self._context = temporary_library()
        self.library = self._context.__enter__()
        self.root = pathlib.Path(self.library.path).parent
        self.covers = CoverStore(self.root, self.library)
        self.importer = Importer(self.library, self.covers, self.root / 'Books')
        self.out = pathlib.Path(tempfile.mkdtemp(dir=self.root))
        source = self.root / 'in'
        source.mkdir()
        report = self.importer.add([make_epub(source / 'a.epub', cover=make_png(2, 2)),
                                    make_mobi(source / 'a.mobi', title='A Quiet Harbour')])
        self.book_id = report.added[0]

    def tearDown(self):
        self.covers.shutdown()
        self._context.__exit__(None, None, None)

    def test_epub_carries_the_edits(self):
        self.library.update_book(self.book_id, title='Harbour Lights', series='Coast',
                                 series_index=2, tags=['Sea'], identifiers={'isbn':
                                                                           '9780306406157'})
        self.covers.save(self.book_id, make_png(5, 5))
        original = self.library.files(self.book_id)[0]
        before = pathlib.Path(original.path).read_bytes()
        path = exporting.export_copy(self.library, self.covers, self.book_id, self.out)
        self.assertEqual(pathlib.Path(path).name, 'Harbour Lights - Ada Lark.epub')
        info = formats.read(path)
        self.assertEqual(info.title, 'Harbour Lights')
        self.assertEqual((info.series, info.series_index), ('Coast', 2.0))
        self.assertEqual(info.tags, ['Sea'])
        self.assertEqual(info.identifiers['isbn'], '9780306406157')
        self.assertEqual(info.identifiers['uuid'], self.library.book(self.book_id).uuid)
        self.assertEqual(info.cover, make_png(5, 5))
        self.assertEqual(pathlib.Path(original.path).read_bytes(), before)

        again = exporting.export_copy(self.library, self.covers, self.book_id, self.out)
        self.assertEqual(pathlib.Path(again).name, 'Harbour Lights - Ada Lark (2).epub')
        plain = exporting.export_copy(self.library, self.covers, self.book_id, self.out,
                                      embed=False, name='device copy')
        self.assertEqual(pathlib.Path(plain).name, 'device copy.epub')
        self.assertEqual(pathlib.Path(plain).read_bytes(), before)

    def test_other_formats_are_copied(self):
        path = exporting.export_copy(self.library, self.covers, self.book_id, self.out,
                                     format='mobi')
        mobi = [f for f in self.library.files(self.book_id) if f.format == 'mobi'][0]
        self.assertEqual(pathlib.Path(path).read_bytes(), pathlib.Path(mobi.path).read_bytes())
        with self.assertRaises(exporting.ExportError):
            exporting.export_copy(self.library, self.covers, self.book_id, self.out,
                                  format='pdf')

    def test_missing_file(self):
        for file in self.library.files(self.book_id):
            pathlib.Path(file.path).unlink()
        with self.assertRaises(exporting.ExportError):
            exporting.export_copy(self.library, self.covers, self.book_id, self.out)


if __name__ == '__main__':
    unittest.main()
