# SPDX-License-Identifier: GPL-2.0-or-later
# SPDX-FileCopyrightText: 2026 Jack Tully

"""Edit Metadata (dialogs/edit_metadata.py): its pure helpers, and the dialog on a temporary
library: one book and many, saving through the library as one undo step."""

import unittest

from tests import ROOT  # noqa: F401  (registers bookcase)
from tests.dialog_support import fake_app
from tests.gtk import requires_gtk
from tests.support import add_book, make_png

ISBN = '9780000000002'


class TestParsing(unittest.TestCase):
    @requires_gtk
    def test_helpers(self):
        from bookcase.dialogs import edit_metadata as em

        self.assertEqual(em.parse_authors('Ada Lark & Ben Ross; Cy Moss, Ada Lark'),
                         ['Ada Lark', 'Ben Ross', 'Cy Moss'])
        self.assertEqual(em.format_authors(['Ada Lark', 'Ben Ross']), 'Ada Lark & Ben Ross')
        self.assertEqual(em.parse_tags(' Sea,  fiction , ,Sea'), ['Sea', 'fiction'])
        self.assertEqual(em.valid_date('2004'), '2004')
        self.assertEqual(em.valid_date('2004-5'), '2004-05')
        self.assertEqual(em.valid_date('2004-02-30'), None)
        self.assertEqual(em.valid_date('May 2004'), None)
        self.assertEqual(em.valid_date(' '), '')
        self.assertEqual(em.valid_isbn('978-0-00-000000-2'), ISBN)
        self.assertEqual(em.valid_isbn('12345'), None)


@requires_gtk
class TestSingle(unittest.TestCase):
    def test_builds_with_the_book(self):
        from bookcase.dialogs.edit_metadata import EditMetadataDialog

        with fake_app() as app:
            book_id = add_book(app.library, 'A Quiet Harbour', ('Ada Lark', 'Ben Ross'),
                               series='Harbour Books', series_index=2, tags=['Sea'],
                               publisher='Tidewater Press', published='2011-05',
                               language='en', description='<p>One.</p><p>Two &amp; three.</p>',
                               identifiers={'isbn': ISBN, 'google': 'gVol1'})
            dialog = EditMetadataDialog(app, [book_id])
            self.assertTrue(dialog.single)
            self.assertTrue(dialog.top_box.get_visible())
            self.assertTrue(dialog.details_group.get_visible())
            self.assertFalse(dialog.bulk_group.get_visible())
            self.assertEqual(dialog.title_row.get_text(), 'A Quiet Harbour')
            self.assertEqual(dialog.authors_row.get_text(), 'Ada Lark & Ben Ross')
            self.assertEqual(dialog.series_index_row.get_value(), 2)
            self.assertEqual(dialog.isbn_row.get_text(), ISBN)
            self.assertEqual(dialog.values()['language'], 'en')
            buffer = dialog.description_view.get_buffer()
            text = buffer.get_text(buffer.get_start_iter(), buffer.get_end_iter(), False)
            self.assertEqual(text, 'One.\n\nTwo & three.')
            self.assertEqual(dialog.changed_fields(), {})  # nothing edited, nothing to save
            self.assertFalse(dialog.save())

    def test_save_writes_one_undo_step(self):
        from bookcase.dialogs.edit_metadata import EditMetadataDialog

        with fake_app() as app:
            library = app.library
            book_id = add_book(library, 'A Quiet Harbour', description='<p>Kept.</p>')
            dialog = EditMetadataDialog(app, [book_id])
            dialog.title_row.set_text('The Quiet Harbour')
            dialog.authors_row.set_text('Ada Lark & Ben Ross')
            dialog.series_row.set_text('Harbour Books')
            dialog.series_index_row.set_value(3)
            dialog.add_tag('Sea, Fiction')
            dialog.published_row.set_text('2011')
            dialog.isbn_row.set_text(ISBN)
            dialog.stars.set_value(8)
            dialog.pages_row.set_value(312)
            fields = dialog.changed_fields()
            self.assertNotIn('description', fields)  # untouched: its HTML is kept
            self.assertEqual(fields['sort_title'], 'Quiet Harbour, The')  # follows the title
            self.assertTrue(dialog.save())
            book = library.book(book_id)
            self.assertEqual(book.title, 'The Quiet Harbour')
            self.assertEqual(book.authors, ('Ada Lark', 'Ben Ross'))
            self.assertEqual((book.series, book.series_index), ('Harbour Books', 3.0))
            self.assertEqual(set(book.tags), {'Sea', 'Fiction'})
            self.assertEqual(book.published, '2011')
            self.assertEqual(book.identifiers.get('isbn'), ISBN)
            self.assertEqual(book.rating, 8)
            self.assertEqual(book.pages, 312)
            self.assertEqual(book.description, '<p>Kept.</p>')
            self.assertEqual(app.toasts, [('Saved “The Quiet Harbour”', True)])
            library.undo()
            self.assertEqual(library.book(book_id).title, 'A Quiet Harbour')
            self.assertEqual(library.book(book_id).tags, ())

    def test_description_edited_becomes_paragraphs(self):
        from bookcase.dialogs.edit_metadata import EditMetadataDialog

        with fake_app() as app:
            book_id = add_book(app.library, 'A Quiet Harbour')
            dialog = EditMetadataDialog(app, [book_id])
            dialog.description_view.get_buffer().set_text('First <b>.\n\nSecond')
            dialog.save()
            self.assertEqual(app.library.book(book_id).description,
                             '<p>First &lt;b&gt;.</p><p>Second</p>')

    def test_invalid_fields_block_saving(self):
        from bookcase.dialogs.edit_metadata import EditMetadataDialog

        with fake_app() as app:
            book_id = add_book(app.library, 'A Quiet Harbour')
            dialog = EditMetadataDialog(app, [book_id])
            dialog.published_row.set_text('someday')
            self.assertFalse(dialog.save_button.get_sensitive())
            self.assertIn('error', dialog.published_row.get_css_classes())
            self.assertFalse(dialog.save())
            dialog.published_row.set_text('2011-04-01')
            self.assertTrue(dialog.save_button.get_sensitive())

    def test_cover_saved_and_removed(self):
        from bookcase.dialogs.edit_metadata import EditMetadataDialog

        with fake_app() as app:
            book_id = add_book(app.library, 'A Quiet Harbour')
            dialog = EditMetadataDialog(app, [book_id])
            self.assertTrue(dialog.set_cover(make_png(4, 6)))
            self.assertFalse(dialog.set_cover(b'not an image'))
            self.assertTrue(dialog.save())
            self.assertTrue(app.library.book(book_id).has_cover)
            dialog = EditMetadataDialog(app, [book_id])
            dialog.set_cover(None)
            self.assertTrue(dialog.save())
            self.assertFalse(app.library.book(book_id).has_cover)

    def test_apply_values_from_fetch(self):
        from bookcase.dialogs.edit_metadata import EditMetadataDialog

        with fake_app() as app:
            book_id = add_book(app.library, 'Harbour')
            dialog = EditMetadataDialog(app, [book_id])
            dialog.apply_values({'title': 'A Quiet Harbour', 'publisher': 'Tidewater Press',
                                 'language': 'xx', 'description': '<p>Found.</p>',
                                 'identifiers': {'openlibrary': 'OL1M'}, 'tags': ['Sea'],
                                 'pages': 288})
            values = dialog.values()
            self.assertEqual(values['pages'], 288)
            self.assertEqual(dialog.changed_fields()['pages'], 288)
            self.assertEqual(values['title'], 'A Quiet Harbour')
            self.assertEqual(values['language'], 'xx')  # an unlisted code is added
            self.assertEqual(values['tags'], ['Sea'])
            self.assertEqual(dialog.changed_fields()['identifiers'], {'openlibrary': 'OL1M'})
            self.assertEqual(app.library.book(book_id).title, 'Harbour')  # not saved yet

    def test_steps_through_siblings(self):
        from bookcase.dialogs.edit_metadata import EditMetadataDialog

        with fake_app() as app:
            ids = [add_book(app.library, title) for title in ('One', 'Two', 'Three')]
            dialog = EditMetadataDialog(app, [ids[0]], siblings=ids)
            self.assertTrue(dialog.step_box.get_visible())
            self.assertFalse(dialog._actions['previous'].get_enabled())
            dialog.publisher_row.set_text('Tidewater Press')
            self.assertTrue(dialog.step(1))
            self.assertEqual(dialog.title_row.get_text(), 'Two')
            self.assertEqual(app.library.book(ids[0]).publisher, 'Tidewater Press')
            self.assertTrue(dialog.step(1))
            self.assertFalse(dialog._actions['next'].get_enabled())
            self.assertFalse(dialog.step(1))


