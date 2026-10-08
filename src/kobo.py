# SPDX-License-Identifier: GPL-2.0-or-later
# SPDX-FileCopyrightText: 2026 Jack Tully

"""A Kobo's own database (`.kobo/KoboReader.sqlite`): reading progress read back, and the
library's shelves written as the Kobo's collections.

    kobo.content_id('Bookcase/Ada Lark/A.kepub.epub')  # 'file:///mnt/onboard/Bookcase/…'
    kobo.reading_states(path)       # {ContentID: State(status, percent, last_read)}; read only
    kobo.check(path)                # the dbversion; raises KoboError when the schema is not
                                    # the one expected
    kobo.collection_changes(path, names, wanted, known)   # Changes, read only
    kobo.write_collections(path, names, wanted, known)    # Changes, written
    kobo.ahead(book, state)         # (status, fraction) the library lacks, or None
    kobo.backup_path(path)          # '<path>.bookcase-backup'

Where things are (from Calibre's KoboTouch driver; the DDL Bookcase expects is in
tests/fixtures/kobo_reader.sql):

- `content`: a row per book the Kobo has found (`ContentType = 6`, `BookID IS NULL`; its
  chapters are rows of other types). A sideloaded book's ContentID is
  `file:///mnt/onboard/<path on the Kobo's storage>` (an SD card's `file:///mnt/sd/…`), the
  path as it is, not percent-encoded. `ReadStatus` is 0 unread, 1 reading, 2 finished;
  `___PercentRead` 0–100; `DateLastRead` an ISO time ('2026-09-30T20:15:03Z', sometimes with
  milliseconds). The Kobo makes these rows itself, after it is unplugged and has looked at its
  storage: a book just sent has none yet.
- `Shelf`: a collection per row (`Name`; `Id` and `InternalName` the name too; `Type`
  'UserTag'; `_IsDeleted`, `_IsVisible`, `_IsSynced` the strings 'true' or 'false').
- `ShelfContent`: (`ShelfName`, `ContentId`) pairs, with `DateModified` and the same flags.

Writing (write_collections) is done with care, as the database is the reader's and holds its
reading progress, highlights and store account:

- the schema is checked first (every table and column used present, dbversion at least 64,
  when Shelf gained Id and Type): anything else and nothing is written (KoboError);
- never while the database is in use: a `-journal` file beside it (a write left unfinished,
  which the Kobo rolls back itself) or a lock another program holds (Calibre) refuses;
- a copy of the database is made first, beside it, as `KoboReader.sqlite.bookcase-backup`
  (SQLite's backup API, so the copy is whole), replaced at each write: the state before
  Bookcase's last change;
- a database that fails `PRAGMA quick_check` before the change is not written;
- the changes are one transaction (BEGIN IMMEDIATE … COMMIT), and `PRAGMA integrity_check`
  runs after it; should it fail, the copy is put back and KoboError raised;
- only collections named after a library shelf are touched, and in them only the books
  Bookcase knows (on the Kobo and in the library, `known`): a book is added when it is on the
  shelf and taken out when it is not; books the Kobo has not found yet (no content row) wait
  for the next connection; other collections, and books the user put in them on the Kobo,
  are left alone.

reading_states() opens the database read only (`mode=ro`) and writes nothing.
"""

import dataclasses
import datetime
import logging
import os
import sqlite3
import time
import urllib.parse
from gettext import gettext as _

log = logging.getLogger(__name__)

