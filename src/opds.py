# SPDX-License-Identifier: GPL-2.0-or-later
# SPDX-FileCopyrightText: 2026 Jack Tully

"""Book catalogues on the web (OPDS 1.2, Atom, and OPDS 2.0, JSON): reading their feeds,
searching them, and downloading their books.

    feed = opds.parse(data, url, content_type='')   # Feed; raises NotOpdsError
    client = opds.Client(username='', password='')  # HTTP, Bookcase's User-Agent, timeouts
    feed = client.feed(url, refresh=False)          # fetched, parsed, kept in memory a while
    url = client.search_url(feed, 'dickens')        # None when the feed cannot be searched
    path = client.download(acquisition, folder, progress=None, cancelled=None, title='')
    acquisition = opds.best_acquisition(entry)      # the format to download, None if none
    book_id = opds.find_in_library(library, entry)  # the library's copy of it, or None
    task = opds.run_async(func, callback, *args)    # callback(result, error) on the main loop
    catalogs = opds.load_catalogs(settings.get_string('catalogs'))   # [Catalog]
    opds.dump_catalogs(catalogs)                    # the JSON for the setting
    opds.keyring.lookup(url, username)              # the password (passwords.py), or None
    opds.keyring.store(url, username, password, label)   # in a thread: the keyring may
    opds.keyring.clear(url, username)               # wait on an unlock prompt
    opds.ThumbnailCache(directory).fetch(url, client)   # a cover's file in the cache
    downloads = opds.Downloads(importer)            # downloads, then adds to the library

A Feed has a title, `navigation` (Entries leading to other feeds: `entry.href`), `books`
(Entries with acquisition links), `next`/`previous` page links, `search` (how to search it,
see search_url), `facets` ([FacetGroup(title, [Facet(title, href, active, count)])]) and an
icon. An Entry has title, authors, summary (HTML, untrusted: widgets/markup.py shows it),
categories, language, issued, publisher, series and series_index (Calibre's or schema.org's
metadata, OPDS 2's belongsTo), identifiers ({'isbn': …, 'uuid': …}), `cover` and `thumbnail`
URLs and `acquisitions` ([Acquisition(href, type, format, title, size, price, currency,
kind, drm)]). URLs are absolute (resolved against the feed's). An Acquisition is
`available` when Bookcase can take it: free (open-access, a plain acquisition or a sample),
not DRM-protected, in a format Bookcase reads; others carry what they are (a price, a loan)
for the detail sheet to show. Entries of one feed with the same title and authors are
merged (Project Gutenberg lists a book's editions apart): their acquisitions are pooled.

Search: an OpenSearch description (fetched, its Atom or OPDS URL template taken), a search
link whose href is a template with {searchTerms}, or OPDS 2's templated search link
({?query}). Optional template parameters are left empty, startPage and startIndex are 1.

The Client sends HTTP Basic credentials only to the catalogue's own host (and port), never
after a redirect to another; a 401 raises AuthError, an unreachable host OfflineError, any
other failure OpdsError (each with a sentence for the user). An HTML page that names an OPDS
feed in a <link rel="alternate"> is followed to the feed. Feeds are kept in memory for
CACHE_SECONDS (the last CACHE_SIZE of them); refresh=True asks again. download() streams the
file into `folder` as NAME.part (calling progress(done, total) and stopping, the part file
removed, when cancelled() says so) and renames it to the file name the server gave
(Content-Disposition), else one made of the title, with the format's suffix.

Downloads(importer) is the app's queue of downloads (one thread each, GObject signals on the
main loop): start(key, client, acquisition, entry, folder) downloads into `folder` (the
cache's downloads folder: the importer adds a file under the library folder in place, so the
part file stays outside it), then hands the file to importer.add_async(copy=True), which
copies it into the library folder as Author/Title.ext, and removes the download. Its
'progress' (key, fraction) and 'finished' (key, book id or 0, error message or '') signals
say how it goes; state(key) is ('downloading', fraction), ('done', book id),
('failed', message) or None; cancel(key) stops one.

Catalogues are Catalog(id, title, url, username, description) records, kept as JSON in the
`catalogs` setting; an empty setting means the built-in ones (builtin_catalogs(): free
catalogues that need no account, checked live when they were added). Passwords are in the
keyring (passwords.py, schema SECRET_SCHEMA, the account 'USER on URL'); without a keyring
service they last the session.
"""

import base64
import collections
import dataclasses
import hashlib
import html
import json
import logging
import os
import re
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
import uuid
from gettext import gettext as _

from gi.repository import GLib, GObject

from . import titles

log = logging.getLogger(__name__)

USER_AGENT = 'Bookcase/0.2 (https://github.com/Jackicus/GNOME-Bookcase)'
TIMEOUT = 20  # seconds
MAX_FEED_BYTES = 16 * 1024 * 1024
MAX_BOOK_BYTES = 2 * 1024 * 1024 * 1024
MAX_IMAGE_BYTES = 8 * 1024 * 1024
CHUNK = 64 * 1024
WEB_SCHEMES = ('http', 'https')
MAX_ALTERNATES = 3  # HTML pages followed to the feed they name
CACHE_SECONDS = 600
CACHE_SIZE = 64
RETRY_DELAY = 1.0  # seconds before asking a 502 or 504 again
SECRET_SCHEMA = 'io.github.jackicus.Bookcase.Catalog'

ATOM = '{http://www.w3.org/2005/Atom}'
OPDS_NS = '{http://opds-spec.org/2010/catalog}'
DC = '{http://purl.org/dc/elements/1.1/}'
DCTERMS = '{http://purl.org/dc/terms/}'
OPENSEARCH = '{http://a9.com/-/spec/opensearch/1.1/}'
THR = '{http://purl.org/syndication/thread/1.0}'
CALIBRE = '{http://calibre.kovidgoyal.net/2009/metadata}'
SCHEMA_ORG = '{http://schema.org/}'
XHTML = '{http://www.w3.org/1999/xhtml}'

ACQUISITION = 'http://opds-spec.org/acquisition'
IMAGE_RELS = ('http://opds-spec.org/image', 'http://opds-spec.org/cover',
              'x-stanza-cover-image')
THUMBNAIL_RELS = ('http://opds-spec.org/image/thumbnail', 'http://opds-spec.org/thumbnail',
                  'x-stanza-cover-image-thumbnail')
FACET_REL = 'http://opds-spec.org/facet'

