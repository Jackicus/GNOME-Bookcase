# SPDX-License-Identifier: GPL-2.0-or-later
# SPDX-FileCopyrightText: 2026 Jack Tully

"""The cover store and its thumbnail cache, in the data directory.

    covers = CoverStore(data_dir, library)
    covers.save(book_id, data)            # an undoable 'Set Cover'; returns the file's path
    covers.remove(book_id)                # an undoable 'Remove Cover'
    covers.path(book)                     # the current cover file (book or id), or None
    covers.data(book)                     # its bytes, or None
    covers.thumbnail_path(book, width)    # made now if needed (blocking); path or None
    covers.load_thumbnail(book, width, callback)   # callback(path or None) on the main loop
    covers.with_library(worker)           # the same store on a worker's Library (threads)
    covers.prune()                        # at startup: drop files no book version uses

A cover is kept as the image's own bytes in `covers/<id>-<version>.<ext>` (the extension
from the magic bytes), where version is the book's `cover_version`: Library.update_book()
bumps it whenever `has_cover` is set, and undo puts the old version back, so the previous
cover file is still there for it. Files of older versions stay until prune() (called when
the app starts, when the undo stack is empty) removes them.

Thumbnails are PNGs at `thumbnails/<width>/<id>-<version>.png`, the cover scaled to that
width keeping its aspect ratio (GdkPixbuf; never wider than the cover itself). They are
made in a small thread pool; the callback runs on the main loop through GLib.idle_add, or
at once when the thumbnail is already there.
"""

import concurrent.futures
import glob
import logging
import os
import re
import tempfile
import threading
from gettext import gettext as _

from .formats import IMAGE_SUFFIXES, image_type

log = logging.getLogger(__name__)

try:
    import gi
    gi.require_version('GdkPixbuf', '2.0')
    from gi.repository import GdkPixbuf, GLib
except (ImportError, ValueError):  # pragma: no cover - GdkPixbuf comes with GTK
    GdkPixbuf = None
    from gi.repository import GLib

WORKERS = 3
_NAME = re.compile(r'(\d+)-(\d+)\.\w+$')


