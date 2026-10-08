# SPDX-License-Identifier: GPL-2.0-or-later
# SPDX-FileCopyrightText: 2026 Jack Tully

"""Adding book files to the library, scanning watched folders, linking Calibre libraries.

    partial_md5(path) -> str                 KOReader's document hash (the kosync id)
    library_path(folder, book, suffix) -> str   'folder/Author/Title.ext', free, sanitised
    safe_name(text) -> str                   a file name made of text

    importer = Importer(library, covers, library_folder)
    importer.add(paths, copy=True, progress=None, cancelled=None) -> ImportReport
    importer.scan(folder, progress=None, cancelled=None) -> ImportReport
    importer.link_calibre(path, progress=None, cancelled=None) -> ImportReport
    importer.add_async(paths, copy=True, progress=None, done=None) -> Job
    importer.scan_async(folder, progress=None, done=None) -> Job
    importer.link_calibre_async(path, progress=None, done=None) -> Job
    importer.open_in_place(path) -> book id  a file from outside, read without adding it
    importer.copy_into_library(book_id) -> (path, hash, size, format)   a copy in the
                                             library folder, the library unchanged
    describe(report) -> str                  one sentence for a toast

`progress(done, total, path)` is called after each file and `cancelled()` asked before each;
both may be None. An ImportReport lists `added` (book ids), `merged` (ids of books that got
a new format), `updated` (ids changed by a Calibre rescan), `duplicates` ([(path, book id)]),
`failed` ([(path, message)]), `moved` (file ids found at a new path), `missing` (file ids
no longer found), and `cancelled`.

add(): directories are walked for book files. A file whose content hash is already in the
library is a duplicate (skipped). A file of a book already there (formats.read() title and
an author in common, Library.find_similar()) in a format that book lacks becomes its new
format ('merged'); in a format it has, a duplicate. The file of a book opened without
adding (open_in_place) makes that book a library book, its place and highlights kept
('added'). Anything else is a new book. With
copy=True the file is copied into the library folder as 'Author/Title.ext' (a CBR is
converted to CBZ there when bsdtar is installed, since the reader opens zips only; without
bsdtar it is stored as it is, and cannot be read in Bookcase); with copy=False it is added
where it is (for files under a watched or library folder). The file's own cover is saved
through covers.

scan(folder): adds the folder as 'watched' if it is not a folder yet, then walks it. Files
the library has under it that are gone are marked missing; a new file whose hash matches a
missing file anywhere is that file moved (its path updated); other new files are added in
place. Files marked missing that are back are unmarked.

link_calibre(path): adds the folder as 'calibre' and its books in place (no copies, never
writing metadata.db): one book per Calibre book with its formats, Calibre's metadata,
rating and cover.jpg, keyed by Calibre's id (source_key) with Calibre's last_modified
(source_modified). Run again, it is the rescan: a book whose last_modified changed gets
Calibre's metadata (and cover) again, new formats are added, a file Calibre renamed (it
renames a book's folder when its title or author changes) is followed to its new path, and
the files of books gone from Calibre are marked missing.

Threads: Importer methods are blocking. The UI runs them through the *_async helpers,
which run the work in a thread on a worker Library (`library.open_worker()`, closed after)
and call `progress(done, total, path)` and then `done(report)` on the main loop (through
GLib.idle_add), after telling the main library's listeners (`notify_changed`). The Job
they return has cancel(): the work stops before the next file (what was added stays).

    job = app.importer.add_async(paths, progress=self.on_progress, done=self.on_done)
    cancel_button.connect('clicked', lambda _b: job.cancel())
"""

import contextlib
import dataclasses
import hashlib
import json
import logging
import os
import re
import shutil
import tempfile
import threading
from gettext import gettext as _
from gettext import ngettext

from gi.repository import GLib

from . import calibre, formats
from .formats import FormatError, comic
from .library import OPENED, READING_ORDER, LibraryError

log = logging.getLogger(__name__)

NAME_LIMIT = 120  # bytes for each part of a library path


