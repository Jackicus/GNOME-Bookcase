# SPDX-License-Identifier: GPL-2.0-or-later
# SPDX-FileCopyrightText: 2026 Jack Tully

"""E-readers plugged in by cable: Kobo, Kindle, and any reader showing a books folder, as a
USB drive or over MTP.

    monitor = DeviceMonitor()           # app.devices; follows Gio.VolumeMonitor's mounts
    monitor.connect('added', handler)   # handler(monitor, device)
    monitor.connect('removed', handler) # handler(monitor, device_id)
    monitor.connect('changed', handler) # handler(monitor, device_id): its books changed
    monitor.devices()                   # [Device], in the order they came
    monitor.device(device_id)           # Device or None
    monitor.books_changed(device_id)    # say so (after a send or a removal, on the main thread)
    monitor.add_test_root(path, name=None)  # a folder that acts as a plugged-in device
    monitor.remove_test_root(path)

    device.id, .name, .kind, .root, .books_dir, .formats, .local, .transport ('usb', 'mtp')
    device.key                          # what settings remember it by (a Kobo's serial)
    device.space()                      # (free, total) bytes, or (0, 0) when unknown
    device.plan(formats, kepub=True)    # Plan(source, target, label) or None for a book
                                        # with these formats; why_not(formats) says why
    device.list_books()                 # [DeviceBook] (slow: in a thread)
    device.match(library, books)        # {path: book id or None}; also device.book_ids
    device.send(library, covers, book_id, kepub=True, progress=None, cancellable=None,
                remember=None)          # the name written on the device (in a thread)
    device.remove(path)                 # deletes a book file from the device
    device.fetch(path, folder)          # a local copy of a book on the device (in a thread)
    device.has_clippings(), device.read_clippings()  # a Kindle's My Clippings.txt
    device.kobo_database()              # a Kobo's .kobo/KoboReader.sqlite path, or None
    device.content_id(book)             # the Kobo's ContentID for a DeviceBook (kobo.py)
    device.eject(callback, operation=None)  # callback(error or None), on the main thread
    monitor.devices_with_book(book_id)  # [Device] whose last match() found the book

Every file on a device is reached through Gio (Storage, below): a USB drive is a file://
mount with a local path, an MTP reader (2024 and later Kindles, Android readers) an mtp://
mount with none, and one code path serves both. A name on a device (DeviceBook.path,
Device.root, .books_dir, what send() returns) is a local path on a USB drive and a URI over
MTP; Storage.relative() turns either into the path under the device's root. Over MTP a
book's title and author come from its file name (reading each file would mean fetching it),
and it has no content hash.

Kinds and where a sent book goes (each file written under a temporary name, synced on a
local drive, then renamed, so an unplugged reader never shows half a book):

- 'kobo' (`.kobo/` at the root; always a USB drive): `<root>/Bookcase/<Author>/<Title>.kepub.epub`
  (or .epub, .pdf, .cbz…). A Kobo finds books anywhere on its storage, so a folder of our own
  keeps them apart from the reader's. Its database (`.kobo/KoboReader.sqlite`) is read for
  reading progress and written only for collections, opted into per Kobo (kobo.py). The
  name comes from `.kobo/version`'s model id ("Kobo Libra 2"), else "Kobo eReader"; its
  serial (the first field) is its key.
- 'kindle' (`documents/` and `system/` at the root of a drive; over MTP `documents/` in a
  storage such as `Internal Storage`, with `system/` or a mount named Kindle):
  `documents/<Title> - <Author>.azw3`. A Kindle reads AZW3, MOBI, PDF and TXT by cable, over
  USB or MTP, but not EPUB: an EPUB-only book is converted with Calibre's `ebook-convert`
  when it is installed, and otherwise cannot be sent by cable (Amazon's Send to Kindle takes
  EPUB by e-mail: mail.py).
- 'generic' (PocketBook, Tolino, Boox…): a removable drive, or an MTP storage, with a books
  folder at its root (`Books`, `books`, `eBooks`, `ebooks`, `Digital Editions`), which is
  where books go, as `<Title> - <Author>.<ext>`. A plain USB stick with such a folder counts
  too (and over MTP so would a phone with a Books folder).

Device ids are stable for a mount (a hash of its root), so a sidebar key `device:ID` lasts
until the device is unplugged. Mounts of other schemes (smb, sftp, gphoto2…) are not
readers; an MTP mount is looked at in a thread (each look is a round trip to gvfs).

Books on a device are matched to the library by content hash (an exact copy) or, failing
that, by normalised title and author (a copy sent with edited metadata, or a kepub).
list_books() reads each local file's metadata with formats.read(), cached by path, size and
modification time.

Testing and demos: `BOOKCASE_TEST_DEVICE=/some/dir` (several joined by ':') makes the
monitor treat each folder as a device plugged in at startup, detected like a real one; a
folder with no markers is a generic reader whose books folder is the folder itself.
make_test_tree(path, kind) lays out an empty Kobo, Kindle or generic reader there.
Ejecting a test device just removes it. Storage takes a root Gio.File and a stand-in for
Gio.File.new_for_path, so the tests drive the MTP path through a fake Gio-like layer.
"""

