# SPDX-License-Identifier: GPL-2.0-or-later
# SPDX-FileCopyrightText: 2026 Jack Tully

"""Reading time, streaks, speed and time left, from invented sessions."""

import datetime
import unittest

from tests import ROOT  # noqa: F401
from bookcase import stats
from tests.support import add_book, temporary_library


def at(day, hour=20):
    return datetime.datetime.combine(day, datetime.time(hour)).timestamp()


class StatsTest(unittest.TestCase):

    def test_nothing_read(self):
        with temporary_library() as library:
            book_id = add_book(library, 'T')
            self.assertEqual(stats.daily_seconds(library), {})
            self.assertEqual(stats.streak(library), 0)
            self.assertEqual(stats.book_seconds(library, book_id), 0)
            self.assertEqual(stats.book_times(library), {})
            self.assertIsNone(stats.speed(library, book_id))
            self.assertIsNone(stats.time_left(library, book_id, 0.3))

    def test_days_and_streak(self):
        today = datetime.date(2026, 3, 10)
        with temporary_library() as library:
            book_id = add_book(library, 'T')
            other = add_book(library, 'U')
            for back in (0, 1, 2, 4):
                library.log_session(book_id, at(today - datetime.timedelta(days=back)), 600,
                                    0, 0.01)
            library.log_session(other, at(today, 9), 300, 0, 0.1)
            days = stats.daily_seconds(library)
            self.assertEqual(days[today], 900)
            self.assertEqual(len(days), 4)
            self.assertEqual(stats.streak(library, today), 3)
            # Today not read yet: the streak up to yesterday still stands.
            self.assertEqual(stats.streak(library, today + datetime.timedelta(days=1)), 3)
            self.assertEqual(stats.streak(library, today + datetime.timedelta(days=2)), 0)
            self.assertEqual(stats.book_seconds(library, book_id), 2400)
            self.assertEqual(stats.book_times(library), {book_id: 2400, other: 300})
            since = at(today - datetime.timedelta(days=1), 0)
            self.assertEqual(len(stats.daily_seconds(library, since)), 2)

    def test_speed_and_time_left(self):
        with temporary_library() as library:
            book_id = add_book(library, 'T')
            # 0.1 of the book in 600 s, twice; a jump and a step back are left out.
            library.log_session(book_id, 1000, 600, 0.0, 0.1)
            library.log_session(book_id, 2000, 600, 0.1, 0.2)
            library.log_session(book_id, 3000, 30, 0.2, 0.9)
            library.log_session(book_id, 4000, 60, 0.9, 0.5)
            self.assertAlmostEqual(stats.speed(library, book_id), 0.2 / 1200)
            left = stats.time_left(library, book_id, 0.5, chapter_end_fraction=0.55)
            self.assertAlmostEqual(left.book, 3000)
            self.assertAlmostEqual(left.chapter, 300)
            self.assertIsNone(stats.time_left(library, book_id, 0.5).chapter)
            self.assertEqual(stats.time_left(library, book_id, 1.0).book, 0)

    def test_too_little_reading_falls_back_on_the_overall_pace(self):
        with temporary_library() as library:
            read = add_book(library, 'Read')  # add_book's files are 1000 bytes
            new = add_book(library, 'New')
            library.log_session(read, 1000, 1000, 0.0, 0.1)  # 100 bytes in 1000 s
            library.log_session(new, 5000, 10, 0.0, 0.001)
            self.assertIsNone(stats.speed(library, new))
            left = stats.time_left(library, new, 0.5)
            self.assertAlmostEqual(left.book, 5000)  # 500 bytes at 0.1 per second

    def test_a_short_session_alone_gives_nothing(self):
        with temporary_library() as library:
            book_id = add_book(library, 'T')
            library.log_session(book_id, 1000, 60, 0.0, 0.05)
            self.assertIsNone(stats.time_left(library, book_id, 0.05))



