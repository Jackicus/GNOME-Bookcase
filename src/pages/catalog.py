# SPDX-License-Identifier: GPL-2.0-or-later
# SPDX-FileCopyrightText: 2026 Jack Tully

"""A page of an online catalogue, pushed from Discover: its sections as a list, its books as
covers; and what the catalogue pages share (the list of catalogues, their clients, the
downloads).

    page = CatalogPage(catalog)                         # the catalogue's first page
    page = CatalogPage(catalog, url=URL, feed=feed)     # a section (fetched already)
    page = CatalogPage(catalog, search=(feed, 'terms')) # a search of the catalogue
    load_catalogs(); save_catalogs(catalogs)            # the `catalogs` setting
    client_for(catalog); forget_client(catalog)         # one opds.Client per catalogue
    downloads()                                         # the app's opds.Downloads
    open_entry(page, catalog, client, entry, row=None)  # a section, or a book's sheet

The feed is fetched in a thread (opds.run_async; a spinner meanwhile). Sections are rows of
a boxed list; books (and sections that are books, such as ManyBooks' lists) are tiles of
covers in a FlowBox: a catalogue sends a page of 25 to 100 entries at a time, and sections
and books share one scroller. Scrolling to the end (or More) appends the next page. A
section opens as a new page, unless it turns out to be a single book's feed (Project
Gutenberg's), which opens the book's sheet (dialogs/catalog_entry.py), as a book's tile
does. The search entry (when the catalogue can be searched) pushes the results; the
catalogue's filters (OPDS facets) are menus under the header bar and reload the page.
Books already in the library carry a mark, kept up to date while the page is shown.

Errors replace the content with a status page: no connection (Try Again), sign-in
required (Sign In…, the catalogue's dialog), not a catalogue (Edit Catalogue…), anything
else. A download ends in a toast, wherever the user is: "Added “Title”" with Read.
"""

import logging
import os
import shutil
import threading
from gettext import gettext as _

from gi.repository import Adw, Gio, GLib, Gtk

from .. import opds
from ..widgets.book_tile import CoverSize
from ..widgets.markup import html_to_text
from ..widgets.remote_cover import CatalogTile, thumbnails
from ..widgets.util import connect_weak
from . import PageListener, app

log = logging.getLogger(__name__)

COVER_WIDTH = 150
COMPACT_COVER = 112

_clients = {}
_clients_lock = threading.Lock()
_downloads = None


# -- what the catalogue pages share --------------------------------------------------------

def load_catalogs():
    return opds.load_catalogs(app().settings.get_string('catalogs'))


def save_catalogs(catalogs):
    app().settings.set_string('catalogs', opds.dump_catalogs(catalogs))


def find_catalog(catalog_id):
    return next((c for c in load_catalogs() if c.id == catalog_id), None)


def client_for(catalog):
    """The catalogue's client (made on first use, in a worker thread: the password comes
    from the keyring)."""
    key = (catalog.id, catalog.url, catalog.username)
    with _clients_lock:
        client = _clients.get(key)
    if client is None:
        client = opds.Client.for_catalog(catalog)
        with _clients_lock:
            client = _clients.setdefault(key, client)
    return client


def forget_client(catalog):
    with _clients_lock:
        for key in [key for key in _clients if key[0] == catalog.id]:
            del _clients[key]


def icon_path(catalog):
    return os.path.join(opds.cache_dir(), 'catalog-icons', catalog.id)


def _save_icon(catalog, feed, client):
    """In a worker: the catalogue's icon, kept for its card on Discover."""
    path = icon_path(catalog)
    if not feed.icon or os.path.exists(path):
        return
    try:
        source = thumbnails().fetch(feed.icon, client)
        os.makedirs(os.path.dirname(path), exist_ok=True)
        shutil.copyfile(source, path)
    except (opds.OpdsError, OSError) as error:
        log.info('the icon of %s: %s', catalog.title, error)


def downloads():
    """The app's downloads; each one ends in a toast."""
    global _downloads
    if _downloads is None:
        _downloads = opds.Downloads(app().importer)
        _downloads.titles = {}
        _downloads.connect('finished', _on_download_finished)
    return _downloads


def start_download(entry, client, acquisition=None):
    acquisition = acquisition or opds.best_acquisition(entry)
    if acquisition is None:
        return False
    manager = downloads()
    manager.titles[entry.key] = entry.title
    manager.start(entry.key, client, acquisition, entry)
    return True


def _on_download_finished(manager, key, book_id, message):
    application = app()
    title = manager.titles.get(key, '')
    if book_id:
        report = manager.reports.get(key)
        if report is not None and not report.added and not report.merged:
            text = _('“{title}” is in your library already').format(title=title)
        else:
            text = _('Added “{title}”').format(title=title)
        toast = application.toast(text)
        if toast is not None:
            toast.set_button_label(_('Read'))
            toast.connect('button-clicked', lambda *_args: application.open_book(book_id))
    elif message:
        application.toast(_('Could not download “{title}”: {error}').format(
            title=title, error=message))