# Media types of the formats Bookcase reads, and the order it prefers them in.
FORMAT_TYPES = {
    'application/epub+zip': 'epub',
    'application/kepub+zip': 'kepub',
    'application/x-kobo-epub+zip': 'kepub',
    'application/vnd.amazon.ebook': 'azw3',
    'application/x-mobi8-ebook': 'azw3',
    'application/x-mobipocket-ebook': 'mobi',
    'application/x-mobi': 'mobi',
    'application/fb2+zip': 'fbz',
    'application/x-zip-compressed-fb2': 'fbz',
    'application/fb2': 'fb2',
    'application/x-fictionbook+xml': 'fb2',
    'text/fb2+xml': 'fb2',
    'application/pdf': 'pdf',
    'application/vnd.comicbook+zip': 'cbz',
    'application/x-cbz': 'cbz',
    'application/vnd.comicbook-rar': 'cbr',
    'application/x-cbr': 'cbr',
    'text/plain': 'txt',
}
FORMAT_ORDER = ('epub', 'kepub', 'azw3', 'mobi', 'fb2', 'fbz', 'pdf', 'cbz', 'cbr', 'txt')
SUFFIXES = {'epub': '.epub', 'kepub': '.kepub.epub', 'azw3': '.azw3', 'mobi': '.mobi',
            'fb2': '.fb2', 'fbz': '.fb2.zip', 'pdf': '.pdf', 'cbz': '.cbz', 'cbr': '.cbr',
            'txt': '.txt'}
FORMAT_NAMES = {'epub': 'EPUB', 'kepub': 'Kobo EPUB', 'azw3': 'AZW3', 'mobi': 'MOBI',
                'fb2': 'FB2', 'fbz': 'FB2', 'pdf': 'PDF', 'cbz': 'CBZ', 'cbr': 'CBR',
                'txt': 'TXT'}
DRM_TYPES = ('application/vnd.adobe.adept+xml', 'application/vnd.readium.lcp.license.v1.0+json',
             'application/vnd.readium.license.status.v1.0+json',
             'application/vnd.librarysimplified.bearer-token+json')


class OpdsError(Exception):
    """A catalogue could not be read: the message is a sentence for the user."""


class OfflineError(OpdsError):
    pass


class AuthError(OpdsError):
    pass


class NotOpdsError(OpdsError):
    pass


class Cancelled(OpdsError):
    pass


# -- the records -----------------------------------------------------------------------------

@dataclasses.dataclass
class Link:
    href: str
    rel: str = ''
    type: str = ''
    title: str = ''


@dataclasses.dataclass
class Acquisition:
    href: str
    type: str = ''
    format: str | None = None  # FORMAT_TYPES' name, None for one Bookcase cannot read
    title: str = ''
    size: int = 0
    price: float = 0.0
    currency: str = ''
    kind: str = 'open-access'  # open-access, acquisition, sample, buy, borrow, subscribe
    drm: bool = False

    @property
    def available(self):
        return (self.format is not None and not self.drm and not self.price
                and self.kind in ('open-access', 'acquisition', 'sample'))

    @property
    def format_name(self):
        return FORMAT_NAMES.get(self.format, self.type or _('Unknown format'))


@dataclasses.dataclass
class Entry:
    title: str
    id: str = ''
    authors: list = dataclasses.field(default_factory=list)
    summary: str = ''  # HTML
    categories: list = dataclasses.field(default_factory=list)
    language: str = ''
    issued: str = ''
    publisher: str = ''
    series: str = ''
    series_index: float = 0.0
    identifiers: dict = dataclasses.field(default_factory=dict)
    cover: str = ''
    thumbnail: str = ''
    href: str = ''  # a navigation entry's feed
    acquisitions: list = dataclasses.field(default_factory=list)
    count: int | None = None  # a navigation entry's number of items, when the feed says

    @property
    def key(self):
        """What a download of it is known by."""
        if self.id:
            return self.id
        if self.acquisitions:
            return self.acquisitions[0].href
        return self.href or self.title

    @property
    def author(self):
        return ', '.join(self.authors)

    @property
    def is_book(self):
        return bool(self.acquisitions)

    @property
    def looks_like_book(self):
        """A navigation entry that is a book (an author and a cover of its own, leading to
        the book's feed): shown with the books."""
        return bool(self.authors and self.thumbnail and not self.thumbnail.startswith('data:'))


@dataclasses.dataclass
class Facet:
    title: str
    href: str
    active: bool = False
    count: int | None = None


@dataclasses.dataclass
class FacetGroup:
    title: str
    facets: list = dataclasses.field(default_factory=list)

    @property
    def active(self):
        return next((facet for facet in self.facets if facet.active), None)


@dataclasses.dataclass
class Search:
    kind: str  # 'opensearch' (href is the description), 'template', 'opds2'
    href: str


@dataclasses.dataclass
class Feed:
    url: str
    title: str = ''
    subtitle: str = ''
    icon: str = ''
    navigation: list = dataclasses.field(default_factory=list)
    books: list = dataclasses.field(default_factory=list)
    next: str = ''
    previous: str = ''
    search: Search | None = None
    facets: list = dataclasses.field(default_factory=list)
    web: str = ''  # its page on the web, when it links one
    total: int | None = None

    @property
    def single_book(self):
        """The one book this feed is about (a book's own feed), or None."""
        if len(self.books) == 1 and not self.navigation and not self.next:
            return self.books[0]
        return None


# -- parsing ---------------------------------------------------------------------------------

def parse(data, url, content_type=''):
    """A Feed from a feed's bytes; NotOpdsError when they are not an OPDS feed."""
    if isinstance(data, str):
        data = data.encode('utf-8')
    head = data.lstrip()[:1]
    if 'json' in (content_type or '') or head in (b'{', b'['):
        return parse_json(data, url)
    return parse_atom(data, url)


def _resolve(base, href):
    if not href:
        return ''
    href = href.strip()
    if href.startswith('data:'):
        return href
    return urllib.parse.urljoin(base, href)


def _xml_root(data):
    from lxml import etree

    if isinstance(data, str):
        data = data.encode('utf-8')
    parser = etree.XMLParser(resolve_entities=False, no_network=True, huge_tree=False,
                             recover=False)
    try:
        return etree.fromstring(data, parser)
    except etree.XMLSyntaxError as error:
        raise NotOpdsError(_('This address is not a book catalogue')) from error


def _text(element):
    if element is None:
        return ''
    return ' '.join(''.join(element.itertext()).split())


def _child_text(element, *names):
    for name in names:
        child = element.find(name)
        if child is not None and _text(child):
            return _text(child)
    return ''


def _html_of(element):
    """An Atom text construct (title, summary, content) as HTML."""
    if element is None:
        return ''
    kind = (element.get('type') or 'text').lower()
    if kind in ('xhtml', 'text/xhtml', 'application/xhtml+xml'):
        return html.escape(element.text or '', quote=False) + ''.join(
            _xhtml(child) for child in element)
    if kind in ('html', 'text/html'):
        return element.text or ''
    text = ''.join(element.itertext()).strip()
    return '<br>'.join(html.escape(line) for line in text.splitlines())


def _xhtml(node):
    """An XHTML element (and its tail) as HTML without namespaces; only links keep an
    attribute."""
    tail = html.escape(node.tail or '', quote=False)
    if not isinstance(node.tag, str):
        return tail
    name = node.tag.rsplit('}', 1)[-1]
    attributes = ''
    if name == 'a' and node.get('href'):
        attributes = f' href="{html.escape(node.get("href"))}"'
    inner = html.escape(node.text or '', quote=False) + ''.join(_xhtml(child) for child in node)
    return f'<{name}{attributes}>{inner}</{name}>{tail}'


