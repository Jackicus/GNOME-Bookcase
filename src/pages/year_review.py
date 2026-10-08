# SPDX-License-Identifier: GPL-2.0-or-later
# SPDX-FileCopyrightText: 2026 Jack Tully

"""Year in Review: a finished year's reading on one page, pushed from Statistics (a past
year's card), and saved as a picture to share.

    page = YearReviewPage(2025, library=None)   # the app's library unless given (tests)
    page.review                                 # the stats.YearReview shown
    page.poster                                 # the part saved as a picture
    page.save_image(path, scale=2)              # the poster as a PNG; True when written
    page.save_as()                              # Save as Image… (Gtk.FileDialog, then save)

The poster (a card, drawn as the page draws it, in the light or dark style shown) holds the
year, four numbers (books finished, the pages in them, hours read, the longest streak of
days), the favourite author and tag (the most books finished, then the most time), the
month read most, the books finished each month as bars, and the covers of the books
finished, in the order they were (a click on one opens its details). The picture is the
poster's render nodes (Gtk.WidgetPaintable) drawn by the window's Gsk renderer into a
texture, at twice the size for a sharp image, and written with Gdk.Texture.save_to_png.
"""

import datetime
import logging
from gettext import gettext as _
from gettext import ngettext

from gi.repository import Adw, Gio, GLib, Graphene, Gtk, Pango

from .. import stats
from ..widgets import charts
from ..widgets.cover import Cover
from ..widgets.util import connect_weak
from . import app
from .stats import Tile, hours_text, streak_text

log = logging.getLogger(__name__)

COVER_WIDTH = 60
COVERS_SHOWN = 60
POSTER_WIDTH = 560


