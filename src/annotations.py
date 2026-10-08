# SPDX-License-Identifier: GPL-2.0-or-later
# SPDX-FileCopyrightText: 2026 Jack Tully

"""Highlights and notes out, as Markdown; a Kindle's My Clippings.txt in.

    text = annotations.to_markdown(book, library.annotations(book.id))
    clippings = annotations.parse_kindle_clippings(text)   # [Clipping]
    matches = annotations.match_clippings(library, clippings)   # [ClippingMatch]

A Clipping is a named tuple (title, author, kind, location_text, text, note, added): kind is
'highlight', 'note' (a note with no highlight under it) or 'bookmark'; location_text what the
Kindle says ('page 12, Location 180-183'); added a Unix time (0 when the date is in a form
not understood). A note the Kindle saved as an entry of its own is joined to the highlight
it sits on. `clipping.location` is (start, end) of the Kindle location, or None.

match_clippings() pairs clippings with the library's books by normalised title and author
(Library.find_similar). A match's `position` (0-1) is only an estimate for ordering: the
clipping's location over the highest location clipped in that book, since a Kindle location
cannot be turned into a CFI without the Kindle's own file. Adding them as annotations is up
to the caller (location '', that position).
"""

import dataclasses
import datetime
import re
from gettext import gettext as _
from typing import NamedTuple

SEPARATOR = re.compile(r'^=+\s*$', re.MULTILINE)
_LOCATION = re.compile(r'\b(?:location|loc\.?)\s*(\d+)(?:\s*-\s*(\d+))?', re.IGNORECASE)
_PAGE = re.compile(r'\bpage\s*(\d+)', re.IGNORECASE)
MONTHS = {name: number for number, name in enumerate(
    ('january', 'february', 'march', 'april', 'may', 'june', 'july', 'august', 'september',
     'october', 'november', 'december'), 1)}


def color_name(color):
    """A highlight colour as a word, translated."""
    return {'yellow': _('Yellow'), 'green': _('Green'), 'blue': _('Blue'), 'pink': _('Pink'),
            'purple': _('Purple')}.get(color, color)


def _percent(position):
    return f'{round(max(0.0, min(1.0, position)) * 100)}%'


def to_markdown(book, annotations):
    """A Markdown document of a book's highlights (quoted, with their notes, colours and
    places) and bookmarks."""
    lines = [f'# {book.title}', '']
    if book.authors:
        lines += [book.author, '']
    highlights = [item for item in annotations if item.kind == 'highlight']
    bookmarks = [item for item in annotations if item.kind == 'bookmark']
    if highlights:
        lines += [f'## {_("Highlights")}', '']
        for item in sorted(highlights, key=lambda item: item.position):
            for line in (item.text or '').strip().splitlines() or ['']:
                lines.append(f'> {line.strip()}'.rstrip())
            lines.append('')
            if item.note.strip():
                lines += [item.note.strip(), '']
            lines += [f'*{color_name(item.color)} · {_percent(item.position)}*', '']
    if bookmarks:
        lines += [f'## {_("Bookmarks")}', '']
        for item in sorted(bookmarks, key=lambda item: item.position):
            label = item.text.strip() or item.note.strip()
            lines.append(f'- {_percent(item.position)}' + (f': {label}' if label else ''))
        lines.append('')
    return '\n'.join(lines).rstrip() + '\n'


class Clipping(NamedTuple):
    title: str
    author: str
    kind: str
    location_text: str
    text: str
    note: str
    added: float

    @property
    def location(self):
        match = _LOCATION.search(self.location_text)
        if not match:
            return None
        start = int(match.group(1))
        end = match.group(2)
        if end is None:
            return start, start
        # Old Kindles shorten the end: 'Loc. 1180-83' is 1180-1183.
        if len(end) < len(match.group(1)):
            end = match.group(1)[:len(match.group(1)) - len(end)] + end
        return start, max(start, int(end))