import dataclasses
import hashlib
import logging
import os
import posixpath
import shutil
import subprocess
import tempfile
import threading
from gettext import gettext as _
from gettext import ngettext
from urllib.parse import unquote

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
CLIPPINGS = 'My Clippings.txt'
NOT_BOOKS = frozenset((CLIPPINGS,))  # a Kindle's highlights
KOBO_DATABASE = '.kobo/KoboReader.sqlite'
SCHEMES = ('file', 'mtp')  # the mounts that can be e-readers
LIST_ATTRIBUTES = ('standard::name,standard::type,standard::size,standard::is-hidden,'
                   'time::modified,time::modified-usec')


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


def _is_cancelled(error):
    return isinstance(error, GLib.Error) and error.matches(Gio.io_error_quark(),
                                                           Gio.IOErrorEnum.CANCELLED)


def _io_error(error, code):
    return isinstance(error, GLib.Error) and error.matches(Gio.io_error_quark(), code)


@dataclasses.dataclass(frozen=True)
class Plan:
    source: str  # the library format sent ('epub')
    target: str  # the format on the device ('kepub')
    label: str  # 'EPUB → Kobo EPUB', 'AZW3'
    convert: str = ''  # '' (none), 'kepub' or 'ebook-convert'


@dataclasses.dataclass(frozen=True)
class DeviceBook:
    path: str  # a local path, or a URI over MTP
    format: str
    size: int
    mtime: float
    title: str
    authors: tuple = ()
    hash: str = ''
    rel: str = ''  # its path under the device's root ('Bookcase/Ada Lark/x.kepub.epub')

    @property
    def author(self):
        if not self.authors:
            return ''
        if len(self.authors) == 1:
            return self.authors[0]
        if len(self.authors) == 2:
            return _('{first} and {second}').format(first=self.authors[0],
                                                    second=self.authors[1])
        others = len(self.authors) - 1
        return ngettext('{first} and {count} other', '{first} and {count} others',
                        others).format(first=self.authors[0], count=others)


# -- storage -----------------------------------------------------------------------------------

@dataclasses.dataclass(frozen=True)
class Entry:
    name: str
    is_dir: bool
    size: int = 0
    mtime_usec: int = 0
    hidden: bool = False


def location_file(name):
    """A Gio.File for a name on a device: a URI ('mtp://…') or a local path."""
    if '://' in name:
        return Gio.File.new_for_uri(name)
    return Gio.File.new_for_path(name)


