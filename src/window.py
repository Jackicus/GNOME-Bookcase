# SPDX-License-Identifier: GPL-2.0-or-later
# SPDX-FileCopyrightText: 2026 Jack Tully

"""The library window: a sidebar of places, shelves and devices, and the page it opens.

    window = Window(application=app)
    window.show_root('shelf:3')          # a sidebar key (pages.make_root's)
    window.show_book(book_id)            # pushes the book's details
    window.show_books(title, **filters)  # pushes the books of an author, series, tag, search
    window.search(query)                 # All Books, searching for the query
    window.push(page); window.pop()
    window.add_toast(toast); window.set_dialog_open(True); window.undone(label)

The sidebar is sidebar.py's SidebarController over the window's Adw.Sidebar; choosing an
item replaces the navigation view's stack with that place's root page, which is made on the
first visit and kept. The shelf actions (win.shelf-*) act on the shelf the sidebar's context
menu was opened on, else the shelf shown. Files dropped on the window are added
(app.add_files). The size, maximized state and last page are kept in the settings. Keyed
window actions are disabled while a dialog is open over the window, so a dialog's entry gets
its keys. A sidebar toggle (F9) hides the sidebar in a wide window (collapsing the split
view) and shows it again; the sidebar-hidden setting remembers it. Find Duplicates
(win.find-duplicates, the main menu) pushes pages/duplicates.py; Ctrl+K (win.quick-open)
opens the Go To search (dialogs/quick_open.py).
"""

import logging
from gettext import gettext as _
from gettext import ngettext

from gi.repository import Adw, Gdk, Gio, GObject, Gtk

from . import pages
from .sidebar import SidebarController

log = logging.getLogger(__name__)