@requires_gtk
class TestBulk(unittest.TestCase):
    def test_only_bulk_fields(self):
        from bookcase.dialogs.edit_metadata import EditMetadataDialog

        with fake_app() as app:
            ids = [add_book(app.library, title) for title in ('One', 'Two')]
            dialog = EditMetadataDialog(app, ids)
            self.assertFalse(dialog.single)
            self.assertFalse(dialog.top_box.get_visible())
            self.assertFalse(dialog.details_group.get_visible())
            self.assertTrue(dialog.bulk_group.get_visible())
            self.assertEqual(dialog.bulk_fields(), {})  # all left unchanged
            self.assertFalse(dialog.save())
            self.assertEqual(dialog.get_title(), 'Edit 2 Books')

    def test_save(self):
        from bookcase.dialogs.edit_metadata import EditMetadataDialog

        with fake_app() as app:
            library = app.library
            ids = [add_book(library, title, tags=['Old', 'Keep'])
                   for title in ('One', 'Two', 'Three')]
            dialog = EditMetadataDialog(app, ids)
            dialog.bulk_series_row.set_text('Harbour Books')
            self.assertTrue(dialog.bulk_number_row.get_sensitive())
            dialog.bulk_number_row.set_active(True)
            dialog.bulk_start_row.set_value(4)
            dialog.bulk_rating_row.set_selected(5)  # 4 stars
            dialog.bulk_status_row.set_selected(3)  # finished
            dialog.bulk_add_tags_row.set_text('Sea')
            dialog.bulk_remove_tags_row.set_text('Old')
            self.assertTrue(dialog.save())
            books = [library.book(book_id) for book_id in ids]
            self.assertEqual([book.series_index for book in books], [4.0, 5.0, 6.0])
            self.assertTrue(all(book.series == 'Harbour Books' for book in books))
            self.assertTrue(all(book.rating == 8 for book in books))
            self.assertTrue(all(book.status == 'finished' for book in books))
            self.assertTrue(all(set(book.tags) == {'Keep', 'Sea'} for book in books))
            self.assertEqual(books[0].authors, ('Ada Lark',))  # left unchanged
            self.assertEqual(app.toasts, [('Saved 3 books', True)])
            library.undo()  # one step
            self.assertTrue(all(library.book(book_id).series == '' for book_id in ids))
            self.assertTrue(all(library.book(book_id).status == 'unread' for book_id in ids))


if __name__ == '__main__':
    unittest.main()
