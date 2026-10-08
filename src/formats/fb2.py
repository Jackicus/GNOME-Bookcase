# SPDX-License-Identifier: GPL-2.0-or-later
# SPDX-FileCopyrightText: 2026 Jack Tully

"""FictionBook: FB2 (XML) and FBZ (a zip holding one .fb2): metadata and cover, read only.

    read(path) -> BookInfo

From description/title-info: book-title, author (first, middle and last name, or
nickname), genre (tags), lang, annotation (the description), date, sequence (series and
number), coverpage/image (the cover, a base64 <binary> in the file); from publish-info:
publisher, year, isbn. Elements are matched by local name, so files with a wrong or
missing namespace are read too.
"""

import base64
import binascii
import html
import zipfile

from lxml import etree

from . import BookInfo, FormatError, clean_isbn, image_type

XLINK_HREF = '{http://www.w3.org/1999/xlink}href'
MAX_SIZE = 256 * 1024 * 1024


def read(path):
    with open(path, 'rb') as file:
        head = file.read(4)
    if head.startswith(b'PK'):
        try:
            with zipfile.ZipFile(path) as archive:
                names = [m.filename for m in archive.infolist()
                         if m.filename.lower().endswith('.fb2') and m.file_size <= MAX_SIZE]
                if not names:
                    raise FormatError('An FBZ without an .fb2 inside')
                data = archive.read(names[0])
        except (zipfile.BadZipFile, EOFError) as error:
            raise FormatError(f'Broken FBZ: {error}') from error
    else:
        with open(path, 'rb') as file:
            data = file.read(MAX_SIZE)
    return parse(data)


def _local(element):
    return etree.QName(element).localname if isinstance(element.tag, str) else ''


def _child(element, name):
    if element is None:
        return None
    for child in element:
        if _local(child) == name:
            return child
    return None


def _children(element, name):
    if element is None:
        return []
    return [child for child in element if _local(child) == name]


def _text(element):
    return ' '.join(''.join(element.itertext()).split()) if element is not None else ''


def parse(data):
    parser = etree.XMLParser(resolve_entities=False, no_network=True, huge_tree=True,
                             recover=True)
    try:
        root = etree.fromstring(data, parser)
    except (etree.XMLSyntaxError, ValueError) as error:
        raise FormatError(f'Not an FB2 book: {error}') from error
    if root is None or _local(root) != 'FictionBook':
        raise FormatError('Not an FB2 book')
    description = _child(root, 'description')
    title_info = _child(description, 'title-info')
    publish_info = _child(description, 'publish-info')
    info = BookInfo()
    info.title = _text(_child(title_info, 'book-title'))
    for author in _children(title_info, 'author'):
        parts = [_text(_child(author, key)) for key in ('first-name', 'middle-name',
                                                         'last-name')]
        name = ' '.join(part for part in parts if part) or _text(_child(author, 'nickname'))
        if name:
            info.authors.append(name)
    info.tags = [_text(genre) for genre in _children(title_info, 'genre') if _text(genre)]
    info.language = _text(_child(title_info, 'lang'))
    annotation = _child(title_info, 'annotation')
    if annotation is not None:
        paragraphs = [_text(p) for p in annotation if _text(p)] or [_text(annotation)]
        info.description = ''.join(f'<p>{html.escape(p)}</p>' for p in paragraphs if p)
    date = _child(title_info, 'date')
    if date is not None:
        info.published = date.get('value') or _text(date)
    sequence = _child(title_info, 'sequence')
    if sequence is not None and (sequence.get('name') or '').strip():
        info.series = sequence.get('name').strip()
        try:
            info.series_index = float(sequence.get('number') or 0)
        except ValueError:
            info.series_index = 0.0
    if publish_info is not None:
        info.publisher = _text(_child(publish_info, 'publisher'))
        if not info.published:
            info.published = _text(_child(publish_info, 'year'))
        isbn = _text(_child(publish_info, 'isbn'))
        if isbn:
            info.identifiers['isbn'] = clean_isbn(isbn)
    document_info = _child(description, 'document-info')
    identifier = _text(_child(document_info, 'id'))
    if identifier:
        info.identifiers['fb2'] = identifier
    info.cover = _cover(root, title_info)
    return info


def _cover(root, title_info):
    image = _child(_child(title_info, 'coverpage'), 'image')
    if image is None:
        return None
    href = image.get(XLINK_HREF) or image.get('href') or ''
    for key, value in image.attrib.items():
        if not href and key.endswith('}href'):
            href = value
    wanted = href.lstrip('#')
    for binary in _children(root, 'binary'):
        if binary.get('id') == wanted:
            try:
                data = base64.b64decode(''.join((binary.text or '').split()))
            except (binascii.Error, ValueError):
                return None
            return data if image_type(data) else None
    return None
