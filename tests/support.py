# SPDX-License-Identifier: GPL-2.0-or-later
# SPDX-FileCopyrightText: 2026 Jack Tully

"""What model tests share: a library on a temporary path, and invented books and images.

    with temporary_library() as library:
        book_id = add_book(library, 'A Quiet Harbour', ('Ada Lark',))

    make_epub(path, title='A Quiet Harbour', authors=('Ada Lark',), cover=make_png(4, 6))
    make_png(4, 6, (200, 40, 40))           # a PNG's bytes; path=… also writes it there

The EPUBs are valid (mimetype first and stored, container.xml, an OPF, a nav document and an
NCX, XHTML chapters of invented filler text); the PNGs are built with zlib and struct, no GTK.
"""

import contextlib
import pathlib
import shutil
import struct
import tempfile
import uuid
import zipfile
import zlib
from xml.sax.saxutils import escape

from bookcase.formats import BookInfo
from bookcase.library import Library

FILLER = ('The lamps along the harbour wall were lit one by one as the tide came in. '
          'Nobody on the quay said much; the gulls said enough for everyone. '
          'A cart of crates rolled past the chandlery, and the smell of tar and rope '
          'followed it up the hill. ')


@contextlib.contextmanager
def temporary_library():
    """A Library in a new temporary directory, closed and removed afterwards. The directory
    is `library.path.parent`, free for a test's files."""
    directory = tempfile.mkdtemp(prefix='bookcase-test-')
    library = Library(pathlib.Path(directory) / 'library.sqlite')
    try:
        yield library
    finally:
        library.close()
        shutil.rmtree(directory, ignore_errors=True)


def add_book(library, title, authors=('Ada Lark',), path=None, fmt='epub', **fields):
    """Add an invented book (no file is written) and return its id. `fields` are BookInfo
    fields (series, series_index, tags, publisher, published, language, description,
    identifiers)."""
    info = BookInfo(title=title, authors=list(authors), format=fmt, **fields)
    if path is None:
        path = f'/invented/{uuid.uuid4().hex}.{fmt}'
    return library.add_book(info, str(path), hash=uuid.uuid4().hex, size=1000)


def make_png(width, height, rgb=(90, 120, 200), path=None):
    """The bytes of a width x height PNG of one colour, also written to `path` if given."""
    def chunk(kind, data):
        body = kind + data
        return struct.pack('>I', len(data)) + body + struct.pack('>I', zlib.crc32(body))

    row = b'\x00' + bytes(rgb) * width
    data = (b'\x89PNG\r\n\x1a\n'
            + chunk(b'IHDR', struct.pack('>IIBBBBB', width, height, 8, 2, 0, 0, 0))
            + chunk(b'IDAT', zlib.compress(row * height))
            + chunk(b'IEND', b''))
    if path is not None:
        pathlib.Path(path).write_bytes(data)
    return data


def _image_type(data):
    if data.startswith(b'\x89PNG'):
        return 'png', 'image/png'
    if data.startswith(b'GIF8'):
        return 'gif', 'image/gif'
    return 'jpg', 'image/jpeg'


