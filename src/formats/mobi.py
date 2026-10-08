# SPDX-License-Identifier: GPL-2.0-or-later
# SPDX-FileCopyrightText: 2026 Jack Tully

"""MOBI, AZW and AZW3 (PalmDB containers): metadata and cover, read only.

    read(path) -> BookInfo

The metadata is in record 0: the full name, the locale and the EXTH block (100 author, 101
publisher, 103 description, 104 ISBN, 105 subject, 106 date, 113 ASIN, 503 updated title,
524 language, 201 cover offset, 202 thumbnail offset). The cover is the image record at the
first image index plus EXTH 201 (else 202), else the first image record there is. All
integers are big-endian; every offset is checked, so a broken file is a FormatError.
"""

import struct

from . import BookInfo, FormatError, clean_isbn, image_type

_STRINGS = {100: 'author', 101: 'publisher', 103: 'description', 104: 'isbn', 105: 'subject',
            106: 'date', 113: 'asin', 503: 'title', 524: 'language'}
_MULTIPLE = {'author', 'subject'}
NONE = 0xFFFFFFFF
MAX_SIZE = 1024 * 1024 * 1024
# Windows LANGID primary languages, the locale's low byte.
_LOCALES = {9: 'en', 12: 'fr', 7: 'de', 10: 'es', 16: 'it', 22: 'pt', 25: 'ru', 17: 'ja',
            4: 'zh', 19: 'nl', 29: 'sv', 20: 'no', 6: 'da', 11: 'fi', 21: 'pl', 5: 'cs',
            8: 'el', 31: 'tr', 1: 'ar', 13: 'he', 57: 'hi', 18: 'ko', 34: 'uk', 14: 'hu',
            24: 'ro', 3: 'ca'}


def read(path):
    with open(path, 'rb') as file:
        data = file.read(MAX_SIZE)
    return parse(data)


def parse(data):
    """The BookInfo of a MOBI file's bytes."""
    if len(data) < 78 + 8 or data[60:68] not in (b'BOOKMOBI', b'TEXtREAd'):
        raise FormatError('Not a MOBI book')
    count = struct.unpack_from('>H', data, 76)[0]
    if count == 0 or 78 + 8 * count > len(data):
        raise FormatError('Broken MOBI book: bad record list')
    offsets = [struct.unpack_from('>I', data, 78 + 8 * i)[0] for i in range(count)]
    offsets.append(len(data))
    for start, end in zip(offsets, offsets[1:], strict=False):
        if start > end or start > len(data):
            raise FormatError('Broken MOBI book: bad record offsets')

    def record(index):
        return data[offsets[index]:offsets[index + 1]]

    info = BookInfo()
    record0 = record(0)
    info.title = data[:32].split(b'\0', 1)[0].decode('latin-1').replace('_', ' ')
    if len(record0) < 24 or record0[16:20] != b'MOBI':
        return info  # PalmDOC: the database name is all there is
    header_length, _kind, encoding = struct.unpack_from('>III', record0, 20)
    codec = 'utf-8' if encoding == 65001 else 'cp1252'

    def field(offset):
        if offset + 4 > min(len(record0), 16 + header_length):
            return None
        return struct.unpack_from('>I', record0, offset)[0]

    name_offset, name_length = field(84), field(88)
    if name_offset is not None and name_length is not None \
            and name_offset + name_length <= len(record0) and name_length:
        info.title = record0[name_offset:name_offset + name_length].decode(codec, 'replace')
    locale = field(92)
    if locale:
        info.language = _LOCALES.get(locale & 0xFF, '')
    first_image = field(108)
    exth = _exth(record0, header_length, codec) if (field(128) or 0) & 0x40 else {}

    if exth.get('title'):
        info.title = exth['title']
    info.authors = exth.get('author', [])
    info.publisher = exth.get('publisher', '')
    info.description = exth.get('description', '')
    info.published = exth.get('date', '')
    info.tags = [tag for subject in exth.get('subject', []) for tag in _split_subject(subject)]
    if exth.get('language'):
        info.language = exth['language']
    if exth.get('isbn'):
        info.identifiers['isbn'] = clean_isbn(exth['isbn'])
    if exth.get('asin'):
        info.identifiers['amazon'] = exth['asin']

    if first_image not in (None, 0, NONE) and first_image < count:
        for index in (exth.get(201), exth.get(202)):
            if index not in (None, NONE) and first_image + index < count:
                image = record(first_image + index)
                if image_type(image):
                    info.cover = image
                    break
        else:
            for index in range(first_image, count):
                image = record(index)
                if image_type(image):
                    info.cover = image
                    break
    return info


def _split_subject(subject):
    return [part.strip() for part in subject.split(';') if part.strip()]


def _exth(record0, header_length, codec):
    result = {}
    start = 16 + header_length
    if record0[start:start + 4] != b'EXTH' or start + 12 > len(record0):
        return result
    entries = struct.unpack_from('>I', record0, start + 8)[0]
    position = start + 12
    for _entry in range(entries):
        if position + 8 > len(record0):
            break
        kind, length = struct.unpack_from('>II', record0, position)
        if length < 8 or position + length > len(record0):
            break
        value = record0[position + 8:position + length]
        position += length
        if kind in (201, 202) and len(value) == 4:
            result[kind] = struct.unpack('>I', value)[0]
        elif kind in _STRINGS:
            name = _STRINGS[kind]
            text = value.decode(codec, 'replace').strip('\0').strip()
            if name in _MULTIPLE:
                result.setdefault(name, []).append(text)
            else:
                result[name] = text
    return result
