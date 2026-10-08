# SPDX-License-Identifier: GPL-2.0-or-later
# SPDX-FileCopyrightText: 2026 Jack Tully

"""A book in an online catalogue: its cover, title, authors, series, what it is about, and
Download.

    dialog = present(app, parent, catalog, entry, client)   # an opds.Entry of the catalogue

Download takes the best format Bookcase reads (opds.best_acquisition: EPUB before Kobo
EPUB, AZW3, MOBI, FB2, PDF, CBZ); the Formats list offers each one the catalogue has (a
format Bookcase cannot take says why: a price, a loan, DRM, a format it does not read).
While the book downloads the sheet shows the progress (Cancel stops it), and once it is in
the library (or was already: same identifier, or same title and an author in common) Read
and Show in Library. The download goes on when the sheet is closed (pages/catalog.py's
downloads(), which toasts when it is done).
"""

import logging
from gettext import gettext as _

from gi.repository import Adw, GLib, Gtk

from .. import opds
from ..widgets.markup import html_to_markup
from ..widgets.remote_cover import RemoteCover
from ..widgets.util import connect_weak
from . import watch_dialog

log = logging.getLogger(__name__)

COVER_WIDTH = 150


def size_text(size):
    if not size:
        return ''
    return GLib.format_size(size)


def acquisition_note(acquisition):
    """Why Bookcase cannot take a format, or '' when it can."""
    if acquisition.price:
        # Translators: a book on sale in a catalogue: "Buy · 4.99 EUR".
        return _('Buy · {price} {currency}').format(
            price=f'{acquisition.price:.2f}', currency=acquisition.currency).strip()
    if acquisition.drm:
        return _('Protected by DRM')
    if acquisition.kind == 'borrow':
        return _('On loan from the library’s website')
    if acquisition.kind in ('buy', 'subscribe'):
        return _('Sold on the catalogue’s website')
    if acquisition.format is None:
        return _('A format Bookcase does not read')
    return ''


