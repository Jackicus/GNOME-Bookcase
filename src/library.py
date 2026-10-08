# SPDX-License-Identifier: GPL-2.0-or-later
# SPDX-FileCopyrightText: 2026 Jack Tully

"""The library: every book Bookcase knows, in one SQLite database (schema.py).

    library = Library(path)                 # creates the schema in a new file
    library.close()
    library_path()                          # $BOOKCASE_DATA_DIR or the user data dir's
                                            # bookcase[-devel]/library.sqlite
    worker = library.open_worker()          # a Library on the same file, for a thread
    library.connect('changed', handler)     # handler(library, kind): 'books', 'files',
                                            # 'shelves', 'annotations', 'progress', 'folders'
    library.notify_changed('books', 'files')  # emit `changed` for each kind; returns False,
                                            # so it can be given to GLib.idle_add directly

`changed` is emitted once per kind when the outermost undoable block ends (at once for a
change outside one). Book.missing comes from the files, so a list showing it listens to
'files' as well; set_missing() emits both.

Threads: a Library is used by one thread at a time. A thread doing long work (importing,
scanning, Calibre) opens its own with `worker = library.open_worker()`, writes through it
(WAL mode: the main thread keeps reading; a writer waits up to 10 s for another's
transaction, so a worker keeps each transaction short, a book or a few at a time), closes it
when done, and tells the main library's listeners through the main loop:

    GLib.idle_add(library.notify_changed, 'books', 'files')

(a worker's own `changed` signals fire in its thread, where nothing should listen). The
main library keeps no cache, so it sees the worker's rows at once. A worker's changes are
not on the main library's undo stack.

Books (a Book is a frozen dataclass; lists are tuples):

    library.book(book_id)                   # Book or None
    library.books(query='', shelf=None, status=None, author=None, series=None, tag=None,
                  sort='added', descending=None, limit=None, offset=0)   # [Book]
    library.book_ids(...same filters...)    # [int], for large grids
    library.count(...same filters...)       # int (sort, limit and offset not taken)
    library.continue_reading(limit=12)      # status 'reading', most recently read first
    library.recently_added(limit=12)
    library.add_book(info, path, *, hash, size, source='library', source_key=None,
                     source_modified='')    # a new book from a formats.BookInfo and its
                                            # first file; returns the book id (undoable)
    library.add_file(book_id, path, *, hash, size, format=None)   # another format of the
                                            # same book; returns the file id (undoable)
    library.files(book_id)                  # [BookFile], preferred reading format first
    library.reading_file(book_id)           # the BookFile to open, or None
    library.find_by_hash(hash)              # book id or None
    library.find_file(path)                 # BookFile or None
    library.find_by_source_key(source, key) # book id or None ('calibre', '42')
    library.find_similar(title, authors)    # [book id]: same normalised title and an author
    library.find_by_identifier(kind, value) # book id or None ('isbn', '…'; 'uuid' is the book's)
                                            # in common (or no authors on one side)
    library.update_book(book_id, **fields)  # undoable 'Edit Book'
    library.update_books(book_ids, **fields)  # bulk; also add_tags=, remove_tags=
    library.remove_books(book_ids)          # undoable; files stay where they are
    library.duplicates()                    # [[book id]]: the same title, an author shared
    library.richest(book_ids)               # the one of them to keep in a merge
    library.merge_books(keep_id, other_ids) # undoable 'Merge Books': one book of them all

Books opened from outside without adding them (source OPENED) have rows, so the reader
keeps their place and highlights, but no list, count, search or group shows them:

    library.add_opened(info, path, hash=…, size=…)   # its id; no undo step
    library.opened_ids()                    # {book id}
    library.keep_book(book_id, source='library', path=None, hash=None, size=None,
                      format=None)          # undoable 'Add to Library'
    library.authors() / series() / tags()   # [Group(id, name, sort, count)], books only
    library.publishers() / languages()      # [str]

Filters: `query` is search.py's syntax; `shelf` a shelf (a manual one's books, a smart one's
search); `status` one of STATUSES; `author`, `series`, `tag` a Group's id. Each takes the id
or the object. Sorts (SORTS): 'added', 'title', 'author', 'series' (series then number;
books in none last), 'published' (undated last), 'last-read', 'rating'; `descending=None`
is the sort's own direction (DESCENDING: newest, latest and best first; titles and names
A to Z).

Editable fields: title, sort_title, authors (sequence of names, first is the main author),
author_sort, series (name or ''), series_index (float), tags (sequence), publisher,
published ('YYYY', 'YYYY-MM' or 'YYYY-MM-DD'), language (ISO 639 code), description (HTML),
rating (0-10, Calibre's half stars; 0 none), identifiers ({'isbn': …, 'google': …}),
has_cover (set through covers.py), status, source_key, source_modified. A new title gives a
new sort_title, new authors a new author_sort, unless the same call sets them. Passing
has_cover at all (True again for a replaced cover) adds one to cover_version; undo puts
both back. An unknown field raises ValueError.

Reading state (not undo steps; `changed('progress')`):

    library.set_progress(book_id, fraction, location)  # location: the reader's CFI; an
                                            # unread book becomes 'reading'
    library.set_status(book_ids, status)    # undoable: 'unread', 'reading', 'finished'
    library.log_session(book_id, started, seconds, start_fraction, end_fraction)
    library.sessions(book_id=None, since=None)          # [Session], oldest first
    library.finished(since=None, until=None)            # [(book_id, when)] marked
                                            # finished (when: kept while read again,
                                            # cleared by 'unread'), oldest first
    library.page_counts()                   # {book_id: pages} where books.pages is known

Annotations ('highlight', 'bookmark'; a highlight may carry a note):

    library.annotations(book_id, kind=None) # [Annotation], in reading order (by position)
    library.add_annotation(book_id, kind, location, *, text='', note='', color='yellow',
                           position=0.0)    # undoable; returns its id
    library.update_annotation(annotation_id, note=None, color=None)   # undoable
    library.remove_annotation(annotation_id)  # undoable
    library.set_annotation_location(annotation_id, location, position=None)   # a CFI for
                                            # one imported without (not an undo step)

Shelves (a manual shelf holds books; a smart shelf has a search query instead):

    library.shelves()                       # [Shelf], in the user's order
    library.shelf(shelf_id)                 # Shelf or None
    library.add_shelf(name, query=None)     # undoable; returns its id
    library.update_shelf(shelf_id, name=None, query=None)
    library.move_shelf(shelf_id, index)     # undoable; to that place in the order
    library.remove_shelf(shelf_id)          # undoable; its books stay in the library
    library.add_to_shelf(shelf_id, book_ids) / remove_from_shelf(shelf_id, book_ids)
    library.book_shelves(book_id)           # [Shelf] the manual shelves holding it

Folders (where books live: 'library' is the folder added books are copied into, 'watched' a
folder read in place, 'calibre' a linked Calibre library). A file under a folder's path
belongs to it (the longest match), whenever it is added or found again:

    library.folders()                       # [Folder]
    library.add_folder(path, kind)          # returns its id (the existing one's for a path
                                            # already added); undoable
    library.remove_folder(folder_id, remove_books=False)   # undoable; with remove_books,
                                            # the books with no file elsewhere go too
    library.folder_files(folder_id)         # [BookFile] under it, for a rescan
    library.in_calibre(book_id)             # a file of it is in a linked Calibre library
    library.set_file_path(file_id, path, hash=None, size=None)   # a moved file found
                                            # again (not undoable)
    library.set_missing(file_ids, missing=True)            # not undoable

Undo:

    with library.undoable(_('Edit Book')):  # nests; one step per outermost block, one
        ...                                 # transaction (rolled back on an exception)
    library.undo()                          # the label put back, or None
    library.can_undo()
    library.undo_label                      # what undo() would put back, or None

Each write inside a block records its inverse (the old values of the columns it changed,
the rows it deleted, the keys of the rows it inserted); undo() plays them back newest first.
So undoing an edit leaves alone what changed since outside undo (reading progress).
"""

