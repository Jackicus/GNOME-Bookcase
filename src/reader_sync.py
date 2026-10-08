# SPDX-License-Identifier: GPL-2.0-or-later
# SPDX-FileCopyrightText: 2026 Jack Tully

"""The reader window's side of reading-position sync (kosync.py, app.sync).

    sync = ReaderSync(window)           # after the window has opened its book
    sync.relocated(place)               # each relocation the window gets
    sync.close()                        # on closing: what waits is pushed now

When the book has opened (its first relocation), the position stored on the sync server is
asked for in the background (kosync.PULL_TIMEOUT at most; opening never waits). When
another device is somewhere else and more recently (kosync.should_offer), a banner under the
header bar says so ("Kobo Libra is at 62%") with Go There: the exact place when that device
stored a CFI (another Bookcase, a foliate-js reader) or a PDF page, else the percentage.
Turning a few pages hides it. The position is pushed after kosync.PUSH_DELAY_S without a
page turn, when the window loses the focus, and on closing; never before the reader has
moved, so opening a book never overwrites a newer place from another device. The main menu
gets a section with the sync status ("Synced 2 min ago") and Sync Now.
"""

import logging
import weakref
from gettext import gettext as _

from gi.repository import Adw, Gio, GLib

from . import kosync, reading

log = logging.getLogger(__name__)

HIDE_AFTER_TURNS = 3  # page turns after which the banner goes