def _float(text):
    try:
        value = float(str(text).strip())
    except (TypeError, ValueError):
        return 0.0
    return value if value == value and abs(value) < 1e9 else 0.0


def _date(text):
    match = re.match(r'\s*(\d{4}(?:-\d{2}(?:-\d{2})?)?)', text or '')
    return match.group(1) if match else ''


def _identifier(value):
    """('isbn' | 'uuid' | '', value) of an identifier or an entry's id."""
    value = (value or '').strip()
    low = value.lower()
    if low.startswith('urn:isbn:'):
        return 'isbn', re.sub(r'[^0-9Xx]', '', value[9:]).upper()
    if low.startswith('isbn:'):
        return 'isbn', re.sub(r'[^0-9Xx]', '', value[5:]).upper()
    if low.startswith('urn:uuid:'):
        return 'uuid', value[9:].lower()
    if re.fullmatch(r'[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}', low):
        return 'uuid', low
    return '', value


def _format_of(media_type, href=''):
    base = (media_type or '').split(';')[0].strip().lower()
    format = FORMAT_TYPES.get(base)
    path = urllib.parse.urlsplit(href).path.lower() if href else ''
    if format == 'mobi' and path.endswith('.azw3'):
        return 'azw3'
    if format == 'epub' and path.endswith('.kepub.epub'):
        return 'kepub'
    if format is None and base in ('application/zip', 'application/octet-stream', ''):
        for suffix, name in (('.kepub.epub', 'kepub'), ('.epub', 'epub'), ('.cbz', 'cbz'),
                             ('.pdf', 'pdf'), ('.azw3', 'azw3'), ('.mobi', 'mobi'),
                             ('.fb2.zip', 'fbz'), ('.fb2', 'fb2')):
            if path.endswith(suffix):
                return name
    return format


def _acquisition_kind(rel):
    if rel == ACQUISITION:
        return 'acquisition'
    return rel[len(ACQUISITION) + 1:] if rel.startswith(ACQUISITION + '/') else rel


def _is_feed_type(media_type):
    low = (media_type or '').lower()
    if 'type=entry' in low:
        return False
    return 'atom+xml' in low or 'opds+json' in low or 'opds-catalog' in low


def parse_atom(data, url):
    root = _xml_root(data)
    if root.tag != ATOM + 'feed':
        if root.tag == ATOM + 'entry':
            feed = Feed(url=url, title=_child_text(root, ATOM + 'title'))
            entry = _atom_entry(root, url)
            (feed.books if entry.is_book else feed.navigation).append(entry)
            return feed
        raise NotOpdsError(_('This address is not a book catalogue'))
    feed = Feed(url=url, title=_child_text(root, ATOM + 'title'),
                subtitle=_child_text(root, ATOM + 'subtitle'),
                icon=_resolve(url, _child_text(root, ATOM + 'icon', ATOM + 'logo')))
    total = _child_text(root, OPENSEARCH + 'totalResults')
    feed.total = int(total) if total.isdigit() else None
    groups = collections.OrderedDict()
    for element in root.findall(ATOM + 'link'):
        rel = element.get('rel') or 'alternate'
        href = _resolve(url, element.get('href'))
        media_type = element.get('type') or ''
        if not href:
            continue
        if rel == 'next' and not feed.next:
            feed.next = href
        elif rel in ('previous', 'prev') and not feed.previous:
            feed.previous = href
        elif rel == 'search' and feed.search is None:
            if 'opensearchdescription' in media_type:
                feed.search = Search('opensearch', href)
            elif '{searchTerms}' in urllib.parse.unquote(href):
                feed.search = Search('template', urllib.parse.unquote(href))
        elif rel == FACET_REL:
            group = element.get(OPDS_NS + 'facetGroup') or _('Filter')
            count = element.get(THR + 'count')
            groups.setdefault(group, FacetGroup(group)).facets.append(Facet(
                element.get('title') or href, href,
                (element.get(OPDS_NS + 'activeFacet') or '').lower() == 'true',
                int(count) if count and count.isdigit() else None))
        elif rel == 'alternate' and 'html' in media_type and not feed.web:
            feed.web = href
    feed.facets = list(groups.values())
    for element in root.findall(ATOM + 'entry'):
        entry = _atom_entry(element, url)
        if entry.is_book:
            feed.books.append(entry)
        elif entry.href:
            feed.navigation.append(entry)
    feed.books = merge_variants(feed.books)
    return feed


def _atom_entry(element, base):
    title_element = element.find(ATOM + 'title')
    entry = Entry(title=_text(title_element) or _('Untitled'),
                  id=_child_text(element, ATOM + 'id'))
    for author in element.findall(ATOM + 'author'):
        name = _child_text(author, ATOM + 'name')
        if name:
            entry.authors.append(name)
    if not entry.authors:
        entry.authors = [_text(e) for e in element.findall(DC + 'creator') if _text(e)]
    content = element.find(ATOM + 'content')
    summary = element.find(ATOM + 'summary')
    entry.summary = _html_of(content) if _text(content) else _html_of(summary)
    for category in element.findall(ATOM + 'category'):
        name = category.get('label') or category.get('term')
        if name and name not in entry.categories:
            entry.categories.append(name)
    entry.language = _child_text(element, DCTERMS + 'language', DC + 'language')
    entry.issued = _date(_child_text(element, DCTERMS + 'issued', DC + 'issued',
                                     DCTERMS + 'date', DC + 'date', ATOM + 'published'))
    entry.publisher = _child_text(element, DCTERMS + 'publisher', DC + 'publisher')
    _series(element, entry)
    for identifier in (element.findall(DC + 'identifier')
                       + element.findall(DCTERMS + 'identifier')):
        kind, value = _identifier(_text(identifier))
        if kind and kind not in entry.identifiers:
            entry.identifiers[kind] = value
    kind, value = _identifier(entry.id)
    if kind and kind not in entry.identifiers:
        entry.identifiers[kind] = value
    for link in element.findall(ATOM + 'link'):
        _atom_link(link, base, entry)
    if not entry.href and not entry.acquisitions:
        # ManyBooks links its books' feeds as text/html: a link into an /opds/ path is one.
        for link in element.findall(ATOM + 'link'):
            href = _resolve(base, link.get('href'))
            if ((link.get('rel') or 'alternate') == 'alternate'
                    and 'type=entry' not in (link.get('type') or '')
                    and '/opds' in urllib.parse.urlsplit(href).path):
                entry.href = href
                break
    if not entry.thumbnail:
        entry.thumbnail = entry.cover
    if not entry.cover:
        entry.cover = entry.thumbnail
    return entry


def _series(element, entry):
    series = _child_text(element, CALIBRE + 'series', SCHEMA_ORG + 'Series')
    index = _child_text(element, CALIBRE + 'series_index')
    if not series:
        node = element.find(SCHEMA_ORG + 'Series')
        if node is not None:
            series = node.get(SCHEMA_ORG + 'name') or node.get('name') or ''
            index = node.get(SCHEMA_ORG + 'position') or node.get('position') or ''
    entry.series = series
    entry.series_index = _float(index)


