# SPDX-License-Identifier: GPL-2.0-or-later
# SPDX-FileCopyrightText: 2026 Jack Tully

"""The reader window's logic that needs no GTK: the page's style from the settings, the
progress label, the reading-session clock.

    build_style(get, dark, title='')     # the style dict reader.js's setStyle() takes;
                                         # get(key) reads a reader-* setting
    STYLE_KEYS                           # the settings that change the style
    theme_colors(name, dark, custom='', high_contrast=False)  # {'name', 'bg', 'fg', 'link',
                                         # 'dark'}; custom the reader-custom-theme JSON, for
                                         # name 'custom'; "auto" under the system's high
                                         # contrast is a contrast-* paper
    custom_theme(text) / custom_theme_json(bg, fg, link)   # that JSON read and written
    is_dark(color)                       # whether a #rrggbb colour is a dark paper's
    progress_text(kind, place, ...)      # the progress label: 'percent', 'page',
    next_label(kind)                     # 'chapter-time' or 'book-time'; the next to show
    format_minutes(minutes)              # '2 h 5 min'
    readable(fmt)                        # whether the reader page opens this format
    SessionClock(fraction, now)          # a reading session: paused after IDLE_SECONDS
        clock.activity(now, fraction)    # -> a finished Session (logged) or None
        clock.finish(now)                # -> the last Session or None

A `place` is the relocated message's detail (see .claude/rules/reader.md): fraction, cfi,
chapter, page, section, location, time…
"""

import dataclasses
import json
import re
from gettext import gettext as _
from gettext import ngettext

# What foliate-js opens as it is (PDFs open in widgets/pdf_view.py; TXT and CBR are
# converted first, converting.py; CB7 is unsupported).
READABLE = ('epub', 'kepub', 'azw3', 'azw', 'mobi', 'prc', 'fb2', 'fbz', 'cbz')

# The paper themes: background, text, links. "auto" is light or dark by the system's style.
THEMES = {
    'light': {'bg': '#ffffff', 'fg': '#1d1d1f', 'link': '#1a5fb4', 'dark': False},
    'sepia': {'bg': '#f4ecd8', 'fg': '#5b4636', 'link': '#8a4b08', 'dark': False},
    'dark': {'bg': '#222226', 'fg': '#deddda', 'link': '#99c1f1', 'dark': True},
    'black': {'bg': '#000000', 'fg': '#c0bfbc', 'link': '#99c1f1', 'dark': True},
    # "auto" under the system's High Contrast: not offered as choices of their own.
    'contrast-light': {'bg': '#ffffff', 'fg': '#000000', 'link': '#0b3d91', 'dark': False},
    'contrast-dark': {'bg': '#000000', 'fg': '#ffffff', 'link': '#9fd0ff', 'dark': True},
}
THEME_NAMES = ('auto', 'light', 'sepia', 'dark', 'black')
# The user's own paper (reader-theme 'custom', its colours in reader-custom-theme): the
# schema's default until it is set.
CUSTOM_THEME = {'bg': '#e8f0e3', 'fg': '#26331f', 'link': '#2c6a2e'}
COLOR = re.compile(r'#[0-9a-fA-F]{6}')

FONTS = {
    'serif': '"Literata", "Noto Serif", "Source Serif 4", "DejaVu Serif", Georgia, serif',
    'sans': '"Adwaita Sans", "Inter", "Cantarell", "Noto Sans", "DejaVu Sans", sans-serif',
}

STYLE_KEYS = ('reader-theme', 'reader-font', 'reader-custom-font', 'reader-font-size',
              'reader-line-height', 'reader-margin', 'reader-max-width', 'reader-justify',
              'reader-hyphenate', 'reader-publisher-styles', 'reader-scrolled',
              'reader-two-pages', 'reader-animate', 'reader-custom-theme')

LABEL_KINDS = ('percent', 'page', 'chapter-time', 'book-time')

IDLE_SECONDS = 5 * 60  # a session pauses after this long without a page turned
MIN_SESSION_SECONDS = 10  # shorter sessions are not logged


def readable(fmt):
    return (fmt or '').lower() in READABLE


def is_dark(color):
    """Whether a #rrggbb background is dark (its relative luminance under a half)."""
    if not COLOR.fullmatch(color or ''):
        return False

    def linear(channel):
        value = int(channel, 16) / 255
        return value / 12.92 if value <= 0.04045 else ((value + 0.055) / 1.055) ** 2.4

    r, g, b = (linear(color[i:i + 2]) for i in (1, 3, 5))
    return 0.2126 * r + 0.7152 * g + 0.0722 * b < 0.18


def custom_theme(text):
    """The custom paper's colours from reader-custom-theme's JSON ({'bg', 'fg', 'link',
    'dark'}): a colour that is missing or no #rrggbb is CUSTOM_THEME's."""
    try:
        data = json.loads(text or '{}')
    except ValueError:
        data = {}
    if not isinstance(data, dict):
        data = {}
    colors = {key: (data.get(key) if isinstance(data.get(key), str)
                    and COLOR.fullmatch(data.get(key)) else default).lower()
              for key, default in CUSTOM_THEME.items()}
    return {**colors, 'dark': is_dark(colors['bg'])}


def custom_theme_json(bg, fg, link):
    return json.dumps({'bg': bg, 'fg': fg, 'link': link})


