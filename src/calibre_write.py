# SPDX-License-Identifier: GPL-2.0-or-later
# SPDX-FileCopyrightText: 2026 Jack Tully

"""Keep Calibre in Step: edits made in Bookcase written back to a linked Calibre library's
metadata.db, for the libraries the user opted in (the `calibre-write-libraries` setting).
The one place Bookcase writes to a Calibre library; calibre.py only reads.

    calibre_write.check_library(folder) -> user_version     CalibreRefused when we must not
    calibre_write.calibre_running() -> bool                 Calibre's single-instance lock
    with calibre_write.calibre_lock(): …                    hold it (CalibreBusy if taken)
    calibre_write.backup(folder) -> path or None            metadata.db.bookcase-backup-DATE
    calibre_write.write_changes(folder, {calibre id: {field: value}}) -> {id: Written}
    calibre_write.pending(library, covers, folder) -> {book id: Edit}
    calibre_write.flush(library, covers, folder) -> FlushReport
    sync = CalibreSync(library, covers, settings)           the app's coordinator (app.calibre)
    sync.status(folder) -> Status; sync.connect('changed', handler); sync.request()
    calibre_write.title_sort(title) / author_to_author_sort(name)   Calibre's own rules

What is written, field by field, the way Calibre's db/write.py does it: title (and `sort`
through title_sort, which the books_update_trg trigger also runs), authors (`,` in a name
stored as `|`; a new author's `sort` from author_to_author_sort; the link rows inserted in
display order, since Calibre orders authors by books_authors_link.id), author_sort, series
and series_index, tags, publisher, pubdate (a date at noon UTC, so it is the same day in
every time zone; no date is Calibre's 0101-01-01), languages (the first replaced, Calibre's
others kept, as ISO 639-3 codes), comments, rating (the ratings table's row, linked; 0
unlinks), identifiers (type lower-case without ':' or ',', ',' in a value as '|'; the book's
uuid is never changed) and the cover (cover.jpg in the book's folder, JPEG, has_cover). An
item matching another only in case is the same item, renamed to the new case (Calibre's
allow_case_change), which touches the other books that use it. An item no book uses any
more is deleted, as Calibre does, unless Calibre keeps a note on it (.calnotes/notes.db).
Each book written gets a new last_modified (UTC) and a row in metadata_dirtied, so Calibre
rewrites its metadata.opf when it next runs (Calibre's own backup thread does that; we do
not write OPFs). Folders and file names are never renamed: Calibre finds a book by
books.path whatever it says, and renames the folder itself the next time it changes the
title or author there.

Safety: nothing is written when the library's user_version is newer than SCHEMA_VERSION (28,
Calibre 8-9), when a table or column we write is missing (an old library Calibre has not
upgraded: opening it in Calibre once does), when application_id is set and not Calibre's,
or when PRAGMA quick_check fails. Calibre is detected through its own single-instance lock:
on Linux an abstract unix socket `\\0calibre-singleinstance-<euid>-db` (calibre
utils/lock.py, held by the GUI, calibre-server and calibredb). Bookcase binds it for the
whole write, so Calibre cannot start meanwhile; when it is taken, CalibreBusy, and the edits
wait. Before the first write of a day metadata.db is copied (SQLite's backup API) to
`metadata.db.bookcase-backup-YYYYMMDD` beside it; BACKUPS_KEPT are kept. Everything goes in
one IMMEDIATE transaction, checked with PRAGMA integrity_check before COMMIT (rolled back
when not 'ok') and again after; the cover files are written after the commit, each through
a temporary file renamed into place.

Which edits are pending needs no queue: a linked book's `source_values` (importing.py) is
what Calibre last said of it, so a field whose value in Bookcase differs from it was edited
in Bookcase and not yet written; the edits wait (in the library database) for as long as
Calibre is open, and survive a restart. Fields Calibre changed and Bookcase did not are
left to the rescan, which takes them; a field both changed gets Bookcase's value. After a
write the book's source_values say what was written, and its source_modified the new
last_modified when Calibre had not changed the book since our last look (otherwise the
next rescan still takes Calibre's other changes). Turning the switch on writes the edits
made before too (the confirmation counts them).

Threads: write_changes(), pending() and flush() block; CalibreSync runs flush() in a thread
on a worker Library, a few seconds after the library changes, and again every RETRY_S while
Calibre holds its lock, and emits `changed` on the main loop.
"""

import contextlib
import dataclasses
import datetime
import errno
import glob
import hashlib
import json
import logging
import os
import re
import socket
import sqlite3
import tempfile
import threading
import uuid
from gettext import gettext as _
from gettext import ngettext
from urllib.parse import quote

from gi.repository import Gio, GLib, GObject

from . import calibre, titles
from .formats import image_type

log = logging.getLogger(__name__)

SCHEMA_VERSION = 28  # the newest metadata.db user_version written for (Calibre 8.x/9.x)
APPLICATION_ID = 0x63616C69  # 'cali'
BACKUP_PREFIX = 'metadata.db.bookcase-backup-'
BACKUPS_KEPT = 3
SETTING = 'calibre-write-libraries'
DELAY_S = 4  # after the library changes
RETRY_S = 60  # while Calibre is open
UNDEFINED_DATE = '0101-01-01 00:00:00+00:00'
FIELDS = ('title', 'authors', 'author_sort', 'series', 'series_index', 'tags', 'publisher',
          'published', 'language', 'description', 'rating', 'identifiers', 'cover')