def _atom_link(link, base, entry):
    rel = (link.get('rel') or 'alternate').strip()
    href = _resolve(base, link.get('href'))
    media_type = link.get('type') or ''
    if not href:
        return
    if rel.startswith(ACQUISITION):
        indirect = [child.get('type') or '' for child in link.iter(OPDS_NS + 'indirectAcquisition')]
        price = link.find(OPDS_NS + 'price')
        final = indirect[-1] if indirect else media_type
        length = link.get('length') or ''
        entry.acquisitions.append(Acquisition(
            href=href, type=final, format=_format_of(final, href),
            title=link.get('title') or '', size=int(length) if length.isdigit() else 0,
            price=_float(_text(price)) if price is not None else 0.0,
            currency=price.get('currencycode', '') if price is not None else '',
            kind=_acquisition_kind(rel),
            drm=any(t.split(';')[0] in DRM_TYPES for t in [media_type] + indirect)))
    elif rel in IMAGE_RELS:
        entry.cover = entry.cover or href
    elif rel in THUMBNAIL_RELS:
        entry.thumbnail = entry.thumbnail or href
    elif (_is_feed_type(media_type) and not entry.href
          and rel not in ('self', 'related', 'search', 'start', 'up')):
        entry.href = href
        count = link.get(THR + 'count')
        entry.count = int(count) if count and count.isdigit() else None


def merge_variants(books):
    """Pool the acquisitions of entries with the same title and authors (a book's editions
    listed apart), keeping the first entry's metadata and filling its gaps."""
    merged = []
    seen = {}
    for entry in books:
        key = (titles.title_key(entry.title), tuple(a.casefold() for a in entry.authors))
        first = seen.get(key)
        if first is None or not key[0]:
            seen[key] = entry
            merged.append(entry)
            continue
        known = {acquisition.href for acquisition in first.acquisitions}
        first.acquisitions.extend(a for a in entry.acquisitions if a.href not in known)
        for name in ('summary', 'cover', 'thumbnail', 'language', 'issued', 'publisher',
                     'series'):
            if not getattr(first, name):
                setattr(first, name, getattr(entry, name))
        for kind, value in entry.identifiers.items():
            first.identifiers.setdefault(kind, value)
    return merged


# -- OPDS 2 ----------------------------------------------------------------------------------

def _rels(link):
    rel = link.get('rel') or ''
    return rel if isinstance(rel, list) else [rel]


def _names(value):
    """OPDS 2's contributors and subjects: a string, an object with a name, or a list."""
    if value is None:
        return []
    if isinstance(value, (str, dict)):
        value = [value]
    names = []
    for item in value:
        if isinstance(item, str):
            name = item
        elif isinstance(item, dict):
            name = item.get('name')
            if isinstance(name, dict):  # a language map
                name = next(iter(name.values()), '')
        else:
            continue
        if name and str(name).strip():
            names.append(str(name).strip())
    return names


def _string(value):
    if isinstance(value, dict):  # a language map
        return str(next(iter(value.values()), ''))
    if isinstance(value, list):
        return str(value[0]) if value else ''
    return str(value) if value is not None else ''


def parse_json(data, url):
    try:
        document = json.loads(data)
    except (ValueError, UnicodeDecodeError) as error:
        raise NotOpdsError(_('This address is not a book catalogue')) from error
    if not isinstance(document, dict):
        raise NotOpdsError(_('This address is not a book catalogue'))
    if 'metadata' in document and not any(
            key in document for key in ('navigation', 'publications', 'groups', 'links')):
        raise NotOpdsError(_('This address is not a book catalogue'))
    if not any(key in document for key in ('navigation', 'publications', 'groups')):
        if 'metadata' in document and 'links' in document and _is_publication(document):
            feed = Feed(url=url, title=_string(document['metadata'].get('title')))
            feed.books.append(_publication(document, url))
            return feed
        raise NotOpdsError(_('This address is not a book catalogue'))
    metadata = document.get('metadata') or {}
    feed = Feed(url=url, title=_string(metadata.get('title')),
                subtitle=_string(metadata.get('subtitle')))
    total = metadata.get('numberOfItems')
    feed.total = total if isinstance(total, int) else None
    for link in document.get('links') or []:
        if not isinstance(link, dict) or not link.get('href'):
            continue
        rels = _rels(link)
        href = link['href'] if link.get('templated') else _resolve(url, link['href'])
        if 'next' in rels and not feed.next:
            feed.next = href
        elif ('previous' in rels or 'prev' in rels) and not feed.previous:
            feed.previous = href
        elif 'search' in rels and feed.search is None:
            if link.get('templated'):
                feed.search = Search('opds2', _resolve_template(url, link['href']))
            elif 'opensearchdescription' in (link.get('type') or ''):
                feed.search = Search('opensearch', href)
        elif 'alternate' in rels and 'html' in (link.get('type') or '') and not feed.web:
            feed.web = href
        elif 'icon' in rels and (link.get('type') or '').startswith('image/'):
            feed.icon = feed.icon or href
    for item in document.get('navigation') or []:
        entry = _navigation(item, url)
        if entry is not None:
            feed.navigation.append(entry)
    for item in document.get('publications') or []:
        if isinstance(item, dict):
            feed.books.append(_publication(item, url))
    for group in document.get('groups') or []:
        if not isinstance(group, dict):
            continue
        group_title = _string((group.get('metadata') or {}).get('title'))
        for item in group.get('navigation') or []:
            entry = _navigation(item, url)
            if entry is not None:
                feed.navigation.append(entry)
        if group.get('publications'):
            more = next((link for link in group.get('links') or []
                         if isinstance(link, dict) and 'self' in _rels(link)), None)
            if more is not None and group_title:
                feed.navigation.append(Entry(title=group_title,
                                             href=_resolve(url, more.get('href'))))
            feed.books.extend(_publication(item, url) for item in group['publications']
                              if isinstance(item, dict))
    for group in document.get('facets') or []:
        if not isinstance(group, dict):
            continue
        facets = FacetGroup(_string((group.get('metadata') or {}).get('title'))
                            or _('Filter'))
        for link in group.get('links') or []:
            if isinstance(link, dict) and link.get('href'):
                count = (link.get('properties') or {}).get('numberOfItems')
                facets.facets.append(Facet(link.get('title') or link['href'],
                                           _resolve(url, link['href']),
                                           'self' in _rels(link),
                                           count if isinstance(count, int) else None))
        if facets.facets:
            feed.facets.append(facets)
    feed.books = merge_variants(feed.books)
    return feed


def _is_publication(document):
    return any(rel.startswith(ACQUISITION) for link in document.get('links') or []
               if isinstance(link, dict) for rel in _rels(link))


def _resolve_template(base, href):
    """A templated href made absolute without touching its {…} expressions."""
    head, brace, tail = href.partition('{')
    return _resolve(base, head) + brace + tail if head else href


