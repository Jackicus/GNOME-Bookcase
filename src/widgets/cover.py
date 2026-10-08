# SPDX-License-Identifier: GPL-2.0-or-later
# SPDX-FileCopyrightText: 2026 Jack Tully

"""A book's cover, 2:3, for the grids, the home page's shelves and the details page.

    cover = Cover(width=150)
    cover.set_book(book)                 # a library.Book (None clears it)
    placeholder_colour(title)            # '#2e4a62': the drawn cover's colour for a title

A book with a cover shows its thumbnail, asked of `app.covers.load_thumbnail(book, width,
callback)` (made off the main thread; the callback gets its path on the main loop), decoded
in a thread of this module's, and drawn to fill the
2:3 box, cropped at the edges when its proportions differ; the last few hundred thumbnails
are kept here, so scrolling back is instant. A book without one (or whose image fails to
load) gets a drawn cover, like Apple Books' generated ones: a colour from PALETTE chosen by
the title, a darker spine at the left, the title in a serif face and the author under a short
rule. Rounded corners are drawn here; the shadow is style.css's `.book-cover`. The widget is
an image to assistive technologies, labelled with the title and author.
"""

import collections
import concurrent.futures
import logging
import zlib
from gettext import gettext as _

from gi.repository import Gdk, Gio, GLib, GObject, Graphene, Gsk, Gtk, Pango

log = logging.getLogger(__name__)

# The drawn covers' colours: deep, book-cloth tones that keep white text readable.
PALETTE = (
    '#2e4a62',  # navy
    '#6d2e2e',  # oxblood
    '#2f5d4e',  # forest
    '#7a5a1e',  # ochre
    '#4a3b6b',  # plum
    '#22646b',  # teal
    '#8a4a2f',  # rust
    '#55613a',  # olive
    '#3b4252',  # slate
    '#7b3f5e',  # mulberry
)
RATIO = 1.5  # height / width
RADIUS = 4
CACHE_SIZE = 400
DECODERS = 2

_executor = None
_cache = collections.OrderedDict()  # (book id, cover version, pixel width) -> Gdk.Texture


def placeholder_colour(title):
    """The drawn cover's colour for a title: the same title, the same colour."""
    key = ' '.join((title or '').casefold().split())
    return PALETTE[zlib.crc32(key.encode('utf-8')) % len(PALETTE)]


def cover_height(width):
    return round(width * RATIO)


def _remember(key, texture):
    _cache[key] = texture
    _cache.move_to_end(key)
    while len(_cache) > CACHE_SIZE:
        _cache.popitem(last=False)


def forget(book_id):
    """Drop a book's thumbnails from the cache (its cover changed)."""
    for key in [key for key in _cache if key[0] == book_id]:
        del _cache[key]


def _decoder():
    global _executor
    if _executor is None:
        _executor = concurrent.futures.ThreadPoolExecutor(
            max_workers=DECODERS, thread_name_prefix='bookcase-cover')
    return _executor


def _decode(path, show):
    """Read a thumbnail file into a texture (in a decoding thread), then show it (on the
    main loop)."""
    try:
        texture = Gdk.Texture.new_from_filename(path)
    except GLib.Error as error:
        log.warning('cover thumbnail %s: %s', path, error.message)
        texture = None
    GLib.idle_add(show, texture)


def _rgba(spec, alpha=1.0):
    colour = Gdk.RGBA()
    colour.parse(spec)
    colour.alpha = alpha
    return colour


def _rect(x, y, width, height):
    return Graphene.Rect().init(x, y, width, height)


