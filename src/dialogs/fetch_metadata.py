# SPDX-License-Identifier: GPL-2.0-or-later
# SPDX-FileCopyrightText: 2026 Jack Tully

"""Find Metadata and Find Cover: Open Library (and Google Books with a key) searched for
the book shown in Edit Metadata.

    present(app, parent, values, on_apply)          # Find Metadata…
    present_covers(app, parent, values, on_choose)  # Find Cover Online…
    comparison(values, candidate)       # [Field]: what a candidate would change
    chosen_values(fields, candidate)    # the values Apply hands back for the checked fields

`values` is edit_metadata's EditMetadataDialog.values() (title, authors, isbn, … and the
shown cover as a Gdk.Texture). The search starts at once: by ISBN when there is one (falling
back on title and author), else by title and first author; the terms can be changed under
"Search For". Candidates are rows with a thumbnail (fetched in threads) and their source;
choosing one fetches what the search leaves out (online.complete) and pushes the comparison:
a check per field that differs, the found value above the current one, checked when the
current one is empty or the field is not the title or authors (which are usually right
already). Apply calls on_apply(values) with the checked fields (the cover as image bytes,
and the candidate's other identifiers) and closes; nothing is saved until Edit Metadata's
Save. Find Cover shows the candidates' covers in a grid; a click downloads the large one
and calls on_choose(bytes). Offline, refused or busy: a status page says so, with Try Again.
Closing the dialog cancels whatever is still running.
"""

import dataclasses
import logging
from gettext import gettext as _

from gi.repository import Adw, Gio, GLib, Gtk, Pango

from .. import online
from ..widgets.util import connect_weak, connect_weak_call
from . import watch_dialog
from .edit_metadata import format_authors, language_label, texture_from_bytes

log = logging.getLogger(__name__)

THUMB_WIDTH, THUMB_HEIGHT = 40, 60
GRID_WIDTH, GRID_HEIGHT = 96, 144
COMPARE_COVER_WIDTH, COMPARE_COVER_HEIGHT = 64, 96
# Fields the comparison leaves unchecked when the book already has a value.
KEEP_BY_DEFAULT = ('title', 'authors', 'tags')


@dataclasses.dataclass
class Field:
    key: str
    label: str
    found: str  # as shown
    current: str  # as shown
    checked: bool


def _field_labels():
    return {
        'title': _('Title'), 'authors': _('Authors'), 'series': _('Series'),
        'publisher': _('Publisher'), 'published': _('Published'), 'pages': _('Pages'),
        'language': _('Language'),
        'isbn': _('ISBN'), 'tags': _('Tags'), 'description': _('Description'),
        'cover': _('Cover'),
    }


def _series_text(name, index):
    if not name:
        return ''
    if index:
        # Translators: a series and the book's number in it: "The Harbour Books #2".
        return _('{series} #{number}').format(series=name, number=f'{index:g}')
    return name


def _shown(key, values):
    """A field of `values` (edit_metadata's) as text."""
    if key == 'authors':
        return format_authors(values.get('authors') or ())
    if key == 'series':
        return _series_text(values.get('series', ''), values.get('series_index', 0))
    if key == 'language':
        return language_label(values['language']) if values.get('language') else ''
    if key == 'description':
        return online.html_to_plain(values.get('description', ''))
    if key == 'tags':
        return ', '.join(values.get('tags') or ())
    if key == 'cover':
        return 'cover' if values.get('cover') is not None else ''
    if key == 'pages':
        return str(values['pages']) if values.get('pages') else ''
    return str(values.get(key) or '')


def candidate_values(candidate):
    """A candidate's fields in edit_metadata's terms."""
    return {
        'title': candidate.title,
        'authors': list(candidate.authors),
        'series': candidate.series,
        'series_index': candidate.series_index,
        'publisher': candidate.publisher,
        'published': candidate.published,
        'pages': candidate.pages,
        'language': candidate.language,
        'isbn': candidate.identifiers.get('isbn', ''),
        'tags': list(candidate.tags),
        'description': candidate.description,
        'cover': candidate.cover_url or None,
    }


