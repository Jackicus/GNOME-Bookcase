# SPDX-License-Identifier: GPL-2.0-or-later
# SPDX-FileCopyrightText: 2026 Jack Tully

"""calibre_write.py (Keep Calibre in Step): edits written into a synthetic Calibre library
built from Calibre's own schema (docs/research/calibre_metadata_sqlite.sql, tests/
test_calibre.py's CalibreLibrary), read back through calibre.py and checked row by row as
Calibre would have left them; the refusals, Calibre's lock (simulated by binding the
abstract socket), the backups, and the round trip through a linked Bookcase library. When
Calibre's calibredb is installed the result is also read with it; otherwise this is
verified against the schema only. Everything is invented."""

import datetime
import os
import pathlib
import shutil
import socket
import sqlite3
import subprocess
import unittest
import uuid

from tests import ROOT  # noqa: F401
from tests.support import make_png, temporary_library
from tests.test_calibre import CalibreTestCase
from bookcase import calibre, calibre_write
from bookcase.calibre_write import (CalibreBusy, CalibreRefused, author_to_author_sort,
                                    language_code, pubdate, title_sort)
from bookcase.covers import CoverStore
from bookcase.importing import Importer


class TestRules(unittest.TestCase):

    def test_title_sort(self):
        self.assertEqual(title_sort('The Hobbit'), 'Hobbit, The')
        self.assertEqual(title_sort('A Quiet Harbour'), 'Quiet Harbour, A')
        self.assertEqual(title_sort('“The Lamp”'), 'Lamp, The')
        self.assertEqual(title_sort('Theory of Tides'), 'Theory of Tides')

    def test_author_sort(self):
        self.assertEqual(author_to_author_sort('Ada Lark'), 'Lark, Ada')
        self.assertEqual(author_to_author_sort('J. R. Moss'), 'Moss, J. R.')
        self.assertEqual(author_to_author_sort('Dev Okafor Jr.'), 'Okafor, Dev Jr.')
        self.assertEqual(author_to_author_sort('Dr. Cara Moss'), 'Moss, Cara')
        self.assertEqual(author_to_author_sort('Lark, Ada'), 'Lark, Ada')
        self.assertEqual(author_to_author_sort('Quay Books Company'), 'Quay Books Company')
        self.assertEqual(author_to_author_sort('Plato'), 'Plato')

    def test_values(self):
        self.assertEqual(pubdate('2019-04-02'), '2019-04-02 12:00:00+00:00')
        self.assertEqual(pubdate('2019-04'), '2019-04-15 12:00:00+00:00')
        self.assertEqual(pubdate('2019'), '2019-01-15 12:00:00+00:00')
        self.assertEqual(pubdate(''), calibre_write.UNDEFINED_DATE)
        self.assertEqual(language_code('en'), 'eng')
        self.assertEqual(language_code('de'), 'deu')
        self.assertEqual(language_code('fra'), 'fra')

    def test_lock_address_is_calibres(self):
        # calibre/utils/lock.py: '\0' + f'calibre-singleinstance-{geteuid()}-db'
        self.assertEqual(calibre_write.lock_address(),
                         f'\0calibre-singleinstance-{os.geteuid()}-db')


class WriteTestCase(CalibreTestCase):

    def setUp(self):
        super().setUp()
        # A lock name of our own, so a Calibre running on the machine changes nothing.
        self.address = f'\0bookcase-test-lock-{uuid.uuid4().hex}'

    def write(self, changes, **kwargs):
        return calibre_write.write_changes(self.calibre.folder, changes, address=self.address,
                                           **kwargs)

    def row(self, sql, *params):
        return self.calibre.db.execute(sql, params).fetchone()

    def rows(self, sql, *params):
        return self.calibre.db.execute(sql, params).fetchall()

    def book(self, calibre_id):
        return next(book for book in calibre.read_library(self.calibre.folder)
                    if book.id == calibre_id)