class YearReviewPage(Adw.NavigationPage):
    __gtype_name__ = 'BookcaseYearReviewPage'

    def __init__(self, year, library=None):
        super().__init__(title=_('{year} in Review').format(year=year))
        self.year = year
        self.library = library if library is not None else app().library
        self.review = stats.year_review(self.library, year)

        header = Adw.HeaderBar()
        self.save_button = Gtk.Button(icon_name='document-save-as-symbolic',
                                      tooltip_text=_('Save as Image…'))
        connect_weak(self.save_button, 'clicked', self._on_save_clicked)
        header.pack_end(self.save_button)

        self.poster = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=18)
        self.poster.add_css_class('year-review')
        self._fill()
        clamp = Adw.Clamp(maximum_size=POSTER_WIDTH, tightening_threshold=POSTER_WIDTH,
                          child=self.poster, margin_top=12, margin_bottom=24,
                          margin_start=12, margin_end=12)
        scroller = Gtk.ScrolledWindow(child=clamp, hscrollbar_policy=Gtk.PolicyType.NEVER,
                                      vexpand=True)
        view = Adw.ToolbarView(content=scroller)
        view.add_top_bar(header)
        self.set_child(view)

    # -- the poster --------------------------------------------------------------------------

    def _fill(self):
        review = self.review
        books = len(review.finished)
        top = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=2)
        year = Gtk.Label(label=str(self.year), xalign=0)
        year.add_css_class('year-review-year')
        year.add_css_class('numeric')
        top.append(year)
        # Translators: the heading of a year's review, under the year ("2025").
        subtitle = Gtk.Label(label=_('A Year in Books'), xalign=0,
                             accessible_role=Gtk.AccessibleRole.HEADING)
        subtitle.add_css_class('title-3')
        top.append(subtitle)
        self.poster.append(top)

        tiles = Gtk.Grid(column_spacing=12, row_spacing=12, column_homogeneous=True)
        self.tile_books = Tile(ngettext('Book finished', 'Books finished', books))
        self.tile_books.set(f'{books:n}')
        self.tile_pages = Tile(_('Pages in them'))
        self.tile_pages.set(f'{review.pages:,}')
        self.tile_hours = Tile(_('Read'))
        self.tile_hours.set(hours_text(review.seconds))
        self.tile_streak = Tile(_('Longest streak'))
        self.tile_streak.set(streak_text(review.longest_streak))
        for index, tile in enumerate((self.tile_books, self.tile_pages, self.tile_hours,
                                      self.tile_streak)):
            tiles.attach(tile, index % 2, index // 2, 1, 1)
        self.poster.append(tiles)

        facts = Gtk.ListBox(selection_mode=Gtk.SelectionMode.NONE)
        facts.add_css_class('boxed-list')
        self.facts = facts
        if review.author:
            facts.append(self._fact('avatar-default-symbolic', _('Favourite Author'),
                                    review.author))
        if review.genre:
            facts.append(self._fact('tag-symbolic', _('Favourite Genre'), review.genre))
        if review.best_month is not None:
            month = charts.date_label(review.best_month, '%B')
            facts.append(self._fact('x-office-calendar-symbolic', _('The Month You Read Most'),
                                    # Translators: a month and the time read in it ("March · 12 h").
                                    _('{month} · {time}').format(
                                        month=month,
                                        time=hours_text(review.best_month_seconds))))
        facts.append(self._fact('object-select-symbolic', _('Days You Read'),
                                ngettext('{n} day', '{n} days', review.days).format(
                                    n=review.days)))
        self.poster.append(facts)

        if books:
            self.poster.append(self._heading(_('Books Finished Each Month')))
            months = [datetime.date(self.year, month, 1) for month in range(1, 13)]
            chart = charts.BarChart()
            chart.set_data(review.finished_by_month,
                           labels=[charts.date_label(month, '%b')[:1] for month in months],
                           tooltips=[f'{charts.date_label(month, "%B")}: {count}' for month, count
                                     in zip(months, review.finished_by_month, strict=True)],
                           highlight=max(range(12),
                                         key=lambda month: review.finished_by_month[month]),
                           format=charts.format_count)
            chart.set_description(_('Books finished in each month of {year}: {list}').format(
                year=self.year, list=', '.join(
                    f'{charts.date_label(month, "%B")} {count}' for month, count
                    in zip(months, review.finished_by_month, strict=True))))
            box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
            box.add_css_class('card')
            box.add_css_class('stats-card')
            box.append(chart)
            self.poster.append(box)

            self.poster.append(self._heading(_('Your Books of {year}').format(year=self.year)))
            covers = Adw.WrapBox(child_spacing=6, line_spacing=6)
            self.covers = covers
            for book_id, _when in review.finished[:COVERS_SHOWN]:
                book = self.library.book(book_id)
                if book is None:
                    continue
                cover = Cover(width=COVER_WIDTH)
                cover.set_book(book)
                button = Gtk.Button(child=cover, tooltip_text=book.title)
                button.add_css_class('flat')
                button.add_css_class('stats-cover')
                button.book_id = book_id
                button.update_property([Gtk.AccessibleProperty.LABEL],
                                       [f'{book.title}, {book.author}'])
                connect_weak(button, 'clicked', self._on_cover_clicked)
                covers.append(button)
            self.poster.append(covers)
            more = len(review.finished) - COVERS_SHOWN
            if more > 0:
                label = Gtk.Label(label=ngettext('and {n} more', 'and {n} more', more).format(
                    n=more), xalign=0)
                label.add_css_class('dimmed')
                self.poster.append(label)
        else:
            empty = Gtk.Label(label=_('No books finished this year'), xalign=0)
            empty.add_css_class('dimmed')
            self.poster.append(empty)

        footer = Gtk.Label(label='Bookcase', xalign=1)
        footer.add_css_class('caption')
        footer.add_css_class('dimmed')
        self.poster.append(footer)

    def _heading(self, text):
        label = Gtk.Label(label=text, xalign=0, accessible_role=Gtk.AccessibleRole.HEADING,
                          margin_top=6)
        label.add_css_class('heading')
        return label

    def _fact(self, icon, title, value):
        row = Adw.ActionRow(title=title, use_markup=False, activatable=False)
        row.add_prefix(Gtk.Image(icon_name=icon, accessible_role=Gtk.AccessibleRole.PRESENTATION))
        label = Gtk.Label(label=value, xalign=1, wrap=True, max_width_chars=20,
                          justify=Gtk.Justification.RIGHT, wrap_mode=Pango.WrapMode.WORD_CHAR)
        label.add_css_class('heading')
        row.add_suffix(label)
        row.value = label
        return row

    def _on_cover_clicked(self, button):
        window = self.get_root()
        if window is not None and hasattr(window, 'show_book'):
            window.show_book(button.book_id)

    # -- the picture -------------------------------------------------------------------------

    def save_image(self, path, scale=2):
        """Write the poster, as shown, to `path` as a PNG; False when it cannot be drawn
        (not shown yet)."""
        widget = self.poster
        width, height = widget.get_width(), widget.get_height()
        native = widget.get_native()
        if width <= 0 or height <= 0 or native is None:
            return False
        snapshot = Gtk.Snapshot()
        snapshot.scale(scale, scale)
        Gtk.WidgetPaintable.new(widget).snapshot(snapshot, width, height)
        node = snapshot.to_node()
        if node is None:
            return False
        bounds = Graphene.Rect().init(0, 0, width * scale, height * scale)
        texture = native.get_renderer().render_texture(node, bounds)
        return texture.save_to_png(str(path))

    def _on_save_clicked(self, _button):
        self.save_as()

    def save_as(self):
        dialog = Gtk.FileDialog(title=_('Save Year in Review'),
                                # Translators: the file name of a saved Year in Review.
                                initial_name=_('{year} in Books.png').format(year=self.year))
        filters = Gio.ListStore(item_type=Gtk.FileFilter)
        png = Gtk.FileFilter(name=_('PNG Images'))
        png.add_mime_type('image/png')
        filters.append(png)
        dialog.set_filters(filters)
        ref = self.weak_ref()

        def chosen(dialog, result):
            page = ref()
            try:
                file = dialog.save_finish(result)
            except GLib.Error:
                return  # dismissed
            if page is None or file is None or file.get_path() is None:
                return
            try:
                written = page.save_image(file.get_path())
            except GLib.Error as error:
                log.warning('saving the year in review: %s', error)
                written = False
            if written:
                app().toast(_('Saved “{name}”').format(name=file.get_basename()))
            else:
                app().toast(_('Could not save the image'))

        dialog.save(self.get_root(), None, chosen)
