# SPDX-License-Identifier: GPL-2.0-or-later
# SPDX-FileCopyrightText: 2026 Jack Tully

"""E-readers plugged in over USB: Kobo, Kindle, and any reader showing a books folder.

    monitor = DeviceMonitor()           # app.devices; follows Gio.VolumeMonitor's mounts
    monitor.connect('added', handler)   # handler(monitor, device)
    monitor.connect('removed', handler) # handler(monitor, device_id)
    monitor.connect('changed', handler) # handler(monitor, device_id): its books changed
    monitor.devices()                   # [Device], in the order they came
    monitor.device(device_id)           # Device or None
    monitor.books_changed(device_id)    # say so (after a send or a removal, on the main thread)
    monitor.add_test_root(path, name=None)  # a folder that acts as a plugged-in device
    monitor.remove_test_root(path)

    device.id, .name, .kind, .root, .books_dir, .formats
    device.space()                      # (free, total) bytes, or (0, 0) when unknown
    device.plan(formats, kepub=True)    # Plan(source, target, label) or None for a book
                                        # with these formats; why_not(formats) says why
    device.list_books()                 # [DeviceBook] (slow: in a thread)
    device.match(library, books)        # {path: book id or None}; also device.book_ids
    device.send(library, covers, book_id, kepub=True, progress=None, cancellable=None)
                                        # the path written on the device (in a thread)
    device.remove(path)                 # deletes a book file from the device
    device.eject(callback, operation=None)  # callback(error or None), on the main thread
    monitor.devices_with_book(book_id)  # [Device] whose last match() found the book

Kinds and where a sent book goes (each file written under a temporary name, synced, then
renamed, so an unplugged reader never shows half a book):

- 'kobo' (`.kobo/` at the root): `<root>/Bookcase/<Author>/<Title>.kepub.epub` (or .epub,
  .pdf, .cbz…). A Kobo finds books anywhere on its storage, so a folder of our own keeps them
  apart from the reader's; its database (`.kobo/KoboReader.sqlite`) is never written. The
  name comes from `.kobo/version`'s model id ("Kobo Libra 2"), else "Kobo eReader".
- 'kindle' (`documents/` and `system/` at the root): `<root>/documents/<Title> - <Author>.azw3`.
  A Kindle reads AZW3, MOBI, PDF and TXT over USB but not EPUB: an EPUB-only book is
  converted with Calibre's `ebook-convert` when it is installed, and otherwise cannot be
  sent by cable (Amazon's Send to Kindle takes EPUB by e-mail; not done yet).
- 'generic' (PocketBook, Tolino, Boox in USB mode…): a removable drive with a books folder
  at its root (`Books`, `books`, `eBooks`, `ebooks`, `Digital Editions`), which is where
  books go, as `<Title> - <Author>.<ext>`. A plain USB stick with such a folder counts too.

Device ids are stable for a mount (a hash of its root), so a sidebar key `device:ID` lasts
until the device is unplugged. Only mounts with a local path count: an MTP reader (some
2024 Kindles) is not handled yet.

Books on a device are matched to the library by content hash (an exact copy) or, failing
that, by normalised title and author (a copy sent with edited metadata, or a kepub).
list_books() reads each file's metadata with formats.read(), cached by path, size and
modification time.

Testing and demos: `BOOKCASE_TEST_DEVICE=/some/dir` (several joined by ':') makes the
monitor treat each folder as a device plugged in at startup, detected like a real one; a
folder with no markers is a generic reader whose books folder is the folder itself.
make_test_tree(path, kind) lays out an empty Kobo, Kindle or generic reader there.
Ejecting a test device just removes it.
"""

import dataclasses
import hashlib
import logging
import os
import shutil
import subprocess
import tempfile
import threading
from gettext import gettext as _

from gi.repository import Gio, GLib, GObject

log = logging.getLogger(__name__)

