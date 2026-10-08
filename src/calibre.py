# SPDX-License-Identifier: GPL-2.0-or-later
# SPDX-FileCopyrightText: 2026 Jack Tully

"""Reading a Calibre library (its metadata.db) to link its books in place. Read only:
calibre_write.py is the one writer, for the libraries the user opted in.

    calibre.is_library(path) -> bool       a folder with a metadata.db
    calibre.read_library(path) -> [CalibreBook]
    CalibreError                           not a Calibre library, or one we cannot read
    LINK_LOCK                              held by a link or rescan (importing.py) and by
                                           calibre_write.flush(), so they take turns

The database is opened read-only (`mode=ro`); when Calibre holds it locked, a copy taken to
a temporary file is read instead. Nothing in the library folder is written here.

A CalibreBook carries a formats.BookInfo (title, authors in Calibre's link order, series and
index, tags, publisher, published, languages (the first), comments as the description,
identifiers, Calibre's uuid as 'uuid'; a '|' in an author's name is Calibre's stored ','),
Calibre's author_sort, the rating (0-10), the book's files ({format:
path}, from the data table: `<library>/<book path>/<name>.<format lowercased>`, only those
that exist), the cover.jpg path when there is one, and Calibre's last_modified text, which a
rescan compares with the Library's source_modified to know which books Calibre changed.
importing.Importer.link_calibre() adds them.
"""

import contextlib
import dataclasses
import logging
import os
import shutil
import sqlite3
import tempfile
import threading
from urllib.parse import quote

from .formats import BookInfo, normalize_date, normalize_language

log = logging.getLogger(__name__)

LINK_LOCK = threading.Lock()

# Calibre's format names to ours.
FORMATS = {'EPUB': 'epub', 'KEPUB': 'kepub', 'AZW3': 'azw3', 'MOBI': 'mobi', 'AZW': 'mobi',
           'PDF': 'pdf', 'CBZ': 'cbz', 'CBR': 'cbr', 'FB2': 'fb2', 'FBZ': 'fbz', 'TXT': 'txt'}


@dataclasses.dataclass
class CalibreBook:
    id: int
    info: BookInfo
    rating: int = 0
    files: dict = dataclasses.field(default_factory=dict)  # {'epub': path, …}
    cover_path: str | None = None
    last_modified: str = ''  # as Calibre wrote it: '2026-10-08 09:12:44.123456+00:00'
    folder: str = ''  # the book's own folder
    author_sort: str = ''  # Calibre's books.author_sort


class CalibreError(Exception):
    """Not a Calibre library, or one that cannot be read."""


def is_library(path):
    return os.path.isfile(os.path.join(str(path), 'metadata.db'))


def _collate(a, b):
    a, b = (a or '').casefold(), (b or '').casefold()
    return (a > b) - (a < b)


def _connect(path):
    db = sqlite3.connect(path, uri=True, timeout=1)
    db.create_collation('PYNOCASE', _collate)
    db.create_collation('icucollate', _collate)
    db.create_function('title_sort', 1, lambda t: t, deterministic=True)
    db.create_function('uuid4', 0, lambda: '')
    db.create_function('books_list_filter', 1, lambda x: 1)
    return db


@contextlib.contextmanager
def _open(folder):
    """A read-only connection to the library's metadata.db (or to a copy of it)."""
    database = os.path.join(folder, 'metadata.db')
    if not os.path.isfile(database):
        raise CalibreError(f'No metadata.db in {folder}')
    uri = 'file:' + quote(os.path.abspath(database)) + '?mode=ro'
    db = None
    try:
        db = _connect(uri)
        db.execute('SELECT count(*) FROM books').fetchone()
    except sqlite3.Error as error:
        if db is not None:
            db.close()
        db = None
        log.info('Reading a copy of %s: %s', database, error)
    if db is not None:
        try:
            yield db
        finally:
            db.close()
        return
    with tempfile.TemporaryDirectory(prefix='bookcase-calibre-') as temporary:
        copy = os.path.join(temporary, 'metadata.db')
        shutil.copyfile(database, copy)
        for suffix in ('-wal', '-journal'):
            if os.path.exists(database + suffix):
                with contextlib.suppress(OSError):
                    shutil.copyfile(database + suffix, copy + suffix)
        db = None
        try:
            db = _connect('file:' + quote(copy) + '?mode=ro')
            db.execute('SELECT count(*) FROM books').fetchone()
        except sqlite3.Error as error:
            if db is not None:
                db.close()
            raise CalibreError(f'Cannot read {database}: {error}') from error
        try:
            yield db
        finally:
            db.close()


