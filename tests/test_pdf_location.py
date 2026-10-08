# SPDX-License-Identifier: GPL-2.0-or-later
# SPDX-FileCopyrightText: 2026 Jack Tully

"""A PDF's places: location strings round trip (over several pages too), through the
library's progress; a book's saved layout is checked; a page's text is cut into sentences."""

import unittest

from tests import ROOT  # noqa: F401  (registers src/ as bookcase)
from tests.support import add_book, temporary_library

from bookcase import pdf_location
from bookcase.pdf_location import Place


class LocationTest(unittest.TestCase):

    def test_a_page_round_trips(self):
        for page, offset in ((1, 0.0), (12, 0.25), (300, 0.999), (7, 1.0)):
            text = pdf_location.location(page, offset)
            self.assertEqual(pdf_location.parse(text), Place(page, offset))
        self.assertEqual(pdf_location.location(12), 'page:12')
        self.assertEqual(pdf_location.location(12, 0.25), 'page:12@0.25')
        self.assertEqual(pdf_location.location(0, -1), 'page:1')

    def test_rects_round_trip_rounded_and_ordered(self):
        rects = [(72, 90.54, 300, 104), (180.04, 119.5, 72, 106)]
        text = pdf_location.location(3, rects=rects)
        self.assertEqual(text, 'page:3#72,90.5,300,104;180,119.5,72,106')
        place = pdf_location.parse(text)
        self.assertEqual(place.page, 3)
        self.assertEqual(place.rects, ((72, 90.5, 300, 104), (72, 106, 180, 119.5)))

    def test_what_is_not_a_page_location(self):
        for text in ('', None, 'epubcfi(/6/4!/4/2/1:0)', 'page:', 'page:x', 'page:0',
                     'page:3@abc'):
            self.assertIsNone(pdf_location.parse(text), text)
        self.assertEqual(pdf_location.parse('page:3#1,2,3;bad').rects, ())

    def test_a_highlight_over_pages(self):
        parts = [(3, [(72, 700, 300, 714)]), (4, [(72, 60, 180, 74), (72, 76, 120, 90)])]
        text = pdf_location.span_location(parts)
        self.assertEqual(text, 'page:3#72,700,300,714|4#72,60,180,74;72,76,120,90')
        place = pdf_location.parse(text)
        self.assertEqual((place.page, place.rects), (3, ((72, 700, 300, 714),)))
        self.assertEqual(place.more, ((4, ((72, 60, 180, 74), (72, 76, 120, 90))),))
        self.assertEqual([page for page, _rects in place.parts], [3, 4])
        self.assertEqual(pdf_location.location(3, rects=parts[0][1], more=parts[1:]), text)
        # one page: the old form, which an older Bookcase reads
        self.assertEqual(pdf_location.span_location(parts[:1]), 'page:3#72,700,300,714')
        self.assertEqual(pdf_location.span_location([]), '')
        # pages out of order, unreadable or empty are dropped; the first page stays
        place = pdf_location.parse('page:5#1,2,3,4|x#1,2,3,4|4#1,2,3,4|6#bad|7#1,2,3,4')
        self.assertEqual([page for page, _rects in place.parts], [5, 7])
        # what an older Bookcase made of it: the first page's rects, the rest dropped
        old = pdf_location.parse_rects('72,700,300,714|4#72,60,180,74')
        self.assertEqual(old, ())

    def test_the_saved_layout_is_checked(self):
        state = pdf_location.layout_state
        empty = {'fit': None, 'zoom': None, 'flow': None, 'rtl': None, 'cover': None}
        for value in (None, [], 'x', {}, {'fit': 'sideways', 'zoom': 'big', 'rtl': 'yes'}):
            self.assertEqual(state(value), empty, value)
        self.assertEqual(state({'fit': 'width', 'zoom': 150, 'flow': 'paginated',
                                'rtl': True, 'cover': False}),
                         {'fit': 'width', 'zoom': None, 'flow': 'paginated', 'rtl': True,
                          'cover': False})
        self.assertEqual(state({'zoom': 1e9})['zoom'], 600)
        self.assertEqual(state({'zoom': 3})['zoom'], 25)
        self.assertEqual(state({'zoom': 133.4})['zoom'], 133)
        self.assertIsNone(state({'zoom': True})['zoom'])

    def test_a_page_in_sentences(self):
        text = ('Page 1 of the harbour\nThe lamps were lit along the whole of the harbour '
                'wall. Nobody said \u201cmuch.\u201d The gulls\nsaid enough for everyone on '
                'the quay, and the rest of the town as well, every-\none!\n\nAnd on it '
                'went')
        spoken = [pdf_location.speakable(text[a:b]) for a, b in pdf_location.sentences(text)]
        self.assertEqual(spoken, [
            'Page 1 of the harbour',
            'The lamps were lit along the whole of the harbour wall.',
            'Nobody said \u201cmuch.\u201d',
            'The gulls said enough for everyone on the quay, and the rest of the town as '
            'well, everyone!',
            'And on it went'])
        self.assertEqual(pdf_location.sentences(''), [])
        self.assertEqual(pdf_location.sentences(' \n 12 \n'), [(3, 5)])
        self.assertEqual(pdf_location.sentences('-- . --'), [])
        self.assertFalse(pdf_location.ends_sentence('and on it'))
        self.assertTrue(pdf_location.ends_sentence('it went.\n'))
        self.assertTrue(pdf_location.ends_sentence(''))

    def test_what_is_spoken_maps_back_to_the_page(self):
        text = '  The har-\n bour, at\n\ndusk.  '
        spoken, positions = pdf_location.speakable_map(text)
        self.assertEqual(spoken, 'The harbour, at dusk.')
        self.assertEqual(len(positions), len(spoken))
        for char, position in zip(spoken, positions, strict=True):
            self.assertEqual(char, ' ' if text[position].isspace() else text[position])
        self.assertEqual(text[positions[spoken.index('bour')]:][:4], 'bour')
        self.assertEqual(pdf_location.speakable_map(''), ('', []))

    def test_a_pdf_that_reads_right_to_left(self):
        import tempfile

        with tempfile.TemporaryDirectory() as directory:
            path = f'{directory}/a.pdf'
            with open(path, 'wb') as stream:
                stream.write(b'%PDF-1.4\n1 0 obj << /Type /Catalog /ViewerPreferences '
                             b'<< /Direction  /R2L >> >> endobj\n')
            self.assertTrue(pdf_location.declares_rtl(path))
            with open(path, 'wb') as stream:
                stream.write(b'%PDF-1.4\n1 0 obj << /Direction /L2R >> endobj\n')
            self.assertFalse(pdf_location.declares_rtl(path))
        self.assertFalse(pdf_location.declares_rtl('/invented/missing.pdf'))

    def test_fractions(self):
        self.assertEqual(pdf_location.fraction(1, 0.0, 10), 0.0)
        self.assertAlmostEqual(pdf_location.fraction(6, 0.5, 10), 0.55)
        self.assertEqual(pdf_location.fraction(10, 1.0, 10), 1.0)
        self.assertEqual(pdf_location.fraction(3, 0, 0), 0.0)
        page, offset = pdf_location.from_fraction(0.55, 10)
        self.assertEqual(page, 6)
        self.assertAlmostEqual(offset, 0.5)
        self.assertEqual(pdf_location.from_fraction(1.0, 10), (10, 1.0))
        self.assertEqual(pdf_location.from_fraction(None, 10), (1, 0.0))

    def test_the_place_survives_the_library(self):
        with temporary_library() as library:
            book_id = add_book(library, 'A Quiet Harbour', path='/invented/harbour.pdf',
                               fmt='pdf')
            location = pdf_location.location(42, 0.3)
            library.set_progress(book_id, pdf_location.fraction(42, 0.3, 120), location)
            book = library.book(book_id)
            self.assertEqual(pdf_location.parse(book.location), Place(42, 0.3))
            self.assertAlmostEqual(book.progress, 41.3 / 120)


if __name__ == '__main__':
    unittest.main()
