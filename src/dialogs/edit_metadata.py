# SPDX-License-Identifier: GPL-2.0-or-later
# SPDX-FileCopyrightText: 2026 Jack Tully

"""Edit Metadata: one book's fields, or a few fields of many books at once.

    dialog = present(app, parent, book_ids, siblings=None)
    dialog.save()                   # what Save does: one undo step, a toast; True when saved
    dialog.values()                 # the single-book fields as shown (fetch_metadata's input)
    dialog.apply_values(values)     # put found values in (from fetch_metadata; not saved)
    parse_authors('Ada Lark & Ben Ross'), format_authors(names), parse_tags('a, b')
    valid_date('2004-05')           # '', 'YYYY', 'YYYY-MM' or 'YYYY-MM-DD'

One book (book_ids of one): the cover (its menu chooses an image file, pastes one, finds one
online or removes it), title and authors with their sort forms (which follow the title and
authors while they are the automatic ones), series and number, tags as chips (suggestions
from the library's tags as you type), publisher, published (a year, a month or a date, with
a calendar), language, page count (0 when not known), rating, ISBN and the other
identifiers, and the description as plain paragraphs (an unedited description keeps its
HTML). Find Metadata… opens fetch_metadata, whose Apply fills the fields. When `siblings`
(the ids of the list the book was opened from) holds more than one, arrows in the header
save and step to the previous or next one. Closing with unsaved edits asks whether to save
them.

Many books: authors, series (optionally numbered in the given order), publisher, language,
rating, status, tags to add and tags to remove; an empty row or "Leave Unchanged" keeps
each book's value. Either way Save writes through library.update_book(s) (and covers.py
for the cover) inside one Library.undoable step and toasts with Undo.
"""

import dataclasses
import datetime
import logging
import re
from gettext import gettext as _
from gettext import ngettext

from gi.repository import Adw, Gdk, Gio, GLib, GObject, Gtk, Pango

from .. import online
from ..titles import authors_sort, fold, title_sort
from ..widgets.cover import Cover
from ..widgets.rating import Rating
from ..widgets.util import connect_weak, connect_weak_call
from . import watch_dialog

log = logging.getLogger(__name__)

COVER_WIDTH = 128
# The languages offered first: ISO 639-1 codes. A book in another language adds its own.
LANGUAGES = ('en', 'de', 'fr', 'es', 'it', 'pt', 'nl', 'sv', 'no', 'da', 'fi', 'pl', 'cs',
             'ru', 'uk', 'el', 'tr', 'ar', 'he', 'hi', 'zh', 'ja', 'ko', 'la')
STATUSES = ('unread', 'reading', 'finished')
MAX_SUGGESTIONS = 8


def language_names():
    """{code: translated name} for LANGUAGES."""
    return {
        'en': _('English'), 'de': _('German'), 'fr': _('French'), 'es': _('Spanish'),
        'it': _('Italian'), 'pt': _('Portuguese'), 'nl': _('Dutch'), 'sv': _('Swedish'),
        'no': _('Norwegian'), 'da': _('Danish'), 'fi': _('Finnish'), 'pl': _('Polish'),
        'cs': _('Czech'), 'ru': _('Russian'), 'uk': _('Ukrainian'), 'el': _('Greek'),
        'tr': _('Turkish'), 'ar': _('Arabic'), 'he': _('Hebrew'), 'hi': _('Hindi'),
        'zh': _('Chinese'), 'ja': _('Japanese'), 'ko': _('Korean'), 'la': _('Latin'),
    }


def identifier_label(key):
    """An identifier's name as a person knows it: 'Open Library' for 'openlibrary'."""
    names = {'isbn': 'ISBN', 'google': 'Google Books', 'openlibrary': 'Open Library',
             'goodreads': 'Goodreads', 'amazon': 'Amazon', 'asin': 'ASIN', 'mobi-asin': 'ASIN',
             'doi': 'DOI', 'oclc': 'OCLC', 'lccn': 'LCCN', 'librarything': 'LibraryThing',
             'calibre': 'Calibre', 'uri': 'URI', 'url': 'URL'}
    return names.get(key.lower(), key)


def language_label(code):
    names = language_names()
    base = code.split('-')[0].lower()
    if base in names and base != code:
        return f'{names[base]} ({code})'
    return names.get(code, code)


# -- parsing ---------------------------------------------------------------------------------

def parse_authors(text):
    """Names from 'Ada Lark & Ben Ross', 'Ada Lark, Ben Ross' or 'Ada Lark; Ben Ross',
    blanks and repeats dropped."""
    names = []
    for name in re.split(r'\s*[&;,]\s*', text or ''):
        name = ' '.join(name.split())
        if name and name not in names:
            names.append(name)
    return names