class Storage:
    """A device's files through Gio, by paths relative to its root ('documents/x.azw3').

    `root` is a Gio.File (or a Gio.File-like stand-in); `new_for_path` makes the Gio.File of
    a local file (the source of an upload, the target of a fetch)."""

    def __init__(self, root, new_for_path=Gio.File.new_for_path):
        self.root = root
        self.new_for_path = new_for_path
        self.local = root.get_uri_scheme() == 'file' and root.get_path() is not None

    # -- names -------------------------------------------------------------------------------

    def file(self, rel=''):
        return self.root.resolve_relative_path(rel) if rel else self.root

    def name(self, rel=''):
        """What the app keeps for a file: its local path, or its URI over MTP."""
        file = self.file(rel)
        return file.get_path() if self.local else file.get_uri()

    def relative(self, name):
        """The path under the root of a name (a local path or a URI) on this storage: ''
        for the root, None for a name elsewhere."""
        name = os.fspath(name)
        if not self.local:  # by the URI's text: a fake root and a real Gio.File never compare
            base = self.root.get_uri().rstrip('/')
            if name.rstrip('/') == base:
                return ''
            if not name.startswith(base + '/'):
                return None
            parts = [unquote(part) for part in name[len(base) + 1:].rstrip('/').split('/')]
            if any(part in ('', '.', '..') for part in parts):
                return None
            return '/'.join(parts)
        file = location_file(name) if '://' in name else self.new_for_path(name)
        if file.equal(self.root):
            return ''
        return self.root.get_relative_path(file)

    # -- reading -----------------------------------------------------------------------------

    def kind(self, rel):
        """'dir', 'file' or None (absent, or unreadable)."""
        try:
            found = self.file(rel).query_file_type(Gio.FileQueryInfoFlags.NONE, None)
        except GLib.Error:
            return None
        if found == Gio.FileType.DIRECTORY:
            return 'dir'
        if found in (Gio.FileType.REGULAR, Gio.FileType.SYMBOLIC_LINK):
            return 'file'
        return None

    def is_dir(self, rel):
        return self.kind(rel) == 'dir'

    def is_file(self, rel):
        return self.kind(rel) == 'file'

    def children(self, rel=''):
        """[Entry] in a folder. Raises GLib.Error."""
        enumerator = self.file(rel).enumerate_children(
            LIST_ATTRIBUTES, Gio.FileQueryInfoFlags.NONE, None)
        entries = []
        try:
            while (info := enumerator.next_file(None)) is not None:
                name = info.get_name()
                usec = (info.get_attribute_uint64('time::modified') * 1_000_000
                        + info.get_attribute_uint32('time::modified-usec'))
                entries.append(Entry(
                    name=name, is_dir=info.get_file_type() == Gio.FileType.DIRECTORY,
                    size=info.get_size(), mtime_usec=usec,
                    hidden=name.startswith('.') or info.get_is_hidden()))
        finally:
            enumerator.close(None)
        return entries

    def read(self, rel, limit):
        """Up to `limit` bytes of a file. Raises GLib.Error."""
        stream = self.file(rel).read(None)
        try:
            chunks, left = [], limit
            while left > 0:
                data = stream.read_bytes(min(left, 1 << 20), None).get_data()
                if not data:
                    break
                chunks.append(data)
                left -= len(data)
            return b''.join(chunks)
        finally:
            stream.close(None)

    def space(self, rel=''):
        """(free, total) bytes of the file system holding rel; (0, 0) when it will not say."""
        try:
            info = self.file(rel).query_filesystem_info('filesystem::free,filesystem::size',
                                                       None)
        except GLib.Error:
            return 0, 0
        return (info.get_attribute_uint64('filesystem::free'),
                info.get_attribute_uint64('filesystem::size'))

    def download(self, rel, local_path, cancellable=None):
        """Copy a file on the device to a local path. Raises GLib.Error."""
        self.file(rel).copy(self.new_for_path(local_path), Gio.FileCopyFlags.OVERWRITE,
                            cancellable, None, None)

    # -- writing -----------------------------------------------------------------------------

    def make_dirs(self, rel):
        try:
            self.file(rel).make_directory_with_parents(None)
        except GLib.Error as error:
            if not _io_error(error, Gio.IOErrorEnum.EXISTS):
                raise

    def upload(self, local_path, rel, progress=None, cancellable=None):
        """Copy a local file to rel: through a temporary name beside it (synced on a local
        drive), then renamed over rel. progress(bytes done) runs in this thread. Raises
        GLib.Error (CANCELLED when `cancellable` is cancelled)."""
        parent = posixpath.dirname(rel)
        if parent:
            self.make_dirs(parent)
        dest = self.file(rel)
        partial = self.file(posixpath.join(parent, '.' + posixpath.basename(rel) + '.part'))

        def report(done, _total, *_data):
            if progress is not None:
                try:
                    progress(done)
                except Exception:  # never let a callback's failure escape into Gio
                    log.exception('progress')

        try:
            self.new_for_path(local_path).copy(partial, Gio.FileCopyFlags.OVERWRITE,
                                               cancellable, report, None)
            if self.local:
                _fsync(partial.get_path())
            self._replace(partial, dest)
            if self.local:
                _sync(os.path.dirname(dest.get_path()))
        except BaseException:
            try:
                partial.delete(None)
            except GLib.Error:
                pass
            raise

    def _replace(self, partial, dest):
        """Rename partial over dest: a move where the device can (a local drive), else (MTP,
        which may refuse moves) dest deleted and partial renamed in its folder."""
        try:
            partial.move(dest, Gio.FileCopyFlags.OVERWRITE, None, None, None)
            return
        except GLib.Error as error:
            if not _io_error(error, Gio.IOErrorEnum.NOT_SUPPORTED):
                raise
        try:
            dest.delete(None)
        except GLib.Error as error:
            if not _io_error(error, Gio.IOErrorEnum.NOT_FOUND):
                raise
        partial.set_display_name(dest.get_basename(), None)

    def delete(self, rel):
        self.file(rel).delete(None)

    def delete_tree(self, rel):
        """Delete a folder and what is in it, deepest first (a folder is deleted only once
        empty: deleting a full folder over MTP may or may not take its contents)."""
        for entry in self.children(rel):
            child = posixpath.join(rel, entry.name)
            if entry.is_dir:
                self.delete_tree(child)
            else:
                self.delete(child)
        self.delete(rel)

    def remove_dir_if_empty(self, rel):
        """Delete a folder when nothing is in it; whether it was."""
        try:
            if self.children(rel):
                return False
            self.delete(rel)
        except GLib.Error:
            return False
        return True


