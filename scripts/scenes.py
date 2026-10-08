# SPDX-License-Identifier: GPL-2.0-or-later
# SPDX-FileCopyrightText: 2026 Jack Tully

"""Invented situations for screenshots (screenshot.py's --welcome, --duplicates and
--outside), made under build/, never touching the real home or library:

    welcome()            an empty library (build/scenes/welcome-data) and an invented home
                         (build/scenes/home: a Calibre library, ~/Books, ~/Downloads), set
                         as BOOKCASE_DATA_DIR and BOOKCASE_HOME_HINTS
    duplicates()         a copy of the demo library (build/scenes/duplicates) with a few
                         books added twice, set as BOOKCASE_DATA_DIR
    outside_book()       an EPUB that is in no library (build/scenes/outside/…): one of the
                         demo's with another title; its path

Call welcome() or duplicates() before harness.make_app() (it reads BOOKCASE_DATA_DIR).
"""

import os
import pathlib
import shutil
import sqlite3
import zipfile

import harness

SCENES = pathlib.Path(harness.ROOT) / 'build' / 'scenes'


def _fresh(path):
    shutil.rmtree(path, ignore_errors=True)
    path.mkdir(parents=True)
    return path


def welcome():
    data = _fresh(SCENES / 'welcome-data')
    home = _fresh(SCENES / 'home')
    calibre = home / 'Calibre Library'
    calibre.mkdir()
    db = sqlite3.connect(calibre / 'metadata.db')
    db.execute('CREATE TABLE books (id INTEGER PRIMARY KEY, title TEXT)')
    db.executemany('INSERT INTO books (title) VALUES (?)',
                   [(f'Book {n}',) for n in range(1234)])
    db.commit()
    db.close()
    for n in range(48):
        path = home / 'Books' / f'Author {n % 9}' / f'Story {n}.epub'
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(b'')
    downloads = home / 'Downloads'
    downloads.mkdir()
    for n in range(12):
        (downloads / f'download-{n}.{"epub" if n % 3 else "mobi"}').write_bytes(b'')
    for name in ('invoice.pdf', 'photo.jpg', 'notes.txt'):
        (downloads / name).write_bytes(b'')
    os.environ['BOOKCASE_DATA_DIR'] = str(data)
    os.environ['BOOKCASE_HOME_HINTS'] = str(home)


def duplicates():
    harness.ensure_demo_library()
    target = SCENES / 'duplicates'
    shutil.rmtree(target, ignore_errors=True)
    shutil.copytree(harness.DEMO_DIR, target)
    os.environ['BOOKCASE_DATA_DIR'] = str(target)
    db = sqlite3.connect(target / 'library.sqlite')
    try:
        rows = db.execute("SELECT id FROM books WHERE source = 'library' ORDER BY id "
                          'LIMIT 3').fetchall()
    finally:
        db.close()
    return [row[0] for row in rows]


def make_duplicates(library, book_ids):
    """Add a second copy of each book (another format, its own place), as a second import
    of the same book from elsewhere would."""
    from bookcase.formats import BookInfo

    for number, book_id in enumerate(book_ids):
        book = library.book(book_id)
        fmt = ('pdf', 'mobi', 'epub')[number % 3]
        info = BookInfo(title=book.title, authors=list(book.authors), format=fmt,
                        language=book.language)
        path = f'{harness.ROOT}/build/scenes/elsewhere/{book.title}.{fmt}'
        library.add_book(info, path, hash=f'scene-{book_id}', size=2_400_000,
                         source='watched')
    library.clear_undo()


def outside_book():
    """A demo EPUB under another title (so another hash): a book in no library."""
    harness.ensure_demo_library()
    db = sqlite3.connect(pathlib.Path(harness.DEMO_DIR) / 'library.sqlite')
    try:
        row = db.execute("SELECT path FROM files WHERE format = 'epub' ORDER BY id "
                         'LIMIT 1').fetchone()
    finally:
        db.close()
    folder = _fresh(SCENES / 'outside')
    target = folder / 'A Field Guide to Quiet Harbours.epub'
    with zipfile.ZipFile(row[0]) as source, zipfile.ZipFile(target, 'w') as copy:
        for item in source.infolist():
            data = source.read(item.filename)
            if item.filename.endswith('.opf'):
                text = data.decode('utf-8')
                start = text.index('<dc:title')
                start = text.index('>', start) + 1
                end = text.index('</dc:title>', start)
                data = (text[:start] + 'A Field Guide to Quiet Harbours'
                        + text[end:]).encode('utf-8')
            compress = (zipfile.ZIP_STORED if item.filename == 'mimetype'
                        else zipfile.ZIP_DEFLATED)
            copy.writestr(item, data, compress_type=compress)
    return str(target)
