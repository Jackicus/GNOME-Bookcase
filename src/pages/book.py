# SPDX-License-Identifier: GPL-2.0-or-later
# SPDX-FileCopyrightText: 2026 Jack Tully

"""A book's details page, pushed (window.show_book).

    page = BookPage(book_id)           # page.book_id
    page.refresh()                     # from the library (also on map and on `changed`)

The cover beside (above, when narrow) the title; the authors and the series as links to
their books (window.show_books); the rating, which a click changes (an undo step); the
reading state and progress (with the time left, stats.py); Read (Continue Reading, Read
Again) and Mark as Finished (or Unread). Under them: the description (its HTML as safe Pango
markup, widgets/markup.py), the tags (each opens its books), Details (publisher, published,
language, identifiers: an ISBN opens Open Library, added, the shelves), Reading (highlights
and bookmarks, which open the reader at its annotations) and Files (each format's size and
path, with Show in Files). The header has Edit Details and the book menu (pages/actions.py).
A book that leaves the library takes its page with it.
"""

import datetime
import logging
from gettext import gettext as _
from gettext import ngettext

from gi.repository import Adw, Gio, GLib, Gtk

from .. import stats
from ..widgets.book_tile import duration_text, format_index
from ..widgets.cover import Cover
from ..widgets.markup import html_to_markup
from ..widgets.rating import Rating, rating_text
from ..widgets.util import connect_weak
from . import PageListener, app
from .actions import BookActions, book_menu

log = logging.getLogger(__name__)

CHANGE_KINDS = ('books', 'files', 'shelves', 'progress', 'annotations')
COVER_WIDTH = 220

LANGUAGES = {
    'ar': 'العربية', 'ca': 'Català', 'cs': 'Čeština', 'da': 'Dansk', 'de': 'Deutsch',
    'el': 'Ελληνικά', 'en': 'English', 'eo': 'Esperanto', 'es': 'Español', 'et': 'Eesti',
    'eu': 'Euskara', 'fa': 'فارسی', 'fi': 'Suomi', 'fr': 'Français', 'ga': 'Gaeilge',
    'he': 'עברית', 'hi': 'हिन्दी', 'hu': 'Magyar', 'id': 'Bahasa Indonesia', 'is': 'Íslenska',
    'it': 'Italiano', 'ja': '日本語', 'ko': '한국어', 'la': 'Latina', 'lt': 'Lietuvių',
    'nl': 'Nederlands', 'no': 'Norsk', 'nb': 'Norsk bokmål', 'pl': 'Polski',
    'pt': 'Português', 'ro': 'Română', 'ru': 'Русский', 'sk': 'Slovenčina', 'sl': 'Slovenščina',
    'sr': 'Српски', 'sv': 'Svenska', 'th': 'ไทย', 'tr': 'Türkçe', 'uk': 'Українська',
    'vi': 'Tiếng Việt', 'zh': '中文',
}

# Identifier scheme -> (label, address with {value}).
IDENTIFIERS = {
    'isbn': ('ISBN', 'https://openlibrary.org/isbn/{value}'),
    'openlibrary': ('Open Library', 'https://openlibrary.org/books/{value}'),
    'google': ('Google Books', 'https://books.google.com/books?id={value}'),
    'goodreads': ('Goodreads', 'https://www.goodreads.com/book/show/{value}'),
    'amazon': ('Amazon', 'https://www.amazon.com/dp/{value}'),
    'asin': ('ASIN', 'https://www.amazon.com/dp/{value}'),
    'doi': ('DOI', 'https://doi.org/{value}'),
}


def language_name(code):
    """'Deutsch' for 'de' (each language in its own name), else the code as given."""
    if not code:
        return ''
    base = code.lower().replace('_', '-').split('-')[0]
    return LANGUAGES.get(base, code)


def published_text(published):
    """'2019', 'March 2019', '4 March 2019' for 'YYYY', 'YYYY-MM', 'YYYY-MM-DD'."""
    parts = (published or '').split('-')
    try:
        if len(parts) >= 3:
            # Translators: a full publication date (strftime): "4 March 2019".
            return datetime.date(int(parts[0]), int(parts[1]), int(parts[2][:2])).strftime(
                _('%-d %B %Y'))
        if len(parts) == 2:
            # Translators: a publication month (strftime): "March 2019".
            return datetime.date(int(parts[0]), int(parts[1]), 1).strftime(_('%B %Y'))
    except ValueError:
        pass
    return parts[0] if parts and parts[0] else (published or '')