KINDS = ('kobo', 'kindle', 'generic')
BOOK_FOLDERS = ('Books', 'books', 'eBooks', 'ebooks', 'Digital Editions')
# What each kind reads, preferred first.
FORMATS = {
    'kobo': ('kepub', 'epub', 'pdf', 'cbz', 'cbr', 'txt'),
    'kindle': ('azw3', 'mobi', 'pdf', 'txt'),
    'generic': ('epub', 'pdf', 'fb2', 'cbz', 'txt', 'mobi'),
}
SUFFIXES = {'kepub': '.kepub.epub'}
LABELS = {'kepub': 'Kobo EPUB', 'epub': 'EPUB', 'azw3': 'AZW3', 'mobi': 'MOBI', 'pdf': 'PDF',
          'cbz': 'CBZ', 'cbr': 'CBR', 'txt': 'TXT', 'fb2': 'FB2', 'fbz': 'FBZ'}
# The last field of .kobo/version, as an integer, to the model's name.
KOBO_MODELS = {
    310: 'Kobo Touch', 320: 'Kobo Touch', 330: 'Kobo Glo', 340: 'Kobo Mini',
    350: 'Kobo Aura HD', 360: 'Kobo Aura', 370: 'Kobo Aura H2O', 371: 'Kobo Glo HD',
    372: 'Kobo Touch 2.0', 373: 'Kobo Aura ONE', 374: 'Kobo Aura H2O Edition 2',
    375: 'Kobo Aura Edition 2', 376: 'Kobo Clara HD', 377: 'Kobo Forma', 380: 'Kobo Forma',
    381: 'Kobo Aura ONE', 382: 'Kobo Nia', 383: 'Kobo Sage', 384: 'Kobo Libra H2O',
    386: 'Kobo Clara 2E', 387: 'Kobo Elipsa', 388: 'Kobo Libra 2', 389: 'Kobo Elipsa 2E',
    390: 'Kobo Libra Colour', 391: 'Kobo Clara BW', 393: 'Kobo Clara Colour',
}
CHUNK = 1 << 20
NOT_BOOKS = frozenset(('My Clippings.txt',))  # a Kindle's highlights


# A test device's invented card: its size, and what the reader's own system takes of it.
TEST_CAPACITY = 16 * 1000 ** 3
TEST_SYSTEM_BYTES = 5 * 1000 ** 3


def _used_bytes(root):
    """The bytes the files under root take."""
    used = 0
    for directory, _dirs, files in os.walk(root):
        for name in files:
            try:
                used += os.path.getsize(os.path.join(directory, name))
            except OSError:
                continue
    return used


class DeviceError(Exception):
    """A failure to tell the user in a sentence (str(error) is translated)."""


class Cancelled(Exception):
    """send() stopped because its cancellable was cancelled."""


@dataclasses.dataclass(frozen=True)
class Plan:
    source: str  # the library format sent ('epub')
    target: str  # the format on the device ('kepub')
    label: str  # 'EPUB → Kobo EPUB', 'AZW3'
    convert: str = ''  # '' (none), 'kepub' or 'ebook-convert'


@dataclasses.dataclass(frozen=True)
class DeviceBook:
    path: str
    format: str
    size: int
    mtime: float
    title: str
    authors: tuple = ()
    hash: str = ''

    @property
    def author(self):
        if not self.authors:
            return ''
        if len(self.authors) == 1:
            return self.authors[0]
        if len(self.authors) == 2:
            return _('{first} and {second}').format(first=self.authors[0],
                                                    second=self.authors[1])
        return _('{first} and {count} others').format(first=self.authors[0],
                                                      count=len(self.authors) - 1)


# -- detection ---------------------------------------------------------------------------------

def _is_dir(root, *parts):
    return os.path.isdir(os.path.join(root, *parts))


def _is_file(root, *parts):
    return os.path.isfile(os.path.join(root, *parts))


def kobo_model(root):
    """The Kobo's model name from .kobo/version ("serial,…,firmware,…,…,model-id"), or None."""
    try:
        with open(os.path.join(root, '.kobo', 'version'), encoding='utf-8',
                  errors='replace') as file:
            fields = file.read(512).strip().split(',')
    except OSError:
        return None
    if not fields:
        return None
    try:
        model = int(fields[-1].strip().split('-')[-1])
    except ValueError:
        return None
    return KOBO_MODELS.get(model)


