# SPDX-License-Identifier: GPL-2.0-or-later
# SPDX-FileCopyrightText: 2026 Jack Tully

"""The library window's behaviour around its pages and dialogs: toasts that go and an Undo
toast that is about the newest change, Ctrl+Z in a text field, dialogs and pushed pages that
are freed once gone, the home page's rows kept while a reader saves its place, the groups'
filter, sending to an e-reader unplugged meanwhile, the window letting go of the library
when it closes, and the icons the window's widgets name."""

import gc
import os
import re
import time
import unittest

from tests import ROOT
from tests.gtk import close_window, pump, requires_gtk, wait_for
from tests.support import add_book, temporary_library
from tests.test_pages import Covers, stand_ins


def _freed(ref):
    """Whether the object a GObject weak reference follows goes within a moment (a dialog's
    closing animation), garbage collected meanwhile."""
    deadline = time.monotonic() + 0.8
    while time.monotonic() < deadline:
        pump(50)
        gc.collect()
        if ref() is None:
            return True
    return False


class _ToastWindow:
    """What Application.toast needs of the library window."""

    def __init__(self):
        from gi.repository import Adw

        self.overlay = Adw.ToastOverlay()

    def add_toast(self, toast):
        self.overlay.add_toast(toast)


@requires_gtk
class ToastTest(unittest.TestCase):
    def setUp(self):
        from bookcase import main

        self.app = main.Application('0', 'io.github.jackicus.Bookcase.ToastTest',
                                    'io.github.jackicus.Bookcase', 'default')
        self.window = _ToastWindow()
        self.app.window = lambda: self.window
        self.addCleanup(lambda: stand_ins()['app'].set_default())

    def test_toasts_go_by_themselves(self):
        plain = self.app.toast('Exported 3 books')
        self.assertGreater(plain.get_timeout(), 0)
        undo = self.app.toast('Removed “Lantern Hill” from the library', undo=True)
        self.assertGreater(undo.get_timeout(), 0)
        self.assertEqual(undo.get_action_name(), 'app.undo')

    def test_a_new_undo_toast_replaces_the_last(self):
        from gi.repository import Adw

        first = self.app.toast('Removed a book', undo=True)
        dismissed = []
        first.connect('dismissed', lambda *_args: dismissed.append(True))
        second = self.app.toast('Marked a book as finished', undo=True)
        # The first toast's Undo would now put back the second change: it goes.
        self.assertEqual(dismissed, [True])
        self.assertEqual(second.get_priority(), Adw.ToastPriority.HIGH)
        self.assertIs(self.app._undo_toast, second)


@requires_gtk
class TextUndoTest(unittest.TestCase):
    def test_ctrl_z_in_a_text_field_is_the_fields(self):
        from gi.repository import Gtk

        from bookcase.main import text_undo

        window = Gtk.Window()
        box = Gtk.Box()
        entry = Gtk.Entry()
        button = Gtk.Button(label='Go')
        box.append(entry)
        box.append(button)
        window.set_child(box)
        window.present()
        self.addCleanup(close_window, window)
        self.assertTrue(wait_for(window.get_mapped))
        entry.grab_focus()
        self.assertTrue(text_undo(window))
        button.grab_focus()
        self.assertFalse(text_undo(window))
        self.assertFalse(text_undo(None))


@requires_gtk
class FreedTest(unittest.TestCase):
    """A dialog closed, or a pushed page popped, is freed: nothing it connected holds it."""

    @classmethod
    def setUpClass(cls):
        found = stand_ins()
        cls.app = found['app']
        cls.window = found['Window']()
        # Pushed without animating: an animated push keeps the page in libadwaita a while.
        cls.window.navigation_view.set_animate_transitions(False)
        cls.window.present()
        wait_for(cls.window.get_mapped, 2)

    @classmethod
    def tearDownClass(cls):
        cls.window.destroy()
        pump()

    def setUp(self):
        self.app.set_default()
        context = temporary_library()
        self.library = context.__enter__()
        self.addCleanup(context.__exit__, None, None, None)
        self.app.library = self.library
        self.app.covers = Covers()
        self.book = add_book(self.library, 'A Quiet Harbour', ('Ada Lark',), tags=['Sea'])

    def closed_and_freed(self, dialog):
        ref = dialog.weak_ref()
        pump(100)
        dialog.force_close()
        del dialog
        return _freed(ref)

    def test_shelf_dialog(self):
        from bookcase.dialogs import shelf

        self.assertTrue(self.closed_and_freed(shelf.present_new(self.app, self.window)))

    def test_go_to(self):
        from bookcase.dialogs import quick_open

        self.assertTrue(self.closed_and_freed(quick_open.present(self.app, self.window)))

    def test_edit_details(self):
        from bookcase.dialogs import edit_metadata

        self.assertTrue(self.closed_and_freed(
            edit_metadata.present(self.app, self.window, [self.book])))

    def test_book_page(self):
        from gi.repository import Adw

        from bookcase.pages.book import BookPage

        page = BookPage(self.book)
        self.window.navigation_view.replace([page])
        pump(100)
        ref = page.weak_ref()
        del page
        self.window.navigation_view.replace([Adw.NavigationPage(title='Blank')])
        self.assertTrue(_freed(ref))


