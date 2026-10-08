# SPDX-License-Identifier: GPL-2.0-or-later
# SPDX-FileCopyrightText: 2026 Jack Tully

"""A book in a grid or a home page shelf: its cover (with a progress bar while it is being
read, a series number on a series' page, a mark when it is finished or its file is missing),
the title and a line under it (the author, or what the caller says).

    tile = BookTile(size)                # size: a CoverSize the page shares (its width)
    tile.set_item(item)                  # a BookItem: follows its notify::book; None unbinds
    tile.set_book(book, subtitle=None)   # or a Book directly (the home page's rows)
    tile.show_series_index = True        # the number in the series, on the cover
    progress_text(book, minutes_left=None)   # '45%', '45% · 2 h left', 'Finished'
"""

from gettext import gettext as _

from gi.repository import GObject, Gtk, Pango

from .cover import Cover


class CoverSize(GObject.Object):
    """The width of a page's covers, which its tiles follow (a breakpoint changes it)."""

    __gtype_name__ = 'BookcaseCoverSize'

    width = GObject.Property(type=int, default=150)


def format_index(index):
    """A series number as people write it: 2, 2.5."""
    if not index:
        return ''
    return f'{index:g}'


def duration_text(minutes):
    """A reading time left: '5 min', '2 h', '2 h 30 min'."""
    minutes = max(1, round(minutes))
    if minutes < 60:
        # Translators: time left to read, in minutes ("5 min").
        return _('{m} min').format(m=minutes)
    hours, rest = divmod(minutes, 60)
    if hours >= 10 or rest < 5:
        # Translators: time left to read, in hours ("2 h").
        return _('{h} h').format(h=hours if rest < 30 or hours >= 10 else hours + 1)
    rest = rest // 5 * 5
    # Translators: time left to read ("2 h 30 min").
    return _('{h} h {m} min').format(h=hours, m=rest)


def progress_text(book, minutes_left=None):
    """How far into a book its reader is: '45%', '45% · 2 h left', 'Finished', 'New'."""
    if book.status == 'finished':
        return _('Finished')
    if book.status != 'reading' and not book.progress:
        return _('New')
    percent = max(1, min(99, round(book.progress * 100))) if book.progress else 0
    # Translators: a percentage ("45%").
    text = _('{percent}%').format(percent=percent)
    if minutes_left:
        # Translators: progress and time left to read: "45% · 2 h left".
        text = _('{progress} · {time} left').format(progress=text,
                                                      time=duration_text(minutes_left))
    return text


class BookTile(Gtk.Box):
    __gtype_name__ = 'BookcaseBookTile'

    def __init__(self, size=None, width=None):
        super().__init__(orientation=Gtk.Orientation.VERTICAL, spacing=4,
                         halign=Gtk.Align.CENTER, valign=Gtk.Align.START)
        self.add_css_class('book-tile')
        self.show_series_index = False
        self._item = None
        self._handler = None
        self.cover = Cover(width=width or (size.props.width if size is not None else 150))
        if size is not None:
            size.bind_property('width', self.cover, 'width', GObject.BindingFlags.SYNC_CREATE)

        overlay = Gtk.Overlay(halign=Gtk.Align.CENTER)
        overlay.set_child(self.cover)
        self.progress = Gtk.ProgressBar(valign=Gtk.Align.END, visible=False)
        self.progress.update_property([Gtk.AccessibleProperty.LABEL], [_('Reading Progress')])
        self.progress.add_css_class('cover-progress')
        overlay.add_overlay(self.progress)
        self.badge = Gtk.Label(halign=Gtk.Align.START, valign=Gtk.Align.START, visible=False)
        self.badge.add_css_class('cover-badge')
        self.badge.add_css_class('numeric')
        overlay.add_overlay(self.badge)
        self.mark = Gtk.Image(halign=Gtk.Align.END, valign=Gtk.Align.END, visible=False,
                              pixel_size=12)
        self.mark.add_css_class('cover-mark')
        overlay.add_overlay(self.mark)
        self.append(overlay)

        self.title = Gtk.Label(xalign=0, yalign=0, margin_top=6, wrap=True,
                               wrap_mode=Pango.WrapMode.WORD_CHAR, lines=2,
                               ellipsize=Pango.EllipsizeMode.END, max_width_chars=1,
                               width_chars=1)
        self.title.add_css_class('book-tile-title')
        # Two lines whatever the title, so the tiles of a row line up (and the minimum
        # height is the natural one: a sideways-scrolling row gets the minimum).
        _width, two_lines = self.title.create_pango_layout('Ag\nAg').get_pixel_size()
        self.title.set_size_request(-1, two_lines)
        self.append(self.title)
        self.subtitle = Gtk.Label(xalign=0, ellipsize=Pango.EllipsizeMode.END,
                                  max_width_chars=1, width_chars=1)
        self.subtitle.add_css_class('caption')
        self.subtitle.add_css_class('dimmed')
        self.append(self.subtitle)

    # -- binding -----------------------------------------------------------------------------

    def set_item(self, item):
        if self._item is not None and self._handler is not None:
            self._item.disconnect(self._handler)
        self._item = item
        self._handler = None
        if item is None:
            self.cover.set_book(None)
            return
        self._handler = item.connect('notify::book', self._on_book_changed)
        self.set_book(item.book)

    def _on_book_changed(self, item, _pspec):
        self.set_book(item.book)

    def set_book(self, book, subtitle=None):
        self.cover.set_book(book)
        self.title.set_text(book.title)
        self.subtitle.set_text(subtitle if subtitle is not None else book.author)
        self.subtitle.set_visible(bool(self.subtitle.get_text()))
        reading = book.status == 'reading' or (book.status != 'finished' and book.progress > 0)
        self.progress.set_visible(reading)
        self.progress.set_fraction(max(0.03, book.progress) if reading else 0)
        if reading:
            self.progress.update_property([Gtk.AccessibleProperty.VALUE_TEXT],
                                          [progress_text(book)])
        index = format_index(book.series_index) if self.show_series_index else ''
        self.badge.set_text(index)
        self.badge.set_visible(bool(index))
        if book.missing:
            self.mark.set_from_icon_name('dialog-warning-symbolic')
            self.mark.set_tooltip_text(_('The book’s file cannot be found'))
            self.mark.set_visible(True)
            self.mark.add_css_class('missing')
        elif book.status == 'finished':
            self.mark.set_from_icon_name('object-select-symbolic')
            self.mark.set_tooltip_text(_('Finished'))
            self.mark.set_visible(True)
            self.mark.remove_css_class('missing')
        else:
            self.mark.set_visible(False)
        tooltip = book.title
        if book.authors:
            # Translators: a book's title, then its author on the next line.
            tooltip = _('{title}\n{author}').format(title=book.title, author=book.author)
        self.set_tooltip_text(tooltip)

