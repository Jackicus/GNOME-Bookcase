# SPDX-License-Identifier: GPL-2.0-or-later
# SPDX-FileCopyrightText: 2026 Jack Tully

"""The Statistics page, the home page's goal card and the goals dialog over a temporary
library: the empty state, a year of invented reading, the goal's wording, the charts'
descriptions, a finished book's cover opening its details."""

import datetime
import time
import unittest

from tests import ROOT  # noqa: F401  (registers bookcase)
from tests.gtk import pump, requires_gtk, wait_for
from tests.support import add_book, temporary_library

SCHEMA_ID = 'io.github.jackicus.Bookcase'
_stand_ins = {}


class Covers:
    """A cover store with no covers."""

    def load_thumbnail(self, _book, _width, callback):
        callback(None)


def stand_ins():
    """A stand-in Application (one per process, its own ID: test_pages has another) and
    Window class."""
    if _stand_ins:
        return _stand_ins
    from gi.repository import Adw, Gio

    class App(Adw.Application):
        def __init__(self):
            super().__init__(application_id='io.github.jackicus.Bookcase.StatsTest',
                             flags=Gio.ApplicationFlags.NON_UNIQUE)
            self.settings = Gio.Settings.new(SCHEMA_ID)
            self.library = None
            self.covers = None

    class Window(Adw.Window):
        def __init__(self):
            super().__init__(default_width=900, default_height=700)
            self.navigation_view = Adw.NavigationView()
            self.set_content(self.navigation_view)
            self.roots, self.books, self.lists = [], [], []

        def show_root(self, key):
            self.roots.append(key)

        def show_book(self, book_id):
            self.books.append(book_id)

        def show_books(self, title, **filters):
            self.lists.append((title, filters))

        def set_dialog_open(self, _open):
            pass

        def push(self, page):
            self.navigation_view.push(page)

    app = App()
    app.register(None)
    _stand_ins.update(app=app, Window=Window)
    return _stand_ins


def at(day, hour=20):
    return datetime.datetime.combine(day, datetime.time(hour)).timestamp()


