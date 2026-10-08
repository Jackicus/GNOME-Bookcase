# SPDX-License-Identifier: GPL-2.0-or-later
# SPDX-FileCopyrightText: 2026 Jack Tully

"""Find Metadata for many books (bulk_metadata.py and dialogs/bulk_metadata.py) on canned
answers: classification, the changes, the queue, apply as one undo step. No network."""

from tests import ROOT  # noqa: F401  (registers bookcase)

import unittest

from bookcase import bulk_metadata
from tests.dialog_support import fake_app
from tests.gtk import requires_gtk, wait_for
from tests.support import add_book, snapshot, temporary_library
from tests.test_dialogs_fetch import PNG
from tests.test_online import ISBN, OL_BOOKS, OL_EDITION, OL_SEARCH, OL_WORK, Server


def server():
    return Server({'/search.json': OL_SEARCH, '/api/books': OL_BOOKS,
                   '/books/OL1000001M.json': OL_EDITION, '/works/OL1W.json': OL_WORK,
                   '/b/id/112-L.jpg': PNG, '/b/id/112-M.jpg': PNG,
                   '/1-L.jpg': PNG})


ORCHARD_SEARCH = {'docs': [
    {'key': '/works/OL5W', 'title': 'The Orchard Murders', 'author_name': ['Tobias Wren'],
     'first_publish_year': 2014, 'publisher': ['Lantern House'],
     'number_of_pages_median': 304},
    {'key': '/works/OL6W', 'title': 'The Orchard Murders: A Novel',
     'author_name': ['Tobias Wren'], 'first_publish_year': 2019,
     'publisher': ['Tidewater Press'], 'number_of_pages_median': 288},
    {'key': '/works/OL7W', 'title': 'Orchard Murders', 'author_name': ['T. Wren'],
     'first_publish_year': 2021},
    {'key': '/works/OL8W', 'title': 'A Field Guide to Moths', 'author_name': ['Di Fen']},
]}


def orchard_server():
    return Server({'/search.json': ORCHARD_SEARCH})


class FakeCovers:
    def __init__(self, library):
        self.library = library
        self.saved = []

    def save(self, book_id, data):
        self.saved.append(book_id)
        with self.library.undoable('Set Cover'):
            self.library.update_book(book_id, has_cover=True)