# Tables and columns written, which an old library may lack until Calibre upgrades it.
REQUIRED = {
    'books': {'id', 'title', 'sort', 'author_sort', 'series_index', 'pubdate', 'path',
              'uuid', 'has_cover', 'last_modified'},
    'authors': {'id', 'name', 'sort'}, 'books_authors_link': {'book', 'author'},
    'tags': {'id', 'name'}, 'books_tags_link': {'book', 'tag'},
    'series': {'id', 'name'}, 'books_series_link': {'book', 'series'},
    'publishers': {'id', 'name'}, 'books_publishers_link': {'book', 'publisher'},
    'ratings': {'id', 'rating'}, 'books_ratings_link': {'book', 'rating'},
    'languages': {'id', 'lang_code'},
    'books_languages_link': {'book', 'lang_code', 'item_order'},
    'identifiers': {'book', 'type', 'val'}, 'comments': {'book', 'text'},
    'metadata_dirtied': {'book'},
}


class CalibreWriteError(Exception):
    """Writing to a Calibre library failed; str() is a sentence for the user."""


class CalibreRefused(CalibreWriteError):
    """This library must not be written (a newer schema, an unknown database, damage)."""


class CalibreBusy(CalibreWriteError):
    """Calibre (or calibredb, calibre-server) is running: try again later."""


# -- Calibre's sort rules (calibre/ebooks/metadata/__init__.py, default tweaks) ----------------

_ARTICLES = re.compile(r'^(A\s+|The\s+|An\s+)', re.IGNORECASE)
_QUOTE_PAIRS = {'"': '"', "'": "'", '“': '”“', '”': '”', '„': '”“', '‚': '’‘', '’': '’‘',
                '‘': '’‘', '‹': '›', '›': '‹', '《': '》', '〈': '〉', '»': '«»', '«': '«»',
                '「': '」', '『': '』'}
_NAME_PREFIXES = {'mr', 'mrs', 'ms', 'dr', 'prof'}
_NAME_SUFFIXES = {'jr', 'sr', 'inc', 'ph.d', 'phd', 'md', 'm.d', 'i', 'ii', 'iii', 'iv',
                  'junior', 'senior'}
_COPYWORDS = {'agency', 'corporation', 'company', 'co.', 'council', 'committee', 'inc.',
              'institute', 'national', 'society', 'club', 'team', 'software', 'games',
              'entertainment', 'media', 'studios'}
_BRACKETED = re.compile(r'[\[(\{][^\])}]*[\])}]')


def _strip_quotes(title):
    if title and title[0] in _QUOTE_PAIRS:
        closing = _QUOTE_PAIRS[title[0]]
        title = title[1:]
        if title and title[-1] in closing:
            title = title[:-1]
    return title


def title_sort(title, *_args):
    """Calibre's title_sort with its default (English) articles: 'The Hobbit' -> 'Hobbit,
    The'; one pair of surrounding quotes dropped."""
    title = _strip_quotes((title or '').strip())
    match = _ARTICLES.search(title)
    if match:
        article = match.group(1)
        title = _strip_quotes(title[len(article):] + ', ' + article)
    return title.strip()


def author_to_author_sort(author):
    """Calibre's author_to_author_sort with its default tweaks ('comma'): 'J. R. R.
    Tolkien' -> 'Tolkien, J. R. R.', 'Martin Luther King Jr.' -> 'King, Martin Luther Jr.';
    a name with a comma, of one word or naming a company is kept."""
    if not author:
        return ''
    plain = _BRACKETED.sub('', author).strip()
    if ',' in plain:
        return author
    tokens = plain.split()
    if len(tokens) < 2 or {token.lower() for token in tokens} & _COPYWORDS:
        return author
    prefixes = _NAME_PREFIXES | {prefix + '.' for prefix in _NAME_PREFIXES}
    suffixes = _NAME_SUFFIXES | {suffix + '.' for suffix in _NAME_SUFFIXES}
    first = next((i for i, token in enumerate(tokens) if token.lower() not in prefixes), None)
    if first is None:
        return author
    last = next((i for i in range(len(tokens) - 1, first - 1, -1)
                 if tokens[i].lower() not in suffixes), None)
    if last is None:
        return author
    suffix = ' '.join(tokens[last + 1:])
    parts = tokens[last:last + 1] + tokens[first:last]
    count = len(parts)
    if suffix:
        parts.append(suffix)
    if count > 1:
        parts[0] += ','
    return ' '.join(parts)


# -- languages: Bookcase's ISO 639-1 codes to Calibre's ISO 639-3 -----------------------------

_THREE = {'en': 'eng', 'fr': 'fra', 'de': 'deu', 'es': 'spa', 'it': 'ita', 'pt': 'por',
          'ru': 'rus', 'ja': 'jpn', 'zh': 'zho', 'nl': 'nld', 'sv': 'swe', 'no': 'nor',
          'nb': 'nob', 'da': 'dan', 'fi': 'fin', 'pl': 'pol', 'cs': 'ces', 'el': 'ell',
          'tr': 'tur', 'ar': 'ara', 'he': 'heb', 'hi': 'hin', 'ko': 'kor', 'uk': 'ukr',
          'hu': 'hun', 'ro': 'ron', 'ca': 'cat', 'la': 'lat', 'ga': 'gle', 'cy': 'cym',
          'is': 'isl', 'vi': 'vie', 'th': 'tha', 'id': 'ind', 'fa': 'fas', 'bg': 'bul',
          'hr': 'hrv', 'sr': 'srp', 'sk': 'slk', 'sl': 'slv', 'et': 'est', 'lv': 'lav',
          'lt': 'lit', 'eo': 'epo'}
