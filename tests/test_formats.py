# SPDX-License-Identifier: GPL-2.0-or-later
# SPDX-FileCopyrightText: 2026 Jack Tully

"""formats/: EPUB 2 and 3 read and written back, the cover lookups, MOBI (made by a tiny
writer here), FB2 and FBZ, CBZ with ComicInfo.xml, PDF when Poppler is there, file name
fallbacks, and junk that must give FormatError. Every book is invented and built here."""

import base64
import os
import pathlib
import shutil
import struct
import tempfile
import unittest
import zipfile

from tests import ROOT  # noqa: F401
from tests.support import make_epub, make_png
from bookcase import formats
from bookcase.formats import BookInfo, FormatError, comic, epub, pdf

JPEG = b'\xff\xd8\xff\xe0' + bytes(range(64))


def make_mobi(path, title='The Tidewater Line', authors=('Ada Lark',), cover=None,
              exth_cover=True, extra=(), name_title=None, locale=9, utf8=True):
    """A small PalmDB + MOBI header + EXTH file: record 0, one text record, the cover."""
    codec = 'utf-8' if utf8 else 'cp1252'
    records = []
    exth = [(100, a) for a in authors] + list(extra)
    if cover is not None and exth_cover:
        exth.append((201, struct.pack('>I', 0)))
    entries = b''
    for kind, value in exth:
        data = value if isinstance(value, bytes) else value.encode(codec)
        entries += struct.pack('>II', kind, len(data) + 8) + data
    exth_block = b'EXTH' + struct.pack('>II', 12 + len(entries), len(exth)) + entries
    mobi_length = 232
    full_name = (name_title or title).encode(codec)
    name_offset = 16 + mobi_length + len(exth_block)
    first_image = 2 if cover is not None else 0xFFFFFFFF
    header = bytearray(16 + mobi_length)
    struct.pack_into('>HHIHHHH', header, 0, 1, 0, 5, 1, 4096, 0, 0)
    header[16:20] = b'MOBI'
    struct.pack_into('>IIIII', header, 20, mobi_length, 2, 65001 if utf8 else 1252, 1, 6)
    struct.pack_into('>II', header, 84, name_offset, len(full_name))
    struct.pack_into('>I', header, 92, locale)
    struct.pack_into('>I', header, 108, first_image)
    struct.pack_into('>I', header, 128, 0x40)
    record0 = bytes(header) + exth_block + full_name + b'\0\0'
    records = [record0, b'Hello']
    if cover is not None:
        records.append(cover)
    start = 78 + 8 * len(records) + 2
    offsets = []
    for record in records:
        offsets.append(start)
        start += len(record)
    db = bytearray(78)
    db[:len(title[:31])] = title[:31].encode('latin-1', 'replace')
    db[60:68] = b'BOOKMOBI'
    struct.pack_into('>H', db, 76, len(records))
    for number, offset in enumerate(offsets):
        db += struct.pack('>II', offset, number)
    db += b'\0\0'
    with open(path, 'wb') as file:
        file.write(bytes(db) + b''.join(records))
    return path


def make_fb2(title='Salt and Ember', cover=None, namespace=True):
    ns = (' xmlns="http://www.gribuser.ru/xml/fictionbook/2.0"' if namespace else '')
    cover_xml = binary = ''
    if cover is not None:
        cover_xml = '<coverpage><image l:href="#cover.png"/></coverpage>'
        binary = ('<binary id="cover.png" content-type="image/png">'
                  + base64.b64encode(cover).decode() + '</binary>')
    return (f'<?xml version="1.0" encoding="utf-8"?>\n<FictionBook{ns} '
            'xmlns:l="http://www.w3.org/1999/xlink"><description><title-info>'
            '<genre>sf_fantasy</genre><author><first-name>Ben</first-name>'
            '<middle-name>Q.</middle-name><last-name>Ross</last-name></author>'
            '<author><nickname>Quill</nickname></author>'
            f'<book-title>{title}</book-title>'
            '<annotation><p>First line.</p><p>Second &amp; last.</p></annotation>'
            '<date value="2011-05-04">2011</date><lang>ru</lang>'
            f'<sequence name="Embers" number="3"/>{cover_xml}</title-info>'
            '<publish-info><publisher>Driftwood Press</publisher><year>2012</year>'
            '<isbn>978-0-306-40615-7</isbn></publish-info></description>'
            f'<body><section><p>Text.</p></section></body>{binary}</FictionBook>\n'
            ).encode()


