# SPDX-License-Identifier: GPL-2.0-or-later
# SPDX-FileCopyrightText: 2026 Jack Tully

"""Finding the books a new user already has, for the welcome page (pages/home.py): Calibre
libraries and folders with e-books in them. Read only, quick, and bounded.

    sources = existing_books.find(home=None, config_dirs=None, seconds=3.0)   # [Source]
    existing_books.calibre_libraries(home, config_dirs) -> [path]   # the ones that exist
    existing_books.count_books(path, max_depth=3, deadline=None, ebooks_only=False, skip=())
                                            # -> Count(count, paths, complete)
    existing_books.home_dir()                     # BOOKCASE_HOME_HINTS, else the home directory

A Source is (kind, path, count, paths, complete):

- 'calibre': a Calibre library (a folder with metadata.db); `count` is its books, read from
  metadata.db opened read-only (None when it cannot be read). Looked for in ~/Calibre
  Library, ~/calibre, ~/Documents/Calibre Library, and the library_path of Calibre's
  global.py.json (its native and its Flatpak configuration).
- 'folder': a folder people keep books in (~/Books, ~/Documents/Books, ~/eBooks, ~/Ebooks):
  every book file counts, PDFs included. Worth watching (read in place) or copying in.
- 'files': a folder that holds books among other things (Downloads, Documents): only
  e-book formats count (not PDFs or plain text, which there are mostly not books), and
  `paths` lists them, so they can be copied in without taking the rest.

Counting walks at most `max_depth` folders down, skips hidden folders, Calibre libraries
(found on their own) and the other places looked at (each is counted once), never follows
symbolic links, and stops at the deadline or after MAX_ENTRIES entries: `complete` is False
then, and the count is at least what was found. find() shares one deadline between all the
places, so it returns in about `seconds` whatever the disk holds; it runs in a thread
(the welcome page's), never on the main loop.

BOOKCASE_HOME_HINTS names a folder to look in instead of the home directory (its .config
stands for the user's configuration): the screenshots and tests use invented ones, never
the real home.
"""

import dataclasses
import json
import logging
import os
import sqlite3
import time

from gi.repository import GLib

from . import formats

log = logging.getLogger(__name__)

MAX_ENTRIES = 40000  # directory entries looked at in one place
MAX_PATHS = 5000  # files a 'files' source remembers
# Formats that are books wherever they are found; PDFs and plain text are often not.
EBOOK_FORMATS = {'epub', 'kepub', 'mobi', 'azw3', 'fb2', 'fbz', 'cbz', 'cbr'}
CALIBRE_NAMES = ('Calibre Library', 'calibre', 'Calibre', os.path.join('Documents',
                                                                        'Calibre Library'))
BOOK_FOLDERS = ('Books', os.path.join('Documents', 'Books'), 'eBooks', 'Ebooks', 'E-books')


@dataclasses.dataclass
class Source:
    kind: str  # 'calibre', 'folder', 'files'
    path: str
    count: int | None
    paths: tuple = ()  # a 'files' source's book files
    complete: bool = True


@dataclasses.dataclass
class Count:
    count: int
    paths: list
    complete: bool


def home_dir():
    """Where to look: BOOKCASE_HOME_HINTS when set (screenshots, tests), else the home."""
    return os.environ.get('BOOKCASE_HOME_HINTS') or GLib.get_home_dir()


def _config_dirs(home):
    if os.environ.get('BOOKCASE_HOME_HINTS'):
        config = os.path.join(home, '.config')
    else:
        config = GLib.get_user_config_dir()
    return [config, os.path.join(home, '.var', 'app', 'com.calibre_ebook.calibre', 'config')]


def _special_dir(home, directory, fallback):
    if os.environ.get('BOOKCASE_HOME_HINTS'):
        return os.path.join(home, fallback)
    return GLib.get_user_special_dir(directory) or os.path.join(home, fallback)