_iso_codes = None


def language_code(language):
    """'en' -> 'eng' (iso-codes' table when installed); a three-letter code stays."""
    global _iso_codes
    language = (language or '').strip().lower()
    if len(language) != 2:
        return language
    if _iso_codes is None:
        _iso_codes = dict(_THREE)
        with contextlib.suppress(OSError, ValueError, KeyError, TypeError):
            with open('/usr/share/iso-codes/json/iso_639-3.json', encoding='utf-8') as file:
                for entry in json.load(file)['639-3']:
                    if 'alpha_2' in entry:
                        _iso_codes[entry['alpha_2']] = entry['alpha_3']
    return _iso_codes.get(language, language)


def pubdate(published):
    """Bookcase's 'YYYY', 'YYYY-MM' or 'YYYY-MM-DD' as Calibre's pubdate text."""
    match = re.match(r'^(\d{4})(?:-(\d{2}))?(?:-(\d{2}))?$', (published or '').strip())
    if not match or match.group(1) <= '0101':
        return UNDEFINED_DATE
    year, month, day = match.group(1), match.group(2) or '01', match.group(3)
    day = day or '15'  # a year or a month alone: the middle of it, as Calibre does
    return f'{year}-{month}-{day} 12:00:00+00:00'


def now_text():
    """Calibre's last_modified form: '2026-10-08 09:12:44.123456+00:00'."""
    return datetime.datetime.now(datetime.UTC).isoformat(sep=' ')


# -- the lock, the checks and the backups --------------------------------------------------------

def lock_address():
    """Calibre's single-instance lock for programs that change libraries (Linux)."""
    return '\0' + f'calibre-singleinstance-{os.geteuid()}-db'


@contextlib.contextmanager
def calibre_lock(address=None):
    """Hold Calibre's lock: Calibre, calibredb and calibre-server refuse to start while
    it is held. CalibreBusy when one of them holds it."""
    sock = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM | socket.SOCK_CLOEXEC)
    try:
        sock.bind(address or lock_address())
    except OSError as error:
        sock.close()
        if error.errno == errno.EADDRINUSE:
            raise CalibreBusy(_('Calibre is open. Bookcase writes to its library once it '
                                'is closed.')) from error
        raise CalibreWriteError(str(error)) from error
    try:
        yield
    finally:
        sock.close()


def calibre_running(address=None):
    try:
        with calibre_lock(address):
            return False
    except CalibreBusy:
        return True
    except CalibreWriteError:
        return False


def _database(folder):
    return os.path.join(os.path.abspath(str(folder)), 'metadata.db')


def _register(db):
    """The functions Calibre's triggers and views use (tech.md §6)."""
    db.create_function('title_sort', -1, title_sort, deterministic=True)
    db.create_function('author_to_author_sort', 1,
                       lambda name: author_to_author_sort((name or '').replace('|', ',')),
                       deterministic=True)
    db.create_function('uuid4', 0, lambda: str(uuid.uuid4()))
    db.create_function('books_list_filter', 1, lambda _x: 1)
    db.create_collation('PYNOCASE', calibre._collate)
    db.create_collation('icucollate', calibre._collate)


def _check(db):
    version = db.execute('PRAGMA user_version').fetchone()[0]
    if version > SCHEMA_VERSION:
        raise CalibreRefused(_('This Calibre library was made by a newer Calibre than Bookcase '
                               'knows: Bookcase does not write to it.'))
    application = db.execute('PRAGMA application_id').fetchone()[0]
    if application not in (0, APPLICATION_ID):
        raise CalibreRefused(_('This metadata.db is not a Calibre library’s.'))
    for table, columns in REQUIRED.items():
        have = {row[1] for row in db.execute(f'PRAGMA table_info({table})')}
        if not columns <= have:
            raise CalibreRefused(_('This Calibre library is from an older Calibre: open it in '
                                   'Calibre once to bring it up to date.'))
    return version


def check_library(folder):
    """The library's user_version, after checking Bookcase may write to it (read only)."""
    database = _database(folder)
    if not os.path.isfile(database):
        raise CalibreRefused(_('The Calibre library’s metadata.db cannot be found.'))
    try:
        db = sqlite3.connect('file:' + quote(database) + '?mode=ro', uri=True, timeout=10)
    except sqlite3.Error as error:
        raise CalibreRefused(str(error)) from error
    try:
        return _check(db)
    except sqlite3.Error as error:
        raise CalibreRefused(_('The Calibre library cannot be read: {error}').format(error=error)) \
            from error
    finally:
        db.close()


def backups(folder):
    """The backups of the library's metadata.db, oldest first."""
    directory = glob.escape(os.path.dirname(_database(folder)))
    return sorted(glob.glob(os.path.join(directory, BACKUP_PREFIX + '[0-9]' * 8)))


def backup(folder, today=None):
    """Copy metadata.db to metadata.db.bookcase-backup-YYYYMMDD unless today's copy is
    there; keep the newest BACKUPS_KEPT. Returns the new copy's path, or None."""
    today = today or datetime.date.today()
    database = _database(folder)
    target = os.path.join(os.path.dirname(database), BACKUP_PREFIX + today.strftime('%Y%m%d'))
    if os.path.exists(target):
        return None
    temporary = target + '.part'
    source = sqlite3.connect('file:' + quote(database) + '?mode=ro', uri=True, timeout=10)
    try:
        with contextlib.suppress(FileNotFoundError):
            os.unlink(temporary)
        copy = sqlite3.connect(temporary)
        try:
            source.backup(copy)
        finally:
            copy.close()
        os.replace(temporary, target)
    except (sqlite3.Error, OSError) as error:
        with contextlib.suppress(OSError):
            os.unlink(temporary)
        raise CalibreWriteError(_('metadata.db could not be backed up: {error}').format(
            error=error)) from error
    finally:
        source.close()
    for old in backups(folder)[:-BACKUPS_KEPT]:
        with contextlib.suppress(OSError):
            os.unlink(old)
    return target