def make_pdf(path, title='A Paper Boat', author='Cara Moss'):
    """A one-page PDF written by hand (with a correct xref table)."""
    objects = [b'<< /Type /Catalog /Pages 2 0 R >>',
               b'<< /Type /Pages /Kids [3 0 R] /Count 1 >>',
               b'<< /Type /Page /Parent 2 0 R /MediaBox [0 0 200 300] /Contents 5 0 R >>',
               f'<< /Title ({title}) /Author ({author}) /Keywords (boats, paper) >>'.encode(),
               b'<< /Length 35 >>\nstream\n0 0 1 rg 20 20 160 260 re f\nendstream']
    out = b'%PDF-1.4\n'
    offsets = []
    for number, body in enumerate(objects, 1):
        offsets.append(len(out))
        out += f'{number} 0 obj\n'.encode() + body + b'\nendobj\n'
    xref = len(out)
    out += f'xref\n0 {len(objects) + 1}\n0000000000 65535 f \n'.encode()
    for offset in offsets:
        out += f'{offset:010d} 00000 n \n'.encode()
    out += (f'trailer\n<< /Size {len(objects) + 1} /Root 1 0 R /Info 4 0 R >>\n'
            f'startxref\n{xref}\n%%EOF\n').encode()
    with open(path, 'wb') as file:
        file.write(out)
    return path


class FormatsTestCase(unittest.TestCase):

    def setUp(self):
        self.directory = tempfile.mkdtemp(prefix='bookcase-formats-')

    def tearDown(self):
        shutil.rmtree(self.directory, ignore_errors=True)

    def path(self, name):
        return os.path.join(self.directory, name)


class TestNames(FormatsTestCase):

    def test_format_of(self):
        cases = {'a.epub': 'epub', 'A.EPUB': 'epub', 'b.kepub.epub': 'kepub', 'c.mobi': 'mobi',
                 'd.azw': 'mobi', 'e.azw3': 'azw3', 'f.pdf': 'pdf', 'g.cbz': 'cbz',
                 'h.cbr': 'cbr', 'i.fb2': 'fb2', 'j.fbz': 'fbz', 'k.fb2.zip': 'fbz',
                 'l.txt': 'txt', 'm.doc': None, 'n.zip': None, '.epub': None, 'o': None}
        for name, expected in cases.items():
            self.assertEqual(formats.format_of(name), expected, name)
        self.assertIn('.kepub.epub', formats.SUFFIXES)
        self.assertIn('.cbr', formats.SUFFIXES)

    def test_parse_filename(self):
        cases = {
            'Ada Lark - A Quiet Harbour.epub': ('A Quiet Harbour', ['Ada Lark']),
            'A_Quiet_Harbour.pdf': ('A Quiet Harbour', []),
            'A Quiet Harbour (Ada Lark).pdf': ('A Quiet Harbour', ['Ada Lark']),
            'A Quiet Harbour (2nd edition).pdf': ('A Quiet Harbour (2nd edition)', []),
            'Ada Lark & Ben Ross - Two Tides.kepub.epub': ('Two Tides', ['Ada Lark',
                                                                         'Ben Ross']),
            'Book 01 - Harbour.txt': ('Book 01 - Harbour', []),
            'Lark, Ada - Harbour.fb2.zip': ('Harbour', ['Lark, Ada']),
        }
        for name, expected in cases.items():
            self.assertEqual(formats.parse_filename(name), expected, name)

    def test_normalisers(self):
        self.assertEqual(formats.normalize_date('2019-04-02T00:00:00+00:00'), '2019-04-02')
        self.assertEqual(formats.normalize_date('2019-4'), '2019-04')
        self.assertEqual(formats.normalize_date('April 2019'), '2019')
        self.assertEqual(formats.normalize_date('0101-01-01T00:00:00+00:00'), '')
        self.assertEqual(formats.normalize_date('soon'), '')
        self.assertEqual(formats.normalize_language('en-GB'), 'en')
        self.assertEqual(formats.normalize_language('fre'), 'fr')
        self.assertEqual(formats.normalize_language('und'), '')
        self.assertEqual(formats.text_to_html('One\n\nTwo & three'),
                         '<p>One</p><p>Two &amp; three</p>')
        self.assertEqual(formats.text_to_html('<p>As is</p>'), '<p>As is</p>')
        self.assertTrue(formats.isbn_valid('978-0-306-40615-7'))
        self.assertTrue(formats.isbn_valid('0306406152'))
        self.assertFalse(formats.isbn_valid('9780306406158'))
        self.assertEqual(formats.image_type(make_png(1, 1)), 'png')
        self.assertEqual(formats.image_type(JPEG), 'jpeg')
        self.assertIsNone(formats.image_type(b'nothing'))


