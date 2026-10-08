# SPDX-License-Identifier: GPL-2.0-or-later
# SPDX-FileCopyrightText: 2026 Jack Tully

"""The Statistics page: reading goals and what stats.summary() finds, as charts.

    page = StatsPage(library=None, settings=None)   # the app's unless given (tests)
    page.refresh()                     # query the library and redraw (also on map, on the
                                       # library's `changed` and on a goal setting's change)
    page.summary                       # the stats.Summary of the last refresh
    page.set_year(2025)                # a past year (None: this year), as the drop-down does
    page.show_review()                 # push the shown past year's Year in Review

From the top: this year's goal as a ring (books finished of `goal-books`; with no goal, the
books finished so far) with where the year's pace stands, and the covers of the books
finished this year (a click opens a book's details); tiles for today's reading (against
`goal-minutes`), the streak, the year's hours, pages and pace; a heatmap of the last year's
reading days; hours per month over the last twelve; the days of the week and the hours of
the day reading happens in; the most read authors and tags this year (a click shows their
books). The goals are edited in dialogs/goals.py (the pencil in the header bar). With no
reading at all, a status page.

The year drop-down (shown when there are years before this one with reading or books
finished, stats.years) shows a past year as it ended: its books, hours and pages, its
reading days, months, days of the week and favourites, without today's tile or the goal;
a card at the top opens its Year in Review (pages/year_review.py). While the page is shown,
a timeout set for the next reading day's start (stats.next_day_start: 4 in the morning)
refreshes it, so "today" and the streak move on with the clock.

Nothing here scolds: being behind the year's pace reads as what is left and the time to do
it in, never as a shortfall.
"""

import datetime
import logging
import math
import time
from gettext import gettext as _
from gettext import ngettext

from gi.repository import Adw, GLib, Gtk, Pango

from .. import stats
from ..widgets import charts
from ..widgets.charts import BarChart, Heatmap, Ring  # noqa: F401 (the template's types)
from ..widgets.cover import Cover
from ..widgets.util import connect_weak
from . import PageListener, app

log = logging.getLogger(__name__)

CHANGE_KINDS = ('books', 'progress')
COVER_WIDTH = 64
COVER_WIDTH_NARROW = 44
FINISHED_SHOWN = 30  # covers of the books finished this year, the latest first
MONDAY = datetime.date(2024, 1, 1)


def hours_text(seconds):
    """A total reading time: '45 min', '3.5 h', '120 h'."""
    minutes = seconds / 60
    if minutes < 59.5:
        # Translators: a reading time in minutes ("45 min").
        return _('{m} min').format(m=round(minutes))
    hours = minutes / 60
    if hours < 10:
        # Translators: a reading time in hours, with one decimal ("3.5 h").
        return _('{h} h').format(h=f'{hours:.1f}'.removesuffix('.0'))
    return _('{h} h').format(h=f'{round(hours):,}')