@Gtk.Template(resource_path='/io/github/jackicus/Bookcase/window.ui')
class Window(Adw.ApplicationWindow):
    __gtype_name__ = 'BookcaseWindow'

    toast_overlay = Gtk.Template.Child()
    split_view = Gtk.Template.Child()
    sidebar = Gtk.Template.Child()
    navigation_view = Gtk.Template.Child()
    content_page = Gtk.Template.Child()
    primary_menu_button = Gtk.Template.Child()

    def __init__(self, **kwargs):
        super().__init__(**kwargs)
        app = self.get_application()
        self.app = app
        self.settings = app.settings
        self._roots = {}  # sidebar key -> root page
        self._current_key = None
        self._menu_shelf_id = None  # the shelf the sidebar's menu was opened on
        self._sidebar_hidden = False  # hidden by the toggle (the split view collapsed)
        if app.profile == 'development':
            self.add_css_class('devel')
        self._restore_size()
        self._add_actions()
        self._add_drop_target()
        self.sidebar_controller = SidebarController(self, self.sidebar, app)
        self.navigation_view.connect('notify::visible-page', self._on_visible_page)
        # The app outlives a closed library window (a reader may stay open, and a new window
        # is made on the next activation): what the window connected to goes with it.
        self._handlers = [(app.library, app.library.connect('changed',
                                                             self._on_library_changed))]
        if app.devices is not None:
            try:
                self._handlers.append((app.devices, app.devices.connect(
                    'removed', self._on_device_removed)))
            except TypeError:
                pass
        self.connect('close-request', self._on_close_request)
        self.connect('unrealize', self._on_unrealize)
        last = self.settings.get_string('last-page')
        self.show_root(last if self.sidebar_controller.has_key(last) else 'home')
        if self.settings.get_boolean('sidebar-hidden'):
            self._hide_sidebar()

    # -- size and closing ------------------------------------------------------------------

    def _restore_size(self):
        self.set_default_size(self.settings.get_int('window-width'),
                              self.settings.get_int('window-height'))
        if self.settings.get_boolean('window-maximized'):
            self.maximize()

    def _on_close_request(self, *_args):
        if not self.is_maximized():
            width, height = self.get_default_size()
            self.settings.set_int('window-width', width)
            self.settings.set_int('window-height', height)
        self.settings.set_boolean('window-maximized', self.is_maximized())
        if self._current_key and not self._current_key.startswith('device:'):
            self.settings.set_string('last-page', self._current_key)
        return False

    def _on_unrealize(self, *_args):
        for emitter, handler in self._handlers:
            if emitter.handler_is_connected(handler):
                emitter.disconnect(handler)
        self._handlers = []
        self.sidebar_controller.disconnect()

    # -- actions -----------------------------------------------------------------------------

    def _add_actions(self):
        self._keyed = []
        for name, callback, keyed in (
                ('search', self.on_search, True),
                ('home', lambda *_a: self.show_root('home'), True),
                ('all', lambda *_a: self.show_root('all'), True),
                ('authors', lambda *_a: self.show_root('authors'), True),
                ('series', lambda *_a: self.show_root('series'), True),
                ('stats', lambda *_a: self.show_root('stats'), True),
                ('back', self.on_back, True),
                ('toggle-sidebar', self.on_toggle_sidebar, True),
                ('view-grid', lambda *_a: self.settings.set_string('view-mode', 'grid'), True),
                ('view-list', lambda *_a: self.settings.set_string('view-mode', 'list'), True),
                ('close', lambda *_a: self.close(), False),
                ('find-duplicates', self.on_find_duplicates, False),
                ('quick-open', self.on_quick_open, True),
                ('shelf-new', self.on_shelf_new, False),
                ('shelf-edit', self.on_shelf_edit, False),
                ('shelf-remove', self.on_shelf_remove, False)):
            action = Gio.SimpleAction.new(name, None)
            action.connect('activate', callback)
            self.add_action(action)
            if keyed:
                self._keyed.append(action)

    def set_dialog_open(self, is_open):
        """Keyed window actions step aside while a dialog is open over the window."""
        for action in self._keyed:
            action.set_enabled(not is_open)

    def on_search(self, *_args):
        page = self.navigation_view.get_visible_page()
        if not hasattr(page, 'focus_search'):
            self.show_root('all')
            page = self._roots.get('all')
        if page is not None and hasattr(page, 'focus_search'):
            page.focus_search()

    def search(self, query):
        """All Books, searching for `query`."""
        self.show_root('all')
        page = self._roots.get('all')
        if page is not None:
            page.search(query)
        return page

    def on_back(self, *_args):
        if self.navigation_view.get_visible_page() is not self._roots.get(self._current_key):
            self.navigation_view.pop()
        elif self.split_view.get_collapsed() and self.split_view.get_show_content():
            self.split_view.set_show_content(False)

    def on_toggle_sidebar(self, *_args):
        if self.split_view.get_collapsed() and not self._sidebar_hidden:
            self.split_view.set_show_content(not self.split_view.get_show_content())
        elif self._sidebar_hidden:
            self._sidebar_hidden = False
            self.split_view.set_collapsed(False)
            self.settings.set_boolean('sidebar-hidden', False)
        else:
            self._hide_sidebar()
            self.settings.set_boolean('sidebar-hidden', True)

    def _hide_sidebar(self):
        self._sidebar_hidden = True
        self.split_view.set_collapsed(True)
        self.split_view.set_show_content(True)

    def on_find_duplicates(self, *_args):
        """Find Duplicates: pushes pages/duplicates.py."""
        from .pages.duplicates import DuplicatesPage

        page = DuplicatesPage()
        self.push(page)
        return page

    def on_quick_open(self, *_args):
        """Ctrl+K: go to a book, an author, a series or a shelf (dialogs/quick_open.py)."""
        from .dialogs import quick_open

        return quick_open.present(self.app, self)

    # -- shelves -----------------------------------------------------------------------------

    def set_menu_shelf(self, shelf_id):
        """The shelf the sidebar's context menu acts on (None once it closes)."""
        self._menu_shelf_id = shelf_id

    def _target_shelf(self):
        if self._menu_shelf_id is not None:
            return self._menu_shelf_id
        if self._current_key and self._current_key.startswith('shelf:'):
            return int(self._current_key[6:])
        return None

    def on_shelf_new(self, *_args):
        from .dialogs import shelf

        shelf.present_new(self.app, self)

    def on_shelf_edit(self, *_args):
        from .dialogs import shelf

        shelf_id = self._target_shelf()
        if shelf_id is not None:
            shelf.present_edit(self.app, self, shelf_id)

    def on_shelf_remove(self, *_args):
        shelf_id = self._target_shelf()
        if shelf_id is not None:
            self.remove_shelf(shelf_id)

    def remove_shelf(self, shelf_id):
        """Remove a shelf: at once (with Undo) when it is smart or empty, else after asking."""
        library = self.app.library
        shelf = library.shelf(shelf_id)
        if shelf is None:
            return None

        def remove():
            library.remove_shelf(shelf_id)
            self.app.toast(_('Removed the shelf “{name}”').format(name=shelf.name), undo=True)

        if shelf.query is not None or shelf.count == 0:
            remove()
            return None
        dialog = Adw.AlertDialog(
            heading=_('Remove “{name}”?').format(name=shelf.name),
            body=ngettext('Its {n} book stays in the library.',
                          'Its {n} books stay in the library.', shelf.count).format(
                              n=shelf.count))
        dialog.add_response('cancel', _('_Cancel'))
        dialog.add_response('remove', _('_Remove Shelf'))
        dialog.set_response_appearance('remove', Adw.ResponseAppearance.DESTRUCTIVE)
        dialog.set_default_response('cancel')
        dialog.set_close_response('cancel')
        dialog.connect('response', lambda _d, response: remove() if response == 'remove'
                       else None)
        self.set_dialog_open(True)
        dialog.connect('closed', lambda *_args: self.set_dialog_open(False))
        dialog.present(self)
        return dialog

    # -- pages -------------------------------------------------------------------------------

    def show_root(self, key):
        """Show a place's root page (made on the first visit) and select it in the sidebar."""
        if not self.sidebar_controller.has_key(key):
            key = 'home'
        page = self._roots.get(key)
        if page is None:
            page = pages.make_root(key)
            if page is None:
                log.warning('no page for %s', key)
                return None
            self._roots[key] = page
        self._current_key = key
        self.navigation_view.replace([page])
        self.sidebar_controller.select(key)
        if self.split_view.get_collapsed():
            self.split_view.set_show_content(True)
        return page

    @property
    def current_key(self):
        return self._current_key

    def push(self, page):
        self.navigation_view.push(page)
        if self.split_view.get_collapsed():
            self.split_view.set_show_content(True)

    def pop(self):
        self.navigation_view.pop()

    def show_book(self, book_id):
        """Push a book's details page."""
        from .pages.book import BookPage

        page = BookPage(book_id)
        self.push(page)
        return page

    def show_books(self, title, **filters):
        """Push the books of an author (author=ID), a series (series=ID), a tag (tag=ID) or a
        search (query=TEXT)."""
        from .pages.books import BooksPage

        page = BooksPage(title=title, **filters)
        self.push(page)
        return page

    def forget_root(self, key):
        """A shelf or device that is gone takes its page; Home takes its place if shown."""
        if key in self._roots:
            del self._roots[key]
        if self._current_key == key:
            self.show_root('home')

    def _on_visible_page(self, *_args):
        page = self.navigation_view.get_visible_page()
        if page is not None:
            self.content_page.set_title(page.get_title() or _('Bookcase'))

    def _on_library_changed(self, _library, kind):
        if kind == 'shelves':
            existing = {shelf.id for shelf in self.app.library.shelves()}
            for key in list(self._roots):
                if key.startswith('shelf:') and int(key[6:]) not in existing:
                    self.forget_root(key)

    def _on_device_removed(self, _monitor, device_id, *_args):
        self.forget_root(f'device:{getattr(device_id, "id", device_id)}')

    def undone(self, label):
        """An undo happened: the page shown, when it cares, hears of it."""
        page = self.navigation_view.get_visible_page()
        if hasattr(page, 'undone'):
            page.undone(label)

    # -- dropping files ----------------------------------------------------------------------

    def _add_drop_target(self):
        target = Gtk.DropTarget.new(Gdk.FileList, Gdk.DragAction.COPY)
        target.connect('drop', self._on_drop)
        self.add_controller(target)

    def _on_drop(self, _target, value, _x, _y):
        files = value.get_files() if hasattr(value, 'get_files') else list(value)
        if not files:
            return False
        self.app.add_files(files)
        return True

    # -- messages ----------------------------------------------------------------------------

    def add_toast(self, toast):
        self.toast_overlay.add_toast(toast)

    def announce(self, text):
        """Say something to a screen reader."""
        self.toast_overlay.announce(text, Gtk.AccessibleAnnouncementPriority.MEDIUM)


GObject.type_ensure(Window)