def theme_colors(name, dark, custom='', high_contrast=False):
    """The colours of a theme; "auto" (or an unknown name) is dark or light by `dark`,
    contrast-dark or contrast-light with `high_contrast` (the system's High Contrast);
    "custom" is the reader-custom-theme JSON `custom`."""
    if name == 'custom':
        return {'name': 'custom', **custom_theme(custom)}
    if name not in THEME_NAMES[1:]:
        name = ('contrast-' if high_contrast else '') + ('dark' if dark else 'light')
    return {'name': name, **THEMES[name]}


def font_family(font, custom=''):
    """The CSS font-family of a reader-font setting; '' for the publisher's."""
    if font == 'custom':
        custom = (custom or '').replace('"', '').replace(';', '').strip()
        return f'"{custom}", serif' if custom else ''
    return FONTS.get(font, '')


def build_style(get, dark, title='', high_contrast=False):
    """The style reader.js's setStyle() takes, from the reader-* settings (`get(key)`
    returns a setting's value: an int, float, bool or str) and the system's dark style (and
    High Contrast)."""
    return {
        'theme': theme_colors(get('reader-theme'), dark, get('reader-custom-theme') or '',
                              high_contrast),
        'font': font_family(get('reader-font'), get('reader-custom-font')),
        'fontSize': int(get('reader-font-size')),
        'lineHeight': round(float(get('reader-line-height')), 2),
        'justify': bool(get('reader-justify')),
        'hyphenate': bool(get('reader-hyphenate')),
        'publisherStyles': bool(get('reader-publisher-styles')),
        'flow': 'scrolled' if get('reader-scrolled') else 'paginated',
        'maxColumns': 2 if get('reader-two-pages') else 1,
        'maxWidth': int(get('reader-max-width')),
        'margin': int(get('reader-margin')),
        'animated': bool(get('reader-animate')),
        'marginals': True,
        'serif': FONTS['serif'],  # for a book that names no typeface
        'title': title,
    }


def next_label(kind):
    """The progress label's kind after `kind`, cycling."""
    index = LABEL_KINDS.index(kind) if kind in LABEL_KINDS else -1
    return LABEL_KINDS[(index + 1) % len(LABEL_KINDS)]


def format_minutes(minutes):
    minutes = max(0, round(minutes))
    if minutes < 1:
        return _('less than a minute')
    if minutes < 60:
        return ngettext('{n} min', '{n} min', minutes).format(n=minutes)
    hours, rest = divmod(minutes, 60)
    if rest == 0:
        # Translators: hours, abbreviated ("2 h").
        return ngettext('{n} h', '{n} h', hours).format(n=hours)
    return _('{hours} h {minutes} min').format(hours=hours, minutes=rest)


def progress_text(kind, place, chapter_minutes=None, book_minutes=None):
    """The progress label for a relocated `place`. The times left are in minutes: those
    given (from the reader's own speed, stats.py), else foliate-js's estimate in `place`."""
    if not place:
        return ''
    fraction = place.get('fraction') or 0.0
    if kind == 'page':
        page = place.get('page')
        if page and place.get('pages'):  # a PDF
            return _('Page {page} of {pages}').format(page=page, pages=place['pages'])
        if page:
            return _('Page {page}').format(page=page)
        location = place.get('location') or {}
        total = location.get('total')
        if total:
            current = min(total, (location.get('current') or 0) + 1)
            return _('Location {current} of {total}').format(current=current, total=total)
        kind = 'percent'
    if kind in ('chapter-time', 'book-time'):
        time = place.get('time') or {}
        if kind == 'chapter-time':
            minutes = chapter_minutes if chapter_minutes is not None else time.get('section')
            if minutes is not None:
                return _('{time} left in chapter').format(time=format_minutes(minutes))
        else:
            minutes = book_minutes if book_minutes is not None else time.get('total')
            if minutes is not None:
                return _('{time} left in book').format(time=format_minutes(minutes))
    # Translators: a percentage ("45%").
    return _('{percent}%').format(percent=int(fraction * 100 + 1e-9))


@dataclasses.dataclass
class Session:
    started: float
    seconds: float
    start_fraction: float
    end_fraction: float


class SessionClock:
    """The time spent reading a book: from opening it to the last page turned, paused when
    nothing happens for IDLE_SECONDS (the idle time is not counted, and what came before
    is a session of its own)."""

    def __init__(self, fraction, now):
        self._start(now, fraction)

    def _start(self, now, fraction):
        self.started = now
        self.last = now
        self.start_fraction = fraction
        self.end_fraction = fraction

    def _session(self):
        seconds = self.last - self.started
        if seconds < MIN_SESSION_SECONDS:
            return None
        return Session(self.started, seconds, self.start_fraction, self.end_fraction)

    def activity(self, now, fraction=None):
        """A page turned (or anything showing the reader is there). Returns the session
        that ended when the reader came back after a pause, else None."""
        ended = None
        if now - self.last > IDLE_SECONDS:
            ended = self._session()
            self._start(now, self.end_fraction)
        self.last = now
        if fraction is not None:
            self.end_fraction = fraction
        return ended

    def finish(self, now):
        """The session at closing (None when too short). Time since the last activity
        counts up to IDLE_SECONDS."""
        self.last = min(now, self.last + IDLE_SECONDS) if now > self.last else self.last
        return self._session()