@requires_gtk
class PagesTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        found = stand_ins()
        cls.app = found['app']
        cls.window = found['Window']()
        cls.window.present()
        wait_for(cls.window.get_mapped, 2)

    @classmethod
    def tearDownClass(cls):
        cls.window.destroy()
        pump()

    def setUp(self):
        self.app.set_default()
        self.app.toasts = []
        context = temporary_library()
        self.library = context.__enter__()
        self.addCleanup(context.__exit__, None, None, None)
        self.app.library = self.library
        self.app.covers = Covers()
        self.harbour = add_book(self.library, 'A Quiet Harbour', ('Ada Lark',))
        self.hill = add_book(self.library, 'Lantern Hill', ('Ben Ross',))

    def tearDown(self):
        from gi.repository import Adw

        self.window.navigation_view.replace([Adw.NavigationPage(title='Blank')])
        pump()

    def test_home_rows_stay_while_a_reader_saves_its_place(self):
        from bookcase.pages.home import HomePage

        self.library.set_status([self.harbour], 'reading')
        self.library.set_progress(self.harbour, 0.4012, '')
        page = HomePage()
        self.window.navigation_view.replace([page])
        self.assertTrue(wait_for(page.get_mapped))
        page.refresh()
        tiles = [tile for _key, row in page.rows for tile in row.tiles]
        self.library.set_progress(self.harbour, 0.4013, '')  # a page turned
        page.refresh()
        self.assertEqual([tile for _key, row in page.rows for tile in row.tiles], tiles)
        self.library.update_book(self.hill, title='Lantern Hill Again')
        page.refresh()
        self.assertNotEqual([tile for _key, row in page.rows for tile in row.tiles], tiles)

    def test_groups_filter_ignores_accents(self):
        from bookcase.pages.groups import GroupsPage

        add_book(self.library, 'Salt Roads', ('Tomás Ibarra',))
        page = GroupsPage('authors')
        self.window.navigation_view.replace([page])
        self.assertTrue(wait_for(page.get_mapped))
        self.assertTrue(wait_for(lambda: page._store.get_n_items() == 3))
        page.search('tomas')
        self.assertEqual([page._filtered.get_item(n).name
                          for n in range(page._filtered.get_n_items())], ['Tomás Ibarra'])

    def test_books_with_files_in_a_calibre_library_are_never_trashed(self):
        from gi.repository import Gtk

        from bookcase.pages.actions import BookActions, confirm_trash

        self.library.add_folder('/invented/Calibre Library', 'calibre')
        # A Calibre book merged into a library book: its source says 'library'.
        self.library.add_file(self.harbour, '/invented/Calibre Library/Ada/harbour.epub',
                              hash='c0ffee', size=10)
        actions = BookActions(Gtk.Box(), lambda: [self.harbour])
        self.assertFalse(actions.group.lookup_action('trash').get_enabled())
        self.assertIsNone(confirm_trash(self.window, [self.harbour]))
        self.assertIn('Calibre', self.app.toasts[-1][0])
        self.assertIsNotNone(self.library.book(self.harbour))

    def test_book_actions_on_a_book_gone_from_the_library(self):
        from gi.repository import Gtk

        from bookcase.pages.actions import BookActions

        widget = Gtk.Box()
        actions = BookActions(widget, lambda: [self.harbour])
        self.library.remove_books([self.harbour])
        actions.update()  # the selection still names it
        self.assertFalse(actions.group.lookup_action('details').get_enabled())
        self.assertFalse(actions.group.lookup_action('show-in-files').get_enabled())


