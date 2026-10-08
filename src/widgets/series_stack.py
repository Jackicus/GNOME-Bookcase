# SPDX-License-Identifier: GPL-2.0-or-later
# SPDX-FileCopyrightText: 2026 Jack Tully

"""A series as one tile of a grid (All Books' Group Series): its first books' covers fanned,
the series' name, its count, and how far through it the reader is.

    item = SeriesItem(name, books)       # books in series order; a BookItem whose `book` is
                                         # the first, so a grid holds it among BookItems
    item.key, item.name, item.books, item.ids, item.fraction
    item.set_books(books)                # newer books (notify::book when they changed)
    collapse(books)                      # [Book or (name, [Book])]: each series' books
                                         # gathered where its first one comes
    tile = SeriesStackTile(size)         # size: the page's CoverSize, which the tile fills
    tile.set_item(item)                  # None unbinds
    series_fraction(books)               # how much of the series is read, 0 to 1

The stack takes the place a book's cover would (the same width and height), so a row of
books and stacks lines up: the first book's cover in front, the next two behind it, each a
little smaller and further right.
"""

from gettext import gettext as _
from gettext import ngettext

from gi.repository import Gtk, Pango

from .book_item import BookItem
from .cover import Cover, cover_height
from .util import connect_weak

SHOWN = 3  # covers in a stack


def series_fraction(books):
    """How far through a series its reader is: finished books count whole, the one being
    read by its progress."""
    if not books:
        return 0.0
    done = 0.0
    for book in books:
        if book.status == 'finished':
            done += 1
        elif book.progress:
            done += min(1.0, book.progress)
    return done / len(books)


def collapse(books):
    """The books with each series gathered where its first book (in the given order)
    comes: [Book, or (series name, [Book] in series order)]. A series of one stays a book."""
    members = {}
    for book in books:
        if book.series:
            members.setdefault(book.series, []).append(book)
    shown = []
    placed = set()
    for book in books:
        name = book.series
        if not name or len(members[name]) == 1:
            shown.append(book)
        elif name not in placed:
            placed.add(name)
            shown.append((name, sorted(members[name],
                                       key=lambda each: (each.series_index, each.sort_title))))
    return shown


class SeriesItem(BookItem):
    __gtype_name__ = 'BookcaseSeriesItem'

    def __init__(self, name, books):
        super().__init__(books[0])
        self.name = name
        self.key = ('series', name)
        self.books = list(books)

    @property
    def ids(self):
        return [book.id for book in self.books]

    @property
    def fraction(self):
        return series_fraction(self.books)

    def set_books(self, books):
        """Newer books for the stack; notify::book when anything shown may differ."""
        books = list(books)
        if books == self.books:
            return False
        self.books = books
        self.id = books[0].id
        self._book = books[0]
        self.notify('book')
        return True

    def __repr__(self):
        return f'<SeriesItem {self.name!r} {len(self.books)}>'


class SeriesStackTile(Gtk.Box):
    """See the module."""

    __gtype_name__ = 'BookcaseSeriesStackTile'

    def __init__(self, size=None, width=150):
        super().__init__(orientation=Gtk.Orientation.VERTICAL, spacing=4,
                         halign=Gtk.Align.CENTER, valign=Gtk.Align.START)
        self.add_css_class('book-tile')
        self.add_css_class('series-stack')
        self.item = None
        self._handler = None
        self.fixed = Gtk.Fixed(accessible_role=Gtk.AccessibleRole.PRESENTATION,
                               halign=Gtk.Align.CENTER)
        self.covers = []
        self.front = Gtk.Overlay()
        for depth in range(SHOWN - 1, -1, -1):
            cover = Cover(width=width)
            if depth:
                cover.add_css_class('behind')
                self.fixed.put(cover, 0, 0)
            else:
                self.front.set_child(cover)
                self.fixed.put(self.front, 0, 0)
            self.covers.insert(0, cover)  # the front one first
        self.progress = Gtk.ProgressBar(valign=Gtk.Align.END, visible=False,
                                        accessible_role=Gtk.AccessibleRole.PRESENTATION)
        self.progress.add_css_class('cover-progress')
        self.front.add_overlay(self.progress)
        self.mark = Gtk.Image(icon_name='object-select-symbolic', halign=Gtk.Align.END,
                              valign=Gtk.Align.END, visible=False, pixel_size=12,
                              tooltip_text=_('Finished'))
        self.mark.add_css_class('cover-mark')
        self.front.add_overlay(self.mark)
        self.append(self.fixed)

        self.title = Gtk.Label(xalign=0, yalign=0, margin_top=6, wrap=True,
                               wrap_mode=Pango.WrapMode.WORD_CHAR, lines=2,
                               ellipsize=Pango.EllipsizeMode.END, max_width_chars=1,
                               width_chars=1)
        self.title.add_css_class('book-tile-title')
        _width, two_lines = self.title.create_pango_layout('Ag\nAg').get_pixel_size()
        self.title.set_size_request(-1, two_lines)
        self.append(self.title)
        self.subtitle = Gtk.Label(xalign=0, ellipsize=Pango.EllipsizeMode.END,
                                  max_width_chars=1, width_chars=1)
        self.subtitle.add_css_class('caption')
        self.subtitle.add_css_class('dimmed')
        self.append(self.subtitle)

        self._width = 0
        if size is not None:
            connect_weak(size, 'notify::width', self._on_size_changed)
            width = size.props.width
        self.set_width(width)

    def _on_size_changed(self, size, _pspec):
        self.set_width(size.props.width)

    def set_width(self, width):
        """Fit the stack into a cover of `width` (a book tile's)."""
        if width == self._width:
            return
        self._width = width
        height = cover_height(width)
        self.fixed.set_size_request(width, height)
        front = round(width / 1.3)
        offset = max(2, round(front * 0.12))
        shrink = max(2, round(front * 0.07))
        for depth, cover in enumerate(self.covers):
            cover_width = front - depth * shrink
            cover.props.width = cover_width
            widget = self.front if depth == 0 else cover
            self.fixed.move(widget, depth * (offset + shrink), height - cover_height(cover_width))

    # -- binding -----------------------------------------------------------------------------

    def set_item(self, item):
        if self.item is not None and self._handler is not None:
            self.item.disconnect(self._handler)
        self.item = item
        self._handler = None
        if item is None:
            for cover in self.covers:
                cover.set_book(None)
            return
        self._handler = item.connect('notify::book', self._on_changed)
        self._show_item()

    def _on_changed(self, _item, _pspec):
        self._show_item()

    def _show_item(self):
        item = self.item
        books = item.books
        for index, cover in enumerate(self.covers):
            book = books[index] if index < len(books) else None
            cover.set_book(book)
            cover.set_visible(book is not None)
        self.title.set_text(item.name)
        count = ngettext('{n} book', '{n} books', len(books)).format(n=f'{len(books):n}')
        self.subtitle.set_text(count)
        fraction = item.fraction
        finished = all(book.status == 'finished' for book in books)
        self.progress.set_visible(0 < fraction and not finished)
        self.progress.set_fraction(max(0.03, fraction))
        self.mark.set_visible(finished)
        # Translators: a series' stack in the grid: "The Saltmarsh Chronicles · 5 books".
        self.set_tooltip_text(_('{series} · {count}').format(series=item.name, count=count))
