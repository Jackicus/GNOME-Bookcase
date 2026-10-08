# SPDX-License-Identifier: GPL-2.0-or-later
# SPDX-FileCopyrightText: 2026 Jack Tully

"""The library: every book Bookcase knows, in one SQLite database (schema.py).

    library = Library(path)                 # creates the schema in a new file
    library.close()
    worker = library.open_worker()          # a Library on the same file, for a thread
    library.connect('changed', handler)     # handler(library, kind): 'books', 'files',
                                            # 'shelves', 'annotations', 'progress', 'folders'

Books (a Book is a frozen dataclass; lists are tuples):

    library.book(book_id)                   # Book or None
    library.books(query='', shelf=None, status=None, author=None, series=None, tag=None,
                  sort='added', descending=None, limit=None, offset=0)   # [Book]
    library.book_ids(...same filters...)    # [int], for large grids
    library.count(...same filters...)       # int
    library.continue_reading(limit=12)      # status 'reading', most recently read first
    library.recently_added(limit=12)
    library.add_book(info, path, *, hash, size, source='library', source_key=None)
                                            # a new book from a formats.BookInfo and its
                                            # first file; returns the book id
    library.add_file(book_id, path, *, hash, size)      # another format of the same book
    library.files(book_id)                  # [BookFile], preferred reading format first
    library.reading_file(book_id)           # the BookFile to open, or None
    library.find_by_hash(hash)              # book id or None
    library.find_file(path)                 # BookFile or None
    library.find_similar(title, authors)    # [book id]: same normalised title and an author
    library.update_book(book_id, **fields)  # undoable 'Edit Book'
    library.update_books(book_ids, **fields)  # bulk; also add_tags=, remove_tags=
    library.remove_books(book_ids)          # undoable; files stay where they are
    library.authors() / series() / tags()   # [Group(id, name, sort, count)]
    library.publishers() / languages()      # [str]

Editable fields: title, sort_title, authors (sequence of names, first is the main author),
author_sort, series (name or ''), series_index (float), tags (sequence), publisher,
published ('YYYY', 'YYYY-MM' or 'YYYY-MM-DD'), language (ISO 639 code), description (HTML),
rating (0-10, Calibre's half stars; 0 none), identifiers ({'isbn': …, 'google': …}),
has_cover (set through covers.py), status.

Reading state (not undo steps; `changed('progress')`):

    library.set_progress(book_id, fraction, location)  # location: the reader's CFI
    library.set_status(book_ids, status)    # undoable: 'unread', 'reading', 'finished'
    library.log_session(book_id, started, seconds, start_fraction, end_fraction)
    library.sessions(book_id=None, since=None)          # [Session]

Annotations ('highlight', 'bookmark'; a highlight may carry a note):

    library.annotations(book_id, kind=None) # [Annotation], in reading order (by position)
    library.add_annotation(book_id, kind, location, *, text='', note='', color='yellow',
                           position=0.0)    # undoable; returns its id
    library.update_annotation(annotation_id, note=None, color=None)   # undoable
    library.remove_annotation(annotation_id)  # undoable

Shelves (a manual shelf holds books; a smart shelf has a search query instead):

    library.shelves()                       # [Shelf], in the user's order
    library.add_shelf(name, query=None)     # undoable; returns its id
    library.update_shelf(shelf_id, name=None, query=None)
    library.remove_shelf(shelf_id)          # undoable; its books stay in the library
    library.add_to_shelf(shelf_id, book_ids) / remove_from_shelf(shelf_id, book_ids)
    library.book_shelves(book_id)           # [Shelf] the manual shelves holding it

Folders (where books live: 'library' is the folder added books are copied into, 'watched' a
folder read in place, 'calibre' a linked Calibre library):

    library.folders()                       # [Folder]
    library.add_folder(path, kind)          # returns its id
    library.remove_folder(folder_id, remove_books=False)
    library.set_file_path(file_id, path)    # a moved file found again
    library.set_missing(file_ids, missing=True)

Undo:

    with library.undoable(_('Edit Book')):  # nests; one step per outermost block
        ...
    library.undo()                          # the label put back, or None
    library.can_undo()
"""

import dataclasses

STATUSES = ('unread', 'reading', 'finished')
KINDS = ('highlight', 'bookmark')
COLORS = ('yellow', 'green', 'blue', 'pink', 'purple')
FOLDER_KINDS = ('library', 'watched', 'calibre')
# The order a book's formats are offered for reading in.
READING_ORDER = ('epub', 'kepub', 'azw3', 'mobi', 'fb2', 'fbz', 'cbz', 'pdf', 'txt')


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

    @property
    def author(self):
        """The authors, for a label: 'Ada Lark', 'Ada Lark and Ben Ross', 'Ada Lark and 2
        others' (translated)."""
        raise NotImplementedError


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


class Library:
    """See the module docstring. Implemented by the core."""
