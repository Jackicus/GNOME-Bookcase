# SPDX-License-Identifier: GPL-2.0-or-later
# SPDX-FileCopyrightText: 2026 Jack Tully

"""Copies of books that carry the library's metadata (Export, and sending to a device).

    export_copy(library, covers, book_id, dest_dir, format=None, embed=True, name=None,
                replace=False) -> path
    book_info(library, covers, book) -> formats.BookInfo    the library's view of a book
    copy_name(book, suffix) -> str                          'Title - Author.epub'
    ExportError                                             str() is a sentence for the user

export_copy() copies the book's file in `format` (else its reading format) into dest_dir as
'Title - Author.ext' (or `name`, a file name with or without the suffix, made safe: never a
path out of dest_dir), with ' (2)' when taken unless `replace` (which never replaces the
book's own file). An EPUB (or kepub) gets the library's metadata and cover written
into the copy (formats.epub.write); other formats, and an EPUB that cannot be rewritten
(DRM, broken), are copied as they are. The library's file is never changed.
"""

import contextlib
import logging
import os
import shutil
import tempfile
from gettext import gettext as _

from . import formats
from .formats import BookInfo, FormatError, epub
from .importing import safe_name

log = logging.getLogger(__name__)

EMBEDDABLE = ('epub', 'kepub')


class ExportError(Exception):
    """A copy that could not be made; str(error) is a translated sentence."""


def book_info(library, covers, book):
    """A BookInfo of a Book's library metadata, with its cover's bytes."""
    if isinstance(book, int):
        book = library.book(book)
    identifiers = dict(book.identifiers)
    identifiers.setdefault('uuid', book.uuid)
    return BookInfo(
        title=book.title, authors=list(book.authors), series=book.series,
        series_index=book.series_index, tags=list(book.tags), publisher=book.publisher,
        published=book.published, language=book.language, description=book.description,
        identifiers=identifiers, cover=covers.data(book) if covers is not None else None)


def copy_name(book, suffix):
    """'Title - Author.ext', safe as a file name."""
    title = book.title or _('Untitled')
    stem = f'{title} - {book.authors[0]}' if book.authors else title
    return safe_name(stem, limit=200) + suffix


def _free(path):
    stem, suffix = path, ''
    for book_suffix in formats.SUFFIXES:
        if path.lower().endswith(book_suffix):
            stem, suffix = path[:-len(book_suffix)], path[-len(book_suffix):]
            break
    number = 2
    while os.path.lexists(path):
        path = f'{stem} ({number}){suffix}'
        number += 1
    return path


def _same_file(a, b):
    try:
        return os.path.samefile(a, b)
    except OSError:
        return os.path.abspath(a) == os.path.abspath(b)


def export_copy(library, covers, book_id, dest_dir, format=None, embed=True, name=None,
                replace=False):
    book = library.book(book_id)
    if book is None:
        raise ExportError(_('The book is no longer in the library'))
    files = [file for file in library.files(book_id) if not file.missing]
    if format is not None:
        files = [file for file in files if file.format == format]
    if not files or not os.path.isfile(files[0].path):
        raise ExportError(_('The book’s file cannot be found'))
    source = files[0]
    suffix = formats.suffix_of(source.path)
    if name is None:
        name = copy_name(book, suffix)
    else:
        # Always one file name in dest_dir: a name with '/' or '..' goes nowhere else.
        if name.lower().endswith(suffix):
            name = name[:len(name) - len(suffix)]
        name = safe_name(name, limit=200) + suffix
    os.makedirs(dest_dir, exist_ok=True)
    dest = os.path.join(str(dest_dir), name)
    if not replace:
        dest = _free(dest)
    elif os.path.lexists(dest) and _same_file(dest, source.path):
        raise ExportError(_('The copy would replace the book’s own file'))
    if embed and source.format in EMBEDDABLE:
        try:
            info = book_info(library, covers, book)
            epub.write(source.path, info, cover=info.cover, dest=dest)
            return dest
        except (FormatError, ValueError) as error:
            log.info('Copying %s as it is: %s', source.path, error)
    folder = os.path.dirname(os.path.abspath(dest))
    fd, temporary = tempfile.mkstemp(dir=folder, prefix='.part-')
    os.close(fd)
    try:
        shutil.copyfile(source.path, temporary)
        os.replace(temporary, dest)
    except OSError as error:
        with contextlib.suppress(OSError):
            os.unlink(temporary)
        raise ExportError(_('The book could not be copied: {error}').format(error=error.strerror
                                                                        or error)) from error
    return dest
