# SPDX-License-Identifier: GPL-2.0-or-later
# SPDX-FileCopyrightText: 2026 Jack Tully

"""calibre.py and Importer.link_calibre(): a synthetic Calibre library, built here from
Calibre's own schema (docs/research/calibre_metadata_sqlite.sql) with the functions
Calibre registers, read in place, never written, read through a copy when locked, and
rescanned after Calibre changes a book or loses one. Everything is invented."""

import hashlib
import pathlib
import re
import shutil
import sqlite3
import tempfile
import unittest
import uuid

from tests import ROOT
from tests.support import make_epub, make_png, temporary_library
from tests.test_formats import make_mobi
from bookcase import calibre
from bookcase.covers import CoverStore
from bookcase.importing import Importer

SCHEMA = ROOT / 'docs' / 'research' / 'calibre_metadata_sqlite.sql'


def _title_sort(title, *_args):
    title = (title or '').strip()
    match = re.match(r'^(A\s+|The\s+|An\s+)', title, re.I)
    return f'{title[len(match.group(1)):]}, {match.group(1).strip()}' if match else title


class _Concat:
    def __init__(self):
        self.values = []

    def step(self, value):
        if value is not None:
            self.values.append(value)

    def finalize(self):
        return ','.join(self.values) if self.values else None


def _sortconcat(separator):
    class SortConcat:
        def __init__(self):
            self.values = {}

        def step(self, index, value):
            if value is not None:
                self.values[index] = value

        def finalize(self):
            return separator.join(self.values[k] for k in sorted(self.values)) \
                if self.values else None
    return SortConcat


class _IdentifiersConcat:
    def __init__(self):
        self.values = []

    def step(self, key, value):
        self.values.append(f'{key}:{value}')

    def finalize(self):
        return ','.join(self.values) if self.values else None


class _AumSortConcat:
    def __init__(self):
        self.values = {}

    def step(self, index, author, sort, link):
        if author is not None:
            self.values[index] = ':::'.join((author, sort or '', link or ''))

    def finalize(self):
        return ':#:'.join(self.values[k] for k in sorted(self.values)) if self.values else None


def _collate(a, b):
    return (a.lower() > b.lower()) - (a.lower() < b.lower())


def connect(path):
    """A connection to a Calibre metadata.db with Calibre's functions (tech.md §6)."""
    db = sqlite3.connect(path, isolation_level=None)
    db.create_function('title_sort', 1, _title_sort, deterministic=True)
    db.create_function('author_to_author_sort', 1, lambda a: a, deterministic=True)
    db.create_function('uuid4', 0, lambda: str(uuid.uuid4()))
    db.create_function('books_list_filter', 1, lambda x: 1)
    db.create_aggregate('concat', 1, _Concat)
    db.create_aggregate('sortconcat', 2, _sortconcat(','))
    db.create_aggregate('sortconcat_bar', 2, _sortconcat('|'))
    db.create_aggregate('sortconcat_amper', 2, _sortconcat('&'))
    db.create_aggregate('identifiers_concat', 2, _IdentifiersConcat)
    db.create_aggregate('aum_sortconcat', 4, _AumSortConcat)
    db.create_collation('PYNOCASE', _collate)
    db.create_collation('icucollate', _collate)
    return db


