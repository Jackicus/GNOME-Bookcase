# SPDX-License-Identifier: GPL-2.0-or-later
# SPDX-FileCopyrightText: 2026 Jack Tully

"""The library's SQLite schema: one file, library.sqlite, in the data directory.

    schema.apply(db)        # creates the tables in a new file, migrates an older one

`PRAGMA user_version` is the schema's version (VERSION); MIGRATIONS[n] is the SQL that takes
a version n-1 file to version n. Times are Unix seconds (REAL). Ids are AUTOINCREMENT so a
removed row's id is never given again: undo puts rows back under their old ids.

books: `sort_title` and `author_sort` are shown and editable (titles.py computes them);
`series_index` is a float (Calibre's 1.5); `rating` 0-10 (half stars, 0 none); `status`
unread, reading or finished; `progress` 0-1 and `location` (the reader's CFI) are where the
reader left off, `last_read` when; `finished` when the book was last marked finished (0
never; kept while it is read again, cleared when it is marked unread); `pages` its page
count when known (0 unknown: stats.py estimates it from the file); `cover_version` grows
whenever the cover changes (the thumbnail cache keys on it); `source` is the folder kind the
book came from (library, watched, calibre), `source_key` its key there (a Calibre book id)
and `source_modified` when the source last changed it, as the source writes it (Calibre's
last_modified). Two derived columns are kept up to date by library.py: `title_key`
(titles.title_key(), for finding a book added twice) and `search_text` (the folded title,
authors, series, tags and publisher, one per line, for search.py's bare words).

Search uses that one folded column with LIKE, not FTS5: a library is thousands of books, not
millions, and a LIKE over 10,000 short rows takes a few milliseconds, while FTS5 would need
a tokenizer that matches inside words ('hobb' finds The Hobbit) and triggers to keep it in
step with undo. Field searches (author:, tag:) fold in SQL through the `fold` function
library.py registers.

files: a book has one file per format; `path` is absolute and unique; `hash` is
importing.partial_md5(); `missing` is 1 when the file was not found at the last look;
`folder_id` the folder (folders) it lives under, if any.

authors, series, tags: names are unique (NOCASE: 'ada lark' is 'Ada Lark'); book_authors
keeps the order (`position`, 0 the main author). Rows no book uses are dropped when the
library opens, never during a session (undo may need them).

identifiers: (book, type) -> value, types lower-case ('isbn', 'google', 'amazon', …).
shelves: a manual shelf has `query` NULL and its books in shelf_books; a smart shelf has a
search query. annotations: `kind` highlight or bookmark, `location` a CFI, `position` 0-1
for ordering, `color` one of library.COLORS. sessions: a stretch of reading, logged by the
reader window. folders: `kind` library, watched or calibre.
"""

VERSION = 2