def format_authors(names):
    return ' & '.join(names)


def parse_tags(text):
    tags = []
    for tag in (text or '').split(','):
        tag = ' '.join(tag.split())
        if tag and tag not in tags:
            tags.append(tag)
    return tags


def valid_date(text):
    """`text` as 'YYYY', 'YYYY-MM' or 'YYYY-MM-DD' (spaces trimmed), '' when empty; None
    when it is none of these or no real date."""
    text = (text or '').strip()
    if not text:
        return ''
    match = re.fullmatch(r'(\d{4})(?:-(\d{1,2})(?:-(\d{1,2}))?)?', text)
    if match is None:
        return None
    year, month, day = match.groups()
    if month is None:
        return year
    if not 1 <= int(month) <= 12:
        return None
    if day is None:
        return f'{year}-{int(month):02d}'
    try:
        datetime.date(int(year), int(month), int(day))
    except ValueError:
        return None
    return f'{year}-{int(month):02d}-{int(day):02d}'


def valid_isbn(text):
    """The ISBN as typed without spaces or dashes, '' when empty, None when not an ISBN."""
    text = re.sub(r'[\s-]', '', text or '').upper()
    if not text:
        return ''
    return text if online.normalize_isbn(text) else None


def texture_from_bytes(data):
    """A Gdk.Texture of an image's bytes, or None when they are not an image GTK reads."""
    try:
        return Gdk.Texture.new_from_bytes(GLib.Bytes.new(data))
    except GLib.Error:
        return None


# -- the dialog ------------------------------------------------------------------------------