# -- writing -----------------------------------------------------------------------------------

@dataclasses.dataclass
class Written:
    """What write_changes() did to one Calibre book."""
    last_modified: str  # the new one
    previous: str  # the one before


class _Items:
    """A Calibre item table (authors, tags, series, publishers, languages), matched
    case-blind the way Calibre matches them, with Calibre's case change."""

    def __init__(self, db, table, column, link_table, link_column, note_field, dirtied):
        self.db, self.table, self.column = db, table, column
        self.link_table, self.link_column = link_table, link_column
        self.note_field = note_field
        self.dirtied = dirtied
        self.ids = {row[1].casefold(): (row[0], row[1])
                    for row in db.execute(f'SELECT id, {column} FROM {table}')}

    def id_for(self, value, insert_extra=None):
        found = self.ids.get(value.casefold())
        if found is not None:
            item_id, current = found
            if current != value:  # the same item in another case: renamed, as Calibre does
                self.db.execute(f'UPDATE {self.table} SET {self.column} = ? WHERE id = ?',
                                (value, item_id))
                self.ids[value.casefold()] = (item_id, value)
                self.dirtied.update(row[0] for row in self.db.execute(
                    f'SELECT book FROM {self.link_table} WHERE {self.link_column} = ?',
                    (item_id,)))
            return item_id
        names, values = [self.column], [value]
        for name, extra in (insert_extra or {}).items():
            names.append(name)
            values.append(extra)
        cursor = self.db.execute(
            f'INSERT INTO {self.table} ({", ".join(names)}) '
            f'VALUES ({", ".join("?" * len(names))})', values)
        self.ids[value.casefold()] = (cursor.lastrowid, value)
        return cursor.lastrowid

    def linked(self, book_id):
        return [row[0] for row in self.db.execute(
            f'SELECT {self.link_column} FROM {self.link_table} WHERE book = ? ORDER BY id',
            (book_id,))]

    def drop_unused(self, item_ids, notes):
        """Delete the items no book uses any more (as Calibre does), unless noted."""
        for item_id in set(item_ids):
            if self.db.execute(f'SELECT 1 FROM {self.link_table} WHERE {self.link_column} = ? '
                               'LIMIT 1', (item_id,)).fetchone():
                continue
            if notes(self.note_field, item_id):
                continue
            self.db.execute(f'DELETE FROM {self.table} WHERE id = ?', (item_id,))
            self.ids = {key: value for key, value in self.ids.items() if value[0] != item_id}


def _notes_check(folder):
    """A function telling whether Calibre keeps a note on an item (calibre 7's
    .calnotes/notes.db); when the notes cannot be read, every item counts as noted."""
    path = os.path.join(os.path.abspath(str(folder)), '.calnotes', 'notes.db')
    if not os.path.exists(path):
        return lambda _field, _item: False
    try:
        notes = sqlite3.connect('file:' + quote(path) + '?mode=ro', uri=True, timeout=5)
        noted = {(field.casefold(), item) for field, item in
                 notes.execute('SELECT colname, item FROM notes')}
        notes.close()
    except sqlite3.Error as error:
        log.info('Cannot read %s: %s', path, error)
        return lambda _field, _item: True
    return lambda field, item: (field, item) in noted


def _jpeg(data):
    """The cover as JPEG bytes (Calibre's cover.jpg is always a JPEG), or None."""
    if not data:
        return None
    if image_type(data) == 'jpeg':
        return data
    try:
        import gi
        gi.require_version('GdkPixbuf', '2.0')
        from gi.repository import GdkPixbuf

        loader = GdkPixbuf.PixbufLoader()
        loader.write(data)
        loader.close()
        pixbuf = loader.get_pixbuf()
        if pixbuf.get_has_alpha():
            flat = GdkPixbuf.Pixbuf.new(GdkPixbuf.Colorspace.RGB, False, 8,
                                        pixbuf.get_width(), pixbuf.get_height())
            flat.fill(0xFFFFFFFF)
            pixbuf.composite(flat, 0, 0, pixbuf.get_width(), pixbuf.get_height(), 0, 0, 1, 1,
                             GdkPixbuf.InterpType.NEAREST, 255)
            pixbuf = flat
        ok, jpeg = pixbuf.save_to_bufferv('jpeg', ['quality'], ['92'])
        return bytes(jpeg) if ok else None
    except (ImportError, ValueError, GLib.Error) as error:
        log.info('Cannot make the cover a JPEG: %s', error)
        return None


def _set_books_value(db, column, value, book_id):
    db.execute(f'UPDATE books SET {column} = ? WHERE id = ?', (value, book_id))