class ReaderSync:
    def __init__(self, window):
        self._window = window.weak_ref()
        self.sync = getattr(window.app, 'sync', None)
        self.book_id = window.book_id
        self.book = window.book
        self.remote = None
        self._pulled = False
        self._baseline = None  # the place the book opened at: not pushed
        self._turns = 0
        self._handlers = []
        self._section = None
        self._closed = False

        self.banner = Adw.Banner(button_label=_('_Go There'), revealed=False,
                                 button_style=Adw.BannerButtonStyle.SUGGESTED)
        _connect(self, self.banner, 'button-clicked', self._on_go_there)
        window.toolbar_view.add_top_bar(self.banner)

        action = Gio.SimpleAction.new('sync-now', None)
        _connect(self, action, 'activate', self._on_sync_now)
        window.add_action(action)
        self._action = action
        menu_button = getattr(window, 'menu_button', None)
        self._menu = menu_button.get_menu_model() if menu_button is not None else None
        if menu_button is not None:
            _connect(self, menu_button, 'notify::active', self._on_menu_shown)
        _connect(self, window, 'notify::is-active', self._on_active_changed)
        if self.sync is not None:
            self._handlers.append(_connect(self, self.sync, 'status-changed',
                                               self._on_status_changed))
        self._update_menu()

    def window(self):
        return self._window()

    @property
    def file(self):
        window = self.window()
        return window.file if window is not None else None

    @property
    def fmt(self):
        file = self.file
        return (file.format or '').lower() if file is not None else ''

    def active(self):
        """Whether this book syncs: signed in, its file open in the window."""
        window = self.window()
        return (self.sync is not None and self.sync.signed_in() and self.file is not None
                and window is not None and window.content_stack.get_visible_child_name()
                == 'book')

    # -- pulling ----------------------------------------------------------------------------

    def _on_pulled(self, remote, error):
        if self._closed:
            return
        if error is not None:
            log.info('sync: %s', error)
            return
        self.remote = remote
        book = self.book
        place = self._baseline or {}
        fraction = place.get('fraction', book.progress if book else 0.0)
        location = place.get('cfi', book.location if book else '')
        last_read = book.last_read if book else 0.0
        if kosync.should_offer(remote, fraction, location, last_read, self.sync.device_id()):
            self.offer(remote)

    def offer(self, remote):
        """Show the banner for `remote`."""
        self.remote = remote
        percent = reading.progress_text('percent', {'fraction': remote.percentage})
        if remote.device:
            # Translators: a sync banner: another device's name and its place in the book.
            title = _('{device} is at {percent}').format(device=remote.device,
                                                         percent=percent)
        else:
            title = _('Another device is at {percent}').format(percent=percent)
        self.banner.set_title(GLib.markup_escape_text(title))
        self._turns = 0
        self.banner.set_revealed(True)

    def _on_go_there(self, _banner):
        self.banner.set_revealed(False)
        window = self.window()
        if window is None or self.remote is None:
            return
        kind, value = kosync.jump_target(self.remote, self.fmt)
        view = window.view
        if kind == 'cfi':
            view.go_to(value)
        elif kind == 'page':
            from . import pdf_location

            view.go_to(pdf_location.location(value))
        else:
            view.go_to_fraction(value)

    # -- pushing ----------------------------------------------------------------------------

    def relocated(self, place):
        if self._closed or place.get('fraction') is None:
            return
        location = place.get('cfi') or ''
        if self._baseline is None:
            self._baseline = {'fraction': place['fraction'], 'cfi': location}
            self._update_menu()
            if self.active() and not self._pulled:
                self._pulled = True
                self.sync.pull(self.book_id, self.file.path, self.fmt,
                               _weak_method(self, ReaderSync._on_pulled))
            return
        if self.banner.get_revealed() and place.get('reason') in ('page', 'scroll', 'snap'):
            self._turns += 1
            if self._turns >= HIDE_AFTER_TURNS:
                self.banner.set_revealed(False)
        if (location and location == self._baseline['cfi']) or (
                not location and place['fraction'] == self._baseline['fraction']):
            return
        if self.active():
            self.sync.position(self.book_id, self.file.path, self.fmt, place['fraction'],
                               location)

    def _on_active_changed(self, window, _pspec):
        if not window.is_active() and self.sync is not None:
            self.sync.flush(self.book_id)

    def _on_sync_now(self, *_args):
        if not self.active():
            return
        self.sync.flush(self.book_id)
        self.sync.pull(self.book_id, self.file.path, self.fmt,
                       _weak_method(self, ReaderSync._on_pulled_now), timeout=kosync.TIMEOUT)

    def _on_pulled_now(self, remote, error):
        window = self.window()
        if window is None or self._closed:
            return
        if error is not None:
            window.toast(str(error))
            return
        place = getattr(window, '_place', None) or {}
        if kosync.should_offer(remote, place.get('fraction', 0.0), place.get('cfi', ''),
                               0, self.sync.device_id()):
            self.offer(remote)
        else:
            window.toast(_('Up to date'))

    def close(self):
        if self._closed:
            return
        self._closed = True
        if self.sync is not None:
            self.sync.flush(self.book_id)
            for handler in self._handlers:
                if self.sync.handler_is_connected(handler):
                    self.sync.disconnect(handler)
        self._handlers = []

    # -- the menu ---------------------------------------------------------------------------

    def _on_status_changed(self, _sync, book_id):
        if book_id in (0, self.book_id):
            self._update_menu()

    def _on_menu_shown(self, button, _pspec):
        if button.get_active():
            self._update_menu()

    def status_text(self):
        if not self.active():
            return ''
        return kosync.status_text(*self.sync.status(self.book_id))

    def _update_menu(self):
        """The menu's sync section: its label the status, its item Sync Now; none when the
        book does not sync."""
        menu = self._menu
        if menu is None:
            return
        index = self._section_index()
        if index is not None:
            menu.remove(index)
        self._section = None
        self._action.set_enabled(self.active())
        if not self.active():
            return
        section = Gio.Menu()
        section.append(_('Sync Now'), 'win.sync-now')
        position = max(0, menu.get_n_items() - 2)  # before Keyboard Shortcuts and Close
        menu.insert_section(position if index is None else index, self.status_text(),
                            section)
        self._section = section

    def _section_index(self):
        menu = self._menu
        if self._section is None:
            return None
        for index in range(menu.get_n_items()):
            if menu.get_item_link(index, Gio.MENU_LINK_SECTION) == self._section:
                return index
        return None


def _weak_method(owner, function):
    """A callback calling function(owner, …) while the window that owns `owner` lives."""
    window_ref = owner._window

    def call(*args):
        if window_ref() is None:
            return None
        return function(owner, *args)

    return call


def _connect(owner, obj, signal, method):
    """obj.connect(signal, method) holding `owner` (a ReaderSync, which the window holds)
    weakly, so a signal of the window or the app never keeps it alive."""
    ref = weakref.ref(owner)
    function = method.__func__

    def call(*args):
        instance = ref()
        return function(instance, *args) if instance is not None else None

    return obj.connect(signal, call)
