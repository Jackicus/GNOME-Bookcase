# SPDX-License-Identifier: GPL-2.0-or-later
# SPDX-FileCopyrightText: 2026 Jack Tully

"""Places in a PDF: the location strings the reader saves for a book shown by pages
(widgets/pdf_view.py), where an EPUB has a CFI; and the rest of what the PDF reader keeps or
works out without a display (its per-book layout, the sentences it reads aloud).

    location(page, offset=0.0, rects=(), more=())  # 'page:12', 'page:12@0.25',
                                          # 'page:12#10,20.5,90,32', 'page:12#…|13#…'
    span_location(parts)                  # the location of [(page, rects)] over pages
    parse(text) -> Place or None          # Place(page, offset, rects, more); place.parts
    fraction(page, offset, count)         # the place as a fraction of the book, 0-1
    from_fraction(fraction, count)        # (page, offset)
    rects_text(rects) / parse_rects(text)
    layout_state(value) -> dict           # a book's saved zoom and layout, checked
    sentences(text) -> [(start, end)]     # a page's text cut into sentences
    speakable(text)                       # a sentence's text as it is spoken
    speakable_map(text) -> (spoken, positions)    # and where each character came from
    declares_rtl(path)                    # the PDF's catalogue says /Direction /R2L

`page` counts from 1 (the PDF's own order, not its printed labels). `offset` is how far down
that page the top of the view was, 0 (its top) to 1. `rects` are a highlight's or search
match's boxes on the page, (x0, y0, x1, y1) in PDF points from the page's top-left corner,
rounded to a tenth: 'page:3#72,90.5,300,104;72,106,180,119.5'. Progress is saved as
library.set_progress(book, fraction(...), location(page, offset)), and opening goes back to
the location (the fraction when the location does not parse). Bookmarks are a page's
location without offset; highlights carry their rects.

A selection or highlight that runs over pages carries each page's rects, the pages after
the first after a '|': 'page:3#72,700,300,714|4#72,60,180,74'. An older Bookcase reads such
a string as its first page's (the rects after the '|' do not parse and are dropped), and
this one reads the old strings as before. `more` holds the following pages as
((page, rects), …), and `parts` all of them, the first included.

The layout (layout_state) is what the reader keeps per PDF in the library
(Library.book_state()['pdf']): {'fit': 'auto' | 'width' | 'page' | None (a percentage),
'zoom': the percentage (25-600) or None, 'flow': 'scrolled' | 'paginated' | None, 'rtl':
spreads and page turns right to left, 'cover': the first page alone in spreads}; a key that
is None is not set (the book follows the default: the reader-pdf-scrolled setting, the
PDF's own viewer preferences).
"""

import dataclasses
import re

FITS = ('auto', 'width', 'page')
FLOWS = ('scrolled', 'paginated')
ZOOM_RANGE = (25, 600)  # percent


@dataclasses.dataclass(frozen=True)
class Place:
    page: int
    offset: float = 0.0
    rects: tuple = ()
    more: tuple = ()  # ((page, rects), …): the pages after the first a highlight runs over

    @property
    def parts(self):
        """((page, rects), …) of every page the place covers, the first included."""
        return ((self.page, self.rects),) + self.more


def _number(value):
    text = f'{round(float(value), 1):.1f}'
    return text[:-2] if text.endswith('.0') else text


def rects_text(rects):
    return ';'.join(','.join(_number(v) for v in rect) for rect in rects)


def parse_rects(text):
    rects = []
    for part in (text or '').split(';'):
        values = part.split(',')
        if len(values) != 4:
            continue
        try:
            x0, y0, x1, y1 = (float(v) for v in values)
        except ValueError:
            continue
        rects.append((min(x0, x1), min(y0, y1), max(x0, x1), max(y0, y1)))
    return tuple(rects)


def location(page, offset=0.0, rects=(), more=()):
    text = f'page:{max(1, int(page))}'
    offset = max(0.0, min(1.0, float(offset or 0)))
    if offset >= 0.0005:
        text += '@' + f'{offset:.3f}'.rstrip('0').rstrip('.')
    if rects:
        text += '#' + rects_text(rects)
        for other, other_rects in more:
            if other_rects:
                text += f'|{max(1, int(other))}#' + rects_text(other_rects)
    return text


def span_location(parts):
    """The location of rects over one page or several: parts [(page, rects)] in order."""
    parts = [(page, rects) for page, rects in parts if rects]
    if not parts:
        return ''
    return location(parts[0][0], rects=parts[0][1], more=parts[1:])


def parse(text):
    """The Place of a location string, or None when it is not one (a CFI, '')."""
    if not text or not text.startswith('page:'):
        return None
    first, *others = text[5:].split('|')
    body, _hash, rects = first.partition('#')
    number, _at, offset = body.partition('@')
    try:
        page = int(number)
        offset = float(offset) if offset else 0.0
    except ValueError:
        return None
    if page < 1:
        return None
    more, last = [], page
    for other in others:
        number, _hash, other_rects = other.partition('#')
        try:
            other_page = int(number)
        except ValueError:
            continue
        parsed = parse_rects(other_rects)
        if other_page > last and parsed:
            more.append((other_page, parsed))
            last = other_page
    return Place(page, max(0.0, min(1.0, offset)), parse_rects(rects), tuple(more))