def _write_book(db, book_id, fields, items, notes, dirtied):
    """Apply one book's fields (FIELDS) inside the open transaction."""
    authors_changed = False
    if 'title' in fields:
        title = ' '.join(str(fields['title'] or '').split()) or 'Unknown'
        db.execute('UPDATE books SET title = ?, sort = ? WHERE id = ?',
                   (title, title_sort(title), book_id))
    if 'authors' in fields:
        names, seen = [], set()
        for name in fields['authors'] or ():
            name = ' '.join(str(name).split())
            if name and name.casefold() not in seen:
                seen.add(name.casefold())
                names.append(name)
        names = names or ['Unknown']
        table = items['authors']
        old = table.linked(book_id)
        db.execute('DELETE FROM books_authors_link WHERE book = ?', (book_id,))
        for name in names:
            author_id = table.id_for(name.replace(',', '|'),
                                     {'sort': author_to_author_sort(name)})
            db.execute('INSERT INTO books_authors_link (book, author) VALUES (?, ?)',
                       (book_id, author_id))
        table.drop_unused(old, notes)
        authors_changed = True
    if 'author_sort' in fields and fields['author_sort']:
        _set_books_value(db, 'author_sort', str(fields['author_sort']).strip(), book_id)
    elif authors_changed:
        sorts = [row[0] or '' for row in db.execute(
            'SELECT a.sort FROM books_authors_link l JOIN authors a ON a.id = l.author '
            'WHERE l.book = ? ORDER BY l.id', (book_id,))]
        _set_books_value(db, 'author_sort', ' & '.join(sorts), book_id)
    for field, key in (('series', 'series'), ('publisher', 'publishers')):
        if field not in fields:
            continue
        table = items[key]
        old = table.linked(book_id)
        db.execute(f'DELETE FROM {table.link_table} WHERE book = ?', (book_id,))
        name = ' '.join(str(fields[field] or '').split())
        if name:
            db.execute(f'INSERT INTO {table.link_table} (book, {table.link_column}) '
                       'VALUES (?, ?)', (book_id, table.id_for(name)))
        table.drop_unused(old, notes)
    if 'series_index' in fields:
        _set_books_value(db, 'series_index', float(fields['series_index'] or 0), book_id)
    if 'tags' in fields:
        table = items['tags']
        old = table.linked(book_id)
        db.execute('DELETE FROM books_tags_link WHERE book = ?', (book_id,))
        done = set()
        for tag in fields['tags'] or ():
            tag = ' '.join(str(tag).split())
            if tag and tag.casefold() not in done:
                done.add(tag.casefold())
                db.execute('INSERT INTO books_tags_link (book, tag) VALUES (?, ?)',
                           (book_id, table.id_for(tag)))
        table.drop_unused(old, notes)
    if 'published' in fields:
        _set_books_value(db, 'pubdate', pubdate(fields['published']), book_id)
    if 'language' in fields:
        table = items['languages']
        old = table.linked(book_id)
        codes = [db.execute('SELECT lang_code FROM languages WHERE id = ?',
                            (item,)).fetchone()[0] for item in old]
        first = language_code(fields['language'])
        rest = codes[1:] if codes else []
        wanted = ([first] if first else []) + [code for code in rest if code != first]
        db.execute('DELETE FROM books_languages_link WHERE book = ?', (book_id,))
        for order, code in enumerate(wanted):
            db.execute('INSERT INTO books_languages_link (book, lang_code, item_order) '
                       'VALUES (?, ?, ?)', (book_id, table.id_for(code), order))
        table.drop_unused(old, notes)
    if 'description' in fields:
        text = str(fields['description'] or '').strip()
        if text:
            db.execute('INSERT OR REPLACE INTO comments (book, text) VALUES (?, ?)',
                       (book_id, text))
        else:
            db.execute('DELETE FROM comments WHERE book = ?', (book_id,))
    if 'rating' in fields:
        db.execute('DELETE FROM books_ratings_link WHERE book = ?', (book_id,))
        rating = max(0, min(10, int(fields['rating'] or 0)))
        if rating:
            row = db.execute('SELECT id FROM ratings WHERE rating = ?', (rating,)).fetchone()
            rating_id = row[0] if row else db.execute(
                'INSERT INTO ratings (rating) VALUES (?)', (rating,)).lastrowid
            db.execute('INSERT INTO books_ratings_link (book, rating) VALUES (?, ?)',
                       (book_id, rating_id))
    if 'identifiers' in fields:
        db.execute('DELETE FROM identifiers WHERE book = ?', (book_id,))
        for kind, value in (fields['identifiers'] or {}).items():
            kind = str(kind).strip().lower().replace(':', '').replace(',', '')
            value = str(value or '').strip().replace(',', '|')
            if kind and value and kind != 'uuid':
                db.execute('INSERT OR REPLACE INTO identifiers (book, type, val) '
                           'VALUES (?, ?, ?)', (book_id, kind, value))
    dirtied.add(book_id)


