# SPDX-License-Identifier: GPL-2.0-or-later
# SPDX-FileCopyrightText: 2026 Jack Tully

"""What a book file says about itself: metadata and cover, per format.

    info = formats.read(path)          # BookInfo; raises FormatError for a broken file
    formats.format_of(path)            # 'epub', 'mobi', 'azw3', 'pdf', 'cbz', 'cbr', 'fb2',
                                       # 'fbz', 'txt', 'kepub', or None when not a book
    formats.SUFFIXES                   # the file suffixes Bookcase takes, for file choosers

Each module (epub, mobi, pdf, comic, fb2, txt) has read(path) -> BookInfo; epub also has
write(path, info, cover=None, dest=None), which writes a copy (exporting.py uses it; the
user's file is never rewritten). A reader trusts what the file says and falls back on the
file name ("Author - Title.epub", "Title (Author).pdf") for what it does not say.
"""

import dataclasses


@dataclasses.dataclass
class BookInfo:
    title: str = ''
    authors: list = dataclasses.field(default_factory=list)
    series: str = ''
    series_index: float = 0.0
    tags: list = dataclasses.field(default_factory=list)
    publisher: str = ''
    published: str = ''  # 'YYYY', 'YYYY-MM' or 'YYYY-MM-DD'
    language: str = ''  # ISO 639-1 where known ('en'), else as given
    description: str = ''  # HTML
    identifiers: dict = dataclasses.field(default_factory=dict)  # {'isbn': '978…', 'uuid': …}
    cover: bytes | None = None  # the image's bytes (JPEG, PNG, …)
    format: str = ''


class FormatError(Exception):
    """A file that cannot be read as the book it claims to be."""