def detect(root, mount_name='', removable=True):
    """(kind, name, books_dir) for a mounted folder, or None when it is no e-reader."""
    if _is_dir(root, '.kobo') and (_is_file(root, '.kobo', 'KoboReader.sqlite')
                                   or _is_file(root, '.kobo', 'version')):
        name = kobo_model(root)
        if name is None:
            name = mount_name if mount_name and mount_name != 'KOBOeReader' else ''
            name = name or 'Kobo eReader'
        return 'kobo', name, os.path.join(root, 'Bookcase')
    if _is_dir(root, 'documents') and _is_dir(root, 'system'):
        return 'kindle', mount_name or 'Kindle', os.path.join(root, 'documents')
    if removable:
        for folder in BOOK_FOLDERS:
            if _is_dir(root, folder):
                name = mount_name or os.path.basename(root.rstrip(os.sep)) or _('E-Reader')
                return 'generic', name, os.path.join(root, folder)
    return None


def make_test_tree(path, kind='kobo', model=388):
    """Lay out an empty reader of `kind` in the folder `path` (for tests and demos)."""
    os.makedirs(path, exist_ok=True)
    if kind == 'kobo':
        os.makedirs(os.path.join(path, '.kobo'), exist_ok=True)
        with open(os.path.join(path, '.kobo', 'version'), 'w', encoding='utf-8') as file:
            file.write(f'N000000000000,4.1.15,4.38.21908,4.1.15,4.1.15,'
                       f'00000000-0000-0000-0000-000000000{model:03d}\n')
    elif kind == 'kindle':
        os.makedirs(os.path.join(path, 'documents'), exist_ok=True)
        os.makedirs(os.path.join(path, 'system'), exist_ok=True)
    else:
        os.makedirs(os.path.join(path, 'Books'), exist_ok=True)
    return path


def device_id(root):
    return hashlib.sha1(os.path.abspath(root).encode()).hexdigest()[:12]


def safe_name(text, limit=80):
    """A file name part that FAT, exFAT and the readers' own software all take."""
    from .importing import safe_name as library_safe_name

    return library_safe_name(text, limit=limit) or _('Unknown')


# -- metadata cache ----------------------------------------------------------------------------

_cache = {}  # (path, size, mtime_ns) -> (title, authors, hash)
_cache_lock = threading.Lock()


def _read_book(path, stat, fmt):
    key = (path, stat.st_size, stat.st_mtime_ns)
    with _cache_lock:
        cached = _cache.get(key)
    if cached is None:
        from . import formats, importing

        title, authors = '', ()
        try:
            info = formats.read(path)
            title, authors = info.title, tuple(info.authors)
        except Exception as error:  # a broken file still shows, by its name
            log.info('cannot read %s: %s', path, error)
        if not title:
            try:
                title, names = formats.parse_filename(path)
                authors = authors or tuple(names)
            except Exception:
                title = os.path.basename(path)
        try:
            book_hash = importing.partial_md5(path)
        except OSError:
            book_hash = ''
        cached = (title, authors, book_hash)
        with _cache_lock:
            _cache[key] = cached
    title, authors, book_hash = cached
    return DeviceBook(path=path, format=fmt, size=stat.st_size, mtime=stat.st_mtime,
                      title=title, authors=authors, hash=book_hash)


def _format_of(path):
    from . import formats

    return formats.format_of(path)


# -- devices -----------------------------------------------------------------------------------