class TestWrite(WriteTestCase):

    def test_round_trip(self):
        before = self.row('SELECT path, uuid, last_modified FROM books WHERE id = ?',
                          self.first)
        folder_files = sorted(os.listdir(self.calibre.folder / before[0]))
        result = self.write({self.first: {
            'title': 'The Night Lantern', 'authors': ['Lark, Ada', 'Ben Ross'],
            'series': 'Beacons', 'series_index': 3.0, 'tags': ['Coast', 'Storm'],
            'publisher': 'Harbour Press', 'published': '2020-05-06', 'language': 'de',
            'description': '<p>New words.</p>', 'rating': 6,
            'identifiers': {'isbn': '9780000000002', 'Goodreads:': '12,34', 'uuid': 'x'},
            'cover': make_png(3, 5),
        }})
        self.assertEqual(set(result), {self.first})
        self.assertEqual(result[self.first].previous, before[2])

        # Read back as Bookcase reads a Calibre library.
        book = self.book(self.first)
        self.assertEqual(book.info.title, 'The Night Lantern')
        self.assertEqual(book.info.authors, ['Lark, Ada', 'Ben Ross'])
        self.assertEqual((book.info.series, book.info.series_index), ('Beacons', 3.0))
        self.assertEqual(sorted(book.info.tags), ['Coast', 'Storm'])
        self.assertEqual(book.info.publisher, 'Harbour Press')
        self.assertEqual(book.info.published, '2020-05-06')
        self.assertEqual(book.info.language, 'de')
        self.assertEqual(book.info.description, '<p>New words.</p>')
        self.assertEqual(book.rating, 6)
        self.assertEqual(book.info.identifiers['isbn'], '9780000000002')
        self.assertEqual(book.info.identifiers['goodreads'], '12|34')
        self.assertEqual(book.info.identifiers['uuid'], before[1])  # never changed
        self.assertEqual(book.author_sort, 'Lark, Ada & Ross, Ben')

        # The rows, as Calibre leaves them.
        self.assertEqual(self.row('SELECT sort FROM books WHERE id = ?', self.first)[0],
                         'Night Lantern, The')
        self.assertEqual(self.rows(
            'SELECT a.name, a.sort FROM books_authors_link l JOIN authors a ON a.id = l.author '
            'WHERE l.book = ? ORDER BY l.id', self.first),
            [('Lark| Ada', 'Lark, Ada'), ('Ben Ross', 'Ross, Ben')])
        self.assertEqual(self.row('SELECT sort FROM series WHERE name = ?', 'Beacons')[0],
                         'Beacons')
        self.assertEqual(self.rows('SELECT g.lang_code FROM books_languages_link l JOIN '
                                   'languages g ON g.id = l.lang_code WHERE l.book = ? '
                                   'ORDER BY l.item_order', self.first),
                         [('deu',), ('fra',)])  # the first replaced, Calibre's others kept
        self.assertEqual(self.row('SELECT r.rating FROM books_ratings_link l JOIN ratings r '
                                  'ON r.id = l.rating WHERE l.book = ?', self.first)[0], 6)
        self.assertEqual(self.row('SELECT pubdate FROM books WHERE id = ?', self.first)[0],
                         '2020-05-06 12:00:00+00:00')
        # Items no book uses any more are gone, as Calibre does it.
        self.assertIsNone(self.row("SELECT id FROM tags WHERE name = 'Night'"))
        self.assertIsNone(self.row("SELECT id FROM series WHERE name = 'Lights'"))
        self.assertIsNone(self.row("SELECT id FROM authors WHERE name = 'Dev Okafor'"))
        self.assertIsNotNone(self.row("SELECT id FROM authors WHERE name = 'Ada Lark'"))
        # last_modified, dirtied (Calibre rewrites metadata.opf), the cover.
        modified, has_cover, path, book_uuid = self.row(
            'SELECT last_modified, has_cover, path, uuid FROM books WHERE id = ?', self.first)
        self.assertEqual(modified, result[self.first].last_modified)
        self.assertGreater(modified, before[2])
        self.assertRegex(modified, r'^\d{4}-\d\d-\d\d \d\d:\d\d:\d\d(\.\d+)?\+00:00$')
        self.assertEqual(self.rows('SELECT book FROM metadata_dirtied'), [(self.first,)])
        self.assertEqual(has_cover, 1)
        cover = (self.calibre.folder / path / 'cover.jpg').read_bytes()
        self.assertEqual(cover[:3], b'\xff\xd8\xff')  # a JPEG, made from the PNG
        # Nothing renamed: the same path, uuid and files (and the new cover).
        self.assertEqual((path, book_uuid), before[:2])
        self.assertEqual(sorted(os.listdir(self.calibre.folder / path)), folder_files)
        self.assertEqual(self.row('PRAGMA integrity_check')[0], 'ok')
        # Another book is untouched.
        self.assertEqual(self.row('SELECT last_modified FROM books WHERE id = ?',
                                  self.second)[0], '2026-01-02 03:04:05.000000+00:00')

    def test_case_change_renames_the_item_for_every_book(self):
        self.calibre.db.execute("UPDATE books SET last_modified = '2026-01-02' WHERE id = ?",
                                (self.second,))
        tag = self.row("SELECT id FROM tags WHERE name = 'Coast'")[0]
        self.calibre.db.execute('INSERT INTO books_tags_link (book, tag) VALUES (?, ?)',
                                (self.second, tag))
        self.write({self.first: {'tags': ['coast', 'Night']}})
        self.assertEqual(self.row('SELECT id, name FROM tags WHERE id = ?', tag), (tag, 'coast'))
        self.assertEqual({row[0] for row in self.rows('SELECT book FROM metadata_dirtied')},
                         {self.first, self.second})
        self.assertNotEqual(self.row('SELECT last_modified FROM books WHERE id = ?',
                                     self.second)[0], '2026-01-02')

    def test_clearing_values(self):
        self.write({self.first: {'series': '', 'tags': [], 'publisher': '', 'rating': 0,
                                 'description': '', 'identifiers': {}, 'published': '',
                                 'authors': [], 'cover': None}})
        book = self.book(self.first)
        self.assertEqual((book.info.series, book.info.tags, book.info.publisher, book.rating),
                         ('', [], '', 0))
        self.assertEqual(book.info.authors, [])  # 'Unknown', Calibre's word for none
        self.assertEqual(self.row('SELECT a.name FROM books_authors_link l JOIN authors a '
                                  'ON a.id = l.author WHERE l.book = ?', self.first)[0],
                         'Unknown')
        self.assertEqual(self.row('SELECT pubdate, has_cover FROM books WHERE id = ?',
                                  self.first), (calibre_write.UNDEFINED_DATE, 0))
        self.assertIsNone(self.row('SELECT 1 FROM comments WHERE book = ?', self.first))
        self.assertIsNone(book.cover_path)

    def test_a_noted_item_is_kept(self):
        notes = self.calibre.folder / '.calnotes'
        notes.mkdir()
        tag = self.row("SELECT id FROM tags WHERE name = 'Night'")[0]
        with sqlite3.connect(notes / 'notes.db') as db:
            db.execute('CREATE TABLE notes (id INTEGER PRIMARY KEY, item INTEGER, '
                       'colname TEXT, doc TEXT)')
            db.execute("INSERT INTO notes (item, colname, doc) VALUES (?, 'tags', 'x')",
                       (tag,))
        db.close()
        self.write({self.first: {'tags': ['Coast']}})
        self.assertIsNotNone(self.row('SELECT 1 FROM tags WHERE id = ?', tag))

    def test_a_book_gone_from_calibre_is_left_out(self):
        self.assertEqual(self.write({999: {'title': 'Nobody'}}), {})


