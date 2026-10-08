# SPDX-License-Identifier: GPL-2.0-or-later
# SPDX-FileCopyrightText: 2026 Jack Tully

from tests import ROOT  # noqa: F401  (registers src/ as bookcase)

import pathlib
import shutil
import tempfile
import unittest
import zipfile

from lxml import etree

from bookcase import kepub
from tests.support import make_epub

XHTML = '{http://www.w3.org/1999/xhtml}'


def spans(root):
    return [span for span in root.iter(f'{XHTML}span') if span.get('class') == 'koboSpan']


class DocumentTest(unittest.TestCase):
    def convert(self, body, head='<title>T</title>', doctype=''):
        source = ('<?xml version="1.0" encoding="UTF-8"?>\n' + doctype
                  + '<html xmlns="http://www.w3.org/1999/xhtml" '
                  'xmlns:epub="http://www.idpf.org/2007/ops">'
                  f'<head>{head}</head><body>{body}</body></html>').encode()
        return kepub.convert_document(source)

    def test_sentences_and_paragraphs(self):
        out = self.convert('<p>One. Two! Three?</p><p>Four</p>')
        root = etree.fromstring(out)
        ids = [span.get('id') for span in spans(root)]
        self.assertEqual(ids, ['kobo.1.1', 'kobo.1.2', 'kobo.1.3', 'kobo.2.1'])
        self.assertEqual([span.text for span in spans(root)],
                         ['One. ', 'Two! ', 'Three?', 'Four'])

    def test_wrapper_divs_and_style(self):
        root = etree.fromstring(self.convert('<h1>Title</h1><p>Text.</p>'))
        body = root.find(f'{XHTML}body')
        columns = body[0]
        self.assertEqual(len(body), 1)
        self.assertEqual(columns.get('id'), 'book-columns')
        self.assertEqual(columns[0].get('id'), 'book-inner')
        self.assertEqual([child.tag for child in columns[0]], [f'{XHTML}h1', f'{XHTML}p'])
        styles = root.find(f'{XHTML}head').findall(f'{XHTML}style')
        self.assertEqual(styles[-1].get('class'), 'kobostylehacks')

    def test_inline_elements_and_tails_keep_their_text_and_order(self):
        out = self.convert('<p>Before <em>inside. More</em> after. End</p>')
        root = etree.fromstring(out)
        paragraph = root.find(f'.//{XHTML}p')
        self.assertEqual(''.join(paragraph.itertext()), 'Before inside. More after. End')
        self.assertEqual(paragraph.find(f'{XHTML}em')[0].get('class'), 'koboSpan')

    def test_images_get_a_span(self):
        root = etree.fromstring(self.convert('<div><img src="a.png" alt=""/></div>'))
        image = root.find(f'.//{XHTML}img')
        self.assertEqual(image.getparent().get('class'), 'koboSpan')

    def test_skips_script_style_pre_and_svg(self):
        out = self.convert('<pre>a. b</pre><svg xmlns="http://www.w3.org/2000/svg">'
                           '<text>Not. Here</text></svg><p>Yes.</p>',
                           head='<title>T</title><style>p { margin: 0 }</style>')
        root = etree.fromstring(out)
        self.assertEqual(root.find(f'.//{XHTML}pre').text, 'a. b')
        self.assertEqual(len(spans(root)), 1)
        self.assertEqual(root.find('.//{http://www.w3.org/2000/svg}text').text, 'Not. Here')

    def test_html_entities_and_doctype(self):
        doctype = ('<!DOCTYPE html PUBLIC "-//W3C//DTD XHTML 1.1//EN" '
                   '"http://www.w3.org/TR/xhtml11/DTD/xhtml11.dtd">\n')
        out = self.convert('<p>A&nbsp;b &mdash; c &amp; d.</p>', doctype=doctype)
        self.assertIn(b'<!DOCTYPE html PUBLIC', out)
        root = etree.fromstring(out)
        self.assertEqual(''.join(root.find(f'.//{XHTML}p').itertext()),
                         'A b — c & d.')
        self.assertIn(b'xmlns:epub="http://www.idpf.org/2007/ops"', out)

    def test_idempotent(self):
        once = self.convert('<p>One. Two.</p>')
        self.assertEqual(kepub.convert_document(once), once)

    def test_unparsable_document_is_kept(self):
        broken = b'<html><body><p>Unclosed</body></html>'
        self.assertEqual(kepub.convert_document(broken), broken)


class BookTest(unittest.TestCase):
    def setUp(self):
        self.directory = pathlib.Path(tempfile.mkdtemp(prefix='bookcase-test-kepub-'))
        self.addCleanup(shutil.rmtree, self.directory, True)

    def test_convert_book(self):
        src = make_epub(self.directory / 'book.epub', chapters=2)
        dest = kepub.convert(src, self.directory / kepub.kepub_name('book.epub'))
        self.assertEqual(dest.name, 'book.kepub.epub')
        with zipfile.ZipFile(dest) as archive:
            first = archive.infolist()[0]
            self.assertEqual(first.filename, 'mimetype')
            self.assertEqual(first.compress_type, zipfile.ZIP_STORED)
            self.assertEqual(archive.read('mimetype'), b'application/epub+zip')
            with zipfile.ZipFile(src) as original:
                self.assertEqual(sorted(archive.namelist()), sorted(original.namelist()))
                self.assertEqual(archive.read('OEBPS/content.opf'),
                                 original.read('OEBPS/content.opf'))
                # The navigation document is left alone.
                self.assertEqual(archive.read('OEBPS/nav.xhtml'),
                                 original.read('OEBPS/nav.xhtml'))
            chapter = etree.fromstring(archive.read('OEBPS/chapter1.xhtml'))
            self.assertTrue(spans(chapter))
        self.assertTrue(kepub.is_kepub(dest))
        self.assertFalse(kepub.is_kepub(src))

    def test_converting_twice_changes_nothing(self):
        src = make_epub(self.directory / 'book.epub', chapters=1)
        once = kepub.convert(src, self.directory / 'once.kepub.epub')
        twice = kepub.convert(once, self.directory / 'twice.kepub.epub')
        with zipfile.ZipFile(once) as first, zipfile.ZipFile(twice) as second:
            for name in first.namelist():
                self.assertEqual(first.read(name), second.read(name), name)

    def test_kepub_name(self):
        self.assertEqual(kepub.kepub_name('A.epub'), 'A.kepub.epub')
        self.assertEqual(kepub.kepub_name('A.kepub.epub'), 'A.kepub.epub')
        self.assertEqual(kepub.kepub_name('A'), 'A.kepub.epub')


if __name__ == '__main__':
    unittest.main()
