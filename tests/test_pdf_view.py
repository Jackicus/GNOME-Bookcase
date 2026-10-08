# SPDX-License-Identifier: GPL-2.0-or-later
# SPDX-FileCopyrightText: 2026 Jack Tully

"""The PDF view: a generated PDF opens, says where the reader is, follows the outline,
searches, selects and highlights, recolours for the paper themes, and zooms."""

import pathlib
import shutil
import tempfile
import unittest

from tests import ROOT  # noqa: F401  (registers src/ as bookcase)
from tests.gtk import pump, requires_gtk, wait_for
from tests.support import make_paged_pdf

from bookcase import pdf_location

WAIT = 3


@requires_gtk
class PdfViewTest(unittest.TestCase):

    @classmethod
    def setUpClass(cls):
        from bookcase.widgets import pdf_view

        if not pdf_view.available():
            raise unittest.SkipTest('Poppler or pycairo is not available')
        cls.directory = pathlib.Path(tempfile.mkdtemp(prefix='bookcase-pdf-'))
        cls.path = make_paged_pdf(cls.directory / 'harbour.pdf', pages=6)

    @classmethod
    def tearDownClass(cls):
        shutil.rmtree(cls.directory, ignore_errors=True)

    def setUp(self):
        from gi.repository import Gtk

        from bookcase.widgets.pdf_view import PdfView

        self.view = PdfView()
        self.window = Gtk.Window(default_width=400, default_height=500, child=self.view)
        self.events = {}
        for name in ('loaded', 'toc-ready', 'relocated', 'selection', 'search-result',
                     'search-done', 'history', 'annotation-activated'):
            self.view.connect(name, self.record, name)
        self.window.present()

    def tearDown(self):
        self.view.close()
        self.window.destroy()
        pump()

    def record(self, _view, message, name):
        self.events.setdefault(name, []).append(message)

    def style(self, theme='light', flow='scrolled'):
        colours = {'light': ('#ffffff', '#1d1d1f', False), 'dark': ('#222226', '#deddda', True)}
        bg, fg, dark = colours[theme]
        return {'theme': {'name': theme, 'bg': bg, 'fg': fg, 'dark': dark}, 'flow': flow,
                'maxColumns': 1}

    def open(self, **kwargs):
        self.view.open(self.path, 'pdf', style=self.style(), **kwargs)
        self.assertTrue(wait_for(lambda: self.events.get('relocated'), WAIT))
        return self.events['relocated'][-1]

    def relocated_to(self, page):
        return wait_for(lambda: self.view.place and self.view.place['page'] == page, WAIT)

    def test_it_opens_at_the_saved_page_and_reports_it(self):
        place = self.open(location='page:3')
        loaded = self.events['loaded'][0]
        self.assertEqual(loaded['pages'], 6)
        self.assertEqual([item['label'] for item in loaded['toc']],
                         ['Part 1', 'Part 2', 'Part 3'])
        self.assertEqual(place['page'], 3)
        self.assertEqual(pdf_location.parse(place['cfi']).page, 3)
        self.assertAlmostEqual(place['fraction'], 2 / 6, places=2)
        self.assertEqual(place['chapter']['label'], 'Part 2')
        self.assertEqual(place['chapter']['href'], loaded['toc'][1]['href'])
        self.assertEqual(place['pages'], 6)

    def test_a_fraction_and_the_contents_and_back(self):
        self.open(fraction=0.5)
        self.assertEqual(self.view.place['page'], 4)
        self.view.go_to(self.events['loaded'][0]['toc'][0]['href'])
        self.assertTrue(self.relocated_to(1))
        self.assertEqual(self.view.place['jumpedFrom'][:6], 'page:4')
        self.assertTrue(self.view.place['canGoBack'])
        self.view.back()
        self.assertTrue(self.relocated_to(4))
        self.view.next_section()
        self.assertTrue(self.relocated_to(5))

    def test_paginated_turns_a_page(self):
        self.open()
        self.view.set_style(self.style(flow='paginated'))
        self.assertEqual(self.view.place['page'], 1)
        self.view.go_right()
        self.assertTrue(self.relocated_to(2))
        self.view.end()
        self.assertTrue(wait_for(lambda: self.view.place['atEnd'], WAIT))
        self.assertEqual(self.view.place['fraction'], 1.0)

    def test_search_finds_the_text_with_its_context(self):
        self.open()
        self.view.search('the gulls')
        self.assertTrue(wait_for(lambda: self.events.get('search-done'), WAIT))
        self.assertEqual(self.events['search-done'][0]['count'], 6)
        result = self.events['search-result'][0]
        self.assertEqual(result['label'], 'Part 1 · Page 1')
        item = result['items'][0]
        self.assertEqual(item['match'], 'the gulls')
        self.assertTrue(item['pre'].endswith('said much; '))
        place = pdf_location.parse(item['cfi'])
        self.assertEqual((place.page, len(place.rects)), (1, 1))
        self.view.select(self.events['search-result'][2]['items'][0]['cfi'])
        self.assertTrue(self.relocated_to(3))
        self.view.clear_search()
        self.assertEqual(self.view._hits, {})

    def test_selecting_text_and_highlights(self):
        from gi.repository import Poppler

        self.open()
        view = self.view
        view._select(0, (25, 80), (290, 115), Poppler.SelectionStyle.GLYPH)
        view._selection_done()
        selection = self.events['selection'][-1]
        self.assertIn('the gulls said', selection['text'])
        self.assertEqual(view.selected_text(), selection['text'])
        place = pdf_location.parse(selection['cfi'])
        self.assertEqual(place.page, 1)
        self.assertEqual(len(place.rects), 2)  # one box per line
        self.assertGreater(selection['rect']['width'], 100)
        view.clear_selection()
        self.assertEqual(view.selected_text(), '')
        view.set_annotations([{'cfi': selection['cfi'], 'color': 'green'}])
        x0, y0, x1, y1 = place.rects[0]
        self.assertEqual(view._annotation_at(0, (x0 + x1) / 2, (y0 + y1) / 2)[0],
                         selection['cfi'])
        view.remove_annotation(selection['cfi'])
        self.assertIsNone(view._annotation_at(0, (x0 + x1) / 2, (y0 + y1) / 2))

    def test_links_and_bookmarks(self):
        self.open()
        links = self.view._links_of(0)
        self.assertEqual(sorted(kind for _rect, (kind, _target) in links), ['page', 'uri'])
        page_link = next(target for _rect, target in links if target[0] == 'page')
        self.view._follow(page_link)
        self.assertTrue(self.relocated_to(6))
        found = []
        self.view.set_bookmarks(['page:6', 'page:2'], found.append)
        self.assertEqual(found, ['page:6'])

    def test_zoom(self):
        self.open()
        before = self.view.zoom_percent
        self.view.zoom_in()
        self.assertGreater(self.view.zoom_percent, before)
        self.assertIsNone(self.view.fit)
        self.view.set_fit('page')
        self.assertEqual(self.view.fit, 'page')
        self.assertEqual(self.view.place['page'], 1)

    def test_the_paper_themes(self):
        from bookcase.widgets import pdf_view

        self.assertIsNone(pdf_view.theme_matrix({'name': 'light', 'bg': '#ffffff'}))

        def apply(theme, rgb):
            rows, offset = pdf_view.theme_matrix(theme)
            return [round(sum(rows[j][i] * rgb[i] for i in range(3)) + offset[j], 3)
                    for j in range(3)]

        dark = {'name': 'dark', 'bg': '#222226', 'fg': '#deddda', 'dark': True}
        bg = [round(v / 255, 3) for v in (0x22, 0x22, 0x26)]
        fg = [round(v / 255, 3) for v in (0xde, 0xdd, 0xda)]
        self.assertEqual(apply(dark, (1, 1, 1)), bg)  # white paper becomes the dark paper
        self.assertEqual(apply(dark, (0, 0, 0)), fg)  # black ink becomes the light ink
        sepia = {'name': 'sepia', 'bg': '#f4ecd8', 'fg': '#5b4636', 'dark': False}
        self.assertEqual(apply(sepia, (1, 1, 1)),
                         [round(v / 255, 3) for v in (0xf4, 0xec, 0xd8)])


@requires_gtk
class PasswordTest(unittest.TestCase):

    def test_an_encrypted_pdf_asks_for_its_password(self):
        from unittest import mock

        from bookcase.widgets import pdf_view

        if not pdf_view.available():
            self.skipTest('Poppler or pycairo is not available')
        from gi.repository import GLib, Poppler

        error = GLib.Error.new_literal(Poppler.error_quark(), 'encrypted',
                                       int(Poppler.Error.ENCRYPTED))
        self.assertTrue(pdf_view.is_encrypted_error(error))
        view = pdf_view.PdfView()
        with mock.patch.object(pdf_view, 'open_document', side_effect=error), \
                mock.patch.object(GLib, 'idle_add') as idle_add:
            view.open('/invented/locked.pdf', 'pdf')
        idle_add.assert_called_once_with(view._ask_password, False)


if __name__ == '__main__':
    unittest.main()