SCHEMA = """
CREATE TABLE IF NOT EXISTS books (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    uuid TEXT NOT NULL UNIQUE,
    title TEXT NOT NULL,
    sort_title TEXT NOT NULL DEFAULT '',
    author_sort TEXT NOT NULL DEFAULT '',
    series_id INTEGER,
    series_index REAL NOT NULL DEFAULT 0,
    publisher TEXT NOT NULL DEFAULT '',
    published TEXT NOT NULL DEFAULT '',
    language TEXT NOT NULL DEFAULT '',
    description TEXT NOT NULL DEFAULT '',
    rating INTEGER NOT NULL DEFAULT 0,
    added REAL NOT NULL,
    modified REAL NOT NULL,
    status TEXT NOT NULL DEFAULT 'unread',
    progress REAL NOT NULL DEFAULT 0,
    location TEXT NOT NULL DEFAULT '',
    last_read REAL NOT NULL DEFAULT 0,
    finished REAL NOT NULL DEFAULT 0,
    pages INTEGER NOT NULL DEFAULT 0,
    has_cover INTEGER NOT NULL DEFAULT 0,
    cover_version INTEGER NOT NULL DEFAULT 0,
    source TEXT NOT NULL DEFAULT 'library',
    source_key TEXT,
    source_modified TEXT NOT NULL DEFAULT '',
    title_key TEXT NOT NULL DEFAULT '',
    search_text TEXT NOT NULL DEFAULT ''
);
CREATE INDEX IF NOT EXISTS books_added ON books (added);
CREATE INDEX IF NOT EXISTS books_last_read ON books (last_read);
CREATE INDEX IF NOT EXISTS books_status ON books (status, last_read);
CREATE INDEX IF NOT EXISTS books_series ON books (series_id, series_index);
CREATE INDEX IF NOT EXISTS books_title_key ON books (title_key);
CREATE INDEX IF NOT EXISTS books_source ON books (source, source_key);

CREATE TABLE IF NOT EXISTS files (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    book_id INTEGER NOT NULL,
    path TEXT NOT NULL UNIQUE,
    format TEXT NOT NULL,
    size INTEGER NOT NULL DEFAULT 0,
    hash TEXT NOT NULL DEFAULT '',
    missing INTEGER NOT NULL DEFAULT 0,
    folder_id INTEGER,
    added REAL NOT NULL DEFAULT 0
);
CREATE INDEX IF NOT EXISTS files_book ON files (book_id);
CREATE INDEX IF NOT EXISTS files_hash ON files (hash);
CREATE INDEX IF NOT EXISTS files_folder ON files (folder_id);

CREATE TABLE IF NOT EXISTS authors (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    name TEXT NOT NULL UNIQUE COLLATE NOCASE,
    sort TEXT NOT NULL DEFAULT ''
);

CREATE TABLE IF NOT EXISTS book_authors (
    book_id INTEGER NOT NULL,
    author_id INTEGER NOT NULL,
    position INTEGER NOT NULL DEFAULT 0,
    PRIMARY KEY (book_id, author_id)
);
CREATE INDEX IF NOT EXISTS book_authors_author ON book_authors (author_id);

CREATE TABLE IF NOT EXISTS series (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    name TEXT NOT NULL UNIQUE COLLATE NOCASE,
    sort TEXT NOT NULL DEFAULT ''
);

CREATE TABLE IF NOT EXISTS tags (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    name TEXT NOT NULL UNIQUE COLLATE NOCASE
);

CREATE TABLE IF NOT EXISTS book_tags (
    book_id INTEGER NOT NULL,
    tag_id INTEGER NOT NULL,
    PRIMARY KEY (book_id, tag_id)
);
CREATE INDEX IF NOT EXISTS book_tags_tag ON book_tags (tag_id);

CREATE TABLE IF NOT EXISTS identifiers (
    book_id INTEGER NOT NULL,
    type TEXT NOT NULL,
    value TEXT NOT NULL,
    PRIMARY KEY (book_id, type)
);
CREATE INDEX IF NOT EXISTS identifiers_value ON identifiers (type, value);

CREATE TABLE IF NOT EXISTS shelves (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    name TEXT NOT NULL,
    query TEXT,
    position INTEGER NOT NULL DEFAULT 0
);

CREATE TABLE IF NOT EXISTS shelf_books (
    shelf_id INTEGER NOT NULL,
    book_id INTEGER NOT NULL,
    added REAL NOT NULL DEFAULT 0,
    PRIMARY KEY (shelf_id, book_id)
);
CREATE INDEX IF NOT EXISTS shelf_books_book ON shelf_books (book_id);

CREATE TABLE IF NOT EXISTS annotations (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    book_id INTEGER NOT NULL,
    kind TEXT NOT NULL,
    location TEXT NOT NULL DEFAULT '',
    position REAL NOT NULL DEFAULT 0,
    text TEXT NOT NULL DEFAULT '',
    note TEXT NOT NULL DEFAULT '',
    color TEXT NOT NULL DEFAULT 'yellow',
    created REAL NOT NULL,
    modified REAL NOT NULL
);
CREATE INDEX IF NOT EXISTS annotations_book ON annotations (book_id, position);

CREATE TABLE IF NOT EXISTS sessions (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    book_id INTEGER NOT NULL,
    started REAL NOT NULL,
    seconds REAL NOT NULL,
    start_fraction REAL NOT NULL DEFAULT 0,
    end_fraction REAL NOT NULL DEFAULT 0
);
CREATE INDEX IF NOT EXISTS sessions_book ON sessions (book_id, started);
CREATE INDEX IF NOT EXISTS sessions_started ON sessions (started);

CREATE TABLE IF NOT EXISTS folders (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    path TEXT NOT NULL UNIQUE,
    kind TEXT NOT NULL
);
"""

# version -> the SQL taking a file of version - 1 to it. Version 1 is SCHEMA itself.
MIGRATIONS = {
    # books.finished: when the book was last marked finished. A book finished before it
    # was kept gets the end of its last reading session, else when it was last read.
    # books.pages: its page count, when known.
    2: """
ALTER TABLE books ADD COLUMN finished REAL NOT NULL DEFAULT 0;
ALTER TABLE books ADD COLUMN pages INTEGER NOT NULL DEFAULT 0;
UPDATE books SET finished = COALESCE(
    (SELECT MAX(started + seconds) FROM sessions WHERE sessions.book_id = books.id),
    NULLIF(last_read, 0), modified)
WHERE status = 'finished';
""",
}

# Each table's key, for undo's inverse operations (library.py).
KEYS = {
    'books': ('id',),
    'files': ('id',),
    'authors': ('id',),
    'book_authors': ('book_id', 'author_id'),
    'series': ('id',),
    'tags': ('id',),
    'book_tags': ('book_id', 'tag_id'),
    'identifiers': ('book_id', 'type'),
    'shelves': ('id',),
    'shelf_books': ('shelf_id', 'book_id'),
    'annotations': ('id',),
    'sessions': ('id',),
    'folders': ('id',),
}


class SchemaError(Exception):
    """A library file written by a newer Bookcase."""


def apply(db):
    """Create the schema in a new database, or migrate an older one to VERSION, in one
    transaction. Raises SchemaError for a file of a newer version."""
    version = db.execute('PRAGMA user_version').fetchone()[0]
    if version > VERSION:
        raise SchemaError(f'library schema {version} is newer than {VERSION}')
    if version == VERSION:
        return
    if version == 0:
        steps = SCHEMA
    else:
        steps = '\n'.join(MIGRATIONS[step] for step in range(version + 1, VERSION + 1))
    db.executescript(f'BEGIN IMMEDIATE;\n{steps}\nPRAGMA user_version = {VERSION};\nCOMMIT;')
