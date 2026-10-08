# SPDX-License-Identifier: GPL-2.0-or-later
# SPDX-FileCopyrightText: 2026 Jack Tully

"""Widget tests for the device page (pages/device.py) and Send to Device (dialogs/send.py),
over a fake Kobo (a folder made by devices.make_test_tree) and a stand-in library that
answers only what they ask: book, files, find_by_hash, find_similar and `changed`."""

from tests import ROOT  # noqa: F401  (registers src/ as bookcase)

import os
import pathlib
import shutil
import tempfile
import time
import unittest

from bookcase import devices
from bookcase.library import Book, BookFile
from tests.gtk import SCHEMA_ID, pump, requires_gtk, wait_for
from tests.support import make_epub

_stand_ins = {}


class StandInLibrary:
    def __init__(self, books, files):
        self._books = {book.id: book for book in books}
        self._files = files  # book id -> [BookFile]
        self._handlers = {}

    def book(self, book_id):
        return self._books.get(book_id)

    def files(self, book_id):
        return self._files.get(book_id, [])

    def find_by_hash(self, _hash):
        return None

    def find_similar(self, title, _authors):
        return [book.id for book in self._books.values() if book.title == title]

    def open_worker(self):
        return self

    def close(self):
        pass

    def connect(self, _signal, handler):
        handler_id = len(self._handlers) + 1
        self._handlers[handler_id] = handler
        return handler_id

    def disconnect(self, handler_id):
        self._handlers.pop(handler_id, None)


def stand_ins():
    if _stand_ins:
        return _stand_ins
    from gi.repository import Adw, Gio, Gtk

    class App(Adw.Application):
        def __init__(self):
            super().__init__(application_id='io.github.jackicus.Bookcase.DeviceTest',
                             flags=Gio.ApplicationFlags.NON_UNIQUE)
            self.settings = Gio.Settings.new(SCHEMA_ID)
            self.library = None
            self.covers = None
            self.devices = devices.DeviceMonitor(watch_mounts=False)
            self.toasts = []
            self.added = []

        def toast(self, text, undo=False):
            self.toasts.append(text)

        def report(self, error, context=None):
            raise AssertionError(error)

        def add_files(self, files):
            self.added.extend(file.get_path() for file in files)

    class Window(Adw.Window):
        def __init__(self):
            super().__init__(default_width=800, default_height=700)
            self.navigation_view = Adw.NavigationView()
            self.set_content(self.navigation_view)
            self.shown = []
            self.books = []

        def show_root(self, key):
            self.shown.append(key)

        def show_book(self, book_id):
            self.books.append(book_id)

        def set_dialog_open(self, _open):
            pass

    _stand_ins.update(app=App(), Window=Window, Gtk=Gtk)
    return _stand_ins


def rows(listbox):
    found = []
    row = listbox.get_first_child()
    while row is not None:
        found.append(row)
        row = row.get_next_sibling()
    return found


