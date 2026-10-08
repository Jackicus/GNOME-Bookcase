# SPDX-License-Identifier: GPL-2.0-or-later
# SPDX-FileCopyrightText: 2026 Jack Tully

"""Reading time, speed, streaks and goals, from the reading sessions the reader window logs
(Library.log_session) and the dates books were finished (Library.finished).

    stats.daily_seconds(library, since=None)     # {datetime.date: seconds read that day}
    stats.streak(library, today=None)            # days in a row read, up to today (or
                                                 # yesterday, while today is still open)
    stats.book_seconds(library, book_id)         # total seconds spent in a book
    stats.book_times(library)                    # {book_id: seconds}, every book read
    stats.speed(library, book_id)                # the book's fraction per second, or None
    stats.time_left(library, book_id, fraction, chapter_end_fraction=None)
                                                 # TimeLeft(book, chapter) in seconds, or None
    stats.summary(library, today=None)           # Summary: everything the Statistics page
                                                 # shows, in one pass over the sessions
    stats.goal_progress(done, target, today)     # Goal (on schedule?) or None for no goal
    stats.estimated_pages(book_file)             # a file's page count, estimated, or None

A reading day starts at DAY_START_HOUR (4 in the morning, as Retain's): a session at 1 am
counts for the evening before. A day counts towards a streak with MIN_DAY_SECONDS of
reading. current_streak() and longest_streak() take a {date: seconds} as daily_seconds()
gives.

A book's speed is its own once it has been read for MIN_SECONDS. Before that it is
estimated from the reader's overall pace in bytes of book file per second (over every book
with enough reading), divided by this book's file size; with no reading anywhere, time_left()
is None. Sessions that jump (more than JUMP of the book at once: a link or the contents
followed) or go backwards are left out of speeds; they still count as reading time.

Pages are the book's own count when the library knows it (books.pages), else an estimate
from its file: an EPUB's text (its XHTML, at MARKUP_BYTES_PER_PAGE), a PDF's or a comic's
pages, other formats by size. Pages read are the ground each session moved forward, in
pages, never more than MAX_PAGES_PER_MINUTE of its time (a skim through a book is not
reading it).
"""

import dataclasses
import datetime
import logging
import math
import os
import re
import time
import zipfile

log = logging.getLogger(__name__)

MIN_SECONDS = 5 * 60
JUMP = 0.2
DAY_START_HOUR = 4
MIN_DAY_SECONDS = 60
CHARS_PER_PAGE = 1500
MARKUP_BYTES_PER_PAGE = 2000  # an EPUB's XHTML: CHARS_PER_PAGE of text and its markup
BYTES_PER_PAGE = {'txt': CHARS_PER_PAGE, 'fb2': 2500, 'fbz': 1000}
DEFAULT_BYTES_PER_PAGE = 2000  # MOBI and AZW3: compressed text, and pictures
PDF_BYTES_PER_PAGE = 60000  # without Poppler
MAX_PAGES_PER_MINUTE = 3.0
HEATMAP_DAYS = 365
MONTHS = 12
TOP = 5


@dataclasses.dataclass(frozen=True)
class TimeLeft:
    book: float  # seconds to the end of the book
    chapter: float | None  # seconds to the end of the chapter, when its end was given


@dataclasses.dataclass(frozen=True)
class Goal:
    done: int  # books finished this year
    target: int  # the goal
    expected: float  # where the year's pace would be today
    ahead: int  # books ahead of that pace (negative: behind)

    @property
    def reached(self):
        return self.done >= self.target

    @property
    def fraction(self):
        return min(1.0, self.done / self.target) if self.target else 0.0


@dataclasses.dataclass
class Summary:
    """What the Statistics page shows. Seconds throughout; days are reading days (dates)."""
    today: datetime.date
    days: dict  # {date: seconds}, every day read
    today_seconds: float
    current_streak: int
    longest_streak: int
    finished: list  # [(book_id, when)] finished this year, newest first
    finished_by_month: list  # 12 counts, January to December of this year
    months: list  # [(date, seconds)]: the first of each of the last 12 months, oldest first
    year_seconds: float  # read this year
    year_days: int  # days read this year
    pages: int  # pages read this year (estimated)
    pages_per_hour: float | None  # this year's pace, when there is enough to tell
    authors: list  # [(name, seconds)]: the most read this year, most first
    tags: list  # [(name, seconds)]
    weekdays: list  # 7 seconds, Monday first, over the last year
    hours: list  # 24 seconds, by the hour sessions started in, over the last year
    total_seconds: float  # read ever

    @property
    def empty(self):
        return not self.days and not self.finished