import contextlib
import dataclasses
import logging
import os
import pathlib
import sqlite3
import time
import uuid as uuidlib
from gettext import gettext as _
from gettext import ngettext

from gi.repository import GLib, GObject

from bookcase import schema, search, titles

log = logging.getLogger(__name__)

STATUSES = ('unread', 'reading', 'finished')
KINDS = ('highlight', 'bookmark')
COLORS = ('yellow', 'green', 'blue', 'pink', 'purple')
FOLDER_KINDS = ('library', 'watched', 'calibre')
# The order a book's formats are offered for reading in.
READING_ORDER = ('epub', 'kepub', 'azw3', 'mobi', 'fb2', 'fbz', 'cbz', 'pdf', 'txt')
SORTS = ('added', 'title', 'author', 'series', 'published', 'last-read', 'rating')
DESCENDING = {'added': True, 'title': False, 'author': False, 'series': False,
              'published': True, 'last-read': True, 'rating': True}
UNDO_LIMIT = 50
# A book opened from outside without being added (main.py's open_path): it has a row, so
# the reader keeps its place and highlights, but no list, count or group shows it.
OPENED = 'opened'
HIDDEN = f"b.source != '{OPENED}'"
CHUNK = 500  # ids per IN (…) list

# Which `changed` kind a write to each table is.
TABLE_KINDS = {
    'books': 'books', 'authors': 'books', 'book_authors': 'books', 'series': 'books',
    'tags': 'books', 'book_tags': 'books', 'identifiers': 'books', 'files': 'files',
    'shelves': 'shelves', 'shelf_books': 'shelves', 'annotations': 'annotations',
    'sessions': 'progress', 'folders': 'folders',
}
# What keeps undo from deleting a name row (made by the step it puts back) that a book uses.
NAME_IN_USE = {
    'authors': ' AND NOT EXISTS (SELECT 1 FROM book_authors WHERE author_id = authors.id)',
    'series': ' AND NOT EXISTS (SELECT 1 FROM books WHERE series_id = series.id)',
    'tags': ' AND NOT EXISTS (SELECT 1 FROM book_tags WHERE tag_id = tags.id)',
}
# Fields update_book() writes straight to a books column.
COLUMN_FIELDS = ('title', 'sort_title', 'author_sort', 'series_index', 'publisher', 'published',
                 'language', 'description', 'rating', 'status', 'has_cover', 'source_key',
                 'source_modified', 'source_values')
EDITABLE = set(COLUMN_FIELDS) | {'authors', 'series', 'tags', 'identifiers'}

BOOK_SELECT = ('SELECT b.id, b.uuid, b.title, b.sort_title, b.author_sort, s.name AS series, '
               'b.series_index, b.publisher, b.published, b.language, b.description, b.rating, '
               'b.added, b.modified, b.status, b.progress, b.location, b.last_read, '
               'b.has_cover, b.cover_version, b.source, b.source_key, b.source_modified, '
               'b.source_values '
               'FROM books b LEFT JOIN series s ON s.id = b.series_id')


def library_path(development=False):
    """Where the library file lives: $BOOKCASE_DATA_DIR/library.sqlite, else the user data
    directory's bookcase/ (bookcase-devel/ for the development build)."""
    override = os.environ.get('BOOKCASE_DATA_DIR')
    if override:
        return pathlib.Path(override) / 'library.sqlite'
    name = 'bookcase-devel' if development else 'bookcase'
    return pathlib.Path(GLib.get_user_data_dir()) / name / 'library.sqlite'


def format_of(path):
    """A file's format from its name: 'epub', 'kepub' (for .kepub.epub), 'pdf', …"""
    name = pathlib.Path(path).name.lower()
    if name.endswith('.kepub.epub') or name.endswith('.kepub'):
        return 'kepub'
    return pathlib.PurePath(name).suffix.lstrip('.')


def _format_rank(fmt):
    return READING_ORDER.index(fmt) if fmt in READING_ORDER else len(READING_ORDER)


def _chunks(ids):
    ids = list(ids)
    for start in range(0, len(ids), CHUNK):
        yield ids[start:start + CHUNK]


def _id(value):
    """An id from an id or a record that has one."""
    return getattr(value, 'id', value)


@dataclasses.dataclass(frozen=True)
class Book:
    id: int
    uuid: str
    title: str
    sort_title: str
    authors: tuple = ()
    author_sort: str = ''
    series: str = ''
    series_index: float = 0.0
    tags: tuple = ()
    publisher: str = ''
    published: str = ''
    language: str = ''
    description: str = ''
    rating: int = 0
    identifiers: dict = dataclasses.field(default_factory=dict)
    formats: tuple = ()  # ('epub', 'pdf'), reading order
    added: float = 0.0  # Unix time
    modified: float = 0.0
    status: str = 'unread'
    progress: float = 0.0  # 0-1
    location: str = ''  # the reader's CFI
    last_read: float = 0.0
    has_cover: bool = False
    cover_version: int = 0  # bumped whenever the cover changes (thumbnail caches key on it)
    missing: bool = False  # no file of it can be found
    source: str = 'library'  # the folder kind it came from
    source_key: str | None = None  # its key in the source (a Calibre book id)
    source_modified: str = ''  # when the source last changed it, as the source says
    source_values: str = ''  # what the source last said of its details (JSON)

    @property
    def author(self):
        """The authors, for a label: 'Ada Lark', 'Ada Lark and Ben Ross', 'Ada Lark and 2
        others' (translated)."""
        if not self.authors:
            return _('Unknown Author')
        if len(self.authors) == 1:
            return self.authors[0]
        if len(self.authors) == 2:
            return _('{first} and {second}').format(first=self.authors[0],
                                                    second=self.authors[1])
        others = len(self.authors) - 1
        return ngettext('{first} and {count} other', '{first} and {count} others',
                        others).format(first=self.authors[0], count=others)


@dataclasses.dataclass(frozen=True)
class BookFile:
    id: int
    book_id: int
    path: str
    format: str  # 'epub', 'pdf', …
    size: int
    hash: str  # importing.partial_md5()
    missing: bool = False


@dataclasses.dataclass(frozen=True)
class Group:
    id: int
    name: str
    sort: str
    count: int


@dataclasses.dataclass(frozen=True)
class Annotation:
    id: int
    book_id: int
    kind: str
    location: str  # CFI
    position: float  # 0-1, for ordering and display
    text: str
    note: str
    color: str
    created: float
    modified: float


@dataclasses.dataclass(frozen=True)
class Shelf:
    id: int
    name: str
    query: str | None  # a smart shelf's search; None for a manual shelf
    position: int
    count: int


@dataclasses.dataclass(frozen=True)
class Folder:
    id: int
    path: str
    kind: str


@dataclasses.dataclass(frozen=True)
class Session:
    id: int
    book_id: int
    started: float
    seconds: float
    start_fraction: float
    end_fraction: float


class LibraryError(Exception):
    """A failure to tell the user in a sentence (str(error) is translated)."""


def _file(row):
    return BookFile(id=row['id'], book_id=row['book_id'], path=row['path'], format=row['format'],
                    size=row['size'], hash=row['hash'], missing=bool(row['missing']))


def _annotation(row):
    return Annotation(**{field.name: row[field.name]
                         for field in dataclasses.fields(Annotation)})


