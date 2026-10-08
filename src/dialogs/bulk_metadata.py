# SPDX-License-Identifier: GPL-2.0-or-later
# SPDX-FileCopyrightText: 2026 Jack Tully

"""Find Metadata for several books (the book menu's Find Metadata… on a selection).

    dialog = present(app, parent, book_ids, fetch=None)   # starts looking up at once
    dialog.start() / dialog.stop()      # the queue (bulk_metadata.Queue); stop keeps what
                                        # was found
    dialog.review()                     # push the review page
    dialog.selection()                  # {book_id: {group, …}} that Apply would write
    dialog.set_group(book_id, group, active)
    dialog.apply()                      # one undo step, a toast with Undo, closes

The first page lists the books with their state (Waiting, Searching…, Found, Check This
Match, Not Found, the error), a progress bar and Stop. Open Library is asked about once a
second (online's throttle), so the page says it takes a while. Review (once done or stopped)
pushes a page with "Replace Existing Details" (off: only empty fields are filled) and, per
book found, an expander with a check for the book and one per group of changes (Details,
Description, Cover, Tags) showing old → new; a match that is only likely is unchecked.
Books not found are listed at the end. Closing the dialog stops the queue; nothing is
written until Apply.
"""

import logging
from gettext import gettext as _
from gettext import ngettext

from gi.repository import Adw, GLib, Gtk

from .. import bulk_metadata, online
from ..widgets.util import connect_weak, connect_weak_call
from . import watch_dialog

log = logging.getLogger(__name__)

SECONDS_A_BOOK = 3  # a search, an edition and a work, a second apart
THUMB_WIDTH, THUMB_HEIGHT = 40, 60


def present(app, parent, book_ids, fetch=None):
    dialog = BulkMetadataDialog(app, book_ids, fetch=fetch)
    watch_dialog(dialog, parent)
    dialog.present(parent)
    dialog.start()
    return dialog


def _status_labels():
    return {'waiting': _('Waiting'), 'searching': _('Searching…'), 'found': _('Found'),
            'ambiguous': _('Check This Match'), 'not-found': _('Not Found'),
            'error': _('Could Not Search')}


def _shorten(text, limit=120):
    text = ' '.join((text or '').split())
    return text if len(text) <= limit else text[:limit - 1].rstrip() + '…'


def change_text(change):
    """'Publisher: Tidewater Press', or 'Publisher: Old → New' when there was a value."""
    if change.field == 'cover':
        return _('A new cover') if change.old else _('A cover, where there was none')
    if change.field == 'description':
        new = _shorten(change.new, 160)
        return _('Replaces the description: {new}').format(new=new) if change.old else new
    if change.field == 'tags':
        # Translators: tags found online, added to the book's own.
        return _('Adds {tags}').format(tags=_shorten(change.new, 100))
    if change.old:
        # Translators: a field's change in the review: "Publisher: Old Press → New Press".
        return _('{label}: {old} → {new}').format(label=change.label,
                                                  old=_shorten(change.old, 50),
                                                  new=_shorten(change.new, 50))
    return _('{label}: {new}').format(label=change.label, new=_shorten(change.new, 80))