class BulkMetadataTest(unittest.TestCase):
    def test_classify_and_changes(self):
        with temporary_library() as library:
            harbour = add_book(library, 'A Quiet Harbour', ('Ada Lark',), publisher='Mine')
            nothing = add_book(library, 'Zzyzx Qwerty', ('Nobody',))
            close = add_book(library, 'A Quiet Harbor Tale', ('Someone Else',))
            fetch = server()
            lookups = [bulk_metadata.run_lookup(bulk_metadata.Lookup.of(library.book(id_)),
                                                fetch=fetch)
                       for id_ in (harbour, nothing, close)]
            self.assertEqual([lookup.status for lookup in lookups],
                             ['found', 'not-found', 'ambiguous'])
            self.assertEqual(lookups[0].cover, PNG)
            self.assertIn('The tide comes in.', lookups[0].candidate.description)
            found = bulk_metadata.changes(library.book(harbour), lookups[0])
            self.assertEqual(sorted(found), ['cover', 'description', 'details', 'tags'])
            fields = [change.field for change in found['details']]
            self.assertNotIn('publisher', fields)  # filled already: kept
            self.assertIn('series', fields)
            replaced = bulk_metadata.changes(library.book(harbour), lookups[0], replace=True)
            self.assertIn('publisher', [change.field for change in replaced['details']])

    def test_isbn_first(self):
        with temporary_library() as library:
            book_id = add_book(library, 'Some Other Title', ('X',),
                               identifiers={'isbn': ISBN})
            fetch = server()
            lookup = bulk_metadata.run_lookup(bulk_metadata.Lookup.of(library.book(book_id)),
                                              fetch=fetch)
            self.assertEqual(lookup.status, 'found')
            self.assertIn('/api/books', fetch.urls[0])

    def test_offline_is_an_error(self):
        with temporary_library() as library:
            book_id = add_book(library, 'A Quiet Harbour')
            fetch = Server({}, fail=('openlibrary.org',))
            lookup = bulk_metadata.run_lookup(bulk_metadata.Lookup.of(library.book(book_id)),
                                              fetch=fetch)
            self.assertEqual(lookup.status, 'error')
            self.assertTrue(lookup.error)

    def test_apply_is_one_undo_step(self):
        with temporary_library() as library:
            one = add_book(library, 'A Quiet Harbour', ('Ada Lark',), tags=['Old'])
            two = add_book(library, 'A Quiet Harbour', ('Ada Lark',))
            fetch = server()
            lookups = [bulk_metadata.run_lookup(bulk_metadata.Lookup.of(library.book(id_)),
                                                fetch=fetch) for id_ in (one, two)]
            covers = FakeCovers(library)
            library.clear_undo()
            selection = {one: {'details', 'description', 'cover', 'tags'},
                         two: {'details'}}
            changed = bulk_metadata.apply(library, covers, lookups, selection)
            self.assertEqual(changed, 2)
            first, second = library.book(one), library.book(two)
            self.assertEqual(first.publisher, 'Tidewater Press')
            self.assertEqual(first.series, 'The Harbour Books')
            self.assertEqual(first.identifiers.get('isbn'), ISBN)
            self.assertTrue(first.has_cover)
            self.assertEqual(first.tags, ('Old',))  # not empty: tags only with replace
            self.assertIn('tide', first.description)
            self.assertEqual(second.description, '')
            self.assertFalse(second.has_cover)
            self.assertEqual(library.undo(), 'Find Metadata')
            self.assertEqual(library.book(one).publisher, '')
            self.assertFalse(library.book(one).has_cover)
            self.assertEqual(library.book(two).series, '')
            self.assertFalse(library.can_undo())

    def test_apply_and_undo_put_back_every_row(self):
        from bookcase.covers import CoverStore

        with temporary_library() as library:
            covers = CoverStore(library.path.parent, library)
            one = add_book(library, 'A Quiet Harbour', ('Ada Lark',), tags=['Old'],
                           publisher='Mine', identifiers={'isbn': '9780000000002'})
            two = add_book(library, 'A Quiet Harbour', ('Ada Lark',))
            covers.save(one, PNG)
            fetch = server()
            lookups = [bulk_metadata.run_lookup(bulk_metadata.Lookup.of(library.book(id_)),
                                                fetch=fetch) for id_ in (one, two)]
            selection = {one: set(bulk_metadata.GROUPS), two: set(bulk_metadata.GROUPS)}
            before = snapshot(library)
            cover_before = covers.data(one)
            self.assertEqual(bulk_metadata.apply(library, covers, lookups, selection,
                                                 replace=True), 2)
            self.assertNotEqual(snapshot(library), before)
            self.assertEqual(library.undo(), 'Find Metadata')
            self.assertEqual(snapshot(library), before)
            self.assertEqual(covers.data(one), cover_before)
            self.assertIsNone(covers.data(two))

    def test_choices_and_pages(self):
        with temporary_library() as library:
            book_id = add_book(library, 'The Orchard Murders', ('Tobias Wren',))
            lookup = bulk_metadata.run_lookup(bulk_metadata.Lookup.of(library.book(book_id)),
                                              fetch=orchard_server())
            self.assertEqual(lookup.status, 'found')
            # Three plausible books; the moths are not one of them.
            self.assertEqual([choice.key for choice in lookup.choices],
                             ['/works/OL5W', '/works/OL6W', '/works/OL7W'])
            self.assertEqual(lookup.chosen, 0)
            found = bulk_metadata.changes(library.book(book_id), lookup)
            details = {change.field: change.new for change in found['details']}
            self.assertEqual(details['pages'], '304')
            self.assertEqual(details['publisher'], 'Lantern House')
            bulk_metadata.choose(lookup, 1, fetch=orchard_server())
            self.assertEqual(lookup.chosen, 1)
            self.assertEqual(lookup.candidate.publisher, 'Tidewater Press')
            bulk_metadata.apply(library, FakeCovers(library), [lookup], {book_id: {'details'}})
            book = library.book(book_id)
            self.assertEqual((book.publisher, book.pages), ('Tidewater Press', 288))
            with self.assertRaises(IndexError):
                bulk_metadata.choose(lookup, 5)

    def test_queue_runs_and_cancels(self):
        with temporary_library() as library:
            ids = [add_book(library, 'A Quiet Harbour') for _n in range(3)]
            lookups = [bulk_metadata.Lookup.of(library.book(id_)) for id_ in ids]
            updates, done = [], []
            queue = bulk_metadata.Queue(lookups, updates.append, done.append, fetch=server())
            queue.start()
            self.assertTrue(wait_for(lambda: done, 5))
            self.assertEqual(done, [False])
            self.assertEqual([lookup.status for lookup in lookups], ['found'] * 3)
            self.assertEqual(len(updates), 6)
            lookups = [bulk_metadata.Lookup.of(library.book(id_)) for id_ in ids]
            done = []
            queue = bulk_metadata.Queue(lookups, lambda _l: None, done.append, fetch=server())
            queue.cancel()
            queue.start()
            self.assertTrue(wait_for(lambda: done, 5))
            self.assertEqual(done, [True])
            self.assertEqual({lookup.status for lookup in lookups}, {'waiting'})