class Device:
    """One e-reader; see the module docstring."""

    def __init__(self, root, kind, name, books_dir, mount=None):
        self.root = root
        self.kind = kind
        self.name = name
        self.books_dir = books_dir
        self.mount = mount  # Gio.Mount, None for a test device
        self.id = device_id(root)
        self.formats = FORMATS[kind]
        self.book_ids = set()  # the library books found by the last match()

    def __repr__(self):
        return f'<Device {self.kind} {self.name!r} at {self.root}>'

    # -- space -------------------------------------------------------------------------------

    def space(self):
        """(free, total) in bytes; (0, 0) when the file system will not say. A test device
        reports an invented 16 GB card, so screenshots never show the real disk's size."""
        if self.mount is None and self.root in _test_monitors:
            used = TEST_SYSTEM_BYTES + _used_bytes(self.root)
            return max(TEST_CAPACITY - used, 0), TEST_CAPACITY
        try:
            info = Gio.File.new_for_path(self.root).query_filesystem_info(
                'filesystem::free,filesystem::size', None)
        except GLib.Error:
            return 0, 0
        return (info.get_attribute_uint64('filesystem::free'),
                info.get_attribute_uint64('filesystem::size'))

    # -- what to send ------------------------------------------------------------------------

    def plan(self, formats, kepub=True):
        """How a book with these library formats goes to this device, or None."""
        formats = tuple(formats)
        if self.kind == 'kobo' and kepub and 'kepub' not in formats and 'epub' in formats:
            return Plan('epub', 'kepub', _('EPUB → Kobo EPUB'), 'kepub')
        for fmt in self.formats:
            if fmt in formats:
                if fmt == 'kepub' and not kepub and 'epub' in formats:
                    return Plan('epub', 'epub', LABELS['epub'])
                return Plan(fmt, fmt, LABELS.get(fmt, fmt.upper()))
        if self.kind == 'kindle' and 'epub' in formats and ebook_convert():
            return Plan('epub', 'azw3', _('EPUB → AZW3, with Calibre'), 'ebook-convert')
        return None

    def why_not(self, formats):
        """Why plan() found nothing for a book with these formats: a sentence."""
        if self.kind == 'kindle' and ('epub' in formats or 'kepub' in formats):
            return _('A Kindle cannot open EPUB books sent by cable. Install Calibre to '
                     'convert them, or use Amazon’s Send to Kindle.')
        return _('No format this reader can open')

    # -- books on the device -----------------------------------------------------------------

    def _scan_dirs(self):
        if self.kind == 'kobo':
            return [self.root]  # a Kobo finds books anywhere on it
        return [self.books_dir]

    def list_books(self):
        """[DeviceBook] for every book file on the device (hidden folders and a Kindle's
        .sdr folders skipped), by title. Reads files: call it from a thread."""
        books = []
        for top in self._scan_dirs():
            for directory, dirs, files in os.walk(top):
                dirs[:] = [name for name in dirs
                           if not name.startswith('.') and not name.endswith('.sdr')]
                for name in files:
                    if name.startswith('.') or name in NOT_BOOKS:
                        continue
                    path = os.path.join(directory, name)
                    fmt = _format_of(path)
                    if fmt is None:
                        continue
                    try:
                        stat = os.stat(path)
                    except OSError:
                        continue
                    books.append(_read_book(path, stat, fmt))
        books.sort(key=lambda book: (book.title.casefold(), book.path))
        return books

    def match(self, library, books):
        """{path: library book id or None}, by content hash, then by title and author.
        Remembers the matched ids in self.book_ids."""
        found = {}
        for book in books:
            book_id = library.find_by_hash(book.hash) if book.hash else None
            if book_id is None and book.title:
                similar = library.find_similar(book.title, list(book.authors))
                book_id = similar[0] if similar else None
            found[book.path] = book_id
        self.book_ids = {book_id for book_id in found.values() if book_id is not None}
        return found

    # -- sending -----------------------------------------------------------------------------

    def destination(self, title, author, fmt):
        """Where a book of this title, main author and device format goes."""
        suffix = SUFFIXES.get(fmt, f'.{fmt}')
        title = safe_name(title)
        author = safe_name(author, 60) if author else ''
        if self.kind == 'kobo':
            folder = os.path.join(self.books_dir, author or _('Unknown'))
            return os.path.join(folder, title + suffix)
        name = f'{title} - {author}' if author else title
        return os.path.join(self.books_dir, name + suffix)

    def send(self, library, covers, book_id, kepub=True, progress=None, cancellable=None):
        """Copy a book to the device, with its library metadata written in (and as a kepub
        for a Kobo when `kepub`), and return its path there. progress(fraction) is called in
        this thread. Raises DeviceError, Cancelled or OSError. Call it from a thread, with a
        library of the thread's own (Library.open_worker())."""
        from . import exporting

        book = library.book(book_id)
        if book is None:
            raise DeviceError(_('The book is no longer in the library'))
        formats = [file.format for file in library.files(book_id) if not file.missing]
        plan = self.plan(formats, kepub=kepub)
        if plan is None:
            raise DeviceError(self.why_not(formats))

        def report(fraction):
            if cancellable is not None and cancellable.is_cancelled():
                raise Cancelled()
            if progress is not None:
                progress(fraction)

        report(0.0)
        work = tempfile.mkdtemp(prefix='bookcase-send-')
        try:
            try:
                copy = exporting.export_copy(library, covers, book_id, work,
                                             format=plan.source, embed=True)
            except exporting.ExportError as error:
                raise DeviceError(str(error)) from error
            report(0.3)
            if plan.convert == 'kepub':
                from . import kepub as kepub_module

                converted = os.path.join(work, 'converted.kepub.epub')
                kepub_module.convert(copy, converted)
                copy = converted
            elif plan.convert == 'ebook-convert':
                converted = os.path.join(work, 'converted.azw3')
                _run_ebook_convert(copy, converted)
                copy = converted
            report(0.5)
            dest = self.destination(book.title, book.authors[0] if book.authors else '',
                                    plan.target)
            size = os.path.getsize(copy)
            free, _total = self.space()
            if free and size > free:
                raise DeviceError(_('Not enough space on {device}').format(device=self.name))
            _copy_file(copy, dest, lambda done: report(0.5 + 0.5 * done / max(size, 1)))
            report(1.0)
            return dest
        finally:
            shutil.rmtree(work, ignore_errors=True)

    def remove(self, path):
        """Delete a book file from the device (and a Kindle's .sdr folder beside it); empty
        folders it leaves inside the books folder go too."""
        path = os.path.abspath(path)
        root = os.path.abspath(self.root)
        if os.path.commonpath([path, root]) != root or path == root:
            raise DeviceError(_('That file is not on {device}').format(device=self.name))
        os.remove(path)
        if self.kind == 'kindle':
            sidecar = os.path.splitext(path)[0] + '.sdr'
            if os.path.isdir(sidecar):
                shutil.rmtree(sidecar, ignore_errors=True)
        books_dir = os.path.abspath(self.books_dir)
        parent = os.path.dirname(path)
        while parent.startswith(books_dir + os.sep):
            try:
                os.rmdir(parent)  # only when empty
            except OSError:
                break
            parent = os.path.dirname(parent)
        _sync(parent)

    # -- ejecting ----------------------------------------------------------------------------

    def eject(self, callback, operation=None):
        """Eject the device (or unmount it when it cannot be ejected); callback(error) on the
        main thread, error a GLib.Error or None."""
        if self.mount is None:
            def eject_test_device():
                monitor = _test_monitors.get(self.root)
                if monitor is not None:
                    monitor.remove_test_root(self.root)
                callback(None)
                return GLib.SOURCE_REMOVE

            GLib.idle_add(eject_test_device)
            return
        operation = operation or Gio.MountOperation()
        flags = Gio.MountUnmountFlags.NONE

        def done(mount, result, finish):
            try:
                finish(result)
            except GLib.Error as error:
                callback(error)
                return
            callback(None)

        if self.mount.can_eject():
            self.mount.eject_with_operation(flags, operation, None, done,
                                            self.mount.eject_with_operation_finish)
        else:
            self.mount.unmount_with_operation(flags, operation, None, done,
                                              self.mount.unmount_with_operation_finish)