class TestEpub(FormatsTestCase):

    def test_read_epub3(self):
        cover = make_png(3, 4)
        path = make_epub(self.path('a.epub'), title='A Quiet Harbour',
                         authors=('Ada Lark', 'Ben Ross'), series='Harbour Tales',
                         series_index=2.5, language='en-GB', description='Gulls & tar.',
                         isbn='9780306406157', cover=cover, tags=('Sea', 'Quiet'),
                         publisher='Driftwood Press', published='2020-03-01T00:00:00Z')
        info = formats.read(path)
        self.assertEqual(info.title, 'A Quiet Harbour')
        self.assertEqual(info.authors, ['Ada Lark', 'Ben Ross'])
        self.assertEqual((info.series, info.series_index), ('Harbour Tales', 2.5))
        self.assertEqual(info.language, 'en')
        self.assertEqual(info.description, '<p>Gulls &amp; tar.</p>')
        self.assertEqual(info.identifiers['isbn'], '9780306406157')
        self.assertIn('uuid', info.identifiers)
        self.assertEqual(info.tags, ['Sea', 'Quiet'])
        self.assertEqual(info.publisher, 'Driftwood Press')
        self.assertEqual(info.published, '2020-03-01')
        self.assertEqual(info.cover, cover)
        self.assertEqual(info.format, 'epub')

    def test_read_epub2_and_kepub(self):
        cover = make_png(2, 2)
        path = make_epub(self.path('b.kepub.epub'), epub3=False, series='Tides',
                         series_index=1, isbn='0306406152', cover=cover)
        info = formats.read(path)
        self.assertEqual(info.format, 'kepub')
        self.assertEqual(info.authors, ['Ada Lark'])
        self.assertEqual((info.series, info.series_index), ('Tides', 1.0))
        self.assertEqual(info.identifiers['isbn'], '0306406152')
        self.assertEqual(info.cover, cover)

    def test_series_from_collection_only(self):
        path = self.write_epub('c.epub', version='3.0', metadata=(
            '<dc:title>T</dc:title><meta property="belongs-to-collection" id="s">'
            'The Long Coast</meta><meta refines="#s" property="collection-type">series</meta>'
            '<meta refines="#s" property="group-position">4</meta>'))
        info = formats.read(path)
        self.assertEqual((info.series, info.series_index), ('The Long Coast', 4.0))

    def write_epub(self, name, version='2.0', metadata='', manifest='', guide='',
                   files=None, opf_dir='OPS'):
        """An EPUB with the given OPF pieces and extra members, for the cover lookups."""
        path = self.path(name)
        opf = (f'<?xml version="1.0"?><package xmlns="http://www.idpf.org/2007/opf" '
               f'version="{version}" unique-identifier="id"><metadata '
               'xmlns:dc="http://purl.org/dc/elements/1.1/" '
               'xmlns:opf="http://www.idpf.org/2007/opf"><dc:identifier id="id">urn:uuid:'
               f'0000-1</dc:identifier>{metadata}</metadata><manifest>{manifest}</manifest>'
               f'<spine/>{guide}</package>')
        with zipfile.ZipFile(path, 'w') as archive:
            archive.writestr('mimetype', 'application/epub+zip')
            archive.writestr('META-INF/container.xml',
                             '<container xmlns="urn:oasis:names:tc:opendocument:xmlns:'
                             f'container"><rootfiles><rootfile full-path="{opf_dir}/c.opf"/>'
                             '</rootfiles></container>')
            archive.writestr(f'{opf_dir}/c.opf', opf)
            for member, data in (files or {}).items():
                archive.writestr(member, data)
        return path

    def test_cover_lookup_order(self):
        red, green, blue = make_png(1, 1, (255, 0, 0)), make_png(1, 1, (0, 255, 0)), \
            make_png(1, 1, (0, 0, 255))
        images = {'OPS/img/a.png': red, 'OPS/img/b.png': green, 'OPS/img/front page.png': blue}
        items = ('<item id="a" href="img/a.png" media-type="image/png"/>'
                 '<item id="b" href="img/b.png" media-type="image/png"/>')
        # EPUB 3 cover-image wins over the EPUB 2 meta.
        path = self.write_epub('1.epub', version='3.0', metadata='<meta name="cover" '
                               'content="a"/>', manifest=items.replace(
                                   'id="b"', 'id="b" properties="cover-image"'), files=images)
        self.assertEqual(formats.read(path).cover, green)
        # EPUB 2 meta by id; by href when that is what it holds.
        path = self.write_epub('2.epub', metadata='<meta name="cover" content="b"/>',
                               manifest=items, files=images)
        self.assertEqual(formats.read(path).cover, green)
        path = self.write_epub('3.epub', metadata='<meta name="cover" content="img/b.png"/>',
                               manifest=items, files=images)
        self.assertEqual(formats.read(path).cover, green)
        # An item named like a cover.
        path = self.write_epub('4.epub', manifest=items + '<item id="x" href="img/my-cover'
                               '.png" media-type="image/png"/>',
                               files={**images, 'OPS/img/my-cover.png': blue})
        self.assertEqual(formats.read(path).cover, blue)
        # The guide's cover page: its image, relative to the page, URL-encoded.
        page = ('<html xmlns="http://www.w3.org/1999/xhtml"><body><svg xmlns="http://www.w3.'
                'org/2000/svg" xmlns:xlink="http://www.w3.org/1999/xlink"><image xlink:href'
                '="../img/front%20page.png"/></svg></body></html>')
        path = self.write_epub('5.epub', manifest=items, guide='<guide><reference type="cover"'
                               ' href="text/title.xhtml#top"/></guide>',
                               files={**images, 'OPS/text/title.xhtml': page})
        self.assertEqual(formats.read(path).cover, blue)
        # The first image there is.
        path = self.write_epub('6.epub', manifest=items, files=images)
        self.assertEqual(formats.read(path).cover, red)
        # A cover item whose file is missing: no cover, no error.
        path = self.write_epub('7.epub', manifest='<item id="cover" href="gone.png" '
                               'media-type="image/png"/>')
        self.assertIsNone(formats.read(path).cover)

    def assert_valid_container(self, path):
        with zipfile.ZipFile(path) as archive:
            first = archive.infolist()[0]
            self.assertEqual(first.filename, 'mimetype')
            self.assertEqual(first.compress_type, zipfile.ZIP_STORED)
            self.assertEqual(first.extra, b'')
            self.assertEqual(archive.read('mimetype'), b'application/epub+zip')
        with open(path, 'rb') as file:
            self.assertEqual(file.read(58)[30:], b'mimetypeapplication/epub+zip')

    def round_trip(self, epub3):
        source = make_epub(self.path('source.epub'), epub3=epub3, series='Old Series',
                           series_index=1, isbn='0306406152', cover=make_png(2, 3),
                           tags=('Old',), description='Old text')
        before = pathlib.Path(source).read_bytes()
        info = BookInfo(title='The New Title', authors=['Cara Moss', 'Dev Okafor'],
                        series='Lanterns', series_index=3.5, tags=['Night', 'Lamps'],
                        publisher='Lamplight', published='2021-07', language='de',
                        description='<p>New &amp; improved.</p>',
                        identifiers={'isbn': '978-0-306-40615-7', 'google': 'g00gle'})
        cover = JPEG
        dest = self.path('copy.epub')
        self.assertEqual(epub.write(source, info, cover=cover, dest=dest), dest)
        self.assertEqual(pathlib.Path(source).read_bytes(), before)
        self.assert_valid_container(dest)
        again = formats.read(dest)
        self.assertEqual(again.title, 'The New Title')
        self.assertEqual(again.authors, ['Cara Moss', 'Dev Okafor'])
        self.assertEqual((again.series, again.series_index), ('Lanterns', 3.5))
        self.assertEqual(again.tags, ['Night', 'Lamps'])
        self.assertEqual(again.publisher, 'Lamplight')
        self.assertEqual(again.published, '2021-07')
        self.assertEqual(again.language, 'de')
        self.assertEqual(again.description, '<p>New &amp; improved.</p>')
        self.assertEqual(again.identifiers['isbn'], '9780306406157')
        self.assertEqual(again.identifiers['google'], 'g00gle')
        self.assertEqual(again.identifiers['uuid'], formats.read(source).identifiers['uuid'])
        self.assertEqual(again.cover, cover)
        with zipfile.ZipFile(source) as a, zipfile.ZipFile(dest) as b:
            for name in a.namelist():
                if not name.endswith(('.opf', '.png')):
                    self.assertEqual(a.read(name), b.read(name), name)
            opf = b.read('OEBPS/content.opf').decode()
        return opf

    def test_write_epub3(self):
        opf = self.round_trip(epub3=True)
        self.assertIn('belongs-to-collection', opf)
        self.assertIn('calibre:series', opf)
        self.assertIn('urn:isbn:9780306406157', opf)
        self.assertIn('dcterms:modified', opf)
        self.assertNotIn('2026-01-01T00:00:00Z', opf)
        self.assertEqual(opf.count('properties="cover-image"'), 1)

    def test_write_epub2(self):
        opf = self.round_trip(epub3=False)
        self.assertNotIn('belongs-to-collection', opf)
        self.assertIn('opf:scheme="ISBN"', opf)
        self.assertIn('opf:event="publication"', opf)

    def test_write_adds_a_cover(self):
        source = make_epub(self.path('bare.epub'))
        dest = self.path('covered.epub')
        epub.write(source, formats.read(source), cover=make_png(5, 5), dest=dest)
        self.assertEqual(formats.read(dest).cover, make_png(5, 5))
        self.assert_valid_container(dest)
        info = BookInfo(title='Plain')
        dest2 = self.path('plain.epub')
        epub.write(dest, info, dest=dest2)
        self.assertEqual(formats.read(dest2).series, '')
        self.assertEqual(formats.read(dest2).cover, make_png(5, 5))

    def test_write_refuses(self):
        source = make_epub(self.path('x.epub'))
        with self.assertRaises(ValueError):
            epub.write(source, BookInfo(title='x'), dest=source)
        protected = self.path('drm.epub')
        shutil.copy(source, protected)
        with zipfile.ZipFile(protected, 'a') as archive:
            archive.writestr('META-INF/rights.xml', '<rights/>')
        self.assertTrue(epub.is_protected(protected))
        self.assertFalse(epub.is_protected(source))
        with self.assertRaises(FormatError):
            epub.write(protected, BookInfo(title='x'), dest=self.path('y.epub'))
        self.assertFalse(os.path.exists(self.path('y.epub')))


