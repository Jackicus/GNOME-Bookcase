# SPDX-License-Identifier: GPL-2.0-or-later
# SPDX-FileCopyrightText: 2026 Jack Tully

"""Accessibility: the library window's pages, a book's details, the dialogs and the reader
window built over an invented library, and their widget trees walked for what a screen reader
needs: an icon-only button with a tooltip or an accessible label, an image either labelled or
presentation, a cover labelled, a progress bar labelled with its value as text, a text field
or a control labelled (an Adw row labels its own), nothing that takes input out of the
keyboard's reach; and Tab through the library window going from the sidebar into the grid
and back round.

GTK keeps a widget's accessible properties only in its test backend (GTK_A11Y=test: with
`none`, which scripts/check.sh's runs may set, it forgets them), so the walk runs in a child
process of its own with GTK_A11Y=test, started by AccessibilityTest; the classes under it
run only there.
"""

import os
import subprocess
import sys
import unittest

from tests import ROOT
from tests.gtk import gtk_unavailable, pump, requires_gtk, wait_for
from tests.support import add_book, make_epub

CHILD = 'BOOKCASE_A11Y_CHILD'
# A web view's page is WebKit's own accessible tree; a dialog's sheet controls are like a
# window's (libadwaita's private class).
PRIVATE_COMPOSITES = ('WebView', 'AdwSheetControls')
IN_CHILD = os.environ.get(CHILD) == '1'


class AccessibilityTest(unittest.TestCase):
    """Runs the walk below in a child process under GTK's test accessibility backend."""

    def test_the_walk_passes_in_gtks_test_backend(self):
        if IN_CHILD:
            self.skipTest('this is the child')
        reason = gtk_unavailable()
        if reason is not None:
            self.skipTest(reason)
        env = dict(os.environ, GTK_A11Y='test', **{CHILD: '1'})
        done = subprocess.run([sys.executable, '-m', 'unittest', 'tests.test_accessibility'],
                              cwd=ROOT, env=env, capture_output=True, text=True, timeout=300)
        self.assertEqual(done.returncode, 0, done.stdout + done.stderr)


# -- the walk ---------------------------------------------------------------------------------


def _children(widget):
    child = widget.get_first_child()
    while child is not None:
        yield child
        child = child.get_next_sibling()


def _composites():
    """The toolkit's widgets whose insides are the toolkit's: walked as one widget."""
    from gi.repository import Adw, Gtk

    return (Gtk.Switch, Gtk.SpinButton, Gtk.Entry, Gtk.SearchEntry, Gtk.PasswordEntry,
            Gtk.DropDown, Gtk.Scale, Gtk.Calendar, Gtk.Text, Gtk.ColorDialogButton,
            Gtk.FontDialogButton, Adw.SplitButton, Gtk.ShortcutLabel, Adw.ShortcutLabel,
            Gtk.WindowControls)  # a window's buttons are never focusable, by design


def visible_descendants(widget, composites=None):
    """`widget` and every widget under it that is visible (a hidden widget's subtree is
    skipped; a stack's other pages are walked), the toolkit's composites as one widget."""
    composites = composites or _composites()
    if not widget.get_visible():
        return
    yield widget
    if isinstance(widget, composites) or type(widget).__name__ in PRIVATE_COMPOSITES:
        return
    for child in _children(widget):
        yield from visible_descendants(child, composites)


def _named(widget):
    """Whether a screen reader has a name for `widget`: an accessible label (or labelled-by),
    a tooltip, or a label of text inside it."""
    from gi.repository import Gtk

    if Gtk.test_accessible_has_property(widget, Gtk.AccessibleProperty.LABEL):
        return True
    if Gtk.test_accessible_has_relation(widget, Gtk.AccessibleRelation.LABELLED_BY):
        return True
    if widget.get_tooltip_text():
        return True
    for found in visible_descendants(widget, (Gtk.Popover,)):
        if isinstance(found, Gtk.Label) and found.get_text().strip():
            return True
        if isinstance(found, Gtk.Inscription) and (found.get_text() or '').strip():
            return True
    getter = getattr(widget, 'get_label', None)
    try:
        label = getter() if getter is not None else None
    except TypeError:
        label = None
    return isinstance(label, str) and bool(label.strip())


def _inside(widget, kinds):
    parent = widget.get_parent()
    while parent is not None:
        if isinstance(parent, kinds):
            return parent
        parent = parent.get_parent()
    return None


def _where(widget):
    """A path of type names (and buildable ids) from the root to `widget`, for a message."""
    from gi.repository import Gtk

    parts = []
    while widget is not None:
        name = type(widget).__name__
        ident = Gtk.Buildable.get_buildable_id(widget) if isinstance(
            widget, Gtk.Buildable) else None
        parts.append(f'{name}#{ident}' if ident else name)
        widget = widget.get_parent()
    return ' > '.join(reversed(parts[:6]))


def _reachable(widget):
    """Whether the keyboard can get to `widget`: no container above it refuses the focus."""
    parent = widget.get_parent()
    while parent is not None:
        if not parent.get_can_focus():
            return False
        parent = parent.get_parent()
    return True