# -- detection ---------------------------------------------------------------------------------

def _storage(root):
    return root if isinstance(root, Storage) else Storage(location_file(root))


def _read_text(storage, rel, limit=512):
    try:
        return storage.read(rel, limit).decode('utf-8', errors='replace')
    except GLib.Error:
        return None


def _version_fields(storage):
    text = _read_text(storage, '.kobo/version')
    if not text:
        return []
    return [field.strip() for field in text.strip().split(',')]


def kobo_model(root):
    """The Kobo's model name from .kobo/version ("serial,…,firmware,…,…,model-id"), or None."""
    fields = _version_fields(_storage(root))
    if not fields:
        return None
    try:
        model = int(fields[-1].split('-')[-1])
    except ValueError:
        return None
    return KOBO_MODELS.get(model)


def kobo_serial(root):
    """The Kobo's serial number (the first field of .kobo/version), or ''."""
    fields = _version_fields(_storage(root))
    return fields[0] if fields and fields[0] else ''


def _storages(storage):
    """Where on a mount an e-reader's folders may be: its root, and over MTP each storage
    under it ('Internal Storage', 'SD Card')."""
    bases = ['']
    if not storage.local:
        try:
            bases += [entry.name for entry in storage.children('')
                      if entry.is_dir and not entry.hidden]
        except GLib.Error as error:
            log.info('cannot list %s: %s', storage.name(), error)
    return bases


def detect(root, mount_name='', removable=True):
    """(kind, name, books_dir) for a mounted folder (a local path, a URI or a Storage), or
    None when it is no e-reader. books_dir is a name like root's: a path or a URI."""
    storage = _storage(root)
    if storage.is_dir('.kobo') and (storage.is_file(KOBO_DATABASE)
                                    or storage.is_file('.kobo/version')):
        name = kobo_model(storage)
        if name is None:
            name = mount_name if mount_name and mount_name != 'KOBOeReader' else ''
            name = name or 'Kobo eReader'
        return 'kobo', name, storage.name('Bookcase')
    named_kindle = 'kindle' in mount_name.casefold()
    for base in _storages(storage):
        documents = posixpath.join(base, 'documents')
        if storage.is_dir(documents) and (
                storage.is_dir(posixpath.join(base, 'system'))
                or (not storage.local and named_kindle)):
            return 'kindle', mount_name or 'Kindle', storage.name(documents)
    if removable:
        for base in _storages(storage):
            for folder in BOOK_FOLDERS:
                rel = posixpath.join(base, folder)
                if storage.is_dir(rel):
                    name = mount_name or storage.root.get_basename() or _('E-Reader')
                    return 'generic', name, storage.name(rel)
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
    key = root if '://' in root else os.path.abspath(root)
    return hashlib.sha1(key.encode()).hexdigest()[:12]