class TestMobi(FormatsTestCase):

    def test_read(self):
        cover = make_png(3, 5)
        path = make_mobi(self.path('Tidewater.azw3'), cover=cover, authors=('Ada Lark',
                                                                             'Ben Ross'),
                         extra=[(101, 'Driftwood Press'), (103, '<p>Rails by the sea.</p>'),
                                (104, '978-0-306-40615-7'), (105, 'Trains; Sea'),
                                (106, '2018-06-01T00:00:00+00:00'), (524, 'en-US'),
                                (503, 'The Tidewater Line, Revised')])
        info = formats.read(path)
        self.assertEqual(info.format, 'azw3')
        self.assertEqual(info.title, 'The Tidewater Line, Revised')
        self.assertEqual(info.authors, ['Ada Lark', 'Ben Ross'])
        self.assertEqual(info.publisher, 'Driftwood Press')
        self.assertEqual(info.description, '<p>Rails by the sea.</p>')
        self.assertEqual(info.identifiers['isbn'], '9780306406157')
        self.assertEqual(info.tags, ['Trains', 'Sea'])
        self.assertEqual(info.published, '2018-06-01')
        self.assertEqual(info.language, 'en')
        self.assertEqual(info.cover, cover)

    def test_full_name_locale_and_first_image(self):
        cover = make_png(2, 2)
        path = make_mobi(self.path('x.mobi'), name_title='Ünder the Pier', cover=cover,
                         exth_cover=False, locale=12, utf8=False)
        info = formats.read(path)
        self.assertEqual(info.title, 'Ünder the Pier')
        self.assertEqual(info.language, 'fr')
        self.assertEqual(info.cover, cover)

    def test_truncated(self):
        path = make_mobi(self.path('t.mobi'), cover=make_png(2, 2))
        data = pathlib.Path(path).read_bytes()
        for size in (10, 80, 100, 200, 400):
            with open(path, 'wb') as file:
                file.write(data[:size])
            with self.subTest(size=size):
                try:
                    formats.read(path)
                except FormatError:
                    pass