def ebook_convert():
    """The path of Calibre's ebook-convert, or None."""
    return shutil.which('ebook-convert')


def _run_ebook_convert(src, dest):
    program = ebook_convert()
    if program is None:
        raise DeviceError(_('Calibre’s ebook-convert is not installed'))
    try:
        subprocess.run([program, src, dest], check=True, capture_output=True, timeout=900)
    except (subprocess.CalledProcessError, subprocess.TimeoutExpired) as error:
        log.warning('ebook-convert failed: %s', getattr(error, 'stderr', error))
        raise DeviceError(_('Calibre could not convert the book')) from error


def _sync(directory):
    """Flush a folder's entries (a file written, renamed or removed in it) to the device,
    not every file system the computer has, as os.sync() would."""
    try:
        fd = os.open(directory, os.O_RDONLY | os.O_DIRECTORY)
    except OSError:
        return
    try:
        os.fsync(fd)
    except OSError:
        pass
    finally:
        os.close(fd)


def _copy_file(src, dest, progress):
    """Copy src to dest through a temporary name in dest's folder, synced, then renamed."""
    os.makedirs(os.path.dirname(dest), exist_ok=True)
    partial = os.path.join(os.path.dirname(dest), '.' + os.path.basename(dest) + '.part')
    try:
        with open(src, 'rb') as source, open(partial, 'wb') as target:
            done = 0
            while chunk := source.read(CHUNK):
                target.write(chunk)
                done += len(chunk)
                progress(done)
            target.flush()
            os.fsync(target.fileno())
        os.replace(partial, dest)
        _sync(os.path.dirname(dest))
    except BaseException:
        try:
            os.remove(partial)
        except OSError:
            pass
        raise


