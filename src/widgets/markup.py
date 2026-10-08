# SPDX-License-Identifier: GPL-2.0-or-later
# SPDX-FileCopyrightText: 2026 Jack Tully

"""A book's description (HTML, from the book or a website: untrusted) as Pango markup for a
Gtk.Label, and as plain text.

    html_to_markup('<p>A <b>bold</b> start.</p><p>Then</p>')   # 'A <b>bold</b> start.\\n\\nThen'
    html_to_text(html)                                          # the same without markup

Kept: b/strong, i/em/cite, u, s/strike/del, sub, sup, code/tt, br; paragraphs, divs,
headings, list items and block quotes become line breaks (a list item starts with a bullet,
a heading is bold); links to http(s) addresses stay links (the label opens them in the
browser when clicked), any other link keeps only its text. Everything else keeps its text and
loses its element; script and style elements lose their text too. Whitespace collapses as a
browser collapses it. The result is always valid markup (everything is escaped, every
element closed).
"""

import html
import re
from html.parser import HTMLParser

INLINE = {
    'b': 'b', 'strong': 'b',
    'i': 'i', 'em': 'i', 'cite': 'i', 'var': 'i',
    'u': 'u', 'ins': 'u',
    's': 's', 'strike': 's', 'del': 's',
    'sub': 'sub', 'sup': 'sup',
    'code': 'tt', 'tt': 'tt', 'kbd': 'tt', 'samp': 'tt',
}
BLOCKS = {'p', 'div', 'section', 'article', 'blockquote', 'ul', 'ol', 'dl', 'dt', 'dd',
          'table', 'tr', 'pre', 'header', 'footer', 'figure', 'figcaption', 'hr'}
HEADINGS = {'h1', 'h2', 'h3', 'h4', 'h5', 'h6'}
HIDDEN = {'script', 'style', 'head', 'title', 'noscript', 'template'}
BULLET = '•'
_TAG = re.compile(r'<[a-zA-Z/!]')
SAFE_LINK = re.compile(r'^https?://[^\s<>"]+$', re.IGNORECASE)


class _Converter(HTMLParser):

    def __init__(self, links):
        super().__init__(convert_charrefs=True)
        self.links = links
        self.parts = []  # ('text', str) | ('open', tag, attrs) | ('close', tag) | ('break', n)
        self.open = []  # the Pango elements open, innermost last
        self.hidden = 0

    # Whitespace is collapsed as it arrives (a run becomes one space, none at the start of a
    # line) and trimmed at the end of each line.

    def _trim(self):
        """Drop the space that ends the last text written (before a line break)."""
        for index in range(len(self.parts) - 1, -1, -1):
            part = self.parts[index]
            if part[0] == 'text':
                self.parts[index] = ('text', part[1].rstrip(' '))
                return
            if part[0] == 'break':
                return

    def _ends_with_space(self):
        for part in reversed(self.parts):
            if part[0] == 'text':
                return part[1].endswith(' ')
            if part[0] == 'break':
                return True
        return True

    def _break(self, lines):
        if not any(p[0] == 'text' for p in self.parts):
            return  # nothing written yet: no leading blank lines
        self._trim()
        if self.parts and self.parts[-1][0] == 'break':
            previous = self.parts.pop()
            lines = max(lines, previous[1])
        self.parts.append(('break', lines))

    def handle_starttag(self, tag, attrs):
        if tag in HIDDEN:
            self.hidden += 1
            return
        if self.hidden:
            return
        if tag == 'br':
            self._break(1)
        elif tag in BLOCKS or tag in HEADINGS:
            self._break(2 if tag not in ('dt', 'dd', 'tr') else 1)
            if tag in HEADINGS:
                self._push('b', ())
        elif tag == 'li':
            self._break(1)
            self.parts.append(('text', BULLET + ' '))
        elif tag in INLINE:
            self._push(INLINE[tag], ())
        elif tag == 'a':
            href = dict(attrs).get('href') or ''
            if self.links and SAFE_LINK.match(href.strip()):
                self._push('a', (('href', href.strip()),))
            else:
                self._push(None, ())

    def handle_startendtag(self, tag, attrs):
        if tag in ('br', 'hr'):
            self.handle_starttag(tag, attrs)
        elif tag in BLOCKS:
            self._break(2)

    def handle_endtag(self, tag):
        if tag in HIDDEN:
            self.hidden = max(0, self.hidden - 1)
            return
        if self.hidden:
            return
        if tag in HEADINGS:
            self._pop('b')
            self._break(2)
        elif tag in BLOCKS:
            self._break(2 if tag not in ('dt', 'dd', 'tr') else 1)
        elif tag == 'li':
            self._break(1)
        elif tag in INLINE:
            self._pop(INLINE[tag])
        elif tag == 'a':
            for name in reversed(self.open):
                if name in ('a', None):
                    self._pop(name)
                    break

    def handle_data(self, data):
        if self.hidden or not data:
            return
        words = data.split()
        if not words:
            if not self._ends_with_space():
                self.parts.append(('text', ' '))
            return
        text = ' '.join(words)
        if data[:1].isspace() and not self._ends_with_space():
            text = ' ' + text
        if data[-1:].isspace():
            text += ' '
        self.parts.append(('text', text))

    def _push(self, name, attrs):
        self.open.append(name)
        if name is not None:
            self.parts.append(('open', name, attrs))

    def _pop(self, name):
        if name not in self.open:
            return
        # Close what was opened inside it too (HTML that does not nest), and open it again
        # after: Pango needs proper nesting.
        reopen = []
        while self.open:
            top = self.open.pop()
            if top is not None:
                self.parts.append(('close', top))
            if top == name:
                break
            reopen.append(top)
        for top in reversed(reopen):
            self._push(top, ())

    def result(self, markup):
        while self.open:
            top = self.open.pop()
            if top is not None:
                self.parts.append(('close', top))
        while self.parts and self.parts[-1][0] == 'break':
            self.parts.pop()
        self._trim()
        out = []
        for part in self.parts:
            kind = part[0]
            if kind == 'text':
                out.append(html.escape(part[1], quote=False) if markup else part[1])
            elif kind == 'break':
                out.append('\n' * part[1])
            elif markup and kind == 'open':
                attrs = ''.join(f' {key}="{html.escape(value, quote=True)}"'
                                for key, value in part[2])
                out.append(f'<{part[1]}{attrs}>')
            elif markup and kind == 'close':
                out.append(f'</{part[1]}>')
        text = ''.join(out)
        if markup:
            # An element left empty by the rules above says nothing.
            text = re.sub(r'<(\w+)(?: [^>]*)?></\1>', '', text)
        return text.strip()


def _convert(text, markup, links):
    if not text:
        return ''
    if not _TAG.search(text):
        # Plain text: keep its paragraphs.
        paragraphs = [' '.join(html.unescape(block).split())
                      for block in re.split(r'\n\s*\n', text)]
        joined = '\n\n'.join(paragraph for paragraph in paragraphs if paragraph)
        return html.escape(joined, quote=False) if markup else joined
    converter = _Converter(links)
    converter.feed(text)
    converter.close()
    return converter.result(markup)


def html_to_markup(text, links=True):
    """Pango markup for an HTML description (see the module)."""
    return _convert(text, True, links)


def html_to_text(text):
    """The text of an HTML description, its paragraphs kept."""
    return _convert(text, False, False)
