# SPDX-License-Identifier: GPL-2.0-or-later
# SPDX-FileCopyrightText: 2026 Jack Tully

"""The home page: what to read next, at a glance.

    page = HomePage()
    page.refresh()                     # from the library (also on map and on `changed`)
    page.rows                          # [(key, ShelfRow)], for tests

Over them, the reading goal card (widgets/goal_card.py: a click opens Statistics), then
rows of covers, each scrolling sideways: Continue Reading (the books being read, most
recently read first, large, each with its progress and the time left from stats.py), Recently
Added, then each shelf with books (smart shelves first). A row's Show All opens its page (the
reading state, All Books, the shelf). A cover in Continue Reading opens the reader; elsewhere
it opens the book's details; a right click (or a long press) opens the book menu
(pages/actions.py).

An empty library shows the welcome. It looks for the books already on the computer
(existing_books.find(), in a thread, once per page) and offers each in a row: a Calibre library to
link, a folder of books to read in place (watched) or copy in, the e-books in Downloads or
Documents to copy in; then Add Books…, Add a Folder…, Link a Calibre Library…. When books
arrive after the welcome was shown, a banner suggests smart shelves and e-readers, once
ever (the welcome-tip-shown setting).

    page.sources                       # [existing_books.Source] found; None until the look ends
"""

import logging
import os
import threading
from gettext import gettext as _
from gettext import ngettext

from gi.repository import Adw, Gdk, Gio, GLib, Gtk, Pango

from .. import existing_books, stats
from ..widgets.book_tile import BookTile, progress_text
from ..widgets.goal_card import GoalCard
from ..widgets.util import connect_weak
from . import PageListener, app
from .actions import BookActions, book_menu, popup_menu

log = logging.getLogger(__name__)

CHANGE_KINDS = ('books', 'files', 'shelves', 'progress')
ROW_LIMIT = 12
SHELF_ROWS = 6
LARGE, SMALL = 168, 120
LARGE_COMPACT, SMALL_COMPACT = 128, 96


def minutes_left(library, book):
    """The reading time left in a book, in minutes, or None when stats.py cannot tell."""
    try:
        left = stats.time_left(library, book.id, book.progress)
    except Exception:
        log.exception('time left in book %s', book.id)
        return None
    return left.book / 60 if left is not None and left.book else None


class ShelfRow(Gtk.Box):
    """A titled row of covers that scrolls sideways, with Show All when there are more."""

    __gtype_name__ = 'BookcaseShelfRow'

    def __init__(self, title, show_all_key=None, more=False):
        super().__init__(orientation=Gtk.Orientation.VERTICAL, spacing=6)
        self.add_css_class('shelf-row')
        header = Gtk.Box(spacing=12, margin_start=24, margin_end=18)
        self.title = Gtk.Label(label=title, xalign=0, hexpand=True,
                               ellipsize=Pango.EllipsizeMode.END,
                               accessible_role=Gtk.AccessibleRole.HEADING)
        self.title.add_css_class('title-3')
        header.append(self.title)
        self.show_all = Gtk.Button(label=_('Show All'), valign=Gtk.Align.CENTER,
                                   visible=show_all_key is not None and more)
        self.show_all.add_css_class('flat')
        self.show_all_key = show_all_key
        header.append(self.show_all)
        self.append(header)
        self.box = Gtk.Box(spacing=18, margin_start=24, margin_end=24, margin_top=6,
                           margin_bottom=6)
        self.scroller = Gtk.ScrolledWindow(vscrollbar_policy=Gtk.PolicyType.NEVER,
                                           hscrollbar_policy=Gtk.PolicyType.AUTOMATIC,
                                           propagate_natural_height=True, child=self.box)
        self.append(self.scroller)
        self.tiles = []

    def add_tile(self, book, width, subtitle=None):
        tile = BookTile(width=width)
        tile.set_book(book, subtitle=subtitle)
        button = Gtk.Button(child=tile, valign=Gtk.Align.END)
        button.add_css_class('flat')
        button.add_css_class('book-button')
        button.book_id = book.id
        button.update_property([Gtk.AccessibleProperty.LABEL],
                               [f'{book.title}, {book.author}'])
        self.box.append(button)
        self.tiles.append(button)
        return button