# -- the monitor -------------------------------------------------------------------------------

_test_monitors = {}  # test root -> the DeviceMonitor holding it


class DeviceMonitor(GObject.Object):
    """The plugged-in e-readers; see the module docstring."""

    __gsignals__ = {
        'added': (GObject.SignalFlags.RUN_FIRST, None, (object,)),
        'removed': (GObject.SignalFlags.RUN_FIRST, None, (str,)),
        'changed': (GObject.SignalFlags.RUN_FIRST, None, (str,)),
    }

    def __init__(self, watch_mounts=True):
        super().__init__()
        self._devices = {}  # id -> Device, in the order they came
        self._mount_ids = {}  # the mount's root URI -> device id
        self._volume_monitor = None
        if watch_mounts:
            self._volume_monitor = Gio.VolumeMonitor.get()
            self._volume_monitor.connect('mount-added', self._on_mount_added)
            self._volume_monitor.connect('mount-removed', self._on_mount_removed)
            for mount in self._volume_monitor.get_mounts():
                self._on_mount_added(self._volume_monitor, mount)
        for path in os.environ.get('BOOKCASE_TEST_DEVICE', '').split(os.pathsep):
            if path:
                self.add_test_root(path)

    def devices(self):
        return list(self._devices.values())

    def device(self, device_id):
        return self._devices.get(device_id)

    def devices_with_book(self, book_id):
        return [device for device in self._devices.values() if book_id in device.book_ids]

    def books_changed(self, device_id):
        if device_id in self._devices:
            self.emit('changed', device_id)

    def _add(self, device):
        if device.id in self._devices:
            return self._devices[device.id]
        self._devices[device.id] = device
        log.info('device: %r', device)
        self.emit('added', device)
        return device

    def _remove(self, device_id):
        if self._devices.pop(device_id, None) is not None:
            self.emit('removed', device_id)

    # -- test devices ------------------------------------------------------------------------

    def add_test_root(self, path, name=None):
        """Treat the folder `path` as a plugged-in device; returns the Device."""
        root = os.path.abspath(path)
        found = detect(root, name or '', removable=True)
        if found is None:
            kind, found_name, books_dir = 'generic', os.path.basename(root), root
        else:
            kind, found_name, books_dir = found
        device = Device(root, kind, name or found_name, books_dir)
        _test_monitors[root] = self
        return self._add(device)

    def remove_test_root(self, path):
        root = os.path.abspath(path)
        _test_monitors.pop(root, None)
        self._remove(device_id(root))

    # -- mounts ------------------------------------------------------------------------------

    def _on_mount_added(self, _monitor, mount):
        if mount.is_shadowed():
            return
        root = mount.get_root()
        path = root.get_path()
        if path is None:
            return  # MTP and other gvfs mounts: not handled yet
        drive = mount.get_drive()
        removable = mount.can_eject() or (drive is not None and drive.is_removable())
        try:
            found = detect(path, mount.get_name() or '', removable=removable)
        except OSError as error:
            log.info('cannot look at %s: %s', path, error)
            return
        if found is None:
            return
        kind, name, books_dir = found
        device = self._add(Device(path, kind, name, books_dir, mount))
        self._mount_ids[root.get_uri()] = device.id

    def _on_mount_removed(self, _monitor, mount):
        device_id_ = self._mount_ids.pop(mount.get_root().get_uri(), None)
        if device_id_ is not None:
            self._remove(device_id_)
