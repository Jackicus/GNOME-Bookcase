# SPDX-License-Identifier: GPL-2.0-or-later
# SPDX-FileCopyrightText: 2026 Jack Tully

"""Plain text: a book known by its file name ("Author - Title.txt").

    read(path) -> BookInfo
    decode(data) -> str        UTF-8 (with or without a BOM), UTF-16 with a BOM, else cp1252

A file with NUL bytes in its first block is not text (FormatError), unless it is UTF-16.
"""

import codecs

from . import BookInfo, FormatError

SAMPLE = 64 * 1024


def read(path):
    with open(path, 'rb') as file:
        sample = file.read(SAMPLE)
    if b'\0' in sample and not sample.startswith((codecs.BOM_UTF16_LE, codecs.BOM_UTF16_BE)):
        raise FormatError('Not a text file')
    return BookInfo()


def decode(data):
    if data.startswith(codecs.BOM_UTF8):
        return data[3:].decode('utf-8', 'replace')
    if data.startswith((codecs.BOM_UTF16_LE, codecs.BOM_UTF16_BE)):
        return data.decode('utf-16', 'replace')
    try:
        return data.decode('utf-8')
    except UnicodeDecodeError:
        return data.decode('cp1252', 'replace')