@requires_gtk
class BulkDialogTest(unittest.TestCase):
    def test_find_review_apply(self):
        from bookcase.dialogs import bulk_metadata as dialog_module

        with fake_app() as app:
            one = add_book(app.library, 'A Quiet Harbour', ('Ada Lark',))
            two = add_book(app.library, 'Zzyzx Qwerty', ('Nobody',))
            dialog = dialog_module.BulkMetadataDialog(app, [one, two], fetch=server())
            dialog.start()
            self.assertTrue(wait_for(lambda: dialog.finished, 5))
            self.assertEqual(dialog.status_label(dialog.lookups[0]), 'Found')
            dialog.review()
            self.assertEqual(dialog.selection(), {one: {'details', 'description', 'cover',
                                                        'tags'}})
            dialog.set_group(one, 'cover', False)
            self.assertEqual(dialog.selection()[one], {'details', 'description', 'tags'})
            dialog.apply()
            book = app.library.book(one)
            self.assertEqual(book.publisher, 'Tidewater Press')
            self.assertFalse(book.has_cover)
            self.assertEqual(app.toasts[-1], ('Found metadata for “A Quiet Harbour”', True))
            self.assertEqual(app.library.undo_label, 'Find Metadata')


    def test_choosing_another_match(self):
        from gi.repository import Adw

        from bookcase.dialogs import bulk_metadata as dialog_module

        with fake_app() as app:
            book_id = add_book(app.library, 'The Orchard Murders', ('Tobias Wren',))
            dialog = dialog_module.BulkMetadataDialog(app, [book_id], fetch=orchard_server())
            dialog.start()
            self.assertTrue(wait_for(lambda: dialog.finished, 5))
            dialog.review()
            row = dialog._review_rows[0]
            combos = [child for child in _descendants(row) if isinstance(child, Adw.ComboRow)]
            self.assertEqual(len(combos), 1)
            self.assertEqual(combos[0].get_model().get_n_items(), 3)
            self.assertEqual(dialog_module.choice_text(dialog.lookups[0].choices[1]),
                             'The Orchard Murders: A Novel — Tobias Wren, 2019')
            row.set_expanded(True)
            combos[0].set_selected(1)
            self.assertTrue(wait_for(lambda: not dialog._choosing, 5))
            self.assertEqual(dialog.lookups[0].chosen, 1)
            self.assertTrue(dialog._review_rows[0].get_expanded())  # kept open
            dialog.apply()
            self.assertEqual(app.library.book(book_id).publisher, 'Tidewater Press')
            self.assertEqual(app.library.book(book_id).pages, 288)


def _descendants(widget):
    child = widget.get_first_child()
    while child is not None:
        yield child
        yield from _descendants(child)
        child = child.get_next_sibling()


if __name__ == '__main__':
    unittest.main()