class TestSafety(WriteTestCase):

    def assert_unchanged(self, call, error):
        before = self.database_digest()
        with self.assertRaises(error):
            call()
        self.assertEqual(self.database_digest(), before)

    def test_a_newer_calibre_is_refused(self):
        self.calibre.db.execute('PRAGMA user_version = 29')
        self.assert_unchanged(lambda: self.write({self.first: {'title': 'X'}}), CalibreRefused)

    def test_an_old_library_is_refused(self):
        self.calibre.db.execute('DROP TABLE metadata_dirtied')
        self.assert_unchanged(lambda: self.write({self.first: {'title': 'X'}}), CalibreRefused)

    def test_another_application_is_refused(self):
        self.calibre.db.execute('PRAGMA application_id = 1234')
        self.assert_unchanged(lambda: self.write({self.first: {'title': 'X'}}), CalibreRefused)

    def test_nothing_is_written_while_calibre_runs(self):
        calibre_app = socket.socket(socket.AF_UNIX)  # what Calibre's SingleInstance does
        calibre_app.bind(self.address)
        try:
            self.assertTrue(calibre_write.calibre_running(self.address))
            self.assert_unchanged(lambda: self.write({self.first: {'title': 'X'}}),
                                  CalibreBusy)
            self.assertEqual(calibre_write.backups(self.calibre.folder), [])
        finally:
            calibre_app.close()
        self.assertFalse(calibre_write.calibre_running(self.address))

    def test_calibre_cannot_start_while_bookcase_writes(self):
        with calibre_write.calibre_lock(self.address):
            calibre_app = socket.socket(socket.AF_UNIX)
            with self.assertRaises(OSError):
                calibre_app.bind(self.address)  # Calibre: "Cannot start calibre"
            calibre_app.close()

    def test_a_busy_database_is_not_written(self):
        self.calibre.db.execute('BEGIN IMMEDIATE')  # someone else is writing
        try:
            original = calibre_write.sqlite3.connect

            def quick(*args, **kwargs):
                kwargs['timeout'] = 0.1
                return original(*args, **kwargs)

            calibre_write.sqlite3.connect = quick
            try:
                with self.assertRaises(CalibreBusy):
                    self.write({self.first: {'title': 'X'}})
            finally:
                calibre_write.sqlite3.connect = original
        finally:
            self.calibre.db.execute('ROLLBACK')
        self.assertEqual(self.book(self.first).info.title, 'The Lantern Keeper')

    def test_backups(self):
        folder = self.calibre.folder
        day = datetime.date(2026, 10, 1)
        self.write({self.first: {'title': 'One'}}, today=day)
        first = folder / 'metadata.db.bookcase-backup-20261001'
        self.assertTrue(first.exists())
        with sqlite3.connect(first) as copy:  # the library as it was before the write
            self.assertEqual(copy.execute('SELECT title FROM books WHERE id = ?',
                                          (self.first,)).fetchone()[0], 'The Lantern Keeper')
        copy.close()
        self.write({self.first: {'title': 'Two'}}, today=day)  # once a day
        self.assertEqual(len(calibre_write.backups(folder)), 1)
        for offset in range(1, 5):
            self.write({self.first: {'title': f'Day {offset}'}},
                       today=day + datetime.timedelta(days=offset))
        names = [os.path.basename(path) for path in calibre_write.backups(folder)]
        self.assertEqual(names, [f'metadata.db.bookcase-backup-2026100{n}' for n in (3, 4, 5)])
        self.assertFalse(any(name.endswith('.part') for name in os.listdir(folder)))