def _navigation(item, base):
    if not isinstance(item, dict) or not item.get('href'):
        return None
    count = (item.get('properties') or {}).get('numberOfItems')
    return Entry(title=item.get('title') or item['href'], href=_resolve(base, item['href']),
                 count=count if isinstance(count, int) else None)


def _publication(item, base):
    metadata = item.get('metadata') or {}
    entry = Entry(title=_string(metadata.get('title')) or _('Untitled'),
                  id=_string(metadata.get('identifier')))
    entry.authors = _names(metadata.get('author')) or _names(metadata.get('contributor'))
    description = _string(metadata.get('description'))
    entry.summary = description if '<' in description else '<br>'.join(
        html.escape(line) for line in description.splitlines())
    entry.categories = _names(metadata.get('subject'))
    entry.language = _string(metadata.get('language'))
    entry.issued = _date(_string(metadata.get('published')))
    entry.publisher = ', '.join(_names(metadata.get('publisher')))
    belongs = metadata.get('belongsTo') or {}
    series = belongs.get('series') if isinstance(belongs, dict) else None
    if isinstance(series, list):
        series = series[0] if series else None
    if isinstance(series, str):
        entry.series = series
    elif isinstance(series, dict):
        entry.series = _string(series.get('name'))
        entry.series_index = _float(series.get('position'))
    kind, value = _identifier(entry.id)
    if kind:
        entry.identifiers[kind] = value
    for link in item.get('links') or []:
        if not isinstance(link, dict) or not link.get('href'):
            continue
        rels = _rels(link)
        rel = next((r for r in rels if r.startswith(ACQUISITION)), None)
        href = _resolve(base, link['href'])
        if rel is not None:
            properties = link.get('properties') or {}
            indirect = []
            chain = properties.get('indirectAcquisition') or []
            while isinstance(chain, list) and chain and isinstance(chain[0], dict):
                indirect.append(chain[0].get('type') or '')
                chain = chain[0].get('child') or []
            price = properties.get('price') or {}
            final = indirect[-1] if indirect else (link.get('type') or '')
            entry.acquisitions.append(Acquisition(
                href=href, type=final, format=_format_of(final, href),
                title=link.get('title') or '',
                size=link.get('length') if isinstance(link.get('length'), int) else 0,
                price=_float(price.get('value')) if isinstance(price, dict) else 0.0,
                currency=price.get('currency', '') if isinstance(price, dict) else '',
                kind=_acquisition_kind(rel),
                drm=any(t.split(';')[0] in DRM_TYPES
                        for t in [link.get('type') or ''] + indirect)))
        elif any(r in IMAGE_RELS for r in rels):
            entry.cover = entry.cover or href
        elif any(r in THUMBNAIL_RELS for r in rels):
            entry.thumbnail = entry.thumbnail or href
    images = [image for image in item.get('images') or []
              if isinstance(image, dict) and image.get('href')]
    if images:
        by_width = sorted(images, key=lambda image: image.get('width') or 0)
        entry.cover = entry.cover or _resolve(base, by_width[-1]['href'])
        entry.thumbnail = entry.thumbnail or _resolve(
            base, next((image['href'] for image in by_width
                        if (image.get('width') or 0) >= 200), by_width[-1]['href']))
    if not entry.thumbnail:
        entry.thumbnail = entry.cover
    return entry


# -- searching -------------------------------------------------------------------------------

def parse_opensearch(data, base):
    """The URL template of an OpenSearch description that returns a feed (Atom or OPDS 2)."""
    root = _xml_root(data)
    best = None
    for element in root.iter(OPENSEARCH + 'Url'):
        media_type = (element.get('type') or '').lower()
        template = element.get('template') or ''
        if not template or ('atom' not in media_type and 'opds' not in media_type):
            continue
        rank = 0 if 'opds-catalog' in media_type or 'opds+json' in media_type else 1
        if best is None or rank < best[0]:
            best = (rank, template)
    if best is None:
        raise NotOpdsError(_('This catalogue cannot be searched'))
    return _resolve_template(base, best[1])


def expand_template(template, terms):
    """An OpenSearch template ({searchTerms}, {count?}…) or an OPDS 2 one ({?query}) filled
    in with the search terms."""
    quoted = urllib.parse.quote(terms.strip(), safe='')

    def replace(match):
        body = match.group(1)
        if body[:1] in '?&':
            names = [name.strip() for name in body[1:].split(',')]
            if 'query' in names:
                return f'{body[0]}query={quoted}'
            if 'searchTerms' in names:
                return f'{body[0]}searchTerms={quoted}'
            return ''
        name = body.rstrip('?')
        if name in ('searchTerms', 'query'):
            return quoted
        if name in ('startPage', 'startIndex') and not body.endswith('?'):
            return '1'
        if name in ('inputEncoding', 'outputEncoding') and not body.endswith('?'):
            return 'UTF-8'
        if name == 'language' and not body.endswith('?'):
            return '*'
        return ''

    return re.sub(r'\{([^{}]*)\}', replace, template)


# -- choosing ------------------------------------------------------------------------------

def _variant_rank(acquisition):
    """Within a format: an edition with images before one without, a current one before
    one for older readers (Project Gutenberg's titles say which)."""
    title = acquisition.title.lower()
    return (('no images' in title or 'noimages' in acquisition.href) * 2
            + ('older' in title))


def available_acquisitions(entry):
    """The acquisitions Bookcase can take, best first."""
    found = [a for a in entry.acquisitions if a.available]
    found.sort(key=lambda a: (a.kind == 'sample', FORMAT_ORDER.index(a.format),
                              _variant_rank(a)))
    return found


def best_acquisition(entry):
    found = available_acquisitions(entry)
    return found[0] if found else None


def find_in_library(library, entry):
    """The id of the library's book that is this entry (an ISBN or UUID in common, else the
    same title and an author in common), or None."""
    for kind in ('uuid', 'isbn'):
        value = entry.identifiers.get(kind)
        if value:
            found = library.find_by_identifier(kind, value)
            if found is not None:
                return found
    if entry.title:
        found = library.find_similar(entry.title, entry.authors)
        if found:
            return found[0]
    return None


# -- catalogues -------------------------------------------------------------------------------

@dataclasses.dataclass
class Catalog:
    id: str
    title: str
    url: str
    username: str = ''
    description: str = ''


def builtin_catalogs():
    """Free catalogues that need no account (each checked live when it was added here)."""
    return [
        Catalog('gutenberg', 'Project Gutenberg', 'https://www.gutenberg.org/ebooks.opds/',
                description=_('Over 75,000 free books whose copyright has expired')),
        Catalog('manybooks', 'ManyBooks', 'https://manybooks.net/opds',
                description=_('Free books, classics and new writers')),
        Catalog('wolnelektury', 'Wolne Lektury', 'https://wolnelektury.pl/opds/',
                description=_('Free books in Polish')),
    ]