class Cover(Gtk.Widget):
    __gtype_name__ = 'BookcaseCover'

    def __init__(self, width=150, **kwargs):
        kwargs.setdefault('accessible_role', Gtk.AccessibleRole.IMG)
        super().__init__(**kwargs)
        self.set_layout_manager(None)
        self.add_css_class('book-cover')
        self.set_overflow(Gtk.Overflow.HIDDEN)
        self.set_halign(Gtk.Align.CENTER)
        self.set_valign(Gtk.Align.END)
        self.update_property([Gtk.AccessibleProperty.LABEL], [_('Cover')])
        self._width = width
        self._book = None
        self._texture = None
        self._failed = False
        self._token = 0
        self._layouts = None  # (key, title layout, author layout) of the drawn cover

    # -- size --------------------------------------------------------------------------------

    @GObject.Property(type=int, default=150)
    def width(self):
        return self._width

    @width.setter
    def width(self, value):
        value = max(16, int(value))
        if value != self._width:
            self._width = value
            self._layouts = None
            self.queue_resize()
            if self._book is not None and self._book.has_cover:
                self._load()

    def do_get_request_mode(self):
        return Gtk.SizeRequestMode.CONSTANT_SIZE

    def do_measure(self, orientation, _for_size):
        size = self._width if orientation == Gtk.Orientation.HORIZONTAL else cover_height(
            self._width)
        return size, size, -1, -1

    def do_size_allocate(self, _width, _height, _baseline):
        pass

    # -- the book ----------------------------------------------------------------------------

    @property
    def book(self):
        return self._book

    def set_book(self, book):
        previous = self._book
        self._book = book
        if book is None:
            self._token += 1
            self._texture = None
            self._failed = False
            self.queue_draw()
            return
        label = book.title
        if book.authors:
            # Translators: a cover as a screen reader names it: the title, then the author.
            label = _('{title}, by {author}').format(title=book.title, author=book.authors[0])
        self.update_property([Gtk.AccessibleProperty.LABEL], [label])
        same_cover = (previous is not None and previous.id == book.id
                      and previous.cover_version == book.cover_version
                      and previous.has_cover == book.has_cover)
        if same_cover and (self._texture is not None or self._failed or not book.has_cover):
            if (previous.title, previous.authors) != (book.title, book.authors):
                self._layouts = None
                self.queue_draw()
            return
        self._texture = None
        self._failed = False
        self._layouts = None
        if book.has_cover:
            self._load()
        else:
            self._token += 1
        self.queue_draw()

    def _pixel_width(self):
        return self._width * max(1, self.get_scale_factor())

    def _load(self):
        book = self._book
        self._token += 1
        token = self._token
        key = (book.id, book.cover_version, self._pixel_width())
        cached = _cache.get(key)
        if cached is not None:
            _cache.move_to_end(key)
            self._texture = cached
            self.queue_draw()
            return
        covers = getattr(Gio.Application.get_default(), 'covers', None)
        if covers is None:
            self._failed = True
            return
        ref = self.weak_ref()

        def show(texture):
            if texture is not None:
                _remember(key, texture)
            cover = ref()
            if cover is not None and cover._token == token:
                cover._texture = texture
                cover._failed = texture is None
                cover.queue_draw()
            return False

        def loaded(path):
            if not path:
                show(None)
                return
            cover = ref()
            if cover is None or cover._token != token:
                return  # scrolled past already: not worth decoding
            _decoder().submit(_decode, path, show)

        try:
            covers.load_thumbnail(book, self._pixel_width(), loaded)
        except Exception:
            log.exception('loading the cover of book %s', book.id)
            self._failed = True

    # -- drawing -----------------------------------------------------------------------------

    def do_snapshot(self, snapshot):
        width = self.get_width()
        height = self.get_height()
        if width <= 0 or height <= 0 or self._book is None:
            return
        bounds = _rect(0, 0, width, height)
        rounded = Gsk.RoundedRect()
        rounded.init_from_rect(bounds, RADIUS)
        snapshot.push_rounded_clip(rounded)
        if self._texture is not None:
            self._draw_texture(snapshot, width, height)
        elif not self._book.has_cover or self._failed:
            self._draw_placeholder(snapshot, width, height)
        snapshot.pop()

    def _draw_texture(self, snapshot, width, height):
        texture = self._texture
        tw = texture.get_intrinsic_width() or width
        th = texture.get_intrinsic_height() or height
        scale = max(width / tw, height / th)
        drawn_w, drawn_h = tw * scale, th * scale
        x = (width - drawn_w) / 2
        y = (height - drawn_h) / 2
        rect = _rect(x, y, drawn_w, drawn_h)
        if isinstance(texture, Gdk.Texture):
            snapshot.append_scaled_texture(texture, Gsk.ScalingFilter.TRILINEAR, rect)
        else:
            snapshot.save()
            snapshot.translate(Graphene.Point().init(x, y))
            texture.snapshot(snapshot, drawn_w, drawn_h)
            snapshot.restore()

    def _draw_placeholder(self, snapshot, width, height):
        book = self._book
        snapshot.append_color(_rgba(placeholder_colour(book.title)), _rect(0, 0, width, height))
        # The spine: a shade down the left edge, and a crease beside it.
        spine = width * 0.07
        stops = [Gsk.ColorStop(), Gsk.ColorStop()]
        stops[0].offset, stops[0].color = 0.0, _rgba('#000000', 0.28)
        stops[1].offset, stops[1].color = 1.0, _rgba('#000000', 0.0)
        snapshot.append_linear_gradient(
            _rect(0, 0, spine, height), Graphene.Point().init(0, 0),
            Graphene.Point().init(spine, 0), stops)
        snapshot.append_color(_rgba('#ffffff', 0.12), _rect(spine, 0, max(1, width / 150), height))

        margin = width * 0.12
        inner = width - margin * 1.6 - spine
        title_layout, author_layout = self._text_layouts(inner)
        left = spine + (width - spine - inner) / 2
        y = height * 0.13
        snapshot.save()
        snapshot.translate(Graphene.Point().init(left, y))
        snapshot.append_layout(title_layout, _rgba('#ffffff', 0.96))
        snapshot.restore()
        _ink, logical = title_layout.get_pixel_extents()
        rule_y = y + logical.height + height * 0.05
        rule_w = width * 0.18
        snapshot.append_color(_rgba('#ffffff', 0.55),
                              _rect(left + (inner - rule_w) / 2, rule_y, rule_w,
                                    max(1, width / 120)))
        if author_layout is not None:
            _ink, author_logical = author_layout.get_pixel_extents()
            author_y = max(rule_y + height * 0.05, height - margin - author_logical.height)
            snapshot.save()
            snapshot.translate(Graphene.Point().init(left, author_y))
            snapshot.append_layout(author_layout, _rgba('#ffffff', 0.78))
            snapshot.restore()

    def _text_layouts(self, inner):
        book = self._book
        key = (book.title, book.authors, self._width)
        if self._layouts is not None and self._layouts[0] == key:
            return self._layouts[1], self._layouts[2]
        title = self._layout(book.title or _('Untitled'), 'Serif Bold', self._width / 9.5,
                             inner, lines=5)
        author = None
        if book.authors:
            author = self._layout(book.authors[0], 'Serif Italic', self._width / 14, inner,
                                  lines=2)
        self._layouts = (key, title, author)
        return title, author

    def _layout(self, text, face, size, width, lines):
        layout = self.create_pango_layout(text)
        font = Pango.FontDescription.from_string(face)
        font.set_absolute_size(max(6.0, size) * Pango.SCALE)
        layout.set_font_description(font)
        layout.set_width(int(width * Pango.SCALE))
        layout.set_wrap(Pango.WrapMode.WORD_CHAR)
        layout.set_alignment(Pango.Alignment.CENTER)
        layout.set_ellipsize(Pango.EllipsizeMode.END)
        layout.set_height(-lines)
        return layout