def partial_md5(path):
    """KOReader's partial MD5 (util.partialMD5, the kosync document id): an MD5 over the
    1024-byte samples at offsets 1024 * 4**i for i in -1..10 (0, 1024, 4096, 16384, …;
    LuaJIT's bit.lshift(1024, -2) is 0), stopping at the first that is past the end."""
    md5 = hashlib.md5()
    with open(path, 'rb') as file:
        for i in range(-1, 11):
            file.seek(0 if i < 0 else 1024 << (2 * i))
            sample = file.read(1024)
            if not sample:
                break
            md5.update(sample)
    return md5.hexdigest()


def safe_name(text, limit=NAME_LIMIT):
    """text as one file name: no slashes or characters other systems refuse, no leading
    dot, at most `limit` bytes of UTF-8."""
    text = re.sub(r'[\x00-\x1f/\\:*?"<>|]', '_', text or '')
    text = ' '.join(text.split()).strip(' .')
    while len(text.encode()) > limit:
        text = text[:-1]
    return text.rstrip(' .') or '_'


def library_path(folder, book, suffix):
    """A free path for a book file in the library folder: 'folder/Author/Title.ext', with
    ' (2)', ' (3)', … when that is taken. book is a BookInfo or a Book (title, authors)."""
    authors = list(book.authors or ())
    author = safe_name(authors[0] if authors else _('Unknown Author'))
    title = safe_name(book.title or _('Untitled'))
    directory = os.path.join(str(folder), author)
    path = os.path.join(directory, title + suffix)
    number = 2
    while os.path.lexists(path):
        path = os.path.join(directory, f'{title} ({number}){suffix}')
        number += 1
    return path


@dataclasses.dataclass
class ImportReport:
    added: list = dataclasses.field(default_factory=list)
    merged: list = dataclasses.field(default_factory=list)
    updated: list = dataclasses.field(default_factory=list)
    duplicates: list = dataclasses.field(default_factory=list)
    failed: list = dataclasses.field(default_factory=list)
    moved: list = dataclasses.field(default_factory=list)
    missing: list = dataclasses.field(default_factory=list)
    cancelled: bool = False


def describe(report):
    """One sentence about a report, for a toast."""
    parts = []
    if report.added:
        parts.append(ngettext('{} book added', '{} books added',
                              len(report.added)).format(len(report.added)))
    if report.merged:
        parts.append(ngettext('{} format added', '{} formats added',
                              len(report.merged)).format(len(report.merged)))
    if report.updated:
        parts.append(ngettext('{} book updated', '{} books updated',
                              len(report.updated)).format(len(report.updated)))
    if report.duplicates:
        parts.append(ngettext('{} already in the library', '{} already in the library',
                              len(report.duplicates)).format(len(report.duplicates)))
    if report.failed:
        parts.append(ngettext('{} could not be read', '{} could not be read',
                              len(report.failed)).format(len(report.failed)))
    if report.missing:
        parts.append(ngettext('{} file missing', '{} files missing',
                              len(report.missing)).format(len(report.missing)))
    if not parts:
        return _('No new books')
    return ', '.join(parts)


def _walk(paths):
    """The book files among paths, directories walked (hidden entries skipped)."""
    for path in paths:
        path = os.path.abspath(str(path))
        if os.path.isdir(path):
            for directory, folders, files in os.walk(path):
                folders[:] = sorted(f for f in folders if not f.startswith('.'))
                for name in sorted(files):
                    if not name.startswith('.') and formats.format_of(name):
                        yield os.path.join(directory, name)
        else:
            yield path


class Job:
    """A running *_async task: cancel() stops it before its next file."""

    def __init__(self):
        self._event = threading.Event()
        self.thread = None

    def cancel(self):
        self._event.set()

    def cancelled(self):
        return self._event.is_set()



SOURCE_FIELDS = ('title', 'authors', 'series', 'series_index', 'tags', 'publisher',
                 'published', 'language', 'description', 'identifiers', 'rating')


def source_values(book, cover):
    """A book's details as JSON-ready values, for comparing what the library holds with what
    a source last said: lists for tuples, the cover as its MD5."""
    values = {}
    for field in SOURCE_FIELDS:
        value = getattr(book, field)
        values[field] = list(value) if isinstance(value, tuple) else value
    values['cover'] = hashlib.md5(cover).hexdigest() if cover else ''
    return values