class DaysAndStreaksTest(unittest.TestCase):

    def test_a_reading_day_starts_at_four(self):
        day = datetime.date(2026, 3, 10)
        late = datetime.datetime.combine(day + datetime.timedelta(days=1),
                                         datetime.time(1, 30)).timestamp()
        self.assertEqual(stats.day_of(late), day)
        self.assertEqual(stats.day_of(at(day, 4)), day)
        self.assertEqual(stats.day_of(at(day, 3)), day - datetime.timedelta(days=1))
        self.assertEqual(stats.day_of(stats.day_start(day)), day)

    def test_a_session_after_midnight_keeps_the_streak(self):
        today = datetime.date(2026, 3, 10)
        with temporary_library() as library:
            book_id = add_book(library, 'T')
            for back in (1, 2):  # read past midnight, at 1 am, the two nights before
                night = datetime.datetime.combine(today - datetime.timedelta(days=back - 1),
                                                  datetime.time(1))
                library.log_session(book_id, night.timestamp(), 900, 0, 0.01)
            library.log_session(book_id, at(today), 900, 0, 0.01)
            self.assertEqual(stats.streak(library, today), 3)

    def test_a_minute_makes_a_day(self):
        today = datetime.date(2026, 3, 10)
        days = {today: 60, today - datetime.timedelta(days=1): 59,
                today - datetime.timedelta(days=2): 600}
        self.assertEqual(stats.current_streak(days, today), 1)
        self.assertEqual(stats.longest_streak(days), 1)

    def test_longest_streak(self):
        start = datetime.date(2025, 12, 28)
        days = {start + datetime.timedelta(days=n): 600 for n in (0, 1, 2, 3, 4, 6, 7)}
        self.assertEqual(stats.longest_streak(days), 5)  # across the new year
        self.assertEqual(stats.current_streak(days, start + datetime.timedelta(days=8)), 2)
        self.assertEqual(stats.longest_streak({}), 0)
        self.assertEqual(stats.current_streak({}, start), 0)


class GoalsTest(unittest.TestCase):

    def test_no_goal(self):
        self.assertIsNone(stats.goal_progress(3, 0, datetime.date(2026, 6, 1)))

    def test_ahead_on_and_behind_schedule(self):
        today = datetime.date(2026, 10, 8)  # day 281 of 365: 24 books would be 18.5
        self.assertEqual(stats.goal_progress(20, 24, today).ahead, 2)
        self.assertEqual(stats.goal_progress(18, 24, today).ahead, 0)
        self.assertEqual(stats.goal_progress(13, 24, today).ahead, -5)
        goal = stats.goal_progress(13, 15, today)
        self.assertEqual(goal.ahead, 2)
        self.assertFalse(goal.reached)
        self.assertAlmostEqual(goal.fraction, 13 / 15)

    def test_a_new_year_is_on_schedule(self):
        goal = stats.goal_progress(0, 24, datetime.date(2026, 1, 1))
        self.assertEqual(goal.ahead, 0)
        self.assertEqual(goal.fraction, 0)

    def test_reached(self):
        goal = stats.goal_progress(26, 24, datetime.date(2026, 12, 31))
        self.assertTrue(goal.reached)
        self.assertEqual(goal.fraction, 1.0)


class FinishedTest(unittest.TestCase):

    def test_marking_finished_records_when(self):
        with temporary_library() as library:
            book_id = add_book(library, 'T')
            self.assertEqual(library.finished(), [])
            library.set_status([book_id], 'finished')
            [(found, when)] = library.finished()
            self.assertEqual(found, book_id)
            self.assertGreater(when, 0)
            # Marked finished again: the same date. Read again: it keeps its date.
            library.set_status([book_id], 'finished')
            library.set_status([book_id], 'reading')
            self.assertEqual(library.finished(), [(book_id, when)])
            # Finished a second time: the new date.
            library.db.execute('UPDATE books SET finished = 1000 WHERE id = ?', (book_id,))
            library.set_status([book_id], 'finished')
            [(_found, again)] = library.finished()
            self.assertGreater(again, 1000)
            library.undo()
            self.assertEqual(library.finished(), [(book_id, 1000)])
            # Unread: forgotten; undo puts it back.
            library.set_status([book_id], 'unread')
            self.assertEqual(library.finished(), [])
            library.undo()
            self.assertEqual(library.finished(), [(book_id, 1000)])

    def test_finished_per_year_and_month(self):
        today = datetime.date(2026, 10, 8)
        with temporary_library() as library:
            dates = {'A': datetime.date(2025, 12, 30), 'B': datetime.date(2026, 1, 2),
                     'C': datetime.date(2026, 3, 5), 'D': datetime.date(2026, 3, 20)}
            ids = {}
            for title, day in dates.items():
                ids[title] = add_book(library, title)
                library.set_status([ids[title]], 'finished')
                library.db.execute('UPDATE books SET finished = ? WHERE id = ?',
                                   (at(day), ids[title]))
            summary = stats.summary(library, today)
            self.assertEqual([book_id for book_id, _when in summary.finished],
                             [ids['D'], ids['C'], ids['B']])
            self.assertEqual(summary.finished_by_month[:4], [1, 0, 2, 0])
            self.assertEqual(sum(summary.finished_by_month), 3)

    def test_migration_backfills_from_the_last_session(self):
        import sqlite3

        from bookcase import schema

        with temporary_library() as library:
            book_id = add_book(library, 'T')
            other = add_book(library, 'U')
            library.set_status([book_id, other], 'finished')
            library.log_session(book_id, 5000, 600, 0.9, 1.0)
            path = library.path
            library.db.execute('UPDATE books SET last_read = 7777')
            # Back to a version 1 file: no finished, pages or source_values column.
            library.db.executescript(
                'ALTER TABLE books DROP COLUMN finished; ALTER TABLE books DROP COLUMN pages;'
                'ALTER TABLE books DROP COLUMN source_values; PRAGMA user_version = 1;')
            db = sqlite3.connect(path)
            schema.apply(db)
            rows = dict(db.execute('SELECT id, finished FROM books').fetchall())
            db.close()
            self.assertEqual(rows, {book_id: 5600, other: 7777})


