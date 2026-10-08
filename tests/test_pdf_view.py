# SPDX-License-Identifier: GPL-2.0-or-later
# SPDX-FileCopyrightText: 2026 Jack Tully

"""The PDF view: a generated PDF opens, says where the reader is, follows the outline,
searches, selects (across pages too) and highlights, recolours for the paper themes, zooms
(in tiles when it is big), lays spreads out right to left or with the cover beside page 2,
keeps its layout, reads aloud a sentence at a time, and prints."""

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
        cls.prose = make_prose_pdf(cls.directory / 'prose.pdf', pages=4)

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

    def open(self, path=None, **kwargs):
        self.view.open(path or self.path, 'pdf', style=self.style(), **kwargs)
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

    def test_selecting_across_pages(self):
        from gi.repository import Poppler

        self.open(self.prose)
        view = self.view
        # from page 2's first line back to page 1's third: the points in either order
        view._select_range((1, 140, 56), (0, 40, 82), Poppler.SelectionStyle.GLYPH)
        view._selection_done()
        selection = self.events['selection'][-1]
        place = pdf_location.parse(selection['cfi'])
        self.assertEqual([page for page, _rects in place.parts], [1, 2])
        self.assertIn('|2#', selection['cfi'])
        text = selection['text']
        self.assertTrue(text.startswith('A cart of crates'), text)
        self.assertTrue(text.endswith('up the hill and into the town.'), text)
        self.assertNotIn('The lamps', text)
        self.assertEqual(view.selected_text(), text)
        view.set_annotations([{'cfi': selection['cfi'], 'color': 'blue'}])
        for index, (_page, rects) in enumerate(place.parts):
            x0, y0, x1, y1 = rects[0]
            self.assertEqual(view._annotation_at(index, (x0 + x1) / 2, (y0 + y1) / 2)[0],
                             selection['cfi'])

    def test_a_drag_into_the_gap_goes_to_the_nearest_page(self):
        self.open()
        view = self.view
        index, x, y, w, h = next((i, *box) for i, box in view._visible() if i == 0)
        below = view._nearest_point(x + w / 2, y + h + 4)  # in the gap under page 1
        self.assertEqual(below[0], 0)
        self.assertAlmostEqual(below[2], view._sizes[0][1])
        self.assertEqual(view._nearest_point(x - 50, y + 10)[1], 0)  # left of the page
        middle = view._viewport[0] / 2
        view._drag_pointer = (middle, view._viewport[1] - 2)
        self.assertGreater(view._edge_speed()[1], 0)  # near the bottom: scroll down
        view._drag_pointer = (middle, view._viewport[1] / 2)
        self.assertEqual(view._edge_speed(), (0, 0))

    def test_zoomed_in_pages_are_drawn_in_sharp_tiles(self):
        from bookcase.widgets import pdf_view

        self.open()
        view = self.view
        view.set_zoom(6 * pdf_view.POINT)
        target = round(view._render_scale(), 3)
        self.assertTrue(view._tiled(0, target))
        shown = view._visible()
        tiles = view._tiles_on_screen(shown[0][0], shown[0][1], target)
        self.assertTrue(tiles)
        self.assertTrue(wait_for(lambda: all((shown[0][0], target) + t in view._cache
                                             for t in tiles), WAIT))
        texture = view._cache[(shown[0][0], target) + tiles[0]][1]
        self.assertLessEqual(max(texture.get_width(), texture.get_height()), pdf_view.TILE)
        # under the tiles, a whole page no bigger than the backdrop
        self.assertTrue(wait_for(lambda: shown[0][0] in view._cache, WAIT))
        whole = view._cache[shown[0][0]][1]
        self.assertLessEqual(whole.get_width() * whole.get_height(),
                             pdf_view.BACKDROP_PIXELS * 1.01)
        self.assertLessEqual(view._cache_bytes, pdf_view.CACHE_BYTES)
        # a tile's pixels are the page's at full scale: a tile of the top left corner is
        # page 1 drawn at 4x
        page = view._document.get_page(0)
        self.assertEqual(pdf_view.page_pixels(page.get_size(), target)[0],
                         round(view._sizes[0][0] * target))

    def wide(self):
        self.window.set_default_size(900, 400)
        self.view.set_style({**self.style(flow='paginated'), 'maxColumns': 2})
        self.assertTrue(wait_for(lambda: self.view._wants_two(), WAIT))
        self.view._relayout(keep=True)

    def test_spreads_right_to_left_and_the_cover(self):
        self.open()
        view = self.view
        self.wide()
        self.assertEqual(view._spreads[:2], [[0], [1, 2]])  # the cover alone
        view.set_cover(False)
        self.assertEqual(view._spreads[:2], [[0, 1], [2, 3]])
        self.assertFalse(view.cover)
        x_of = dict(zip(view._laid_out, (box[0] for box in view._boxes), strict=True))
        self.assertLess(x_of[0], x_of[1])
        view.set_rtl(True)
        x_of = dict(zip(view._laid_out, (box[0] for box in view._boxes), strict=True))
        self.assertGreater(x_of[0], x_of[1])  # the first page of the spread on the right
        view.go_left()  # forward, right to left
        self.assertTrue(self.relocated_to(3))
        view.go_right()
        self.assertTrue(self.relocated_to(1))
        self.assertEqual(view.layout_state(), {'fit': 'auto', 'zoom': None, 'rtl': True,
                                               'cover': False})

    def test_the_pdf_says_right_to_left(self):
        from bookcase.widgets import pdf_view

        path = make_rtl_pdf(self.directory / 'rtl.pdf')
        view = self.view
        view.open(path, 'pdf', style={**self.style(flow='paginated'), 'maxColumns': 2})
        self.assertTrue(wait_for(lambda: self.events.get('loaded'), WAIT))
        self.assertEqual(self.events['loaded'][-1]['dir'], 'rtl')
        self.assertTrue(view.rtl)
        self.assertFalse(view.cover)  # /PageLayout /TwoPageLeft
        self.assertEqual(view.layout_state()['rtl'], None)  # as the PDF says: not kept
        view.set_rtl(False)
        self.assertFalse(view.rtl)
        self.assertTrue(pdf_view.available())

    def test_the_kept_layout_comes_back(self):
        self.open(layout={'zoom': 200, 'rtl': True})
        self.assertIsNone(self.view.fit)
        self.assertEqual(self.view.zoom_percent, 200)
        self.assertTrue(self.view.rtl)
        self.view.restore_layout({'fit': 'page'})
        self.assertEqual(self.view.fit, 'page')
        self.assertFalse(self.view.rtl)
        self.assertEqual(self.view.layout_state(), {'fit': 'page', 'zoom': None, 'rtl': None,
                                                    'cover': None})

    def test_reading_aloud_a_sentence_at_a_time(self):
        self.open(self.prose)
        view = self.view
        said = []
        view.tts_start(said.append)
        self.assertEqual(said, [True])
        for _ in range(3):
            view.tts_next(said.append)
        self.assertEqual(said[1:], PROSE[:2] + [PROSE[2] + ' ' + PROSE[3]])
        (first, rects), (second, more) = view._tts_parts  # a sentence over two pages
        self.assertEqual((first, len(rects), second, len(more)), (0, 2, 1, 1))
        self.assertTrue(view._has_marks(0) and view._has_marks(1))
        view.tts_prev(said.append)
        self.assertEqual(said[-1], PROSE[1])
        (index, rects), = view._tts_parts
        self.assertEqual((index, len(rects)), (0, 1))
        view.tts_word(len('Nobody on the '))  # 'quay'
        (index, (word,)), = view._tts_word
        self.assertLess(word[2] - word[0], (rects[0][2] - rects[0][0]) / 5)
        # on into the next page, its first line left out (read with page 1's last)
        for _ in range(2):
            view.tts_next(said.append)
        self.assertEqual(said[-1], PROSE[0])
        self.assertTrue(self.relocated_to(2))
        # the reader jumps: the next sentence is the new page's first
        view.go_to('page:4')
        self.assertTrue(wait_for(lambda: self.view.place.get('ttsMoved'), WAIT))
        view.tts_next(said.append)
        self.assertEqual(said[-1], PROSE[3])  # starting on page 4: its first line too
        view.tts_stop()
        self.assertIsNone(view._tts_parts)
        view.tts_next(said.append)
        self.assertIsNone(said[-1])

    def test_printing_draws_every_page(self):
        # The operation is never run here (its EXPORT blocks in a full test run): its
        # draw-page handler draws on a PDF surface standing in for the paper.
        import cairo
        from gi.repository import Gio, Poppler

        from bookcase.widgets import pdf_view

        self.open()
        operation = self.view.print_operation()
        self.assertEqual(operation.props.n_pages, 6)
        self.assertEqual(operation.props.job_name, 'A Quiet Harbour')
        out = self.directory / 'printed.pdf'
        surface = cairo.PDFSurface(str(out), 595, 842)  # A4: the A6 pages fitted to it
        cr = cairo.Context(surface)

        class Paper:
            def get_width(self):
                return 595

            def get_height(self):
                return 842

            def get_cairo_context(self):
                return cr

        document = pdf_view.open_document(self.path)
        for number in range(document.get_n_pages()):
            cr.save()
            pdf_view._print_page(operation, Paper(), number, document)
            cr.restore()
            cr.show_page()
        surface.finish()
        printed = Poppler.Document.new_from_gfile(Gio.File.new_for_path(str(out)), None, None)
        self.assertEqual(printed.get_n_pages(), 6)
        self.assertIn('Page 3 of the harbour', printed.get_page(2).get_text())
        found = printed.get_page(0).find_text('Page 1 of the harbour')
        self.assertGreater(found[0].x1, 55)  # scaled up twice: 60 points in, not 30