def calibre_libraries(home, config_dirs):
    """The Calibre libraries that exist among the usual places and the one Calibre's
    configuration names, each once."""
    candidates = [os.path.join(home, name) for name in CALIBRE_NAMES]
    for config in config_dirs:
        settings = os.path.join(config, 'calibre', 'global.py.json')
        try:
            with open(settings, encoding='utf-8') as file:
                path = json.load(file).get('library_path')
        except (OSError, ValueError, AttributeError):
            continue
        if isinstance(path, str) and path:
            candidates.insert(0, os.path.expanduser(path))
    found, seen = [], set()
    for path in candidates:
        try:
            real = os.path.realpath(path)
        except OSError:
            continue
        if real in seen or not os.path.isfile(os.path.join(real, 'metadata.db')):
            continue
        seen.add(real)
        found.append(path)
    return found


def calibre_count(path):
    """How many books a Calibre library's metadata.db lists, or None."""
    uri = 'file:' + os.path.join(path, 'metadata.db').replace('?', '%3F').replace('#', '%23')
    try:
        db = sqlite3.connect(uri + '?mode=ro', uri=True, timeout=0.5)
    except sqlite3.Error:
        return None
    try:
        return db.execute('SELECT COUNT(*) FROM books').fetchone()[0]
    except sqlite3.Error as error:
        log.debug('counting the books of %s: %s', path, error)
        return None
    finally:
        db.close()


def count_books(path, max_depth=3, deadline=None, ebooks_only=False, skip=()):
    """The book files under `path` (see the module docstring), breadth first."""
    skip = {os.path.realpath(each) for each in skip}
    count, paths, entries = 0, [], 0
    level = [path]
    for depth in range(max_depth + 1):
        deeper = []
        for directory in level:
            try:
                with os.scandir(directory) as found:
                    listed = list(found)
            except OSError:
                continue
            if depth and any(entry.name == 'metadata.db' for entry in listed):
                continue  # a Calibre library: a source of its own
            for entry in listed:
                entries += 1
                if entries > MAX_ENTRIES or (deadline is not None and entries % 256 == 0
                                             and time.monotonic() > deadline):
                    return Count(count, paths, False)
                if entry.name.startswith('.'):
                    continue
                try:
                    if entry.is_dir(follow_symlinks=False):
                        if os.path.realpath(entry.path) not in skip:
                            deeper.append(entry.path)
                        continue
                    if not entry.is_file(follow_symlinks=False):
                        continue
                except OSError:
                    continue
                fmt = formats.format_of(entry.name)
                if fmt is None or (ebooks_only and fmt not in EBOOK_FORMATS):
                    continue
                count += 1
                if len(paths) < MAX_PATHS:
                    paths.append(entry.path)
        if not deeper:
            return Count(count, paths, True)
        level = sorted(deeper)
    return Count(count, paths, not level)


def find(home=None, config_dirs=None, seconds=3.0):
    """The Calibre libraries and folders with books under `home` (home_dir() when None):
    [Source], Calibre libraries first, then folders with books, most books first. Folders
    with none are left out."""
    home = home or home_dir()
    if config_dirs is None:
        config_dirs = _config_dirs(home)
    deadline = time.monotonic() + seconds
    sources = []
    libraries = calibre_libraries(home, config_dirs)
    for path in libraries:
        sources.append(Source('calibre', path, calibre_count(path)))
    places = [(os.path.join(home, name), 'folder') for name in BOOK_FOLDERS]
    places += [(_special_dir(home, GLib.UserDirectory.DIRECTORY_DOWNLOAD, 'Downloads'), 'files'),
               (_special_dir(home, GLib.UserDirectory.DIRECTORY_DOCUMENTS, 'Documents'),
                'files')]
    seen = {os.path.realpath(path) for path in libraries}
    existing = []
    for path, kind in places:
        real = os.path.realpath(path)
        if real in seen or real == os.path.realpath(home) or not os.path.isdir(path):
            continue
        seen.add(real)
        existing.append((path, kind))
    folders = []
    for path, kind in existing:
        others = [other for other, _kind in existing if other != path] + libraries
        counted = count_books(path, max_depth=3 if kind == 'folder' else 2,
                              deadline=deadline, ebooks_only=kind == 'files', skip=others)
        if counted.count:
            folders.append(Source(kind, path, counted.count,
                                  tuple(counted.paths) if kind == 'files' else (),
                                  counted.complete))
    folders.sort(key=lambda source: -source.count)
    return sources + folders
