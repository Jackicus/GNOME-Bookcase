# SPDX-License-Identifier: GPL-2.0-or-later
# SPDX-FileCopyrightText: 2026 Jack Tully

"""The schema and its migrations: a library made by an older Bookcase opens with everything
it held.

V1_SCHEMA is schema.SCHEMA as Bookcase 0.1.0 shipped it (`git show v0.1.0:src/schema.py`),
kept here word for word: make_v1_library() builds a version 1 file from it and invented rows
the way 0.1.0 wrote them, so each migration is tested from the real old layout (its columns
in their old order), not from today's schema with columns dropped.
"""

import os
import sqlite3
import tempfile
import unittest

from tests import ROOT  # noqa: F401
from bookcase import schema
from bookcase.library import Library

V1_SCHEMA = """
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


def make_v1_library(path):
    """A library file of schema version 1 (Bookcase 0.1.0) holding two invented books
    with files (one missing), authors, a series, tags, identifiers, progress, a finished
    book, a highlight and a bookmark, a manual and a smart shelf, reading sessions and a
    watched folder."""
    db = sqlite3.connect(path, isolation_level=None)
    db.executescript(f'BEGIN; {V1_SCHEMA} PRAGMA user_version = 1; COMMIT;')
    db.executescript("""
BEGIN;
INSERT INTO folders VALUES (1, '/invented/watched', 'watched');
INSERT INTO series VALUES (1, 'Tides', 'Tides');
INSERT INTO authors VALUES (1, 'Ada Lark', 'Lark, Ada'), (2, 'Ben Ross', 'Ross, Ben'),
    (3, 'Cy Doe', 'Doe, Cy');
INSERT INTO tags VALUES (1, 'Sea'), (2, 'Calm');
INSERT INTO books VALUES
    (1, '00000000-0000-4000-8000-000000000001', 'A Quiet Harbour', 'Quiet Harbour, A',
     'Lark, Ada', 1, 2.0, 'Pier Press', '2019', 'en', '<p>Tides.</p>', 8, 1000.0, 1100.0,
     'reading', 0.5, 'epubcfi(/6/4!/4/2)', 1200.0, 1, 3, 'library', NULL, '',
     'quiet harbour', 'a quiet harbour' || char(10) || 'ada lark' || char(10) || 'tides'),
    (2, '00000000-0000-4000-8000-000000000002', 'The Night Train', 'Night Train, The',
     'Ross, Ben & Doe, Cy', NULL, 0.0, '', '', '', '', 0, 2000.0, 2100.0, 'finished', 1.0,
     'epubcfi(/6/20)', 0.0, 0, 0, 'calibre', '42', '2026-01-01 10:00:00+00:00',
     'night train', 'the night train' || char(10) || 'ben ross' || char(10) || 'cy doe');
INSERT INTO files VALUES
    (1, 1, '/invented/watched/harbour.epub', 'epub', 10, 'h1', 0, 1, 1000.0),
    (2, 2, '/invented/train.pdf', 'pdf', 20, 'h2', 0, NULL, 2000.0),
    (3, 2, '/invented/train.epub', 'epub', 30, 'h3', 1, NULL, 2001.0);
INSERT INTO book_authors VALUES (1, 1, 0), (2, 2, 0), (2, 3, 1);
INSERT INTO book_tags VALUES (1, 1), (1, 2);
INSERT INTO identifiers VALUES (1, 'isbn', '9780000000002'), (2, 'google', 'abc');
INSERT INTO shelves VALUES (1, 'Favourites', NULL, 0), (2, 'Unread sea', 'tag:sea', 1);
INSERT INTO shelf_books VALUES (1, 1, 1500.0), (1, 2, 1501.0);
INSERT INTO annotations VALUES
    (1, 1, 'highlight', 'epubcfi(/6/4!/4/2,/1:0,/1:5)', 0.4, 'lamps', 'a note', 'blue',
     1300.0, 1300.0),
    (2, 1, 'bookmark', 'epubcfi(/6/8)', 0.7, '', '', 'yellow', 1301.0, 1301.0);
INSERT INTO sessions VALUES (1, 2, 3000.0, 600.0, 0.1, 0.9), (2, 2, 4000.0, 300.0, 0.9, 1.0),
    (3, 1, 5000.0, 120.0, 0.4, 0.5);