class Importer:

    def __init__(self, library, covers, library_folder):
        self.library = library
        self.covers = covers
        self.library_folder = os.path.abspath(os.path.expanduser(str(library_folder)))

    # -- adding files -------------------------------------------------------------------

    def add(self, paths, copy=True, progress=None, cancelled=None):
        report = ImportReport()
        files = list(_walk(paths))
        if copy and files:
            os.makedirs(self.library_folder, exist_ok=True)
            self.library.add_folder(self.library_folder, 'library')
        for number, path in enumerate(files, 1):
            if cancelled is not None and cancelled():
                report.cancelled = True
                break
            # A file already in the library folder is added where it is.
            in_place = not copy or path.startswith(self.library_folder + os.sep)
            try:
                self._add_one(path, not in_place, report)
            except (FormatError, OSError) as error:
                report.failed.append((path, str(error)))
            except Exception as error:
                log.exception('Adding %s failed', path)
                report.failed.append((path, str(error)))
            if progress is not None:
                progress(number, len(files), path)
        return report

    def _source_of(self, path):
        best = ('library', -1)
        for folder in self.library.folders():
            prefix = folder.path.rstrip('/') + '/'
            if path.startswith(prefix) and len(prefix) > best[1]:
                best = (folder.kind, len(prefix))
        return best[0]

    def _add_one(self, path, copy, report, source=None):
        format = formats.format_of(path)
        if format is None:
            raise FormatError(_('Not a book Bookcase can read'))
        known = self.library.find_file(path)
        if known is not None:
            if self._keep_opened(known.book_id, path, copy, report):
                return known.book_id
            report.duplicates.append((path, known.book_id))
            return None
        file_hash = partial_md5(path)
        existing = self.library.find_by_hash(file_hash)
        if existing is not None:
            if self._keep_opened(existing, path, copy, report):
                return existing
            report.duplicates.append((path, existing))
            return None
        info = formats.read(path)
        stored_format = 'cbz' if format == 'cbr' and copy and shutil.which('bsdtar') else format
        for book_id in self.library.find_similar(info.title, info.authors):
            book = self.library.book(book_id)
            if stored_format in book.formats:
                report.duplicates.append((path, book_id))
                return None
            destination, file_hash, size, stored = self._place(path, book, format, copy,
                                                                     file_hash)
            try:
                self.library.add_file(book_id, destination, hash=file_hash, size=size,
                                      format=stored)
            except BaseException:
                self._unplace(path, destination)
                raise
            if not book.has_cover and info.cover:
                self._save_cover(book_id, info.cover)
            report.merged.append(book_id)
            return book_id
        destination, file_hash, size, info.format = self._place(path, info, format, copy,
                                                                file_hash)
        try:
            book_id = self.library.add_book(
                info, destination, hash=file_hash, size=size,
                source='library' if copy else (source or self._source_of(destination)))
        except BaseException:
            self._unplace(path, destination)
            raise
        if info.cover:
            self._save_cover(book_id, info.cover)
        report.added.append(book_id)
        return book_id

    def _keep_opened(self, book_id, path, copy, report):
        """A book opened without adding, added now: copied into the library folder (with
        `copy`) or kept where it is, and in the library from now on."""
        book = self.library.book(book_id)
        if book is None or book.source != OPENED:
            return False
        if copy:
            files = self.library.files(book_id)
            source = files[0].path if files and not files[0].missing else path
            destination, file_hash, size, format = self.copy_into_library(book_id, source)
            try:
                self.library.keep_book(book_id, path=destination, hash=file_hash, size=size,
                                       format=format)
            except BaseException:
                self._unplace(source, destination)
                raise
        else:
            self.library.keep_book(book_id, source=self._source_of(path))
        report.added.append(book_id)
        return True

    def copy_into_library(self, book_id, source=None):
        """Copy a book's file (`source`, else its reading file) into the library folder as
        Author/Title.ext (a CBR as a CBZ when bsdtar is there), changing nothing in the
        library: (path, hash, size, format) of the copy. For a book opened without adding,
        the caller then makes it the book's file with Library.keep_book(book_id, path=…,
        hash=…, size=…, format=…), an undo step on the library it is called on (so the
        copy can be made in a thread and kept on the main library). Raises OSError,
        FormatError, or LibraryError when the book has no file to copy."""
        book = self.library.book(book_id)
        if source is None:
            found = self.library.reading_file(book_id) if book is not None else None
            if found is None:
                raise LibraryError(_('The book’s file cannot be found'))
            source = found.path
        os.makedirs(self.library_folder, exist_ok=True)
        self.library.add_folder(self.library_folder, 'library')
        return self._place(source, book, formats.format_of(source), True,
                           partial_md5(source))

    def open_in_place(self, path):
        """The id of the book to read a file from outside in, without adding it: the
        library's book when it has the file (by path, else content), else a new book opened
        without adding (Library.add_opened: read where it is, kept out of the lists). Raises
        OSError or FormatError."""
        path = os.path.abspath(str(path))
        known = self.library.find_file(path)
        if known is not None:
            return known.book_id
        file_hash = partial_md5(path)
        existing = self.library.find_by_hash(file_hash)
        if existing is not None:
            return existing
        if formats.format_of(path) is None:
            raise FormatError(_('Not a book Bookcase can read'))
        info = formats.read(path)
        book_id = self.library.add_opened(info, path, hash=file_hash,
                                          size=os.path.getsize(path))
        if info.cover:
            self._save_cover_quietly(book_id, info.cover)
        return book_id

    def _save_cover_quietly(self, book_id, data):
        """A cover saved with no undo step (covers.save makes one)."""
        with self.library.undoable(None):
            self._save_cover(book_id, data)

    def _place(self, path, book, format, copy, file_hash):
        """Where the file goes (a copy in the library folder, or where it is), and that
        file's hash, size and format (a CBR converted is a CBZ)."""
        if not copy:
            return path, file_hash, os.path.getsize(path), format
        if format == 'cbr' and shutil.which('bsdtar'):
            destination = library_path(self.library_folder, book, '.cbz')
            os.makedirs(os.path.dirname(destination), exist_ok=True)
            try:
                comic.cbr_to_cbz(path, destination)
                return (destination, partial_md5(destination), os.path.getsize(destination),
                        'cbz')
            except FormatError:
                log.info('Cannot convert %s; storing it as it is', path)
        destination = library_path(self.library_folder, book, formats.suffix_of(path))
        os.makedirs(os.path.dirname(destination), exist_ok=True)
        fd, temporary = tempfile.mkstemp(dir=os.path.dirname(destination), prefix='.part-')
        os.close(fd)
        try:
            shutil.copyfile(path, temporary)
            os.replace(temporary, destination)
        except BaseException:
            with contextlib.suppress(OSError):
                os.unlink(temporary)
            raise
        return destination, file_hash, os.path.getsize(destination), format

    def _unplace(self, path, destination):
        if destination != path:
            with contextlib.suppress(OSError):
                os.unlink(destination)

    def _save_cover(self, book_id, data):
        try:
            self.covers.save(book_id, data)
        except (ValueError, OSError) as error:
            log.info('Cannot save the cover of book %s: %s', book_id, error)

    # -- watched folders ----------------------------------------------------------------

    def _folder(self, path, kind):
        path = os.path.abspath(os.path.expanduser(str(path))).rstrip('/') or '/'
        for folder in self.library.folders():
            if folder.path == path:
                return folder.id
        return self.library.add_folder(path, kind)

    def scan(self, folder, progress=None, cancelled=None):
        report = ImportReport()
        folder = os.path.abspath(os.path.expanduser(str(folder)))
        folder_id = self._folder(folder, 'watched')
        known = {file.path: file for file in self.library.folder_files(folder_id)}
        present = set(_walk([folder])) if os.path.isdir(folder) else set()
        gone = [file for path, file in known.items() if path not in present]
        back = [file.id for path, file in known.items() if path in present and file.missing]
        if back:
            self.library.set_missing(back, False)
        opened = self.library.opened_ids()
        for path, file in known.items():
            if file.book_id in opened and path in present:
                self._keep_opened(file.book_id, path, False, report)
        new = sorted(path for path in present if path not in known)
        for number, path in enumerate(new, 1):
            if cancelled is not None and cancelled():
                report.cancelled = True
                break
            try:
                if not self._found_moved(path, report):
                    self._add_one(path, False, report)
            except (FormatError, OSError) as error:
                report.failed.append((path, str(error)))
            except Exception as error:
                log.exception('Adding %s failed', path)
                report.failed.append((path, str(error)))
            if progress is not None:
                progress(number, len(new), path)
        moved = set(report.moved)
        newly_missing = [file.id for file in gone if file.id not in moved and not file.missing
                         and not os.path.exists(file.path)]
        if newly_missing and not report.cancelled:
            self.library.set_missing(newly_missing, True)
            report.missing.extend(newly_missing)
        return report

    def _found_moved(self, path, report):
        """Whether path is a known file that moved here (then its record follows it)."""
        if self.library.find_file(path) is not None:
            return True
        file_hash = partial_md5(path)
        book_id = self.library.find_by_hash(file_hash)
        if book_id is None:
            return False
        for file in self.library.files(book_id):
            if file.hash == file_hash and (file.missing or not os.path.exists(file.path)):
                self.library.set_file_path(file.id, path)
                report.moved.append(file.id)
                return True
        return False

    # -- Calibre libraries --------------------------------------------------------------

    def link_calibre(self, path, progress=None, cancelled=None):
        report = ImportReport()
        folder = os.path.abspath(os.path.expanduser(str(path)))
        books = calibre.read_library(folder)  # CalibreError for a folder that is not one
        self._folder(folder, 'calibre')
        seen = set()
        for number, book in enumerate(books, 1):
            if cancelled is not None and cancelled():
                report.cancelled = True
                break
            seen.add(str(book.id))
            try:
                self._link_one(book, report)
            except Exception as error:
                log.exception('Linking Calibre book %s failed', book.id)
                report.failed.append((book.folder, str(error)))
            if progress is not None:
                progress(number, len(books), book.folder)
        if not report.cancelled:
            self._mark_gone_calibre(folder, seen, report)
        return report

    def _link_one(self, book, report):
        key = str(book.id)
        book_id = self.library.find_by_source_key('calibre', key)
        if book_id is not None:
            self._relink(book_id, book, report)
            return
        if not book.files:
            report.failed.append((book.folder, _('No book file Bookcase can read')))
            return
        ordered = sorted(book.files.items(), key=lambda item: _rank(item[0]))
        hashes = {}
        for format, path in ordered:
            hashes[format] = partial_md5(path)
            existing = self.library.find_by_hash(hashes[format])
            if existing is not None:
                report.duplicates.append((path, existing))
                return
        info = dataclasses.replace(book.info, cover=None, format=ordered[0][0])
        first_format, first_path = ordered[0]
        book_id = self.library.add_book(
            info, first_path, hash=hashes[first_format], size=os.path.getsize(first_path),
            source='calibre', source_key=key, source_modified=book.last_modified)
        for format, path in ordered[1:]:
            self.library.add_file(book_id, path, hash=hashes[format],
                                  size=os.path.getsize(path), format=format)
        if book.rating:
            self.library.update_book(book_id, rating=book.rating)
        self._calibre_cover(book_id, book)
        self._remember_source(book_id)
        report.added.append(book_id)

    def _remember_source(self, book_id, kept=None):
        """Keep what Calibre said of the book, as the library holds it (`kept`: the details
        Calibre said that the book does not hold, edited in Bookcase; 'cover' as bytes): the
        next rescan changes only the details that still agree with it."""
        values = source_values(self.library.book(book_id), self.covers.data(book_id))
        for field, value in (kept or {}).items():
            if field == 'cover':
                value = hashlib.md5(value).hexdigest()
            elif isinstance(value, tuple):
                value = list(value)
            values[field] = value
        self.library.update_book(book_id, source_values=json.dumps(values, sort_keys=True))

    def _calibre_cover(self, book_id, book):
        if book.cover_path:
            try:
                with open(book.cover_path, 'rb') as file:
                    self._save_cover(book_id, file.read())
            except OSError as error:
                log.info('Cannot read %s: %s', book.cover_path, error)

    def _relink(self, book_id, book, report):
        known = {file.path: file for file in self.library.files(book_id)}
        back = [known[path].id for path in book.files.values()
                if path in known and known[path].missing]
        if back:
            self.library.set_missing(back, False)
        current = self.library.book(book_id)
        calibre_paths = set(book.files.values())
        for format, path in book.files.items():
            if path in known:
                continue
            # Calibre renames a book's folder and files when its title or author changes:
            # the file of that format it no longer lists follows it to the new path.
            renamed = next((file for file in known.values() if file.format == format
                            and file.path not in calibre_paths), None)
            if renamed is not None:
                self.library.set_file_path(renamed.id, path, hash=partial_md5(path),
                                           size=os.path.getsize(path))
                report.moved.append(renamed.id)
            elif format not in current.formats:
                self.library.add_file(book_id, path, hash=partial_md5(path),
                                      size=os.path.getsize(path), format=format)
                report.merged.append(book_id)
        if current.source_modified == book.last_modified:
            return
        # Field by field: Calibre's change is taken where the book still says what Calibre
        # last said (a detail edited in Bookcase since keeps the edit). A book linked before
        # Bookcase remembered (no source_values) takes everything, as it always did.
        try:
            before = json.loads(current.source_values) if current.source_values else None
        except ValueError:
            before = None
        now = source_values(current, self.covers.data(book_id))

        def unedited(field):
            return before is None or now.get(field) == before.get(field)

        info = book.info
        identifiers = {k: v for k, v in info.identifiers.items() if k != 'uuid'}
        fresh = {'title': info.title, 'authors': info.authors, 'series': info.series,
                 'series_index': info.series_index, 'tags': info.tags,
                 'publisher': info.publisher, 'published': info.published,
                 'language': info.language, 'description': info.description,
                 'identifiers': identifiers, 'rating': book.rating}
        changes = {field: value for field, value in fresh.items() if unedited(field)}
        self.library.update_book(book_id, source_modified=book.last_modified, **changes)
        cover = None
        if book.cover_path:
            with contextlib.suppress(OSError), open(book.cover_path, 'rb') as file:
                cover = file.read()
        if cover is not None and unedited('cover') and cover != self.covers.data(book_id):
            self._save_cover(book_id, cover)
        # Remember what Calibre says, not what the book now holds: a detail kept as edited
        # remembers Calibre's value, so the edit is still told from it at the next rescan.
        kept = {field: fresh[field] for field in fresh if field not in changes}
        if cover is not None and not unedited('cover'):
            kept['cover'] = cover
        self._remember_source(book_id, kept)
        report.updated.append(book_id)

    def _mark_gone_calibre(self, folder, seen, report):
        for folder_record in self.library.folders():
            if folder_record.path != folder.rstrip('/'):
                continue
            gone = []
            for file in self.library.folder_files(folder_record.id):
                book = self.library.book(file.book_id)
                if not file.missing and (not os.path.exists(file.path) or (
                        book is not None and book.source == 'calibre'
                        and book.source_key not in seen)):
                    gone.append(file.id)
            if gone:
                self.library.set_missing(gone, True)
                report.missing.extend(gone)

    # -- threads ------------------------------------------------------------------------

    def _run_async(self, method, args, progress, done):
        job = Job()
        main = self.library

        def report_progress(number, total, path):
            if progress is not None:
                GLib.idle_add(_call, progress, number, total, path)

        def run():
            worker = main.open_worker()
            try:
                importer = Importer(worker, self.covers.with_library(worker),
                                    self.library_folder)
                report = getattr(importer, method)(*args, progress=report_progress,
                                                   cancelled=job.cancelled)
            except Exception as error:
                log.exception('%s failed', method)
                report = ImportReport()
                report.failed.append((str(args[0]), str(error)))
            finally:
                worker.close()
            GLib.idle_add(main.notify_changed, 'books', 'files', 'folders')
            if done is not None:
                GLib.idle_add(_call, done, report)

        job.thread = threading.Thread(target=run, name=f'bookcase-{method}', daemon=True)
        job.thread.start()
        return job

    def add_async(self, paths, copy=True, progress=None, done=None):
        return self._run_async('add', (list(paths), copy), progress, done)

    def scan_async(self, folder, progress=None, done=None):
        return self._run_async('scan', (folder,), progress, done)

    def link_calibre_async(self, path, progress=None, done=None):
        return self._run_async('link_calibre', (path,), progress, done)


def _call(function, *args):
    function(*args)
    return GLib.SOURCE_REMOVE


def _rank(format):
    return READING_ORDER.index(format) if format in READING_ORDER else len(READING_ORDER)