ONBOARD = 'file:///mnt/onboard/'
FINISHED, READING, UNREAD = 2, 1, 0
STATUSES = {UNREAD: 'unread', READING: 'reading', FINISHED: 'finished'}
MIN_VERSION = 64  # Shelf.Id and Shelf.Type (Calibre: dbversion >= 64)
REQUIRED = {
    'dbversion': {'version'},
    'content': {'ContentID', 'ContentType', 'BookID', 'ReadStatus', '___PercentRead',
                'DateLastRead'},
    'Shelf': {'CreationDate', 'Id', 'InternalName', 'LastModified', 'Name', 'Type',
              '_IsDeleted', '_IsVisible', '_IsSynced'},
    'ShelfContent': {'ShelfName', 'ContentId', 'DateModified', '_IsDeleted', '_IsSynced'},
}
READ_TABLES = ('content',)
TIMESTAMP = '%Y-%m-%dT%H:%M:%SZ'
AHEAD_BY = 0.01  # how much further the Kobo must be to count as ahead
# A book read in Bookcase more than this after the Kobo last opened it is not behind: the
# Kobo's place is older (a slack for a Kobo's clock, which may say local time as UTC).
STALE_S = 24 * 60 * 60


class KoboError(Exception):
    """A refusal or failure to tell the user in a sentence (str(error) is translated)."""


@dataclasses.dataclass(frozen=True)
class State:
    status: str  # 'unread', 'reading', 'finished'
    percent: int  # 0-100
    last_read: float = 0.0  # Unix time, 0 when never

    @property
    def fraction(self):
        return 1.0 if self.status == 'finished' else max(0, min(self.percent, 100)) / 100


@dataclasses.dataclass
class Changes:
    created: list = dataclasses.field(default_factory=list)  # collection names made
    added: list = dataclasses.field(default_factory=list)  # (name, ContentID)
    removed: list = dataclasses.field(default_factory=list)  # (name, ContentID)
    waiting: set = dataclasses.field(default_factory=set)  # ContentIDs the Kobo lacks yet

    def __bool__(self):
        return bool(self.created or self.added or self.removed)


def content_id(rel):
    """The ContentID of a book at `rel` (a path under the Kobo's root, '/'-separated)."""
    return ONBOARD + rel.lstrip('/')


def backup_path(path):
    return path + '.bookcase-backup'


def _true(value):
    return str(value).strip().lower() in ('true', '1')


def _when(text):
    if not text:
        return 0.0
    text = str(text).strip().replace('Z', '+00:00')
    try:
        moment = datetime.datetime.fromisoformat(text)
    except ValueError:
        return 0.0
    if moment.tzinfo is None:
        moment = moment.replace(tzinfo=datetime.UTC)
    return moment.timestamp()


# -- opening -----------------------------------------------------------------------------------

def _open_read_only(path):
    uri = 'file:' + urllib.parse.quote(os.path.abspath(path)) + '?mode=ro'
    try:
        return sqlite3.connect(uri, uri=True, timeout=1)
    except sqlite3.Error as error:
        raise KoboError(_('Could not read the Kobo’s database: {error}').format(
            error=error)) from error


def _columns(db, table):
    return {row[1] for row in db.execute(f'PRAGMA table_info("{table}")')}


def _check(db, tables):
    missing = []
    for table in tables:
        columns = _columns(db, table)
        if not columns:
            missing.append(table)
            continue
        missing.extend(f'{table}.{column}' for column in sorted(REQUIRED[table] - columns))
    if missing:
        log.warning('KoboReader.sqlite lacks %s', ', '.join(missing))
        raise KoboError(_('The Kobo’s database is not laid out as Bookcase expects, so it '
                          'was left alone'))


def _version(db):
    row = db.execute('SELECT version FROM dbversion').fetchone()
    return int(row[0]) if row and row[0] is not None else 0


def check(path):
    """The database's version, after checking every table and column Bookcase writes is
    there. Raises KoboError."""
    db = _open_read_only(path)
    try:
        _check(db, REQUIRED)
        version = _version(db)
    except sqlite3.Error as error:
        raise KoboError(_('Could not read the Kobo’s database: {error}').format(
            error=error)) from error
    finally:
        db.close()
    if version < MIN_VERSION:
        raise KoboError(_('This Kobo’s software is too old for collections from Bookcase'))
    return version


# -- reading progress --------------------------------------------------------------------------