def open_entry(page, catalog, client, entry, row=None):
    """Open an entry of a catalogue page: a book's sheet, or a section (fetched first: a
    section that is a single book's feed opens its sheet instead of a page)."""
    from ..dialogs import catalog_entry

    window = page.get_root()
    if entry.is_book:
        return catalog_entry.present(app(), window, catalog, entry, client)
    if getattr(page, '_opening', None) is not None:
        return None
    ref = page.weak_ref()
    _set_row_busy(row, True)

    def loaded(feed, error):
        target = ref()
        _set_row_busy(row, False)
        if target is None:
            return
        target._opening = None
        if error is not None:
            app().toast(str(error))
            return
        book = feed.single_book
        if book is not None:
            catalog_entry.present(app(), target.get_root(), catalog, book, client)
        else:
            target.get_root().push(CatalogPage(catalog, url=entry.href, feed=feed,
                                               client=client))

    page._opening = opds.run_async(client.feed, loaded, entry.href)
    return None


def _set_row_busy(row, busy):
    if row is None or not hasattr(row, 'busy'):
        return
    row.busy.set_visible(busy)
    row.arrow.set_visible(not busy)


class _SectionRow(Adw.ActionRow):
    """A section of a catalogue: its title, what it says of itself, how many books."""

    def __init__(self, entry):
        super().__init__(title=GLib.markup_escape_text(entry.title), activatable=True)
        self.entry = entry
        summary = html_to_text(entry.summary) if entry.summary else ''
        if not summary and entry.authors:
            summary = entry.author
        if summary:
            self.set_subtitle(GLib.markup_escape_text(summary))
            self.set_subtitle_lines(2)
        if entry.count is not None:
            count = Gtk.Label(label=f'{entry.count:n}', valign=Gtk.Align.CENTER)
            count.add_css_class('dimmed')
            count.add_css_class('numeric')
            self.add_suffix(count)
        self.busy = Adw.Spinner(visible=False, valign=Gtk.Align.CENTER)
        self.add_suffix(self.busy)
        self.arrow = Gtk.Image(icon_name='go-next-symbolic',
                               accessible_role=Gtk.AccessibleRole.PRESENTATION)
        self.add_suffix(self.arrow)