@Gtk.Template(resource_path='/io/github/jackicus/Bookcase/edit_metadata.ui')
class EditMetadataDialog(Adw.Dialog):
    __gtype_name__ = 'BookcaseEditMetadataDialog'

    single = GObject.Property(type=bool, default=True)

    save_button = Gtk.Template.Child()
    step_box = Gtk.Template.Child()
    scrolled = Gtk.Template.Child()
    top_box = Gtk.Template.Child()
    details_group = Gtk.Template.Child()
    bulk_group = Gtk.Template.Child()
    cover_button = Gtk.Template.Child()
    cover_stack = Gtk.Template.Child()
    cover_picture = Gtk.Template.Child()
    cover_placeholder = Gtk.Template.Child()
    title_row = Gtk.Template.Child()
    authors_row = Gtk.Template.Child()
    sort_row = Gtk.Template.Child()
    sort_title_row = Gtk.Template.Child()
    author_sort_row = Gtk.Template.Child()
    series_row = Gtk.Template.Child()
    series_index_row = Gtk.Template.Child()
    tag_chips_row = Gtk.Template.Child()
    tag_box = Gtk.Template.Child()
    tags_row = Gtk.Template.Child()
    suggestions_row = Gtk.Template.Child()
    suggestion_box = Gtk.Template.Child()
    publisher_row = Gtk.Template.Child()
    published_row = Gtk.Template.Child()
    pages_row = Gtk.Template.Child()
    calendar_popover = Gtk.Template.Child()
    calendar = Gtk.Template.Child()
    language_row = Gtk.Template.Child()
    rating_row = Gtk.Template.Child()
    identifiers_group = Gtk.Template.Child()
    isbn_row = Gtk.Template.Child()
    description_view = Gtk.Template.Child()
    bulk_authors_row = Gtk.Template.Child()
    bulk_series_row = Gtk.Template.Child()
    bulk_number_row = Gtk.Template.Child()
    bulk_start_row = Gtk.Template.Child()
    bulk_publisher_row = Gtk.Template.Child()
    bulk_language_row = Gtk.Template.Child()
    bulk_rating_row = Gtk.Template.Child()
    bulk_status_row = Gtk.Template.Child()
    bulk_add_tags_row = Gtk.Template.Child()
    bulk_remove_tags_row = Gtk.Template.Child()

    def __init__(self, app, book_ids, siblings=None):
        super().__init__()
        self.app = app
        self.library = app.library
        self.book_ids = list(book_ids)
        self.siblings = list(siblings or [])
        self.book = None
        self.saved = False
        self._loading = False
        self._tags = []
        self._identifiers = {}
        self._identifier_rows = []
        self._cover = None  # None unchanged, ('set', bytes), ('remove',)
        self._cover_texture = None  # what the cover shows, for fetch_metadata
        self._cover_token = 0
        self._description_shown = ''
        self._language_codes = []
        self._bulk_language_codes = []
        self._all_tags = None
        self._find_task = None
        self.stars = Rating(editable=True)
        self.rating_row.add_suffix(self.stars)
        self.placeholder = Cover(width=COVER_WIDTH)
        self.cover_placeholder.append(self.placeholder)
        self.rating_row.set_activatable_widget(None)
        self._add_actions()
        self._connect_rows()
        self.single = len(self.book_ids) == 1
        if self.single:
            self.load(self.book_ids[0])
        else:
            self._load_bulk()
        self.connect('close-attempt', self._on_close_attempt)
        self.connect('closed', self._on_closed)

    # -- setup -------------------------------------------------------------------------------

    def _add_actions(self):
        group = Gio.SimpleActionGroup()
        self._actions = {}
        for name, callback in (
                ('cancel', lambda dialog: dialog.force_close()),
                ('save', lambda dialog: dialog._on_save()),
                ('previous', lambda dialog: dialog.step(-1)),
                ('next', lambda dialog: dialog.step(1)),
                ('find-metadata', lambda dialog: dialog.find_metadata()),
                ('choose-cover', lambda dialog: dialog.choose_cover()),
                ('paste-cover', lambda dialog: dialog.paste_cover()),
                ('find-cover', lambda dialog: dialog.find_cover()),
                ('remove-cover', lambda dialog: dialog.set_cover(None))):
            action = Gio.SimpleAction.new(name, None)
            # The dialog held weakly: the action group is the dialog's own.
            action.connect('activate', _weak_action(self, callback))
            group.add_action(action)
            self._actions[name] = action
        self.insert_action_group('edit', group)

    def _connect_rows(self):
        connect_weak(self.title_row, 'changed', self._on_title_changed)
        connect_weak(self.authors_row, 'changed', self._on_authors_changed)
        for row in (self.sort_title_row, self.author_sort_row, self.series_row,
                    self.publisher_row):
            connect_weak(row, 'changed', self._on_changed)
        connect_weak(self.series_index_row, 'notify::value', self._on_changed)
        connect_weak(self.pages_row, 'notify::value', self._on_changed)
        connect_weak(self.published_row, 'changed', self._on_published_changed)
        connect_weak(self.isbn_row, 'changed', self._on_isbn_changed)
        connect_weak(self.language_row, 'notify::selected', self._on_changed)
        connect_weak(self.stars, 'notify::value', self._on_changed)
        connect_weak(self.description_view.get_buffer(), 'changed', self._on_changed)
        connect_weak(self.tags_row, 'changed', self._on_tag_text_changed)
        connect_weak(self.tags_row, 'apply', self._on_tag_apply)
        connect_weak(self.tags_row, 'entry-activated', self._on_tag_apply)
        connect_weak(self.calendar, 'day-selected', self._on_day_selected)
        connect_weak(self.calendar_popover, 'show', self._on_calendar_shown)
        connect_weak(self.bulk_series_row, 'changed', self._on_bulk_series_changed)
        for row in (self.bulk_authors_row, self.bulk_publisher_row, self.bulk_add_tags_row,
                    self.bulk_remove_tags_row):
            connect_weak(row, 'changed', self._on_changed)
        for row in (self.bulk_language_row, self.bulk_rating_row, self.bulk_status_row):
            connect_weak(row, 'notify::selected', self._on_changed)
        connect_weak(self.bulk_number_row, 'notify::active', self._on_changed)
        connect_weak(self.bulk_start_row, 'notify::value', self._on_changed)

    # -- one book ----------------------------------------------------------------------------

    def load(self, book_id):
        """Show a book's fields as the library has them, forgetting any edits."""
        book = self.library.book(book_id)
        if book is None:
            return False
        self.book = book
        self._loading = True
        try:
            self.set_title(book.title or _('Edit Metadata'))
            self.title_row.set_text(book.title)
            self.authors_row.set_text(format_authors(book.authors))
            self.sort_title_row.set_text(book.sort_title)
            self.author_sort_row.set_text(book.author_sort)
            self.series_row.set_text(book.series)
            self.series_index_row.set_value(book.series_index or 0)
            self._tags = list(book.tags)
            self._show_tags()
            self.tags_row.set_text('')
            self.publisher_row.set_text(book.publisher)
            self.published_row.set_text(book.published)
            self.pages_row.set_value(book.pages or 0)
            self._fill_languages(self.language_row, book.language)
            self.stars.value = max(0, min(10, int(book.rating or 0)))
            self._identifiers = dict(book.identifiers or {})
            self.isbn_row.set_text(self._identifiers.pop('isbn', ''))
            self._show_identifiers()
            self._description_shown = online.html_to_plain(book.description)
            self.description_view.get_buffer().set_text(self._description_shown)
            self._cover = None
            self._show_book_cover(book)
        finally:
            self._loading = False
        self._update_sort_subtitle()
        self._update_series()
        self._validate()
        self._update_steps()
        self._update_can_close()
        self.scrolled.get_vadjustment().set_value(0)
        return True

    def values(self):
        """The fields as shown: title, authors (list), series, series_index, publisher,
        published, pages, language, description (HTML), isbn, tags (list), cover (a Gdk.Texture
        or None)."""
        return {
            'title': self.title_row.get_text().strip(),
            'authors': parse_authors(self.authors_row.get_text()),
            'series': self.series_row.get_text().strip(),
            'series_index': round(self.series_index_row.get_value(), 2),
            'publisher': self.publisher_row.get_text().strip(),
            'published': valid_date(self.published_row.get_text()) or '',
            'pages': int(self.pages_row.get_value()),
            'language': self._selected_language(self.language_row),
            'description': self._description_html(),
            'isbn': valid_isbn(self.isbn_row.get_text()) or '',
            'tags': list(self._tags),
            'cover': self._shown_cover(),
        }

    def _shown_cover(self):
        """The cover shown, read from the store when its thumbnail has not come yet."""
        if (self._cover_texture is None and self._cover is None and self.book is not None
                and self.book.has_cover):
            try:
                path = self.app.covers.path(self.book)
            except Exception:
                log.exception('cover of %s', self.book.id)
                path = None
            if path:
                return _as_texture(str(path))
        return self._cover_texture

    def apply_values(self, values):
        """Fill in found values (any of values()'s keys; 'cover' as image bytes). Saved only
        by Save."""
        if 'title' in values:
            self.title_row.set_text(values['title'])
        if 'authors' in values:
            self.authors_row.set_text(format_authors(values['authors']))
        if 'series' in values:
            self.series_row.set_text(values['series'])
        if 'series_index' in values:
            self.series_index_row.set_value(values['series_index'] or 0)
        for key, row in (('publisher', self.publisher_row), ('published', self.published_row),
                         ('isbn', self.isbn_row)):
            if key in values:
                row.set_text(values[key])
        if values.get('pages'):
            self.pages_row.set_value(values['pages'])
        if 'language' in values:
            self._fill_languages(self.language_row, values['language'])
        if 'description' in values:
            self.description_view.get_buffer().set_text(
                online.html_to_plain(values['description']))
        if 'tags' in values:
            for tag in values['tags']:
                self.add_tag(tag)
        if 'identifiers' in values:
            for key, value in values['identifiers'].items():
                if key != 'isbn' and value:
                    self._identifiers[key] = value
            self._show_identifiers()
        if values.get('cover'):
            self.set_cover(values['cover'])
        self._on_changed()

    def _description_html(self):
        buffer = self.description_view.get_buffer()
        text = buffer.get_text(buffer.get_start_iter(), buffer.get_end_iter(), False)
        if self.book is not None and text == self._description_shown:
            return self.book.description
        return online.plain_to_html(text)

    def changed_fields(self):
        """The library fields whose shown value differs from the book's, as update_book
        takes them."""
        book = self.book
        if book is None:
            return {}
        values = self.values()
        identifiers = dict(self._identifiers)
        if values['isbn']:
            identifiers['isbn'] = values['isbn']
        current = {
            'title': values['title'] or book.title,
            'sort_title': self.sort_title_row.get_text().strip(),
            'authors': tuple(values['authors']),
            'author_sort': self.author_sort_row.get_text().strip(),
            'series': values['series'],
            'series_index': float(values['series_index']) if values['series'] else 0.0,
            'tags': tuple(values['tags']),
            'publisher': values['publisher'],
            'published': values['published'],
            'pages': values['pages'],
            'language': values['language'],
            'description': values['description'],
            'rating': self.stars.value,
            'identifiers': identifiers,
        }
        original = {
            'title': book.title, 'sort_title': book.sort_title, 'authors': tuple(book.authors),
            'author_sort': book.author_sort, 'series': book.series,
            'series_index': float(book.series_index or 0) if book.series else 0.0,
            'tags': tuple(book.tags), 'publisher': book.publisher,
            'published': book.published, 'pages': int(book.pages or 0),
            'language': book.language,
            'description': book.description, 'rating': int(book.rating or 0),
            'identifiers': dict(book.identifiers or {}),
        }
        return {key: value for key, value in current.items() if value != original[key]}

    def is_dirty(self):
        if not self.single:
            return bool(self.bulk_fields())
        return bool(self.changed_fields()) or self._cover is not None

    # -- sort forms --------------------------------------------------------------------------

    def _auto_title_sort(self, title):
        language = self.book.language if self.book else ''
        return title_sort(title, language) if title else ''

    def _on_title_changed(self, row):
        if self._loading or self.book is None:
            return self._on_changed()
        sort_text = self.sort_title_row.get_text()
        if sort_text in ('', self.book.sort_title) and (
                self.book.sort_title in ('', self._auto_title_sort(self.book.title))):
            self.sort_title_row.set_text(self._auto_title_sort(row.get_text().strip()))
        self._on_changed()

    def _on_authors_changed(self, row):
        if self._loading or self.book is None:
            return self._on_changed()
        sort_text = self.author_sort_row.get_text()
        automatic = authors_sort(list(self.book.authors)) if self.book.authors else ''
        if sort_text in ('', self.book.author_sort) and self.book.author_sort in ('', automatic):
            names = parse_authors(row.get_text())
            self.author_sort_row.set_text(authors_sort(names) if names else '')
        self._on_changed()

    def _update_series(self):
        self.series_index_row.set_sensitive(bool(self.series_row.get_text().strip()))

    def _update_sort_subtitle(self):
        parts = [self.sort_title_row.get_text().strip(), self.author_sort_row.get_text().strip()]
        self.sort_row.set_subtitle(' · '.join(GLib.markup_escape_text(part)
                                              for part in parts if part))

    # -- validation --------------------------------------------------------------------------

    def _on_changed(self, *_args):
        if self._loading:
            return
        self._update_sort_subtitle()
        self._update_series()
        self._validate()
        self._update_can_close()

    def _on_published_changed(self, _row):
        self._on_changed()

    def _on_isbn_changed(self, _row):
        self._on_changed()

    def _validate(self):
        ok = True
        if self.single:
            for row, valid in ((self.published_row, valid_date), (self.isbn_row, valid_isbn)):
                good = valid(row.get_text()) is not None
                if good:
                    row.remove_css_class('error')
                else:
                    row.add_css_class('error')
                ok = ok and good
            ok = ok and bool(self.title_row.get_text().strip())
        else:
            ok = bool(self.bulk_fields())
        self.save_button.set_sensitive(ok)
        self._actions['save'].set_enabled(ok)
        return ok

    # -- dates -------------------------------------------------------------------------------

    def _on_calendar_shown(self, _popover):
        date = valid_date(self.published_row.get_text())
        if date and len(date) == 10:
            year, month, day = (int(part) for part in date.split('-'))
            self._loading_calendar = True
            try:
                self.calendar.select_day(GLib.DateTime.new_local(year, month, day, 0, 0, 0))
            finally:
                self._loading_calendar = False

    def _on_day_selected(self, calendar):
        if getattr(self, '_loading_calendar', False):
            return
        date = calendar.get_date()
        self.published_row.set_text(
            f'{date.get_year():04d}-{date.get_month():02d}-{date.get_day_of_month():02d}')
        self.calendar_popover.popdown()

    # -- languages ---------------------------------------------------------------------------

    def _fill_languages(self, row, code, leave_unchanged=False):
        codes = list(LANGUAGES)
        code = (code or '').strip()
        if code and code not in codes:
            codes.append(code)
        codes.sort(key=lambda c: fold(language_label(c)))
        labels = [language_label(c) for c in codes]
        if leave_unchanged:
            codes.insert(0, None)
            labels.insert(0, _('Leave Unchanged'))
        else:
            codes.insert(0, '')
            labels.insert(0, _('Unknown'))
        if row is self.language_row:
            self._language_codes = codes
        else:
            self._bulk_language_codes = codes
        row.set_model(Gtk.StringList.new(labels))
        row.set_selected(codes.index(code) if code in codes else 0)

    def _selected_language(self, row):
        codes = self._language_codes if row is self.language_row else self._bulk_language_codes
        index = row.get_selected()
        if not codes:
            return None if row is not self.language_row else ''
        return codes[index] if 0 <= index < len(codes) else codes[0]

    # -- tags --------------------------------------------------------------------------------

    def _show_tags(self):
        while (child := self.tag_box.get_first_child()) is not None:
            self.tag_box.remove(child)
        for tag in self._tags:
            self.tag_box.append(self._chip(tag))
        self.tag_chips_row.set_visible(bool(self._tags))

    def _chip(self, tag):
        box = Gtk.Box(spacing=2)
        box.add_css_class('tag-chip')
        box.append(Gtk.Label(label=tag, ellipsize=Pango.EllipsizeMode.END, max_width_chars=28,
                             margin_start=10))
        remove = Gtk.Button(icon_name='window-close-symbolic', valign=Gtk.Align.CENTER)
        # Translators: a button removing a tag from the book; {} is the tag.
        remove.set_tooltip_text(_('Remove “{tag}”').format(tag=tag))
        remove.add_css_class('flat')
        remove.add_css_class('circular')
        connect_weak_call(remove, 'clicked', self.remove_tag, tag)
        box.append(remove)
        return box

    def add_tag(self, tag):
        for name in parse_tags(tag):
            if fold(name) not in (fold(existing) for existing in self._tags):
                self._tags.append(name)
        self._show_tags()
        self._on_changed()

    def remove_tag(self, tag):
        if tag in self._tags:
            self._tags.remove(tag)
        self._show_tags()
        self._on_changed()

    def _library_tags(self):
        if self._all_tags is None:
            try:
                self._all_tags = [group.name for group in self.library.tags()]
            except Exception:  # an unfinished library: no suggestions
                log.exception('tags')
                self._all_tags = []
        return self._all_tags

    def _on_tag_text_changed(self, row):
        text = fold(row.get_text().strip())
        while (child := self.suggestion_box.get_first_child()) is not None:
            self.suggestion_box.remove(child)
        matches = []
        if text:
            chosen = {fold(tag) for tag in self._tags}
            names = [name for name in self._library_tags() if fold(name) not in chosen]
            matches = [name for name in names if fold(name).startswith(text)]
            matches += [name for name in names if text in fold(name) and name not in matches]
        for name in matches[:MAX_SUGGESTIONS]:
            button = Gtk.Button(label=name)
            button.add_css_class('pill')
            button.add_css_class('tag-suggestion')
            connect_weak(button, 'clicked', self._on_suggestion, name)
            self.suggestion_box.append(button)
        self.suggestions_row.set_visible(bool(matches))

    def _on_suggestion(self, _button, name):
        self.add_tag(name)
        self.tags_row.set_text('')
        self.tags_row.grab_focus()

    def _on_tag_apply(self, row):
        if row.get_text().strip():
            self.add_tag(row.get_text())
            row.set_text('')

    # -- identifiers -------------------------------------------------------------------------

    def _show_identifiers(self):
        for row in self._identifier_rows:
            self.identifiers_group.remove(row)
        self._identifier_rows = []
        for key, value in sorted(self._identifiers.items()):
            row = Adw.ActionRow(title=GLib.markup_escape_text(identifier_label(key)),
                                subtitle=GLib.markup_escape_text(str(value)),
                                subtitle_selectable=True)
            row.add_css_class('property')
            remove = Gtk.Button(icon_name='user-trash-symbolic', valign=Gtk.Align.CENTER)
            remove.set_tooltip_text(_('Remove Identifier'))
            remove.add_css_class('flat')
            connect_weak(remove, 'clicked', self._on_remove_identifier, key)
            row.add_suffix(remove)
            self.identifiers_group.add(row)
            self._identifier_rows.append(row)

    def _on_remove_identifier(self, _button, key):
        self._identifiers.pop(key, None)
        self._show_identifiers()
        self._on_changed()

    # -- cover -------------------------------------------------------------------------------

    def _show_book_cover(self, book):
        """The book's cover as the store has it, loaded off the main thread."""
        self.placeholder.set_book(dataclasses.replace(book, has_cover=False))
        self._show_texture(None)
        if not book.has_cover:
            return
        self._cover_token += 1
        token = self._cover_token
        covers = getattr(self.app, 'covers', None)
        if covers is None:
            return
        ref = self.weak_ref()

        def loaded(result, *_rest):
            dialog = ref()
            if dialog is None or dialog._cover_token != token or dialog._cover is not None:
                return
            dialog._show_texture(_as_texture(result))

        try:
            covers.load_thumbnail(book, COVER_WIDTH * 2, loaded)
        except Exception:  # the store not ready: the placeholder stays
            log.exception('cover of %s', book.id)

    def _show_texture(self, texture):
        self._cover_texture = texture
        self.cover_picture.set_paintable(texture)
        self.cover_stack.set_visible_child_name('picture' if texture else 'none')
        self._actions['remove-cover'].set_enabled(texture is not None)
        title = self.title_row.get_text() or (self.book.title if self.book else '')
        self.cover_picture.update_property([Gtk.AccessibleProperty.LABEL],
                                           [_('Cover of {title}').format(title=title)])

    def set_cover(self, data):
        """A new cover (image bytes), or None to remove it; saved by Save."""
        self._cover_token += 1
        if data is None:
            self._cover = ('remove',) if self.book and self.book.has_cover else None
            self._show_texture(None)
            self._on_changed()
            return True
        texture = texture_from_bytes(data)
        if texture is None:
            self.app.toast(_('The image could not be read'))
            return False
        self._cover = ('set', data)
        self._show_texture(texture)
        self._on_changed()
        return True

    def choose_cover(self):
        images = Gtk.FileFilter(name=_('Images'))
        images.add_pixbuf_formats()
        filters = Gio.ListStore.new(Gtk.FileFilter)
        filters.append(images)
        chooser = Gtk.FileDialog(title=_('Choose Cover'), filters=filters, default_filter=images,
                                 modal=True)
        chooser.open(self.get_root(), None, self._on_cover_chosen)

    def _on_cover_chosen(self, chooser, result):
        try:
            file = chooser.open_finish(result)
        except GLib.Error:
            return  # dismissed
        file.load_contents_async(None, self._on_cover_loaded)

    def _on_cover_loaded(self, file, result):
        try:
            _ok, data, _etag = file.load_contents_finish(result)
        except GLib.Error as error:
            self.app.report(error, _('Could not read the image'))
            return
        self.set_cover(bytes(data))

    def paste_cover(self):
        clipboard = self.get_clipboard()
        clipboard.read_texture_async(None, self._on_cover_pasted)

    def _on_cover_pasted(self, clipboard, result):
        try:
            texture = clipboard.read_texture_finish(result)
        except GLib.Error:
            texture = None
        if texture is None:
            self.app.toast(_('No image to paste'))
            return
        self.set_cover(texture.save_to_png_bytes().get_data())

    def find_cover(self):
        from . import fetch_metadata

        fetch_metadata.present_covers(self.app, self, self.values(), self.set_cover)

    def find_metadata(self):
        from . import fetch_metadata

        fetch_metadata.present(self.app, self, self.values(), self.apply_values)

    # -- many books --------------------------------------------------------------------------

    def _load_bulk(self):
        count = len(self.book_ids)
        self.set_title(ngettext('Edit {n} Book', 'Edit {n} Books', count).format(n=count))
        self._fill_languages(self.bulk_language_row, None, leave_unchanged=True)
        self.bulk_rating_row.set_model(Gtk.StringList.new(
            [_('Leave Unchanged'), _('No Rating')]
            + [ngettext('{n} Star', '{n} Stars', n).format(n=n) for n in range(1, 6)]))
        self.bulk_status_row.set_model(Gtk.StringList.new(
            [_('Leave Unchanged'), _('Unread'), _('Reading'), _('Finished')]))
        self._validate()

    def _on_bulk_series_changed(self, row):
        has_series = bool(row.get_text().strip())
        self.bulk_number_row.set_sensitive(has_series)
        if not has_series:
            self.bulk_number_row.set_active(False)
        self._on_changed()

    def bulk_fields(self):
        """What a bulk save changes: update_books' fields, plus 'numbering' (the first
        number) and 'status'."""
        fields = {}
        authors = parse_authors(self.bulk_authors_row.get_text())
        if authors:
            fields['authors'] = tuple(authors)
            fields['author_sort'] = authors_sort(authors)
        series = self.bulk_series_row.get_text().strip()
        if series:
            fields['series'] = series
            if self.bulk_number_row.get_active():
                fields['numbering'] = self.bulk_start_row.get_value()
        publisher = self.bulk_publisher_row.get_text().strip()
        if publisher:
            fields['publisher'] = publisher
        language = self._selected_language(self.bulk_language_row)
        if language is not None:
            fields['language'] = language
        rating = self.bulk_rating_row.get_selected()
        if 1 <= rating <= 6:
            fields['rating'] = (rating - 1) * 2
        status = self.bulk_status_row.get_selected()
        if 1 <= status <= len(STATUSES):
            fields['status'] = STATUSES[status - 1]
        add = parse_tags(self.bulk_add_tags_row.get_text())
        if add:
            fields['add_tags'] = tuple(add)
        remove = parse_tags(self.bulk_remove_tags_row.get_text())
        if remove:
            fields['remove_tags'] = tuple(remove)
        return fields

    # -- saving ------------------------------------------------------------------------------

    def save(self):
        """Write the edits as one undo step and toast; True when something was saved."""
        if not self._validate():
            return False
        try:
            if self.single:
                saved = self._save_single()
            else:
                saved = self._save_bulk()
        except Exception as error:
            self.app.report(error, _('Could not save the changes'))
            return False
        self.saved = self.saved or saved
        self._update_can_close()
        return saved

    def _save_single(self):
        fields = self.changed_fields()
        cover = self._cover
        if not fields and cover is None:
            return False
        book_id = self.book.id
        with self.library.undoable(_('Edit Book')):
            if fields:
                self.library.update_book(book_id, **fields)
            if cover is not None:
                if cover[0] == 'set':
                    self.app.covers.save(book_id, cover[1])
                else:
                    self.app.covers.remove(book_id)
        self._cover = None
        self.book = self.library.book(book_id) or self.book
        self._description_shown = online.html_to_plain(self.book.description)
        self.app.toast(_('Saved “{title}”').format(title=self.book.title), undo=True)
        return True

    def _save_bulk(self):
        fields = self.bulk_fields()
        if not fields:
            return False
        numbering = fields.pop('numbering', None)
        status = fields.pop('status', None)
        count = len(self.book_ids)
        with self.library.undoable(_('Edit Books')):
            if fields:
                self.library.update_books(self.book_ids, **fields)
            if numbering is not None:
                for offset, book_id in enumerate(self.book_ids):
                    self.library.update_book(book_id, series_index=float(numbering + offset))
            if status is not None:
                self.library.set_status(self.book_ids, status)
        self.app.toast(ngettext('Saved {n} book', 'Saved {n} books', count).format(n=count),
                       undo=True)
        return True

    def _on_save(self):
        if self.save() or not self.is_dirty():
            self.force_close()

    # -- stepping ----------------------------------------------------------------------------

    def _position(self):
        if not self.single or self.book is None or self.book.id not in self.siblings:
            return None
        return self.siblings.index(self.book.id)

    def _update_steps(self):
        position = self._position()
        self.step_box.set_visible(position is not None and len(self.siblings) > 1)
        if position is not None:
            self._actions['previous'].set_enabled(position > 0)
            self._actions['next'].set_enabled(position < len(self.siblings) - 1)

    def step(self, offset):
        """Save, then show the book `offset` places along in `siblings`."""
        position = self._position()
        if position is None:
            return False
        target = position + offset
        if not 0 <= target < len(self.siblings):
            return False
        if self.is_dirty() and not self.save():
            return False
        return self.load(self.siblings[target])

    # -- closing -----------------------------------------------------------------------------

    def _on_close_attempt(self, _dialog):
        alert = Adw.AlertDialog(heading=_('Save Changes?'),
                                body=_('The edits are lost unless they are saved.'))
        alert.add_response('cancel', _('_Cancel'))
        alert.add_response('discard', _('_Discard'))
        alert.add_response('save', _('_Save'))
        alert.set_response_appearance('discard', Adw.ResponseAppearance.DESTRUCTIVE)
        alert.set_response_appearance('save', Adw.ResponseAppearance.SUGGESTED)
        alert.set_default_response('save')
        alert.set_close_response('cancel')
        connect_weak(alert, 'response', self._on_close_response)
        alert.present(self)

    def _on_close_response(self, _alert, response):
        if response == 'discard':
            self.force_close()
        elif response == 'save':
            self._on_save()

    def _update_can_close(self):
        self.set_can_close(not self.is_dirty())

    def _on_closed(self, _dialog):
        if self._find_task is not None:
            self._find_task.cancel()


def _as_texture(result):
    """A Gdk.Paintable from what a cover loader hands back: a paintable, a pixbuf, image
    bytes or a file path."""
    if result is None or isinstance(result, Gdk.Paintable):
        return result
    if isinstance(result, (bytes, bytearray)):
        return texture_from_bytes(bytes(result))
    if isinstance(result, str):
        try:
            return Gdk.Texture.new_from_filename(result)
        except GLib.Error:
            return None
    try:
        return Gdk.Texture.new_for_pixbuf(result)
    except TypeError:
        return None


def present(app, parent, book_ids, siblings=None):
    """Edit one book (with prev/next through `siblings` when given) or many; returns the
    dialog."""
    dialog = EditMetadataDialog(app, book_ids, siblings)
    watch_dialog(dialog, parent)
    dialog.present(parent)
    if dialog.single:
        dialog.title_row.grab_focus()
    return dialog


def _weak_action(dialog, callback):
    """An action's handler that calls callback(dialog) while the dialog lives."""
    ref = dialog.weak_ref()

    def activate(*_args):
        instance = ref()
        if instance is not None:
            callback(instance)

    return activate
