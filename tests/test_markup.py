# SPDX-License-Identifier: GPL-2.0-or-later
# SPDX-FileCopyrightText: 2026 Jack Tully

"""widgets/markup.py: a description's HTML as safe Pango markup and as text."""

import unittest

from tests import ROOT  # noqa: F401  (registers bookcase)


class MarkupTest(unittest.TestCase):
    def setUp(self):
        from bookcase.widgets import markup

        self.markup = markup

    def test_paragraphs_and_inline_formatting(self):
        self.assertEqual(
            self.markup.html_to_markup('<p>A <b>bold</b> and <em>quiet</em> start.</p>'
                                       '<p>Then  the\n tide.</p>'),
            'A <b>bold</b> and <i>quiet</i> start.\n\nThen the tide.')

    def test_everything_is_escaped(self):
        result = self.markup.html_to_markup('<p>Salt &amp; tar &lt;3 "quoted"</p>')
        self.assertEqual(result, 'Salt &amp; tar &lt;3 "quoted"')

    def test_unknown_elements_keep_their_text_and_scripts_lose_it(self):
        result = self.markup.html_to_markup(
            '<div class="x" style="color:red"><span onclick="evil()">Kept</span>'
            '<script>alert(1)</script><style>p{}</style><img src="x.png"> text</div>')
        self.assertEqual(result, 'Kept text')

    def test_links_only_to_the_web(self):
        result = self.markup.html_to_markup(
            '<a href="https://example.org/a?b=1&amp;c=2">web</a> '
            '<a href="javascript:alert(1)">script</a> <a href="file:///etc/passwd">file</a>')
        self.assertEqual(result, '<a href="https://example.org/a?b=1&amp;c=2">web</a> script '
                                 'file')
        self.assertEqual(self.markup.html_to_markup('<a href="https://x.org">web</a>',
                                                    links=False), 'web')

    def test_lists_headings_and_breaks(self):
        result = self.markup.html_to_markup(
            '<h2>Praise</h2><ul><li>One</li><li>Two</li></ul>Line<br>break')
        self.assertEqual(result, '<b>Praise</b>\n\n• One\n• Two\n\nLine\nbreak')

    def test_badly_nested_html_still_makes_valid_markup(self):
        result = self.markup.html_to_markup('<b>bold <i>both</b> italic</i> plain')
        self.assertEqual(result, '<b>bold <i>both</i></b><i> italic</i> plain')
        result = self.markup.html_to_markup('<b>never closed')
        self.assertEqual(result, '<b>never closed</b>')

    def test_valid_for_pango(self):
        import gi

        gi.require_version('Pango', '1.0')
        from gi.repository import Pango

        for html in ('<p>A <b>b <i>c</b> d</i></p>', '<a href="https://x.org/?a=<b>">x</a>',
                     '&nbsp;&mdash;&#169;', '<p></p><b></b>', 'plain < text & more'):
            markup = self.markup.html_to_markup(html)
            Pango.parse_markup(markup, -1, '\0')  # raises on invalid markup

    def test_plain_text_keeps_paragraphs(self):
        self.assertEqual(self.markup.html_to_markup('One\nline.\n\nTwo & three'),
                         'One line.\n\nTwo &amp; three')
        self.assertEqual(self.markup.html_to_text('<p>One</p><p>Two &amp; three</p>'),
                         'One\n\nTwo & three')
        self.assertEqual(self.markup.html_to_markup(''), '')


if __name__ == '__main__':
    unittest.main()