class TestFb2(FormatsTestCase):

    def check(self, info):
        self.assertEqual(info.title, 'Salt and Ember')
        self.assertEqual(info.authors, ['Ben Q. Ross', 'Quill'])
        self.assertEqual((info.series, info.series_index), ('Embers', 3.0))
        self.assertEqual(info.description, '<p>First line.</p><p>Second &amp; last.</p>')
        self.assertEqual(info.published, '2011-05-04')
        self.assertEqual(info.language, 'ru')
        self.assertEqual(info.publisher, 'Driftwood Press')
        self.assertEqual(info.identifiers['isbn'], '9780306406157')
        self.assertEqual(info.tags, ['sf_fantasy'])

    def test_fb2(self):
        cover = make_png(4, 4)
        path = self.path('a.fb2')
        with open(path, 'wb') as file:
            file.write(make_fb2(cover=cover))
        info = formats.read(path)
        self.check(info)
        self.assertEqual(info.cover, cover)
        with open(path, 'wb') as file:
            file.write(make_fb2(namespace=False))
        self.check(formats.read(path))

    def test_fbz(self):
        path = self.path('a.fb2.zip')
        with zipfile.ZipFile(path, 'w') as archive:
            archive.writestr('book.fb2', make_fb2())
        info = formats.read(path)
        self.assertEqual(info.format, 'fbz')
        self.check(info)


