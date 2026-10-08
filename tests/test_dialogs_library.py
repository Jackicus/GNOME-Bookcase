# SPDX-License-Identifier: GPL-2.0-or-later
# SPDX-FileCopyrightText: 2026 Jack Tully

"""The shelf, add-books and preferences dialogs on a temporary library."""

import os
import tempfile
import unittest

from tests import ROOT  # noqa: F401  (registers bookcase)
from tests.dialog_support import fake_app
from tests.gtk import requires_gtk, wait_for
from tests.support import add_book, make_epub


@requires_gtk
class TestShelf(unittest.TestCase):
    def test_new_shelf_with_books(self):
        from bookcase.dialogs.shelf import ShelfDialog

        with fake_app() as app:
            ids = [add_book(app.library, title) for title in ('One', 'Two')]
            dialog = ShelfDialog(app, book_ids=ids)
            self.assertFalse(dialog.apply_button.get_sensitive())  # no name yet
            self.assertFalse(dialog.query_group.get_visible())
            dialog.name_row.set_text('Holiday')
            self.assertTrue(dialog.apply())
            [shelf] = app.library.shelves()
            self.assertEqual((shelf.name, shelf.query, shelf.count), ('Holiday', None, 2))
            self.assertEqual(app.toasts, [('Added 2 books to “Holiday”', True)])
            app.library.undo()  # one step: the shelf and its books
            self.assertEqual(app.library.shelves(), [])

    def test_smart_shelf_counts_and_names(self):
        from bookcase.dialogs.shelf import EXAMPLES, ShelfDialog

        with fake_app() as app:
            add_book(app.library, 'One', tags=['Fantasy'])
            add_book(app.library, 'Two')
            app.library.add_shelf('Taken')
            dialog = ShelfDialog(app, smart=True)
            self.assertTrue(dialog.query_group.get_visible())
            dialog.name_row.set_text('taken')
            self.assertIn('already exists', dialog.error_label.get_text())
            self.assertFalse(dialog.apply())
            dialog.name_row.set_text('Fantasy')
            self.assertFalse(dialog.apply_button.get_sensitive())  # no search yet
            dialog.query_row.set_text('tag:fantasy')
            self.assertEqual(dialog.update_count(), 1)
            self.assertEqual(dialog.count_label.get_text(), '1 book')
            dialog.example_chips.get_first_child().emit('clicked')
            self.assertEqual(dialog.query_row.get_text(), f'tag:fantasy {EXAMPLES[0]}')
            self.assertTrue(dialog.apply())
            dialog._on_closed(dialog)  # never presented: drop the pending count
            shelf = next(s for s in app.library.shelves() if s.name == 'Fantasy')
            self.assertEqual(shelf.query, f'tag:fantasy {EXAMPLES[0]}')

    def test_edit(self):
        from bookcase.dialogs.shelf import ShelfDialog

        with fake_app() as app:
            shelf_id = app.library.add_shelf('Recent', query='added:<30d')
            dialog = ShelfDialog(app, shelf=app.library.shelf(shelf_id))
            self.assertTrue(dialog.smart)
            self.assertEqual(dialog.query_row.get_text(), 'added:<30d')
            dialog.name_row.set_text('Recent')  # its own name is not "taken"
            self.assertEqual(dialog.problem(), '')
            dialog.query_row.set_text('added:<7d')
            self.assertTrue(dialog.apply())
            dialog._on_closed(dialog)
            self.assertEqual(app.library.shelf(shelf_id).query, 'added:<7d')


class FakeJob:
    def __init__(self):
        self._cancelled = False

    def cancel(self):
        self._cancelled = True

    def cancelled(self):
        return self._cancelled


class FakeImporter:
    """Records the call; the test drives progress and done itself."""

    def __init__(self):
        self.calls = []

    def add_async(self, paths, copy=True, progress=None, done=None):
        self.calls.append(('add', paths, copy))
        self.progress, self.done = progress, done
        self.job = FakeJob()
        return self.job

    def scan_async(self, folder, progress=None, done=None):
        self.calls.append(('scan', folder))
        self.progress, self.done = progress, done
        self.job = FakeJob()
        return self.job


