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


if __name__ == '__main__':
    unittest.main()