COMMIT;
""")
    db.close()


def _rows(path):
    """Every row of every table of a library file, by table, the columns by name."""
    db = sqlite3.connect(path)
    db.row_factory = sqlite3.Row
    try:
        tables = [row[0] for row in db.execute(
            "SELECT name FROM sqlite_master WHERE type = 'table' AND name NOT LIKE 'sqlite_%'")]
        return {table: sorted((dict(row) for row in db.execute(f'SELECT * FROM {table}')),
                              key=lambda row: sorted(row.items(), key=str))
                for table in tables}
    finally:
        db.close()


class MigrationTest(unittest.TestCase):

    def setUp(self):
        self.directory = tempfile.TemporaryDirectory(prefix='bookcase-schema-')
        self.path = os.path.join(self.directory.name, 'library.sqlite')
        make_v1_library(self.path)

    def tearDown(self):
        self.directory.cleanup()

    def test_a_version_1_library_migrates_with_nothing_lost(self):
        before = _rows(self.path)
        library = Library(self.path)
        try:
            self.assertEqual(library.db.execute('PRAGMA user_version').fetchone()[0],
                             schema.VERSION)
            harbour, train = library.book(1), library.book(2)
            self.assertEqual((harbour.title, harbour.authors, harbour.series,
                              harbour.series_index, harbour.tags, harbour.rating),
                             ('A Quiet Harbour', ('Ada Lark',), 'Tides', 2.0, ('Calm', 'Sea'),
                              8))
            self.assertEqual((harbour.status, harbour.progress, harbour.location,
                              harbour.has_cover, harbour.cover_version),
                             ('reading', 0.5, 'epubcfi(/6/4!/4/2)', True, 3))
            self.assertEqual(train.authors, ('Ben Ross', 'Cy Doe'))
            self.assertEqual((train.source, train.source_key), ('calibre', '42'))
            self.assertEqual(train.formats, ('epub', 'pdf'))
            self.assertFalse(train.missing)  # its PDF is there
            self.assertEqual([f.missing for f in library.files(2)], [False, True])
            self.assertEqual([(a.kind, a.note, a.color) for a in library.annotations(1)],
                             [('highlight', 'a note', 'blue'), ('bookmark', '', 'yellow')])
            self.assertEqual([(s.name, s.count) for s in library.shelves()],
                             [('Favourites', 2), ('Unread sea', 1)])
            self.assertEqual(len(library.sessions()), 3)
            self.assertEqual([(f.path, f.kind) for f in library.folders()],
                             [('/invented/watched', 'watched')])
            self.assertEqual(library.count(query='tides'), 1)
            self.assertEqual(library.count(query='author:doe'), 1)
            # Version 2: a finished book gets the end of its last session as its date.
            self.assertEqual(library.finished(), [(2, 4300.0)])
            self.assertEqual(library.page_counts(), {})
        finally:
            library.close()
        after = _rows(self.path)
        # Version 4: book_state, empty.
        self.assertEqual(after.pop('book_state'), [])
        self.assertEqual(set(after), set(before))
        for table, rows in before.items():
            if table == 'books':
                rows = [dict(row, finished=4300.0 if row['status'] == 'finished' else 0.0,
                             pages=0, source_values='') for row in rows]
            self.assertEqual(after[table], rows, table)

    def test_a_finished_book_without_sessions_gets_its_last_read_else_modified(self):
        db = sqlite3.connect(self.path)
        db.execute('DELETE FROM sessions')
        db.execute("UPDATE books SET status = 'finished', last_read = 1234.0 WHERE id = 1")
        db.commit()
        db.close()
        library = Library(self.path)
        try:
            self.assertEqual(library.finished(), [(1, 1234.0), (2, 2100.0)])
        finally:
            library.close()

    def test_the_migrated_library_takes_new_books_and_edits(self):
        library = Library(self.path)
        try:
            from tests.support import add_book

            book_id = add_book(library, 'Lantern Hill', ('Ada Lark',))
            library.set_status([book_id], 'finished')
            library.update_book(1, title='A Quiet Harbour Again')
            self.assertEqual(library.undo(), 'Edit Book')
            self.assertEqual(library.book(1).title, 'A Quiet Harbour')
            self.assertEqual([book.id for book in library.books(sort='added')],
                             [book_id, 2, 1])
        finally:
            library.close()

    def test_version_4_keeps_a_books_reader_state_and_removes_it_with_the_book(self):
        library = Library(self.path)
        try:
            self.assertEqual(library.book_state(2), {})
            library.set_book_state(2, 'pdf', {'fit': 'width', 'rtl': True})
            library.set_book_state(2, 'other', 1)
            library.set_book_state(2, 'other', None)
            self.assertEqual(library.book_state(2), {'pdf': {'fit': 'width', 'rtl': True}})
            library.db.execute("UPDATE book_state SET state = 'not json' WHERE book_id = 2")
            self.assertEqual(library.book_state(2), {})
            library.set_book_state(2, 'pdf', {'zoom': 150})
            library.remove_books([2])
            self.assertEqual(library.book_state(2), {})
            library.undo()
            self.assertEqual(library.book_state(2), {'pdf': {'zoom': 150}})
        finally:
            library.close()

    def test_a_failed_migration_leaves_the_file_as_it_was(self):
        before = _rows(self.path)
        broken = dict(schema.MIGRATIONS)
        broken[2] = schema.MIGRATIONS[2] + '\nUPDATE no_such_table SET x = 1;\n'
        original = schema.MIGRATIONS
        schema.MIGRATIONS = broken
        try:
            with self.assertRaises(sqlite3.Error):
                Library(self.path)
            # On a connection kept open, the failed steps are rolled back, not left in a
            # transaction a later write would commit.
            db = sqlite3.connect(self.path, isolation_level=None)
            try:
                with self.assertRaises(sqlite3.Error):
                    schema.apply(db)
                self.assertFalse(db.in_transaction)
                self.assertNotIn('finished', [row[1] for row in
                                              db.execute('PRAGMA table_info(books)')])
            finally:
                db.close()
        finally:
            schema.MIGRATIONS = original
        db = sqlite3.connect(self.path)
        try:
            self.assertEqual(db.execute('PRAGMA user_version').fetchone()[0], 1)
            self.assertFalse(db.in_transaction)
        finally:
            db.close()
        self.assertEqual(_rows(self.path), before)
        library = Library(self.path)  # and the real migration still runs afterwards
        try:
            self.assertEqual(library.finished(), [(2, 4300.0)])
        finally:
            library.close()


if __name__ == '__main__':
    unittest.main()
