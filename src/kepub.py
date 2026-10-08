# SPDX-License-Identifier: GPL-2.0-or-later
# SPDX-FileCopyrightText: 2026 Jack Tully

"""EPUB to Kobo EPUB (kepub), in the manner of kepubify, for sending a book to a Kobo.

    kepub.convert(src, dest)           # writes dest (name it '….kepub.epub'); returns dest
    kepub.kepub_name('Title.epub')     # 'Title.kepub.epub'
    kepub.is_kepub(path)               # True when the book already carries koboSpans

A Kobo shows page numbers, reading statistics and its better highlighting only for a kepub:
an EPUB whose content documents mark every sentence with a `<span class="koboSpan"
id="kobo.P.S">` (P counts paragraphs, S the sentences within one) and wrap the body's
content in `<div id="book-columns"><div id="book-inner">`. This does what kepubify does for
those and nothing else (no punctuation smartening, no Adobe cleanups):

- each XHTML document of the manifest (the navigation document excepted) is parsed as XML,
  with HTML's named entities (&nbsp;, &mdash;…) turned into character references first, so
  books relying on the XHTML DTD's entities still parse; one that does not parse is copied
  unchanged;
- text is split into sentences after '.', '!', '?' or '…' (and any closing quote or
  bracket) followed by white space; a paragraph is a block element (p, h1-h6, li, div, td…);
  an image gets a span of its own; script, style, pre, SVG and MathML are left alone;
- a document that already has koboSpans is left as it is, so converting twice changes
  nothing;
- a small style block (`kobostylehacks`) keeps the wrapper divs from adding margins;
- the archive is rewritten with `mimetype` first and stored, every other entry as it was.
"""

import html.entities
import logging
import posixpath
import re
import shutil
import zipfile
from urllib.parse import unquote

from lxml import etree

log = logging.getLogger(__name__)

XHTML = 'http://www.w3.org/1999/xhtml'
OPF = 'http://www.idpf.org/2007/opf'
CONTAINER = 'urn:oasis:names:tc:opendocument:xmlns:container'
XHTML_TYPES = ('application/xhtml+xml', 'text/html')

BLOCKS = frozenset(('p', 'h1', 'h2', 'h3', 'h4', 'h5', 'h6', 'li', 'blockquote', 'div', 'td',
                    'th', 'dt', 'dd', 'figcaption', 'caption', 'section', 'article', 'aside',
                    'header', 'footer', 'address', 'center'))
SKIP = frozenset(('script', 'style', 'pre', 'code', 'svg', 'math', 'head', 'title',
                  'textarea', 'audio', 'video', 'object', 'iframe', 'noscript'))
# A sentence ends at ., !, ?, … (and closing quotes or brackets) followed by white space.
SENTENCE = re.compile(r'.*?(?:[.!?…]+["”’»)\]]*\s+|$)', re.S)
ENTITY = re.compile(r'&([A-Za-z][A-Za-z0-9]*);')
XML_ENTITIES = frozenset(('amp', 'lt', 'gt', 'quot', 'apos'))
STYLE = 'div#book-inner { margin-top: 0; margin-bottom: 0; }'
MAX_DOCUMENT = 32 * 1024 * 1024  # a larger content document is copied unconverted


def _parser():
    """A parser for the book's own XML (untrusted): no entities, DTDs or network."""
    return etree.XMLParser(resolve_entities=False, load_dtd=False, no_network=True,
                           huge_tree=False)


def kepub_name(name):
    """'Title.epub' → 'Title.kepub.epub' (a name already ending so is kept)."""
    if name.lower().endswith('.kepub.epub'):
        return name
    if name.lower().endswith('.epub'):
        name = name[:-5]
    return name + '.kepub.epub'


def is_kepub(path):
    """True when a content document of the book at `path` already carries koboSpans."""
    with zipfile.ZipFile(path) as archive:
        for name in _content_documents(archive):
            try:
                if b'koboSpan' in _read(archive, name):
                    return True
            except (KeyError, ValueError):
                continue
    return False


def _read(archive, name):
    """A member's bytes; ValueError for one that says it is larger than MAX_DOCUMENT
    (zipfile inflates no member past what it says)."""
    if archive.getinfo(name).file_size > MAX_DOCUMENT:
        raise ValueError(f'{name} is too large')
    return archive.read(name)


def convert(src, dest):
    """Write a kepub of the EPUB at `src` to `dest` and return dest. Raises
    zipfile.BadZipFile or KeyError (no container) for a file that is not an EPUB."""
    with zipfile.ZipFile(src) as archive:
        documents = set(_content_documents(archive))
        with zipfile.ZipFile(dest, 'w') as out:
            out.writestr(zipfile.ZipInfo('mimetype'), 'application/epub+zip',
                         compress_type=zipfile.ZIP_STORED)
            for info in archive.infolist():
                if info.filename == 'mimetype':
                    continue
                if info.filename in documents and info.file_size <= MAX_DOCUMENT:
                    data = convert_document(archive.read(info), info.filename)
                    out.writestr(info, data, compress_type=info.compress_type)
                    continue
                # Streamed as it is: a large member is never held in memory whole.
                copy = zipfile.ZipInfo(info.filename, date_time=info.date_time)
                copy.external_attr = info.external_attr
                copy.compress_type = info.compress_type
                large = info.file_size >= zipfile.ZIP64_LIMIT
                with archive.open(info) as reader, \
                        out.open(copy, 'w', force_zip64=large) as writer:
                    shutil.copyfileobj(reader, writer, 1 << 20)
    return dest