class BulkMetadataDialog(Adw.Dialog):
    __gtype_name__ = 'BookcaseBulkMetadataDialog'

    def __init__(self, app, book_ids, fetch=None):
        super().__init__(title=_('Find Metadata'), content_width=560, content_height=680)
        self.app = app
        self.fetch = fetch
        books = [app.library.book(book_id) for book_id in book_ids]
        self.lookups = [bulk_metadata.Lookup.of(book) for book in books if book is not None]
        self.queue = None
        self.finished = False
        self.stopped = False
        self.rows = {}  # book id -> its row on the first page
        self.book_checks = {}  # book id -> bool
        self.group_checks = {}  # book id -> {group: bool}
        self._review_rows = []
        self._build()
        self.connect('closed', self._on_closed)
        self._update_progress()

    # -- building ----------------------------------------------------------------------------

    def _build(self):
        self.navigation = Adw.NavigationView()
        self.set_child(self.navigation)

        header = Adw.HeaderBar(show_start_title_buttons=False, show_end_title_buttons=False)
        cancel = Gtk.Button(label=_('_Cancel'), use_underline=True)
        connect_weak_call(cancel, 'clicked', self.close)
        header.pack_start(cancel)
        self.review_button = Gtk.Button(label=_('_Review'), use_underline=True,
                                        sensitive=False)
        self.review_button.add_css_class('suggested-action')
        connect_weak_call(self.review_button, 'clicked', self.review)
        header.pack_end(self.review_button)

        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=12, margin_top=12,
                      margin_bottom=24, margin_start=12, margin_end=12)
        self.progress_label = Gtk.Label(xalign=0, wrap=True)
        self.progress_label.add_css_class('heading')
        self.progress_bar = Gtk.ProgressBar()
        self.note_label = Gtk.Label(xalign=0, wrap=True, label=_(
            'Open Library is asked about one book every few seconds, as it asks of apps. '
            'Nothing changes until you review and apply.'))
        self.note_label.add_css_class('caption')
        self.note_label.add_css_class('dimmed')
        self.stop_button = Gtk.Button(label=_('_Stop'), use_underline=True,
                                      halign=Gtk.Align.START)
        connect_weak_call(self.stop_button, 'clicked', self.stop)
        self.books_list = Gtk.ListBox(selection_mode=Gtk.SelectionMode.NONE, margin_top=6)
        self.books_list.add_css_class('boxed-list')
        for child in (self.progress_label, self.progress_bar, self.note_label,
                      self.stop_button, self.books_list):
            box.append(child)
        for lookup in self.lookups:
            row = self._book_row(lookup)
            self.rows[lookup.book_id] = row
            self.books_list.append(row)
        page_view = Adw.ToolbarView(content=self._scrolled(box))
        page_view.add_top_bar(header)
        self.progress_page = Adw.NavigationPage(title=_('Find Metadata'), child=page_view,
                                                tag='progress')
        self.navigation.add(self.progress_page)

        review_header = Adw.HeaderBar(show_end_title_buttons=False)
        self.apply_button = Gtk.Button(label=_('_Apply'), use_underline=True)
        self.apply_button.add_css_class('suggested-action')
        connect_weak_call(self.apply_button, 'clicked', self.apply)
        review_header.pack_end(self.apply_button)
        self.review_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=24,
                                  margin_top=12, margin_bottom=24, margin_start=12,
                                  margin_end=12)
        options = Adw.PreferencesGroup()
        self.replace_row = Adw.SwitchRow(
            title=_('Replace Existing Details'),
            subtitle=_('Off: only empty fields are filled. Titles and authors are never '
                       'changed.'))
        connect_weak_call(self.replace_row, 'notify::active', self._fill_review)
        options.add(self.replace_row)
        self.review_box.append(options)
        self.update_group = Adw.PreferencesGroup()
        self.update_list = Gtk.ListBox(selection_mode=Gtk.SelectionMode.NONE)
        self.update_list.add_css_class('boxed-list')
        self.update_group.add(self.update_list)
        self.review_box.append(self.update_group)
        self.nothing_status = Adw.StatusPage(icon_name='edit-find-symbolic',
                                             title=_('Nothing to Change'),
                                             description=_('The books found already have '
                                                           'what Open Library knows'))
        self.nothing_status.add_css_class('compact')
        self.review_box.append(self.nothing_status)
        self.missing_group = Adw.PreferencesGroup(title=_('Not Found'))
        self.missing_list = Gtk.ListBox(selection_mode=Gtk.SelectionMode.NONE)
        self.missing_list.add_css_class('boxed-list')
        self.missing_group.add(self.missing_list)
        self.review_box.append(self.missing_group)
        review_view = Adw.ToolbarView(content=self._scrolled(self.review_box))
        review_view.add_top_bar(review_header)
        self.review_page = Adw.NavigationPage(title=_('Review'), child=review_view,
                                              tag='review')

    def _scrolled(self, child):
        clamp = Adw.Clamp(maximum_size=600, child=child)
        return Gtk.ScrolledWindow(child=clamp, hscrollbar_policy=Gtk.PolicyType.NEVER,
                                  vexpand=True)

    def _book_row(self, lookup):
        book = lookup.book
        row = Adw.ActionRow(title=book.title, subtitle=book.author, use_markup=False,
                            title_lines=1, subtitle_lines=1)
        row.spinner = Adw.Spinner(visible=False, valign=Gtk.Align.CENTER)
        row.status = Gtk.Label(valign=Gtk.Align.CENTER, xalign=1, wrap=True,
                               max_width_chars=16, justify=Gtk.Justification.RIGHT)
        row.status.add_css_class('caption')
        row.add_suffix(row.spinner)
        row.add_suffix(row.status)
        self._show_status(row, lookup)
        return row

    # -- looking up --------------------------------------------------------------------------

    def status_label(self, lookup):
        return _status_labels()[lookup.status]

    def _show_status(self, row, lookup):
        row.spinner.set_visible(lookup.status == 'searching')
        row.status.set_text(self.status_label(lookup))
        row.status.set_tooltip_text(lookup.error or None)
        for name in ('success', 'warning', 'dimmed', 'error'):
            row.status.remove_css_class(name)
        style = {'found': 'success', 'ambiguous': 'warning', 'not-found': 'dimmed',
                 'error': 'error', 'waiting': 'dimmed'}.get(lookup.status)
        if style:
            row.status.add_css_class(style)
        if lookup.status in ('found', 'ambiguous') and lookup.candidate is not None:
            found = lookup.candidate
            parts = [] if online.normalize(found.title) == online.normalize(lookup.book.title) \
                else [found.title]
            parts += [', '.join(found.authors[:2]), found.published[:4], found.publisher]
            row.set_subtitle(' · '.join(part for part in parts if part))

    def _google_key(self):
        settings = getattr(self.app, 'settings', None)
        try:
            return settings.get_string('google-books-key').strip() if settings else ''
        except (TypeError, AttributeError):
            return ''

    def start(self):
        if self.queue is not None or not self.lookups:
            self.finished = not self.lookups
            self._update_progress()
            return
        self.queue = bulk_metadata.Queue(self.lookups, self._on_update, self._on_done,
                                         fetch=self.fetch, google_key=self._google_key())
        self.queue.start()

    def stop(self):
        if self.queue is not None and not self.finished:
            self.stopped = True
            self.queue.cancel()
            self.stop_button.set_sensitive(False)
            self.progress_label.set_text(_('Stopping…'))

    def _on_update(self, lookup):
        row = self.rows.get(lookup.book_id)
        if row is not None:
            self._show_status(row, lookup)
        self._update_progress()

    def _on_done(self, cancelled):
        self.finished = True
        self.stopped = cancelled
        self._update_progress()

    def _counts(self):
        counts = {}
        for lookup in self.lookups:
            counts[lookup.status] = counts.get(lookup.status, 0) + 1
        return counts

    def _update_progress(self):
        total = len(self.lookups)
        counts = self._counts()
        done = sum(count for status, count in counts.items()
                   if status not in ('waiting', 'searching'))
        self.progress_bar.set_fraction(done / total if total else 1)
        if self.finished:
            parts = [ngettext('{n} found', '{n} found', counts.get('found', 0)).format(
                n=counts.get('found', 0))]
            if counts.get('ambiguous'):
                parts.append(ngettext('{n} to check', '{n} to check',
                                      counts['ambiguous']).format(n=counts['ambiguous']))
            missing = counts.get('not-found', 0) + counts.get('error', 0)
            if missing:
                parts.append(ngettext('{n} not found', '{n} not found', missing).format(
                    n=missing))
            prefix = _('Stopped') if self.stopped else _('Done')
            # Translators: the summary after looking books up: "Done: 8 found, 2 to check".
            self.progress_label.set_text(_('{state}: {counts}').format(
                state=prefix, counts=', '.join(parts)))
            self.stop_button.set_visible(False)
            self.note_label.set_visible(False)
        elif not self.stopped:
            left = max(total - done, 0) * SECONDS_A_BOOK
            minutes = max(1, round(left / 60))
            when = ngettext('about {n} minute left', 'about {n} minutes left',
                            minutes).format(n=minutes)
            # Translators: progress of Find Metadata: "Looking up 3 of 12 · about 1 minute
            # left".
            self.progress_label.set_text(_('Looking up {number} of {total} · {when}').format(
                number=min(done + 1, total), total=total, when=when))
        found = counts.get('found', 0) + counts.get('ambiguous', 0)
        self.review_button.set_sensitive(self.finished and found > 0)

    # -- reviewing ---------------------------------------------------------------------------

    def review(self):
        for lookup in self.lookups:
            if lookup.book_id not in self.book_checks:
                self.book_checks[lookup.book_id] = lookup.status == 'found'
                self.group_checks[lookup.book_id] = {
                    group: True for group in bulk_metadata.GROUPS}
        self._fill_review()
        if self.navigation.get_visible_page() is not self.review_page:
            self.navigation.push(self.review_page)

    def _changes(self, lookup):
        book = self.app.library.book(lookup.book_id) or lookup.book
        return bulk_metadata.changes(book, lookup, replace=self.replace_row.get_active())

    def _fill_review(self):
        self.update_list.remove_all()
        self.missing_list.remove_all()
        self._review_rows = []
        updating = 0
        for lookup in self.lookups:
            if lookup.status in ('found', 'ambiguous') and lookup.candidate is not None:
                changes = self._changes(lookup)
                if changes:
                    row = self._review_row(lookup, changes)
                    self.update_list.append(row)
                    self._review_rows.append(row)
                    updating += 1
            elif lookup.status in ('not-found', 'error'):
                row = Adw.ActionRow(title=lookup.book.title, use_markup=False,
                                    subtitle=lookup.error or lookup.book.author)
                row.add_css_class('dimmed')
                self.missing_list.append(row)
        self.update_group.set_title(ngettext('{n} Book to Update', '{n} Books to Update',
                                             updating).format(n=updating))
        self.update_group.set_visible(updating > 0)
        self.nothing_status.set_visible(updating == 0)
        self.missing_group.set_visible(self.missing_list.get_first_child() is not None)
        self._update_apply()

    def _review_row(self, lookup, changes):
        book = lookup.book
        names = bulk_metadata.group_names()
        row = Adw.ExpanderRow(title=GLib.markup_escape_text(book.title), title_lines=2)
        if lookup.status == 'ambiguous':
            found = lookup.candidate
            row.set_subtitle(GLib.markup_escape_text(_(
                'Check this match: “{title}” by {authors}').format(
                    title=found.title, authors=', '.join(found.authors[:2]) or '?')))
        else:
            row.set_subtitle(GLib.markup_escape_text(', '.join(
                names[group] for group in bulk_metadata.GROUPS if group in changes)))
        check = Gtk.CheckButton(valign=Gtk.Align.CENTER,
                                active=self.book_checks.get(lookup.book_id, False))
        check.update_property([Gtk.AccessibleProperty.LABEL], [book.title])
        connect_weak(check, 'toggled', self._on_book_toggled, lookup.book_id)
        row.add_prefix(check)
        row.book_id = lookup.book_id
        row.group_rows = {}
        for group in bulk_metadata.GROUPS:
            if group not in changes:
                continue
            child = Adw.ActionRow(title=names[group], use_markup=False)
            child.set_subtitle('\n'.join(change_text(change) for change in changes[group]))
            child.set_subtitle_lines(6)
            group_check = Gtk.CheckButton(
                valign=Gtk.Align.CENTER,
                active=self.group_checks[lookup.book_id].get(group, True))
            group_check.update_property([Gtk.AccessibleProperty.LABEL], [names[group]])
            connect_weak(group_check, 'toggled', self._on_group_toggled, lookup.book_id, group)
            child.add_prefix(group_check)
            child.set_activatable_widget(group_check)
            if group == 'cover' and lookup.cover:
                child.add_suffix(self._cover_picture(lookup.cover))
            child.check = group_check
            row.add_row(child)
            row.group_rows[group] = child
        return row

    def _cover_picture(self, data):
        from .edit_metadata import texture_from_bytes
        from .fetch_metadata import fixed_size

        picture = Gtk.Picture(content_fit=Gtk.ContentFit.COVER, can_shrink=True,
                              width_request=THUMB_WIDTH, height_request=THUMB_HEIGHT,
                              margin_top=6, margin_bottom=6)
        picture.add_css_class('card')
        picture.add_css_class('fetch-thumbnail')
        picture.update_property([Gtk.AccessibleProperty.LABEL], [_('The cover found')])
        texture = texture_from_bytes(data)
        if texture is not None:
            picture.set_paintable(texture)
        return fixed_size(picture, THUMB_WIDTH, THUMB_HEIGHT)

    def _on_book_toggled(self, check, book_id):
        self.book_checks[book_id] = check.get_active()
        self._update_apply()

    def _on_group_toggled(self, check, book_id, group):
        self.group_checks[book_id][group] = check.get_active()
        self._update_apply()

    def set_group(self, book_id, group, active):
        for row in self._review_rows:
            if row.book_id == book_id and group in row.group_rows:
                row.group_rows[group].check.set_active(active)
                return
        self.group_checks[book_id][group] = active
        self._update_apply()

    def selection(self):
        chosen = {}
        for row in self._review_rows:
            if not self.book_checks.get(row.book_id):
                continue
            groups = {group for group in row.group_rows
                      if self.group_checks[row.book_id].get(group, True)}
            if groups:
                chosen[row.book_id] = groups
        return chosen

    def _update_apply(self):
        count = len(self.selection())
        self.apply_button.set_sensitive(count > 0)
        self.apply_button.set_label(ngettext('_Apply to {n} Book', '_Apply to {n} Books',
                                             count).format(n=count) if count else _('_Apply'))

    def apply(self):
        selection = self.selection()
        if not selection:
            return 0
        try:
            changed = bulk_metadata.apply(self.app.library, self.app.covers, self.lookups,
                                          selection, replace=self.replace_row.get_active())
        except Exception as error:
            self.app.report(error, _('Could not save the metadata'))
            return 0
        if changed == 1:
            book = self.app.library.book(next(iter(selection)))
            self.app.toast(_('Found metadata for “{title}”').format(
                title=book.title if book else ''), undo=True)
        elif changed:
            self.app.toast(ngettext('Updated {n} book', 'Updated {n} books', changed).format(
                n=changed), undo=True)
        self.force_close()
        return changed

    def _on_closed(self, *_args):
        if self.queue is not None:
            self.queue.cancel()