class TestFlush(WriteTestCase):

    def setUp(self):
        super().setUp()
        self._library = temporary_library()
        self.library = self._library.__enter__()
        self.covers = CoverStore(pathlib.Path(self.library.path).parent, self.library)
        self.importer = Importer(self.library, self.covers, self.directory / 'Books')
        self.importer.link_calibre(self.calibre.folder)
        self.book_id = self.library.find_by_source_key('calibre', str(self.first))

    def tearDown(self):
        self.covers.shutdown()
        self._library.__exit__(None, None, None)
        super().tearDown()

    def flush(self):
        return calibre_write.flush(self.library, self.covers, self.calibre.folder,
                                   address=self.address)

    def pending(self):
        return calibre_write.pending(self.library, self.covers, self.calibre.folder)

    def test_nothing_pending_after_linking(self):
        before = self.database_digest()
        self.assertEqual(self.pending(), {})
        self.assertEqual(self.flush().written, [])
        self.assertEqual(self.database_digest(), before)

    def test_an_edit_is_written_and_the_rescan_keeps_it(self):
        self.library.update_book(self.book_id, title='Lamp at Dusk', tags=['Coast'], rating=10,
                                 authors=['Cara Moss', 'Eve Stone'])
        edits = self.pending()
        self.assertEqual(set(edits), {self.book_id})
        self.assertEqual(set(edits[self.book_id].fields), {'title', 'tags', 'rating', 'authors'})
        report = self.flush()
        self.assertEqual(report.written, [self.book_id])
        self.assertEqual(self.pending(), {})
        book = self.book(self.first)
        self.assertEqual((book.info.title, book.info.tags, book.rating, book.info.authors),
                         ('Lamp at Dusk', ['Coast'], 10, ['Cara Moss', 'Eve Stone']))
        # Calibre's sort of the authors: the one it had kept, the new one's made.
        self.assertEqual(book.author_sort, 'Cara Moss & Stone, Eve')
        self.assertEqual(self.library.book(self.book_id).source_modified, book.last_modified)
        # The rescan finds nothing to take.
        report = self.importer.link_calibre(self.calibre.folder)
        self.assertEqual(report.updated, [])
        self.assertEqual(self.library.book(self.book_id).title, 'Lamp at Dusk')

    def test_calibres_own_change_still_comes_in(self):
        # Calibre changes the publisher while Bookcase edits the title.
        self.calibre.db.execute("UPDATE books SET last_modified = '2026-05-01 00:00:00+00:00' "
                                'WHERE id = ?', (self.first,))
        publisher = self.calibre._upsert('publishers', 'name', 'Tide House')
        self.calibre.db.execute('UPDATE books_publishers_link SET publisher = ? WHERE book = ?',
                                (publisher, self.first))
        self.library.update_book(self.book_id, title='Lamp at Dusk')
        self.flush()
        book = self.book(self.first)
        self.assertEqual((book.info.title, book.info.publisher), ('Lamp at Dusk', 'Tide House'))
        report = self.importer.link_calibre(self.calibre.folder)
        self.assertEqual(report.updated, [self.book_id])
        ours = self.library.book(self.book_id)
        self.assertEqual((ours.title, ours.publisher), ('Lamp at Dusk', 'Tide House'))
        self.assertEqual(self.pending(), {})

    def test_waiting_for_calibre(self):
        self.library.update_book(self.book_id, series_index=4)
        calibre_app = socket.socket(socket.AF_UNIX)
        calibre_app.bind(self.address)
        try:
            with self.assertRaises(CalibreBusy):
                self.flush()
        finally:
            calibre_app.close()
        self.assertEqual(len(self.pending()), 1)  # still waiting
        self.flush()
        self.assertEqual(self.book(self.first).info.series_index, 4.0)
        self.assertEqual(self.pending(), {})

    def test_undo_is_written_too(self):
        self.library.update_book(self.book_id, publisher='Harbour Press')
        self.flush()
        self.library.undo()
        self.assertEqual(set(self.pending()), {self.book_id})
        self.flush()
        self.assertEqual(self.book(self.first).info.publisher, 'Quay Books')

    def test_a_cover_set_in_bookcase(self):
        self.covers.save(self.book_id, make_png(7, 9, (10, 200, 10)))
        self.assertEqual(set(self.pending()[self.book_id].fields), {'cover'})
        self.flush()
        self.assertEqual(self.pending(), {})
        path = self.row('SELECT path FROM books WHERE id = ?', self.first)[0]
        self.assertEqual((self.calibre.folder / path / 'cover.jpg').read_bytes()[:2],
                         b'\xff\xd8')
        # The book did not change in Calibre since the write: the rescan has nothing to do.
        self.assertEqual(self.importer.link_calibre(self.calibre.folder).updated, [])

    def test_a_book_that_is_not_this_calibre_book_is_skipped(self):
        self.calibre.db.execute("UPDATE books SET uuid = 'other', path = 'Elsewhere/X (1)' "
                                'WHERE id = ?', (self.first,))
        self.library.update_book(self.book_id, title='Lamp at Dusk')
        report = self.flush()
        self.assertEqual(report.skipped, [self.book_id])
        self.assertEqual(self.book(self.first).info.title, 'The Lantern Keeper')

    def test_status(self):
        status = calibre_write.Status(True, 'waiting', 3)
        self.assertIn('3', status.describe())
        self.assertIn('Calibre', status.describe())
        self.assertTrue(calibre_write.Status().describe())

    @unittest.skipUnless(shutil.which('calibredb'), 'calibredb is not installed: verified '
                                                    'against the schema only')
    def test_calibre_reads_what_was_written(self):  # pragma: no cover - needs Calibre
        self.library.update_book(self.book_id, title='Lamp at Dusk', authors=['Lark, Ada'])
        self.flush()
        output = subprocess.run(
            ['calibredb', '--with-library', str(self.calibre.folder), 'list', '--fields',
             'title,authors', '--for-machine'], capture_output=True, text=True, check=True)
        self.assertIn('Lamp at Dusk', output.stdout)
        self.assertIn('Lark, Ada', output.stdout)


    def test_a_cover_that_cannot_be_written_still_waits(self):
        self.covers.save(self.book_id, make_png(7, 9, (10, 200, 10)))
        original = calibre_write._jpeg
        calibre_write._jpeg = lambda data: None
        try:
            self.flush()
        finally:
            calibre_write._jpeg = original
        self.assertEqual(set(self.pending()[self.book_id].fields), {'cover'})
        self.flush()
        self.assertEqual(self.pending(), {})

    def test_the_coordinator_writes(self):
        # CalibreSync._start called Library.closed, a property, as a method: nothing was
        # ever written by the app.
        from gi.repository import GLib

        sync = calibre_write.CalibreSync(self.library, self.covers, address=self.address)
        try:
            self.library.update_book(self.book_id, title='Lamp at Dusk')
            sync._enabled.add(sync._key(self.calibre.folder))
            sync._start()
            self.assertIsNotNone(sync._thread)
            sync._thread.join(timeout=30)
            context = GLib.MainContext.default()
            for _ in range(100):
                if sync._thread is None:
                    break
                context.iteration(False)
            self.assertIsNone(sync._thread)
            self.assertEqual(self.book(self.first).info.title, 'Lamp at Dusk')
            self.assertEqual(sync.status(self.calibre.folder).state, 'idle')
        finally:
            sync.shutdown()