def reading_states(path):
    """{ContentID: State} for the books on the Kobo's own storage that it has found. Read
    only. Raises KoboError."""
    db = _open_read_only(path)
    try:
        _check(db, READ_TABLES)
        rows = db.execute(
            'SELECT ContentID, ReadStatus, ___PercentRead, DateLastRead FROM content '
            'WHERE ContentType = 6 AND BookID IS NULL AND ContentID LIKE ?',
            (ONBOARD + '%',)).fetchall()
    except sqlite3.Error as error:
        raise KoboError(_('Could not read the Kobo’s database: {error}').format(
            error=error)) from error
    finally:
        db.close()
    states = {}
    for content, status, percent, last_read in rows:
        try:
            status = STATUSES.get(int(status or 0), 'unread')
            percent = int(float(percent or 0))
        except (TypeError, ValueError):
            continue
        states[content] = State(status, max(0, min(percent, 100)), _when(last_read))
    return states


RANK = {'unread': 0, 'reading': 1, 'finished': 2}


def ahead(book, state):
    """('finished', 1.0) or ('reading', fraction) when the Kobo is further on with a book
    than the library (`book` a library.Book), else None. Never when the library read the book
    since the Kobo last did (by more than STALE_S): the Kobo's place is then an old one (a
    book started again in Bookcase), and taking it would undo the newer reading."""
    if state is None or state.status == 'unread':
        return None
    if book.status == 'finished':
        return None
    if state.last_read and (book.last_read or 0) > state.last_read + STALE_S:
        return None
    if state.status == 'finished':
        return 'finished', 1.0
    if state.fraction > (book.progress or 0.0) + AHEAD_BY or (
            RANK[state.status] > RANK.get(book.status, 0)
            and state.fraction > (book.progress or 0.0)):
        return 'reading', state.fraction
    return None


# -- collections -------------------------------------------------------------------------------

def _plan(db, names, wanted, known):
    present = {row[0] for row in db.execute(
        'SELECT ContentID FROM content WHERE ContentType = 6 AND BookID IS NULL')}
    shelves = {row[0]: _true(row[1]) for row in db.execute(
        'SELECT Name, _IsDeleted FROM Shelf')}
    changes = Changes()
    for name in sorted(names):
        want = set(wanted.get(name, ()))
        changes.waiting |= want - present
        current = {row[0] for row in db.execute(
            'SELECT ContentId FROM ShelfContent WHERE ShelfName = ? AND '
            "(_IsDeleted IS NULL OR lower(_IsDeleted) NOT IN ('true', '1'))", (name,))}
        add = sorted((want & present) - current)
        remove = sorted((current & set(known)) - want)
        if add and (name not in shelves or shelves[name]):
            changes.created.append(name)
        changes.added.extend((name, content) for content in add)
        changes.removed.extend((name, content) for content in remove)
    return changes


def collection_changes(path, names, wanted, known):
    """What write_collections() would change, without writing. `names` are the library's
    shelves, `wanted` {shelf name: ContentIDs of the books on it}, `known` the ContentIDs of
    every book on the Kobo that is in the library. Raises KoboError."""
    check(path)
    db = _open_read_only(path)
    try:
        return _plan(db, names, wanted, known)
    except sqlite3.Error as error:
        raise KoboError(_('Could not read the Kobo’s database: {error}').format(
            error=error)) from error
    finally:
        db.close()


def _busy():
    return KoboError(_('The Kobo’s database is in use by another program; try again once it '
                       'is closed'))