@requires_gtk
class DeviceWidgetsTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        found = stand_ins()
        cls.app = found['app']
        cls.window = found['Window']()
        cls.window.present()
        deadline = time.monotonic() + 2
        while not cls.window.get_mapped() and time.monotonic() < deadline:
            pump(20)

    @classmethod
    def tearDownClass(cls):
        cls.window.destroy()
        pump()

    def setUp(self):
        self.app.set_default()
        self.app.toasts = []
        self.app.added = []
        self.directory = pathlib.Path(tempfile.mkdtemp(prefix='bookcase-test-device-ui-'))
        self.addCleanup(shutil.rmtree, self.directory, True)
        self.root = devices.make_test_tree(self.directory / 'kobo', 'kobo', model=386)
        folder = self.root / 'Bookcase' / 'Ada Lark'
        folder.mkdir(parents=True)
        self.known = make_epub(folder / 'A Quiet Harbour.kepub.epub', title='A Quiet Harbour')
        self.stranger = make_epub(self.root / 'Salt Roads.epub', title='Salt Roads',
                                  authors=('Cy Moor',))
        books = [Book(id=1, uuid='u1', title='A Quiet Harbour', sort_title='quiet harbour',
                      authors=('Ada Lark',), formats=('epub',)),
                 Book(id=2, uuid='u2', title='Lantern Hill', sort_title='lantern hill',
                      authors=('Ben Ross',), formats=('azw3',))]
        files = {1: [BookFile(1, 1, '/invented/a.epub', 'epub', 10, 'h1')],
                 2: [BookFile(2, 2, '/invented/b.azw3', 'azw3', 10, 'h2')]}
        self.app.library = StandInLibrary(books, files)
        self.device = self.app.devices.add_test_root(str(self.root))
        self.addCleanup(self.app.devices.remove_test_root, str(self.root))

    def show(self, page):
        self.window.navigation_view.replace([page])
        self.assertTrue(wait_for(lambda: page.stack.get_visible_child_name() == 'books'))

    def test_page_lists_books_by_library_status(self):
        from bookcase.pages.device import DevicePage

        page = DevicePage(self.device.id)
        self.show(page)
        self.assertEqual(page.get_title(), 'Kobo Clara 2E')
        self.assertEqual([row.get_title() for row in rows(page.outside_list)], ['Salt Roads'])
        self.assertEqual([row.book_id for row in rows(page.inside_list)], [1])
        self.assertIn('2 books', page.summary_label.get_text())
        self.assertFalse(page.empty_status.get_visible())
        self.assertEqual(self.device.book_ids, {1})

        page.inside_list.emit('row-activated', rows(page.inside_list)[0])
        self.assertEqual(self.window.books, [1])
        rows(page.outside_list)[0].add_button.emit('clicked')
        self.assertEqual(self.app.added, [str(self.stranger)])

    def test_select_and_remove(self):
        from bookcase.pages.device import DevicePage

        page = DevicePage(self.device.id)
        self.show(page)
        page.set_selection_mode(True)
        self.assertTrue(page.selection_bar.get_revealed())
        self.assertFalse(page.remove_button.get_sensitive())
        row = rows(page.outside_list)[0]
        self.assertTrue(row.check.get_visible())
        page.outside_list.emit('row-activated', row)
        self.assertEqual(page.selected, {str(self.stranger)})
        self.assertTrue(page.remove_button.get_sensitive())
        dialog = page.present_remove_dialog()
        self.assertIn('Remove 1 Book From Kobo Clara 2E', dialog.get_heading())
        dialog.force_close()
        page.remove_books([str(self.stranger)])
        self.assertTrue(wait_for(lambda: not self.stranger.exists()
                                 and len(rows(page.outside_list)) == 0),
                        f'file there: {self.stranger.exists()}, '
                        f'rows: {len(rows(page.outside_list))}, toasts: {self.app.toasts}')
        self.assertEqual(self.app.toasts, ['Removed 1 book from Kobo Clara 2E'])
        self.assertFalse(page.select_button.get_active())

    def test_device_unplugged(self):
        from bookcase.pages.device import DevicePage

        page = DevicePage(self.device.id)
        self.show(page)
        self.app.devices.remove_test_root(str(self.root))
        self.assertEqual(page.stack.get_visible_child_name(), 'gone')
        self.assertEqual(self.window.shown[-1:], ['home'])

    def test_empty_device(self):
        from bookcase.pages.device import DevicePage

        os.remove(self.known)
        os.remove(self.stranger)
        page = DevicePage(self.device.id)
        self.show(page)
        self.assertTrue(page.empty_status.get_visible())
        self.assertFalse(page.outside_group.get_visible())
        self.assertFalse(page.select_button.get_sensitive())

    def test_send_dialog_plans_and_sends(self):
        from bookcase.dialogs import send

        dialog = send.present(self.app, self.window, [1, 2])
        self.addCleanup(dialog.force_close)
        self.assertEqual(dialog.stack.get_visible_child_name(), 'form')
        self.assertIs(dialog.device, self.device)
        self.assertTrue(dialog.kepub_row.get_visible())
        subtitles = [row.get_subtitle() for row in rows(dialog.books_list)]
        self.assertEqual(subtitles[0], 'EPUB → Kobo EPUB')
        self.assertEqual(subtitles[1], 'No format this reader can open')
        self.assertEqual(dialog.send_button.get_label(), '_Send 1')

        sent = []

        def fake_send(library, covers, book_id, kepub=True, progress=None,
                      cancellable=None):
            progress(0.5)
            sent.append((book_id, kepub))
            return '/device/path'

        self.device.send = fake_send
        dialog.send()
        self.assertTrue(wait_for(lambda: self.app.toasts))
        self.assertEqual(sent, [(1, dialog.kepub_row.get_active())])
        self.assertEqual(self.app.toasts, ['Sent “A Quiet Harbour” to Kobo Clara 2E'])

    def test_send_dialog_without_a_device(self):
        from bookcase.dialogs import send

        self.app.devices.remove_test_root(str(self.root))
        dialog = send.present(self.app, self.window, [1])
        self.addCleanup(dialog.force_close)
        self.assertEqual(dialog.stack.get_visible_child_name(), 'none')
        self.assertFalse(dialog.send_button.get_sensitive())
        self.app.devices.add_test_root(str(self.root))
        self.assertEqual(dialog.stack.get_visible_child_name(), 'form')

    def test_result_text(self):
        from bookcase.dialogs.send import result_text

        self.assertEqual(result_text('Kobo', 3, ['a', 'b', 'c'], [], False),
                         'Sent 3 books to Kobo')
        self.assertEqual(result_text('Kobo', 2, ['a'], [('b', 'No space')], False),
                         'Sent 1 of 2 books to Kobo. “b”: No space')
        self.assertEqual(result_text('Kobo', 2, ['a'], [], True),
                         'Stopped after sending 1 book')


if __name__ == '__main__':
    unittest.main()