def schedule_text(goal, today):
    """Where a yearly goal stands, kindly: ahead, on schedule, or what is left and the time
    there is for it."""
    if goal.reached:
        extra = goal.done - goal.target
        if extra:
            return ngettext('Goal reached, and {n} more book', 'Goal reached, and {n} more books',
                            extra).format(n=extra)
        return _('Goal reached')
    if goal.ahead > 0:
        return ngettext('{n} book ahead of schedule', '{n} books ahead of schedule',
                        goal.ahead).format(n=goal.ahead)
    if goal.ahead == 0:
        return _('Right on schedule')
    left = goal.target - goal.done
    weeks = max(1, (datetime.date(today.year, 12, 31) - today).days // 7)
    # Translators: a yearly goal still to reach: "{books} to go in {weeks}" makes "3 books
    # to go in 12 weeks".
    return _('{books} to go in {weeks}').format(
        books=ngettext('{n} book', '{n} books', left).format(n=left),
        weeks=ngettext('{n} week', '{n} weeks', weeks).format(n=weeks))


def streak_text(days):
    return ngettext('{n} day', '{n} days', days).format(n=days)


class Tile(Gtk.Box):
    """A number with a caption under it, in a card."""

    def __init__(self, caption):
        super().__init__(orientation=Gtk.Orientation.VERTICAL, spacing=3,
                         accessible_role=Gtk.AccessibleRole.GROUP)
        self.add_css_class('card')
        self.add_css_class('stats-tile')
        self.value = Gtk.Label(xalign=0)
        self.value.add_css_class('title-2')
        self.value.add_css_class('numeric')
        self.caption = Gtk.Label(label=caption, xalign=0, wrap=True,
                                 wrap_mode=Pango.WrapMode.WORD_CHAR)
        self.caption.add_css_class('caption')
        self.caption.add_css_class('dimmed')
        self.bar = Gtk.ProgressBar(visible=False, margin_top=6,
                                   accessible_role=Gtk.AccessibleRole.PRESENTATION)
        self.bar.add_css_class('stats-tile-bar')
        self.append(self.value)
        self.append(self.caption)
        self.append(self.bar)

    def set(self, value, caption=None, fraction=None):
        self.value.set_text(value)
        if caption is not None:
            self.caption.set_text(caption)
        self.bar.set_visible(fraction is not None)
        if fraction is not None:
            self.bar.set_fraction(max(0.0, min(1.0, fraction)))
        self.update_property([Gtk.AccessibleProperty.LABEL],
                             [f'{self.caption.get_text()}: {value}'])


@Gtk.Template(resource_path='/io/github/jackicus/Bookcase/stats.ui')
class StatsPage(Adw.NavigationPage):
    __gtype_name__ = 'BookcaseStatsPage'

    narrow = Gtk.Template.Child()
    year_dropdown = Gtk.Template.Child()
    review_button = Gtk.Template.Child()
    review_title = Gtk.Template.Child()
    stack = Gtk.Template.Child()
    scroller = Gtk.Template.Child()
    ring = Gtk.Template.Child()
    goal_title = Gtk.Template.Child()
    goal_status = Gtk.Template.Child()
    goal_caption = Gtk.Template.Child()
    goal_button = Gtk.Template.Child()
    finished_section = Gtk.Template.Child()
    finished_label = Gtk.Template.Child()
    finished_box = Gtk.Template.Child()
    tiles = Gtk.Template.Child()
    heatmap_scroller = Gtk.Template.Child()
    heatmap = Gtk.Template.Child()
    heatmap_caption = Gtk.Template.Child()
    months_chart = Gtk.Template.Child()
    months_caption = Gtk.Template.Child()
    weekdays_chart = Gtk.Template.Child()
    hours_chart = Gtk.Template.Child()
    when_caption = Gtk.Template.Child()
    most_section = Gtk.Template.Child()
    most_heading = Gtk.Template.Child()
    authors_list = Gtk.Template.Child()
    tags_list = Gtk.Template.Child()

    def __init__(self, library=None, settings=None):
        super().__init__()
        self.library = library if library is not None else app().library
        self.settings = settings if settings is not None else app().settings
        self.summary = None
        self.year = None  # a past year shown, or None for this one
        self.years = []
        self._day_timer = None
        self._narrow = False
        self._covers = []
        self.tile_today = Tile(_('Today'))
        self.tile_streak = Tile(_('Day Streak'))
        self.tile_year = Tile('')
        self.tile_pages = Tile(_('Pages Read'))
        for tile in (self.tile_today, self.tile_streak, self.tile_year, self.tile_pages):
            self.tiles.append(tile)
        for key in ('goal-books', 'goal-minutes'):
            connect_weak(self.settings, f'changed::{key}', self._on_goal_changed)
        connect_weak(self.narrow, 'apply', self._on_narrow_apply)
        connect_weak(self.narrow, 'unapply', self._on_narrow_unapply)
        connect_weak(self.heatmap_scroller.get_hadjustment(), 'changed',
                     self._on_heatmap_adjustment)
        connect_weak(self.year_dropdown, 'notify::selected', self._on_year_selected)
        connect_weak(self, 'map', self._on_map)
        connect_weak(self, 'unmap', self._on_unmap)
        self.listener = PageListener(self, CHANGE_KINDS, StatsPage.refresh)

    # -- the day turning over ----------------------------------------------------------------

    def _on_map(self, *_args):
        self._schedule_day()

    def _on_unmap(self, *_args):
        if self._day_timer is not None:
            GLib.source_remove(self._day_timer)
            self._day_timer = None

    def _schedule_day(self):
        self._on_unmap()
        seconds = max(1, math.ceil(stats.next_day_start() - time.time()) + 1)
        ref = self.weak_ref()

        def turned():
            page = ref()
            if page is not None:
                page._day_timer = None
                if page.get_mapped():
                    page.refresh()
                    page._schedule_day()
            return GLib.SOURCE_REMOVE

        self._day_timer = GLib.timeout_add_seconds(seconds, turned)

    # -- the year shown ----------------------------------------------------------------------

    def set_year(self, year):
        """Show a past year (None, or this year: the current one)."""
        today = stats.current_day()
        year = None if year is None or year >= today.year else year
        if year == self.year:
            return
        self.year = year
        if self.years and (year or today.year) in self.years:
            index = self.years.index(year or today.year)
            if self.year_dropdown.get_selected() != index:
                self.year_dropdown.set_selected(index)
        self.refresh()

    def _on_year_selected(self, dropdown, _pspec):
        index = dropdown.get_selected()
        if self._filling_years or not 0 <= index < len(self.years):
            return
        self.set_year(self.years[index])

    _filling_years = False

    def _fill_years(self, today):
        years = stats.years(self.library, today)
        if self.year is not None and self.year not in years:
            self.year = None
        if years != self.years:
            self.years = years
            self._filling_years = True
            try:
                self.year_dropdown.set_model(Gtk.StringList.new([str(year) for year in years]))
                self.year_dropdown.set_selected(years.index(self.year or today.year))
            finally:
                self._filling_years = False
        self.year_dropdown.set_visible(len(years) > 1)

    def show_review(self):
        """Push the Year in Review of the past year shown."""
        from .year_review import YearReviewPage

        window = self.get_root()
        if self.year is None or window is None or not hasattr(window, 'push'):
            return None
        page = YearReviewPage(self.year, library=self.library)
        window.push(page)
        return page

    @Gtk.Template.Callback()
    def _on_review_clicked(self, _button):
        self.show_review()

    def _on_goal_changed(self, *_args):
        if self.get_mapped():
            self.refresh()

    def _on_narrow_apply(self, _breakpoint):
        self._narrow = True
        for cover in self._covers:
            cover.props.width = COVER_WIDTH_NARROW

    def _on_narrow_unapply(self, _breakpoint):
        self._narrow = False
        for cover in self._covers:
            cover.props.width = COVER_WIDTH

    def _on_heatmap_adjustment(self, adjustment):
        # The latest weeks are the ones to see when the year is wider than the window: from
        # an idle, once the scrolled window has taken its new size.
        GLib.idle_add(_scroll_to_end, adjustment)

    # -- content -----------------------------------------------------------------------------

    def refresh(self):
        today = stats.current_day()
        self._fill_years(today)
        past = self.year is not None
        # A past year is shown as it stood on its last day.
        day = datetime.date(self.year, 12, 31) if past else today
        summary = stats.summary(self.library, day)
        self.summary = summary
        target = 0 if past else self.settings.get_int('goal-books')
        if summary.empty and not target and not past:
            self.stack.set_visible_child_name('empty')
            return
        self.stack.set_visible_child_name('stats')
        self.review_button.set_visible(past)
        if past:
            self.review_title.set_text(_('Your {year} in Review').format(year=self.year))
        self._show_goal(summary, target, day)
        self._show_finished(summary, day)
        self._show_tiles(summary, day)
        self._show_heatmap(summary, day)
        self._show_months(summary, day)
        self._show_when(summary)
        self._show_most(summary, day)

    def _show_goal(self, summary, target, today):
        done = len(summary.finished)
        goal = stats.goal_progress(done, target, today)
        year = str(today.year)
        if goal is None:
            self.ring.set_data(1.0 if done else 0.0, str(done),
                               ngettext('book', 'books', done))
            self.goal_title.set_text(
                ngettext('{n} book finished in {year}', '{n} books finished in {year}',
                         done).format(n=done, year=year))
            past = self.year is not None
            self.goal_status.set_text(_('Your reading in {year}').format(year=year) if past
                                      else _('Set a goal to see how the year is going.'))
            self.goal_caption.set_visible(False)
            self.goal_button.set_visible(not past)
            self.ring.set_description(self.goal_title.get_text())
            return
        self.ring.set_data(goal.fraction, str(done),
                           ngettext('of {n} book', 'of {n} books', target).format(n=target))
        self.goal_title.set_text(
            # Translators: the yearly goal: "13 of 24 books".
            ngettext('{done} of {n} book', '{done} of {n} books', target).format(
                done=done, n=target))
        self.goal_status.set_text(schedule_text(goal, today))
        self.goal_caption.set_text(_('Your reading goal for {year}').format(year=year))
        self.goal_caption.set_visible(True)
        self.goal_button.set_visible(False)
        self.ring.set_description(
            f'{self.goal_caption.get_text()}: {self.goal_title.get_text()}. '
            f'{self.goal_status.get_text()}')

    def _show_finished(self, summary, today):
        while (child := self.finished_box.get_first_child()) is not None:
            self.finished_box.remove(child)
        self._covers = []
        width = COVER_WIDTH_NARROW if self._narrow else COVER_WIDTH
        for book_id, _when in summary.finished[:FINISHED_SHOWN]:
            book = self.library.book(book_id)
            if book is None:
                continue
            cover = Cover(width=width)
            cover.set_book(book)
            self._covers.append(cover)
            button = Gtk.Button(child=cover, tooltip_text=book.title)
            button.add_css_class('flat')
            button.add_css_class('stats-cover')
            button.book_id = book.id
            button.update_property([Gtk.AccessibleProperty.LABEL],
                                   [f'{book.title}, {book.author}'])
            connect_weak(button, 'clicked', self._on_cover_clicked)
            self.finished_box.append(button)
        self.finished_section.set_visible(bool(self._covers))
        self.finished_label.set_text(_('Finished in {year}').format(year=today.year))

    def _show_tiles(self, summary, today):
        past = self.year is not None
        self.tile_today.set_visible(not past)
        if past:
            self.tile_streak.set(streak_text(summary.longest_streak), _('Longest streak'))
        minutes_goal = self.settings.get_int('goal-minutes')
        today_minutes = round(summary.today_seconds / 60)
        if minutes_goal:
            if today_minutes >= minutes_goal:
                caption = _('Today: daily goal met')
            else:
                caption = ngettext('Today, of {n} minute', 'Today, of {n} minutes',
                                   minutes_goal).format(n=minutes_goal)
            self.tile_today.set(hours_text(summary.today_seconds) if today_minutes
                                else _('{m} min').format(m=0), caption,
                                today_minutes / minutes_goal)
        else:
            self.tile_today.set(hours_text(summary.today_seconds) if today_minutes
                                else _('{m} min').format(m=0), _('Today'))
        longest = summary.longest_streak
        if longest > summary.current_streak:
            caption = _('Day streak · best {days}').format(days=streak_text(longest))
        else:
            caption = _('Day streak')
        if not past:
            self.tile_streak.set(streak_text(summary.current_streak), caption)
        self.tile_year.set(hours_text(summary.year_seconds),
                           _('Read in {year}').format(year=today.year))
        if summary.pages_per_hour is not None:
            # Translators: pages read this year (estimated), and the pace.
            caption = _('Pages read · {n} an hour').format(
                n=f'{round(summary.pages_per_hour):,}')
        else:
            caption = _('Pages read')
        self.tile_pages.set(f'{summary.pages:,}', caption)

    def _show_heatmap(self, summary, today):
        minutes = {day: seconds / 60 for day, seconds in summary.days.items()}
        self.heatmap.set_data(minutes, today)
        first = today - datetime.timedelta(days=charts.Heatmap.DAYS - 1)
        read = [day for day, seconds in summary.days.items()
                if first <= day <= today and seconds >= stats.MIN_DAY_SECONDS]
        if self.year is not None:
            caption = ngettext('Read on {n} day in {year}', 'Read on {n} days in {year}',
                               len(read)).format(n=len(read), year=self.year)
        else:
            caption = ngettext('Read on {n} day in the last year',
                               'Read on {n} days in the last year', len(read)).format(n=len(read))
        if summary.longest_streak > 1:
            caption += ' · ' + _('longest streak {days}').format(
                days=streak_text(summary.longest_streak))
        self.heatmap_caption.set_text(caption)
        self.heatmap.set_description(_('A calendar of the last year, each day shaded by '
                                       'the time read. {caption}').format(caption=caption))

    def _show_months(self, summary, today):
        values = [seconds / 3600 for _month, seconds in summary.months]
        labels = [charts.date_label(month, '%b')[:1] if len(values) > 6 and self._narrow
                  else charts.date_label(month, '%b') for month, _seconds in summary.months]
        names = [charts.date_label(month, '%B %Y') for month, _seconds in summary.months]
        tooltips = [f'{name}: {hours_text(seconds)}'
                    for name, (_month, seconds) in zip(names, summary.months, strict=True)]
        self.months_chart.set_data(values, labels=labels, tooltips=tooltips,
                                   highlight=None if self.year is not None else len(values) - 1)
        best = max(summary.months, key=lambda item: item[1])
        if best[1] > 0:
            caption = _('Most in {month}: {time}').format(
                month=charts.date_label(best[0], '%B'), time=hours_text(best[1]))
        else:
            caption = _('Nothing read in the last twelve months')
        self.months_caption.set_text(caption)
        self.months_chart.set_description(
            _('Hours read in each of the last twelve months: {list}').format(
                list=', '.join(tooltips)))

    def _show_when(self, summary):
        names = [weekday_name(day) for day in range(7)]
        self.weekdays_chart.set_data(
            [seconds / 3600 for seconds in summary.weekdays],
            labels=[weekday_name(day, '%a') for day in range(7)],
            tooltips=[f'{names[day]}: {hours_text(seconds)}'
                      for day, seconds in enumerate(summary.weekdays)])
        self.weekdays_chart.set_description(
            _('Hours read on each day of the week: {list}').format(
                list=', '.join(f'{names[day]} {hours_text(seconds)}'
                               for day, seconds in enumerate(summary.weekdays))))
        hour_labels = [f'{hour}' if hour % 6 == 0 else None for hour in range(24)]
        self.hours_chart.set_data(
            [seconds / 3600 for seconds in summary.hours], labels=hour_labels,
            tooltips=[f'{hour:02}:00–{(hour + 1) % 24:02}:00: {hours_text(seconds)}'
                      for hour, seconds in enumerate(summary.hours)])
        self.hours_chart.set_description(_('Hours read by the hour of the day: {list}').format(
            list=', '.join(f'{hour:02}:00 {hours_text(seconds)}'
                           for hour, seconds in enumerate(summary.hours) if seconds)))
        total = sum(summary.weekdays)
        if total <= 0:
            self.when_caption.set_text(_('Nothing read in the last year'))
            return
        day = max(range(7), key=lambda index: summary.weekdays[index])
        part = _part_of_day(summary.hours)
        # Translators: "You read most on Sunday and in the evening" (over the last year).
        self.when_caption.set_text(_('You read most on {day} and {part}').format(
            day=names[day], part=part))

    def _show_most(self, summary, today):
        self.most_section.set_visible(bool(summary.authors or summary.tags))
        self.most_heading.set_text(_('Most Read in {year}').format(year=today.year))
        authors = {group.name.casefold(): group.id for group in self.library.authors()}
        tags = {group.name.casefold(): group.id for group in self.library.tags()}
        _fill_list(self.authors_list, summary.authors, authors, _('Authors'))
        _fill_list(self.tags_list, summary.tags, tags, _('Tags'))

    # -- actions -----------------------------------------------------------------------------

    @Gtk.Template.Callback()
    def _on_edit_goals(self, _button):
        from ..dialogs import goals

        goals.present(app(), self.get_root() or self)

    def _on_cover_clicked(self, button):
        window = self.get_root()
        if window is not None and hasattr(window, 'show_book'):
            window.show_book(button.book_id)

    @Gtk.Template.Callback()
    def _on_author_activated(self, _list, row):
        self._show_group(row, 'author')

    @Gtk.Template.Callback()
    def _on_tag_activated(self, _list, row):
        self._show_group(row, 'tag')

    def _show_group(self, row, kind):
        window = self.get_root()
        group_id = getattr(row, 'group_id', None)
        if group_id is not None and window is not None and hasattr(window, 'show_books'):
            window.show_books(row.get_title(), **{kind: group_id})


def _scroll_to_end(adjustment):
    adjustment.set_value(adjustment.get_upper() - adjustment.get_page_size())
    return GLib.SOURCE_REMOVE


def weekday_name(day, pattern='%A'):
    """A weekday's name in the user's locale, Monday 0."""
    return charts.date_label(MONDAY + datetime.timedelta(days=day), pattern)


def _part_of_day(hours):
    """When most of the reading happens, as words."""
    parts = ((_('in the early hours'), range(0, 5)), (_('in the morning'), range(5, 12)),
             (_('in the afternoon'), range(12, 17)), (_('in the evening'), range(17, 22)),
             (_('late at night'), (22, 23)))
    return max(parts, key=lambda part: sum(hours[hour] for hour in part[1]))[0]


def _fill_list(listbox, items, ids, title):
    """Rows of (name, seconds) with a header row; a row opens the name's books."""
    while (child := listbox.get_first_child()) is not None:
        listbox.remove(child)
    listbox.set_visible(bool(items))
    if not items:
        return
    header = Gtk.ListBoxRow(activatable=False, selectable=False)
    label = Gtk.Label(label=title, xalign=0, margin_start=12, margin_end=12, margin_top=8,
                      margin_bottom=8)
    label.add_css_class('caption-heading')
    label.add_css_class('dimmed')
    header.set_child(label)
    listbox.append(header)
    most = items[0][1] or 1
    for name, seconds in items:
        row = Adw.ActionRow(title=GLib.markup_escape_text(name), activatable=name.casefold()
                            in ids)
        row.group_id = ids.get(name.casefold())
        bar = Gtk.LevelBar(value=seconds / most, valign=Gtk.Align.CENTER, width_request=56)
        bar.add_css_class('stats-share')
        bar.remove_offset_value(Gtk.LEVEL_BAR_OFFSET_LOW)
        bar.remove_offset_value(Gtk.LEVEL_BAR_OFFSET_HIGH)
        bar.remove_offset_value(Gtk.LEVEL_BAR_OFFSET_FULL)
        bar.update_property([Gtk.AccessibleProperty.LABEL], [name])
        time = Gtk.Label(label=hours_text(seconds), width_chars=6, xalign=1)
        time.add_css_class('numeric')
        time.add_css_class('dimmed')
        row.add_suffix(bar)
        row.add_suffix(time)
        if row.get_activatable():
            row.add_suffix(Gtk.Image(icon_name='go-next-symbolic',
                                     accessible_role=Gtk.AccessibleRole.PRESENTATION))
        listbox.append(row)