# -- days ---------------------------------------------------------------------------------

def day_of(timestamp):
    """The reading day of a moment: its local date, the day starting at DAY_START_HOUR."""
    moment = datetime.datetime.fromtimestamp(timestamp)
    return (moment - datetime.timedelta(hours=DAY_START_HOUR)).date()


def current_day(now=None):
    """Today's reading day."""
    return day_of(time.time() if now is None else now)


def day_start(date):
    """The Unix time a reading day begins."""
    return datetime.datetime.combine(date, datetime.time(DAY_START_HOUR)).timestamp()


def daily_seconds(library, since=None):
    return _daily(library.sessions(since=since))


def _daily(sessions):
    days = {}
    for session in sessions:
        day = day_of(session.started)
        days[day] = days.get(day, 0.0) + session.seconds
    return days


def current_streak(days, today):
    """Reading days in a row ending today or, while today has too little, yesterday."""
    def counts(day):
        return days.get(day, 0) >= MIN_DAY_SECONDS

    one = datetime.timedelta(days=1)
    day = today if counts(today) else today - one
    count = 0
    while counts(day):
        count += 1
        day -= one
    return count


def longest_streak(days):
    longest = run = 0
    previous = None
    for day in sorted(day for day, seconds in days.items() if seconds >= MIN_DAY_SECONDS):
        run = run + 1 if previous is not None and (day - previous).days == 1 else 1
        longest = max(longest, run)
        previous = day
    return longest


def streak(library, today=None):
    today = today or current_day()
    since = day_start(today - datetime.timedelta(days=400))
    return current_streak(daily_seconds(library, since), today)


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


# -- pages --------------------------------------------------------------------------------

_pages_cache = {}  # (path, size) -> pages or None


def estimated_pages(book_file):
    """A file's page count, estimated (see the module docstring); None when it cannot be
    read. Remembered for the file's path and size."""
    if book_file is None or book_file.missing:
        return None
    key = (book_file.path, book_file.size)
    if key not in _pages_cache:
        try:
            _pages_cache[key] = _estimate(book_file.path, book_file.format, book_file.size)
        except (OSError, zipfile.BadZipFile, ValueError) as error:
            log.debug('no page count for %s: %s', book_file.path, error)
            _pages_cache[key] = None
    return _pages_cache[key]


def _estimate(path, fmt, size):
    if fmt in ('epub', 'kepub'):
        with zipfile.ZipFile(path) as archive:
            text = sum(info.file_size for info in archive.infolist()
                       if re.search(r'\.x?html?$', info.filename, re.I))
        return max(1, round(text / MARKUP_BYTES_PER_PAGE))
    if fmt == 'cbz':
        from .formats import comic

        with zipfile.ZipFile(path) as archive:
            return max(1, len(comic.images(archive.namelist())))
    if fmt == 'pdf':
        from .formats import pdf

        if pdf.AVAILABLE:
            from gi.repository import Gio, GLib, Poppler

            try:
                document = Poppler.Document.new_from_gfile(Gio.File.new_for_path(path),
                                                           None, None)
                return max(1, document.get_n_pages())
            except GLib.Error:
                pass
        return max(1, round(size / PDF_BYTES_PER_PAGE))
    size = size or os.path.getsize(path)
    return max(1, round(size / BYTES_PER_PAGE.get(fmt, DEFAULT_BYTES_PER_PAGE)))


def book_pages(library, book_id, known=None):
    """A book's page count: books.pages when known (`known` is library.page_counts(), for
    a caller asking about many books), else estimated from its file; None when neither."""
    known = library.page_counts() if known is None else known
    if book_id in known:
        return known[book_id]
    return estimated_pages(library.reading_file(book_id))