def write_changes(folder, changes, address=None, today=None):
    """Write {calibre book id: {field: value}} into the library at folder (fields from
    FIELDS; 'cover' is image bytes, or None to remove it). Holds Calibre's lock throughout,
    backs metadata.db up first (once a day), writes in one transaction and checks the
    database before and after. Returns {calibre id: Written} for the books found (a book
    gone from Calibre is left out). Raises CalibreBusy, CalibreRefused or
    CalibreWriteError; nothing is written then."""
    folder = os.path.abspath(str(folder))
    changes = {int(key): value for key, value in changes.items() if value}
    if not changes:
        return {}
    check_library(folder)
    with calibre_lock(address):
        backup(folder, today)
        db = sqlite3.connect(_database(folder), timeout=10, isolation_level=None)
        _register(db)
        covers = []  # (temporary file, final path or None to remove, its folder)
        try:
            db.execute('PRAGMA foreign_keys = ON')
            try:
                db.execute('BEGIN IMMEDIATE')
            except sqlite3.OperationalError as error:
                raise CalibreBusy(_('The Calibre library is in use: {error}').format(error=error)) \
                    from error
            result = _write_all(db, folder, changes, covers)
            check = db.execute('PRAGMA integrity_check').fetchone()[0]
            if check != 'ok':
                raise CalibreWriteError(_('The Calibre library failed its check after the '
                                          'change, which was undone: {error}').format(error=check))
            db.execute('COMMIT')
        except BaseException:
            if db.in_transaction:
                db.execute('ROLLBACK')
            for temporary, _final, _directory in covers:
                with contextlib.suppress(OSError):
                    os.unlink(temporary)
            db.close()
            raise
        for temporary, final, directory in covers:
            try:
                if final is None:  # removed: to the trash, as Move to Trash does
                    old = Gio.File.new_for_path(os.path.join(directory, 'cover.jpg'))
                    with contextlib.suppress(GLib.Error):
                        old.trash(None)
                else:
                    os.replace(temporary, final)
            except OSError as error:
                log.warning('Cannot write the cover in %s: %s', directory, error)
        check = db.execute('PRAGMA integrity_check').fetchone()[0]
        db.close()
        if check != 'ok':
            raise CalibreWriteError(_('The Calibre library failed its check after Bookcase '
                                      'wrote to it: restore {name} if Calibre complains.')
                                    .format(name=os.path.basename(backups(folder)[-1])
                                            if backups(folder) else 'metadata.db'))
    return result


def _write_all(db, folder, changes, covers):
    try:
        quick = db.execute('PRAGMA quick_check').fetchone()[0]
    except sqlite3.DatabaseError as error:
        raise CalibreRefused(str(error)) from error
    if quick != 'ok':
        raise CalibreRefused(_('The Calibre library’s database is damaged; Bookcase does '
                               'not write to it. Calibre’s Check Library can repair it.'))
    dirtied = set()
    items = {
        'authors': _Items(db, 'authors', 'name', 'books_authors_link', 'author', 'authors',
                          dirtied),
        'tags': _Items(db, 'tags', 'name', 'books_tags_link', 'tag', 'tags', dirtied),
        'series': _Items(db, 'series', 'name', 'books_series_link', 'series', 'series',
                         dirtied),
        'publishers': _Items(db, 'publishers', 'name', 'books_publishers_link', 'publisher',
                             'publisher', dirtied),
        'languages': _Items(db, 'languages', 'lang_code', 'books_languages_link',
                            'lang_code', 'languages', dirtied),
    }
    notes = _notes_check(folder)
    previous = {}
    for book_id, fields in changes.items():
        row = db.execute('SELECT path, last_modified FROM books WHERE id = ?',
                         (book_id,)).fetchone()
        if row is None:
            log.info('Calibre book %s is gone; not written', book_id)
            continue
        previous[book_id] = str(row[1] or '')
        _write_book(db, book_id, fields, items, notes, dirtied)
        if 'cover' in fields:
            directory = os.path.join(folder, *(row[0] or '').split('/'))
            data = _jpeg(fields['cover'])
            if fields['cover'] and data is None:
                continue  # not an image we can turn into a JPEG: the old cover stays
            if not os.path.isdir(directory):
                continue
            if data is None:
                _set_books_value(db, 'has_cover', 0, book_id)
                covers.append(('', None, directory))
                continue
            fd, temporary = tempfile.mkstemp(dir=directory, prefix='.cover-', suffix='.jpg')
            with os.fdopen(fd, 'wb') as file:
                file.write(data)
            covers.append((temporary, os.path.join(directory, 'cover.jpg'), directory))
            _set_books_value(db, 'has_cover', 1, book_id)
    stamp = now_text()
    for book_id in dirtied:
        _set_books_value(db, 'last_modified', stamp, book_id)
        db.execute('INSERT OR IGNORE INTO metadata_dirtied (book) VALUES (?)', (book_id,))
    return {book_id: Written(stamp, previous[book_id]) for book_id in previous}


# -- Bookcase's side: what is pending, and writing it ------------------------------------------

@dataclasses.dataclass
class Edit:
    """A Bookcase book's details not yet in Calibre."""
    calibre_id: int
    fields: dict  # {field: Bookcase's value}; 'cover' as bytes (or None: removed)
    values: dict  # Bookcase's values of the fields as source_values holds them (a cover's
    # MD5 and its cover_version)


@dataclasses.dataclass
class FlushReport:
    written: list = dataclasses.field(default_factory=list)  # Bookcase book ids
    skipped: list = dataclasses.field(default_factory=list)  # gone from Calibre, or not its
    pending: int = 0  # books still waiting (Calibre open, or an error)


def _comparable(field, value):
    if field == 'identifiers':
        return {str(k).lower(): str(v) for k, v in (value or {}).items() if k != 'uuid'}
    if field in ('authors', 'tags'):
        return list(value or ())
    if field == 'series_index':
        return float(value or 0)
    if field == 'rating':
        return int(value or 0)
    return value if value is not None else ''


def _folder_books(library, folder):
    """The Calibre-linked books with a file under the linked folder."""
    folder = os.path.abspath(str(folder)).rstrip('/')
    record = next((f for f in library.folders() if f.kind == 'calibre'
                   and f.path.rstrip('/') == folder), None)
    if record is None:
        return []
    ids = {file.book_id for file in library.folder_files(record.id)}
    return [book for book in library.books() if book.id in ids
            and book.source == 'calibre' and book.source_key]


_cover_hashes = {}  # (book id, cover_version) -> md5 of the cover, '' for none
_cover_lock = threading.Lock()