def fraction(page, offset, count):
    if count <= 0:
        return 0.0
    return max(0.0, min(1.0, (page - 1 + offset) / count))


def from_fraction(value, count):
    """(page, offset) at a fraction of a book of `count` pages."""
    if count <= 0:
        return 1, 0.0
    position = max(0.0, min(1.0, float(value or 0))) * count
    index = min(count - 1, int(position))
    return index + 1, min(1.0, position - index)


def layout_state(value):
    """A book's saved PDF layout, every key checked (the library's JSON is not trusted):
    {'fit', 'zoom', 'flow', 'rtl', 'cover'}, each None when not set."""
    value = value if isinstance(value, dict) else {}
    fit = value.get('fit')
    zoom = value.get('zoom')
    try:
        zoom = None if zoom is None or isinstance(zoom, bool) else float(zoom)
    except (TypeError, ValueError):
        zoom = None
    if zoom is not None:
        zoom = max(ZOOM_RANGE[0], min(ZOOM_RANGE[1], round(zoom)))
    fit = fit if fit in FITS else None  # with a zoom: that percentage; without: the default
    state = {'fit': fit, 'zoom': zoom if fit is None else None,
             'flow': value.get('flow') if value.get('flow') in FLOWS else None}
    for key in ('rtl', 'cover'):
        state[key] = value[key] if isinstance(value.get(key), bool) else None
    return state


# A sentence ends at . ! ? or … (with closing quotes or brackets after it) before a space,
# or at a paragraph break (an empty line).
_SENTENCE_END = re.compile(r'[.!?\u2026]+[\'"\u2019\u201d)\]]*(?=\s)|\n[^\S\n]*\n')


def sentences(text):
    """(start, end) of each sentence of a page's text (Poppler's page.get_text()), the
    spaces around them left out, in order."""
    text = text or ''
    ends = {match.end() for match in _SENTENCE_END.finditer(text)}
    # A short line without punctuation at its end (a heading, a running head) is a
    # sentence of its own.
    lines = text.split('\n')
    lengths = sorted(len(line.strip()) for line in lines if line.strip())
    typical = lengths[len(lengths) * 3 // 4] if lengths else 0
    position = 0
    for line in lines[:-1]:
        position += len(line) + 1
        stripped = line.strip()
        if stripped and len(stripped) < 0.6 * typical and stripped[-1] not in ',;-':
            ends.add(position)
    spans = []
    start = 0
    for end in sorted(ends):
        spans.append((start, end))
        start = end
    spans.append((start, len(text)))
    out = []
    for begin, end in spans:
        while begin < end and text[begin].isspace():
            begin += 1
        while end > begin and text[end - 1].isspace():
            end -= 1
        if begin < end and any(c.isalnum() for c in text[begin:end]):
            out.append((begin, end))
    return out


def speakable(text):
    """A sentence as it is spoken: words hyphenated at a line's end joined, lines one."""
    return speakable_map(text)[0]


def speakable_map(text):
    """(spoken, positions): speakable(text), and for each of its characters the index in
    `text` it comes from (a run of spaces: its first)."""
    text = text or ''
    spoken, positions = [], []
    space = True  # leading spaces are dropped
    index = 0
    while index < len(text):
        char = text[index]
        if char == '-' and index and text[index - 1].isalnum():
            # a word hyphenated at the end of a line: 'har-\nbour' is 'harbour'
            after = index + 1
            while after < len(text) and text[after] in ' \t\r':
                after += 1
            if after < len(text) and text[after] == '\n':
                after += 1
                while after < len(text) and text[after].isspace():
                    after += 1
                if after < len(text) and text[after].isalnum():
                    index = after
                    continue
        if char.isspace():
            if not space:
                spoken.append(' ')
                positions.append(index)
            space = True
        else:
            spoken.append(char)
            positions.append(index)
            space = False
        index += 1
    if spoken and spoken[-1] == ' ':
        spoken.pop()
        positions.pop()
    return ''.join(spoken), positions


def ends_sentence(text):
    """Whether a page's last sentence ends there (else it goes on on the next page)."""
    text = (text or '').rstrip()
    return not text or text.endswith(('.', '!', '?', '\u2026', '"', "'", '\u201d', '\u2019',
                                      ')', ']', ':'))


_DIRECTION = re.compile(rb'/Direction\s*/R2L')
SCAN_BYTES = 1024 * 1024


def declares_rtl(path):
    """Whether a PDF's viewer preferences say right to left (/Direction /R2L), as far as
    can be seen: Poppler's GObject API does not give the direction, so the file's first and
    last SCAN_BYTES are searched (a catalogue compressed in an object stream is missed)."""
    try:
        with open(path, 'rb') as stream:
            head = stream.read(SCAN_BYTES)
            stream.seek(0, 2)
            size = stream.tell()
            tail = b''
            if size > SCAN_BYTES:
                stream.seek(max(SCAN_BYTES, size - SCAN_BYTES))
                tail = stream.read()
    except OSError:
        return False
    return bool(_DIRECTION.search(head) or _DIRECTION.search(tail))