class TestComic(FormatsTestCase):

    def test_cbz(self):
        first, front = make_png(2, 2, (1, 1, 1)), make_png(2, 2, (9, 9, 9))
        path = self.path('c.cbz')
        with zipfile.ZipFile(path, 'w') as archive:
            archive.writestr('__MACOSX/._p1.png', b'junk')
            archive.writestr('p10.png', front)
            archive.writestr('p2.png', first)
            archive.writestr('.hidden.png', b'junk')
        info = formats.read(path)
        self.assertEqual(info.cover, first)
        self.assertEqual(info.title, 'c')
        with zipfile.ZipFile(path, 'a') as archive:
            archive.writestr('ComicInfo.xml', (
                '<?xml version="1.0"?><ComicInfo><Series>Night Ferry</Series>'
                '<Number>7</Number><Writer>Ada Lark, Ben Ross</Writer><Summary>Fog.</Summary>'
                '<Year>2015</Year><Month>9</Month><Publisher>Quay</Publisher>'
                '<Genre>Mystery</Genre><LanguageISO>en</LanguageISO><Pages>'
                '<Page Image="0"/><Page Image="1" Type="FrontCover"/></Pages></ComicInfo>'))
        info = formats.read(path)
        self.assertEqual(info.title, 'Night Ferry 7')
        self.assertEqual((info.series, info.series_index), ('Night Ferry', 7.0))
        self.assertEqual(info.authors, ['Ada Lark', 'Ben Ross'])
        self.assertEqual(info.description, '<p>Fog.</p>')
        self.assertEqual(info.published, '2015-09')
        self.assertEqual(info.tags, ['Mystery'])
        self.assertEqual(info.cover, front)

    def test_natural_order(self):
        self.assertEqual(comic.images(['b/10.jpg', 'b/9.jpg', 'a.txt', 'b/1.JPG']),
                         ['b/1.JPG', 'b/9.jpg', 'b/10.jpg'])

    def test_cbr_that_is_a_zip(self):
        path = self.path('z.cbr')
        with zipfile.ZipFile(path, 'w') as archive:
            archive.writestr('1.png', make_png(1, 1))
        self.assertEqual(formats.read(path).cover, make_png(1, 1))

    @unittest.skipUnless(shutil.which('bsdtar'), 'needs bsdtar')
    def test_cbz_to_cbz_through_bsdtar(self):
        # bsdtar cannot write RAR, so the conversion is checked on a zip it unpacks the same way.
        source = self.path('s.cbz')
        with zipfile.ZipFile(source, 'w') as archive:
            archive.writestr('pages/p1.png', make_png(1, 1))
            archive.writestr('pages/p2.png', make_png(1, 2))
        dest = comic.cbr_to_cbz(source, self.path('out.cbz'))
        with zipfile.ZipFile(dest) as archive:
            self.assertEqual(sorted(archive.namelist()), ['pages/p1.png', 'pages/p2.png'])