class Library(GObject.Object):
    """See the module docstring."""

    __gtype_name__ = 'BookcaseLibrary'

    __gsignals__ = {
        'changed': (GObject.SignalFlags.RUN_FIRST, None, (str,)),
    }

    def __init__(self, path, worker=False):
        super().__init__()
        self.path = pathlib.Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.worker = worker
        # A worker is made in one thread and used in another, never in two at once.
        self.db = sqlite3.connect(self.path, isolation_level=None, timeout=10,
                                  check_same_thread=False)
        self.db.row_factory = sqlite3.Row
        self.db.execute('PRAGMA journal_mode = WAL')
        self.db.execute('PRAGMA synchronous = NORMAL')
        self.db.execute('PRAGMA busy_timeout = 10000')
        self.db.execute('PRAGMA foreign_keys = OFF')
        search.register_functions(self.db)
        try:
            schema.apply(self.db)
        except BaseException:
            self.db.close()
            raise
        if not worker:
            self._prune()
        self._undo = []  # [{'label', 'ops'}], the newest last
        self._open = None  # the outermost undoable block's step while one runs
        self._changes = set()

    def _prune(self):
        """Drop authors, series and tags no book uses (only here: undo may need them)."""
        self.db.execute('BEGIN IMMEDIATE')
        self.db.execute('DELETE FROM authors WHERE id NOT IN '
                        '(SELECT author_id FROM book_authors)')
        self.db.execute('DELETE FROM series WHERE id NOT IN (SELECT series_id FROM books '
                        'WHERE series_id IS NOT NULL)')
        self.db.execute('DELETE FROM tags WHERE id NOT IN (SELECT tag_id FROM book_tags)')
        self.db.execute('COMMIT')

    def open_worker(self):
        """A Library on the same file, for another thread; see the module docstring."""
        return Library(self.path, worker=True)

    @property
    def closed(self):
        """True once close() has run: a late signal handler (a widget outliving the app's
        shutdown) checks it before asking anything."""
        return self.db is None

    def close(self):
        if self.db is not None:
            self.db.close()
            self.db = None

    def notify_changed(self, *kinds):
        """Emit `changed` for each kind (a worker's changes, from the main loop). Returns
        False, GLib.SOURCE_REMOVE, so GLib.idle_add can call it."""
        for kind in kinds:
            self.emit('changed', kind)
        return False

    # -- undo -----------------------------------------------------------------------------

    @contextlib.contextmanager
    def undoable(self, label):
        """One undo step and one transaction: every change inside is put back together by
        undo(). Nested blocks join the outermost. `changed` fires (once per kind) when the
        outermost block ends. A label of None makes a transaction that is no undo step."""
        if self._open is not None:
            yield self._open
            return
        step = {'label': label, 'ops': []}
        self._open = step
        self.db.execute('BEGIN IMMEDIATE')
        try:
            yield step
        except BaseException:
            self.db.execute('ROLLBACK')
            self._open = None
            self._changes.clear()
            raise
        self.db.execute('COMMIT')
        self._open = None
        if label is not None and step['ops']:
            self._undo.append(step)
            del self._undo[:-UNDO_LIMIT]
        self._flush_changes()

    @property
    def undo_label(self):
        return self._undo[-1]['label'] if self._undo else None

    def can_undo(self):
        return bool(self._undo)

    def clear_undo(self):
        self._undo.clear()

    def undo(self):
        """Put the newest undo step back; its label, or None when there is none."""
        if not self._undo:
            return None
        step = self._undo.pop()
        with self.undoable(None):
            for op in reversed(step['ops']):
                action, table = op[0], op[1]
                if action == 'delete':
                    where = ' AND '.join(f'{column} = ?' for column in op[2])
                    # An author, series or tag made by this step may have been taken up
                    # since by a change that is no undo step (a worker's import): it stays
                    # while a book uses it (an unused one goes at the next start).
                    where += NAME_IN_USE.get(table, '')
                    self.db.execute(f'DELETE FROM {table} WHERE {where}', list(op[2].values()))
                elif action == 'insert':
                    row = op[2]
                    columns = ', '.join(row)
                    marks = ', '.join('?' * len(row))
                    self.db.execute(f'INSERT OR IGNORE INTO {table} ({columns}) '
                                    f'VALUES ({marks})', list(row.values()))
                else:
                    key, old = op[2], op[3]
                    assignments = ', '.join(f'{column} = ?' for column in old)
                    where = ' AND '.join(f'{column} = ?' for column in key)
                    self.db.execute(f'UPDATE {table} SET {assignments} WHERE {where}',
                                    list(old.values()) + list(key.values()))
                self._touch(TABLE_KINDS[table])
        return step['label']

    def _touch(self, kind):
        self._changes.add(kind)
        if self._open is None:
            self._flush_changes()

    def _flush_changes(self):
        changes, self._changes = sorted(self._changes), set()
        for kind in changes:
            self.emit('changed', kind)

    def _log(self, op, record):
        if record and self._open is not None:
            self._open['ops'].append(op)

    def _insert(self, table, values, record=True):
        """Insert a row; its id. Inside an undoable block, undo deletes it."""
        columns = ', '.join(values)
        marks = ', '.join('?' * len(values))
        cursor = self.db.execute(f'INSERT INTO {table} ({columns}) VALUES ({marks})',
                                 list(values.values()))
        key = {column: values.get(column, cursor.lastrowid) for column in schema.KEYS[table]}
        self._log(('delete', table, key), record)
        self._touch(TABLE_KINDS[table])
        return cursor.lastrowid

    def _delete(self, table, where, params, record=True):
        """Delete the rows matching `where`; undo puts them back."""
        if record and self._open is not None:
            for row in self.db.execute(f'SELECT * FROM {table} WHERE {where}', params):
                self._open['ops'].append(('insert', table, dict(row)))
        if self.db.execute(f'DELETE FROM {table} WHERE {where}', params).rowcount:
            self._touch(TABLE_KINDS[table])

    def _update(self, table, where, params, values, record=True, kind=None):
        """Set `values` in the rows matching `where`; undo puts back the old values of the
        columns that changed. True when something changed; `changed` is then `kind` (else
        the table's)."""
        if not values:
            return False
        keys = schema.KEYS[table]
        changed = False
        selected = ', '.join(dict.fromkeys(keys + tuple(values)))
        for row in self.db.execute(f'SELECT {selected} FROM {table} WHERE {where}',
                                   params).fetchall():
            old = {column: row[column] for column in values if row[column] != values[column]}
            if old:
                changed = True
                self._log(('update', table, {column: row[column] for column in keys}, old),
                          record)
        if not changed:
            return False
        assignments = ', '.join(f'{column} = ?' for column in values)
        self.db.execute(f'UPDATE {table} SET {assignments} WHERE {where}',
                        list(values.values()) + list(params))
        self._touch(kind or TABLE_KINDS[table])
        return True

    # -- reading books --------------------------------------------------------------------

    def _where(self, query='', shelf=None, status=None, author=None, series=None, tag=None):
        clauses, params = [HIDDEN], []
        if query:
            sql, more = search.to_sql(query)
            clauses.append(sql)
            params += more
        if shelf is not None:
            row = self.db.execute('SELECT query FROM shelves WHERE id = ?',
                                  (_id(shelf),)).fetchone()
            if row is None:
                clauses.append('0')
            elif row['query'] is not None:
                sql, more = search.to_sql(row['query'])
                clauses.append(sql)
                params += more
            else:
                clauses.append('b.id IN (SELECT book_id FROM shelf_books WHERE shelf_id = ?)')
                params.append(_id(shelf))
        if status:
            clauses.append('b.status = ?')
            params.append(status)
        if author is not None:
            clauses.append('b.id IN (SELECT book_id FROM book_authors WHERE author_id = ?)')
            params.append(_id(author))
        if series is not None:
            clauses.append('b.series_id = ?')
            params.append(_id(series))
        if tag is not None:
            clauses.append('b.id IN (SELECT book_id FROM book_tags WHERE tag_id = ?)')
            params.append(_id(tag))
        return ' AND '.join(f'({clause})' for clause in clauses), params

    def _sorted_rows(self, select, filters, sort, descending, limit, offset):
        """The rows (tuples) of `select` (on books as b, series as s, starting with BOOK_SELECT's
        first seven columns) that pass `filters`, in order."""
        if sort not in DESCENDING:
            raise ValueError(f'unknown sort {sort!r}')
        if descending is None:
            descending = DESCENDING[sort]
        where, params = self._where(**filters)
        sql = f'{select} WHERE {where}'
        direction = 'DESC' if descending else 'ASC'
        order = {
            'added': f'b.added {direction}, b.id {direction}',
            'last-read': f'b.last_read {direction}, b.added DESC',
            'rating': f'b.rating {direction}, b.sort_title COLLATE NOCASE',
            'published': f"b.published = '', b.published {direction}, b.added DESC",
        }.get(sort)
        if order is not None:
            sql += f' ORDER BY {order}'
            if limit is not None or offset:
                sql += ' LIMIT ? OFFSET ?'
                params = params + [-1 if limit is None else limit, offset]
            return self._tuples(sql, params)
        rows = self._tuples(sql, params)
        key = titles.sort_key
        if sort == 'title':
            rows.sort(key=lambda row: (key(row[3]), row[0]), reverse=descending)
            last = []
        elif sort == 'author':
            rows.sort(key=lambda row: (key(row[4]), key(row[3])), reverse=descending)
            last = [row for row in rows if not row[4]]
        else:
            rows.sort(key=lambda row: (key(row[5] or ''), row[6], key(row[3])),
                      reverse=descending)
            last = [row for row in rows if not row[5]]
        if last:  # books without one go last either way
            column = 4 if sort == 'author' else 5
            rows = [row for row in rows if row[column]]
            rows += last
        end = None if limit is None else offset + limit
        return rows[offset:end]

    def _grouped(self, sql, ids, where_column):
        """Rows of `sql` for the books `ids`, filtered on `where_column`: one query (over
        every row when there are many ids)."""
        if len(ids) > CHUNK:
            return self._tuples(sql.format(where=''))
        marks = ','.join('?' * len(ids))
        return self._tuples(sql.format(where=f'WHERE {where_column} IN ({marks})'), ids)

    def _tuples(self, sql, params=()):
        """The rows of a query as plain tuples (quicker than sqlite3.Row for many)."""
        cursor = self.db.cursor()
        cursor.row_factory = None
        return cursor.execute(sql, params).fetchall()

    def _books(self, rows):
        """Books from rows of BOOK_SELECT, with their authors, tags, identifiers and formats
        gathered by one query each."""
        if not rows:
            return []
        ids = [row[0] for row in rows]
        authors, tags, identifiers, files = {}, {}, {}, {}
        for book_id, name in self._grouped(
                'SELECT ba.book_id, a.name FROM book_authors ba JOIN authors a '
                'ON a.id = ba.author_id {where} ORDER BY ba.book_id, ba.position', ids,
                'ba.book_id'):
            authors.setdefault(book_id, []).append(name)
        for book_id, name in self._grouped(
                'SELECT bt.book_id, t.name FROM book_tags bt JOIN tags t ON t.id = bt.tag_id '
                '{where} ORDER BY t.name COLLATE NOCASE', ids, 'bt.book_id'):
            tags.setdefault(book_id, []).append(name)
        for book_id, kind, value in self._grouped(
                'SELECT book_id, type, value FROM identifiers {where}', ids, 'book_id'):
            identifiers.setdefault(book_id, {})[kind] = value
        for book_id, fmt, missing in self._grouped(
                'SELECT book_id, format, missing FROM files {where}', ids, 'book_id'):
            entry = files.get(book_id)
            if entry is None:
                files[book_id] = ((fmt,), bool(missing))
            else:
                formats = entry[0] if fmt in entry[0] else tuple(
                    sorted(entry[0] + (fmt,), key=_format_rank))
                files[book_id] = (formats, entry[1] and bool(missing))
        books = []
        new = object.__new__
        no_files = ((), True)
        for row in rows:
            book_id = row[0]
            formats, missing = files.get(book_id, no_files)
            book = new(Book)
            # Faster than the frozen dataclass's __init__, which sets each field in turn.
            book.__dict__.update(
                id=book_id, uuid=row[1], title=row[2], sort_title=row[3],
                authors=tuple(authors.get(book_id, ())), author_sort=row[4],
                series=row[5] or '', series_index=row[6], tags=tuple(tags.get(book_id, ())),
                publisher=row[7], published=row[8], language=row[9], description=row[10],
                rating=row[11], identifiers=identifiers.get(book_id, {}),
                formats=formats, added=row[12], modified=row[13], status=row[14],
                progress=row[15], location=row[16], last_read=row[17],
                has_cover=bool(row[18]), cover_version=row[19],
                missing=missing,
                source=row[20], source_key=row[21], source_modified=row[22],
                source_values=row[23])
            books.append(book)
        return books

    def book(self, book_id):
        row = self.db.execute(f'{BOOK_SELECT} WHERE b.id = ?', (book_id,)).fetchone()
        return self._books([row])[0] if row else None

    def books(self, query='', shelf=None, status=None, author=None, series=None, tag=None,
              sort='added', descending=None, limit=None, offset=0):
        filters = dict(query=query, shelf=shelf, status=status, author=author, series=series,
                       tag=tag)
        return self._books(self._sorted_rows(BOOK_SELECT, filters, sort, descending, limit,
                                             offset))

    def book_ids(self, query='', shelf=None, status=None, author=None, series=None, tag=None,
                 sort='added', descending=None, limit=None, offset=0):
        filters = dict(query=query, shelf=shelf, status=status, author=author, series=series,
                       tag=tag)
        select = ('SELECT b.id, b.uuid, b.title, b.sort_title, b.author_sort, s.name, '
                  'b.series_index FROM books b LEFT JOIN series s ON s.id = b.series_id')
        return [row[0] for row in self._sorted_rows(select, filters, sort, descending, limit,
                                                     offset)]

    def count(self, query='', shelf=None, status=None, author=None, series=None, tag=None,
              **_ignored):
        where, params = self._where(query, shelf, status, author, series, tag)
        return self.db.execute(f'SELECT COUNT(*) FROM books b WHERE {where}',
                               params).fetchone()[0]

    def continue_reading(self, limit=12):
        return self.books(status='reading', sort='last-read', limit=limit)

    def recently_added(self, limit=12):
        return self.books(sort='added', limit=limit)

    def files(self, book_id):
        rows = self.db.execute('SELECT * FROM files WHERE book_id = ?', (book_id,)).fetchall()
        files = [_file(row) for row in rows]
        files.sort(key=lambda file: (file.missing, _format_rank(file.format), file.id))
        return files

    def reading_file(self, book_id):
        return next((file for file in self.files(book_id) if not file.missing), None)

    def find_by_hash(self, hash):
        if not hash:
            return None
        row = self.db.execute('SELECT book_id FROM files WHERE hash = ? LIMIT 1',
                              (hash,)).fetchone()
        return row[0] if row else None

    def find_file(self, path):
        row = self.db.execute('SELECT * FROM files WHERE path = ?', (str(path),)).fetchone()
        return _file(row) if row else None

    def find_by_source_key(self, source, key):
        row = self.db.execute('SELECT id FROM books WHERE source = ? AND source_key = ? '
                              'LIMIT 1', (source, str(key))).fetchone()
        return row[0] if row else None

    def find_by_identifier(self, kind, value):
        if not value:
            return None
        if kind == 'uuid':
            row = self.db.execute('SELECT id FROM books WHERE lower(uuid) = lower(?) LIMIT 1',
                                  (value,)).fetchone()
            if row:
                return row[0]
        row = self.db.execute('SELECT book_id FROM identifiers WHERE type = ? AND value = ? '
                              'LIMIT 1', (kind, value)).fetchone()
        return row[0] if row else None

    def find_similar(self, title, authors):
        key = titles.title_key(title)
        if not key:
            return []
        wanted = set()
        for name in authors or ():
            wanted |= titles.name_tokens(name)
        found = []
        for (book_id,) in self.db.execute(f'SELECT id FROM books b WHERE title_key = ? AND '
                                          f'{HIDDEN}', (key,)):
            names = [row[0] for row in self.db.execute(
                'SELECT a.name FROM book_authors ba JOIN authors a ON a.id = ba.author_id '
                'WHERE ba.book_id = ?', (book_id,))]
            if not wanted or not names or any(
                    titles.name_tokens(name) & wanted for name in names):
                found.append(book_id)
        return found

    def _groups(self, sql):
        groups = [Group(id=row[0], name=row[1], sort=row[2] or row[1], count=row[3])
                  for row in self.db.execute(sql)]
        groups.sort(key=lambda group: titles.sort_key(group.sort))
        return groups

    def authors(self):
        return self._groups('SELECT a.id, a.name, a.sort, COUNT(*) FROM authors a '
                            'JOIN book_authors ba ON ba.author_id = a.id '
                            f'JOIN books b ON b.id = ba.book_id AND {HIDDEN} GROUP BY a.id')

    def series(self):
        return self._groups('SELECT s.id, s.name, s.sort, COUNT(*) FROM series s '
                            f'JOIN books b ON b.series_id = s.id AND {HIDDEN} GROUP BY s.id')

    def tags(self):
        return self._groups('SELECT t.id, t.name, t.name, COUNT(*) FROM tags t '
                            'JOIN book_tags bt ON bt.tag_id = t.id '
                            f'JOIN books b ON b.id = bt.book_id AND {HIDDEN} GROUP BY t.id')

    def publishers(self):
        names = [row[0] for row in self.db.execute(
            f"SELECT DISTINCT publisher FROM books b WHERE publisher != '' AND {HIDDEN}")]
        return sorted(names, key=titles.sort_key)

    def languages(self):
        return sorted(row[0] for row in self.db.execute(
            f"SELECT DISTINCT language FROM books b WHERE language != '' AND {HIDDEN}"))

    # -- adding and editing books ---------------------------------------------------------

    def _folder_for(self, path):
        """The id of the folder holding `path` (the deepest), or None."""
        path = str(path)
        best, length = None, -1
        for row in self.db.execute('SELECT id, path FROM folders'):
            folder = row['path'].rstrip('/')
            if (path.startswith(folder + '/') or path == folder) and len(folder) > length:
                best, length = row['id'], len(folder)
        return best

    def add_book(self, info, path, *, hash, size, source='library', source_key=None,
                 source_modified=''):
        now = time.time()
        identifiers = {str(kind).lower(): str(value)
                       for kind, value in (info.identifiers or {}).items() if value}
        book_uuid = identifiers.pop('uuid', '').removeprefix('urn:uuid:')
        try:
            book_uuid = str(uuidlib.UUID(book_uuid))
        except ValueError:
            book_uuid = ''
        if not book_uuid or self.db.execute('SELECT 1 FROM books WHERE uuid = ?',
                                            (book_uuid,)).fetchone():
            book_uuid = str(uuidlib.uuid4())
        title = ' '.join((info.title or '').split()) or pathlib.Path(path).stem
        authors = [' '.join(name.split()) for name in info.authors or () if name.strip()]
        language = info.language or ''
        with self.undoable(_('Add Book')):
            book_id = self._insert('books', {
                'uuid': book_uuid, 'title': title,
                'sort_title': titles.title_sort(title, language),
                'author_sort': titles.authors_sort(authors),
                'series_id': self._series_id(info.series),
                'series_index': float(info.series_index or 0),
                'publisher': info.publisher or '', 'published': info.published or '',
                'language': language, 'description': info.description or '',
                'added': now, 'modified': now, 'source': source,
                'source_key': None if source_key is None else str(source_key),
                'source_modified': source_modified or '',
            })
            self._set_authors(book_id, authors)
            self._set_tags(book_id, info.tags or ())
            self._set_identifiers(book_id, identifiers)
            self._add_file(book_id, path, hash, size, info.format or None, now)
            self._refresh_derived([book_id])
        return book_id

    def _add_file(self, book_id, path, hash, size, fmt, now):
        path = str(path)
        if self.db.execute('SELECT 1 FROM files WHERE path = ?', (path,)).fetchone():
            raise LibraryError(_('This file is already in the library'))
        return self._insert('files', {
            'book_id': book_id, 'path': path, 'format': (fmt or format_of(path)).lower(),
            'size': size or 0, 'hash': hash or '', 'missing': 0,
            'folder_id': self._folder_for(path), 'added': now})

    def add_file(self, book_id, path, *, hash, size, format=None):
        with self.undoable(_('Add Format')):
            file_id = self._add_file(book_id, path, hash, size, format, time.time())
        return file_id

    def _author_id(self, name):
        row = self.db.execute('SELECT id FROM authors WHERE name = ?', (name,)).fetchone()
        if row:
            return row[0]
        return self._insert('authors', {'name': name, 'sort': titles.author_sort(name)})

    def _series_id(self, name):
        name = ' '.join((name or '').split())
        if not name:
            return None
        row = self.db.execute('SELECT id FROM series WHERE name = ?', (name,)).fetchone()
        if row:
            return row[0]
        return self._insert('series', {'name': name, 'sort': titles.title_sort(name)})

    def _tag_id(self, name):
        row = self.db.execute('SELECT id FROM tags WHERE name = ?', (name,)).fetchone()
        if row:
            return row[0]
        return self._insert('tags', {'name': name})

    def _set_authors(self, book_id, names):
        self._delete('book_authors', 'book_id = ?', [book_id])
        seen = set()
        for name in names:
            name = ' '.join(str(name).split())
            if not name or name.casefold() in seen:
                continue
            seen.add(name.casefold())
            self._insert('book_authors', {'book_id': book_id, 'author_id': self._author_id(name),
                                          'position': len(seen) - 1})

    def _set_tags(self, book_id, names):
        self._delete('book_tags', 'book_id = ?', [book_id])
        self._add_tags(book_id, names)

    def _add_tags(self, book_id, names):
        have = {row[0] for row in self.db.execute('SELECT tag_id FROM book_tags '
                                                  'WHERE book_id = ?', (book_id,))}
        for name in names:
            name = ' '.join(str(name).split())
            if not name:
                continue
            tag_id = self._tag_id(name)
            if tag_id not in have:
                have.add(tag_id)
                self._insert('book_tags', {'book_id': book_id, 'tag_id': tag_id})

    def _remove_tags(self, book_id, names):
        for name in names:
            row = self.db.execute('SELECT id FROM tags WHERE name = ?', (name,)).fetchone()
            if row:
                self._delete('book_tags', 'book_id = ? AND tag_id = ?', [book_id, row[0]])

    def _set_identifiers(self, book_id, identifiers):
        self._delete('identifiers', 'book_id = ?', [book_id])
        for kind, value in identifiers.items():
            value = str(value or '').strip()
            if value:
                self._insert('identifiers', {'book_id': book_id, 'type': str(kind).lower(),
                                             'value': value})

    def _refresh_derived(self, book_ids):
        """Recompute books.title_key and books.search_text from what the books are now."""
        for book in self._books(self.db.execute(
                f'{BOOK_SELECT} WHERE b.id IN ({",".join("?" * len(book_ids))})',
                book_ids).fetchall()):
            text = '\n'.join([book.title, *book.authors, book.series, *book.tags,
                              book.publisher])
            self._update('books', 'id = ?', [book.id], {
                'title_key': titles.title_key(book.title),
                'search_text': titles.fold(text)})

    def update_book(self, book_id, **fields):
        self.update_books([book_id], **fields)

    def update_books(self, book_ids, add_tags=(), remove_tags=(), **fields):
        unknown = set(fields) - EDITABLE
        if unknown:
            raise ValueError(f'not editable: {", ".join(sorted(unknown))}')
        if 'status' in fields and fields['status'] not in STATUSES:
            raise ValueError(f'unknown status {fields["status"]!r}')
        book_ids = list(dict.fromkeys(book_ids))
        if not book_ids:
            return
        label = ngettext('Edit Book', 'Edit Books', len(book_ids))
        now = time.time()
        with self.undoable(label):
            for chunk in _chunks(book_ids):
                for book_id in chunk:
                    self._update_one(book_id, fields, add_tags, remove_tags, now)
                self._refresh_derived(chunk)

    def _update_one(self, book_id, fields, add_tags, remove_tags, now):
        row = self.db.execute('SELECT language, cover_version FROM books WHERE id = ?',
                              (book_id,)).fetchone()
        if row is None:
            return
        values = {name: fields[name] for name in COLUMN_FIELDS if name in fields}
        for name in ('title', 'sort_title', 'author_sort', 'publisher', 'published',
                     'language', 'description', 'source_modified'):
            if name in values:
                values[name] = str(values[name] or '').strip()
        if 'title' in values:
            if not values['title']:
                del values['title']
            elif 'sort_title' not in fields:
                values['sort_title'] = titles.title_sort(
                    values['title'], values.get('language', row['language']))
        if 'rating' in values:
            values['rating'] = max(0, min(10, int(round(values['rating'] or 0))))
        if 'series_index' in values:
            values['series_index'] = float(values['series_index'] or 0)
        if 'source_key' in values and values['source_key'] is not None:
            values['source_key'] = str(values['source_key'])
        if 'has_cover' in values:
            values['has_cover'] = int(bool(values['has_cover']))
            values['cover_version'] = row['cover_version'] + 1
        if 'authors' in fields:
            names = [' '.join(str(name).split()) for name in fields['authors']]
            self._set_authors(book_id, [name for name in names if name])
            if 'author_sort' not in fields:
                values['author_sort'] = titles.authors_sort(names)
        if 'series' in fields:
            values['series_id'] = self._series_id(fields['series'])
        if 'tags' in fields:
            self._set_tags(book_id, fields['tags'])
        if add_tags:
            self._add_tags(book_id, add_tags)
        if remove_tags:
            self._remove_tags(book_id, remove_tags)
        if 'identifiers' in fields:
            self._set_identifiers(book_id, {kind: value for kind, value
                                            in (fields['identifiers'] or {}).items()
                                            if kind != 'uuid'})
        values['modified'] = now
        self._update('books', 'id = ?', [book_id], values)

    def remove_books(self, book_ids):
        book_ids = list(dict.fromkeys(_id(book_id) for book_id in book_ids))
        if not book_ids:
            return
        with self.undoable(ngettext('Remove Book', 'Remove Books', len(book_ids))):
            for chunk in _chunks(book_ids):
                marks = ','.join('?' * len(chunk))
                for table in ('book_authors', 'book_tags', 'identifiers', 'shelf_books',
                              'annotations', 'sessions', 'files'):
                    self._delete(table, f'book_id IN ({marks})', chunk)
                self._delete('books', f'id IN ({marks})', chunk)

    # -- reading state --------------------------------------------------------------------

    def set_progress(self, book_id, fraction, location):
        fraction = max(0.0, min(1.0, float(fraction or 0)))
        row = self.db.execute('SELECT status FROM books WHERE id = ?', (book_id,)).fetchone()
        if row is None:
            return
        values = {'progress': fraction, 'location': location or '', 'last_read': time.time()}
        started = row['status'] == 'unread'
        if started:
            values['status'] = 'reading'
        self._update('books', 'id = ?', [book_id], values, record=False, kind='progress')
        if started:
            self._touch('books')

    def set_status(self, book_ids, status):
        if status not in STATUSES:
            raise ValueError(f'unknown status {status!r}')
        book_ids = list(dict.fromkeys(_id(book_id) for book_id in book_ids))
        label = {'unread': _('Mark as Unread'), 'reading': _('Mark as Reading'),
                 'finished': _('Mark as Finished')}[status]
        now = time.time()
        with self.undoable(label):
            for chunk in _chunks(book_ids):
                marks = ','.join('?' * len(chunk))
                if status == 'finished':  # its date; a book already finished keeps its own
                    self._update('books', f'id IN ({marks}) AND status != ?',
                                 chunk + ['finished'], {'finished': now})
                # Unread starts the book over: no place kept to open at (undo puts it back).
                values = {'status': status, **({'finished': 0, 'progress': 0.0, 'location': ''}
                                               if status == 'unread' else {})}
                self._update('books', f'id IN ({marks})', chunk, values)

    def finished(self, since=None, until=None):
        """[(book_id, when)] of the books marked finished (in [since, until) when given),
        oldest first: books.finished, kept while a finished book is read again."""
        clauses, params = ['finished > 0'], []
        if since is not None:
            clauses.append('finished >= ?')
            params.append(since)
        if until is not None:
            clauses.append('finished < ?')
            params.append(until)
        return [tuple(row) for row in self.db.execute(
            f'SELECT id, finished FROM books WHERE {" AND ".join(clauses)} '
            'ORDER BY finished, id', params)]

    def page_counts(self):
        """{book_id: pages} of the books whose page count is known (books.pages)."""
        return dict(self.db.execute('SELECT id, pages FROM books WHERE pages > 0').fetchall())

    def log_session(self, book_id, started, seconds, start_fraction, end_fraction):
        """Record a stretch of reading; its id."""
        session_id = self._insert('sessions', {
            'book_id': book_id, 'started': started, 'seconds': max(0.0, float(seconds)),
            'start_fraction': start_fraction, 'end_fraction': end_fraction}, record=False)
        return session_id

    def sessions(self, book_id=None, since=None):
        clauses, params = [], []
        if book_id is not None:
            clauses.append('book_id = ?')
            params.append(book_id)
        if since is not None:
            clauses.append('started >= ?')
            params.append(since)
        where = ' WHERE ' + ' AND '.join(clauses) if clauses else ''
        return [Session(*row) for row in self.db.execute(
            'SELECT id, book_id, started, seconds, start_fraction, end_fraction FROM sessions'
            f'{where} ORDER BY started, id', params)]

    # -- annotations ----------------------------------------------------------------------

    def annotations(self, book_id, kind=None):
        sql = 'SELECT * FROM annotations WHERE book_id = ?'
        params = [book_id]
        if kind is not None:
            sql += ' AND kind = ?'
            params.append(kind)
        return [_annotation(row)
                for row in self.db.execute(sql + ' ORDER BY position, created, id', params)]

    def annotation(self, annotation_id):
        row = self.db.execute('SELECT * FROM annotations WHERE id = ?',
                              (annotation_id,)).fetchone()
        return _annotation(row) if row else None

    def add_annotation(self, book_id, kind, location, *, text='', note='', color='yellow',
                       position=0.0, created=None):
        if kind not in KINDS:
            raise ValueError(f'unknown annotation kind {kind!r}')
        now = time.time() if created is None else created
        label = _('Add Highlight') if kind == 'highlight' else _('Add Bookmark')
        with self.undoable(label):
            annotation_id = self._insert('annotations', {
                'book_id': book_id, 'kind': kind, 'location': location or '',
                'position': max(0.0, min(1.0, float(position or 0))), 'text': text or '',
                'note': note or '', 'color': color if color in COLORS else 'yellow',
                'created': now, 'modified': now})
        return annotation_id

    def update_annotation(self, annotation_id, note=None, color=None):
        values = {}
        if note is not None:
            values['note'] = note
        if color is not None:
            if color not in COLORS:
                raise ValueError(f'unknown colour {color!r}')
            values['color'] = color
        if not values:
            return
        values['modified'] = time.time()
        with self.undoable(_('Edit Note') if note is not None else _('Change Colour')):
            self._update('annotations', 'id = ?', [annotation_id], values)

    def remove_annotation(self, annotation_id):
        row = self.db.execute('SELECT kind FROM annotations WHERE id = ?',
                              (annotation_id,)).fetchone()
        if row is None:
            return
        label = _('Remove Highlight') if row['kind'] == 'highlight' else _('Remove Bookmark')
        with self.undoable(label):
            self._delete('annotations', 'id = ?', [annotation_id])

    def set_annotation_location(self, annotation_id, location, position=None):
        """The place of an annotation imported without one (a Kindle highlight the reader
        found in the book): not an undo step, as reading progress is not."""
        values = {'location': location or ''}
        if position is not None:
            values['position'] = max(0.0, min(1.0, float(position)))
        self._update('annotations', 'id = ?', [annotation_id], values, record=False)

    # -- shelves --------------------------------------------------------------------------

    def _shelf(self, row):
        if row['query'] is not None:
            count = self.count(shelf=row['id'])
        else:
            count = self.db.execute('SELECT COUNT(*) FROM shelf_books sb JOIN books b '
                                    'ON b.id = sb.book_id WHERE sb.shelf_id = ?',
                                    (row['id'],)).fetchone()[0]
        return Shelf(id=row['id'], name=row['name'], query=row['query'],
                     position=row['position'], count=count)

    def shelves(self):
        return [self._shelf(row) for row in self.db.execute(
            'SELECT * FROM shelves ORDER BY position, id').fetchall()]

    def shelf(self, shelf_id):
        row = self.db.execute('SELECT * FROM shelves WHERE id = ?', (_id(shelf_id),)).fetchone()
        return self._shelf(row) if row else None

    def add_shelf(self, name, query=None):
        position = self.db.execute('SELECT COALESCE(MAX(position) + 1, 0) FROM shelves'
                                   ).fetchone()[0]
        with self.undoable(_('Add Shelf')):
            shelf_id = self._insert('shelves', {'name': name.strip(), 'query': query,
                                                'position': position})
        return shelf_id

    def update_shelf(self, shelf_id, name=None, query=None):
        values = {}
        if name is not None:
            values['name'] = name.strip()
        if query is not None:
            values['query'] = query
        with self.undoable(_('Edit Shelf')):
            self._update('shelves', 'id = ?', [_id(shelf_id)], values)

    def move_shelf(self, shelf_id, index):
        order = [row[0] for row in self.db.execute('SELECT id FROM shelves '
                                                   'ORDER BY position, id')]
        shelf_id = _id(shelf_id)
        if shelf_id not in order:
            return
        order.remove(shelf_id)
        order.insert(max(0, min(index, len(order))), shelf_id)
        with self.undoable(_('Move Shelf')):
            for position, each in enumerate(order):
                self._update('shelves', 'id = ?', [each], {'position': position})

    def remove_shelf(self, shelf_id):
        shelf_id = _id(shelf_id)
        with self.undoable(_('Remove Shelf')):
            self._delete('shelf_books', 'shelf_id = ?', [shelf_id])
            self._delete('shelves', 'id = ?', [shelf_id])

    def add_to_shelf(self, shelf_id, book_ids):
        shelf_id = _id(shelf_id)
        have = {row[0] for row in self.db.execute('SELECT book_id FROM shelf_books '
                                                  'WHERE shelf_id = ?', (shelf_id,))}
        now = time.time()
        with self.undoable(_('Add to Shelf')):
            for book_id in dict.fromkeys(_id(book_id) for book_id in book_ids):
                if book_id not in have:
                    self._insert('shelf_books', {'shelf_id': shelf_id, 'book_id': book_id,
                                                 'added': now})

    def remove_from_shelf(self, shelf_id, book_ids):
        shelf_id = _id(shelf_id)
        with self.undoable(_('Remove from Shelf')):
            for chunk in _chunks(dict.fromkeys(_id(book_id) for book_id in book_ids)):
                marks = ','.join('?' * len(chunk))
                self._delete('shelf_books', f'shelf_id = ? AND book_id IN ({marks})',
                             [shelf_id] + chunk)

    def book_shelves(self, book_id):
        return [self._shelf(row) for row in self.db.execute(
            'SELECT s.* FROM shelves s JOIN shelf_books sb ON sb.shelf_id = s.id '
            'WHERE sb.book_id = ? AND s.query IS NULL ORDER BY s.position, s.id',
            (book_id,)).fetchall()]

    # -- folders and files ----------------------------------------------------------------

    def folders(self):
        return [Folder(row['id'], row['path'], row['kind'])
                for row in self.db.execute('SELECT * FROM folders ORDER BY id')]

    def add_folder(self, path, kind):
        if kind not in FOLDER_KINDS:
            raise ValueError(f'unknown folder kind {kind!r}')
        path = os.path.abspath(os.path.expanduser(str(path))).rstrip('/') or '/'
        row = self.db.execute('SELECT id FROM folders WHERE path = ?', (path,)).fetchone()
        if row:
            return row[0]
        with self.undoable(_('Add Folder')):
            folder_id = self._insert('folders', {'path': path, 'kind': kind})
            prefix = path.rstrip('/') + '/'
            for file_row in self.db.execute('SELECT id, path FROM files WHERE substr(path, 1, ?)'
                                            ' = ?', (len(prefix), prefix)).fetchall():
                if self._folder_for(file_row['path']) == folder_id:
                    self._update('files', 'id = ?', [file_row['id']], {'folder_id': folder_id})
        return folder_id

    def remove_folder(self, folder_id, remove_books=False):
        folder_id = _id(folder_id)
        with self.undoable(_('Remove Folder')):
            if remove_books:
                # The books with no file outside this folder go; the others lose its files.
                gone = [row[0] for row in self.db.execute(
                    'SELECT DISTINCT book_id FROM files WHERE folder_id = ? AND book_id NOT IN '
                    '(SELECT book_id FROM files WHERE folder_id IS NOT ?)',
                    (folder_id, folder_id))]
                self.remove_books(gone)
                self._delete('files', 'folder_id = ?', [folder_id])
            else:
                self._update('files', 'folder_id = ?', [folder_id], {'folder_id': None})
            self._delete('folders', 'id = ?', [folder_id])

    def in_calibre(self, book_id):
        """Whether a file of the book lies in a linked Calibre library (a book linked from
        one, or another book a Calibre book was merged into): such a file is Calibre's, never
        to be trashed by Bookcase."""
        return self.db.execute(
            "SELECT 1 FROM files f JOIN folders d ON d.id = f.folder_id "
            "WHERE f.book_id = ? AND d.kind = 'calibre' LIMIT 1", (_id(book_id),)
        ).fetchone() is not None

    def folder_files(self, folder_id):
        return [_file(row) for row in self.db.execute(
            'SELECT * FROM files WHERE folder_id = ? ORDER BY path', (_id(folder_id),))]

    def set_file_path(self, file_id, path, hash=None, size=None):
        path = str(path)
        other = self.db.execute('SELECT id FROM files WHERE path = ?', (path,)).fetchone()
        if other and other[0] != file_id:
            raise LibraryError(_('This file is already in the library'))
        values = {'path': path, 'missing': 0, 'folder_id': self._folder_for(path)}
        if hash is not None:
            values['hash'] = hash
        if size is not None:
            values['size'] = size
        if self._update('files', 'id = ?', [file_id], values, record=False):
            self._touch('books')

    def set_missing(self, file_ids, missing=True):
        changed = False
        for chunk in _chunks(dict.fromkeys(file_ids)):
            marks = ','.join('?' * len(chunk))
            changed |= self._update('files', f'id IN ({marks})', chunk,
                                    {'missing': int(bool(missing))}, record=False)
        if changed:
            self._touch('books')

    # -- books opened without adding ------------------------------------------------------

    def add_opened(self, info, path, *, hash, size):
        """A book file opened from outside, read where it is and kept out of the library's
        lists (source OPENED): its id. No undo step: nothing the user sees changed."""
        with self.undoable(None):
            return self.add_book(info, path, hash=hash, size=size, source=OPENED)

    def opened_ids(self):
        """The ids of the books opened without adding."""
        return {row[0] for row in self.db.execute('SELECT id FROM books WHERE source = ?',
                                                  (OPENED,))}

    def keep_book(self, book_id, source='library', path=None, hash=None, size=None,
                  format=None):
        """Add a book opened without adding to the library (undoable 'Add to Library'): its
        source becomes `source`, its 'added' now, and with `path` (a copy made in the
        library folder) its file is that copy (with the copy's `hash`, `size` and `format`
        when given: a CBR copied as a CBZ). False when it was not an opened book."""
        row = self.db.execute('SELECT source FROM books WHERE id = ?', (book_id,)).fetchone()
        if row is None or row['source'] != OPENED:
            return False
        with self.undoable(_('Add to Library')):
            if path is not None:
                path = str(path)
                if self.db.execute('SELECT 1 FROM files WHERE path = ? AND book_id != ?',
                                   (path, book_id)).fetchone():
                    raise LibraryError(_('This file is already in the library'))
                first = self.db.execute('SELECT id FROM files WHERE book_id = ? ORDER BY id '
                                        'LIMIT 1', (book_id,)).fetchone()
                if first is not None:
                    values = {'path': path, 'missing': 0, 'folder_id': self._folder_for(path)}
                    for name, value in (('hash', hash), ('size', size), ('format', format)):
                        if value is not None:
                            values[name] = value
                    self._update('files', 'id = ?', [first[0]], values)
            self._update('books', 'id = ?', [book_id], {'source': source, 'added': time.time()})
            self._touch('books')
        return True

    # -- duplicates -----------------------------------------------------------------------

    def duplicates(self):
        """The books that look like the same book: [[book id]] groups of two or more with
        the same normalised title (titles.title_key) and an author in common (or no author
        on one side), each group oldest first, the groups by title."""
        rows = self.db.execute(
            f"SELECT b.title_key, b.id, b.sort_title FROM books b WHERE b.title_key != '' AND "
            f"{HIDDEN} AND b.title_key IN (SELECT title_key FROM books b WHERE "
            f"title_key != '' AND {HIDDEN} GROUP BY title_key HAVING COUNT(*) > 1) "
            'ORDER BY b.title_key, b.id').fetchall()
        if not rows:
            return []
        names = {}
        for book_id, name in self._grouped(
                'SELECT ba.book_id, a.name FROM book_authors ba JOIN authors a '
                'ON a.id = ba.author_id {where}', [row[1] for row in rows], 'ba.book_id'):
            names.setdefault(book_id, set()).update(titles.name_tokens(name))
        groups, keys = [], {}
        for key, book_id, sort_title in rows:
            tokens = names.get(book_id, set())
            clusters = keys.setdefault(key, [])
            for cluster in clusters:
                if not tokens or not cluster['tokens'] or tokens & cluster['tokens']:
                    cluster['ids'].append(book_id)
                    cluster['tokens'] |= tokens
                    break
            else:
                cluster = {'ids': [book_id], 'tokens': set(tokens), 'sort': sort_title}
                clusters.append(cluster)
                groups.append(cluster)
        groups = [group for group in groups if len(group['ids']) > 1]
        groups.sort(key=lambda group: titles.sort_key(group['sort']))
        return [group['ids'] for group in groups]

    def richest(self, book_ids):
        """Of books that are the same book, the one to keep when they are merged: the one
        with the most to lose (a cover, a description, identifiers, highlights, progress)."""
        def score(book):
            annotations = self.db.execute('SELECT COUNT(*) FROM annotations WHERE book_id = ?',
                                          (book.id,)).fetchone()[0]
            return (3 * book.has_cover + 2 * bool(book.description) + len(book.identifiers)
                    + len(book.tags) + bool(book.series) + bool(book.publisher)
                    + bool(book.published) + bool(book.rating) + 2 * bool(annotations)
                    + 2 * bool(book.progress or book.status != 'unread') + len(book.formats),
                    -book.id)
        books = [book for book in (self.book(book_id) for book_id in book_ids) if book]
        return max(books, key=score).id if books else None

    def merge_books(self, keep_id, other_ids):
        """Merge books that are the same book into `keep_id` (undoable 'Merge Books'): the
        others' files become its formats, their highlights, bookmarks, reading sessions and
        shelves its own; what it lacks (description, publisher, published, language, series,
        identifiers, tags) is taken from them, the best rating kept; the reading place is
        the most recently read one's; it keeps the earliest date added. The others leave
        the library (their files stay where they are, now the kept book's). Returns the
        number of books merged into it."""
        keep = self.book(keep_id)
        others = [book for book in (self.book(book_id) for book_id in
                                    dict.fromkeys(other_ids) if book_id != keep_id) if book]
        if keep is None or not others:
            return 0
        everyone = [keep] + others
        with self.undoable(_('Merge Books')):
            values = {}
            for name in ('description', 'publisher', 'published', 'language'):
                if not getattr(keep, name):
                    found = next((getattr(book, name) for book in others
                                  if getattr(book, name)), '')
                    if found:
                        values[name] = found
            if not keep.series:
                donor = next((book for book in others if book.series), None)
                if donor is not None:
                    values['series_id'] = self._series_id(donor.series)
                    values['series_index'] = donor.series_index
            values['rating'] = max(book.rating for book in everyone)
            read = max(everyone, key=lambda book: (book.last_read, book.progress))
            if read.last_read:
                values.update(status=read.status, progress=read.progress,
                              location=read.location, last_read=read.last_read)
            else:
                rank = {'unread': 0, 'reading': 1, 'finished': 2}
                values['status'] = max((book.status for book in everyone), key=rank.get)
            ids = [book.id for book in everyone]
            marks = ','.join('?' * len(ids))
            values['finished'] = self.db.execute(
                f'SELECT MAX(finished) FROM books WHERE id IN ({marks})', ids).fetchone()[0]
            values['added'] = min(book.added for book in everyone)
            values['modified'] = time.time()
            if not keep.authors:
                donor = next((book for book in others if book.authors), None)
                if donor is not None:
                    self._set_authors(keep_id, donor.authors)
                    values['author_sort'] = titles.authors_sort(donor.authors)
            self._add_tags(keep_id, [tag for book in others for tag in book.tags])
            have = set(keep.identifiers)
            for book in others:
                for kind, value in book.identifiers.items():
                    if kind not in have and kind != 'uuid':
                        have.add(kind)
                        self._insert('identifiers', {'book_id': keep_id, 'type': kind,
                                                     'value': value})
            shelves = {row[0] for row in self.db.execute(
                'SELECT shelf_id FROM shelf_books WHERE book_id = ?', (keep_id,))}
            other_marks = ','.join('?' * len(others))
            other_ids = [book.id for book in others]
            for shelf_id, added in self.db.execute(
                    f'SELECT shelf_id, MIN(added) FROM shelf_books WHERE book_id IN '
                    f'({other_marks}) GROUP BY shelf_id', other_ids).fetchall():
                if shelf_id not in shelves:
                    self._insert('shelf_books', {'shelf_id': shelf_id, 'book_id': keep_id,
                                                 'added': added})
            for table in ('files', 'annotations', 'sessions'):
                self._update(table, f'book_id IN ({other_marks})', other_ids,
                             {'book_id': keep_id})
            for table in ('book_authors', 'book_tags', 'identifiers', 'shelf_books'):
                self._delete(table, f'book_id IN ({other_marks})', other_ids)
            self._delete('books', f'id IN ({other_marks})', other_ids)
            self._update('books', 'id = ?', [keep_id], values)
            self._refresh_derived([keep_id])
            self._touch('books')
        return len(others)