def _grouped(db, sql):
    result = {}
    for book, value in db.execute(sql):
        if value is not None:
            result.setdefault(book, []).append(value)
    return result


def read_library(path):
    """Every book of the Calibre library at path, in Calibre's id order."""
    folder = os.path.abspath(str(path))
    try:
        with _open(folder) as db:
            return _read(db, folder)
    except sqlite3.Error as error:
        raise CalibreError(f'Cannot read the Calibre library: {error}') from error


def _read(db, folder):
    authors = _grouped(db, 'SELECT l.book, a.name FROM books_authors_link l '
                           'JOIN authors a ON a.id = l.author ORDER BY l.book, l.id')
    tags = _grouped(db, 'SELECT l.book, t.name FROM books_tags_link l '
                        'JOIN tags t ON t.id = l.tag ORDER BY l.book, t.name')
    series = _grouped(db, 'SELECT l.book, s.name FROM books_series_link l '
                          'JOIN series s ON s.id = l.series')
    publishers = _grouped(db, 'SELECT l.book, p.name FROM books_publishers_link l '
                              'JOIN publishers p ON p.id = l.publisher')
    languages = _grouped(db, 'SELECT l.book, g.lang_code FROM books_languages_link l '
                             'JOIN languages g ON g.id = l.lang_code '
                             'ORDER BY l.book, l.item_order')
    ratings = _grouped(db, 'SELECT l.book, r.rating FROM books_ratings_link l '
                           'JOIN ratings r ON r.id = l.rating')
    comments = _grouped(db, 'SELECT book, text FROM comments')
    identifiers = {}
    for book, kind, value in db.execute('SELECT book, type, val FROM identifiers'):
        if kind and value:
            identifiers.setdefault(book, {})[kind.lower()] = value
    data = {}
    for book, format, name in db.execute('SELECT book, format, name FROM data'):
        if format and name:
            data.setdefault(book, []).append((format.upper(), name))

    books = []
    rows = db.execute('SELECT id, title, pubdate, series_index, path, uuid, has_cover, '
                      'last_modified, author_sort FROM books ORDER BY id')
    for book_id, title, pubdate, index, book_path, uuid, has_cover, modified, asort in rows:
        book_folder = os.path.join(folder, *(book_path or '').split('/'))
        info = BookInfo(
            title=title or '',
            authors=[name.replace('|', ',') for name in authors.get(book_id, [])],
            series=(series.get(book_id) or [''])[0],
            series_index=float(index or 0) if series.get(book_id) else 0.0,
            tags=tags.get(book_id, []), publisher=(publishers.get(book_id) or [''])[0],
            published=normalize_date(pubdate or ''),
            language=normalize_language((languages.get(book_id) or [''])[0]),
            description=(comments.get(book_id) or [''])[0],
            identifiers=dict(identifiers.get(book_id, {})))
        if uuid:
            info.identifiers['uuid'] = uuid  # the book keeps Calibre's uuid
        if info.authors == ['Unknown']:  # Calibre's word for none
            info.authors = []
        files = {}
        for format, name in data.get(book_id, []):
            ours = FORMATS.get(format)
            file = os.path.join(book_folder, f'{name}.{format.lower()}')
            if ours and ours not in files and os.path.isfile(file):
                files[ours] = file
        cover = os.path.join(book_folder, 'cover.jpg')
        rating = (ratings.get(book_id) or [0])[0] or 0
        books.append(CalibreBook(
            id=book_id, info=info, rating=max(0, min(10, int(rating))), files=files,
            cover_path=cover if has_cover and os.path.isfile(cover) else None,
            last_modified=str(modified or ''), folder=book_folder, author_sort=asort or ''))
    return books