PROSE = ['The lamps along the harbour wall were lit one by one.',
         'Nobody on the quay said much; the gulls said enough.',
         'A cart of crates rolled past the chandlery, and the smell of tar and rope '
         'followed it up the hill',
         'and into the town.']


def make_prose_pdf(path, pages=4):
    """A5 pages of short lines: the first two sentences of PROSE, then the third over two
    lines, which runs on to the next page's first line (the fourth); the last page ends
    it."""
    import cairo

    surface = cairo.PDFSurface(str(path), 420, 595)
    context = cairo.Context(surface)
    context.select_font_face('Sans')
    context.set_font_size(11)
    for number in range(1, pages + 1):
        lines = [PROSE[3]] if number > 1 else []
        lines += PROSE[:2] + ['A cart of crates rolled past the chandlery, and the smell',
                              'of tar and rope followed it up the hill']
        if number == pages:
            lines[-1] += '.'
        for line, text in enumerate(lines):
            context.move_to(40, 60 + 16 * line)
            context.show_text(text)
        context.show_page()
    surface.finish()
    return path


def make_rtl_pdf(path, pages=4):
    """A PDF written by hand whose catalogue says right to left (/ViewerPreferences
    /Direction /R2L) and the cover beside page 2 (/PageLayout /TwoPageLeft)."""
    objects = ['<< /Type /Catalog /Pages 2 0 R /PageLayout /TwoPageLeft '
               '/ViewerPreferences << /Direction /R2L >> >>',
               '<< /Type /Pages /Kids [{}] /Count {} >>'.format(
                   ' '.join(f'{3 + n} 0 R' for n in range(pages)), pages)]
    objects += ['<< /Type /Page /Parent 2 0 R /MediaBox [0 0 200 300] >>'
                for _n in range(pages)]
    out = bytearray(b'%PDF-1.4\n')
    offsets = []
    for number, body in enumerate(objects, 1):
        offsets.append(len(out))
        out += f'{number} 0 obj\n{body}\nendobj\n'.encode()
    xref = len(out)
    out += f'xref\n0 {len(objects) + 1}\n0000000000 65535 f \n'.encode()
    for offset in offsets:
        out += f'{offset:010d} 00000 n \n'.encode()
    out += (f'trailer\n<< /Size {len(objects) + 1} /Root 1 0 R >>\nstartxref\n{xref}\n'
            '%%EOF\n').encode()
    path.write_bytes(bytes(out))
    return path


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