def safe_name(text, limit=80):
    """A file name part that FAT, exFAT and the readers' own software all take."""
    from .importing import safe_name as library_safe_name

    return library_safe_name(text, limit=limit) or _('Unknown')


# -- metadata cache ----------------------------------------------------------------------------

_cache = {}  # (name, size, mtime_usec) -> (title, authors, hash)
_cache_lock = threading.Lock()


def _read_local(path):
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
    return title, authors, book_hash


def _read_name(name, ours_first):
    """(title, authors, '') from a file name alone. Bookcase names the books it sends
    '<Title> - <Author>'; other files are read as formats.parse_filename reads them."""
    from . import formats

    stem = formats.stem_of(name)
    if ours_first and ' - ' in stem:
        title, author = stem.rsplit(' - ', 1)
        if title.strip() and author.strip():
            return title.strip(), (author.strip(),), ''
    try:
        title, authors = formats.parse_filename(name)
    except Exception:
        title, authors = stem, []
    return title or stem, tuple(authors), ''


def _format_of(path):
    from . import formats

    return formats.format_of(path)


# -- devices -----------------------------------------------------------------------------------

class Device:
    """One e-reader; see the module docstring."""

    def __init__(self, root, kind, name, books_dir, mount=None, storage=None):
        self.root = root
        self.kind = kind
        self.name = name
        self.books_dir = books_dir
        self.mount = mount  # Gio.Mount, None for a test device
        self.storage = storage or Storage(location_file(root))
        self.local = self.storage.local
        self.transport = 'usb' if self.local else 'mtp'
        self.id = device_id(root)
        self.formats = FORMATS[kind]
        self.book_ids = set()  # the library books found by the last match()
        self._serial = None

    def __repr__(self):
        return f'<Device {self.kind} {self.name!r} at {self.root}>'

    @property
    def key(self):
        """What settings remember this reader by: a Kobo's serial, else its id."""
        if self._serial is None:
            self._serial = kobo_serial(self.storage) if self.kind == 'kobo' else ''
        return f'kobo:{self._serial}' if self._serial else self.id

    @property
    def books_rel(self):
        rel = self.storage.relative(self.books_dir) if self.books_dir else ''
        return rel or ''

    def _relative(self, name):
        rel = self.storage.relative(name)
        if not rel:
            raise DeviceError(_('That file is not on {device}').format(device=self.name))
        return rel

    # -- space -------------------------------------------------------------------------------

    def space(self):
        """(free, total) in bytes; (0, 0) when the file system will not say. A test device
        reports an invented 16 GB card, so screenshots never show the real disk's size."""
        if self.mount is None and self.root in _test_monitors:
            used = TEST_SYSTEM_BYTES + _used_bytes(self.root)
            return max(TEST_CAPACITY - used, 0), TEST_CAPACITY
        rel = self.books_rel
        return self.storage.space(rel if rel and self.storage.is_dir(rel) else '')

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
            if self.transport == 'mtp':
                return _('This Kindle takes AZW3, MOBI and PDF by cable, not EPUB. Install '
                         'Calibre to convert it, or e-mail it with Send to Kindle.')
            return _('A Kindle cannot open EPUB books sent by cable. Install Calibre to '
                     'convert them, or use Amazon’s Send to Kindle.')
        return _('No format this reader can open')

    # -- a Kindle's clippings, a Kobo's database ---------------------------------------------

    def _clippings_rel(self):
        return posixpath.join(self.books_rel, CLIPPINGS) if self.kind == 'kindle' else None

    def has_clippings(self):
        """Whether this is a Kindle with a documents/My Clippings.txt (its highlights)."""
        rel = self._clippings_rel()
        return rel is not None and self.storage.is_file(rel)

    def read_clippings(self, limit=50 * 1024 * 1024):
        """A Kindle's My Clippings.txt as text, or None."""
        rel = self._clippings_rel()
        if rel is None:
            return None
        try:
            data = self.storage.read(rel, limit)
        except GLib.Error as error:
            log.info('cannot read %s: %s', rel, error)
            return None
        return data.decode('utf-8-sig', errors='replace')

    def kobo_database(self):
        """The local path of a Kobo's .kobo/KoboReader.sqlite, or None (another kind, not a
        local drive, or no database yet)."""
        if self.kind != 'kobo' or not self.local:
            return None
        path = self.storage.file(KOBO_DATABASE).get_path()
        return path if path and os.path.isfile(path) else None

    def content_id(self, book):
        """The ContentID a Kobo gives a book file it found on its storage (kobo.py)."""
        from . import kobo

        return kobo.content_id(book.rel or self.storage.relative(book.path) or '')

    # -- books on the device -----------------------------------------------------------------

    def _scan_dirs(self):
        if self.kind == 'kobo':
            return ['']  # a Kobo finds books anywhere on it
        return [self.books_rel]

    def list_books(self):
        """[DeviceBook] for every book file on the device (hidden folders and a Kindle's
        .sdr folders skipped), by title. Reads files: call it from a thread. Raises
        DeviceError when the books folder cannot be listed."""
        books = []
        ours_first = self.kind != 'kobo'
        for top in self._scan_dirs():
            folders = [top]
            while folders:
                folder = folders.pop()
                try:
                    entries = self.storage.children(folder)
                except GLib.Error as error:
                    if folder == top and not _io_error(error, Gio.IOErrorEnum.NOT_FOUND):
                        raise DeviceError(error.message) from None
                    continue
                for entry in entries:
                    rel = posixpath.join(folder, entry.name) if folder else entry.name
                    if entry.hidden:
                        continue
                    if entry.is_dir:
                        if not entry.name.endswith('.sdr'):
                            folders.append(rel)
                        continue
                    if entry.name in NOT_BOOKS:
                        continue
                    fmt = _format_of(entry.name)
                    if fmt is not None:
                        books.append(self._book(rel, entry, fmt, ours_first))
        books.sort(key=lambda book: (book.title.casefold(), book.path))
        return books

    def _book(self, rel, entry, fmt, ours_first):
        name = self.storage.name(rel)
        key = (name, entry.size, entry.mtime_usec)
        with _cache_lock:
            cached = _cache.get(key)
        if cached is None:
            cached = _read_local(name) if self.local else _read_name(entry.name, ours_first)
            with _cache_lock:
                _cache[key] = cached
        title, authors, book_hash = cached
        return DeviceBook(path=name, format=fmt, size=entry.size,
                          mtime=entry.mtime_usec / 1_000_000, title=title, authors=authors,
                          hash=book_hash, rel=rel)

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

    def fetch(self, path, folder):
        """A local file with the content of a book on the device: the book's own path on a
        local drive, else a copy made in `folder`. Raises DeviceError."""
        if self.local:
            return path
        rel = self._relative(path)
        local = os.path.join(folder, posixpath.basename(rel))
        try:
            self.storage.download(rel, local)
        except GLib.Error as error:
            raise DeviceError(error.message) from None
        return local

    # -- sending -----------------------------------------------------------------------------

    def destination(self, title, author, fmt):
        """Where a book of this title, main author and device format goes (a name like
        books_dir's)."""
        return self.storage.name(self._destination_rel(title, author, fmt))

    def _destination_rel(self, title, author, fmt):
        suffix = SUFFIXES.get(fmt, f'.{fmt}')
        title = safe_name(title)
        author = safe_name(author, 60) if author else ''
        if self.kind == 'kobo':
            return posixpath.join(self.books_rel, author or _('Unknown'), title + suffix)
        name = f'{title} - {author}' if author else title
        return posixpath.join(self.books_rel, name + suffix)

    def send(self, library, covers, book_id, kepub=True, progress=None, cancellable=None,
             remember=None):
        """Copy a book to the device, with its library metadata written in (and as a kepub
        for a Kobo when `kepub`), and return its name there. progress(fraction) is called in
        this thread; remember(local_path) is given the copy sent (named as on the device)
        before it is deleted. Raises DeviceError, Cancelled or OSError. Call it from a
        thread, with a library of the thread's own (Library.open_worker())."""
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
            rel = self._destination_rel(book.title, book.authors[0] if book.authors else '',
                                        plan.target)
            outgoing = os.path.join(work, 'out')
            os.makedirs(outgoing)
            final = os.path.join(outgoing, posixpath.basename(rel))
            os.replace(copy, final)
            size = os.path.getsize(final)
            free, _total = self.space()
            if free and size > free:
                raise DeviceError(_('Not enough space on {device}').format(device=self.name))
            try:
                self.storage.upload(final, rel,
                                    lambda done: progress and progress(
                                        0.5 + 0.5 * min(done, size) / max(size, 1)),
                                    cancellable)
            except GLib.Error as error:
                if _is_cancelled(error):
                    raise Cancelled() from None
                raise DeviceError(error.message) from None
            report(1.0)
            if remember is not None:
                remember(final)
            return self.storage.name(rel)
        finally:
            shutil.rmtree(work, ignore_errors=True)

    def remove(self, path):
        """Delete a book file from the device (and a Kindle's .sdr folder beside it); empty
        folders it leaves inside the books folder go too. Raises DeviceError."""
        rel = self._relative(path)
        try:
            self.storage.delete(rel)
            if self.kind == 'kindle':
                sidecar = posixpath.splitext(rel)[0] + '.sdr'
                if self.storage.is_dir(sidecar):
                    self.storage.delete_tree(sidecar)
        except GLib.Error as error:
            raise DeviceError(error.message) from None
        books_rel = self.books_rel
        parent = posixpath.dirname(rel)
        while parent and parent.startswith(books_rel + '/' if books_rel else ''):
            if parent == books_rel or not self.storage.remove_dir_if_empty(parent):
                break
            parent = posixpath.dirname(parent)
        if self.local:
            _sync(self.storage.file(parent).get_path() if parent else self.storage.name())

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


