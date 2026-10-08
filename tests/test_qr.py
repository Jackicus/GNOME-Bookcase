# SPDX-License-Identifier: GPL-2.0-or-later
# SPDX-FileCopyrightText: 2026 Jack Tully

"""The QR encoder against known outputs: Reed-Solomon codewords from the standard's worked
example (as thonky.com's QR tutorial gives it), and whole matrices made by libqrencode 4.1
(`qrencode -8 -l LEVEL -m 0 -t ASCII`) for the same text, level and mask, each row a hex
number, its leftmost module the highest bit."""

import unittest

from tests import ROOT  # noqa: F401  (registers bookcase)

from bookcase import qr

LONG = 'http://bookcase.example/opds/search?q=' + 'harbour+lark+' * 9

# (text, level, the mask libqrencode chose, version, rows)
VECTORS = (
    ('HELLO WORLD', 'M', 3, 1, (
        '1fd17f', '105141', '17405d', '17555d', '174e5d', '104741', '1fd57f', '1f00',
        '16eb4b', 'c2fec', 'faa3', '15b22a', '116d85', '1665', '1fd7f0', '105caf', '174948',
        '175c4e', '175924', '104ef1', '1fdaa0')),
    ('HELLO WORLD', 'H', 3, 2, (
        '1fc0e7f', '1045441', '174e45d', '174c95d', '175885d', '1042d41', '1fd557f',
        '13a00', '6704d0', '130cf65', '344940', '315a2e', '1372aeb', '8a5be', 'eed396',
        '1719971', '2463f3', '11b18', '1fddb57', '104b511', '17409f1', '1752849', '17584b2',
        '104b1dc', '1fc8d23')),
    ('http://192.168.1.20:8095/', 'M', 6, 2, (
        '1fda97f', '1051441', '175d85d', '1745f5d', '175105d', '104b041', '1fd557f', 'ec00',
        '13fd297', '13aeb9e', '6d9ef9', '28d29f', '19575c1', '1930232', '1e5fb8f',
        '1536515', '1469df6', '1b712', '1fd6d59', '105ed13', '175f3f8', '175386b',
        '1746417', '104a6f7', '1fd2749')),
    (LONG, 'M', 2, 9, (  # version information blocks (version 7 and up)
        '1fc1de13c3fc7f', '104f46ba3d1641', '1755c0d3db125d', '175abb0aedcd5d',
        '175cedfff39c5d', '105ab151bd5441', '1fd5555555557f', '1e81514e7800',
        '17c3773f3bc97c', 'd071e5dd31e81', '1571b78c3d73c8', 'bac1a3878f582',
        '1cf66c1eef01df', '1929944762b60f', 'a6da0c0cc61d0', 'a197079b494cb',
        '17e1b587af8376', '183148d3f48e19', '1efaba66b9e2e8', '26adf9540289',
        '777b376e3a51d', '6b13d96f10e23', '1ff7e39d94a144', '1439c36b55b0e9',
        '7fc661f068bf7', '1d1665f1603719', '195f5135266350', '51a9331687919',
        'ff4923f03ebfd', '108fab65e1bc7f', '1efb0c8d066038', '1b496d49c12d9',
        '9d6871c258967', 'fae41e08a8e51', '55576c35ce398', '1eb1417c5a3bd8',
        '17709e3a6349ae', '1e19fee5db1cf3', '1154ab93016934', '386d33a5ae7f3',
        '26b0b990b5dc6', '1512f42fd3ffbf', '1bf7e3c50f1a2e', 'c00838c13f3cb',
        '2456b1fa149fe', '1c03f1c3b719', '1fcb3555356752', '105440d188531a',
        '17563d5fbf0bff', '1755a6b2d28d84', '175515d4bc42f3', '104f594c5c380a',
        '1fd74c32e747d4')),
)


def rows_of(code):
    return tuple(format(int(''.join('1' if dark else '0' for dark in row), 2), 'x')
                 for row in code.modules)


class QrTest(unittest.TestCase):

    def test_reed_solomon_of_the_worked_example(self):
        data = [32, 91, 11, 120, 209, 114, 220, 77, 67, 64, 236, 17, 236, 17, 236, 17]
        self.assertEqual(qr.reed_solomon(data, 10),
                         [196, 35, 39, 119, 235, 215, 231, 226, 93, 23])

    def test_matrices_match_libqrencode(self):
        for text, level, mask, version, rows in VECTORS:
            with self.subTest(text=text[:20], level=level):
                code = qr.encode(text, level, mask=mask)
                self.assertEqual(code.version, version)
                self.assertEqual(code.size, version * 4 + 17)
                self.assertEqual(rows_of(code), rows)

    def test_the_best_mask_is_chosen(self):
        code = qr.encode('HELLO WORLD')
        scores = [qr.encode('HELLO WORLD', mask=mask) for mask in range(8)]
        best = min(range(8), key=lambda mask: _penalty(scores[mask]))
        self.assertEqual(code.mask, best)
        self.assertEqual(code.modules, scores[best].modules)

    def test_capacity_of_level_m(self):
        # The standard's table of byte-mode capacities, versions 1 to 10.
        self.assertEqual([qr.capacity(version, 'M') for version in range(1, 11)],
                         [14, 26, 42, 62, 84, 106, 122, 152, 180, 213])
        self.assertEqual([qr.codewords(version) for version in (1, 2, 7, 10)],
                         [26, 44, 196, 346])
        self.assertEqual(qr.alignment_positions(7), [6, 22, 38])
        self.assertEqual(qr.alignment_positions(10), [6, 28, 50])

    def test_versions_grow_with_the_text(self):
        self.assertEqual(qr.encode('a' * 14).version, 1)
        self.assertEqual(qr.encode('a' * 15).version, 2)
        self.assertEqual(qr.encode('a' * 213).version, 10)
        with self.assertRaises(ValueError):
            qr.encode('a' * 214)

    def test_unicode_is_utf8(self):
        self.assertEqual(qr.encode('café').modules, qr.encode('café'.encode()).modules)

    def test_text_rendering(self):
        lines = qr.encode('HELLO WORLD').text(border=1).split('\n')
        self.assertEqual(len(lines), 23)
        self.assertTrue(lines[1].startswith('  ██████████████'))


def _penalty(code):
    matrix = qr._Matrix(code.version)
    matrix.modules = code.modules
    return matrix.penalty()


if __name__ == '__main__':
    unittest.main()