def make_epub(path, title='A Quiet Harbour', authors=('Ada Lark',), series=None,
              series_index=None, language='en', description='', isbn=None, cover=None,
              chapters=3, epub3=True, tags=(), publisher='', published=''):
    """Write a small valid EPUB (3, or 2 with epub3=False) to `path` and return the path.
    `cover` is an image's bytes; `series` is written as Calibre's meta (and EPUB 3's
    belongs-to-collection); the identifier is a urn:uuid, with an ISBN beside it if given."""
    path = pathlib.Path(path)
    book_uuid = str(uuid.uuid5(uuid.NAMESPACE_URL, f'bookcase-test:{title}:{authors}'))
    version = '3.0' if epub3 else '2.0'
    meta = [f'<dc:identifier id="uid">urn:uuid:{book_uuid}</dc:identifier>',
            f'<dc:title>{escape(title)}</dc:title>',
            f'<dc:language>{escape(language)}</dc:language>']
    for number, author in enumerate(authors):
        if epub3:
            meta.append(f'<dc:creator id="creator{number}">{escape(author)}</dc:creator>')
            meta.append(f'<meta refines="#creator{number}" property="role" '
                        f'scheme="marc:relators">aut</meta>')
        else:
            meta.append(f'<dc:creator opf:role="aut">{escape(author)}</dc:creator>')
    if isbn:
        if epub3:
            meta.append(f'<dc:identifier id="isbn">urn:isbn:{escape(isbn)}</dc:identifier>')
        else:
            meta.append(f'<dc:identifier opf:scheme="ISBN">{escape(isbn)}</dc:identifier>')
    if description:
        meta.append(f'<dc:description>{escape(description)}</dc:description>')
    if publisher:
        meta.append(f'<dc:publisher>{escape(publisher)}</dc:publisher>')
    if published:
        meta.append(f'<dc:date>{escape(published)}</dc:date>')
    for tag in tags:
        meta.append(f'<dc:subject>{escape(tag)}</dc:subject>')
    if series:
        meta.append(f'<meta name="calibre:series" content="{escape(series)}"/>')
        if series_index is not None:
            meta.append(f'<meta name="calibre:series_index" content="{series_index}"/>')
        if epub3:
            meta.append(f'<meta property="belongs-to-collection" id="c1">{escape(series)}'
                        '</meta>')
            meta.append('<meta refines="#c1" property="collection-type">series</meta>')
            if series_index is not None:
                meta.append(f'<meta refines="#c1" property="group-position">{series_index}'
                            '</meta>')
    if epub3:
        meta.append('<meta property="dcterms:modified">2026-01-01T00:00:00Z</meta>')

    manifest = ['<item id="ncx" href="toc.ncx" media-type="application/x-dtbncx+xml"/>']
    if epub3:
        manifest.append('<item id="nav" href="nav.xhtml" media-type="application/xhtml+xml" '
                        'properties="nav"/>')
    cover_name = None
    if cover is not None:
        suffix, media_type = _image_type(cover)
        cover_name = f'cover.{suffix}'
        properties = ' properties="cover-image"' if epub3 else ''
        manifest.append(f'<item id="cover-image" href="{cover_name}" '
                        f'media-type="{media_type}"{properties}/>')
        meta.append('<meta name="cover" content="cover-image"/>')
    spine = []
    names = [f'chapter{number}.xhtml' for number in range(1, chapters + 1)]
    for number, name in enumerate(names, 1):
        manifest.append(f'<item id="c{number}" href="{name}" '
                        'media-type="application/xhtml+xml"/>')
        spine.append(f'<itemref idref="c{number}"/>')

    opf_ns = '' if epub3 else ' xmlns:opf="http://www.idpf.org/2007/opf"'
    opf = (f'<?xml version="1.0" encoding="UTF-8"?>\n'
           f'<package xmlns="http://www.idpf.org/2007/opf" version="{version}" '
           f'unique-identifier="uid">\n'
           f'<metadata xmlns:dc="http://purl.org/dc/elements/1.1/"{opf_ns}>\n'
           + '\n'.join(meta) + '\n</metadata>\n<manifest>\n' + '\n'.join(manifest)
           + '\n</manifest>\n<spine toc="ncx">\n' + '\n'.join(spine)
           + '\n</spine>\n</package>\n')

    points = ''.join(
        f'<navPoint id="p{number}" playOrder="{number}"><navLabel><text>Chapter {number}'
        f'</text></navLabel><content src="{name}"/></navPoint>'
        for number, name in enumerate(names, 1))
    ncx = ('<?xml version="1.0" encoding="UTF-8"?>\n'
           '<ncx xmlns="http://www.daisy.org/z3986/2005/ncx/" version="2005-1"><head>'
           f'<meta name="dtb:uid" content="urn:uuid:{book_uuid}"/></head>'
           f'<docTitle><text>{escape(title)}</text></docTitle>'
           f'<navMap>{points}</navMap></ncx>\n')
    items = ''.join(f'<li><a href="{name}">Chapter {number}</a></li>'
                    for number, name in enumerate(names, 1))
    nav = ('<?xml version="1.0" encoding="UTF-8"?>\n<!DOCTYPE html>\n'
           '<html xmlns="http://www.w3.org/1999/xhtml" '
           'xmlns:epub="http://www.idpf.org/2007/ops"><head><title>Contents</title></head>'
           f'<body><nav epub:type="toc"><h1>Contents</h1><ol>{items}</ol></nav></body>'
           '</html>\n')

    with zipfile.ZipFile(path, 'w') as archive:
        archive.writestr(zipfile.ZipInfo('mimetype'), 'application/epub+zip',
                         compress_type=zipfile.ZIP_STORED)
        deflated = zipfile.ZIP_DEFLATED
        archive.writestr('META-INF/container.xml',
                         '<?xml version="1.0" encoding="UTF-8"?>\n'
                         '<container version="1.0" '
                         'xmlns="urn:oasis:names:tc:opendocument:xmlns:container">'
                         '<rootfiles><rootfile full-path="OEBPS/content.opf" '
                         'media-type="application/oebps-package+xml"/></rootfiles>'
                         '</container>\n', compress_type=deflated)
        archive.writestr('OEBPS/content.opf', opf, compress_type=deflated)
        archive.writestr('OEBPS/toc.ncx', ncx, compress_type=deflated)
        if epub3:
            archive.writestr('OEBPS/nav.xhtml', nav, compress_type=deflated)
        if cover is not None:
            archive.writestr(f'OEBPS/{cover_name}', cover, compress_type=deflated)
        for number, name in enumerate(names, 1):
            paragraphs = ''.join(f'<p>{FILLER}</p>' for _ in range(3))
            archive.writestr(f'OEBPS/{name}',
                             '<?xml version="1.0" encoding="UTF-8"?>\n'
                             '<html xmlns="http://www.w3.org/1999/xhtml"><head>'
                             f'<title>Chapter {number}</title></head><body>'
                             f'<h1>Chapter {number}</h1>{paragraphs}</body></html>\n',
                             compress_type=deflated)
    return path