def load_catalogs(text):
    if not (text or '').strip():
        return builtin_catalogs()
    try:
        items = json.loads(text)
    except ValueError:
        log.warning('the catalogs setting is not JSON; using the built-in catalogues')
        return builtin_catalogs()
    builtin = {catalog.id: catalog for catalog in builtin_catalogs()}
    catalogs = []
    for item in items if isinstance(items, list) else []:
        if not isinstance(item, dict) or not item.get('url'):
            continue
        known = builtin.get(item.get('id'))
        catalogs.append(Catalog(
            id=str(item.get('id') or new_id()), title=str(item.get('title') or item['url']),
            url=str(item['url']), username=str(item.get('username') or ''),
            description=(known.description if known is not None and not item.get(
                'description') else str(item.get('description') or ''))))
    return catalogs


def dump_catalogs(catalogs):
    builtin = {catalog.id: catalog for catalog in builtin_catalogs()}
    items = []
    for catalog in catalogs:
        item = {'id': catalog.id, 'title': catalog.title, 'url': catalog.url}
        if catalog.username:
            item['username'] = catalog.username
        known = builtin.get(catalog.id)
        if catalog.description and (known is None or known.description != catalog.description):
            item['description'] = catalog.description
        items.append(item)
    return json.dumps(items, ensure_ascii=False)


def new_id():
    return uuid.uuid4().hex[:12]


def normalise_url(text):
    """What the user typed as a catalogue's address, made a URL: https:// added when no
    scheme is given."""
    text = (text or '').strip()
    if text and '://' not in text:
        text = 'https://' + text
    return text


# -- the keyring -------------------------------------------------------------------------------

class CatalogKeyring:
    """Catalogue passwords in the keyring (passwords.py, schema SECRET_SCHEMA), by the
    catalogue's address and user name. Every call may wait on the keyring (an unlock
    prompt): call them from a thread. Without a keyring, a password lasts the session."""

    def __init__(self, backend=None):
        self._backend = backend
        self._session = {}

    @property
    def backend(self):
        if self._backend is None:
            from . import passwords

            self._backend = passwords.Keyring(SECRET_SCHEMA)
        return self._backend

    @staticmethod
    def account(url, username):
        return f'{username} on {url}'

    def lookup(self, url, username):
        remembered = self._session.get((url, username))
        if remembered is not None:
            return remembered
        return self.backend.lookup(self.account(url, username))

    def store(self, url, username, password, label=''):
        """True when the keyring has it; False when it lasts only the session."""
        self._session[(url, username)] = password
        from . import passwords

        try:
            return bool(self.backend.store(self.account(url, username), label or url,
                                           password))
        except passwords.KeyringError as error:
            log.warning('a catalogue password stays for this session only: %s', error)
            return False

    def clear(self, url, username):
        self._session.pop((url, username), None)
        self.backend.clear(self.account(url, username))


def memory_keyring():
    """A CatalogKeyring that keeps passwords in memory (tests, screenshots)."""
    from . import passwords

    return CatalogKeyring(passwords.MemoryKeyring())


keyring = CatalogKeyring()


# -- HTTP ------------------------------------------------------------------------------------

def _origin(url):
    parts = urllib.parse.urlsplit(url)
    port = parts.port or {'http': 80, 'https': 443}.get(parts.scheme)
    return (parts.scheme, (parts.hostname or '').lower(), port)


class _Redirect(urllib.request.HTTPRedirectHandler):
    """Redirects drop the credentials; the client adds them again for the catalogue's own
    host."""

    def redirect_request(self, req, fp, code, msg, headers, newurl):
        target = urllib.parse.urljoin(req.full_url, newurl)
        if urllib.parse.urlsplit(target).scheme.lower() not in WEB_SCHEMES:
            raise urllib.error.HTTPError(target, code, msg, headers, fp)
        new = super().redirect_request(req, fp, code, msg, headers, newurl)
        if new is not None:
            new.remove_header('Authorization')
        return new


class _Auth(urllib.request.BaseHandler):
    handler_order = 900

    def __init__(self, origin, header):
        self.origin = origin
        self.header = header

    def _add(self, request):
        if self.header and _origin(request.full_url) == self.origin:
            request.add_unredirected_header('Authorization', self.header)
        return request

    http_request = _add
    https_request = _add


@dataclasses.dataclass
class Response:
    url: str
    content_type: str
    headers: object
    stream: object = None
    data: bytes = b''


def _error_message(error, url):
    host = urllib.parse.urlsplit(url).hostname or url
    if isinstance(error, urllib.error.HTTPError):
        if error.code == 401:
            return AuthError(_('{host} asks for a user name and password').format(host=host))
        if error.code == 403:
            return OpdsError(_('{host} refused access').format(host=host))
        if error.code == 404:
            return OpdsError(_('{host} has nothing at this address').format(host=host))
        if error.code in (429, 503):
            return OpdsError(_('{host} is busy; try again in a moment').format(host=host))
        return OpdsError(_('{host} answered with an error ({code})').format(
            host=host, code=error.code))
    reason = getattr(error, 'reason', error)
    if isinstance(reason, (TimeoutError,)) or 'timed out' in str(reason):
        return OfflineError(_('{host} did not answer in time').format(host=host))
    return OfflineError(_('Could not reach {host}. Check the address and the network '
                          'connection.').format(host=host))


