# SPDX-License-Identifier: GPL-2.0-or-later
# SPDX-FileCopyrightText: 2026 Jack Tully

"""Sort forms and comparison keys of titles and names."""

import unittest

from tests import ROOT  # noqa: F401
from bookcase import titles


class TitleSortTest(unittest.TestCase):

    def test_articles_move_to_the_end(self):
        self.assertEqual(titles.title_sort('The Hobbit'), 'Hobbit, The')
        self.assertEqual(titles.title_sort('A Quiet Harbour'), 'Quiet Harbour, A')
        self.assertEqual(titles.title_sort('An Owl'), 'Owl, An')
        self.assertEqual(titles.title_sort('  The   Hobbit '), 'Hobbit, The')

    def test_what_stays(self):
        self.assertEqual(titles.title_sort('Theory of Tides'), 'Theory of Tides')
        self.assertEqual(titles.title_sort('The'), 'The')
        self.assertEqual(titles.title_sort('A'), 'A')
        self.assertEqual(titles.title_sort(''), '')
        self.assertEqual(titles.title_sort('Der Prozess'), 'Der Prozess')

    def test_other_languages(self):
        self.assertEqual(titles.title_sort('Der Prozess', 'de'), 'Prozess, Der')
        self.assertEqual(titles.title_sort("L'Étranger", 'fr'), "Étranger, L'")
        self.assertEqual(titles.title_sort('El Camino', 'es-ES'), 'Camino, El')
        self.assertEqual(titles.title_sort('The Owl', 'xx'), 'Owl, The')


class AuthorSortTest(unittest.TestCase):

    def test_names(self):
        cases = {
            'Ada Lark': 'Lark, Ada',
            'Ada  B.  Lark': 'Lark, Ada B.',
            'Plato': 'Plato',
            'Lark, Ada': 'Lark, Ada',
            '': '',
            'Martin Luther King Jr.': 'King, Martin Luther, Jr.',
            'Ada Lark III': 'Lark, Ada, III',
            'Ursula K. Le Guin': 'Le Guin, Ursula K.',
            'Daphne Du Maurier': 'Du Maurier, Daphne',
            'Walter De la Mare': 'De la Mare, Walter',
            'Ludwig van Beethoven': 'Beethoven, Ludwig van',
            'Simone de Beauvoir': 'Beauvoir, Simone de',
            'Henry VIII': 'Henry VIII',
            'Author 12': 'Author 12',
            'de Lark': 'Lark, de',
        }
        for name, expected in cases.items():
            with self.subTest(name=name):
                self.assertEqual(titles.author_sort(name), expected)

    def test_several(self):
        self.assertEqual(titles.authors_sort(['Ada Lark', 'Ben Ross']), 'Lark, Ada & Ross, Ben')
        self.assertEqual(titles.authors_sort([]), '')


class KeyTest(unittest.TestCase):

    def test_fold(self):
        self.assertEqual(titles.fold('Émile ZOLA'), 'emile zola')
        self.assertEqual(titles.fold('Straße'), 'strasse')
        self.assertEqual(titles.fold(''), '')
        self.assertEqual(titles.fold(None), '')

    def test_sort_key_orders_numbers_by_value(self):
        names = ['Book 10', 'book 2', 'Émile', 'Book 1', 'Zed', 'Ashes']
        self.assertEqual(sorted(names, key=titles.sort_key),
                         ['Ashes', 'Book 1', 'book 2', 'Book 10', 'Émile', 'Zed'])

    def test_title_key(self):
        same = ['The Hobbit', 'Hobbit', 'THE HOBBIT!', 'The Hobbit: or There and Back Again',
                'The Hobbit (Illustrated Edition)', 'The Hobbit - A Novel']
        self.assertEqual({titles.title_key(title) for title in same}, {'hobbit'})
        self.assertEqual(titles.title_key('The'), 'the')
        self.assertEqual(titles.title_key(': Only Subtitle'), 'only subtitle')
        self.assertNotEqual(titles.title_key('Hobbit Tales'), 'hobbit')

    def test_name_tokens(self):
        self.assertEqual(titles.name_tokens('Lark, Ada'), titles.name_tokens('Ada Lark'))
        self.assertEqual(titles.name_tokens('A. Lark'), {'lark'})


if __name__ == '__main__':
    unittest.main()