class CoverStore:

    def __init__(self, directory, library, _shared=None):
        self.directory = str(directory)
        self.library = library
        self.covers_dir = os.path.join(self.directory, 'covers')
        self.thumbnails_dir = os.path.join(self.directory, 'thumbnails')
        os.makedirs(self.covers_dir, exist_ok=True)
        if _shared is None:
            _shared = {'lock': threading.Lock(), 'pending': {}, 'pool': None}
        self._shared = _shared

    def with_library(self, library):
        """This store on another Library (a worker's), sharing the files and the pool."""
        return CoverStore(self.directory, library, self._shared)

    def _book(self, book):
        return self.library.book(book) if isinstance(book, int) else book

    def _files(self, book_id):
        return glob.glob(os.path.join(glob.escape(self.covers_dir), f'{book_id}-*.*'))

    def _file(self, book_id, version):
        for path in self._files(book_id):
            match = _NAME.search(os.path.basename(path))
            if match and int(match.group(2)) == version:
                return path
        return None

    def save(self, book_id, data):
        """Make data (an image's bytes) the book's cover. Returns the file's path; raises
        ValueError for bytes that are not an image."""
        kind = image_type(data)
        if kind is None:
            raise ValueError('Not an image')
        book = self.library.book(book_id)
        if book is None:
            raise ValueError(f'No book {book_id}')
        version = book.cover_version + 1
        path = self._write(book_id, version, kind, data)
        with self.library.undoable(_('Set Cover')):
            self.library.update_book(book_id, has_cover=True)
        actual = self.library.book(book_id).cover_version
        if actual != version:
            # The library numbered it otherwise: the file follows the book's version.
            stale = self._file(book_id, actual)
            if stale and stale != path:
                os.unlink(stale)
            moved = os.path.join(self.covers_dir, f'{book_id}-{actual}{IMAGE_SUFFIXES[kind]}')
            os.replace(path, moved)
            path = moved
        return path

    def _write(self, book_id, version, kind, data):
        for old in self._files(book_id):  # a leftover of an undone change
            match = _NAME.search(os.path.basename(old))
            if match and int(match.group(2)) == version:
                os.unlink(old)
        path = os.path.join(self.covers_dir, f'{book_id}-{version}{IMAGE_SUFFIXES[kind]}')
        fd, temporary = tempfile.mkstemp(dir=self.covers_dir, prefix='.cover-')
        try:
            with os.fdopen(fd, 'wb') as file:
                file.write(data)
            os.replace(temporary, path)
        except BaseException:
            if os.path.exists(temporary):
                os.unlink(temporary)
            raise
        return path

    def remove(self, book_id):
        """The book has no cover any more (undoable; the file stays for undo)."""
        with self.library.undoable(_('Remove Cover')):
            self.library.update_book(book_id, has_cover=False)

    def path(self, book):
        """The book's current cover file (a Book or an id), or None."""
        book = self._book(book)
        if book is None or not book.has_cover:
            return None
        return self._file(book.id, book.cover_version)

    def data(self, book):
        path = self.path(book)
        if path is None:
            return None
        try:
            with open(path, 'rb') as file:
                return file.read()
        except OSError:
            return None

    def _thumbnail_file(self, book, width):
        return os.path.join(self.thumbnails_dir, str(width),
                            f'{book.id}-{book.cover_version}.png')

    def thumbnail_path(self, book, width):
        """The book's cover at width pixels wide, made now if it is not cached; None when
        the book has no cover or it cannot be read."""
        book = self._book(book)
        source = self.path(book) if book is not None else None
        if source is None:
            return None
        target = self._thumbnail_file(book, width)
        if os.path.exists(target):
            return target
        if GdkPixbuf is None:
            return None
        try:
            pixbuf = GdkPixbuf.Pixbuf.new_from_file(source)
            if pixbuf.get_width() > width:
                height = max(1, round(pixbuf.get_height() * width / pixbuf.get_width()))
                pixbuf = pixbuf.scale_simple(width, height, GdkPixbuf.InterpType.BILINEAR)
            os.makedirs(os.path.dirname(target), exist_ok=True)
            fd, temporary = tempfile.mkstemp(dir=os.path.dirname(target), prefix='.thumb-')
            os.close(fd)
            try:
                pixbuf.savev(temporary, 'png', [], [])
                os.replace(temporary, target)
            finally:
                if os.path.exists(temporary):
                    os.unlink(temporary)
        except GLib.Error as error:
            log.info('Cannot make a thumbnail of %s: %s', source, error.message)
            return None
        return target

    def load_thumbnail(self, book, width, callback):
        """Call callback(path or None) with the book's thumbnail: at once when it is cached,
        else on the main loop once a pool thread has made it. Requests for the same
        thumbnail made meanwhile share the work."""
        book = self._book(book)
        if book is None or not book.has_cover:
            callback(None)
            return
        target = self._thumbnail_file(book, width)
        if os.path.exists(target):
            callback(target)
            return
        key = (book.id, book.cover_version, width)
        shared = self._shared
        with shared['lock']:
            if key in shared['pending']:
                shared['pending'][key].append(callback)
                return
            shared['pending'][key] = [callback]
            if shared['pool'] is None:
                shared['pool'] = concurrent.futures.ThreadPoolExecutor(
                    WORKERS, thread_name_prefix='thumbnail')
            pool = shared['pool']
        # The Book is a frozen snapshot, so the thread reads no library.
        pool.submit(self._make_thumbnail, book, width, key)

    def _make_thumbnail(self, book, width, key):
        try:
            path = self.thumbnail_path(book, width)
        except Exception:
            log.exception('Thumbnail failed')
            path = None

        def deliver():
            with self._shared['lock']:
                callbacks = self._shared['pending'].pop(key, [])
            for callback in callbacks:
                callback(path)
            return GLib.SOURCE_REMOVE

        GLib.idle_add(deliver)

    def prune(self):
        """Remove cover files of versions no book has now, and thumbnails of them. Only when
        nothing can be undone (at startup)."""
        current = {}

        def version(book_id):
            if book_id not in current:
                book = self.library.book(book_id)
                current[book_id] = book.cover_version if book and book.has_cover else None
            return current[book_id]

        covers = glob.glob(os.path.join(glob.escape(self.covers_dir), '*-*.*'))
        thumbnails = glob.glob(os.path.join(glob.escape(self.thumbnails_dir), '*', '*-*.png'))
        for path in covers + thumbnails:
            match = _NAME.search(os.path.basename(path))
            if match and version(int(match.group(1))) != int(match.group(2)):
                os.unlink(path)

    def shutdown(self):
        """Stop the thumbnail pool (at quit; pending callbacks are dropped)."""
        with self._shared['lock']:
            pool, self._shared['pool'] = self._shared['pool'], None
        if pool is not None:
            pool.shutdown(wait=False, cancel_futures=True)