class SummaryTest(unittest.TestCase):

    def test_empty(self):
        with temporary_library() as library:
            add_book(library, 'T')
            summary = stats.summary(library, datetime.date(2026, 10, 8))
            self.assertTrue(summary.empty)
            self.assertEqual(summary.current_streak, 0)
            self.assertEqual(summary.pages, 0)
            self.assertIsNone(summary.pages_per_hour)
            self.assertEqual(len(summary.months), 12)
            self.assertEqual(summary.authors, [])

    def test_a_year_of_reading(self):
        today = datetime.date(2026, 10, 8)
        with temporary_library() as library:
            sea = add_book(library, 'Sea', ('Ada Lark',), tags=['Fantasy'])
            hill = add_book(library, 'Hill', ('Ben Ross', 'Ada Lark'), tags=['Mystery'])
            library.db.execute('UPDATE books SET pages = 300 WHERE id = ?', (sea,))
            # A tenth of Sea (30 pages) in an hour on a Monday evening, last year's December
            # and this year's.
            library.log_session(sea, at(datetime.date(2026, 10, 5), 20), 3600, 0.1, 0.2)
            library.log_session(sea, at(datetime.date(2025, 12, 1), 20), 3600, 0.0, 0.1)
            # Hill has no page count and no file: time, but no pages.
            library.log_session(hill, at(today, 8), 1800, 0.0, 0.5)
            summary = stats.summary(library, today)
            self.assertFalse(summary.empty)
            self.assertEqual(summary.today_seconds, 1800)
            self.assertEqual(summary.year_seconds, 5400)
            self.assertEqual(summary.year_days, 2)
            self.assertEqual(summary.pages, 30)
            self.assertAlmostEqual(summary.pages_per_hour, 30)
            self.assertEqual(summary.authors, [('Ada Lark', 3600), ('Ben Ross', 1800)])
            self.assertEqual(summary.tags, [('Fantasy', 3600), ('Mystery', 1800)])
            self.assertEqual(summary.weekdays[0], 7200)  # 5 October and 1 December: Mondays
            self.assertEqual(summary.hours[20], 7200)
            self.assertEqual(summary.hours[8], 1800)
            self.assertEqual(summary.months[0], (datetime.date(2025, 11, 1), 0))
            self.assertEqual(summary.months[1], (datetime.date(2025, 12, 1), 3600))
            self.assertEqual(summary.months[-1], (datetime.date(2026, 10, 1), 5400))
            self.assertEqual(summary.total_seconds, 9000)

    def test_a_skim_is_not_reading(self):
        from bookcase.library import Session

        skim = Session(1, 1, 0, 60, 0.0, 0.9)  # 90% of a 300-page book in a minute
        self.assertEqual(stats.session_pages(skim, 300), stats.MAX_PAGES_PER_MINUTE)
        back = Session(2, 1, 0, 600, 0.5, 0.4)
        self.assertEqual(stats.session_pages(back, 300), 0)


class PagesTest(unittest.TestCase):

    def test_estimates(self):
        import tempfile
        import zipfile

        from bookcase.library import BookFile

        with tempfile.TemporaryDirectory() as directory:
            epub = f'{directory}/book.epub'
            with zipfile.ZipFile(epub, 'w') as archive:
                archive.writestr('OEBPS/one.xhtml', 'x' * 20000)
                archive.writestr('OEBPS/two.html', 'x' * 20000)
                archive.writestr('OEBPS/cover.jpg', b'0' * 50000)
            found = BookFile(1, 1, epub, 'epub', 1000, '')
            self.assertEqual(stats.estimated_pages(found), 20)
            cbz = f'{directory}/comic.cbz'
            with zipfile.ZipFile(cbz, 'w') as archive:
                for n in range(12):
                    archive.writestr(f'{n:02}.jpg', b'0')
            self.assertEqual(stats.estimated_pages(BookFile(2, 2, cbz, 'cbz', 1, '')), 12)
            self.assertEqual(stats.estimated_pages(
                BookFile(3, 3, f'{directory}/t.txt', 'txt', 15000, '')), 10)
            self.assertIsNone(stats.estimated_pages(
                BookFile(4, 4, f'{directory}/gone.epub', 'epub', 10, '')))
            self.assertIsNone(stats.estimated_pages(None))


if __name__ == '__main__':
    unittest.main()