def comparison(values, candidate):
    """The fields a candidate has a value for that differs from `values`, in the dialog's
    order (see the module for which are checked)."""
    found = candidate_values(candidate)
    fields = []
    for key, label in _field_labels().items():
        new, old = _shown(key, found), _shown(key, values)
        if key == 'cover':
            if not candidate.cover_url:
                continue
            new = candidate.cover_url
        elif not new or new == old:
            continue
        elif key == 'tags':
            known = {tag.casefold() for tag in values.get('tags') or ()}
            extra = [tag for tag in found['tags'] if tag.casefold() not in known]
            if not extra:
                continue
            new = ', '.join(extra)
        checked = not old or key not in KEEP_BY_DEFAULT
        fields.append(Field(key, label, new, old, checked))
    return fields


def chosen_values(fields, candidate, cover=None):
    """What Apply hands back: the checked fields' found values (tags to add, the cover as
    `cover`, the image's bytes), and the candidate's identifiers other than the ISBN."""
    found = candidate_values(candidate)
    values = {}
    for field in fields:
        if not field.checked:
            continue
        if field.key == 'series':
            values['series'] = found['series']
            values['series_index'] = found['series_index']
        elif field.key == 'tags':
            values['tags'] = [tag.strip() for tag in field.found.split(',') if tag.strip()]
        elif field.key == 'cover':
            if cover:
                values['cover'] = cover
        else:
            values[field.key] = found[field.key]
    identifiers = {key: value for key, value in candidate.identifiers.items()
                   if key != 'isbn' and value}
    if values and identifiers:
        values['identifiers'] = identifiers
    return values


def fixed_size(widget, width, height):
    """`widget` held at width x height, whatever its natural size (a picture's is its
    image's)."""
    inner = Adw.Clamp(maximum_size=height, orientation=Gtk.Orientation.VERTICAL, child=widget)
    return Adw.Clamp(maximum_size=width, child=inner, halign=Gtk.Align.CENTER,
                     valign=Gtk.Align.CENTER)


