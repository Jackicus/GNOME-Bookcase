# SPDX-License-Identifier: GPL-2.0-or-later
# SPDX-FileCopyrightText: 2026 Jack Tully

"""PDF: metadata and a cover drawn from the first page, through Poppler when it is there.

    read(path) -> BookInfo
    AVAILABLE                  whether Poppler's typelib (and pycairo, for the cover) loaded

Without Poppler a PDF is known by its file name alone. The document's title is taken
unless it looks like a word processor's leftover ('Microsoft Word - x.doc', 'untitled');
the cover is page 1 rendered on white, COVER_HEIGHT pixels tall, as PNG bytes. A PDF that
Poppler cannot open (encrypted, damaged) still counts as a book, known by its name.
"""

import io
import logging
import re

from . import BookInfo, FormatError, split_authors

log = logging.getLogger(__name__)

COVER_HEIGHT = 600

try:
    import gi
    gi.require_version('Poppler', '0.18')
    from gi.repository import Gio, GLib, Poppler
    AVAILABLE = True
except (ImportError, ValueError):
    AVAILABLE = False

try:
    import cairo
except ImportError:
    cairo = None

_JUNK_TITLE = re.compile(
    r'^(untitled|title|document\d*|microsoft (word|powerpoint) - .*|.*\.(docx?|rtf|odt|tex|'
    r'dvi|ps|indd|qxd|pdf|html?|txt)|\s*)$', re.I)


def read(path):
    with open(path, 'rb') as file:
        head = file.read(1024)
    if b'%PDF-' not in head:
        raise FormatError('Not a PDF')
    info = BookInfo()
    if not AVAILABLE:
        return info
    try:
        document = Poppler.Document.new_from_gfile(Gio.File.new_for_path(path), None, None)
    except GLib.Error as error:
        log.info('Poppler cannot open %s: %s', path, error.message)
        return info
    title = (document.props.title or '').strip()
    if not _JUNK_TITLE.match(title):
        info.title = title
    info.authors = split_authors(document.props.author or '')
    info.description = (document.props.subject or '').strip()
    info.tags = [tag.strip() for tag in re.split(r'[,;]', document.props.keywords or '')
                 if tag.strip()]
    info.cover = _render_cover(document)
    return info


def _render_cover(document):
    if cairo is None or document.get_n_pages() < 1:
        return None
    try:
        page = document.get_page(0)
        width, height = page.get_size()
        if width <= 0 or height <= 0:
            return None
        scale = COVER_HEIGHT / height
        surface = cairo.ImageSurface(cairo.FORMAT_RGB24, max(1, int(width * scale)),
                                     COVER_HEIGHT)
        context = cairo.Context(surface)
        context.set_source_rgb(1, 1, 1)
        context.paint()
        context.scale(scale, scale)
        page.render(context)
        output = io.BytesIO()
        surface.write_to_png(output)
        return output.getvalue()
    except Exception:
        log.info('Cannot draw the cover of a PDF', exc_info=True)
        return None
