# SPDX-License-Identifier: GPL-2.0-or-later
# SPDX-FileCopyrightText: 2026 Jack Tully

"""Reading time and speed, from the reading sessions the reader window logs
(Library.log_session).

    stats.daily_seconds(library, since=None)     # {datetime.date: seconds read that day}
    stats.streak(library, today=None)            # days in a row read, up to today (or
                                                 # yesterday, while today is still open)
    stats.book_seconds(library, book_id)         # total seconds spent in a book
    stats.book_times(library)                    # {book_id: seconds}, every book read
    stats.speed(library, book_id)                # the book's fraction per second, or None
    stats.time_left(library, book_id, fraction, chapter_end_fraction=None)
                                                 # TimeLeft(book, chapter) in seconds, or None

A book's speed is its own once it has been read for MIN_SECONDS. Before that it is
estimated from the reader's overall pace in bytes of book file per second (over every book
with enough reading), divided by this book's file size; with no reading anywhere, time_left()
is None. Sessions that jump (more than JUMP of the book at once: a link or the contents
followed) or go backwards are left out of speeds; they still count as reading time.
"""

import dataclasses
import datetime

MIN_SECONDS = 5 * 60
JUMP = 0.2


@dataclasses.dataclass(frozen=True)
class TimeLeft:
    book: float  # seconds to the end of the book
    chapter: float | None  # seconds to the end of the chapter, when its end was given


def daily_seconds(library, since=None):
    days = {}
    for session in library.sessions(since=since):
        day = datetime.date.fromtimestamp(session.started)
        days[day] = days.get(day, 0.0) + session.seconds
    return days


def streak(library, today=None):
    today = today or datetime.date.today()
    since = datetime.datetime.combine(today - datetime.timedelta(days=400),
                                      datetime.time()).timestamp()
    days = {day for day, seconds in daily_seconds(library, since).items() if seconds > 0}
    day = today if today in days else today - datetime.timedelta(days=1)
    count = 0
    while day in days:
        count += 1
        day -= datetime.timedelta(days=1)
    return count


def book_seconds(library, book_id):
    return sum(session.seconds for session in library.sessions(book_id))


def book_times(library):
    times = {}
    for session in library.sessions():
        times[session.book_id] = times.get(session.book_id, 0.0) + session.seconds
    return times


def _reading(sessions):
    """(seconds, fraction) read in the sessions that moved forward steadily."""
    seconds = fraction = 0.0
    for session in sessions:
        moved = session.end_fraction - session.start_fraction
        if session.seconds > 0 and 0 < moved <= JUMP:
            seconds += session.seconds
            fraction += moved
    return seconds, fraction


def speed(library, book_id):
    """The fraction of the book read per second, from its own sessions; None until it has
    been read for MIN_SECONDS."""
    seconds, fraction = _reading(library.sessions(book_id))
    if seconds < MIN_SECONDS or fraction <= 0:
        return None
    return fraction / seconds


def _book_size(library, book_id):
    file = library.reading_file(book_id)
    return file.size if file is not None and file.size > 0 else None


def _global_bytes_per_second(library):
    by_book = {}
    for session in library.sessions():
        by_book.setdefault(session.book_id, []).append(session)
    seconds = read = 0.0
    for book_id, sessions in by_book.items():
        book_seconds_read, fraction = _reading(sessions)
        size = _book_size(library, book_id)
        if size is None or fraction <= 0:
            continue
        seconds += book_seconds_read
        read += fraction * size
    if seconds < MIN_SECONDS or read <= 0:
        return None
    return read / seconds


def time_left(library, book_id, fraction, chapter_end_fraction=None):
    """How long the rest of the book (and of the chapter, if its end is given as a
    fraction) should take at the reader's pace; None when there is nothing to go on."""
    rate = speed(library, book_id)
    if rate is None:
        size = _book_size(library, book_id)
        pace = _global_bytes_per_second(library)
        if size is None or pace is None:
            return None
        rate = pace / size
    fraction = max(0.0, min(1.0, fraction))
    chapter = None
    if chapter_end_fraction is not None:
        chapter = max(0.0, min(1.0, chapter_end_fraction) - fraction) / rate
    return TimeLeft(book=(1.0 - fraction) / rate, chapter=chapter)
