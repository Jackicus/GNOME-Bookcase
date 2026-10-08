# SPDX-License-Identifier: GPL-2.0-or-later
# SPDX-FileCopyrightText: 2026 Jack Tully

"""The library window's sidebar: Home and All Books; Browse (Authors, Series, Tags); Reading
(Currently Reading, Unread, Finished); the user's shelves; connected devices.

    controller = SidebarController(window, adw_sidebar, app)
    controller.select('shelf:3'); controller.has_key(key); controller.key_at(index)
    controller.refresh_now()
    build_sections(library, devices)     # the model: [Section(key, title, [Entry])], no GTK

Each item's key is a root key of window.show_root(). A count of books is each item's suffix
where it is cheap (All Books, the reading states, the shelves). The Shelves section appears
once there is a shelf (New Shelf… is the button under the sidebar), the Devices section while
a device is connected. Books dragged from a grid or list (widgets/book_item.py's BookIds)
drop on a manual shelf (added to it) or a reading state (marked so). The Shelves section's
context menu (Edit…, Remove) runs the window's win.shelf-* actions on the shelf it was opened
on (the sidebar's setup-menu signal says which). The counts and the shelves refresh a moment
after the library reports a change; the devices when the device monitor reports one.
"""

import dataclasses
import logging
from gettext import gettext as _

from gi.repository import Adw, Gdk, Gio, GLib, Gtk

from .widgets.book_item import BookIds

log = logging.getLogger(__name__)

REFRESH_DELAY_MS = 250
CHANGE_KINDS = ('books', 'shelves', 'progress', 'files', 'folders')

ICONS = {
    'home': 'user-home-symbolic',
    'all': 'library-symbolic',
    'authors': 'avatar-default-symbolic',
    'series': 'view-list-ordered-symbolic',
    'tags': 'tag-symbolic',
    'status:reading': 'book-open-symbolic',
    'status:unread': 'book-closed-symbolic',
    'status:finished': 'object-select-symbolic',
    'shelf': 'folder-symbolic',
    'smart-shelf': 'folder-saved-search-symbolic',
    'device': 'drive-removable-media-symbolic',
    'device:kobo': 'tablet-symbolic',
    'device:kindle': 'tablet-symbolic',
}


@dataclasses.dataclass
class Entry:
    key: str
    title: str
    icon: str
    count: int | None = None
    tooltip: str | None = None


@dataclasses.dataclass
class Section:
    key: str
    title: str | None
    entries: list


def build_sections(library, devices=()):
    """The sidebar's sections and items, from the library and the devices connected."""
    sections = [
        Section('main', None, [
            Entry('home', _('Home'), ICONS['home']),
            Entry('all', _('All Books'), ICONS['all'], library.count()),
        ]),
        Section('browse', _('Browse'), [
            Entry('authors', _('Authors'), ICONS['authors']),
            Entry('series', _('Series'), ICONS['series']),
            Entry('tags', _('Tags'), ICONS['tags']),
        ]),
        Section('reading', _('Reading'), [
            Entry('status:reading', _('Currently Reading'), ICONS['status:reading'],
                  library.count(status='reading')),
            Entry('status:unread', _('Unread'), ICONS['status:unread'],
                  library.count(status='unread')),
            Entry('status:finished', _('Finished'), ICONS['status:finished'],
                  library.count(status='finished')),
        ]),
    ]
    shelves = []
    for shelf in library.shelves():
        smart = shelf.query is not None
        shelves.append(Entry(f'shelf:{shelf.id}', shelf.name,
                             ICONS['smart-shelf' if smart else 'shelf'], shelf.count,
                             tooltip=(_('Smart shelf: {query}').format(query=shelf.query)
                                      if smart else None)))
    if shelves:
        sections.append(Section('shelves', _('Shelves'), shelves))
    connected = [Entry(f'device:{device.id}', device.name,
                       ICONS.get(f'device:{getattr(device, "kind", "")}', ICONS['device']))
                 for device in devices]
    if connected:
        sections.append(Section('devices', _('Devices'), connected))
    return sections


def _count_label():
    label = Gtk.Label(valign=Gtk.Align.CENTER)
    label.add_css_class('sidebar-count')
    label.add_css_class('numeric')
    label.add_css_class('dimmed')
    return label