def _parse_date(text):
    """A Unix time from 'Monday, 3 March 2025 10:12:01' or 'Monday, March 3, 2025 10:12:01
    AM' (English); 0 when not understood."""
    words = re.findall(r'[A-Za-z]+|\d+:\d+(?::\d+)?|\d+', text)
    month = next((MONTHS[word.lower()] for word in words if word.lower() in MONTHS), None)
    numbers = [int(word) for word in words if word.isdigit()]
    times = [word for word in words if ':' in word]
    if month is None or len(numbers) < 2:
        return 0.0
    year = next((number for number in numbers if number > 31), None)
    day = next((number for number in numbers if number <= 31), None)
    if year is None or day is None:
        return 0.0
    hour = minute = second = 0
    if times:
        parts = [int(part) for part in times[0].split(':')]
        hour, minute = parts[0], parts[1]
        second = parts[2] if len(parts) > 2 else 0
        meridian = next((word.lower() for word in words if word.lower() in ('am', 'pm')), None)
        if meridian == 'pm' and hour < 12:
            hour += 12
        elif meridian == 'am' and hour == 12:
            hour = 0
    try:
        return datetime.datetime(year, month, day, hour, minute, second).timestamp()
    except (ValueError, OverflowError, OSError):
        return 0.0


def _title_author(line):
    line = line.strip().lstrip('﻿').strip()
    if line.endswith(')') and ' (' in line:
        # The author is the last bracketed part, which may hold brackets of its own.
        depth = 0
        for at in range(len(line) - 1, -1, -1):
            if line[at] == ')':
                depth += 1
            elif line[at] == '(':
                depth -= 1
                if depth == 0:
                    return line[:at].strip(), line[at + 1:-1].strip()
    return line, ''


def _parse_entry(entry):
    lines = entry.strip('\r\n').splitlines()
    while lines and not lines[0].strip():
        lines.pop(0)
    if len(lines) < 2 or not lines[1].lstrip().startswith('-'):
        return None
    title, author = _title_author(lines[0])
    meta = lines[1].strip().lstrip('-').strip()
    lowered = meta.lower()
    if 'bookmark' in lowered:
        kind = 'bookmark'
    elif 'note' in lowered:
        kind = 'note'
    elif 'highlight' in lowered:
        kind = 'highlight'
    else:
        return None
    parts = [part.strip() for part in meta.split('|')]
    added = 0.0
    places = []
    for part in parts:
        if part.lower().startswith('added on'):
            added = _parse_date(part[len('added on'):])
        else:
            place = re.sub(r'^(?:your\s+)?(?:highlight|note|bookmark)\s*(?:on|at)?\s*', '', part,
                           flags=re.IGNORECASE)
            if place:
                places.append(place)
    body = '\n'.join(line.rstrip() for line in lines[2:]).strip()
    text, note = (body, '') if kind != 'note' else ('', body)
    return Clipping(title, author, kind, ', '.join(places), text, note, added)


def parse_kindle_clippings(text):
    """The clippings of a My Clippings.txt; entries it cannot read are left out. A note is
    joined to the highlight of the same book whose location range holds it."""
    clippings = [clip for clip in map(_parse_entry, SEPARATOR.split(text or ''))
                 if clip is not None]
    result = [(index, clip) for index, clip in enumerate(clippings) if clip.kind != 'note']
    for index, note in enumerate(clippings):
        if note.kind != 'note':
            continue
        place = note.location
        target = None
        if place is not None:
            for at, (_index, clip) in enumerate(result):
                span = clip.location
                if (clip.kind == 'highlight' and clip.title == note.title
                        and clip.author == note.author and span is not None
                        and span[0] <= place[0] <= span[1]):
                    target = at
        if target is None:
            result.append((index, note))
        else:
            clip_index, clip = result[target]
            joined = f'{clip.note}\n\n{note.note}' if clip.note else note.note
            result[target] = (clip_index, clip._replace(note=joined))
    result.sort(key=lambda pair: pair[0])
    return [clip for _index, clip in result]


@dataclasses.dataclass(frozen=True)
class ClippingMatch:
    clipping: Clipping
    book_id: int | None  # None when no book in the library matched
    position: float  # 0-1, an estimate (see the module)


def match_clippings(library, clippings):
    """A ClippingMatch for each clipping, in the same order."""
    books = {}
    highest = {}
    for clip in clippings:
        key = (clip.title, clip.author)
        if key not in books:
            authors = [clip.author] if clip.author else []
            found = library.find_similar(clip.title, authors)
            books[key] = found[0] if found else None
        place = clip.location
        if place is not None:
            highest[key] = max(highest.get(key, 0), place[1])
    matches = []
    for clip in clippings:
        key = (clip.title, clip.author)
        place = clip.location
        position = place[0] / highest[key] if place is not None and highest.get(key) else 0.0
        matches.append(ClippingMatch(clip, books[key], min(1.0, position)))
    return matches