@requires_gtk
class StatsPageTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        found = stand_ins()
        cls.app = found['app']
        cls.window = found['Window']()
        cls.window.present()
        deadline = time.monotonic() + 2
        while not cls.window.get_mapped() and time.monotonic() < deadline:
            pump(20)

    @classmethod
    def tearDownClass(cls):
        cls.window.destroy()
        pump()

    def setUp(self):
        self.app.set_default()
        for key in ('goal-books', 'goal-minutes'):
            self.app.settings.reset(key)
        self.window.roots, self.window.books, self.window.lists = [], [], []
        context = temporary_library()
        self.library = context.__enter__()
        self.addCleanup(context.__exit__, None, None, None)
        self.app.library = self.library
        self.app.covers = Covers()

    def tearDown(self):
        from gi.repository import Adw

        self.window.navigation_view.replace([Adw.NavigationPage(title='Blank')])
        for key in ('goal-books', 'goal-minutes'):
            self.app.settings.reset(key)
        pump()

    def show(self, page):
        self.window.navigation_view.replace([page])
        self.assertTrue(wait_for(page.get_mapped))
        pump()
        return page

    def read(self):
        """A book finished yesterday after a few evenings, another being read today."""
        today = datetime.date.today()
        finished = add_book(self.library, 'Lantern Hill', ('Ben Ross',), tags=['Mystery'])
        reading = add_book(self.library, 'A Quiet Harbour', ('Ada Lark',))
        for back in range(1, 4):
            self.library.log_session(finished, at(today - datetime.timedelta(days=back)),
                                     1800, (3 - back) / 3, (4 - back) / 3)
        self.library.set_status([finished], 'finished')
        self.library.log_session(reading, at(today, 12), 1200, 0, 0.1)
        return finished, reading

    def test_empty(self):
        from bookcase.pages.stats import StatsPage

        add_book(self.library, 'Unread')
        page = self.show(StatsPage())
        self.assertEqual(page.stack.get_visible_child_name(), 'empty')
        # A goal alone is something to show.
        self.app.settings.set_int('goal-books', 12)
        pump()
        self.assertEqual(page.stack.get_visible_child_name(), 'stats')
        self.assertIn('0 of 12', page.goal_title.get_text())

    def test_a_year_of_reading(self):
        from bookcase.pages.stats import StatsPage

        started = time.monotonic()
        finished, _reading = self.read()
        self.app.settings.set_int('goal-books', 24)
        self.app.settings.set_int('goal-minutes', 30)
        page = self.show(StatsPage())
        self.assertEqual(page.stack.get_visible_child_name(), 'stats')
        self.assertEqual(page.goal_title.get_text(), '1 of 24 books')
        self.assertTrue(page.goal_status.get_text())
        self.assertFalse(page.goal_button.get_visible())
        self.assertEqual(page.tile_streak.value.get_text(), '4 days')
        self.assertEqual(page.tile_today.value.get_text(), '20 min')
        self.assertTrue(page.tile_today.bar.get_visible())
        for chart in (page.ring, page.heatmap, page.months_chart, page.weekdays_chart,
                      page.hours_chart):
            self.assertTrue(chart.description, chart)
        # The finished book's cover opens its details; its author opens the author's books.
        button = page.finished_box.get_first_child()
        button.emit('clicked')
        self.assertEqual(self.window.books, [finished])
        rows = []
        child = page.authors_list.get_first_child()
        while child is not None:
            rows.append(child)
            child = child.get_next_sibling()
        authors = [row for row in rows if getattr(row, 'group_id', None) is not None]
        self.assertEqual([row.get_title() for row in authors], ['Ben Ross', 'Ada Lark'])
        page.authors_list.emit('row-activated', authors[0])
        self.assertEqual(self.window.lists[0][0], 'Ben Ross')
        self.assertIn('author', self.window.lists[0][1])
        self.assertLess(time.monotonic() - started, 1.0)

    def test_without_a_goal(self):
        from bookcase.pages.stats import StatsPage

        self.read()
        page = self.show(StatsPage())
        self.assertIn('1 book finished', page.goal_title.get_text())
        self.assertTrue(page.goal_button.get_visible())
        self.assertFalse(page.tile_today.bar.get_visible())

    def test_goal_card(self):
        from bookcase.widgets.goal_card import GoalCard

        card = GoalCard()
        self.assertFalse(card.get_visible())
        self.read()
        self.app.settings.set_int('goal-books', 24)
        pump()
        self.assertTrue(card.get_visible())
        self.assertEqual(card.title.get_text(), '1 of 24 books this year')
        self.assertIn('4-day streak', card.subtitle.get_text())

    def test_goals_dialog(self):
        from bookcase.dialogs import goals

        dialog = goals.present(self.app, self.window)
        pump()
        dialog.books_row.set_value(30)
        dialog.minutes_row.set_value(20)
        dialog.emit('response', 'save')
        self.assertEqual(self.app.settings.get_int('goal-books'), 30)
        self.assertEqual(self.app.settings.get_int('goal-minutes'), 20)
        dialog.force_close()
        pump()

    def test_a_past_year_and_its_review(self):
        import os
        import tempfile

        from bookcase import stats
        from bookcase.pages.stats import StatsPage
        from bookcase.pages.year_review import YearReviewPage

        last = datetime.date.today().year - 1
        book = add_book(self.library, 'Lantern Hill', ('Ben Ross',), tags=['Mystery'])
        self.library.update_book(book, pages=240)
        for day in (3, 4):
            self.library.log_session(book, at(datetime.date(last, 6, day)), 3600, 0, 0.4)
        self.library.set_status([book], 'finished')
        self.library.db.execute('UPDATE books SET finished = ? WHERE id = ?',
                                (at(datetime.date(last, 6, 5)), book))
        page = self.show(StatsPage())
        self.assertTrue(page.year_dropdown.get_visible())
        self.assertEqual(page.years, [last + 1, last])
        self.assertFalse(page.review_button.get_visible())
        page.year_dropdown.set_selected(1)
        self.assertEqual(page.year, last)
        self.assertTrue(page.review_button.get_visible())
        self.assertFalse(page.tile_today.get_visible())
        self.assertEqual(page.tile_streak.value.get_text(), '2 days')
        self.assertEqual(page.goal_title.get_text(), f'1 book finished in {last}')
        review = page.show_review()
        self.assertIsInstance(review, YearReviewPage)
        self.assertTrue(wait_for(review.get_mapped))
        pump()
        self.assertEqual(review.tile_books.value.get_text(), '1')
        self.assertEqual(review.tile_pages.value.get_text(), '240')
        self.assertEqual(review.tile_hours.value.get_text(), '2 h')
        values = [row.value.get_text() for row in _rows(review.facts)]
        self.assertIn('Ben Ross', values)
        self.assertIn('Mystery', values)
        with tempfile.TemporaryDirectory() as directory:
            path = os.path.join(directory, 'review.png')
            self.assertTrue(review.save_image(path, scale=1))
            with open(path, 'rb') as file:
                self.assertEqual(file.read(8), b'\x89PNG\r\n\x1a\n')
        page.set_year(None)
        self.assertIsNone(page.year)
        self.assertEqual(page.year_dropdown.get_selected(), 0)
        self.assertEqual(stats.years(self.library), [last + 1, last])

    def test_the_day_turning_over(self):
        from bookcase.pages.stats import StatsPage

        page = self.show(StatsPage())
        self.assertIsNotNone(page._day_timer)
        from gi.repository import Adw

        self.window.navigation_view.replace([Adw.NavigationPage(title='Blank')])
        pump()
        self.assertIsNone(page._day_timer)  # no timer while the page is not shown


def _rows(listbox):
    child = listbox.get_first_child()
    while child is not None:
        yield child
        child = child.get_next_sibling()


@requires_gtk
class ScheduleTextTest(unittest.TestCase):

    def test_kind_words(self):
        from bookcase import stats
        from bookcase.pages.stats import schedule_text

        today = datetime.date(2026, 10, 8)
        self.assertEqual(schedule_text(stats.goal_progress(20, 24, today), today),
                         '2 books ahead of schedule')
        self.assertEqual(schedule_text(stats.goal_progress(18, 24, today), today),
                         'Right on schedule')
        self.assertEqual(schedule_text(stats.goal_progress(13, 24, today), today),
                         '11 books to go in 12 weeks')
        self.assertEqual(schedule_text(stats.goal_progress(25, 24, today), today),
                         'Goal reached, and 1 more book')


if __name__ == '__main__':
    unittest.main()