def _zeros(archive, name, size):
    """A member of `size` zero bytes, written in chunks (a zip bomb's shape: it compresses
    to a thousandth of that)."""
    info = zipfile.ZipInfo(name)
    info.compress_type = zipfile.ZIP_DEFLATED
    with archive.open(info, 'w', force_zip64=True) as member:
        chunk = bytes(1 << 20)
        for _ in range(size >> 20):
            member.write(chunk)


class TestUntrusted(FormatsTestCase):
    """Book files are untrusted: no entity reaches a local file, no member is inflated past
    a limit."""

    def test_external_entities_are_never_read(self):
        secret = self.path('secret.txt')
        with open(secret, 'w') as file:
            file.write('SECRET-CONTENT')
        doctype = (f'<!DOCTYPE x [<!ENTITY leak SYSTEM "file://{secret}">'
                   '<!ENTITY lol "lol"><!ENTITY lol2 "&lol;&lol;&lol;&lol;&lol;&lol;">]>')
        fb2 = make_fb2().decode('utf-8').replace(
            '<?xml version="1.0" encoding="utf-8"?>',
            '<?xml version="1.0" encoding="utf-8"?>' + doctype).replace(
            'Salt and Ember', 'Salt &leak; &lol2;', 1)
        path = self.path('x.fb2')
        with open(path, 'w', encoding='utf-8') as file:
            file.write(fb2)
        info = formats.read(path)
        self.assertNotIn('SECRET', info.title)
        self.assertNotIn('lollol', info.title)

        book = make_epub(self.path('x.epub'), title='Plain')
        rewritten = self.path('y.epub')
        with zipfile.ZipFile(book) as source, zipfile.ZipFile(rewritten, 'w') as target:
            for member in source.infolist():
                data = source.read(member)
                if member.filename.endswith('.opf'):
                    text = data.decode('utf-8')
                    text = text.replace('?>', '?>' + doctype, 1).replace(
                        '>Plain<', '>Plain &leak;&lol2;<')
                    data = text.encode('utf-8')
                target.writestr(member, data)
        try:
            info = formats.read(rewritten)
        except FormatError:
            return  # refusing the file is safe too
        self.assertNotIn('SECRET', info.title)
        self.assertNotIn('lollol', info.title)

    def test_an_epub_cover_bomb_is_not_inflated(self):
        path = self.path('bomb.epub')
        book = make_epub(self.path('plain.epub'), cover=make_png(2, 2))
        with zipfile.ZipFile(book) as source, zipfile.ZipFile(
                path, 'w', zipfile.ZIP_DEFLATED) as target:
            for member in source.infolist():
                if member.filename.lower().endswith(('.png', '.jpg', '.gif')):
                    _zeros(target, member.filename, epub.MAX_COVER + (1 << 20))
                else:
                    target.writestr(member, source.read(member))
        self.assertLess(os.path.getsize(path), 1 << 20)
        info = formats.read(path)
        self.assertEqual(info.title, 'A Quiet Harbour')
        self.assertIsNone(info.cover)

    def test_a_huge_container_is_refused(self):
        path = self.path('bomb.epub')
        with zipfile.ZipFile(path, 'w', zipfile.ZIP_DEFLATED) as archive:
            archive.writestr('mimetype', 'application/epub+zip')
            _zeros(archive, 'META-INF/container.xml', epub.MAX_OPF + (1 << 20))
        with self.assertRaises(FormatError):
            formats.read(path)

    def test_a_comic_info_bomb_is_not_inflated(self):
        path = self.path('bomb.cbz')
        with zipfile.ZipFile(path, 'w', zipfile.ZIP_DEFLATED) as archive:
            archive.writestr('p1.png', make_png(1, 1))
            _zeros(archive, 'ComicInfo.xml', comic.MAX_COMIC_INFO + (1 << 20))
        info = formats.read(path)
        self.assertEqual(info.cover, make_png(1, 1))
        self.assertEqual(info.title, 'bomb')

    @unittest.skipUnless(shutil.which('bsdtar'), 'needs bsdtar')
    def test_bsdtar_output_is_read_no_further_than_the_limit(self):
        # A CBR's members come through bsdtar: one inflating past the limit gives nothing.
        path = self.path('big.cbr')
        with zipfile.ZipFile(path, 'w', zipfile.ZIP_DEFLATED) as archive:
            archive.writestr('p1.png', make_png(1, 1))
            _zeros(archive, 'p2.png', 4 << 20)
        tool = shutil.which('bsdtar')
        self.assertIsNone(comic._extract(tool, path, 'p2.png', limit=1 << 20))
        self.assertEqual(len(comic._extract(tool, path, 'p2.png', limit=8 << 20)), 4 << 20)
        self.assertEqual(comic._extract(tool, path, 'p1.png'), make_png(1, 1))
        self.assertIsNone(comic._extract(tool, path, 'missing.png'))

    def test_writing_a_copy_streams_large_members(self):
        path = self.path('big.epub')
        book = make_epub(self.path('plain.epub'))
        with zipfile.ZipFile(book) as source, zipfile.ZipFile(
                path, 'w', zipfile.ZIP_DEFLATED) as target:
            for member in source.infolist():
                target.writestr(member, source.read(member))
            _zeros(target, 'OEBPS/padding.bin', 8 << 20)
        dest = epub.write(path, BookInfo(title='Copied'), dest=self.path('copy.epub'))
        self.assertEqual(formats.read(dest).title, 'Copied')
        with zipfile.ZipFile(dest) as archive:
            self.assertEqual(archive.getinfo('OEBPS/padding.bin').file_size, 8 << 20)
            self.assertIsNone(archive.testzip())