class SidebarController:

    def __init__(self, window, sidebar, app):
        self.window = window
        self.sidebar = sidebar
        self.app = app
        self.library = app.library
        self._keys = []  # the key of each item, in the sidebar's order
        self._items = {}  # key -> Adw.SidebarItem
        self._sections = {}  # section key -> Adw.SidebarSection
        self._shape = None  # what the items were built from, to rebuild only on a change
        self._refresh_pending = None
        self._selecting = False
        self._shelf_menu = _shelf_menu()
        self._build()
        self.sidebar.connect('activated', self._on_activated)
        self.sidebar.connect('notify::selected', self._on_selected)
        self.sidebar.connect('setup-menu', self._on_setup_menu)
        self.sidebar.setup_drop_target(Gdk.DragAction.COPY, [BookIds.__gtype__])
        self.sidebar.connect('drop-enter', self._on_drop_enter)
        self.sidebar.connect('drop', self._on_drop)
        self.library.connect('changed', self._on_changed)
        devices = getattr(app, 'devices', None)
        if devices is not None:
            for signal in ('added', 'removed'):
                try:
                    devices.connect(signal, self._on_devices_changed)
                except TypeError:
                    log.warning('the device monitor has no %r signal', signal)

    # -- building ----------------------------------------------------------------------------

    def _devices(self):
        devices = getattr(self.app, 'devices', None)
        if devices is None:
            return []
        try:
            return list(devices.devices())
        except Exception:
            log.exception('listing devices')
            return []

    def _build(self):
        sections = build_sections(self.library, self._devices())
        shape = [(section.key, [(entry.key, entry.title, entry.icon, entry.tooltip)
                                for entry in section.entries]) for section in sections]
        if shape == self._shape:
            self._update_counts(sections)
            return
        self._shape = shape
        selected = self.key_at(self.sidebar.get_selected())
        self._selecting = True
        try:
            for section in list(self._sections.values()):
                self.sidebar.remove(section)
            self._sections.clear()
            self._items.clear()
            self._keys.clear()
            for model in sections:
                section = Adw.SidebarSection(title=model.title)
                if model.key == 'shelves':
                    section.set_menu_model(self._shelf_menu)
                for entry in model.entries:
                    item = Adw.SidebarItem(title=entry.title, icon_name=entry.icon)
                    if entry.tooltip:
                        item.set_tooltip(entry.tooltip)
                    if entry.count is not None:
                        item.set_suffix(_count_label())
                    section.append(item)
                    self._items[entry.key] = item
                    self._keys.append(entry.key)
                self.sidebar.append(section)
                self._sections[model.key] = section
            self._update_counts(sections)
            if selected in self._items:
                self.sidebar.set_selected(self.index_of(selected))
            else:
                self.sidebar.set_selected(Gtk.INVALID_LIST_POSITION)
        finally:
            self._selecting = False

    def _update_counts(self, sections):
        for section in sections:
            for entry in section.entries:
                item = self._items.get(entry.key)
                label = item.get_suffix() if item is not None else None
                if label is not None and entry.count is not None:
                    label.set_text(f'{entry.count:n}')
                    label.set_visible(entry.count > 0)

    # -- selection ---------------------------------------------------------------------------

    def has_key(self, key):
        return key in self._items

    def key_at(self, index):
        return self._keys[index] if 0 <= index < len(self._keys) else None

    def key_of(self, item):
        return next((key for key, found in self._items.items() if found is item), None)

    def index_of(self, key):
        return self._keys.index(key) if key in self._keys else -1

    def select(self, key):
        index = self.index_of(key)
        if self.sidebar.get_selected() == index:
            return
        self._selecting = True
        try:
            self.sidebar.set_selected(index if index >= 0 else Gtk.INVALID_LIST_POSITION)
        finally:
            self._selecting = False

    def _on_selected(self, *_args):
        if self._selecting:
            return
        key = self.key_at(self.sidebar.get_selected())
        if key is not None:
            self.window.show_root(key)

    def _on_activated(self, _sidebar, index):
        key = self.key_at(index)
        if key is not None:
            self.window.show_root(key)

    def _on_setup_menu(self, _sidebar, item):
        key = self.key_of(item) if item is not None else None
        self.window.set_menu_shelf(int(key[6:]) if key and key.startswith('shelf:') else None)

    # -- dropping books --------------------------------------------------------------------

    def _drop_kind(self, key):
        """What a drop of books on an item does: 'shelf' (a manual shelf), 'status', or
        None."""
        if key is None:
            return None
        if key.startswith('status:'):
            return 'status'
        if key.startswith('shelf:'):
            shelf = self.library.shelf(int(key[6:]))
            return 'shelf' if shelf is not None and shelf.query is None else None
        return None

    def _on_drop_enter(self, _sidebar, index, *_args):
        if self._drop_kind(self.key_at(index)) is None:
            return 0
        return Gdk.DragAction.COPY

    def _on_drop(self, _sidebar, index, value, _action):
        key = self.key_at(index)
        kind = self._drop_kind(key)
        ids = list(getattr(value, 'ids', ()))
        if kind is None or not ids:
            return False
        from .pages.actions import add_to_shelf, set_status

        if kind == 'shelf':
            add_to_shelf(int(key[6:]), ids)
        else:
            set_status(ids, key[7:])
        return True

    # -- refreshing --------------------------------------------------------------------------

    def _on_changed(self, _library, kind):
        if kind in CHANGE_KINDS and self._refresh_pending is None:
            self._refresh_pending = GLib.timeout_add(REFRESH_DELAY_MS, self._refresh)

    def _on_devices_changed(self, *_args):
        self.refresh_now()

    def _refresh(self):
        self._refresh_pending = None
        try:
            self._build()
        except Exception:
            log.exception('refreshing the sidebar')
        return GLib.SOURCE_REMOVE

    def refresh_now(self):
        if self._refresh_pending is not None:
            GLib.source_remove(self._refresh_pending)
        self._refresh()


def _shelf_menu():
    """The Shelves section's context menu (the window's win.shelf-* actions)."""
    menu = Gio.Menu()
    menu.append(_('_Edit…'), 'win.shelf-edit')
    menu.append(_('_Remove'), 'win.shelf-remove')
    return menu