def _cover_hash(covers, book):
    key = (book.id, book.cover_version)
    with _cover_lock:
        if key in _cover_hashes:
            return _cover_hashes[key]
    data = covers.data(book.id) if book.has_cover else None
    digest = hashlib.md5(data).hexdigest() if data else ''
    with _cover_lock:
        _cover_hashes[key] = digest
    return digest


def pending(library, covers, folder):
    """{Bookcase book id: Edit} for the books of the linked Calibre library at folder whose
    details in Bookcase differ from what Calibre last said (their source_values)."""
    edits = {}
    for book in _folder_books(library, folder):
        if not book.source_values:
            continue
        try:
            said = json.loads(book.source_values)
            calibre_id = int(book.source_key)
        except (ValueError, TypeError):
            continue
        fields, values = {}, {}
        for field in FIELDS:
            if field not in said:
                continue  # remembered before Bookcase kept it: unknown, never written
            if field == 'cover':
                if said.get('cover_version') == book.cover_version:
                    continue
                digest = _cover_hash(covers, book)
                if digest != said['cover']:
                    fields['cover'] = covers.data(book.id) if book.has_cover else None
                    values['cover'] = digest
                continue
            ours = getattr(book, field)
            if _comparable(field, ours) != _comparable(field, said[field]):
                fields[field] = ours
                values[field] = list(ours) if isinstance(ours, tuple) else ours
        # New authors with the sort Bookcase made of them: Calibre's own sort of them
        # (author_to_author_sort, joined) instead; a sort typed in Bookcase is written.
        if 'authors' in fields and fields.get('author_sort') == titles.authors_sort(
                book.authors):
            del fields['author_sort']
        if 'cover' in values:
            values['cover_version'] = book.cover_version
        if values:
            edits[book.id] = Edit(calibre_id, fields, values)
    return edits


def _belongs(library, folder, book, calibre_id, row):
    """Whether Calibre's book `row` (uuid, path) is this Bookcase book: the same uuid, or
    a file of it in that book's folder."""
    calibre_uuid, path = row
    if calibre_uuid and calibre_uuid.lower() == (book.uuid or '').lower():
        return True
    directory = os.path.join(os.path.abspath(str(folder)), *(path or '').split('/'))
    return any(os.path.dirname(file.path) == directory for file in library.files(book.id))


def flush(library, covers, folder, address=None, today=None):
    """Write the pending edits of the linked Calibre library at folder, then remember them
    in the books' source_values. Raises what write_changes() raises (nothing written)."""
    folder = os.path.abspath(str(folder))
    report = FlushReport()
    with calibre.LINK_LOCK:  # not while a rescan of a Calibre library runs
        edits = pending(library, covers, folder)
        if not edits:
            return report
        check_library(folder)
        rows = _calibre_rows(folder, [edit.calibre_id for edit in edits.values()])
        changes, books = {}, {}
        for book_id, edit in edits.items():
            book = library.book(book_id)
            row = rows.get(edit.calibre_id)
            if book is None or row is None or not _belongs(library, folder, book,
                                                           edit.calibre_id, row):
                log.warning('Bookcase book %s is not Calibre book %s here; not written',
                            book_id, edit.calibre_id)
                report.skipped.append(book_id)
                continue
            changes[edit.calibre_id] = edit.fields
            books[edit.calibre_id] = book
        written = write_changes(folder, changes, address=address, today=today)
        for calibre_id, result in written.items():
            book = books[calibre_id]
            edit = edits[book.id]
            current = library.book(book.id)
            try:
                said = json.loads(current.source_values) if current.source_values else {}
            except ValueError:
                said = {}
            said.update(edit.values)
            fields = {'source_values': json.dumps(said, sort_keys=True)}
            # Calibre had not changed the book since our last look: what it holds now is
            # what Bookcase holds, and the next rescan has nothing to take.
            if result.previous == current.source_modified:
                fields['source_modified'] = result.last_modified
            with library.undoable(None):  # bookkeeping, no undo step
                library.update_book(book.id, **fields)
            report.written.append(book.id)
    return report


def _calibre_rows(folder, calibre_ids):
    database = _database(folder)
    db = sqlite3.connect('file:' + quote(database) + '?mode=ro', uri=True, timeout=10)
    try:
        rows = {}
        for calibre_id in calibre_ids:
            row = db.execute('SELECT uuid, path FROM books WHERE id = ?',
                             (calibre_id,)).fetchone()
            if row is not None:
                rows[calibre_id] = row
        return rows
    except sqlite3.Error as error:
        raise CalibreWriteError(str(error)) from error
    finally:
        db.close()


# -- the app's coordinator ---------------------------------------------------------------------

@dataclasses.dataclass(frozen=True)
class Status:
    """A linked library's Keep Calibre in Step state, for Preferences."""
    enabled: bool = False
    state: str = 'off'  # off, idle, writing, waiting (Calibre open), refused, error
    pending: int = 0  # books waiting
    message: str = ''  # for 'refused' and 'error'

    def describe(self):
        """One sentence for the user."""
        if self.state == 'off':
            return _('Edits made in Bookcase stay in Bookcase')
        if self.state == 'waiting':
            return ngettext('{n} book’s changes waiting for Calibre to close',
                            '{n} books’ changes waiting for Calibre to close',
                            self.pending).format(n=self.pending)
        if self.state == 'writing':
            return _('Writing to Calibre…')
        if self.state in ('refused', 'error'):
            return self.message
        if self.pending:
            return ngettext('{n} book’s changes to write', '{n} books’ changes to write',
                            self.pending).format(n=self.pending)
        return _('Calibre has every change made in Bookcase')