def _fsync(path):
    """Flush a file written on a local drive."""
    try:
        fd = os.open(path, os.O_RDONLY)
    except OSError:
        return
    try:
        os.fsync(fd)
    except OSError:
        pass
    finally:
        os.close(fd)


def _sync(directory):
    """Flush a folder's entries (a file written, renamed or removed in it) to the device,
    not every file system the computer has, as os.sync() would."""
    try:
        fd = os.open(directory, os.O_RDONLY | os.O_DIRECTORY)
    except (OSError, TypeError):
        return
    try:
        os.fsync(fd)
    except OSError:
        pass
    finally:
        os.close(fd)


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
        self._looking = set()  # the root URIs of MTP mounts being looked at in a thread
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

    def add_mount(self, mount, storage=None):
        """Look at a mount and add it when it is an e-reader: at once for a local drive, in
        a thread for an MTP one. `storage` stands in for the mount's files (tests)."""
        self._on_mount_added(None, mount, storage)

    def _on_mount_added(self, _monitor, mount, storage=None):
        if mount.is_shadowed():
            return
        root = mount.get_root()
        scheme = root.get_uri_scheme()
        if scheme not in SCHEMES:
            return
        storage = storage or Storage(root)
        drive = mount.get_drive()
        removable = (scheme == 'mtp' or mount.can_eject()
                     or (drive is not None and drive.is_removable()))
        name = mount.get_name() or ''
        uri = root.get_uri()
        if storage.local:
            self._found(mount, uri, storage, detect(storage, name, removable=removable))
            return
        self._looking.add(uri)

        def look():
            try:
                found = detect(storage, name, removable=removable)
            except Exception:
                log.exception('looking at %s', uri)
                found = None
            GLib.idle_add(arrived, found)

        def arrived(found):
            if uri in self._looking:
                self._looking.discard(uri)
                self._found(mount, uri, storage, found)
            return GLib.SOURCE_REMOVE

        threading.Thread(target=look, name='bookcase-mtp-look', daemon=True).start()

    def _found(self, mount, uri, storage, found):
        if found is None:
            return
        kind, name, books_dir = found
        root = storage.name()
        device = self._add(Device(root, kind, name, books_dir, mount, storage=storage))
        self._mount_ids[uri] = device.id

    def _on_mount_removed(self, _monitor, mount):
        uri = mount.get_root().get_uri()
        self._looking.discard(uri)
        device_id_ = self._mount_ids.pop(uri, None)
        if device_id_ is not None:
            self._remove(device_id_)
