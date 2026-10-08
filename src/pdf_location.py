# SPDX-License-Identifier: GPL-2.0-or-later
# SPDX-FileCopyrightText: 2026 Jack Tully

"""Places in a PDF: the location strings the reader saves for a book shown by pages
(widgets/pdf_view.py), where an EPUB has a CFI.

    location(page, offset=0.0, rects=())  # 'page:12', 'page:12@0.25', 'page:12#10,20.5,90,32'
    parse(text) -> Place or None          # Place(page, offset, rects)
    fraction(page, offset, count)         # the place as a fraction of the book, 0-1
    from_fraction(fraction, count)        # (page, offset)
    rects_text(rects) / parse_rects(text)

`page` counts from 1 (the PDF's own order, not its printed labels). `offset` is how far down
that page the top of the view was, 0 (its top) to 1. `rects` are a highlight's or search
match's boxes on the page, (x0, y0, x1, y1) in PDF points from the page's top-left corner,
rounded to a tenth: 'page:3#72,90.5,300,104;72,106,180,119.5'. Progress is saved as
library.set_progress(book, fraction(...), location(page, offset)), and opening goes back to
the location (the fraction when the location does not parse). Bookmarks are a page's
location without offset; highlights carry their rects.
"""

import dataclasses


@dataclasses.dataclass(frozen=True)
class Place:
    page: int
    offset: float = 0.0
    rects: tuple = ()


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


def location(page, offset=0.0, rects=()):
    text = f'page:{max(1, int(page))}'
    offset = max(0.0, min(1.0, float(offset or 0)))
    if offset >= 0.0005:
        text += '@' + f'{offset:.3f}'.rstrip('0').rstrip('.')
    if rects:
        text += '#' + rects_text(rects)
    return text


def parse(text):
    """The Place of a location string, or None when it is not one (a CFI, '')."""
    if not text or not text.startswith('page:'):
        return None
    body, _hash, rects = text[5:].partition('#')
    number, _at, offset = body.partition('@')
    try:
        page = int(number)
        offset = float(offset) if offset else 0.0
    except ValueError:
        return None
    if page < 1:
        return None
    return Place(page, max(0.0, min(1.0, offset)), parse_rects(rects))


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