@Gtk.Template(resource_path='/io/github/jackicus/Bookcase/home.ui')
class HomePage(Adw.NavigationPage):
    __gtype_name__ = 'BookcaseHomePage'

    compact_breakpoint = Gtk.Template.Child()
    stack = Gtk.Template.Child()
    scroller = Gtk.Template.Child()
    shelves_box = Gtk.Template.Child()
    empty_page = Gtk.Template.Child()
    tip_banner = Gtk.Template.Child()
    sources_box = Gtk.Template.Child()
    sources_list = Gtk.Template.Child()
    looking_box = Gtk.Template.Child()
    add_button = Gtk.Template.Child()

    def __init__(self):
        super().__init__()
        self.rows = []
        self.sources = None
        self._looking = False
        self._was_empty = False
        self._source_rows = []
        self._add_welcome_actions()
        connect_weak(self.tip_banner, 'button-clicked', self._on_tip_dismissed)
        self._compact = False
        self._menu_ids = []
        self.book_actions = BookActions(self, self._get_menu_ids)
        connect_weak(self.compact_breakpoint, 'apply', self._on_compact_apply)
        connect_weak(self.compact_breakpoint, 'unapply', self._on_compact_unapply)
        self.shelves_box.prepend(GoalCard())  # the reading goal, above the rows
        self.listener = PageListener(self, CHANGE_KINDS, HomePage.refresh)

    def _get_menu_ids(self):
        return self._menu_ids

    def _on_compact_apply(self, _breakpoint):
        self._compact = True
        GLib.idle_add(self._refresh_idle)

    def _on_compact_unapply(self, _breakpoint):
        self._compact = False
        GLib.idle_add(self._refresh_idle)

    def _refresh_idle(self):
        # Rows are rebuilt from an idle, never during the allocation a breakpoint runs in.
        if self.get_mapped():
            self.refresh()
        return GLib.SOURCE_REMOVE

    # -- content -----------------------------------------------------------------------------

    def refresh(self):
        library = app().library
        if library.count() == 0:
            self._clear()
            self.stack.set_visible_child_name('empty')
            self._was_empty = True
            self._look_for_sources()
            return
        self.stack.set_visible_child_name('home')
        self._maybe_show_tip()
        large, small = (LARGE_COMPACT, SMALL_COMPACT) if self._compact else (LARGE, SMALL)
        adjustment = self.scroller.get_vadjustment()
        scrolled = adjustment.get_value()
        self._clear()

        reading = library.continue_reading(limit=ROW_LIMIT)
        if reading:
            row = self._add_row('reading', _('Continue Reading'), 'status:reading',
                                library.count(status='reading') > len(reading))
            row.add_css_class('continue-reading')
            for book in reading:
                button = row.add_tile(book, large,
                                      progress_text(book, minutes_left(library, book)))
                connect_weak(button, 'clicked', self._on_read_clicked)
                self._add_menu(button)

        recent = library.recently_added(limit=ROW_LIMIT)
        if recent:
            row = self._add_row('recent', _('Recently Added'), 'all',
                                library.count() > len(recent))
            self._fill(row, recent, small)

        shelves = sorted(library.shelves(), key=lambda shelf: shelf.query is None)
        shown = 0
        for shelf in shelves:
            if shown >= SHELF_ROWS:
                break
            books = library.books(shelf=shelf.id, sort='added', limit=ROW_LIMIT)
            if not books:
                continue
            row = self._add_row(f'shelf:{shelf.id}', shelf.name, f'shelf:{shelf.id}',
                                library.count(shelf=shelf.id) > len(books))
            self._fill(row, books, small)
            shown += 1
        GLib.idle_add(lambda: (adjustment.set_value(scrolled), GLib.SOURCE_REMOVE)[1])

    # -- the welcome ---------------------------------------------------------------------------

    def _add_welcome_actions(self):
        """welcome.link, .watch, .copy-folder, .copy-files: each takes a source's path."""
        group = Gio.SimpleActionGroup()
        ref = self.weak_ref()
        for name, method in (('link', HomePage._on_link), ('watch', HomePage._on_watch),
                             ('copy-folder', HomePage._on_copy_folder),
                             ('copy-files', HomePage._on_copy_files)):
            action = Gio.SimpleAction.new(name, GLib.VariantType.new('s'))

            def activate(_action, value, method=method):
                page = ref()
                if page is not None:
                    method(page, value.get_string())

            action.connect('activate', activate)
            group.add_action(action)
        self.insert_action_group('welcome', group)

    def _look_for_sources(self):
        """existing_books.find() in a thread, once; its rows when it is done."""
        if self.sources is not None or self._looking:
            self._show_sources()
            return
        self._looking = True
        self._show_sources()
        ref = self.weak_ref()

        def work():
            try:
                found = existing_books.find()
            except Exception:
                log.exception('looking for books')
                found = []
            GLib.idle_add(done, found)

        def done(found):
            page = ref()
            if page is not None:
                page._looking = False
                page.sources = found
                page._show_sources()
            return GLib.SOURCE_REMOVE

        threading.Thread(target=work, name='bookcase-existing-books', daemon=True).start()

    def _show_sources(self):
        self.looking_box.set_visible(self._looking)
        for row in self._source_rows:
            self.sources_list.remove(row)
        self._source_rows = []
        known = {folder.path for folder in app().library.folders()}
        for source in self.sources or ():
            if os.path.abspath(source.path).rstrip('/') in known:
                continue
            row = self._source_row(source)
            self.sources_list.append(row)
            self._source_rows.append(row)
        self.sources_box.set_visible(bool(self._source_rows))
        # What was found comes first: its first row's button is the one to press.
        if self._source_rows:
            self._source_rows[0].button.add_css_class('suggested-action')
            self.add_button.remove_css_class('suggested-action')
        else:
            self.add_button.add_css_class('suggested-action')

    def _source_row(self, source):
        home = existing_books.home_dir().rstrip('/')
        shown = source.path
        if shown == home or shown.startswith(home + '/'):
            shown = '~' + shown[len(home):]
        if source.count is None:
            subtitle = shown
        else:
            number = f'{source.count:n}' + ('' if source.complete else '+')
            if source.kind == 'files':
                count = ngettext('{n} e-book', '{n} e-books', source.count).format(n=number)
            else:
                count = ngettext('{n} book', '{n} books', source.count).format(n=number)
            # Translators: a place books were found: "12 books · ~/Books".
            subtitle = _('{count} · {path}').format(count=count, path=shown)
        name = os.path.basename(source.path.rstrip('/')) or source.path
        target = GLib.Variant('s', source.path)
        if source.kind == 'calibre':
            title = _('Calibre Library')
            if name.casefold() not in ('calibre library', 'calibre'):
                title = _('Calibre Library “{name}”').format(name=name)
            icon = 'library-symbolic'
            button = Gtk.Button(label=_('_Link'), use_underline=True,
                                tooltip_text=_('Read the Calibre library where it is'),
                                action_name='welcome.link', action_target=target)
        elif source.kind == 'folder':
            title, icon = name, 'folder-symbolic'
            menu = Gio.Menu()
            item = Gio.MenuItem.new(_('_Copy into Library Folder'), None)
            item.set_action_and_target_value('welcome.copy-folder', target)
            menu.append_item(item)
            button = Adw.SplitButton(label=_('_Add'), use_underline=True, menu_model=menu,
                                     dropdown_tooltip=_('More Ways to Add'),
                                     tooltip_text=_('Read the books where they are, and the '
                                                    'new ones the folder gets'),
                                     action_name='welcome.watch', action_target=target)
        else:
            title = name
            downloads = GLib.get_user_special_dir(GLib.UserDirectory.DIRECTORY_DOWNLOAD)
            icon = ('folder-download-symbolic' if source.path == downloads
                    or name == 'Downloads' else 'folder-documents-symbolic')
            button = Gtk.Button(label=_('_Add'), use_underline=True,
                                tooltip_text=_('Copy these e-books into your library folder'),
                                action_name='welcome.copy-files', action_target=target)
        button.set_valign(Gtk.Align.CENTER)
        row = Adw.ActionRow(title=title, subtitle=subtitle, use_markup=False)
        row.add_prefix(Gtk.Image(icon_name=icon,
                                 accessible_role=Gtk.AccessibleRole.PRESENTATION))
        row.add_suffix(button)
        row.button = button
        return row

    def _source(self, path):
        return next((source for source in self.sources or () if source.path == path), None)

    def _on_link(self, path):
        app().link_calibre(path)

    def _on_watch(self, path):
        app().add_folder(path)

    def _on_copy_folder(self, path):
        app().add_files([Gio.File.new_for_path(path)])

    def _on_copy_files(self, path):
        source = self._source(path)
        if source is not None:
            app().add_files([Gio.File.new_for_path(each) for each in source.paths])

    def _maybe_show_tip(self):
        settings = app().settings
        if self._was_empty and not settings.get_boolean('welcome-tip-shown'):
            settings.set_boolean('welcome-tip-shown', True)
            self.tip_banner.set_revealed(True)

    def _on_tip_dismissed(self, banner):
        banner.set_revealed(False)

    # -- rows ----------------------------------------------------------------------------------

    def _clear(self):
        for _key, row in self.rows:
            self.shelves_box.remove(row)
        self.rows = []

    def _add_row(self, key, title, show_all_key, more):
        row = ShelfRow(title, show_all_key, more)
        connect_weak(row.show_all, 'clicked', self._on_show_all)
        self.shelves_box.append(row)
        self.rows.append((key, row))
        return row

    def _fill(self, row, books, width):
        for book in books:
            button = row.add_tile(book, width)
            connect_weak(button, 'clicked', self._on_details_clicked)
            self._add_menu(button)

    def _add_menu(self, button):
        click = Gtk.GestureClick(button=Gdk.BUTTON_SECONDARY)
        connect_weak(click, 'pressed', self._on_secondary_click)
        button.add_controller(click)
        press = Gtk.GestureLongPress(touch_only=True)
        connect_weak(press, 'pressed', self._on_long_press)
        button.add_controller(press)

    # -- actions -----------------------------------------------------------------------------

    def _window(self):
        return self.get_root()

    def _on_read_clicked(self, button):
        app().open_book(button.book_id)

    def _on_details_clicked(self, button):
        window = self._window()
        if window is not None and hasattr(window, 'show_book'):
            window.show_book(button.book_id)

    def _on_show_all(self, button):
        row = button.get_ancestor(ShelfRow)
        window = self._window()
        if row is not None and window is not None and hasattr(window, 'show_root'):
            window.show_root(row.show_all_key)

    def _on_secondary_click(self, gesture, _n_press, x, y):
        gesture.set_state(Gtk.EventSequenceState.CLAIMED)
        self._popup(gesture.get_widget(), x, y)

    def _on_long_press(self, gesture, x, y):
        self._popup(gesture.get_widget(), x, y)

    def _popup(self, button, x, y):
        self._menu_ids = [button.book_id]
        self.book_actions.update()
        popup_menu(button, book_menu(app().library), x, y)
