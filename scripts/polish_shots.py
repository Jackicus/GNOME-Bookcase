# SPDX-License-Identifier: GPL-2.0-or-later
# SPDX-FileCopyrightText: 2026 Jack Tully

"""screenshot.py's --scene: the library's series stacks, Recently Opened, a past year's
statistics and its Year in Review, and the catalogue's downloads, set up over the demo (or a
copy of it, never the real library).

    polish_shots.prepare(name)      # before harness.make_app (a copy of the demo for
                                    # 'opened', 'year' and 'review'; for the last two, its
                                    # reading moved back a year)
    polish_shots.steps(app, name)   # [(step(window), delay ms)] for screenshot.py

Scenes: stacks (All Books with Group Series on), opened (two books read without adding them,
on Home), year (Statistics for last year), review (last year's Year in Review), downloads
(the catalogue's Downloads popover: one under way, one done, one failed).
"""

import datetime
import os
import shutil
import sqlite3

import harness

NAMES = ('stacks', 'opened', 'year', 'review', 'downloads')
YEAR_SHIFT = 364  # days: whole weeks, so the days of the week stay


def prepare(name):
    if name not in ('year', 'review', 'opened'):
        return
    harness.ensure_demo_library()
    target = os.path.join(harness.ROOT, 'build', 'scenes', f'polish-{name}')
    shutil.rmtree(target, ignore_errors=True)
    shutil.copytree(harness.DEMO_DIR, target)
    os.environ['BOOKCASE_DATA_DIR'] = target
    if name == 'opened':
        return
    shift = YEAR_SHIFT * 86400
    db = sqlite3.connect(os.path.join(target, 'library.sqlite'))
    try:
        db.execute('UPDATE sessions SET started = started - ?', (shift,))
        db.execute('UPDATE books SET finished = finished - ? WHERE finished > 0', (shift,))
        db.execute('UPDATE books SET last_read = last_read - ? WHERE last_read > 0', (shift,))
        db.commit()
    finally:
        db.close()


def last_year():
    return datetime.date.today().year - 1


def steps(app, name):
    if name == 'stacks':
        app.settings.set_boolean('group-series', True)
        app.settings.set_string('sort-order', 'title')
        return [(lambda window: window.show_root('all'), 800)]
    if name == 'opened':
        return [(lambda window: _add_opened(app), 300),
                (lambda window: window.show_root('home'), 900)]
    if name == 'year':
        return [(lambda window: window.show_root('stats'), 600),
                (lambda window: _visible(window).set_year(last_year()), 900)]
    if name == 'review':
        return [(lambda window: window.show_root('stats'), 600),
                (lambda window: _visible(window).set_year(last_year()), 600),
                (lambda window: _visible(window).show_review(), 1200)]
    if name == 'downloads':
        import catalog_shots

        catalog_shots.setup(app)
        return [(lambda window: window.show_root('discover'), 600),
                (catalog_shots.open_catalog, 2500),
                (lambda window: _downloads(app, window), 800)]
    raise ValueError(name)


def _visible(window):
    return window.navigation_view.get_visible_page()


def _add_opened(app):
    """Two books opened from outside, read a little (scenes.outside_book's file)."""
    import scenes
    from bookcase import formats, importing

    path = scenes.outside_book()
    info = formats.read(path)
    library = app.library
    if any(book.title == info.title for book in library.opened_books(limit=None)):
        return
    size = os.path.getsize(path)
    book_id = library.add_opened(info, path, hash=importing.partial_md5(path), size=size)
    library.set_progress(book_id, 0.34, '')
    info.title = 'Letters from the Lighthouse'
    info.authors = ['Morwenna Kett']
    other = library.add_opened(info, path + '#copy', hash='scene-opened', size=size)
    library.set_progress(other, 0.08, '')


def _downloads(app, window):
    """The catalogue's Downloads popover over three invented downloads."""
    from bookcase import opds
    from bookcase.pages.catalog import downloads

    manager = downloads()
    page = _visible(window)
    tiles = getattr(page, '_tiles', [])
    entries = [tile.entry for tile in tiles[:3]]
    while len(entries) < 3:
        entries.append(opds.Entry(title=f'A Book {len(entries) + 1}', id=f'scene:{len(entries)}'))
    done_book = app.library.books(limit=1)[0].id
    states = [('downloading', 0.42), ('done', done_book),
              ('failed', 'The catalogue did not answer in time')]
    for entry, state in reversed(list(zip(entries, states, strict=True))):  # newest first
        manager._entries[entry.key] = entry
        manager._states[entry.key] = state
    manager.emit('started', entries[0].key)
    page.downloads_button.popup()
