# SPDX-License-Identifier: GPL-2.0-or-later
# SPDX-FileCopyrightText: 2026 Jack Tully

"""kobo.py over synthetic KoboReader.sqlite files built from tests/fixtures/kobo_reader.sql,
with invented books."""

from tests import ROOT

import os
import pathlib
import shutil
import sqlite3
import tempfile
import unittest
from unittest import mock

from bookcase import kobo
from bookcase.library import Book

FIXTURE = ROOT / 'tests' / 'fixtures' / 'kobo_reader.sql'
HARBOUR = 'file:///mnt/onboard/Bookcase/Ada Lark/A Quiet Harbour.kepub.epub'
LANTERN = 'file:///mnt/onboard/Bookcase/Ben Ross/Lantern Hill.kepub.epub'
SALT = 'file:///mnt/onboard/Salt Roads.epub'  # the user's own, not in the library
NEW = 'file:///mnt/onboard/Bookcase/Cy Moor/Just Sent.epub'  # not found by the Kobo yet


def make_database(path, books=((HARBOUR, 1, 45, '2026-09-30T20:15:03Z'),
                               (LANTERN, 2, 100, '2026-08-01T07:00:00.000'),
                               (SALT, 0, 0, None))):
    db = sqlite3.connect(path)
    db.executescript(FIXTURE.read_text(encoding='utf-8'))
    for content, status, percent, last_read in books:
        db.execute('INSERT INTO content (ContentID, ContentType, MimeType, Title, ReadStatus, '
                   '___PercentRead, DateLastRead, ___UserID) VALUES (?, 6, ?, ?, ?, ?, ?, ?)',
                   (content, 'application/x-kobo-epub+zip', os.path.basename(content), status,
                    percent, last_read, 'adobe_user'))
        db.execute('INSERT INTO content (ContentID, ContentType, MimeType, BookID, ___UserID) '
                   "VALUES (?, 9, 'application/xhtml+xml', ?, '')",
                   (content + '!OEBPS!ch1.xhtml', content))  # a chapter row
    db.commit()
    db.close()
    return path


def rows(path, query, *params):
    db = sqlite3.connect(path)
    try:
        return db.execute(query, params).fetchall()
    finally:
        db.close()


def book(status='unread', progress=0.0, last_read=0.0):
    return Book(id=1, uuid='u', title='T', sort_title='t', status=status, progress=progress,
                last_read=last_read)