@requires_gtk
class KeepBookTest(unittest.TestCase):
    """Add to Library for a book read without adding: the copy is made in a thread, the
    change is the main library's own undo step, so the toast's Undo is about it."""

    def test_keep_book_is_its_own_undo_step(self):
        import tempfile

        from bookcase import main
        from bookcase.covers import CoverStore
        from bookcase.importing import Importer
        from tests.support import make_epub

        app = main.Application('0', 'io.github.jackicus.Bookcase.KeepTest',
                               'io.github.jackicus.Bookcase', 'default')
        self.addCleanup(lambda: stand_ins()['app'].set_default())
        with temporary_library() as library, tempfile.TemporaryDirectory() as folder:
            covers = CoverStore(library.path.parent, library)
            self.addCleanup(getattr(covers, 'shutdown', lambda: None))
            outside = make_epub(os.path.join(folder, 'outside.epub'), title='Salt Roads')
            books = os.path.join(folder, 'Books')
            book_id = Importer(library, covers, books).open_in_place(outside)
            app.library, app.covers = library, covers
            app.library_folder = lambda: books
            window = _ToastWindow()
            app.window = lambda: window
            shelf = library.add_shelf('Holiday')  # the change before
            done = []
            app.keep_book(book_id, done=done.append)
            self.assertTrue(wait_for(lambda: done))
            self.assertEqual(done, [True])
            self.assertTrue(app._undo_toast is not None)
            kept = library.reading_file(book_id).path
            self.assertTrue(kept.startswith(books) and os.path.exists(kept))
            self.assertEqual(library.undo(), 'Add to Library')
            self.assertIsNotNone(library.shelf(shelf))  # Undo took back the add, only


class _Device:
    def __init__(self, device_id, name):
        self.id = device_id
        self.name = name
        self.kind = 'kobo'


@requires_gtk
class SendTest(unittest.TestCase):
    def test_unplugged_while_sending(self):
        from gi.repository import Gio

        from bookcase.dialogs.send import SendDialog, gone_text

        dialog = SendDialog.__new__(SendDialog)  # only what _on_devices_changed uses
        kobo = _Device('kobo-1', 'Kobo Clara')

        class Monitor:
            devices = staticmethod(lambda: [])

        class App:
            devices = Monitor()

        class Widget:
            def set_sensitive(self, _value):
                pass

            def set_text(self, text):
                self.text = text

        dialog.__dict__.update(app=App(), device=kobo, sending=True, _device_gone=False,
                               cancellable=Gio.Cancellable(), cancel_button=Widget(),
                               progress_title=Widget())
        SendDialog._on_devices_changed(dialog)
        self.assertTrue(dialog.cancellable.is_cancelled())
        self.assertTrue(dialog._device_gone)
        self.assertIn('Kobo Clara', dialog.progress_title.text)
        self.assertEqual(gone_text('Kobo Clara', ['A', 'B']),
                         'Kobo Clara was disconnected after 2 books were sent')


@requires_gtk
class WindowTest(unittest.TestCase):
    def test_a_closed_window_lets_go_of_the_library(self):
        from bookcase.window import Window

        app = stand_ins()['app']
        app.set_default()
        app.settings.reset('last-page')
        with temporary_library() as library:
            app.library = library
            app.covers = Covers()
            add_book(library, 'A Quiet Harbour', ('Ada Lark',))
            window = Window(application=app)
            controller = window.sidebar_controller
            window.present()
            self.assertTrue(wait_for(window.get_mapped))
            window.destroy()
            pump()
            self.assertEqual(controller._handlers, [])
            self.assertEqual(window._handlers, [])
            library.add_shelf('Holiday')  # nothing of the window hears of it
            self.assertIsNone(controller._refresh_pending)


class IconsTest(unittest.TestCase):
    """Every symbolic icon the window's widgets name is in the Adwaita theme, libadwaita's own
    or the app's (an icon that is not falls back to a full-colour one, or to nothing)."""

    THEMES = ('/usr/share/icons/Adwaita', '/usr/share/icons/hicolor')

    def test_icon_names_exist(self):
        if not os.path.isdir(self.THEMES[0]):
            self.skipTest('the Adwaita icon theme is not installed')
        known = set()
        for top in (*self.THEMES, str(ROOT / 'src' / 'icons')):
            for _directory, _dirs, files in os.walk(top):
                known.update(name.rsplit('.', 1)[0] for name in files)
        sources = [ROOT / 'src' / 'window.blp', ROOT / 'src' / 'sidebar.py',
                   ROOT / 'src' / 'main.py', ROOT / 'src' / 'window.py']
        for folder in ('pages', 'dialogs', 'widgets'):
            sources.extend(sorted((ROOT / 'src' / folder).glob('*.py')))
            sources.extend(sorted((ROOT / 'src' / folder).glob('*.blp')))
        missing = set()
        for path in sources:
            for name in re.findall(r'[\'"]([a-z0-9-]+-symbolic)[\'"]', path.read_text()):
                if name not in known and not name.startswith('adw-'):
                    missing.add(f'{path.name}: {name}')
        self.assertEqual(sorted(missing), [])


if __name__ == '__main__':
    unittest.main()