class Client:
    """HTTP for one catalogue: its credentials, sent only to its own host."""

    def __init__(self, username='', password='', origin_url='', timeout=TIMEOUT,
                 user_agent=USER_AGENT):
        self.username = username
        self.password = password
        self.timeout = timeout
        self.user_agent = user_agent
        header = ''
        if username:
            token = base64.b64encode(f'{username}:{password or ""}'.encode())
            header = 'Basic ' + token.decode('ascii')
        self._auth = _Auth(_origin(origin_url) if origin_url else None, header)
        self._opener = urllib.request.build_opener(_Redirect(), self._auth)
        self._cache = collections.OrderedDict()  # url -> (time, Feed)
        self._templates = {}  # OpenSearch description url -> template
        self._lock = threading.Lock()

    @classmethod
    def for_catalog(cls, catalog, password=None):
        """The client of a catalogue, its password looked up in the keyring (in a worker
        thread: the keyring may have to be unlocked first)."""
        if catalog.username and password is None:
            password = keyring.lookup(catalog.url, catalog.username) or ''
        return cls(catalog.username, password or '', catalog.url)

    def open(self, url, accept=''):
        """A Response with an open stream (the caller reads and closes it)."""
        if url.startswith('data:'):
            return Response(url, *_data_url(url))
        if urllib.parse.urlsplit(url).scheme.lower() not in WEB_SCHEMES:
            # A feed's link to file:// or ftp:// is never followed: a catalogue reads
            # nothing on this computer.
            raise OpdsError(_('This address is not a book catalogue'))
        request = urllib.request.Request(url, headers={
            'User-Agent': self.user_agent,
            'Accept': accept or '*/*',
        })
        if self._auth.origin is None and self._auth.header:
            self._auth.origin = _origin(url)
        for attempt in range(2):
            try:
                stream = self._opener.open(request, timeout=self.timeout)
                break
            except urllib.error.HTTPError as error:
                error.close()
                if error.code in (502, 504) and attempt == 0:
                    time.sleep(RETRY_DELAY)  # a busy gateway: once more
                    continue
                raise _error_message(error, url) from error
            except (urllib.error.URLError, TimeoutError, OSError) as error:
                raise _error_message(error, url) from error
        return Response(stream.geturl(), stream.headers.get('Content-Type', ''),
                        stream.headers, stream)

    def get(self, url, accept='', limit=MAX_FEED_BYTES):
        response = self.open(url, accept)
        if response.stream is None:
            return response
        try:
            data = response.stream.read(limit + 1)
        except (TimeoutError, OSError) as error:
            raise _error_message(error, url) from error
        finally:
            response.stream.close()
        if len(data) > limit:
            raise OpdsError(_('The catalogue sent more than Bookcase can read'))
        response.data = data
        return response

    def feed(self, url, refresh=False, _alternates=0):
        with self._lock:
            cached = self._cache.get(url)
        if cached is not None and not refresh and time.monotonic() - cached[0] < CACHE_SECONDS:
            return cached[1]
        response = self.get(url, accept='application/atom+xml;profile=opds-catalog, '
                                        'application/opds+json, application/atom+xml;q=0.9, '
                                        'application/json;q=0.8, */*;q=0.5')
        try:
            feed = parse(response.data, response.url, response.content_type)
        except NotOpdsError:
            alternate = _html_alternate(response.data, response.url)
            if alternate is None or alternate == url or _alternates >= MAX_ALTERNATES:
                raise
            return self.feed(alternate, refresh, _alternates + 1)
        with self._lock:
            self._cache[url] = (time.monotonic(), feed)
            self._cache.move_to_end(url)
            while len(self._cache) > CACHE_SIZE:
                self._cache.popitem(last=False)
        return feed

    def forget(self):
        with self._lock:
            self._cache.clear()

    def search_url(self, feed, terms):
        """The URL of the search for `terms` in a feed's catalogue, or None."""
        search = feed.search
        if search is None or not terms.strip():
            return None
        if search.kind == 'opensearch':
            template = self._templates.get(search.href)
            if template is None:
                response = self.get(search.href, accept='application/opensearchdescription+xml')
                template = parse_opensearch(response.data, response.url)
                self._templates[search.href] = template
        else:
            template = search.href
        return expand_template(template, terms)

    def download(self, acquisition, folder, progress=None, cancelled=None, title=''):
        """Stream a book into `folder`; the file's path."""
        response = self.open(acquisition.href,
                             accept=(acquisition.type or 'application/epub+zip') + ', */*;q=0.5')
        os.makedirs(folder, exist_ok=True)
        name = download_name(response, acquisition, title)
        part = os.path.join(folder, f'.{uuid.uuid4().hex[:8]}.part')
        try:
            content_type = response.content_type.split(';')[0].strip().lower()
            if content_type in ('text/html', 'application/xhtml+xml') and \
                    acquisition.format not in ('txt',):
                raise OpdsError(_('The catalogue sent a web page, not a book. It may need '
                                  'you to sign in on its website.'))
            total = int(response.headers.get('Content-Length') or 0) if response.headers else 0
            done = 0
            with open(part, 'wb') as file:
                while True:
                    if cancelled is not None and cancelled():
                        raise Cancelled(_('Download cancelled'))
                    try:
                        chunk = response.stream.read(CHUNK) if response.stream else b''
                    except (TimeoutError, OSError) as error:
                        raise _error_message(error, acquisition.href) from error
                    if not chunk:
                        break
                    file.write(chunk)
                    done += len(chunk)
                    if done > MAX_BOOK_BYTES:
                        raise OpdsError(_('The file is too large'))
                    if progress is not None:
                        progress(done, total or acquisition.size)
                if response.stream is None:
                    file.write(response.data)
            path = _free_path(os.path.join(folder, name))
            os.replace(part, path)
            return path
        except BaseException:
            if os.path.exists(part):
                os.unlink(part)
            raise
        finally:
            if response.stream is not None:
                response.stream.close()


def _data_url(url):
    header, _comma, payload = url.partition(',')
    media_type = header[5:].split(';')[0] or 'text/plain'
    if header.endswith(';base64'):
        data = base64.b64decode(payload + '=' * (-len(payload) % 4))
    else:
        data = urllib.parse.unquote_to_bytes(payload)
    return media_type, {}, None, data


def _html_alternate(data, base):
    """The OPDS feed an HTML page names in a <link rel="alternate">, or None."""
    text = data[:200_000].decode('utf-8', 'replace')
    for tag in re.findall(r'<link\b[^>]*>', text, re.IGNORECASE):
        attributes = dict((name.lower(), value) for name, _q, value in re.findall(
            r'([\w-]+)\s*=\s*(["\'])(.*?)\2', tag))
        if 'alternate' in attributes.get('rel', '').lower() and (
                'opds' in attributes.get('type', '') or 'atom+xml' in attributes.get('type', '')):
            return _resolve(base, html.unescape(attributes.get('href', '')))
    return None


def _free_path(path):
    if not os.path.exists(path):
        return path
    stem, suffix = _split_suffix(os.path.basename(path))
    folder = os.path.dirname(path)
    for number in range(2, 1000):
        candidate = os.path.join(folder, f'{stem} ({number}){suffix}')
        if not os.path.exists(candidate):
            return candidate
    return os.path.join(folder, f'{stem} {uuid.uuid4().hex[:6]}{suffix}')


def _split_suffix(name):
    low = name.lower()
    for suffix in ('.kepub.epub', '.fb2.zip'):
        if low.endswith(suffix):
            return name[:-len(suffix)], name[-len(suffix):]
    stem, suffix = os.path.splitext(name)
    return stem, suffix


def _disposition_name(value):
    if not value:
        return ''
    match = re.search(r"filename\*\s*=\s*([\w-]+)'[^']*'([^;]+)", value, re.IGNORECASE)
    if match:
        try:
            return urllib.parse.unquote(match.group(2).strip().strip('"'), match.group(1))
        except LookupError:
            pass
    match = re.search(r'filename\s*=\s*"([^"]*)"', value, re.IGNORECASE) or re.search(
        r'filename\s*=\s*([^;]+)', value, re.IGNORECASE)
    return match.group(1).strip() if match else ''


def _safe(name):
    name = re.sub(r'[\x00-\x1f/\\:*?"<>|]+', ' ', name)
    name = ' '.join(name.split()).strip(' .')
    while len(name.encode('utf-8', 'surrogateescape')) > 120:  # bytes: names hold 255
        name = name[:-1]
    return name.rstrip(' .')