class KoboTest(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.mkdtemp(prefix='bookcase-test-kobo-')
        self.addCleanup(shutil.rmtree, self.directory, True)
        self.path = make_database(os.path.join(self.directory, 'KoboReader.sqlite'))

    def write(self, names=('Favourites',), wanted=None, known=(HARBOUR, LANTERN)):
        if wanted is None:
            wanted = {'Favourites': {HARBOUR}}
        return kobo.write_collections(self.path, list(names), wanted, set(known))

    def members(self, name='Favourites'):
        return {row[0] for row in rows(self.path, 'SELECT ContentId FROM ShelfContent '
                                                  'WHERE ShelfName = ?', name)}

    # -- reading -----------------------------------------------------------------------------

    def test_content_id(self):
        self.assertEqual(kobo.content_id('Bookcase/Ada Lark/A Quiet Harbour.kepub.epub'),
                         HARBOUR)

    def test_reading_states(self):
        states = kobo.reading_states(self.path)
        self.assertEqual(set(states), {HARBOUR, LANTERN, SALT})  # chapters left out
        self.assertEqual((states[HARBOUR].status, states[HARBOUR].percent), ('reading', 45))
        self.assertAlmostEqual(states[HARBOUR].fraction, 0.45)
        self.assertEqual(states[LANTERN].status, 'finished')
        self.assertGreater(states[HARBOUR].last_read, 1.7e9)
        self.assertGreater(states[LANTERN].last_read, 1.7e9)
        self.assertEqual(states[SALT].last_read, 0.0)

    def test_reading_writes_nothing(self):
        before = pathlib.Path(self.path).read_bytes()
        kobo.reading_states(self.path)
        self.assertEqual(pathlib.Path(self.path).read_bytes(), before)
        self.assertFalse(os.path.exists(kobo.backup_path(self.path)))

    def test_ahead(self):
        reading = kobo.State('reading', 45)
        self.assertEqual(kobo.ahead(book(), reading), ('reading', 0.45))
        self.assertEqual(kobo.ahead(book('reading', 0.2), reading), ('reading', 0.45))
        self.assertIsNone(kobo.ahead(book('reading', 0.5), reading))
        self.assertIsNone(kobo.ahead(book('reading', 0.45), reading))
        self.assertEqual(kobo.ahead(book('reading', 0.9), kobo.State('finished', 100)),
                         ('finished', 1.0))
        self.assertIsNone(kobo.ahead(book('finished', 1.0), kobo.State('finished', 100)))
        self.assertIsNone(kobo.ahead(book(), kobo.State('unread', 0)))
        self.assertIsNone(kobo.ahead(book(), None))

    def test_an_older_kobo_place_is_not_ahead(self):
        # Read to 60% on the Kobo in September, started again in Bookcase in October: the
        # Kobo's place (or its Finished) is the older one and is not offered.
        september, october = 1_788_000_000.0, 1_790_600_000.0
        self.assertIsNone(kobo.ahead(book('reading', 0.1, october),
                                     kobo.State('reading', 60, september)))
        self.assertIsNone(kobo.ahead(book('reading', 0.1, october),
                                     kobo.State('finished', 100, september)))
        # Read on the Kobo since (or on the same day, a clock's slack): it is offered.
        self.assertEqual(kobo.ahead(book('reading', 0.1, september),
                                    kobo.State('reading', 60, october)), ('reading', 0.6))
        self.assertEqual(kobo.ahead(book('reading', 0.1, september + 3600),
                                    kobo.State('reading', 60, september)), ('reading', 0.6))
        # A Kobo that never said when: as before.
        self.assertEqual(kobo.ahead(book('reading', 0.1, october), kobo.State('reading', 60)),
                         ('reading', 0.6))

    # -- collections -------------------------------------------------------------------------

    def test_write_collections(self):
        changes = self.write(names=('Favourites', 'Sea Stories'),
                             wanted={'Favourites': {HARBOUR, NEW}, 'Sea Stories': set()})
        self.assertEqual(changes.created, ['Favourites'])
        self.assertEqual(changes.added, [('Favourites', HARBOUR)])
        self.assertEqual(changes.waiting, {NEW})
        shelf = rows(self.path, 'SELECT Id, InternalName, Name, Type, _IsDeleted, _IsVisible, '
                                '_IsSynced FROM Shelf')
        self.assertEqual(shelf, [('Favourites', 'Favourites', 'Favourites', 'UserTag', 'false',
                                  'true', 'false')])
        self.assertEqual(self.members(), {HARBOUR})  # NEW waits for the Kobo to find it
        self.assertEqual(rows(self.path, 'SELECT _IsDeleted, _IsSynced FROM ShelfContent'),
                         [('false', 'false')])
        self.assertEqual(rows(self.path, 'PRAGMA integrity_check'), [('ok',)])
        # The backup is the database as it was before.
        backup = kobo.backup_path(self.path)
        self.assertTrue(backup.endswith('KoboReader.sqlite.bookcase-backup'))
        self.assertEqual(rows(backup, 'SELECT COUNT(*) FROM Shelf'), [(0,)])
        self.assertEqual(rows(backup, 'SELECT COUNT(*) FROM content'), [(6,)])

    def test_nothing_to_do_writes_nothing(self):
        self.write()
        backup = kobo.backup_path(self.path)
        os.remove(backup)
        before = pathlib.Path(self.path).read_bytes()
        changes = self.write()
        self.assertFalse(changes)
        self.assertFalse(os.path.exists(backup))
        self.assertEqual(pathlib.Path(self.path).read_bytes(), before)

    def test_known_books_leave_and_others_stay(self):
        self.write(wanted={'Favourites': {HARBOUR, LANTERN}})
        db = sqlite3.connect(self.path)
        db.execute('INSERT INTO ShelfContent VALUES (?, ?, ?, ?, ?)',
                   ('Favourites', SALT, '2026-01-01T00:00:00Z', 'false', 'false'))
        db.execute('INSERT INTO Shelf (Id, InternalName, Name, _IsDeleted) VALUES '
                   "('Kobo Only', 'Kobo Only', 'Kobo Only', 'false')")
        db.execute('INSERT INTO ShelfContent VALUES (?, ?, ?, ?, ?)',
                   ('Kobo Only', HARBOUR, '2026-01-01T00:00:00Z', 'false', 'false'))
        db.commit()
        db.close()
        changes = self.write(wanted={'Favourites': {LANTERN}})
        self.assertEqual(changes.removed, [('Favourites', HARBOUR)])
        # SALT (put there on the Kobo, not in the library) and the Kobo's own collection stay.
        self.assertEqual(self.members(), {LANTERN, SALT})
        self.assertEqual(self.members('Kobo Only'), {HARBOUR})

    def test_deleted_collection_comes_back(self):
        db = sqlite3.connect(self.path)
        db.execute('INSERT INTO Shelf (Id, InternalName, Name, _IsDeleted, _IsVisible) VALUES '
                   "('Favourites', 'Favourites', 'Favourites', 'true', 'false')")
        db.execute('INSERT INTO ShelfContent VALUES (?, ?, ?, ?, ?)',
                   ('Favourites', HARBOUR, '2026-01-01T00:00:00Z', 'true', 'false'))
        db.commit()
        db.close()
        self.write()
        self.assertEqual(rows(self.path, 'SELECT _IsDeleted, _IsVisible FROM Shelf'),
                         [('false', 'true')])
        self.assertEqual(rows(self.path, 'SELECT _IsDeleted FROM ShelfContent'), [('false',)])

    def test_unexpected_schema_is_refused(self):
        db = sqlite3.connect(self.path)
        db.execute('ALTER TABLE ShelfContent RENAME COLUMN _IsSynced TO Synced')
        db.commit()
        db.close()
        before = pathlib.Path(self.path).read_bytes()
        with self.assertRaises(kobo.KoboError):
            self.write()
        self.assertEqual(pathlib.Path(self.path).read_bytes(), before)
        self.assertFalse(os.path.exists(kobo.backup_path(self.path)))
        kobo.reading_states(self.path)  # reading needs only content

    def test_missing_table_is_refused(self):
        db = sqlite3.connect(self.path)
        db.execute('DROP TABLE Shelf')
        db.commit()
        db.close()
        with self.assertRaises(kobo.KoboError):
            kobo.check(self.path)

    def test_old_firmware_is_refused(self):
        db = sqlite3.connect(self.path)
        db.execute('UPDATE dbversion SET version = 40')
        db.commit()
        db.close()
        with self.assertRaises(kobo.KoboError):
            self.write()
        self.assertEqual(self.members(), set())

    def test_locked_database_is_refused(self):
        other = sqlite3.connect(self.path, isolation_level=None)
        other.execute('BEGIN IMMEDIATE')
        try:
            with self.assertRaises(kobo.KoboError) as caught:
                self.write()
            self.assertIn('in use', str(caught.exception))
        finally:
            other.execute('ROLLBACK')
            other.close()
        self.assertFalse(os.path.exists(kobo.backup_path(self.path)))
        self.assertEqual(self.members(), set())

    def test_unfinished_write_is_refused(self):
        open(self.path + '-journal', 'wb').close()
        with self.assertRaises(kobo.KoboError):
            self.write()
        os.remove(self.path + '-journal')
        self.assertEqual(self.members(), set())

    def test_damaged_database_is_not_written(self):
        real_connect = sqlite3.connect

        class Damaged(sqlite3.Connection):
            def execute(self, sql, *args):
                if sql.startswith('PRAGMA quick_check'):
                    return super().execute("SELECT 'page 3 is bad'")
                return super().execute(sql, *args)

        def connect(path, *args, **kwargs):
            if path == self.path:
                kwargs['factory'] = Damaged
            return real_connect(path, *args, **kwargs)

        before = pathlib.Path(self.path).read_bytes()
        with mock.patch.object(kobo.sqlite3, 'connect', connect):
            with self.assertRaises(kobo.KoboError):
                self.write()
        self.assertEqual(pathlib.Path(self.path).read_bytes(), before)
        self.assertFalse(os.path.exists(kobo.backup_path(self.path)))

    def test_failed_backup_writes_nothing_and_leaves_no_part(self):
        # The Kobo's storage is full: the backup cannot be made, so nothing is written, and
        # no half-made copy is left on the Kobo.
        real_replace = os.replace

        def replace(source, target):
            if str(target).endswith('.bookcase-backup'):
                raise OSError(28, 'No space left on device')
            return real_replace(source, target)

        with mock.patch.object(kobo.os, 'replace', replace):
            with self.assertRaises(kobo.KoboError) as caught:
                self.write()
        self.assertIn('No space left', str(caught.exception))
        self.assertEqual(self.members(), set())
        self.assertEqual([name for name in os.listdir(self.directory)
                          if name.endswith('.part')], [])

    def test_failed_integrity_check_puts_the_database_back(self):
        real_connect = sqlite3.connect

        class Failing(sqlite3.Connection):
            def execute(self, sql, *args):
                if sql.startswith('PRAGMA integrity_check'):
                    return super().execute("SELECT 'page 3 is bad'")
                return super().execute(sql, *args)

        def connect(path, *args, **kwargs):
            if path == self.path:
                kwargs['factory'] = Failing
            return real_connect(path, *args, **kwargs)

        with mock.patch.object(kobo.sqlite3, 'connect', connect):
            with self.assertRaises(kobo.KoboError):
                self.write()
        self.assertEqual(self.members(), set())
        self.assertEqual(rows(self.path, 'SELECT COUNT(*) FROM Shelf'), [(0,)])


if __name__ == '__main__':
    unittest.main()