@requires_gtk
class TestAddBooks(unittest.TestCase):
    def test_summary(self):
        from bookcase.dialogs.add_books import AddBooksDialog
        from bookcase.importing import ImportReport

        with fake_app() as app:
            app.importer = FakeImporter()
            first = add_book(app.library, 'One')
            dialog = AddBooksDialog(app, None, 'add')
            dialog.presented = True  # no window to present over in a test
            dialog.start('add_async', ['/invented/a.epub', '/invented/b.epub'], copy=True)
            self.assertEqual(app.importer.calls[0][0], 'add')
            dialog.on_progress(1, 3, '/invented/a.epub')
            self.assertEqual(dialog.progress_label.get_text(), '1 of 3')
            self.assertEqual(dialog.file_label.get_text(), 'a.epub')
            dialog.cancel_button.emit('clicked')
            self.assertTrue(app.importer.job.cancelled())
            report = ImportReport(added=[first], duplicates=[('/invented/b.epub', first)],
                                  failed=[('/invented/c.pdf', 'Not a PDF')])
            dialog.on_done(report)
            self.assertEqual(dialog.stack.get_visible_child_name(), 'done')
            self.assertEqual(dialog.summary_title.get_text(), '1 Book Added')
            rows = []
            row = dialog.summary_list.get_first_child()
            while row is not None:
                rows.append(row.get_title() if hasattr(row, 'get_title') else '')
                row = row.get_next_sibling()
            self.assertEqual(rows, ['1 book added', '1 already in the library',
                                    '1 file could not be added'])

    def test_one_book_just_toasts(self):
        from bookcase.dialogs.add_books import AddBooksDialog
        from bookcase.importing import ImportReport

        with fake_app() as app:
            app.importer = FakeImporter()
            book_id = add_book(app.library, 'A Quiet Harbour')
            dialog = AddBooksDialog(app, None, 'add')
            dialog.start('add_async', ['/invented/a.epub'])
            dialog.on_done(ImportReport(added=[book_id]))
            self.assertFalse(dialog.presented)
            self.assertEqual(app.toasts, [('Added “A Quiet Harbour”', False)])

    def test_real_import(self):
        from bookcase.dialogs import add_books
        from bookcase.importing import Importer

        with fake_app() as app, tempfile.TemporaryDirectory() as folder:
            app.importer = Importer(app.library, app.covers, os.path.join(folder, 'Books'))
            path = make_epub(os.path.join(folder, 'harbour.epub'), title='A Quiet Harbour')
            dialog = add_books.present(app, None, [path])
            self.assertTrue(wait_for(lambda: dialog.finished, timeout=3))
            self.assertEqual(app.library.count(), 1)
            self.assertEqual(app.toasts, [('Added “A Quiet Harbour”', False)])


@requires_gtk
class TestPreferences(unittest.TestCase):
    def test_bindings_and_folders(self):
        from bookcase.dialogs.preferences import PreferencesDialog, library_folder

        with fake_app() as app, tempfile.TemporaryDirectory() as folder:
            settings = app.settings
            app.library.add_folder(folder, 'watched')
            dialog = PreferencesDialog(app)
            self.assertIsNotNone(dialog.watched_list.get_row_at_index(0))
            self.assertIsNone(dialog.calibre_list.get_row_at_index(0))
            dialog.font_size_row.set_value(22)
            self.assertEqual(settings.get_int('reader-font-size'), 22)
            settings.set_double('reader-line-height', 1.8)
            self.assertAlmostEqual(dialog.line_height_row.get_value(), 1.8)
            dialog.theme_row.set_selected(2)
            self.assertEqual(settings.get_string('reader-theme'), 'sepia')
            settings.set_string('reader-font', 'custom')
            self.assertTrue(dialog.custom_font_row.get_visible())
            dialog.kepub_row.set_active(False)
            self.assertFalse(settings.get_boolean('send-kepub'))
            dialog.google_key_row.set_text('k')
            self.assertEqual(settings.get_string('google-books-key'), 'k')
            dialog.set_library_folder(os.path.join(folder, 'Shelf'))
            self.assertEqual(library_folder(settings), os.path.join(folder, 'Shelf'))
            dialog._on_closed(dialog)  # never presented: let the bindings go
            for key in ('reader-font-size', 'reader-line-height', 'reader-theme', 'reader-font',
                        'send-kepub', 'google-books-key', 'library-folder'):
                settings.reset(key)


if __name__ == '__main__':
    unittest.main()