def _content_documents(archive):
    """The zip names of the book's XHTML documents, the navigation document excepted."""
    container = etree.fromstring(_read(archive, 'META-INF/container.xml'), _parser())
    rootfile = container.find(f'.//{{{CONTAINER}}}rootfile')
    if rootfile is None:
        return []
    opf_path = rootfile.get('full-path', '')
    try:
        opf = etree.fromstring(_read(archive, opf_path), _parser())
    except (KeyError, ValueError, etree.XMLSyntaxError):
        return []
    base = posixpath.dirname(opf_path)
    names = []
    for item in opf.iter(f'{{{OPF}}}item'):
        if item.get('media-type') not in XHTML_TYPES:
            continue
        if 'nav' in (item.get('properties') or '').split():
            continue
        href = item.get('href', '').split('#')[0]
        names.append(posixpath.normpath(posixpath.join(base, unquote(href))))
    return names


def _numeric_entities(text):
    def replace(match):
        name = match.group(1)
        if name in XML_ENTITIES:
            return match.group(0)
        codepoint = html.entities.name2codepoint.get(name)
        if codepoint is None:
            return match.group(0)
        return f'&#{codepoint};'

    return ENTITY.sub(replace, text)


def convert_document(data, name=''):
    """One XHTML document's bytes, with koboSpans and the wrapper divs; unchanged when it
    has koboSpans already or does not parse."""
    if b'koboSpan' in data:
        return data
    try:
        text = data.decode('utf-8-sig')
    except UnicodeDecodeError:
        text = None
    parser = etree.XMLParser(resolve_entities=False, load_dtd=False, no_network=True,
                             huge_tree=False, remove_blank_text=False)
    try:
        if text is not None:
            source = _numeric_entities(text)
            # An encoding declaration cannot go with a str: parse the bytes instead.
            tree = etree.fromstring(source.encode('utf-8'), parser).getroottree()
        else:
            tree = etree.fromstring(data, parser).getroottree()
    except etree.XMLSyntaxError as error:
        log.info('kepub: leaving %s as it is: %s', name, error)
        return data
    root = tree.getroot()
    namespace = etree.QName(root).namespace if isinstance(root.tag, str) else None
    body = _child(root, 'body', namespace)
    if body is None:
        return data
    _add_style(root, namespace)
    _Spanner(namespace).run(body)
    _wrap_body(body, namespace)
    return etree.tostring(tree, xml_declaration=True, encoding='utf-8')


def _tag(name, namespace):
    return f'{{{namespace}}}{name}' if namespace else name


def _local(element):
    if not isinstance(element.tag, str):
        return None  # a comment or processing instruction
    return etree.QName(element).localname.lower()


def _child(root, name, namespace):
    for child in root:
        if _local(child) == name:
            return child
    return None


def _add_style(root, namespace):
    head = _child(root, 'head', namespace)
    if head is None:
        head = etree.Element(_tag('head', namespace))
        root.insert(0, head)
    style = etree.SubElement(head, _tag('style', namespace))
    style.set('type', 'text/css')
    style.set('class', 'kobostylehacks')
    style.text = STYLE


def _wrap_body(body, namespace):
    columns = etree.Element(_tag('div', namespace))
    columns.set('id', 'book-columns')
    inner = etree.SubElement(columns, _tag('div', namespace))
    inner.set('id', 'book-inner')
    inner.text = body.text
    body.text = None
    for child in list(body):
        inner.append(child)  # moves it, its tail with it
    body.append(columns)


class _Spanner:
    """Numbers paragraphs and sentences while walking a body, wrapping text in koboSpans."""

    def __init__(self, namespace):
        self.namespace = namespace
        self.span_tag = _tag('span', namespace)
        self.paragraph = 0
        self.sentence = 0

    def run(self, body):
        self._walk(body)

    def _new_paragraph(self):
        self.paragraph += 1
        self.sentence = 0

    def _span(self, text=None):
        if self.paragraph == 0:
            self._new_paragraph()
        self.sentence += 1
        span = etree.Element(self.span_tag)
        span.set('class', 'koboSpan')
        span.set('id', f'kobo.{self.paragraph}.{self.sentence}')
        span.text = text
        return span

    def _spans(self, text):
        return [self._span(piece) for piece in SENTENCE.findall(text) if piece]

    def _walk(self, element):
        name = _local(element)
        if name in BLOCKS:
            self._new_paragraph()
        children = list(element)  # before the text's spans join them
        if element.text and element.text.strip():
            spans = self._spans(element.text)
            element.text = None
            for index, span in enumerate(spans):
                element.insert(index, span)
        for child in children:
            child_name = _local(child)
            if child_name is None or child_name in SKIP:
                pass
            elif etree.QName(child).namespace not in (self.namespace, None):
                pass  # SVG, MathML or another vocabulary
            elif child_name == 'img':
                self._new_paragraph()
                span = self._span()
                child.addprevious(span)
                tail = child.tail
                child.tail = None
                span.append(child)
                span.tail = tail
                child = span
            else:
                self._walk(child)
            if child.tail and child.tail.strip():
                spans = self._spans(child.tail)
                child.tail = None
                anchor = child
                for span in spans:
                    anchor.addnext(span)
                    anchor = span