@Gtk.Template(resource_path='/io/github/jackicus/Bookcase/catalog.ui')
class CatalogPage(Adw.NavigationPage):
    __gtype_name__ = 'BookcaseCatalogPage'

    compact_breakpoint = Gtk.Template.Child()
    window_title = Gtk.Template.Child()
    more_button = Gtk.Template.Child()
    search_button = Gtk.Template.Child()
    search_bar = Gtk.Template.Child()
    search_entry = Gtk.Template.Child()
    facet_scroller = Gtk.Template.Child()
    facet_box = Gtk.Template.Child()
    stack = Gtk.Template.Child()
    scroller = Gtk.Template.Child()
    nav_clamp = Gtk.Template.Child()
    nav_list = Gtk.Template.Child()
    book_box = Gtk.Template.Child()
    more_box = Gtk.Template.Child()
    more_spinner = Gtk.Template.Child()
    more_page_button = Gtk.Template.Child()
    status_page = Gtk.Template.Child()
    status_button = Gtk.Template.Child()

    def __init__(self, catalog, url='', feed=None, search=None, client=None, title=None):
        super().__init__()
        self.catalog = catalog
        self.url = url or catalog.url
        self.search = search
        self.client = client
        self.feed = None
        self._fixed_title = title
        self._next = ''
        self._task = None
        self._more_task = None
        self._opening = None
        self._status_action = None
        self._tiles = []  # CatalogTiles, to mark the books the library has
        self._size = CoverSize(width=COVER_WIDTH)
        if search is not None:
            self._fixed_title = _('Search Results')
        self.set_title(self._fixed_title or catalog.title)
        self.window_title.set_title(self._fixed_title or catalog.title)
        if search is not None:
            self.window_title.set_subtitle(_('“{terms}” in {catalog}').format(
                terms=search[1], catalog=catalog.title))
        self._add_actions()
        connect_weak(self.search_entry, 'activate', self._on_search_activate)
        connect_weak(self.search_entry, 'stop-search', self._on_stop_search)
        connect_weak(self.nav_list, 'row-activated', self._on_row_activated)
        connect_weak(self.book_box, 'child-activated', self._on_tile_activated)
        connect_weak(self.scroller, 'edge-reached', self._on_edge_reached)
        connect_weak(self.more_page_button, 'clicked', self._on_more_clicked)
        connect_weak(self.status_button, 'clicked', self._on_status_clicked)
        connect_weak(self.compact_breakpoint, 'apply', self._on_compact_apply)
        connect_weak(self.compact_breakpoint, 'unapply', self._on_compact_unapply)
        self.search_bar.connect_entry(self.search_entry)
        self.listener = PageListener(self, ('books',), CatalogPage.refresh_marks)
        if feed is not None:
            self._show_feed(feed)
        else:
            self.load()

    # -- actions ---------------------------------------------------------------------------

    def _add_actions(self):
        group = Gio.SimpleActionGroup()
        ref = self.weak_ref()
        for name, method in (('refresh', '_on_refresh'), ('web', '_on_web'),
                             ('edit', '_on_edit')):
            action = Gio.SimpleAction.new(name, None)
            action.connect('activate', lambda _a, _p, m=method: (
                getattr(ref(), m)() if ref() is not None else None))
            group.add_action(action)
        facet = Gio.SimpleAction.new('facet', GLib.VariantType.new('s'))
        facet.connect('activate', lambda _a, value: (
            ref().open_facet(value.get_string()) if ref() is not None else None))
        group.add_action(facet)
        self.insert_action_group('catalog', group)
        self._actions = group
        menu = Gio.Menu()
        menu.append(_('_Refresh'), 'catalog.refresh')
        menu.append(_('Open in _Browser'), 'catalog.web')
        menu.append(_('_Edit Catalogue…'), 'catalog.edit')
        self.more_button.set_menu_model(menu)
        group.lookup_action('web').set_enabled(False)

    def _on_refresh(self):
        self.load(refresh=True)

    def _on_web(self):
        if self.feed is not None and self.feed.web:
            Gtk.UriLauncher(uri=self.feed.web).launch(self.get_root(), None, None)

    def _on_edit(self):
        from ..dialogs import add_catalog as catalog_dialog

        ref = self.weak_ref()

        def saved(catalog):
            page = ref()
            if page is not None:
                page.catalog = catalog
                page.client = None
                page.load(refresh=True)

        catalog_dialog.present_edit(app(), self.get_root(), self.catalog, done=saved)

    # -- loading ---------------------------------------------------------------------------

    def load(self, refresh=False):
        """Fetch the page's feed (in a thread) and show it."""
        if self._task is not None:
            self._task.cancel()
        self.stack.set_visible_child_name('loading')
        catalog, url, search = self.catalog, self.url, self.search
        root = url == catalog.url and search is None

        def work():
            client = client_for(catalog)
            if search is not None:
                target = client.search_url(search[0], search[1])
                if target is None:
                    raise opds.OpdsError(_('This catalogue cannot be searched'))
            else:
                target = url
            feed = client.feed(target, refresh=refresh)
            if root:
                _save_icon(catalog, feed, client)
            return client, feed

        ref = self.weak_ref()

        def loaded(result, error):
            page = ref()
            if page is None:
                return
            page._task = None
            if error is not None:
                page.show_error(error)
            else:
                page.client = result[0]
                page._show_feed(result[1])

        self._task = opds.run_async(work, loaded)

    def _show_feed(self, feed):
        self.feed = feed
        if self.client is None:
            self.client = client_for(self.catalog)  # made already: no keyring wait
        if not self._fixed_title and feed.title:
            self.set_title(feed.title)
            self.window_title.set_title(feed.title)
            if feed.title != self.catalog.title:
                self.window_title.set_subtitle(self.catalog.title)
        self.search_button.set_visible(feed.search is not None and self.search is None)
        self._actions.lookup_action('web').set_enabled(bool(feed.web))
        self._fill_facets(feed)
        self._clear()
        self._append(feed)
        if not self.feed.navigation and not self.feed.books:
            self._show_message('discover-symbolic', _('Nothing Here'),
                               _('No books found') if self.search is not None
                               else _('This part of the catalogue is empty'))
        else:
            self.stack.set_visible_child_name('feed')
        self.refresh_marks()

    def _clear(self):
        while (row := self.nav_list.get_row_at_index(0)) is not None:
            self.nav_list.remove(row)
        self.book_box.remove_all()
        self._tiles = []
        self.scroller.get_vadjustment().set_value(0)

    def _append(self, feed):
        for entry in feed.navigation:
            if entry.looks_like_book:
                self._add_tile(entry)
            else:
                self.nav_list.append(_SectionRow(entry))
        for entry in feed.books:
            self._add_tile(entry)
        self.nav_clamp.set_visible(self.nav_list.get_row_at_index(0) is not None)
        self.book_box.set_visible(bool(self._tiles))
        self._next = feed.next
        self.more_box.set_visible(bool(feed.next))
        self.more_page_button.set_visible(True)
        self.more_spinner.set_visible(False)

    def _add_tile(self, entry):
        tile = CatalogTile(width=self._size.props.width)
        tile.entry = entry
        self._size.bind_property('width', tile.cover, 'width')
        tile.set_entry(entry, self.client)
        self.book_box.append(tile)
        self._tiles.append(tile)

    def _fill_facets(self, feed):
        while (child := self.facet_box.get_first_child()) is not None:
            self.facet_box.remove(child)
        for group in feed.facets:
            menu = Gio.Menu()
            for facet in group.facets:
                label = facet.title
                if facet.count is not None:
                    label = _('{facet} ({count})').format(facet=facet.title,
                                                         count=f'{facet.count:n}')
                item = Gio.MenuItem.new(label, None)
                item.set_action_and_target_value('catalog.facet',
                                                 GLib.Variant.new_string(facet.href))
                menu.append_item(item)
            active = group.active
            button = Gtk.MenuButton(menu_model=menu, always_show_arrow=True)
            button.set_label(_('{group}: {facet}').format(group=group.title, facet=active.title)
                             if active is not None else group.title)
            button.add_css_class('pill' if active is not None else 'flat')
            button.add_css_class('small-pill')
            self.facet_box.append(button)
        self.facet_scroller.set_visible(bool(feed.facets))

    def open_facet(self, href):
        self.url = href
        self.search = None  # a filter of the results is a page of its own
        self.load()

    def load_more(self):
        if not self._next or self._more_task is not None or self.client is None:
            return
        self.more_spinner.set_visible(True)
        self.more_page_button.set_visible(False)
        ref = self.weak_ref()

        def loaded(feed, error):
            page = ref()
            if page is None:
                return
            page._more_task = None
            if error is not None:
                page.more_spinner.set_visible(False)
                page.more_page_button.set_visible(True)
                app().toast(str(error))
                return
            page._append(feed)
            page.refresh_marks()

        self._more_task = opds.run_async(self.client.feed, loaded, self._next)

    def show_error(self, error):
        if isinstance(error, opds.AuthError):
            self._show_message('dialog-password-symbolic', _('Sign In Required'), str(error),
                               _('_Sign In…'), self._on_edit)
        elif isinstance(error, opds.NotOpdsError):
            self._show_message('dialog-question-symbolic', _('Not a Catalogue'),
                               _('Nothing at this address is a book catalogue (OPDS)'),
                               _('_Edit Catalogue…'), self._on_edit)
        elif isinstance(error, opds.OfflineError):
            self._show_message('network-offline-symbolic', _('No Connection'), str(error),
                               _('_Try Again'), self.load)
        else:
            self._show_message('dialog-error-symbolic', _('Could Not Open the Catalogue'),
                               str(error), _('_Try Again'), self.load)

    def _show_message(self, icon, title, description, button=None, action=None):
        self.status_page.set_icon_name(icon)
        self.status_page.set_title(title)
        self.status_page.set_description(GLib.markup_escape_text(description))
        self.status_button.set_visible(button is not None)
        if button is not None:
            self.status_button.set_label(button)
        self._status_action = action
        self.stack.set_visible_child_name('message')

    def refresh_marks(self):
        library = app().library
        manager = downloads()
        for tile in self._tiles:
            state = manager.state(tile.entry.key)
            have = (state is not None and state[0] == 'done') or (
                opds.find_in_library(library, tile.entry) is not None)
            tile.mark.set_visible(have)

    # -- events ----------------------------------------------------------------------------

    def _on_status_clicked(self, _button):
        if self._status_action is not None:
            self._status_action()

    def _on_search_activate(self, entry):
        terms = entry.get_text().strip()
        if terms and self.feed is not None:
            self.get_root().push(CatalogPage(self.catalog, search=(self.feed, terms),
                                             client=self.client))

    def _on_stop_search(self, _entry):
        self.search_bar.set_search_mode(False)

    def focus_search(self):
        if self.search_button.get_visible():
            self.search_bar.set_search_mode(True)
            self.search_entry.grab_focus()

    def _on_row_activated(self, _list, row):
        open_entry(self, self.catalog, self.client, row.entry, row)

    def _on_tile_activated(self, _box, child):
        tile = child.get_child()
        open_entry(self, self.catalog, self.client, tile.entry)

    def _on_edge_reached(self, _scroller, position):
        if position == Gtk.PositionType.BOTTOM:
            self.load_more()

    def _on_more_clicked(self, _button):
        self.load_more()

    def _on_compact_apply(self, *_args):
        self._size.props.width = COMPACT_COVER

    def _on_compact_unapply(self, *_args):
        self._size.props.width = COVER_WIDTH