def session_pages(session, pages):
    """The pages a session moved forward through in a book of `pages`."""
    moved = session.end_fraction - session.start_fraction
    if moved <= 0 or not pages:
        return 0.0
    return min(moved * pages, session.seconds / 60 * MAX_PAGES_PER_MINUTE)


# -- the page's summary -------------------------------------------------------------------

def _month_back(date, months):
    """The first of the month `months` before `date`'s."""
    index = date.year * 12 + date.month - 1 - months
    return datetime.date(index // 12, index % 12 + 1, 1)


def summary(library, today=None):
    """Everything the Statistics page shows; see Summary."""
    today = today or current_day()
    sessions = library.sessions()
    days = _daily(sessions)
    year_start = datetime.date(today.year, 1, 1)
    last_year = today - datetime.timedelta(days=HEATMAP_DAYS - 1)
    first_month = _month_back(today, MONTHS - 1)
    month_seconds = {}
    weekdays, hours = [0.0] * 7, [0.0] * 24
    book_seconds_this_year = {}
    year_seconds = pages_read = paged_seconds = 0.0
    known = library.page_counts()
    page_counts = {}
    for session in sessions:
        day = day_of(session.started)
        if day >= first_month:
            month = day.replace(day=1)
            month_seconds[month] = month_seconds.get(month, 0.0) + session.seconds
        if day >= last_year:
            weekdays[day.weekday()] += session.seconds
            hours[datetime.datetime.fromtimestamp(session.started).hour] += session.seconds
        if day < year_start or day > today:
            continue
        year_seconds += session.seconds
        book_seconds_this_year[session.book_id] = (
            book_seconds_this_year.get(session.book_id, 0.0) + session.seconds)
        if session.book_id not in page_counts:
            page_counts[session.book_id] = book_pages(library, session.book_id, known)
        pages = page_counts[session.book_id]
        if pages:
            pages_read += session_pages(session, pages)
            paged_seconds += session.seconds
    authors, tags = {}, {}
    for book_id, seconds in book_seconds_this_year.items():
        book = library.book(book_id)
        if book is None:
            continue
        for name in book.authors[:1]:
            authors[name] = authors.get(name, 0.0) + seconds
        for name in book.tags:
            tags[name] = tags.get(name, 0.0) + seconds
    year_end = datetime.date(today.year + 1, 1, 1)
    finished = library.finished(since=day_start(year_start), until=day_start(year_end))
    by_month = [0] * 12
    for _book_id, when in finished:
        by_month[day_of(when).month - 1] += 1
    months = [_month_back(today, back) for back in range(MONTHS - 1, -1, -1)]
    return Summary(
        today=today, days=days, today_seconds=days.get(today, 0.0),
        current_streak=current_streak(days, today), longest_streak=longest_streak(days),
        finished=list(reversed(finished)), finished_by_month=by_month,
        months=[(month, month_seconds.get(month, 0.0)) for month in months],
        year_seconds=year_seconds,
        year_days=sum(1 for day, seconds in days.items()
                      if year_start <= day <= today and seconds >= MIN_DAY_SECONDS),
        pages=round(pages_read),
        pages_per_hour=(pages_read / paged_seconds * 3600
                        if paged_seconds >= MIN_SECONDS and pages_read > 0 else None),
        authors=_top(authors), tags=_top(tags), weekdays=weekdays, hours=hours,
        total_seconds=sum(days.values()))


def _top(seconds_by_name):
    return sorted(seconds_by_name.items(), key=lambda item: (-item[1], item[0]))[:TOP]


# -- goals --------------------------------------------------------------------------------

def goal_progress(done, target, today):
    """Where a yearly goal of `target` books stands with `done` finished by `today`: None
    when there is no goal (0). `ahead` counts against the steady pace, rounded down (a
    book due today is not yet late)."""
    if target <= 0:
        return None
    start = datetime.date(today.year, 1, 1)
    length = (datetime.date(today.year + 1, 1, 1) - start).days
    elapsed = (today - start).days + 1
    expected = target * elapsed / length
    return Goal(done=done, target=target, expected=expected,
                ahead=done - math.floor(expected))