class TestAuditFixes(WriteTestCase):
    """Regressions found by the safety review of Keep Calibre in Step."""

    def test_author_sort_drops_nested_brackets_as_calibre_does(self):
        # calibre's remove_bracketed_text: nested pairs, and all after an unclosed '('.
        self.assertEqual(author_to_author_sort('Ada (the (elder)) Lark'),
                         'Lark, Ada')
        self.assertEqual(author_to_author_sort('Ada Lark (editor'), 'Lark, Ada')

    def test_language_codes_are_calibres(self):
        self.assertEqual(language_code('en-GB'), 'eng')
        self.assertEqual(language_code('pt_BR'), 'por')
        self.assertEqual(language_code('und'), '')
        self.write({self.first: {'language': 'de-AT'}})
        codes = [row[0] for row in self.rows(
            'SELECT g.lang_code FROM books_languages_link l JOIN languages g '
            'ON g.id = l.lang_code WHERE l.book = ? ORDER BY l.item_order', self.first)]
        self.assertEqual(codes[0], 'deu')

    def test_items_match_as_calibre_lowercases(self):
        # casefold() makes 'STRASSE' and 'Straße' one tag; Calibre keeps them apart.
        self.calibre._upsert('tags', 'name', 'STRASSE')
        self.write({self.first: {'tags': ['Straße']}})
        self.assertIsNotNone(self.row("SELECT 1 FROM tags WHERE name = 'STRASSE'"))
        self.assertIsNotNone(self.row("SELECT 1 FROM tags WHERE name = 'Straße'"))

    def test_an_authors_case_change_sorts_the_new_name(self):
        self.write({self.first: {'authors': ['ada lark']}})
        self.assertEqual(self.row("SELECT sort FROM authors WHERE name = 'ada lark'")[0],
                         'lark, ada')

    def test_a_book_that_changed_since_the_check_is_not_written(self):
        uuid_, path = self.row('SELECT uuid, path FROM books WHERE id = ?', self.first)
        before = self.database_digest()
        result = self.write({self.first: {'title': 'X'}},
                            expected={self.first: ('another-uuid', path)})
        self.assertEqual(result, {})
        self.assertEqual(self.database_digest(), before)
        self.assertEqual(set(self.write({self.first: {'title': 'X'}},
                                        expected={self.first: (uuid_.upper(), path)})),
                         {self.first})

    def test_the_schema_is_checked_again_under_the_lock(self):
        # Calibre upgraded the library between check_library() and the lock.
        self.calibre.db.execute('PRAGMA user_version = 99')
        original = calibre_write.check_library
        calibre_write.check_library = lambda folder: 28
        try:
            before = self.database_digest()
            with self.assertRaises(CalibreRefused):
                self.write({self.first: {'title': 'X'}})
            self.assertEqual(self.database_digest(), before)
        finally:
            calibre_write.check_library = original

    def test_the_cover_keeps_its_mode(self):
        path = self.row('SELECT path FROM books WHERE id = ?', self.first)[0]
        cover = self.calibre.folder / path / 'cover.jpg'
        if cover.exists():
            cover.unlink()
        umask = os.umask(0o022)
        try:
            self.write({self.first: {'cover': make_png(3, 3)}})
        finally:
            os.umask(umask)
        self.assertEqual(cover.stat().st_mode & 0o777, 0o644)  # not mkstemp's 0600
        cover.chmod(0o640)
        self.write({self.first: {'cover': make_png(4, 4)}})
        self.assertEqual(cover.stat().st_mode & 0o777, 0o640)

    def test_a_cover_outside_the_library_is_not_written(self):
        self.calibre.db.execute("UPDATE books SET path = '../outside' WHERE id = ?",
                                (self.first,))
        outside = self.calibre.folder.parent / 'outside'
        outside.mkdir()
        result = self.write({self.first: {'cover': make_png(3, 3)}})
        self.assertEqual(os.listdir(outside), [])
        self.assertEqual(result[self.first].skipped, {'cover'})

    def test_a_backup_is_kept_when_the_clock_ran_ahead(self):
        folder = self.calibre.folder
        for name in ('20991001', '20991002', '20991003'):
            shutil.copyfile(folder / 'metadata.db',
                            folder / ('metadata.db.bookcase-backup-' + name))
        self.write({self.first: {'title': 'One'}}, today=datetime.date(2026, 10, 8))
        self.assertTrue((folder / 'metadata.db.bookcase-backup-20261008').exists())
        self.assertEqual(len(calibre_write.backups(folder)), calibre_write.BACKUPS_KEPT)


if __name__ == '__main__':
    unittest.main()
