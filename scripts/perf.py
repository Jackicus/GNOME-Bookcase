#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-2.0-or-later
# SPDX-FileCopyrightText: 2026 Jack Tully

"""How long the library window takes to show on a large library, without a human.

    scripts/headless.sh scripts/perf.py [--books 10000] [--page home|all|…] [--keep DIR]

Makes an invented library of --books books (in build/perf, through library.py's own
methods: authors, series, tags, shelves, progress, a few files missing) unless it is there
already with that many, then starts the installed app on it (harness.make_app) and prints
the milliseconds from the start of the process to the window's first frame (and the app's
startup, and the window's own share), with the page
asked for shown first (the last-page setting), and the slowest steps of the startup
(cProfile, with --profile). Nothing touches the real library: build/perf is its own.
"""

import argparse
import os
import random
import sys
import time

START = time.monotonic()

import harness  # noqa: E402

parser = argparse.ArgumentParser()
parser.add_argument('--books', type=int, default=10000)
parser.add_argument('--page', default='home')
parser.add_argument('--profile', action='store_true')
args = parser.parse_args()

DIRECTORY = os.path.join(harness.ROOT, 'build', 'perf')
WORDS = ('harbour lantern salt glass river heron moth quiet winter orchard tide compass '
         'paper ember meadow cinder hollow silver north copper garden atlas').split()
NAMES = ('Ada Lark', 'Ben Ross', 'Cy Moor', 'Di Fen', 'Eli Strand', 'Fay Holm', 'Gus Thorne',
         'Hal Brook', 'Ivy Marsh', 'Jo Wren')


def make_library(path, count):
    sys.path.insert(1, harness.PKGDATADIR)
    from bookcase.formats import BookInfo
    from bookcase.library import Library

    if os.path.exists(path):
        library = Library(path)
        have = library.count()
        library.close()
        if have == count:
            return
        os.unlink(path)
    rng = random.Random(7)
    library = Library(path)
    started = time.monotonic()
    shelf = library.add_shelf('Holiday')
    library.add_shelf('Five Stars', query='rating:5')
    for start in range(0, count, 500):
        with library.undoable(None):
            for number in range(start, min(count, start + 500)):
                title = ' '.join(rng.choice(WORDS).title() for _ in range(rng.randint(1, 4)))
                authors = [f'{rng.choice(NAMES)} {number % 400}']
                series = f'The {rng.choice(WORDS).title()} Cycle {number % 300}' \
                    if number % 3 == 0 else ''
                info = BookInfo(title=f'{title} {number}', authors=authors, format='epub',
                                series=series, series_index=number % 7 + 1,
                                tags=[rng.choice(WORDS).title(), rng.choice(WORDS).title()],
                                language=rng.choice(('en', 'en', 'de', 'fr')),
                                publisher='Lantern House', published=str(1900 + number % 120))
                book_id = library.add_book(info, f'/invented/perf/{number}.epub',
                                           hash=f'perf-{number}', size=1000)
                if number % 13 == 0:
                    library.set_progress(book_id, rng.random(), 'epubcfi(/6/2)')
                if number % 50 == 0:
                    library.add_to_shelf(shelf, [book_id])
                if number % 97 == 0:
                    library.update_book(book_id, rating=10)
    library.set_missing([row[0] for row in library.db.execute(
        'SELECT id FROM files WHERE id % 211 = 0')])
    library.close()
    print(f'perf: made {count} books in {time.monotonic() - started:.1f} s', file=sys.stderr)


os.makedirs(DIRECTORY, exist_ok=True)
make_library(os.path.join(DIRECTORY, 'library.sqlite'), args.books)
os.environ['BOOKCASE_DATA_DIR'] = DIRECTORY
app = harness.make_app('Perf', size=(1100, 760))

from gi.repository import GLib  # noqa: E402

profiler = None
if args.profile:
    import cProfile

    profiler = cProfile.Profile()


MARKS = {}


def on_startup(_app):
    MARKS['startup'] = time.monotonic()
    app.settings.set_string('last-page', args.page)
    if profiler is not None:
        profiler.enable()


def on_window_added(_app, window):
    MARKS.setdefault('window', time.monotonic())
    def first_frame(*_args):
        elapsed = (time.monotonic() - START) * 1000
        if profiler is not None:
            profiler.disable()
        window_ms = (time.monotonic() - MARKS['window']) * 1000
        startup_ms = (MARKS['window'] - MARKS['startup']) * 1000
        print(f'perf: {args.books} books, page {args.page}: first frame after '
              f'{elapsed:.0f} ms from the process start; {startup_ms:.0f} ms of app '
              f'startup (library, settings, actions), {window_ms:.0f} ms from the window '
              'being made to its first frame')
        if profiler is not None:
            import pstats

            pstats.Stats(profiler).sort_stats('cumulative').print_stats(25)
        GLib.timeout_add(200, lambda: app.quit())

    def on_map(*_args):
        clock = window.get_frame_clock()
        handler = []

        def after_paint(clock):
            clock.disconnect(handler[0])
            first_frame()

        handler.append(clock.connect('after-paint', after_paint))

    if type(window).__name__ != 'Window':
        return
    window.connect('map', on_map)


app.connect('startup', on_startup)
app.connect('window-added', on_window_added)
harness.run_app(app)