def problems(root):
    """What a screen reader or a keyboard user would miss under `root`, as sentences."""
    from gi.repository import Adw, Gtk

    from bookcase.widgets.cover import Cover

    role = Gtk.AccessibleRole
    found = []
    buttons = (Gtk.Button, Gtk.MenuButton)
    fields = (Gtk.Entry, Gtk.SearchEntry, Gtk.PasswordEntry, Gtk.SpinButton, Gtk.TextView,
              Gtk.Scale, Gtk.DropDown, Gtk.Switch)
    for widget in visible_descendants(root):
        where = _where(widget)
        if isinstance(widget, buttons):
            if isinstance(widget, Gtk.ToggleButton) and isinstance(widget.get_parent(),
                                                                    Gtk.MenuButton):
                continue  # the menu button's own toggle: the menu button is checked
            if not _named(widget):
                found.append(f'a button without a tooltip or label: {where}')
            if widget.get_sensitive() and not (widget.get_focusable() or isinstance(
                    widget, Gtk.MenuButton)) or not _reachable(widget):
                found.append(f'a button the keyboard cannot reach: {where}')
        elif isinstance(widget, Cover):
            if widget.get_accessible_role() != role.IMG or not _named(widget):
                found.append(f'a cover that is not an image labelled with its title: {where}')
        elif isinstance(widget, (Gtk.Image, Gtk.Picture)):
            if widget.get_accessible_role() == role.PRESENTATION or _inside(
                    widget, (*buttons, Adw.EntryRow)):  # an entry row's own indicator
                continue
            if not _named(widget):
                found.append(f'an image neither labelled nor presentation: {where}')
        elif isinstance(widget, (Gtk.ProgressBar, Gtk.LevelBar)):
            if widget.get_accessible_role() == role.PRESENTATION:
                continue  # its parent says how far (a statistic's tile)
            if not _named(widget):
                found.append(f'a progress bar without a label: {where}')
            if isinstance(widget, Gtk.ProgressBar) and widget.get_mapped() \
                    and not Gtk.test_accessible_has_property(
                        widget, Gtk.AccessibleProperty.VALUE_TEXT):
                found.append(f'a progress bar without its value as text: {where}')
        elif isinstance(widget, fields):
            if _inside(widget, (Adw.EntryRow, Adw.SpinRow, Adw.ComboRow, Adw.SwitchRow,
                                Adw.PasswordEntryRow)):
                continue  # the row is the field, labelled by its title
            if not _named(widget):
                found.append(f'a field without a label: {where}')
            if not _reachable(widget):
                found.append(f'a field the keyboard cannot reach: {where}')
        elif isinstance(widget, Gtk.CheckButton):
            if not _named(widget):
                found.append(f'a check box without a label: {where}')
        elif isinstance(widget, Adw.ToggleGroup):
            toggles = widget.get_toggles()
            for index in range(toggles.get_n_items()):
                toggle = toggles.get_item(index)
                if not (toggle.get_label() or toggle.get_tooltip()):
                    found.append(f'a toggle without a label or tooltip: {where}')
    return found


# -- the screens ------------------------------------------------------------------------------