def size_text(size):
    return GLib.format_size(size) if size else ''


def series_markup(book):
    """'Book 2 of <a>The Glass Road</a>', or 'Part of <a>…</a>' without a number."""
    if not book.series:
        return ''
    link = f'<a href="series:">{GLib.markup_escape_text(book.series)}</a>'
    index = format_index(book.series_index)
    if index:
        # Translators: a book's place in its series; {series} is the series' name (a link).
        return _('Book {index} of {series}').format(index=index, series=link)
    # Translators: a book's series, when it has no number in it; {series} is a link.
    return _('Part of {series}').format(series=link)


def authors_markup(book):
    if not book.authors:
        return GLib.markup_escape_text(_('Unknown Author'))
    links = [f'<a href="author:{index}">{GLib.markup_escape_text(name)}</a>'
             for index, name in enumerate(book.authors)]
    if len(links) == 1:
        return links[0]
    # Translators: the list of a book's authors: "Ada Lark, Ben Ross and Cy Moor".
    return _('{names} and {last}').format(names=', '.join(links[:-1]), last=links[-1])


def status_text(book, minutes_left=None):
    if book.status == 'finished':
        return _('Finished')
    if book.status == 'reading' or book.progress:
        percent = max(1, min(99, round(book.progress * 100))) if book.progress else 0
        text = _('{percent}% read').format(percent=percent)
        if minutes_left:
            text = _('{progress} · {time} left').format(progress=text,
                                                          time=duration_text(minutes_left))
        return text
    return _('Not started')


def annotations_text(annotations):
    highlights = sum(1 for annotation in annotations if annotation.kind == 'highlight')
    bookmarks = len(annotations) - highlights
    parts = []
    if highlights:
        parts.append(ngettext('{n} highlight', '{n} highlights', highlights).format(
            n=highlights))
    if bookmarks:
        parts.append(ngettext('{n} bookmark', '{n} bookmarks', bookmarks).format(n=bookmarks))
    return ' · '.join(parts) or _('None yet')


