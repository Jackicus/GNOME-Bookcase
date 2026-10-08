# SPDX-License-Identifier: GPL-2.0-or-later
# SPDX-FileCopyrightText: 2026 Jack Tully

"""The Statistics page's charts: widgets that draw their data in do_snapshot (after Retain's,
by the same author).

    heatmap = Heatmap(); heatmap.set_data(minutes, today)  # {datetime.date: minutes}
    chart = BarChart(); chart.set_data(values, labels=…, tooltips=…, highlight=index)
    ring = Ring(); ring.set_data(fraction, centre='13', caption='of 15 books')
    chart.set_description(text)                            # what a screen reader hears

The accent colour is the user's (from the style manager); other marks use the foreground
colour, faint, so the charts follow the theme, and each redraws when the style manager's
dark or accent-color changes while it is mapped. Text is the widget's own (the `caption`
size). Every chart is an image to a screen reader, with the description the page sets.

A chart measures its own size (a plain Gtk.Widget has no layout manager, so do_measure is
honoured): a bar chart asks for a natural height and stretches to the width it is given;
the heatmap never draws a cell smaller than MIN_CELL, so it asks for that width at least
and the page scrolls it when the window is narrower; the ring is a fixed square.

quantile_steps() and level_of() (the heatmap's colour steps) and nice_ticks() (an axis) are
pure, for the tests.
"""

import bisect
import datetime
import math
from gettext import gettext as _
from gettext import ngettext

from gi.repository import Adw, Gdk, GLib, GObject, Graphene, Gsk, Gtk, Pango

HEATMAP_ALPHAS = (0.25, 0.4, 0.6, 0.8, 1.0)  # the accent at each level above zero
STEPS = len(HEATMAP_ALPHAS)
ZERO_ALPHA = 0.08  # the foreground colour for a day without reading
TRACK_ALPHA = 0.1  # the ring's empty track
AXIS_ALPHA = 0.55
GRID_ALPHA = 0.12


# -- colours and pure helpers -------------------------------------------------------------

def with_alpha(rgba, alpha):
    result = Gdk.RGBA()
    result.red, result.green, result.blue, result.alpha = rgba.red, rgba.green, rgba.blue, alpha
    return result


def accent():
    return Adw.StyleManager.get_default().get_accent_color_rgba()