class CalibreLibrary:
    """A Calibre library folder, filled the way Calibre does it."""

    def __init__(self, folder):
        self.folder = pathlib.Path(folder)
        self.folder.mkdir(parents=True, exist_ok=True)
        self.db = connect(self.folder / 'metadata.db')
        self.db.executescript(SCHEMA.read_text())

    def close(self):
        self.db.close()

    def _upsert(self, table, column, value, **extra):
        row = self.db.execute(f'SELECT id FROM {table} WHERE {column} = ?', (value,)).fetchone()
        if row:
            return row[0]
        names = [column, *extra]
        marks = ','.join('?' * len(names))
        cursor = self.db.execute(f'INSERT INTO {table} ({",".join(names)}) VALUES ({marks})',
                                 (value, *extra.values()))
        return cursor.lastrowid

    def add(self, title, authors, files, series=None, series_index=1.0, tags=(), publisher=None,
            pubdate='0101-01-01 00:00:00+00:00', languages=(), comments=None, rating=None,
            identifiers=None, cover=None, modified='2026-01-02 03:04:05.000000+00:00'):
        """files: {'EPUB': bytes-writing function(path)}; returns the Calibre book id."""
        self.db.execute('BEGIN')
        book_id = self.db.execute(
            'INSERT INTO books (title, series_index, author_sort, timestamp, pubdate, '
            'last_modified, path, has_cover) VALUES (?, ?, ?, ?, ?, ?, ?, ?)',
            (title, series_index, authors[0] if authors else '', '2026-01-01 00:00:00+00:00',
             pubdate, modified, '', 1 if cover else 0)).lastrowid
        for author in authors:
            author_id = self._upsert('authors', 'name', author, sort=author)
            self.db.execute('INSERT INTO books_authors_link (book, author) VALUES (?, ?)',
                            (book_id, author_id))
        path = f'{authors[0] if authors else "Unknown"}/{title} ({book_id})'
        self.db.execute('UPDATE books SET path = ? WHERE id = ?', (path, book_id))
        directory = self.folder / path
        directory.mkdir(parents=True)
        name = f'{title} - {authors[0] if authors else "Unknown"}'
        for format, write in files.items():
            write(directory / f'{name}.{format.lower()}')
            self.db.execute('INSERT INTO data (book, format, uncompressed_size, name) '
                            'VALUES (?, ?, ?, ?)', (book_id, format, 100, name))
        if series:
            series_id = self._upsert('series', 'name', series)
            self.db.execute('INSERT INTO books_series_link (book, series) VALUES (?, ?)',
                            (book_id, series_id))
        for tag in tags:
            tag_id = self._upsert('tags', 'name', tag)
            self.db.execute('INSERT INTO books_tags_link (book, tag) VALUES (?, ?)',
                            (book_id, tag_id))
        if publisher:
            publisher_id = self._upsert('publishers', 'name', publisher)
            self.db.execute('INSERT INTO books_publishers_link (book, publisher) VALUES (?, ?)',
                            (book_id, publisher_id))
        for order, code in enumerate(languages):
            language_id = self._upsert('languages', 'lang_code', code)
            self.db.execute('INSERT INTO books_languages_link (book, lang_code, item_order) '
                            'VALUES (?, ?, ?)', (book_id, language_id, order))
        if comments:
            self.db.execute('INSERT INTO comments (book, text) VALUES (?, ?)',
                            (book_id, comments))
        if rating is not None:
            rating_id = self._upsert('ratings', 'rating', rating)
            self.db.execute('INSERT INTO books_ratings_link (book, rating) VALUES (?, ?)',
                            (book_id, rating_id))
        for kind, value in (identifiers or {}).items():
            self.db.execute('INSERT INTO identifiers (book, type, val) VALUES (?, ?, ?)',
                            (book_id, kind, value))
        if cover:
            (directory / 'cover.jpg').write_bytes(cover)
        self.db.execute('COMMIT')
        return book_id


def epub_writer(**fields):
    return lambda path: make_epub(path, **fields)


class CalibreTestCase(unittest.TestCase):

    def setUp(self):
        self.directory = pathlib.Path(tempfile.mkdtemp(prefix='bookcase-calibre-'))
        self.calibre = CalibreLibrary(self.directory / 'Calibre Library')
        self.first = self.calibre.add(
            'The Lantern Keeper', ['Cara Moss', 'Dev Okafor'],
            {'EPUB': epub_writer(title='Lantern'),
             'MOBI': lambda path: make_mobi(path, title='Lantern')},
            series='Lights', series_index=2.5, tags=('Night', 'Coast'), publisher='Quay Books',
            pubdate='2019-04-02 00:00:00+00:00', languages=('eng', 'fra'),
            comments='<p>A keeper and a <b>lamp</b>.</p>', rating=8,
            identifiers={'isbn': '9780306406157', 'google': 'abc123'},
            cover=make_png(4, 6))
        self.second = self.calibre.add('Unread Tides', ['Ada Lark'],
                                       {'PDF': lambda path: path.write_bytes(b'%PDF-1.4\n')})
        self.third = self.calibre.add('No Files', ['Ada Lark'], {})
        self.calibre.db.execute('UPDATE books SET has_cover = 0 WHERE id = ?', (self.second,))

    def tearDown(self):
        self.calibre.close()
        shutil.rmtree(self.directory, ignore_errors=True)

    def database_digest(self):
        return hashlib.sha256((self.calibre.folder / 'metadata.db').read_bytes()).hexdigest()