@Gtk.Template(resource_path='/io/github/jackicus/Bookcase/catalog_entry.ui')
class CatalogEntryDialog(Adw.Dialog):
    __gtype_name__ = 'BookcaseCatalogEntryDialog'

    cover_box = Gtk.Template.Child()
    title_label = Gtk.Template.Child()
    authors_label = Gtk.Template.Child()
    series_label = Gtk.Template.Child()
    facts_label = Gtk.Template.Child()
    action_stack = Gtk.Template.Child()
    download_button = Gtk.Template.Child()
    progress_bar = Gtk.Template.Child()
    cancel_button = Gtk.Template.Child()
    read_button = Gtk.Template.Child()
    show_button = Gtk.Template.Child()
    none_label = Gtk.Template.Child()
    status_label = Gtk.Template.Child()
    summary_label = Gtk.Template.Child()
    categories_label = Gtk.Template.Child()
    formats_group = Gtk.Template.Child()

    def __init__(self, app, catalog, entry, client):
        super().__init__()
        self.app = app
        self.catalog = catalog
        self.entry = entry
        self.client = client
        self.book_id = None
        self._pulse = None
        self._format_buttons = []
        from ..pages.catalog import downloads

        self.downloads = downloads()
        self.set_title(entry.title)
        self.cover = RemoteCover(width=COVER_WIDTH)
        self.cover.add_css_class('large')
        self.cover.set_entry(entry, client, url=entry.cover or entry.thumbnail)
        self.cover_box.append(self.cover)
        self._fill()
        connect_weak(self.download_button, 'clicked', self._on_download)
        connect_weak(self.cancel_button, 'clicked', self._on_cancel)
        connect_weak(self.read_button, 'clicked', self._on_read)
        connect_weak(self.show_button, 'clicked', self._on_show)
        connect_weak(self.downloads, 'progress', self._on_progress)
        connect_weak(self.downloads, 'finished', self._on_finished)
        connect_weak(app.library, 'changed', self._on_library_changed)
        self.connect('closed', self._on_closed)
        self.update_state()

    def _fill(self):
        entry = self.entry
        self.title_label.set_text(entry.title)
        self.authors_label.set_text(entry.author)
        self.authors_label.set_visible(bool(entry.authors))
        series = entry.series
        if series and entry.series_index:
            # Translators: a book's place in its series: "Coastal Tales, book 2".
            series = _('{series}, book {number}').format(series=series,
                                                         number=f'{entry.series_index:g}')
        self.series_label.set_text(series)
        self.series_label.set_visible(bool(series))
        facts = [fact for fact in (entry.issued[:4], _language_name(entry.language),
                                   entry.publisher) if fact]
        self.facts_label.set_text(' · '.join(facts))
        self.facts_label.set_visible(bool(facts))
        markup = html_to_markup(entry.summary) if entry.summary else ''
        self.summary_label.set_markup(markup)
        self.summary_label.set_visible(bool(markup))
        self.categories_label.set_text(', '.join(entry.categories[:12]))
        self.categories_label.set_visible(bool(entry.categories))
        best = opds.best_acquisition(entry)
        if best is not None:
            self.download_button.set_label(_('_Download {format}').format(
                format=best.format_name))
        for acquisition in sorted(entry.acquisitions, key=lambda a: not a.available):
            self.formats_group.add(self._format_row(acquisition))
        self.formats_group.set_visible(bool(entry.acquisitions))

    def _format_row(self, acquisition):
        note = acquisition_note(acquisition)
        details = [part for part in (acquisition.title if acquisition.title !=
                                     acquisition.format_name else '',
                                     size_text(acquisition.size), note) if part]
        row = Adw.ActionRow(title=GLib.markup_escape_text(acquisition.format_name),
                            subtitle=GLib.markup_escape_text(' · '.join(details)))
        if acquisition.available:
            button = Gtk.Button(icon_name='folder-download-symbolic', valign=Gtk.Align.CENTER,
                                tooltip_text=_('Download {format}').format(
                                    format=acquisition.format_name))
            button.add_css_class('flat')
            ref = self.weak_ref()
            button.connect('clicked', lambda _b, a=acquisition: (
                ref()._download(a) if ref() is not None else None))
            row.add_suffix(button)
            row.set_activatable_widget(button)
            self._format_buttons.append(button)
        else:
            row.add_css_class('dimmed')
        return row

    # -- state -------------------------------------------------------------------------------

    def update_state(self):
        state = self.downloads.state(self.entry.key)
        book_id = state[1] if state is not None and state[0] == 'done' else None
        if book_id is None or self.app.library.book(book_id) is None:
            book_id = opds.find_in_library(self.app.library, self.entry)
        self.book_id = book_id
        downloading = state is not None and state[0] == 'downloading'
        for button in self._format_buttons:
            button.set_sensitive(not downloading)
        self.status_label.set_visible(False)
        if downloading:
            self._show_progress(state[1])
            self.action_stack.set_visible_child_name('progress')
        elif book_id is not None:
            self._stop_pulse()
            self.action_stack.set_visible_child_name('library')
            self.status_label.set_text(_('In your library'))
            self.status_label.add_css_class('success')
            self.status_label.set_visible(True)
        elif opds.best_acquisition(self.entry) is not None:
            self._stop_pulse()
            self.action_stack.set_visible_child_name('download')
            if state is not None and state[0] == 'failed':
                self.status_label.set_text(state[1])
                self.status_label.remove_css_class('success')
                self.status_label.add_css_class('error')
                self.status_label.set_visible(True)
        else:
            self.action_stack.set_visible_child_name('none')
            notes = {acquisition_note(a) for a in self.entry.acquisitions} - {''}
            self.none_label.set_text(sorted(notes)[0] if len(notes) == 1 else
                                     _('Bookcase cannot download this book'))

    def _show_progress(self, fraction):
        if fraction < 0:
            if self._pulse is None:
                self._pulse = GLib.timeout_add(120, self._on_pulse)
        else:
            self._stop_pulse()
            self.progress_bar.set_fraction(fraction)

    def _on_pulse(self):
        self.progress_bar.pulse()
        return GLib.SOURCE_CONTINUE

    def _stop_pulse(self):
        if self._pulse is not None:
            GLib.source_remove(self._pulse)
            self._pulse = None

    # -- events ----------------------------------------------------------------------------

    def _download(self, acquisition=None):
        from ..pages.catalog import start_download

        if start_download(self.entry, self.client, acquisition):
            self.progress_bar.set_fraction(0)
            self.update_state()

    def _on_download(self, _button):
        self._download()

    def _on_cancel(self, _button):
        self.downloads.cancel(self.entry.key)

    def _on_read(self, _button):
        if self.book_id is not None:
            self.app.open_book(self.book_id)

    def _on_show(self, _button):
        if self.book_id is None:
            return
        window = self.app.window()
        book_id = self.book_id
        self.close()
        if window is not None:
            window.show_book(book_id)

    def _on_progress(self, _downloads, key, fraction):
        if key == self.entry.key:
            self._show_progress(fraction)

    def _on_finished(self, _downloads, key, _book_id, _message):
        if key == self.entry.key:
            self.update_state()

    def _on_library_changed(self, _library, kind):
        if kind == 'books':
            self.update_state()

    def _on_closed(self, _dialog):
        self._stop_pulse()


def _language_name(code):
    """A language code as its name ('en' -> 'English') for the common ones, else the code."""
    if not code:
        return ''
    names = {'en': _('English'), 'fr': _('French'), 'de': _('German'), 'es': _('Spanish'),
             'it': _('Italian'), 'pl': _('Polish'), 'pt': _('Portuguese'), 'nl': _('Dutch'),
             'fi': _('Finnish'), 'sv': _('Swedish'), 'la': _('Latin'), 'ru': _('Russian'),
             'zh': _('Chinese'), 'ja': _('Japanese'), 'el': _('Greek')}
    return names.get(code.split('-')[0].lower(), code)


def present(app, parent, catalog, entry, client):
    dialog = CatalogEntryDialog(app, catalog, entry, client)
    watch_dialog(dialog, parent)
    dialog.present(parent)
    return dialog
