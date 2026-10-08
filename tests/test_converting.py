# SPDX-License-Identifier: GPL-2.0-or-later
# SPDX-FileCopyrightText: 2026 Jack Tully

"""Books converted for the reader: plain text into an EPUB (paragraphs, headings, encodings),
CBR into CBZ, and the cache that keeps them."""

import codecs
import os
import pathlib
import shutil
import tempfile
import unittest
import zipfile
from unittest import mock

from tests import ROOT  # noqa: F401  (registers src/ as bookcase)

from bookcase import converting, formats
from bookcase.formats import FormatError, comic

TEXT = """A QUIET HARBOUR

CHAPTER I

The Arrival

The lamps along the harbour wall were lit one by one as the tide came in, and
nobody on the quay said much.

Part of the reason was the gulls, who said enough for everyone.

Roses on the sill
Rope on the quay

Chapter 2

Mira kept the ledger open on the counter.
"""


class TextTest(unittest.TestCase):

    def test_paragraphs_are_split_on_blank_lines_and_joined(self):
        blocks = converting.text_blocks('One line\nand its wrap.\n\n\nTwo.\r\n\r\nThree.')
        self.assertEqual(blocks, [['One line', 'and its wrap.'], ['Two.'], ['Three.']])

    def test_a_text_without_blank_lines_has_a_paragraph_a_line(self):
        text = '\n'.join(f'Line {n} of a file that never leaves a blank line.' for n in range(40))
        self.assertEqual(len(converting.text_blocks(text)), 40)

    def test_headings(self):
        sections = converting.chapters(converting.text_blocks(TEXT))
        headings = [heading for heading, _body in sections]
        self.assertEqual(headings, ['A QUIET HARBOUR', 'CHAPTER I: The Arrival', 'Chapter 2'])
        body = dict(sections)['CHAPTER I: The Arrival']
        self.assertEqual(body[1], ['Part of the reason was the gulls, who said enough for '
                                   'everyone.'])  # "Part of" is no heading

    def test_capitals_everywhere_are_not_headings(self):
        text = '\n\n'.join(f'SHOUTED LINE NUMBER {n}' for n in range(30))
        self.assertEqual([h for h, _b in converting.chapters(converting.text_blocks(text))],
                         [''])

    def test_a_long_text_without_headings_is_cut_into_sections(self):
        blocks = [[f'Paragraph {n}.'] for n in range(converting.SECTION_BLOCKS * 2 + 5)]
        self.assertEqual(len(converting.chapters(blocks)), 3)


