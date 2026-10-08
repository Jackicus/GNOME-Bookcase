# SPDX-License-Identifier: GPL-2.0-or-later
# SPDX-FileCopyrightText: 2026 Jack Tully

"""A PDF's places: location strings round trip, through the library's progress too."""

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