def _apply(db, changes, now):
    for name in changes.created:
        row = db.execute('SELECT _IsDeleted FROM Shelf WHERE Name = ?', (name,)).fetchone()
        if row is None:
            db.execute(
                'INSERT INTO Shelf (CreationDate, Id, InternalName, LastModified, Name, Type, '
                "_IsDeleted, _IsVisible, _IsSynced) VALUES (?, ?, ?, ?, ?, 'UserTag', "
                "'false', 'true', 'false')", (now, name, name, now, name))
        else:
            db.execute("UPDATE Shelf SET _IsDeleted = 'false', _IsVisible = 'true', "
                       'LastModified = ? WHERE Name = ?', (now, name))
    for name, content in changes.added:
        row = db.execute('SELECT 1 FROM ShelfContent WHERE ShelfName = ? AND ContentId = ?',
                         (name, content)).fetchone()
        if row is None:
            db.execute('INSERT INTO ShelfContent (ShelfName, ContentId, DateModified, '
                       "_IsDeleted, _IsSynced) VALUES (?, ?, ?, 'false', 'false')",
                       (name, content, now))
        else:
            db.execute("UPDATE ShelfContent SET _IsDeleted = 'false', DateModified = ? "
                       'WHERE ShelfName = ? AND ContentId = ?', (now, name, content))
    for name, content in changes.removed:
        db.execute('DELETE FROM ShelfContent WHERE ShelfName = ? AND ContentId = ?',
                   (name, content))


def _back_up(path):
    """Copy the database to backup_path(path), through a temporary name: read through a
    connection of its own, which the writer's RESERVED lock (BEGIN IMMEDIATE, nothing written
    yet) lets read, so the copy is the database as it stands before the change."""
    target = backup_path(path)
    partial = target + '.part'
    try:
        if os.path.exists(partial):
            os.remove(partial)
        source = _open_read_only(path)
        try:
            copy = sqlite3.connect(partial)
            try:
                source.backup(copy)
            finally:
                copy.close()
        finally:
            source.close()
        with open(partial, 'rb') as file:
            os.fsync(file.fileno())
        os.replace(partial, target)
    except (OSError, sqlite3.Error) as error:
        # Not left half made on the Kobo's storage (a full one, say); nothing is written.
        try:
            os.remove(partial)
        except OSError:
            pass
        raise KoboError(_('Could not back up the Kobo’s database, so it was left alone: '
                          '{error}').format(error=error)) from error
    return target


def _restore(db, backup):
    source = sqlite3.connect(backup)
    try:
        source.backup(db)
    finally:
        source.close()


def write_collections(path, names, wanted, known):
    """Make the Kobo's collections named after library shelves hold the shelves' books (see
    the module docstring for what is touched and the care taken). Returns the Changes made
    (empty when there was nothing to do: then nothing is written, not even the backup).
    Raises KoboError."""
    check(path)
    if os.path.exists(path + '-journal'):
        raise _busy()
    try:
        db = sqlite3.connect(path, timeout=0, isolation_level=None)
    except sqlite3.Error as error:
        raise KoboError(str(error)) from error
    backup = None
    try:
        try:
            db.execute('BEGIN IMMEDIATE')
        except sqlite3.OperationalError as error:
            raise _busy() from error
        try:
            # A database already damaged is not written: the check after the change could
            # not tell Bookcase's change from the damage, and the backup would hold it too.
            if db.execute('PRAGMA quick_check').fetchall() != [('ok',)]:
                raise KoboError(_('The Kobo’s database is damaged, so it was left alone'))
            changes = _plan(db, names, wanted, known)
            if not changes:
                db.execute('ROLLBACK')
                return changes
            backup = _back_up(path)
            _apply(db, changes, time.strftime(TIMESTAMP, time.gmtime()))
            db.execute('COMMIT')
        except BaseException:
            if db.in_transaction:
                db.execute('ROLLBACK')
            raise
        result = db.execute('PRAGMA integrity_check').fetchall()
        if result != [('ok',)]:
            log.error('KoboReader.sqlite after writing: %s', result)
            _restore(db, backup)
            raise KoboError(_('The Kobo’s database failed its check after the change; it was '
                              'put back as it was'))
        log.info('Kobo collections: %d made, %d added, %d removed', len(changes.created),
                 len(changes.added), len(changes.removed))
        return changes
    except sqlite3.Error as error:
        raise KoboError(_('Could not write the Kobo’s database: {error}').format(
            error=error)) from error
    finally:
        db.close()
