# SPDX-License-Identifier: GPL-2.0-or-later
# SPDX-FileCopyrightText: 2026 Jack Tully

"""Find Metadata (dialogs/fetch_metadata.py) on canned answers: the comparison, Apply handing
the chosen fields back, Find Cover, and the offline state. No network."""

import unittest

from tests import ROOT  # noqa: F401  (registers bookcase)
from tests.dialog_support import fake_app
from tests.gtk import requires_gtk, wait_for
from tests.support import make_png
from tests.test_online import ISBN, OL_EDITION, OL_SEARCH, OL_WORK, Server

from bookcase import online

PNG = make_png(40, 60) + bytes(300)  # past fetch_cover's placeholder size


def server(**kwargs):
    return Server({'/search.json': OL_SEARCH, '/api/books': {},
                   '/books/OL1000001M.json': OL_EDITION, '/works/OL1W.json': OL_WORK,
                   '/b/id/112-L.jpg': PNG, '/b/id/112-M.jpg': PNG, '/b/id/222-M.jpg': PNG,
                   '/b/id/222-L.jpg': PNG}, **kwargs)


CURRENT = {'title': 'Harbour', 'authors': ['Ada Lark'], 'series': '', 'series_index': 0,
           'publisher': '', 'published': '', 'language': 'en', 'description': '',
           'isbn': '', 'tags': ['Sea'], 'cover': None}


class TestComparison(unittest.TestCase):
    @requires_gtk
    def test_fields(self):
        from bookcase.dialogs.fetch_metadata import chosen_values, comparison

        candidate = online.Candidate(
            source='openlibrary', title='A Quiet Harbour', authors=('Ada Lark',),
            series='Harbour Books', series_index=2, publisher='Tidewater Press',
            published='2011', language='en', tags=('Sea', 'Tides'),
            identifiers={'isbn': ISBN, 'openlibrary': 'OL1M'},
            cover_url='https://covers.example/1-L.jpg')
        fields = {field.key: field for field in comparison(CURRENT, candidate)}
        self.assertNotIn('authors', fields)  # the same
        self.assertNotIn('language', fields)
        self.assertNotIn('description', fields)  # none found
        self.assertFalse(fields['title'].checked)  # the book has one already
        self.assertTrue(fields['publisher'].checked)
        self.assertEqual(fields['tags'].found, 'Tides')  # only the new ones
        self.assertFalse(fields['tags'].checked)
        self.assertEqual(fields['series'].found, 'Harbour Books #2')
        fields['title'].checked = True
        fields['publisher'].checked = False
        values = chosen_values(fields.values(), candidate, cover=PNG)
        self.assertEqual(values['title'], 'A Quiet Harbour')
        self.assertNotIn('publisher', values)
        self.assertEqual((values['series'], values['series_index']), ('Harbour Books', 2))
        self.assertEqual(values['isbn'], ISBN)
        self.assertEqual(values['cover'], PNG)
        self.assertEqual(values['identifiers'], {'openlibrary': 'OL1M'})


@requires_gtk
class TestDialog(unittest.TestCase):
    def test_search_compare_apply(self):
        from bookcase.dialogs.fetch_metadata import FetchMetadataDialog

        applied = []
        with fake_app() as app:
            dialog = FetchMetadataDialog(app, CURRENT, applied.append, fetch=server())
            dialog.search()
            self.assertTrue(wait_for(lambda: dialog.stack.get_visible_child_name()
                                     == 'results', timeout=2))
            self.assertEqual(dialog.candidates[0].title, 'A Quiet Harbour')  # ranked first
            row = dialog.candidate_list.get_row_at_index(0)
            dialog.candidate_list.emit('row-activated', row)
            self.assertTrue(wait_for(lambda: dialog.candidate is not None, timeout=2))
            keys = [field.key for field in dialog.fields]
            self.assertIn('description', keys)  # from the work, fetched on choosing
            self.assertIn('cover', keys)
            dialog.apply()
            self.assertTrue(wait_for(lambda: applied, timeout=2))
            values = applied[0]
            self.assertEqual(values['publisher'], 'Tidewater Press')
            self.assertEqual(values['cover'], PNG)
            self.assertEqual(values['series'], 'The Harbour Books')
            self.assertNotIn('title', values)  # unchecked by default

    def test_offline(self):
        from bookcase.dialogs.fetch_metadata import FetchMetadataDialog

        with fake_app() as app:
            dialog = FetchMetadataDialog(app, CURRENT, lambda values: None,
                                         fetch=server(fail=('openlibrary.org',)))
            dialog.search()
            self.assertTrue(wait_for(lambda: dialog.stack.get_visible_child_name() == 'error',
                                     timeout=2))
            self.assertEqual(dialog.error_status.get_title(), 'No Connection')

    def test_no_matches(self):
        from bookcase.dialogs.fetch_metadata import FetchMetadataDialog

        with fake_app() as app:
            empty = Server({'/search.json': {'docs': []}, '/api/books': {}})
            dialog = FetchMetadataDialog(app, CURRENT, lambda values: None, fetch=empty)
            dialog.search()
            self.assertTrue(wait_for(lambda: dialog.stack.get_visible_child_name() == 'empty',
                                     timeout=2))

    def test_find_cover(self):
        from bookcase.dialogs.fetch_metadata import FetchMetadataDialog

        chosen = []
        with fake_app() as app:
            dialog = FetchMetadataDialog(app, CURRENT, chosen.append, covers=True,
                                         fetch=server())
            dialog.search()
            self.assertTrue(wait_for(lambda: dialog.stack.get_visible_child_name()
                                     == 'results', timeout=2))
            self.assertFalse(dialog.candidate_list.get_visible())
            tile = dialog.cover_grid.get_child_at_index(0).get_child()
            tile.emit('clicked')
            self.assertTrue(wait_for(lambda: chosen, timeout=2))
            self.assertEqual(chosen[0], PNG)


if __name__ == '__main__':
    unittest.main()