def download_name(response, acquisition, title=''):
    """The file name of a download: the server's (Content-Disposition, else the address's
    last part when it has a book's suffix), else the title; always with the suffix of the
    format, so the importer knows it."""
    headers = response.headers if response.headers is not None else {}
    name = _safe(os.path.basename(_disposition_name(headers.get('Content-Disposition', ''))))
    format = acquisition.format
    if not name:
        last = urllib.parse.unquote(urllib.parse.urlsplit(response.url).path.rsplit('/', 1)[-1])
        if any(last.lower().endswith(suffix) for suffix in SUFFIXES.values()):
            name = _safe(last)
    if not name:
        name = _safe(title) or 'book'
    low = name.lower()
    if format is not None and not low.endswith(SUFFIXES[format]):
        if format == 'kepub' and low.endswith('.epub'):
            name = name[:-5]
        elif format != 'kepub' and any(low.endswith(s) for s in SUFFIXES.values()):
            return name  # the server's own suffix for the format (.azw3 for 'mobi'…)
        name += SUFFIXES[format]
    return name


# -- caches ------------------------------------------------------------------------------------

def cache_dir():
    """Bookcase's cache: BOOKCASE_DATA_DIR/cache when set (the tests, --demo), else
    $XDG_CACHE_HOME/bookcase."""
    data_dir = os.environ.get('BOOKCASE_DATA_DIR')
    if data_dir:
        return os.path.join(data_dir, 'cache')
    return os.path.join(GLib.get_user_cache_dir(), 'bookcase')


def downloads_dir():
    return os.path.join(cache_dir(), 'downloads')


class ThumbnailCache:
    """Covers of catalogue entries as files, named by their URL's hash; the oldest go when
    there are more than `limit`."""

    def __init__(self, directory=None, limit=600):
        self.directory = directory or os.path.join(cache_dir(), 'catalog-covers')
        self.limit = limit
        self._added = 0

    def path_for(self, url):
        return os.path.join(self.directory, hashlib.sha1(url.encode('utf-8')).hexdigest())

    def cached(self, url):
        path = self.path_for(url)
        return path if os.path.exists(path) else None

    def fetch(self, url, client):
        """The path of the image at `url` (fetched now unless cached); OpdsError when it
        cannot be had."""
        path = self.cached(url)
        if path is not None:
            return path
        response = client.get(url, accept='image/*', limit=MAX_IMAGE_BYTES)
        if not response.data:
            raise OpdsError(_('No cover'))
        os.makedirs(self.directory, exist_ok=True)
        path = self.path_for(url)
        part = f'{path}.{threading.get_ident()}.part'
        with open(part, 'wb') as file:
            file.write(response.data)
        os.replace(part, path)
        self._added += 1
        if self._added % 50 == 0:
            self.trim()
        return path

    def trim(self):
        try:
            names = [os.path.join(self.directory, name) for name in os.listdir(self.directory)]
            names.sort(key=lambda path: os.stat(path).st_mtime)
            for path in names[:max(0, len(names) - self.limit)]:
                os.unlink(path)
        except OSError as error:
            log.warning('trimming the catalogue covers: %s', error)


# -- threads ---------------------------------------------------------------------------------

class Task:
    """A function running in a thread; see run_async."""

    def __init__(self):
        self._cancelled = threading.Event()

    def cancel(self):
        self._cancelled.set()

    @property
    def cancelled(self):
        return self._cancelled.is_set()


def run_async(func, callback, *args, **kwargs):
    """Run func(*args, **kwargs) in a daemon thread and call callback(result, error) on the
    main loop (one of them None), unless the returned Task was cancelled first."""
    task = Task()

    def deliver(result, error):
        if not task.cancelled:
            callback(result, error)
        return GLib.SOURCE_REMOVE

    def run():
        try:
            result = func(*args, **kwargs)
        except OpdsError as error:
            GLib.idle_add(deliver, None, error)
        except Exception as error:
            log.exception('catalogue request')
            GLib.idle_add(deliver, None, OpdsError(
                _('Something went wrong: {error}').format(error=error)))
        else:
            GLib.idle_add(deliver, result, None)

    threading.Thread(target=run, name='bookcase-opds', daemon=True).start()
    return task


class Downloads(GObject.Object):
    """The downloads under way and done this session (see the module's docstring)."""

    __gtype_name__ = 'BookcaseDownloads'
    __gsignals__ = {
        'progress': (GObject.SignalFlags.RUN_FIRST, None, (str, float)),
        'finished': (GObject.SignalFlags.RUN_FIRST, None, (str, int, str)),
    }

    def __init__(self, importer, folder=None):
        super().__init__()
        self.importer = importer
        self.folder = folder
        self._states = {}
        self._tasks = {}
        self.reports = {}  # key -> the importer's report

    def state(self, key):
        return self._states.get(key)

    def cancel(self, key):
        task = self._tasks.get(key)
        if task is not None:
            task.cancel()

    def start(self, key, client, acquisition, entry):
        if key in self._tasks:
            return
        task = Task()
        self._tasks[key] = task
        self._states[key] = ('downloading', 0.0)
        folder = self.folder or downloads_dir()
        last = [0.0]

        def progress(done, total):
            fraction = done / total if total else -1.0
            now = time.monotonic()
            if now - last[0] > 0.1:
                last[0] = now
                GLib.idle_add(self._progress, key, min(1.0, fraction))

        def run():
            try:
                path = client.download(acquisition, folder, progress, lambda: task.cancelled,
                                       entry.title)
            except OpdsError as error:
                GLib.idle_add(self._failed, key, str(error), isinstance(error, Cancelled))
                return
            except Exception as error:
                log.exception('downloading %s', acquisition.href)
                GLib.idle_add(self._failed, key, str(error), False)
                return
            GLib.idle_add(self._import, key, path)

        threading.Thread(target=run, name='bookcase-download', daemon=True).start()

    def _progress(self, key, fraction):
        if self._states.get(key, ('',))[0] == 'downloading':
            self._states[key] = ('downloading', fraction)
            self.emit('progress', key, fraction)
        return GLib.SOURCE_REMOVE

    def _failed(self, key, message, cancelled):
        self._tasks.pop(key, None)
        if cancelled:
            self._states.pop(key, None)
            self.emit('finished', key, 0, '')
        else:
            self._states[key] = ('failed', message)
            self.emit('finished', key, 0, message)
        return GLib.SOURCE_REMOVE

    def _import(self, key, path):
        self._states[key] = ('downloading', 1.0)

        def done(report):
            self._tasks.pop(key, None)
            library = getattr(self.importer, 'library', None)
            if library is None or library.closed or library.find_file(path) is None:
                # The download only, never a file the library now reads in place (a
                # library folder holding the cache folder adds it where it is).
                try:
                    os.unlink(path)
                except OSError:
                    pass
            self.reports[key] = report
            ids = (list(report.added) + list(report.merged)
                   + [book for _path, book in report.duplicates])
            if ids:
                self._states[key] = ('done', ids[0])
                self.emit('finished', key, ids[0], '')
            else:
                message = report.failed[0][1] if report.failed else _('Could not add the book')
                self._states[key] = ('failed', message)
                self.emit('finished', key, 0, message)

        self.importer.add_async([path], copy=True, done=done)
        return GLib.SOURCE_REMOVE