class TestPdf(FormatsTestCase):

    def test_pdf(self):
        path = make_pdf(self.path('boat.pdf'))
        info = formats.read(path)
        self.assertEqual(info.format, 'pdf')
        if not pdf.AVAILABLE:
            self.assertEqual(info.title, 'boat')
            return
        self.assertEqual(info.title, 'A Paper Boat')
        self.assertEqual(info.authors, ['Cara Moss'])
        self.assertEqual(info.tags, ['boats', 'paper'])
        if pdf.cairo is not None:
            self.assertEqual(formats.image_type(info.cover), 'png')
            width, height = struct.unpack('>II', info.cover[16:24])
            self.assertEqual((width, height), (400, 600))

    def test_junk_title_and_broken_pdf(self):
        path = make_pdf(self.path('Cara Moss - Paper Boats.pdf'),
                        title='Microsoft Word - boats.doc', author='')
        info = formats.read(path)
        self.assertEqual((info.title, info.authors), ('Paper Boats', ['Cara Moss']))
        path = self.path('Broken.pdf')
        with open(path, 'wb') as file:
            file.write(b'%PDF-1.4\nnothing else')
        self.assertEqual(formats.read(path).title, 'Broken')


class TestJunk(FormatsTestCase):

    def test_junk_is_a_format_error(self):
        junk = [b'', b'junk' * 100, os.urandom(4096), b'PK\x03\x04' + os.urandom(200)]
        for suffix in ('.epub', '.mobi', '.azw3', '.pdf', '.cbz', '.cbr', '.fb2', '.fbz'):
            for number, data in enumerate(junk):
                path = self.path(f'junk{number}{suffix}')
                with open(path, 'wb') as file:
                    file.write(data)
                with self.subTest(suffix=suffix, number=number), \
                        self.assertRaises(FormatError):
                    formats.read(path)

    def test_zips_without_books(self):
        path = self.path('empty.epub')
        with zipfile.ZipFile(path, 'w') as archive:
            archive.writestr('hello.txt', 'hi')
        with self.assertRaises(FormatError):
            formats.read(path)
        path = self.path('empty.cbz')
        with zipfile.ZipFile(path, 'w') as archive:
            archive.writestr('hello.txt', 'hi')
        with self.assertRaises(FormatError):
            formats.read(path)
        path = self.path('notxml.fb2')
        with open(path, 'w') as file:
            file.write('<html><body>no</body></html>')
        with self.assertRaises(FormatError):
            formats.read(path)

    def test_txt(self):
        path = self.path('Ada_Lark_-_Notes.txt')
        with open(path, 'w') as file:
            file.write('Some notes.\n')
        info = formats.read(path)
        self.assertEqual((info.title, info.authors, info.format), ('Notes', ['Ada Lark'],
                                                                   'txt'))
        with open(path, 'wb') as file:
            file.write(b'\0\1\2binary')
        with self.assertRaises(FormatError):
            formats.read(path)

    def test_unknown_suffix(self):
        with self.assertRaises(FormatError):
            formats.read(self.path('a.doc'))


if __name__ == '__main__':
    unittest.main()