@unittest.skipUnless(IN_CHILD, 'run by AccessibilityTest under GTK_A11Y=test')
@requires_gtk
class WalkTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        import uuid

        from gi.repository import Gio, GLib

        from bookcase import main

        cls.app = main.Application('0', 'io.github.jackicus.Bookcase.A11yTest_'
                                   + uuid.uuid4().hex, 'io.github.jackicus.Bookcase',
                                   'default')
        cls.app.set_flags(cls.app.get_flags() | Gio.ApplicationFlags.NON_UNIQUE)
        cls.app.register(None)
        if cls.app._rescan_source is not None:
            GLib.source_remove(cls.app._rescan_source)
            cls.app._rescan_source = None
        library = cls.app.library
        folder = cls.app.data_dir / 'books'
        folder.mkdir(exist_ok=True)
        epub = folder / 'harbour.epub'
        make_epub(epub, chapters=3)
        cls.book = add_book(library, 'A Quiet Harbour', ('Ada Lark',), path=epub,
                            series='Tides', series_index=1, tags=['Sea'])
        for number in range(12):
            book = add_book(library, f'Lantern Book {number}', (f'Ben Ross {number % 3}',),
                            tags=['Night'])
            if number % 4 == 0:
                library.set_progress(book, 0.4, 'epubcfi(/6/2)')
        library.add_shelf('Holiday')
        from bookcase.window import Window

        cls.window = Window(application=cls.app)
        cls.window.navigation_view.set_animate_transitions(False)
        cls.window.present()
        wait_for(cls.window.get_mapped, 3)

    @classmethod
    def tearDownClass(cls):
        cls.window.destroy()
        pump()
        cls.app.do_shutdown()

    def check(self, root, what):
        pump(300)
        found = problems(root)
        self.assertEqual(found, [], what + ':\n' + '\n'.join(found))

    def test_the_walk_finds_what_it_looks_for(self):
        from gi.repository import Gtk

        box = Gtk.Box()
        for widget in (Gtk.Button(icon_name='edit-symbolic'), Gtk.Image(icon_name='x'),
                       Gtk.ProgressBar(), Gtk.Entry(), Gtk.Button(label='Fine')):
            box.append(widget)
        hidden = Gtk.Box(can_focus=False)
        hidden.append(Gtk.Button(label='Out of reach'))
        box.append(hidden)
        self.assertEqual([found.split(':')[0] for found in problems(box)], [
            'a button without a tooltip or label', 'an image neither labelled nor presentation',
            'a progress bar without a label', 'a field without a label',
            'a button the keyboard cannot reach'])

    def test_the_library_pages(self):
        for key in ('home', 'all', 'missing', 'authors', 'series', 'tags', 'status:reading',
                    'status:unread', 'status:finished', 'stats', 'discover', 'shelf:1'):
            with self.subTest(key):
                self.window.show_root(key)
                self.check(self.window, key)

    def test_the_keyboard_goes_from_the_sidebar_into_the_grid(self):
        # GTK sorts Tab's order by where widgets are (top to bottom, then along the line):
        # the sidebar is left of the page, the page's search button above its grid, and
        # nothing on the way keeps the focus out.
        from gi.repository import Gtk

        self.window.show_root('all')
        pump(300)
        order = list(visible_descendants(self.window, (Gtk.GridView,)))
        sidebar = self.window.sidebar
        grid = next(widget for widget in order if isinstance(widget, Gtk.GridView))
        search = next(widget for widget in order if isinstance(widget, Gtk.ToggleButton)
                      and widget.get_icon_name() == 'system-search-symbolic')

        def bounds(widget):
            ok, rect = widget.compute_bounds(self.window)
            self.assertTrue(ok, _where(widget))
            return rect

        self.assertLess(bounds(sidebar).get_x(), bounds(grid).get_x())
        self.assertLess(bounds(search).get_y(), bounds(grid).get_y())
        for widget in (sidebar, grid, search):
            self.assertTrue(widget.get_focusable() or widget.get_can_focus(), _where(widget))
            self.assertTrue(_reachable(widget), _where(widget))

    def test_a_books_details(self):
        self.window.show_root('all')
        self.window.show_book(self.book)
        self.check(self.window, 'book')
        self.window.pop()

    def test_the_dialogs(self):
        from tests.test_dialogs_fetch import CURRENT, server

        from bookcase.dialogs import (add_catalog, edit_metadata, fetch_metadata, goals,
                                      highlights, kindle_mail, note, preferences, quick_open,
                                      send, shelf, shortcuts)

        app, window, book = self.app, self.window, self.book
        for name, present in (
                ('edit metadata', lambda: edit_metadata.present(app, window, [book])),
                ('edit two books', lambda: edit_metadata.present(app, window, [book, 2])),
                ('find metadata', lambda: fetch_metadata.present(
                    app, window, CURRENT, lambda _values: None, fetch=server())),
                ('find cover', lambda: fetch_metadata.present_covers(
                    app, window, CURRENT, lambda _data: None, fetch=server())),
                ('preferences', lambda: preferences.present(app, window)),
                ('new shelf', lambda: shelf.present_new(app, window)),
                ('smart shelf', lambda: shelf.present_new(app, window, smart=True)),
                ('goals', lambda: goals.present(app, window)),
                ('shortcuts', lambda: shortcuts.present(app, window)),
                ('go to', lambda: quick_open.present(app, window)),
                ('note', lambda: note.present(app, window, 'The tide came in.', '',
                                              lambda *_args: None)),
                ('choose book', lambda: highlights.choose_book(app, window, 'Lantern',
                                                               lambda _id: None)),
                ('kindle mail', lambda: kindle_mail.present_setup(app, window)),
                ('add catalogue', lambda: add_catalog.present_add(app, window)),
                ('send', lambda: send.present(app, window, [book]))):
            with self.subTest(name):
                dialog = present()
                try:
                    self.check(dialog, name)
                finally:
                    dialog.force_close()
                    pump()

    def test_the_reader_window(self):
        from bookcase import reader_window

        window = reader_window.open(self.app, self.book)
        try:
            wait_for(window.get_mapped, 3)
            window.split_view.set_show_sidebar(True)
            self.check(window, 'reader')
            popover = window.typography_button.get_popover()
            popover.popup()
            self.check(popover, 'typography')
            popover.popdown()
            said = []
            window.announce = lambda text, _priority: said.append(text)
            window._announced_chapter = None
            for href, label in (('a', 'One'), ('a', 'One'), ('b', 'Two'), ('b', 'Two')):
                window._place = {'chapter': {'href': href, 'label': label}}
                window._update_title()
            self.assertEqual(said, ['Two'])  # chapter changes, not the opening one or pages
        finally:
            window.close()
            pump()


if __name__ == '__main__':
    unittest.main()