class TestRead(CalibreTestCase):

    def test_read_library(self):
        self.assertTrue(calibre.is_library(self.calibre.folder))
        self.assertFalse(calibre.is_library(self.directory))
        before = self.database_digest()
        books = {book.id: book for book in calibre.read_library(self.calibre.folder)}
        self.assertEqual(self.database_digest(), before)
        book = books[self.first]
        info = book.info
        self.assertEqual(info.title, 'The Lantern Keeper')
        self.assertEqual(info.authors, ['Cara Moss', 'Dev Okafor'])
        self.assertEqual((info.series, info.series_index), ('Lights', 2.5))
        self.assertEqual(sorted(info.tags), ['Coast', 'Night'])
        self.assertEqual(info.publisher, 'Quay Books')
        self.assertEqual(info.published, '2019-04-02')
        self.assertEqual(info.language, 'en')
        self.assertEqual(info.description, '<p>A keeper and a <b>lamp</b>.</p>')
        self.assertEqual(info.identifiers['isbn'], '9780306406157')
        self.assertEqual(info.identifiers['google'], 'abc123')
        self.assertTrue(info.identifiers['uuid'])
        self.assertEqual(book.rating, 8)
        self.assertEqual(set(book.files), {'epub', 'mobi'})
        self.assertTrue(book.files['epub'].endswith(
            'Cara Moss/The Lantern Keeper (1)/The Lantern Keeper - Cara Moss.epub'))
        self.assertTrue(book.cover_path.endswith('cover.jpg'))
        self.assertEqual(book.last_modified, '2026-01-02 03:04:05.000000+00:00')
        self.assertEqual(books[self.second].info.published, '')
        self.assertEqual(books[self.second].info.series_index, 0.0)
        self.assertIsNone(books[self.second].cover_path)
        self.assertEqual(books[self.third].files, {})

    def test_locked_database_is_read_through_a_copy(self):
        self.calibre.db.execute('BEGIN EXCLUSIVE')
        self.calibre.db.execute("UPDATE books SET title = 'Uncommitted' WHERE id = 1")
        try:
            books = calibre.read_library(self.calibre.folder)
        finally:
            self.calibre.db.execute('ROLLBACK')
        self.assertEqual(books[0].info.title, 'The Lantern Keeper')

    def test_not_a_library(self):
        with self.assertRaises(calibre.CalibreError):
            calibre.read_library(self.directory)
        (self.directory / 'metadata.db').write_bytes(b'junk' * 100)
        with self.assertRaises(calibre.CalibreError):
            calibre.read_library(self.directory)


class TestLink(CalibreTestCase):

    def test_link_and_rescan(self):
        with temporary_library() as library:
            covers = CoverStore(pathlib.Path(library.path).parent, library)
            importer = Importer(library, covers, self.directory / 'Books')
            before = self.database_digest()
            report = importer.link_calibre(self.calibre.folder)
            self.assertEqual(self.database_digest(), before)
            self.assertEqual(len(report.added), 2)
            self.assertEqual(len(report.failed), 1)  # the book with no files
            self.assertFalse((self.directory / 'Books').exists())
            book_id = library.find_by_source_key('calibre', str(self.first))
            book = library.book(book_id)
            self.assertEqual(book.source, 'calibre')
            self.assertEqual(book.authors, ('Cara Moss', 'Dev Okafor'))
            self.assertEqual(book.rating, 8)
            self.assertEqual(book.series, 'Lights')
            self.assertEqual(set(book.formats), {'epub', 'mobi'})
            self.assertTrue(book.has_cover)
            self.assertEqual(pathlib.Path(covers.path(book)).read_bytes(), make_png(4, 6))
            self.assertIn('calibre', [folder.kind for folder in library.folders()])
            self.assertTrue(all(file.path.startswith(str(self.calibre.folder))
                                for file in library.files(book_id)))

            # Unchanged: nothing to do.
            report = importer.link_calibre(self.calibre.folder)
            self.assertEqual((report.added, report.updated, report.missing), ([], [], []))

            # Calibre changes a title; a book is deleted from Calibre (its files go too).
            self.calibre.db.execute("UPDATE books SET title = 'The Lamp Keeper', "
                                    "last_modified = '2026-02-01 00:00:00+00:00' WHERE id = ?",
                                    (self.first,))
            second_id = library.find_by_source_key('calibre', str(self.second))
            self.calibre.db.execute('DELETE FROM books WHERE id = ?', (self.second,))
            report = importer.link_calibre(self.calibre.folder)
            self.assertEqual(report.updated, [book_id])
            self.assertEqual(library.book(book_id).title, 'The Lamp Keeper')
            self.assertEqual(library.book(book_id).rating, 8)
            self.assertEqual(library.book(book_id).cover_version, book.cover_version)
            self.assertEqual(len(report.missing), 1)
            self.assertTrue(library.book(second_id).missing)
            covers.shutdown()


if __name__ == '__main__':
    unittest.main()