class EpubTest(unittest.TestCase):

    def setUp(self):
        self.directory = pathlib.Path(tempfile.mkdtemp(prefix='bookcase-convert-'))
        self.cache = self.directory / 'cache'

    def tearDown(self):
        shutil.rmtree(self.directory, ignore_errors=True)

    def write(self, name, data):
        path = self.directory / name
        path.write_bytes(data)
        return str(path)

    def epub_text(self, path):
        with zipfile.ZipFile(path) as archive:
            self.assertEqual(archive.namelist()[0], 'mimetype')
            self.assertEqual(archive.read('mimetype'), b'application/epub+zip')
            return {name: archive.read(name).decode() for name in archive.namelist()}

    def test_a_text_becomes_an_epub_with_contents(self):
        source = self.write('Ada Lark - A Quiet Harbour.txt', TEXT.encode())
        path, fmt = converting.prepare(source, 'txt', title='A Quiet Harbour',
                                       author='Ada Lark', language='en',
                                       directory=str(self.cache))
        self.assertEqual(fmt, 'epub')
        self.assertTrue(path.startswith(str(self.cache)))
        files = self.epub_text(path)
        nav = files['OEBPS/nav.xhtml']
        self.assertIn('CHAPTER I: The Arrival', nav)
        self.assertIn('<dc:creator>Ada Lark</dc:creator>', files['OEBPS/content.opf'])
        self.assertIn('<br/>Rope on the quay', files['OEBPS/text0002.xhtml'])
        info = formats.read(path)
        self.assertEqual(info.title, 'A Quiet Harbour')

    def test_encodings(self):
        text = 'Café on the quay — “quiet”.\n\nSecond paragraph.'
        for name, data in (('bom.txt', codecs.BOM_UTF8 + text.encode()),
                           ('utf16.txt', text.encode('utf-16')),
                           ('latin.txt', text.replace('—', '-').replace('“', '"')
                            .replace('”', '"').encode('cp1252'))):
            source = self.write(name, data)
            path = converting.txt_to_epub(source, str(self.directory / (name + '.epub')))
            body = self.epub_text(path)['OEBPS/text0001.xhtml']
            self.assertIn('Café on the quay', body, name)

    def test_binary_is_refused(self):
        source = self.write('not.txt', b'\x00\x01binary\x00' * 10)
        with self.assertRaises(FormatError):
            converting.prepare(source, 'txt', directory=str(self.cache))

    def test_the_cache_is_reused_until_the_file_changes(self):
        source = self.write('harbour.txt', TEXT.encode())
        first, _fmt = converting.prepare(source, 'txt', directory=str(self.cache))
        with mock.patch.object(converting, 'txt_to_epub') as convert:
            again, _fmt = converting.prepare(source, 'txt', directory=str(self.cache))
            convert.assert_not_called()
        self.assertEqual(first, again)
        with open(first, 'rb') as file:
            before = file.read()
        os.remove(first)
        rebuilt, _fmt = converting.prepare(source, 'txt', directory=str(self.cache))
        with open(rebuilt, 'rb') as file:
            self.assertEqual(file.read(), before)  # the same text converts the same way
        stat = os.stat(source)
        os.utime(source, ns=(stat.st_atime_ns, stat.st_mtime_ns + 10**9))
        changed, _fmt = converting.prepare(source, 'txt', directory=str(self.cache))
        self.assertNotEqual(changed, first)

    def test_prune_drops_the_oldest(self):
        self.cache.mkdir()
        for n in range(3):
            path = self.cache / f'{n}.epub'
            path.write_bytes(b'x' * 100)
            os.utime(path, (1000 + n, 1000 + n))
        converting.prune(str(self.cache), limit=250)
        self.assertEqual(sorted(os.listdir(self.cache)), ['1.epub', '2.epub'])


class ComicTest(unittest.TestCase):

    def setUp(self):
        self.directory = pathlib.Path(tempfile.mkdtemp(prefix='bookcase-convert-'))
        self.cache = str(self.directory / 'cache')

    def tearDown(self):
        shutil.rmtree(self.directory, ignore_errors=True)

    def test_a_cbr_that_is_a_zip_opens_as_it_is(self):
        path = self.directory / 'Harbour Comics 1.cbr'
        with zipfile.ZipFile(path, 'w') as archive:
            archive.writestr('001.png', b'invented')
        self.assertEqual(converting.prepare(str(path), 'cbr', directory=self.cache),
                         (str(path), 'cbz'))

    def test_a_cbr_is_converted_once(self):
        path = self.directory / 'Harbour Comics 2.cbr'
        path.write_bytes(b'Rar!\x1a\x07\x00invented')

        def convert(_source, dest):
            with zipfile.ZipFile(dest, 'w') as archive:
                archive.writestr('001.png', b'invented')
            return dest

        with mock.patch.object(comic, 'cbr_to_cbz', side_effect=convert) as cbr_to_cbz:
            first = converting.prepare(str(path), 'cbr', directory=self.cache)
            second = converting.prepare(str(path), 'cbr', directory=self.cache)
        self.assertEqual(cbr_to_cbz.call_count, 1)
        self.assertEqual(first, second)
        self.assertEqual(first[1], 'cbz')
        self.assertTrue(first[0].endswith('.cbz') and os.path.exists(first[0]))

    def test_without_bsdtar_the_error_says_so(self):
        path = self.directory / 'Harbour Comics 3.cbr'
        path.write_bytes(b'Rar!\x1a\x07\x00invented')
        with mock.patch.object(comic, '_bsdtar', return_value=None), \
                self.assertRaisesRegex(FormatError, 'bsdtar'):
            converting.prepare(str(path), 'cbr', directory=self.cache)

    def test_other_formats_pass_through(self):
        self.assertEqual(converting.prepare('/invented/a.epub', 'epub'),
                         ('/invented/a.epub', 'epub'))
        self.assertFalse(converting.needs_conversion('pdf'))
        self.assertTrue(converting.needs_conversion('TXT'))

    def test_the_cache_follows_the_data_directory(self):
        self.assertTrue(converting.cache_dir().startswith(os.environ['BOOKCASE_DATA_DIR']))


if __name__ == '__main__':
    unittest.main()