class CalibreSync(GObject.Object):
    """Writes the opted-in libraries' pending edits a few seconds after the library
    changes (in a thread), and again every RETRY_S while Calibre is open. `changed` is
    emitted on the main loop whenever a status changes."""

    __gtype_name__ = 'BookcaseCalibreSync'
    __gsignals__ = {'changed': (GObject.SignalFlags.RUN_FIRST, None, ())}

    def __init__(self, library, covers, settings=None, address=None):
        super().__init__()
        self.library = library
        self.covers = covers
        self.settings = settings
        self.address = address
        self._enabled = set()
        self._statuses = {}
        self._timeout = None
        self._thread = None
        self._again = False
        self._library_handler = library.connect('changed', self._on_library_changed)
        self._settings_handler = None
        if settings is not None:
            self._enabled = {self._key(path) for path in settings.get_strv(SETTING)}
            self._settings_handler = settings.connect('changed::' + SETTING,
                                                      self._on_setting_changed)
        if self._enabled:
            self.request(DELAY_S)

    @staticmethod
    def _key(path):
        return os.path.abspath(os.path.expanduser(str(path))).rstrip('/')

    def is_enabled(self, folder):
        return self._key(folder) in self._enabled

    def set_enabled(self, folder, enabled):
        key = self._key(folder)
        if enabled:
            self._enabled.add(key)
        else:
            self._enabled.discard(key)
            self._statuses.pop(key, None)
        if self.settings is not None:
            paths = [path for path in self.settings.get_strv(SETTING) if self._key(path) != key]
            if enabled:
                paths.append(key)
            self.settings.set_strv(SETTING, paths)
        self.emit('changed')
        if enabled:
            self.request(0)

    def status(self, folder):
        key = self._key(folder)
        if key not in self._enabled:
            return Status()
        return self._statuses.get(key, Status(True, 'idle'))

    def count_pending(self, folder):
        """Books whose Bookcase details differ from Calibre's (blocking: for the
        confirmation)."""
        try:
            return len(pending(self.library, self.covers, folder))
        except Exception:
            log.exception('counting the edits for %s', folder)
            return 0

    def _on_setting_changed(self, settings, key):
        self._enabled = {self._key(path) for path in settings.get_strv(key)}
        self.emit('changed')
        self.request(0)

    def _on_library_changed(self, _library, kind, *_rest):
        if kind in ('books', 'folders') and self._enabled:
            self.request(DELAY_S)

    def request(self, delay=DELAY_S):
        """Write what is pending after `delay` seconds (sooner requests win)."""
        if self._timeout is not None:
            GLib.source_remove(self._timeout)
        self._timeout = GLib.timeout_add_seconds(max(0, int(delay)), self._start)

    def _start(self):
        self._timeout = None
        if self._thread is not None:
            self._again = True
            return GLib.SOURCE_REMOVE
        folders = [path for path in self._enabled if os.path.isdir(path)]
        if not folders or self.library is None or self.library.closed():
            return GLib.SOURCE_REMOVE
        for path in folders:
            current = self._statuses.get(path, Status(True, 'idle'))
            self._statuses[path] = dataclasses.replace(current, state='writing')
        self.emit('changed')
        main = self.library
        self._thread = threading.Thread(target=self._run, args=(main, folders),
                                        name='bookcase-calibre-write', daemon=True)
        self._thread.start()
        return GLib.SOURCE_REMOVE

    def _run(self, main, folders):
        statuses = {}
        try:
            worker = main.open_worker()
        except Exception as error:
            log.exception('opening a worker for Calibre writes')
            GLib.idle_add(self._finished, {path: Status(True, 'error', 0, str(error))
                                           for path in folders})
            return
        try:
            covers = self.covers.with_library(worker)
            for path in folders:
                statuses[path] = self._flush_one(worker, covers, path)
        finally:
            worker.close()
        GLib.idle_add(self._finished, statuses)

    def _flush_one(self, worker, covers, path):
        try:
            report = flush(worker, covers, path, address=self.address)
        except CalibreBusy:
            return Status(True, 'waiting', len(pending(worker, covers, path)))
        except CalibreRefused as error:
            return Status(True, 'refused', len(pending(worker, covers, path)), str(error))
        except Exception as error:
            log.exception('writing to the Calibre library at %s', path)
            message = str(error) if isinstance(error, CalibreWriteError) else _(
                'Could not write to the Calibre library: {error}').format(error=error)
            return Status(True, 'error', len(pending(worker, covers, path)), message)
        if report.written:
            log.info('wrote %d books to the Calibre library at %s', len(report.written), path)
        return Status(True, 'idle', 0)

    def _finished(self, statuses):
        self._thread = None
        for path, status in statuses.items():
            if path in self._enabled:
                self._statuses[path] = status
        self.emit('changed')
        if self._again:
            self._again = False
            self.request(0)
        elif any(status.state == 'waiting' for status in statuses.values()):
            self.request(RETRY_S)
        return GLib.SOURCE_REMOVE

    def shutdown(self):
        if self._timeout is not None:
            GLib.source_remove(self._timeout)
            self._timeout = None
        if self.library is not None and self._library_handler is not None:
            self.library.disconnect(self._library_handler)
            self._library_handler = None
        if self.settings is not None and self._settings_handler is not None:
            self.settings.disconnect(self._settings_handler)
            self._settings_handler = None
        thread = self._thread
        if thread is not None:
            thread.join(timeout=10)
