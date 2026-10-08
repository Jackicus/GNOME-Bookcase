# SPDX-License-Identifier: GPL-2.0-or-later
# SPDX-FileCopyrightText: 2026 Jack Tully

"""The pages: one Adw.NavigationPage per destination of the sidebar, made by make_root() on
the first visit, and the pushed ones (a book's details, an author's or a series' books).

    page = make_root('shelf:3')        # None for an unknown key
    app()                              # the Application, for a page that needs the library
    PageListener(page, kinds, Page.refresh)  # a debounced refresh while mapped

Root keys: home, all, authors, series, tags, status:reading, status:unread, status:finished,
shelf:ID, device:ID. A root page is an Adw.NavigationPage with its own Adw.ToolbarView and
Adw.HeaderBar (window.py replaces the navigation stack with it). Its module is imported by
its factory, never at the top of window.py (startup time). A page listens to `app().library`'s
`changed` signal while it is mapped and refreshes itself from the library: it holds no state
of its own that the library does not.
"""

from gi.repository import Gio, GLib

REFRESH_DELAY_MS = 150
STATUSES = ('reading', 'unread', 'finished')


def app():
    return Gio.Application.get_default()


def make_root(key):
    if key == 'home':
        from .home import HomePage

        return HomePage()
    if key == 'all':
        from .books import BooksPage

        return BooksPage(key=key)
    if key in ('authors', 'series', 'tags'):
        from .groups import GroupsPage

        return GroupsPage(key)
    if key.startswith('status:') and key[7:] in STATUSES:
        from .books import BooksPage

        return BooksPage(key=key, status=key[7:])
    if key.startswith('shelf:') and key[6:].isdigit():
        from .books import BooksPage

        return BooksPage(key=key, shelf=int(key[6:]))
    if key.startswith('device:'):
        from .device import DevicePage

        return DevicePage(key[7:])
    return None


class PageListener:
    """A page's refresh from the library: `callback()` runs REFRESH_DELAY_MS after the
    library's `changed` reports one of `kinds` (once, however many came), only while the page
    is mapped, and once on each map. The library's signal is connected on map and
    disconnected on unmap, and the page is held weakly, so a dropped page goes."""

    def __init__(self, page, kinds, callback, delay=REFRESH_DELAY_MS):
        self.kinds = set(kinds)
        self.delay = delay
        self._page = page.weak_ref()
        self._function = getattr(callback, '__func__', callback)  # a method or a function
        self._handler = None
        self._library = None  # the library connected to (the app's, when mapped)
        self._pending = None
        page.connect('map', lambda *_args: self._on_map())
        page.connect('unmap', lambda *_args: self._on_unmap())

    def _call(self):
        page = self._page()
        if page is not None:
            self._function(page)

    def _on_map(self):
        if self._handler is None:
            self._library = app().library
            self._handler = self._library.connect('changed', self._on_changed)
        self._call()

    def _on_unmap(self):
        if self._handler is not None:
            self._library.disconnect(self._handler)
            self._handler = None
            self._library = None
        self.cancel()

    def cancel(self):
        if self._pending is not None:
            GLib.source_remove(self._pending)
            self._pending = None

    def _on_changed(self, _library, kind):
        if kind in self.kinds and self._pending is None:
            self._pending = GLib.timeout_add(self.delay, self._fire)

    def _fire(self):
        self._pending = None
        if self._page() is None:
            self._on_unmap()
        else:
            self._call()
        return GLib.SOURCE_REMOVE