def quantile_steps(values, steps=STEPS):
    """The thresholds that split the positive values into `steps` quantile levels:
    `steps - 1` values, ascending (empty when there are no values)."""
    ordered = sorted(value for value in values if value > 0)
    if not ordered:
        return []
    return [ordered[min(len(ordered) - 1, len(ordered) * step // steps)]
            for step in range(1, steps)]


def level_of(value, thresholds):
    """0 for nothing, else 1 to len(thresholds) + 1 by the thresholds passed."""
    if value <= 0:
        return 0
    return 1 + bisect.bisect_right(thresholds, value)


def nice_ticks(maximum, target=3):
    """(step, top) for an axis up to `maximum`: a round step giving about `target`
    gridlines, and the first multiple of it at or above the maximum."""
    if maximum <= 0:
        return 1, 1
    raw = maximum / target
    magnitude = 10 ** math.floor(math.log10(raw))
    step = magnitude
    for factor in (1, 2, 2.5, 5, 10):
        step = factor * magnitude
        if maximum / step <= target + 0.5:
            break
    if step >= 1:
        step = int(step)
    top = math.ceil(maximum / step - 1e-9) * step
    return step, top


def format_count(value):
    """A tick label: whole numbers as they are, else one decimal."""
    if float(value).is_integer():
        return f'{int(value):,}'
    return f'{value:.1f}'


def date_label(date, pattern='%e %b'):
    """A date in the user's locale ('3 Mar')."""
    moment = GLib.DateTime.new_local(date.year, date.month, date.day, 12, 0, 0)
    return moment.format(pattern).strip()


def _rect(x, y, width, height):
    return Graphene.Rect().init(x, y, width, height)


def _rounded(bounds, radius, top_only=False):
    corner = Graphene.Size().init(radius, radius)
    square = Graphene.Size().init(0, 0)
    rounded = Gsk.RoundedRect()
    rounded.init(bounds, corner, corner, square if top_only else corner,
                 square if top_only else corner)
    return rounded


# -- the base ---------------------------------------------------------------------------

class Chart(Gtk.Widget):
    """A widget drawn in do_snapshot, an image to a screen reader; see the module."""

    MIN_WIDTH = 120
    NATURAL_WIDTH = 240
    NATURAL_HEIGHT = 140

    def __init__(self, **kwargs):
        kwargs.setdefault('accessible_role', Gtk.AccessibleRole.IMG)
        super().__init__(**kwargs)
        self.add_css_class('caption')
        self._description = ''
        self._style_handlers = []

    @property
    def description(self):
        """The accessible description last set."""
        return self._description

    def set_description(self, text):
        self._description = text or ''
        self.update_property([Gtk.AccessibleProperty.DESCRIPTION], [self._description])

    def do_measure(self, orientation, for_size):
        if orientation == Gtk.Orientation.HORIZONTAL:
            return self.MIN_WIDTH, max(self.MIN_WIDTH, self.NATURAL_WIDTH), -1, -1
        return self.NATURAL_HEIGHT, self.NATURAL_HEIGHT, -1, -1

    def do_map(self):
        Gtk.Widget.do_map(self)
        manager = Adw.StyleManager.get_default()
        self._style_handlers = [manager.connect(f'notify::{name}', self._on_style_changed)
                                for name in ('dark', 'accent-color')]

    def do_unmap(self):
        manager = Adw.StyleManager.get_default()
        for handler in self._style_handlers:
            manager.disconnect(handler)
        self._style_handlers = []
        Gtk.Widget.do_unmap(self)

    def _on_style_changed(self, *_args):
        self.queue_draw()

    def do_snapshot(self, snapshot):
        self.draw(snapshot, self.get_width(), self.get_height())

    def draw(self, snapshot, width, height):
        """Draw the chart into `width` × `height`; the subclasses do."""

    def dim(self, alpha=AXIS_ALPHA):
        return with_alpha(self.get_color(), alpha)

    def layout(self, text, scale=None, bold=False):
        layout = self.create_pango_layout(text)
        if scale or bold:
            attributes = Pango.AttrList()
            if scale:
                attributes.insert(Pango.attr_scale_new(scale))
            if bold:
                attributes.insert(Pango.attr_weight_new(Pango.Weight.BOLD))
            layout.set_attributes(attributes)
        return layout

    def text_size(self, text):
        return self.layout(text).get_pixel_size()

    def draw_text(self, snapshot, layout, x, y, rgba=None, align='left'):
        width, _height = layout.get_pixel_size()
        if align == 'center':
            x -= width / 2
        elif align == 'right':
            x -= width
        snapshot.save()
        snapshot.translate(Graphene.Point().init(round(x), round(y)))
        snapshot.append_layout(layout, rgba or self.get_color())
        snapshot.restore()

    @staticmethod
    def fill(snapshot, x, y, width, height, rgba, radius=0, top_only=False):
        bounds = _rect(x, y, width, height)
        if radius <= 0 or width < 2 * radius:
            snapshot.append_color(rgba, bounds)
            return
        snapshot.push_rounded_clip(_rounded(bounds, radius, top_only))
        snapshot.append_color(rgba, bounds)
        snapshot.pop()


# -- the heatmap --------------------------------------------------------------------------

class Heatmap(Chart):
    """A year of reading days as a calendar of squares, a week per column, Monday at the
    top; the darker the more minutes read."""

    __gtype_name__ = 'BookcaseHeatmap'

    CELL = 13
    MIN_CELL = 9
    GAP = 3
    WEEKS = 53
    DAYS = 365

    def __init__(self, **kwargs):
        super().__init__(**kwargs)
        self.minutes = {}  # {ordinal: minutes}
        self.today = None  # an ordinal
        self.thresholds = []
        self.set_has_tooltip(True)
        self.connect('query-tooltip', self._on_query_tooltip)

    def set_data(self, minutes, today):
        """`minutes` {datetime.date: minutes read}, `today` a datetime.date."""
        self.minutes = {day.toordinal(): value for day, value in minutes.items()}
        self.today = today.toordinal()
        self.thresholds = quantile_steps(self.minutes.values())
        self.queue_resize()
        self.queue_draw()

    def first_day(self):
        return self.today - self.DAYS + 1

    def start(self):
        """The Monday of the first column."""
        first = self.first_day()
        return first - datetime.date.fromordinal(first).weekday()

    def columns(self):
        if self.today is None:
            return self.WEEKS
        return (self.today - self.start()) // 7 + 1

    def _gutters(self):
        """(left, top): the room for the weekday initials and the month names."""
        width, height = self.text_size('W')
        return width + 8, height + 4

    def _cell_for_width(self, width):
        left, _top = self._gutters()
        cell = (width - left) // self.columns() - self.GAP
        return max(self.MIN_CELL, min(self.CELL, int(cell)))

    def do_get_request_mode(self):
        return Gtk.SizeRequestMode.HEIGHT_FOR_WIDTH

    def do_measure(self, orientation, for_size):
        left, top = self._gutters()
        if orientation == Gtk.Orientation.HORIZONTAL:
            minimum = left + self.columns() * (self.MIN_CELL + self.GAP)
            natural = left + self.columns() * (self.CELL + self.GAP)
            return minimum, natural, -1, -1
        cell = self._cell_for_width(for_size) if for_size >= 0 else self.CELL
        height = top + 7 * (cell + self.GAP)
        return height, height, -1, -1

    def _cell_at(self, x, y):
        """The ordinal of the day under a point, or None."""
        if self.today is None:
            return None
        left, top = self._gutters()
        step = self._cell_for_width(self.get_width()) + self.GAP
        column = int((x - left) // step)
        row = int((y - top) // step)
        if column < 0 or row < 0 or row > 6 or column >= self.columns():
            return None
        day = self.start() + column * 7 + row
        if day < self.first_day() or day > self.today:
            return None
        return day

    def draw(self, snapshot, width, height):
        if self.today is None:
            return
        left, top = self._gutters()
        cell = self._cell_for_width(width)
        step = cell + self.GAP
        foreground = self.get_color()
        dim = self.dim()
        colour = accent()
        zero = with_alpha(foreground, ZERO_ALPHA)
        start = self.start()
        first = self.first_day()
        radius = 3 if cell >= 11 else 2
        # The month names, at each column whose Monday starts a new month, unless the next
        # one follows within three columns.
        labels = []
        previous_month = None
        for column in range(self.columns()):
            date = datetime.date.fromordinal(start + column * 7)
            if date.month != previous_month:
                if previous_month is not None or column == 0:
                    labels.append((column, date))
                previous_month = date.month
        for index, (column, date) in enumerate(labels):
            if index + 1 < len(labels) and labels[index + 1][0] - column < 3:
                continue
            label = self.layout(date_label(date, '%b'))
            x = min(left + column * step, width - label.get_pixel_size()[0])
            self.draw_text(snapshot, label, x, 0, dim)
        # The weekday initials beside Monday, Wednesday and Friday.
        for row in (0, 2, 4):
            label = self.layout(date_label(datetime.date.fromordinal(start + row), '%a')[:1])
            _w, h = label.get_pixel_size()
            self.draw_text(snapshot, label, 0, top + row * step + (cell - h) / 2, dim)
        for column in range(self.columns()):
            for row in range(7):
                day = start + column * 7 + row
                if day < first or day > self.today:
                    continue
                level = level_of(self.minutes.get(day, 0), self.thresholds)
                rgba = zero if level == 0 else with_alpha(colour, HEATMAP_ALPHAS[level - 1])
                x = left + column * step
                y = top + row * step
                self.fill(snapshot, x, y, cell, cell, rgba, radius)
                if day == self.today:
                    outline = _rounded(_rect(x - 1, y - 1, cell + 2, cell + 2), radius + 1)
                    snapshot.append_border(outline, [1.5] * 4, [foreground] * 4)

    def _on_query_tooltip(self, _widget, x, y, keyboard, tooltip):
        if keyboard:
            return False
        day = self._cell_at(x, y)
        if day is None:
            return False
        minutes = round(self.minutes.get(day, 0))
        date = date_label(datetime.date.fromordinal(day), '%e %B %Y')
        if minutes == 0:
            tooltip.set_text(_('Nothing read on {date}').format(date=date))
        else:
            tooltip.set_text(ngettext('{n} minute read on {date}', '{n} minutes read on {date}',
                                      minutes).format(n=minutes, date=date))
        return True


# -- bars -------------------------------------------------------------------------------

class BarChart(Chart):
    """Bars with gridlines and labels under them.

        set_data(values, labels=None, tooltips=None, highlight=None, format=None)

    `labels` a string or None per bar (one that would overlap the one before is left out);
    `tooltips` a string per bar; the bar at `highlight` is drawn in the accent, the others
    a lighter accent; `format` turns a tick value into its label.
    """

    __gtype_name__ = 'BookcaseBarChart'

    NATURAL_HEIGHT = 150
    TOP = 8
    GAP = 4

    def __init__(self, **kwargs):
        super().__init__(**kwargs)
        self.values = []
        self.labels = None
        self.tooltips = None
        self.highlight = None
        self.format = format_count
        self.set_has_tooltip(True)
        self.connect('query-tooltip', self._on_query_tooltip)

    def set_data(self, values, labels=None, tooltips=None, highlight=None, format=None):
        self.values = list(values)
        self.labels = labels
        self.tooltips = tooltips
        self.highlight = highlight
        self.format = format or format_count
        self.queue_draw()

    def _frame(self, width, height):
        """(left, right, top, bottom) of the plot area, the ticks and the top value."""
        step, top_value = nice_ticks(max(self.values, default=0))
        ticks = [tick * step for tick in range(int(round(top_value / step)) + 1)]
        left = max(self.text_size(self.format(tick))[0] for tick in ticks) + 8
        bottom = height
        if self.labels and any(self.labels):
            bottom = height - self.text_size('0')[1] - 4
        return left, width, self.TOP, bottom, ticks, top_value

    def _slot(self, left, right):
        return (right - left) / max(1, len(self.values))

    def draw(self, snapshot, width, height):
        if not self.values:
            return
        left, right, top, bottom, ticks, top_value = self._frame(width, height)
        dim = self.dim()
        grid = self.dim(GRID_ALPHA)
        plot_height = bottom - top
        for tick in ticks:
            y = bottom - plot_height * tick / top_value
            snapshot.append_color(grid, _rect(left, round(y), right - left, 1))
            label = self.layout(self.format(tick))
            _w, h = label.get_pixel_size()
            self.draw_text(snapshot, label, left - 6, y - h / 2, dim, align='right')
        slot = self._slot(left, right)
        gap = self.GAP if slot >= 10 else 1
        bar_width = max(1, min(slot - gap, 40))
        radius = 3 if bar_width >= 8 else 0
        colour = accent()
        light = with_alpha(colour, 0.45)
        for index, value in enumerate(self.values):
            if value <= 0:
                continue
            bar_height = max(2, plot_height * value / top_value)
            x = left + index * slot + (slot - bar_width) / 2
            rgba = colour if self.highlight in (None, index) else light
            self.fill(snapshot, x, bottom - bar_height, bar_width, bar_height, rgba, radius,
                      top_only=True)
        if self.labels:
            last_right = -math.inf
            for index, text in enumerate(self.labels):
                if not text:
                    continue
                label = self.layout(text)
                w, _h = label.get_pixel_size()
                x = left + index * slot + slot / 2
                x = min(max(x, left + w / 2), right - w / 2)
                if x - w / 2 < last_right + 4:
                    continue
                last_right = x + w / 2
                self.draw_text(snapshot, label, x, bottom + 4, dim, align='center')

    def _on_query_tooltip(self, _widget, x, y, keyboard, tooltip):
        if keyboard or not self.tooltips or not self.values:
            return False
        left, right, top, bottom, _ticks, _top = self._frame(self.get_width(),
                                                             self.get_height())
        if x < left or x >= right or y < top or y > bottom:
            return False
        index = int((x - left) // self._slot(left, right))
        if index < 0 or index >= len(self.tooltips) or not self.tooltips[index]:
            return False
        tooltip.set_text(self.tooltips[index])
        return True


# -- the goal's ring --------------------------------------------------------------------

class Ring(Chart):
    """A progress ring with a number in the middle and a caption under it."""

    __gtype_name__ = 'BookcaseRing'

    size = GObject.Property(type=int, default=148)  # its width and height

    def __init__(self, **kwargs):
        super().__init__(**kwargs)
        self.fraction = 0.0
        self.centre = ''
        self.caption = ''
        self.connect('notify::size', lambda *_args: self.queue_resize())

    def set_data(self, fraction, centre='', caption=''):
        self.fraction = max(0.0, min(1.0, fraction))
        self.centre = centre
        self.caption = caption
        self.queue_draw()

    def do_measure(self, orientation, for_size):
        return self.size, self.size, -1, -1

    def draw(self, snapshot, width, height):
        cx, cy = width / 2, height / 2
        thickness = max(4, round(self.size / 10.5))
        radius = min(width, height) / 2 - thickness / 2 - 1
        centre = Graphene.Point().init(cx, cy)
        track = Gsk.PathBuilder.new()
        track.add_circle(centre, radius)
        snapshot.append_stroke(track.to_path(), Gsk.Stroke.new(thickness),
                               self.dim(TRACK_ALPHA))
        if self.fraction > 0:
            stroke = Gsk.Stroke.new(thickness)
            stroke.set_line_cap(Gsk.LineCap.ROUND)
            arc = Gsk.PathBuilder.new()
            if self.fraction >= 1:
                arc.add_circle(centre, radius)
            else:
                angle = 2 * math.pi * self.fraction
                arc.move_to(cx, cy - radius)
                arc.svg_arc_to(radius, radius, 0, angle > math.pi, True,
                               cx + radius * math.sin(angle), cy - radius * math.cos(angle))
            snapshot.append_stroke(arc.to_path(), stroke, accent())
        if not self.centre:
            return
        number = self.layout(self.centre, scale=2.4 if self.size >= 140 else 2.0, bold=True)
        _w, number_height = number.get_pixel_size()
        caption = self.layout(self.caption) if self.caption else None
        caption_height = caption.get_pixel_size()[1] if caption else 0
        y = cy - (number_height + caption_height) / 2
        self.draw_text(snapshot, number, cx, y, align='center')
        if caption:
            self.draw_text(snapshot, caption, cx, y + number_height, self.dim(),
                           align='center')