@Gtk.Template(resource_path='/io/github/jackicus/Bookcase/book.ui')
class BookPage(Adw.NavigationPage):
    __gtype_name__ = 'BookcaseBookPage'

    more_button = Gtk.Template.Child()
    edit_button = Gtk.Template.Child()
    hero = Gtk.Template.Child()
    cover_box = Gtk.Template.Child()
    info_box = Gtk.Template.Child()
    title_label = Gtk.Template.Child()
    authors_label = Gtk.Template.Child()
    series_label = Gtk.Template.Child()
    rating_box = Gtk.Template.Child()
    status_label = Gtk.Template.Child()
    progress_bar = Gtk.Template.Child()
    buttons_box = Gtk.Template.Child()
    read_button = Gtk.Template.Child()
    finished_button = Gtk.Template.Child()
    description_box = Gtk.Template.Child()
    description_label = Gtk.Template.Child()
    tags_box = Gtk.Template.Child()
    details_group = Gtk.Template.Child()
    reading_group = Gtk.Template.Child()
    files_group = Gtk.Template.Child()

    def __init__(self, book_id):
        super().__init__()
        self.book_id = book_id
        self.book = None
        self._rows = []  # (group, row) added by refresh
        self.cover = Cover(width=COVER_WIDTH)
        self.cover.add_css_class('large')
        self.cover_box.append(self.cover)
        self.rating = Rating(editable=True, pixel_size=18)
        self.rating_box.append(self.rating)
        self.book_actions = BookActions(self, self._ids, details=self._no_details)
        connect_weak(self.rating, 'notify::value', self._on_rating_changed)
        connect_weak(self.authors_label, 'activate-link', self._on_activate_link)
        connect_weak(self.series_label, 'activate-link', self._on_activate_link)
        connect_weak(self.finished_button, 'clicked', self._on_finished_clicked)
        self.listener = PageListener(self, CHANGE_KINDS, BookPage.refresh)
        self.refresh()

    def _ids(self):
        return [self.book_id] if self.book is not None else []

    def _no_details(self, _book_id):
        pass  # this is the details

    # -- content -----------------------------------------------------------------------------

    def refresh(self):
        library = app().library
        book = library.book(self.book_id)
        if book is None:
            self.book = None
            GLib.idle_add(self._leave)
            return
        self.book = book
        self.set_title(book.title)
        self.cover.set_book(book)
        self.title_label.set_text(book.title)
        self.authors_label.set_markup(authors_markup(book))
        self.series_label.set_markup(series_markup(book))
        self.series_label.set_visible(bool(book.series))
        if self.rating.get_value() != book.rating:
            self._setting_rating = True
            self.rating.set_value(book.rating)
            self._setting_rating = False
        left = None
        if book.status == 'reading' and book.progress:
            try:
                time_left = stats.time_left(library, book.id, book.progress)
                left = time_left.book / 60 if time_left is not None else None
            except Exception:
                log.exception('time left in book %s', book.id)
        self.status_label.set_text(status_text(book, left))
        reading = book.status == 'reading' or (book.status != 'finished' and book.progress > 0)
        self.progress_bar.set_visible(reading)
        self.progress_bar.set_fraction(book.progress)
        self.read_button.set_label({'reading': _('_Continue Reading'),
                                    'finished': _('_Read Again')}.get(book.status, _('_Read')))
        self.read_button.set_sensitive(not book.missing)
        self.read_button.set_tooltip_text(
            _('The book’s file cannot be found') if book.missing else None)
        self.finished_button.set_label(_('Mark as _Unread') if book.status == 'finished'
                                       else _('Mark as _Finished'))
        markup = html_to_markup(book.description)
        self.description_label.set_markup(markup)
        self.description_box.set_visible(bool(markup))
        self._fill_tags(book)
        for group, row in self._rows:
            group.remove(row)
        self._rows = []
        self._fill_details(library, book)
        self._fill_reading(library, book)
        self._fill_files(library, book)
        self.book_actions.update()
        # Rebuilt with the page: Add to Shelf lists the shelves as they are now.
        self.more_button.set_menu_model(book_menu(library, details=False, read=False))

    def _leave(self):
        view = self.get_ancestor(Adw.NavigationView)
        if view is not None and view.get_visible_page() is self:
            view.pop()
        return GLib.SOURCE_REMOVE

    def _fill_tags(self, book):
        self.tags_box.remove_all()
        for tag in book.tags:
            button = Gtk.Button(label=tag)
            button.add_css_class('tag-pill')
            button.tag_name = tag
            button.set_tooltip_text(_('Books tagged “{tag}”').format(tag=tag))
            connect_weak(button, 'clicked', self._on_tag_clicked)
            self.tags_box.append(button)
        self.tags_box.set_visible(bool(book.tags))

    def _row(self, group, title, subtitle, activatable=False, icon=None):
        row = Adw.ActionRow(title=title, subtitle=subtitle, use_markup=False,
                            subtitle_selectable=not activatable, activatable=activatable)
        row.add_css_class('property')
        if icon:
            row.add_suffix(Gtk.Image(icon_name=icon,
                                     accessible_role=Gtk.AccessibleRole.PRESENTATION))
        group.add(row)
        self._rows.append((group, row))
        return row

    def _fill_details(self, library, book):
        group = self.details_group
        if book.publisher:
            self._row(group, _('Publisher'), book.publisher)
        if book.published:
            self._row(group, _('Published'), published_text(book.published))
        if book.language:
            self._row(group, _('Language'), language_name(book.language))
        for scheme, value in sorted(book.identifiers.items()):
            if scheme in ('uuid', 'calibre') or not value:
                continue
            label, address = IDENTIFIERS.get(scheme, (scheme.upper(), None))
            row = self._row(group, label, value, activatable=address is not None,
                            icon='adw-external-link-symbolic' if address else None)
            if address is not None:
                row.address = address.format(value=GLib.Uri.escape_string(value, None, False))
                row.set_tooltip_text(_('Open in the browser'))
                connect_weak(row, 'activated', self._on_link_row)
        if book.added:
            added = datetime.date.fromtimestamp(book.added)
            # Translators: when a book was added (strftime): "8 October 2026".
            self._row(group, _('Added'), added.strftime(_('%-d %B %Y')))
        shelves = library.book_shelves(book.id)
        if shelves:
            self._row(group, _('Shelves'), ', '.join(shelf.name for shelf in shelves))

    def _fill_reading(self, library, book):
        annotations = library.annotations(book.id)
        row = self._row(self.reading_group, _('Highlights and Bookmarks'),
                        annotations_text(annotations), activatable=bool(annotations),
                        icon='go-next-symbolic' if annotations else None)
        row.remove_css_class('property')
        if annotations:
            connect_weak(row, 'activated', self._on_annotations)
        if book.last_read:
            last = datetime.datetime.fromtimestamp(book.last_read)
            # Translators: when a book was last read (strftime): "8 October 2026".
            self._row(self.reading_group, _('Last Read'), last.strftime(_('%-d %B %Y')))
        try:
            seconds = stats.book_seconds(library, book.id)
        except Exception:
            seconds = 0
        if seconds >= 60:
            self._row(self.reading_group, _('Time Spent Reading'), duration_text(seconds / 60))

    def _fill_files(self, library, book):
        files = library.files(book.id)
        for book_file in files:
            title = book_file.format.upper()
            if book_file.size:
                title = f'{title} · {size_text(book_file.size)}'
            row = Adw.ActionRow(title=title, subtitle=book_file.path, use_markup=False,
                                subtitle_selectable=True, subtitle_lines=2)
            if book_file.missing:
                row.set_subtitle(_('Missing: {path}').format(path=book_file.path))
                icon = Gtk.Image(icon_name='dialog-warning-symbolic',
                                 tooltip_text=_('This file cannot be found'))
                icon.add_css_class('warning')
                row.add_prefix(icon)
            else:
                button = Gtk.Button(icon_name='folder-open-symbolic', valign=Gtk.Align.CENTER,
                                    tooltip_text=_('Show in Files'))
                button.add_css_class('flat')
                button.path = book_file.path
                connect_weak(button, 'clicked', self._on_show_file)
                row.add_suffix(button)
            self.files_group.add(row)
            self._rows.append((self.files_group, row))
        self.files_group.set_visible(bool(files))

    # -- actions -----------------------------------------------------------------------------

    def _window(self):
        return self.get_root()

    def _on_rating_changed(self, rating, _pspec):
        if getattr(self, '_setting_rating', False) or self.book is None:
            return
        value = rating.get_value()
        if value == self.book.rating:
            return
        app().library.update_book(self.book_id, rating=value)
        app().toast(_('Rating changed to {rating}').format(rating=rating_text(value)),
                    undo=True)

    def _on_finished_clicked(self, _button):
        if self.book is None:
            return
        if self.book.status == 'finished':
            self.book_actions.mark_unread([self.book_id])
        else:
            self.book_actions.mark_finished([self.book_id])

    def _on_activate_link(self, _label, uri):
        book = self.book
        window = self._window()
        if book is None or window is None or not hasattr(window, 'show_books'):
            return True
        library = app().library
        if uri.startswith('author:'):
            name = book.authors[int(uri[7:])]
            group = next((group for group in library.authors() if group.name == name), None)
            if group is not None:
                window.show_books(name, author=group.id)
            return True
        if uri.startswith('series:'):
            group = next((group for group in library.series() if group.name == book.series),
                         None)
            if group is not None:
                window.show_books(book.series, series=group.id)
            return True
        return False  # a link out: the label opens it in the browser

    def _on_tag_clicked(self, button):
        window = self._window()
        group = next((group for group in app().library.tags()
                      if group.name == button.tag_name), None)
        if group is not None and window is not None and hasattr(window, 'show_books'):
            window.show_books(group.name, tag=group.id)

    def _on_link_row(self, row):
        Gtk.UriLauncher(uri=row.address).launch(self._window(), None, None, None)

    def _on_show_file(self, button):
        launcher = Gtk.FileLauncher(file=Gio.File.new_for_path(button.path))
        launcher.open_containing_folder(self._window(), None, None, None)

    def _on_annotations(self, _row):
        from ..reader_window import open as open_reader

        window = open_reader(app(), self.book_id)
        if window is not None and hasattr(window, 'show_annotations'):
            window.show_annotations()