@Gtk.Template(resource_path='/io/github/jackicus/Bookcase/fetch_metadata.ui')
class FetchMetadataDialog(Adw.Dialog):
    __gtype_name__ = 'BookcaseFetchMetadataDialog'

    navigation_view = Gtk.Template.Child()
    results_page = Gtk.Template.Child()
    stack = Gtk.Template.Child()
    searching_status = Gtk.Template.Child()
    query_row = Gtk.Template.Child()
    query_title_row = Gtk.Template.Child()
    query_author_row = Gtk.Template.Child()
    query_isbn_row = Gtk.Template.Child()
    search_again_row = Gtk.Template.Child()
    candidate_list = Gtk.Template.Child()
    cover_grid = Gtk.Template.Child()
    sources_label = Gtk.Template.Child()
    empty_status = Gtk.Template.Child()
    error_status = Gtk.Template.Child()
    compare_page = Gtk.Template.Child()
    compare_scrolled = Gtk.Template.Child()
    compare_label = Gtk.Template.Child()
    compare_list = Gtk.Template.Child()
    apply_button = Gtk.Template.Child()

    def __init__(self, app, values, callback, covers=False, fetch=None):
        super().__init__()
        self.app = app
        self.current = dict(values)
        self.callback = callback
        self.covers_only = covers
        self.fetch = fetch  # online's fetch function (tests pass canned answers)
        self.candidates = []
        self.candidate = None  # the one compared
        self.fields = []
        self._checks = []
        self._found_cover = None  # the compared candidate's cover bytes, once fetched
        self._tasks = []
        self._search_task = None
        self._busy = False
        if covers:
            self.set_title(_('Find Cover'))
            self.results_page.set_title(_('Find Cover'))
            self.candidate_list.set_visible(False)
            self.cover_grid.set_visible(True)
            self.empty_status.set_title(_('No Covers Found'))
        else:
            self.set_title(_('Find Metadata'))
        self._add_actions()
        self.query_title_row.set_text(values.get('title', ''))
        authors = values.get('authors') or ()
        self.query_author_row.set_text(authors[0] if authors else '')
        self.query_isbn_row.set_text(values.get('isbn', ''))
        for row in (self.query_title_row, self.query_author_row, self.query_isbn_row):
            connect_weak_call(row, 'entry-activated', self.search)
        connect_weak_call(self.search_again_row, 'activated', self.search)
        connect_weak(self.candidate_list, 'row-activated', self._on_candidate_activated)
        connect_weak(self.compare_list, 'row-activated', self._on_compare_activated)
        self.connect('closed', self._on_closed)

    def _add_actions(self):
        group = Gio.SimpleActionGroup()
        self._actions = {}
        for name, callback in (('search', lambda dialog: dialog.search()),
                               ('edit-query', lambda dialog: dialog.edit_query()),
                               ('apply', lambda dialog: dialog.apply()),
                               ('select-all', lambda dialog: dialog.select_all())):
            action = Gio.SimpleAction.new(name, None)
            # The dialog held weakly: the action group is the dialog's own.
            action.connect('activate', _weak_action(self, callback))
            group.add_action(action)
            self._actions[name] = action
        self.insert_action_group('fetch', group)

    @property
    def google_key(self):
        settings = getattr(self.app, 'settings', None)
        try:
            return settings.get_string('google-books-key').strip() if settings else ''
        except (TypeError, AttributeError):
            return ''

    # -- searching ---------------------------------------------------------------------------

    def _run(self, func, callback, *args, **kwargs):
        if self.fetch is not None:
            kwargs['fetch'] = self.fetch
        task = online.run_async(func, callback, *args, **kwargs)
        self._tasks.append(task)
        return task

    def search(self):
        """Search with the terms under "Search For"."""
        title = self.query_title_row.get_text().strip()
        author = self.query_author_row.get_text().strip()
        isbn = self.query_isbn_row.get_text().strip()
        if not (title or author or isbn):
            self.edit_query()
            return
        if self._search_task is not None:
            self._search_task.cancel()
        self.query_row.set_subtitle(GLib.markup_escape_text(
            ' · '.join(part for part in (isbn, title, author) if part)))
        key = self.google_key
        self.searching_status.set_description(
            _('Asking Open Library and Google Books') if key else _('Asking Open Library'))
        self.stack.set_visible_child_name('searching')
        self._search_task = self._run(online.search, self._on_found, title=title,
                                      authors=[author] if author else [], isbn=isbn,
                                      google_key=key)

    def _on_found(self, candidates, error):
        self._search_task = None
        if error is not None:
            self.show_error(error)
            return
        if self.covers_only:
            candidates = [candidate for candidate in candidates if candidate.cover_url]
        self.show_candidates(candidates)

    def show_error(self, error):
        if isinstance(error, online.OnlineError) and error.offline:
            self.error_status.set_icon_name('network-offline-symbolic')
            self.error_status.set_title(_('No Connection'))
            self.error_status.set_description(
                _('Bookcase could not reach Open Library. Check your internet connection '
                  'and try again.'))
        else:
            self.error_status.set_icon_name('dialog-warning-symbolic')
            self.error_status.set_title(_('Could Not Search'))
            self.error_status.set_description(str(error) if isinstance(
                error, online.OnlineError) else _('Something went wrong while searching.'))
        self.stack.set_visible_child_name('error')

    def edit_query(self):
        self.show_candidates([], quiet=True)
        self.query_row.set_expanded(True)
        self.query_title_row.grab_focus()

    def show_candidates(self, candidates, quiet=False):
        self.candidates = list(candidates)
        for container in (self.candidate_list, self.cover_grid):
            container.remove_all()
        if not candidates and not quiet:
            self.stack.set_visible_child_name('empty')
            return
        for candidate in candidates:
            if self.covers_only:
                self.cover_grid.append(self._cover_tile(candidate))
            else:
                self.candidate_list.append(self._candidate_row(candidate))
        self.candidate_list.set_visible(bool(candidates) and not self.covers_only)
        self.query_row.set_expanded(quiet)
        if self.google_key:
            self.sources_label.set_text(_('From Open Library and Google Books'))
        else:
            self.sources_label.set_text(_('From Open Library. With a Google Books API key '
                                          '(in Preferences) Google Books is searched too.'))
        self.sources_label.set_visible(bool(candidates))
        self.stack.set_visible_child_name('results')

    # -- the candidates ----------------------------------------------------------------------

    def _picture(self, width, height, url, label=None):
        """A width x height picture (a card) of the image at `url`, fetched in a thread:
        labelled `label` for a screen reader, else presentation (its row names it)."""
        picture = Gtk.Picture(content_fit=Gtk.ContentFit.COVER, can_shrink=True,
                              width_request=width, height_request=height)
        if label:
            picture.update_property([Gtk.AccessibleProperty.LABEL], [label])
        else:
            picture.set_accessible_role(Gtk.AccessibleRole.PRESENTATION)
        picture.add_css_class('card')
        picture.add_css_class('fetch-thumbnail')
        if url:
            ref = picture.weak_ref()

            def loaded(data, error):
                widget = ref()
                if widget is not None and data:
                    texture = texture_from_bytes(data)
                    if texture is not None:
                        widget.set_paintable(texture)

            self._run(online.fetch_cover, loaded, url)
        return fixed_size(picture, width, height)

    def _candidate_row(self, candidate):
        row = Adw.ActionRow(title=GLib.markup_escape_text(candidate.title), activatable=True)
        row.set_title_lines(2)
        row.set_subtitle_lines(2)
        details = [format_authors(candidate.authors), candidate.published[:4],
                   candidate.publisher]
        row.set_subtitle(GLib.markup_escape_text(' · '.join(part for part in details
                                                             if part)))
        row.add_prefix(self._picture(THUMB_WIDTH, THUMB_HEIGHT, candidate.thumbnail_url))
        badge = Gtk.Label(label=candidate.source_name, valign=Gtk.Align.CENTER,
                          wrap=True, justify=Gtk.Justification.CENTER, max_width_chars=12)
        badge.add_css_class('caption')
        badge.add_css_class('fetch-source')
        row.add_suffix(badge)
        spinner = Adw.Spinner(visible=False, valign=Gtk.Align.CENTER)
        row.add_suffix(spinner)
        row.add_suffix(Gtk.Image(icon_name='go-next-symbolic',
                                 accessible_role=Gtk.AccessibleRole.PRESENTATION))
        row.candidate = candidate
        row.spinner = spinner
        return row

    def _cover_tile(self, candidate):
        button = Gtk.Button(halign=Gtk.Align.CENTER)
        button.add_css_class('flat')
        button.add_css_class('fetch-cover-tile')
        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=6)
        overlay = Gtk.Overlay()
        overlay.set_child(self._picture(GRID_WIDTH, GRID_HEIGHT,
                                        candidate.thumbnail_url or candidate.cover_url))
        spinner = Adw.Spinner(visible=False, halign=Gtk.Align.CENTER, valign=Gtk.Align.CENTER)
        overlay.add_overlay(spinner)
        box.append(overlay)
        label = Gtk.Label(label=candidate.source_name, ellipsize=Pango.EllipsizeMode.END,
                          max_width_chars=12)
        label.add_css_class('caption')
        label.add_css_class('dimmed')
        box.append(label)
        button.set_child(box)
        button.set_tooltip_text(' · '.join(part for part in (
            candidate.title, format_authors(candidate.authors), candidate.published[:4])
            if part))
        connect_weak(button, 'clicked', self._on_cover_tile_clicked, candidate, spinner)
        return button

    def _on_cover_tile_clicked(self, _button, candidate, spinner):
        if self._busy:
            return
        self._busy = True
        spinner.set_visible(True)
        urls = [url for url in (candidate.cover_url, candidate.thumbnail_url) if url]

        def done(data, error):
            self._busy = False
            spinner.set_visible(False)
            if data is None and len(urls) > 1:
                urls.pop(0)
                self._busy = True
                spinner.set_visible(True)
                self._run(online.fetch_cover, done, urls[0])
                return
            if data is None:
                self.app.toast(str(error) if error else _('No cover found'))
                return
            if self.callback(data) is not False:
                self.force_close()

        self._run(online.fetch_cover, done, urls[0])

    def _on_candidate_activated(self, _list, row):
        candidate = getattr(row, 'candidate', None)
        if candidate is None or self._busy:
            return
        self._busy = True
        row.spinner.set_visible(True)

        def done(completed, error):
            self._busy = False
            row.spinner.set_visible(False)
            self.show_comparison(completed or candidate)

        self._run(online.complete, done, candidate)

    # -- the comparison ----------------------------------------------------------------------

    def show_comparison(self, candidate):
        """Push the comparison of `candidate` with the current values."""
        self.candidate = candidate
        self._found_cover = None
        self.fields = comparison(self.current, candidate)
        self.compare_list.remove_all()
        self._checks = []
        for field in self.fields:
            self.compare_list.append(self._compare_row(field))
        same = not self.fields
        self.compare_list.set_visible(not same)
        if same:
            self.compare_label.set_text(_('This match has nothing the book does not have.'))
        else:
            # Translators: on the comparison page; {source} is "Open Library".
            self.compare_label.set_text(_('Checked fields are filled in from {source}. '
                                          'Nothing is saved until you save the book.').format(
                source=candidate.source_name))
        self.compare_scrolled.get_vadjustment().set_value(0)
        self._update_apply()
        if self.navigation_view.get_visible_page() is not self.compare_page:
            self.navigation_view.push(self.compare_page)

    def _compare_row(self, field):
        row = Gtk.ListBoxRow(activatable=True)
        box = Gtk.Box(spacing=12, margin_top=10, margin_bottom=10, margin_start=12,
                      margin_end=12)
        check = Gtk.CheckButton(active=field.checked, valign=Gtk.Align.START)
        check.update_property([Gtk.AccessibleProperty.LABEL], [field.label])
        connect_weak(check, 'toggled', self._on_check_toggled, field)
        box.append(check)
        text = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=2, hexpand=True)
        heading = Gtk.Label(label=field.label, xalign=0)
        heading.add_css_class('caption-heading')
        heading.add_css_class('dimmed')
        text.append(heading)
        if field.key == 'cover':
            covers = Gtk.Box(spacing=18, margin_top=4)
            found = self._picture(COMPARE_COVER_WIDTH, COMPARE_COVER_HEIGHT,
                                  self.candidate.thumbnail_url or self.candidate.cover_url,
                                  label=_('The cover found'))
            covers.append(found)
            if self.current.get('cover') is not None:
                current = Gtk.Picture(paintable=self.current['cover'],
                                      content_fit=Gtk.ContentFit.COVER, can_shrink=True,
                                      width_request=COMPARE_COVER_WIDTH,
                                      height_request=COMPARE_COVER_HEIGHT)
                current.add_css_class('card')
                current.add_css_class('fetch-thumbnail')
                current.update_property([Gtk.AccessibleProperty.LABEL],
                                        [_('The current cover')])
                column = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=4,
                                 valign=Gtk.Align.END)
                column.append(fixed_size(current, COMPARE_COVER_WIDTH * 2 // 3,
                                         COMPARE_COVER_HEIGHT * 2 // 3))
                now = Gtk.Label(label=_('Now'))
                now.add_css_class('caption')
                now.add_css_class('dimmed')
                column.append(now)
                covers.append(column)
            else:
                now = Gtk.Label(label=_('Now none'), valign=Gtk.Align.END)
                now.add_css_class('caption')
                now.add_css_class('dimmed')
                covers.append(now)
            text.append(covers)
        else:
            found = Gtk.Label(label=field.found, xalign=0, wrap=True,
                              wrap_mode=Pango.WrapMode.WORD_CHAR, selectable=False)
            if field.key == 'description':
                found.set_lines(6)
                found.set_ellipsize(Pango.EllipsizeMode.END)
            text.append(found)
            if field.current:
                current = field.current
                if len(current) > 160:
                    current = current[:157].rstrip() + '…'
                # Translators: under a found value, the book's current one.
                was = Gtk.Label(label=_('Now: {value}').format(value=current), xalign=0,
                                wrap=True, wrap_mode=Pango.WrapMode.WORD_CHAR)
            else:
                was = Gtk.Label(label=_('Now empty'), xalign=0)
            was.add_css_class('caption')
            was.add_css_class('dimmed')
            text.append(was)
        box.append(text)
        row.set_child(box)
        row.check = check
        self._checks.append(check)
        return row

    def _on_compare_activated(self, _list, row):
        check = getattr(row, 'check', None)
        if check is not None:
            check.set_active(not check.get_active())

    def _on_check_toggled(self, check, field):
        field.checked = check.get_active()
        self._update_apply()

    def _update_apply(self):
        enabled = any(field.checked for field in self.fields)
        self._actions['apply'].set_enabled(enabled)
        self._actions['select-all'].set_enabled(not all(field.checked
                                                        for field in self.fields))

    def select_all(self):
        for check in self._checks:
            check.set_active(True)

    def apply(self):
        """Hand the checked fields to on_apply and close (the cover fetched first when
        checked)."""
        if self.candidate is None or self._busy:
            return
        cover_field = next((field for field in self.fields
                            if field.key == 'cover' and field.checked), None)
        if cover_field is not None and self._found_cover is None:
            self._busy = True
            self.apply_button.set_sensitive(False)
            urls = [url for url in (self.candidate.cover_url, self.candidate.thumbnail_url)
                    if url]

            def done(data, error):
                if data is None and len(urls) > 1:
                    urls.pop(0)
                    self._run(online.fetch_cover, done, urls[0])
                    return
                self._busy = False
                self.apply_button.set_sensitive(True)
                if data is None:
                    log.info('cover: %s', error)
                    self.app.toast(_('The cover could not be downloaded'))
                    cover_field.checked = False
                self._found_cover = data or b''
                self.apply()

            self._run(online.fetch_cover, done, urls[0])
            return
        values = chosen_values(self.fields, self.candidate, self._found_cover or None)
        if values:
            self.callback(values)
        self.force_close()

    def _on_closed(self, _dialog):
        for task in self._tasks:
            task.cancel()
        self._tasks = []


def present(app, parent, values, on_apply, fetch=None):
    """Find Metadata for the fields in `values`; on_apply(values) with the chosen ones."""
    dialog = FetchMetadataDialog(app, values, on_apply, fetch=fetch)
    watch_dialog(dialog, parent)
    dialog.present(parent)
    dialog.search()
    return dialog


def present_covers(app, parent, values, on_choose, fetch=None):
    """Find Cover: on_choose(image bytes) with the cover clicked."""
    dialog = FetchMetadataDialog(app, values, on_choose, covers=True, fetch=fetch)
    watch_dialog(dialog, parent)
    dialog.present(parent)
    dialog.search()
    return dialog



def _weak_action(dialog, callback):
    """An action's handler that calls callback(dialog) while the dialog lives."""
    ref = dialog.weak_ref()

    def activate(*_args):
        instance = ref()
        if instance is not None:
            callback(instance)

    return activate
