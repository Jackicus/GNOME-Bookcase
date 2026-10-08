# SPDX-License-Identifier: GPL-2.0-or-later
# SPDX-FileCopyrightText: 2026 Jack Tully

"""The home page's reading goal card: a small ring and a line ("12 of 24 books this year"),
with the streak and where the year's pace stands under it; a click opens the Statistics page.

    card = GoalCard(library=None, settings=None)   # the app's unless given (tests)
    card.refresh()                     # also on map, on the library's `changed` and on a
                                       # goal setting's change

Hidden while there is nothing to show: no goal, no book finished this year and no streak.
Being hidden, it is never mapped, so it follows the library's `changed` itself (weakly,
debounced by REFRESH_DELAY_MS) rather than through a pages.PageListener.
"""

import datetime
from gettext import gettext as _
from gettext import ngettext

from gi.repository import Gio, GLib, Gtk, Pango

from .. import stats
from .charts import Ring
from .util import connect_weak

CHANGE_KINDS = ('books', 'progress')
REFRESH_DELAY_MS = 300


def goal_lines(library, target, today):
    """(title, subtitle, fraction of the goal or None) of the card, or None when there is
    nothing to show."""
    year_start = datetime.date(today.year, 1, 1)
    done = len(library.finished(since=stats.day_start(year_start),
                                until=stats.day_start(datetime.date(today.year + 1, 1, 1))))
    streak = stats.streak(library, today)
    if not target and not done and not streak:
        return None
    if target:
        # Translators: the yearly goal on the home page: "12 of 24 books this year".
        title = ngettext('{done} of {n} book this year', '{done} of {n} books this year',
                         target).format(done=done, n=target)
    else:
        title = ngettext('{n} book finished this year', '{n} books finished this year',
                         done).format(n=done)
    details = []
    if streak:
        # Translators: days read in a row, on the home page ("3-day streak").
        details.append(ngettext('{n}-day streak', '{n}-day streak', streak).format(n=streak))
    goal = stats.goal_progress(done, target, today)
    if goal is not None:
        from ..pages.stats import schedule_text

        details.append(schedule_text(goal, today))
    return title, ' · '.join(details), goal.fraction if goal is not None else None


class GoalCard(Gtk.Button):
    __gtype_name__ = 'BookcaseGoalCard'

    def __init__(self, library=None, settings=None):
        super().__init__(margin_start=24, margin_end=24, halign=Gtk.Align.FILL,
                         tooltip_text=_('Reading statistics'))
        self.add_css_class('card')
        self.add_css_class('goal-card')
        if library is None or settings is None:
            app = Gio.Application.get_default()
            library = library or app.library
            settings = settings or app.settings
        self._pending = None
        self.library = library
        self.settings = settings
        box = Gtk.Box(spacing=12)
        self.ring = Ring(size=40, valign=Gtk.Align.CENTER,
                         accessible_role=Gtk.AccessibleRole.PRESENTATION)
        box.append(self.ring)
        labels = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=2, hexpand=True,
                         valign=Gtk.Align.CENTER)
        self.title = Gtk.Label(xalign=0, wrap=True, wrap_mode=Pango.WrapMode.WORD_CHAR)
        self.title.add_css_class('heading')
        self.subtitle = Gtk.Label(xalign=0, wrap=True, wrap_mode=Pango.WrapMode.WORD_CHAR)
        self.subtitle.add_css_class('caption')
        self.subtitle.add_css_class('dimmed')
        labels.append(self.title)
        labels.append(self.subtitle)
        box.append(labels)
        box.append(Gtk.Image(icon_name='go-next-symbolic',
                             accessible_role=Gtk.AccessibleRole.PRESENTATION))
        self.set_child(box)
        self.set_visible(False)
        for key in ('goal-books', 'goal-minutes'):
            connect_weak(settings, f'changed::{key}', self._on_goal_changed)
        connect_weak(library, 'changed', self._on_library_changed)
        self.refresh()

    def do_clicked(self):
        window = self.get_root()
        if window is not None and hasattr(window, 'show_root'):
            window.show_root('stats')

    def _on_goal_changed(self, *_args):
        self.refresh()

    def _on_library_changed(self, _library, kind):
        if kind in CHANGE_KINDS and self._pending is None:
            ref = self.weak_ref()
            self._pending = GLib.timeout_add(REFRESH_DELAY_MS, _refresh_later, ref)

    def refresh_later_done(self):
        self._pending = None

    def refresh(self):
        if getattr(self.library, 'closed', False):
            return
        lines = goal_lines(self.library, self.settings.get_int('goal-books'),
                           stats.current_day())
        self.set_visible(lines is not None)
        if lines is None:
            return
        title, subtitle, fraction = lines
        self.title.set_text(title)
        self.subtitle.set_text(subtitle)
        self.subtitle.set_visible(bool(subtitle))
        self.ring.set_data(1.0 if fraction is None else fraction)
        self.update_property([Gtk.AccessibleProperty.LABEL],
                             [f'{title}. {subtitle}' if subtitle else title])


def _refresh_later(ref):
    card = ref()
    if card is not None:
        card.refresh_later_done()
        card.refresh()
    return GLib.SOURCE_REMOVE
