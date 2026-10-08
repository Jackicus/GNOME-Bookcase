# SPDX-License-Identifier: GPL-2.0-or-later
# SPDX-FileCopyrightText: 2026 Jack Tully

"""Library sharing: the library as a catalogue other devices on the network read, for
getting books onto an e-reader or a phone over Wi-Fi (Calibre's content server, for its
common use). Read-only: browsing, searching, covers and downloads, nothing else.

    sharing = Sharing(settings, library, covers, keyring=None, cache_dir=None)   # app.sharing
    sharing.state                       # 'off', 'starting', 'on' or 'failed'
    sharing.error                       # why it failed, a sentence (or '')
    sharing.addresses()                 # ['http://192.168.1.20:8095/'] while on
    sharing.username()                  # the sharing-username setting
    sharing.password                    # the password while on (None before, or without)
    sharing.set_password(text, done=None)   # in the keyring (a thread); done(error or None)
    sharing.connect('changed', handler) # state, addresses or password changed
    sharing.shutdown()                  # at quit: stops the server

    server = Server(library_path, covers, cache_dir, host='127.0.0.1', port=0,
                    username='', password=None, lan_only=True)    # the HTTP server itself
    server.start()                      # binds (OSError when it cannot) and serves in a thread
    server.port; server.set_credentials(username, password); server.stop()
    lan_addresses()                     # this computer's IPv4 addresses on its networks

Sharing follows its settings: sharing-enabled turns it on and off (it starts with the app
when on), sharing-scope ('network': every IPv4 interface, answering only private, loopback
and link-local addresses; 'local': 127.0.0.1), sharing-port, sharing-require-password and
sharing-username. The password is in the keyring (passwords.py, SECRET_SCHEMA, the account
KEYRING_ACCOUNT); turning sharing on without one makes one (generate_password()). While on
it is advertised with DNS-SD through Avahi (_opds._tcp and _http._tcp) when Avahi runs.

What the server answers (GET and HEAD; anything else is refused):

    /                                   an HTML page for any browser (no scripts): the
    /recent /reading /all /authors /series /tags /shelves /search?q=
    /author/ID /series/ID /tag/ID /shelf/ID            same lists as the catalogue
    /opds                               the OPDS 1.2 catalogue (Atom): navigation feeds of
    /opds/recent … /opds/shelf/ID       the lists above, acquisition feeds of books (pages of
    /opds/search?q=  /opds/opensearch.xml    PAGE_SIZE: next/previous links), search.py's
                                        syntax through OpenSearch
    /cover/ID  /thumbnail/ID            a book's cover, and a THUMBNAIL_WIDTH PNG of it
    /get/ID/FORMAT/NAME                 a book's file: an EPUB (or kepub) is a copy with the
                                        library's metadata and cover written in
                                        (exporting.export_copy, cached in cache_dir while
                                        sharing is on); other formats are the file as it is.
                                        Content-Disposition names it 'Title - Author.ext';
                                        a Range (one) is answered with 206

Only books in the library are served: by id, never by path, and never a book opened without
adding it (library.OPENED). Every request but those from a refused address and a refused
Host header needs the user name and password (HTTP Basic) when a password is required; after
MAX_FAILURES wrong ones in FAILURE_WINDOW seconds an address is refused (429) for LOCKOUT
seconds. A Host header must name an address, a single-label name or a .local (.lan,
.home.arpa, .internal) one, so a web page cannot reach the server through a DNS name of
its own (DNS rebinding). At most MAX_CONNECTIONS at once. Nothing a user typed (a password,
a search, a path) is logged.
"""

import base64
import contextlib
import dataclasses
import hmac
import html
import ipaddress
import logging
import math
import os
import queue
import re
import secrets
import shutil
import socket
import tempfile
import threading
import time
import urllib.parse
from gettext import gettext as _
from gettext import ngettext

from gi.repository import Gio, GLib, GObject

from . import passwords

log = logging.getLogger(__name__)

DEFAULT_PORT = 8095
SCOPES = ('network', 'local')
SECRET_SCHEMA = 'io.github.jackicus.Bookcase.Sharing'
KEYRING_ACCOUNT = 'library sharing'
PAGE_SIZE = 50  # books per page of a list
GROUP_PAGE_SIZE = 100  # authors, series, tags per page
THUMBNAIL_WIDTH = 240
MAX_FAILURES = 5
FAILURE_WINDOW = 60  # seconds
LOCKOUT = 60  # seconds
MAX_CONNECTIONS = 16
REQUEST_TIMEOUT = 30  # seconds a connection may sit idle
POLL_INTERVAL = 0.2  # seconds stopping may wait
POOL_SIZE = 4  # library connections kept for the request threads
CACHE_FILES = 12  # EPUB copies kept while sharing is on
CHUNK = 64 * 1024
PASSWORD_ALPHABET = 'abcdefghjkmnpqrstuvwxyz23456789'  # nothing to mistake for another
EMBEDDABLE = ('epub', 'kepub')
LOCAL_SUFFIXES = ('.local', '.lan', '.home.arpa', '.internal', '.localdomain')

ATOM_NS = 'http://www.w3.org/2005/Atom'
NAVIGATION = 'application/atom+xml;profile=opds-catalog;kind=navigation'
ACQUISITION = 'application/atom+xml;profile=opds-catalog;kind=acquisition'
OPENSEARCH_TYPE = 'application/opensearchdescription+xml'
ACQUISITION_REL = 'http://opds-spec.org/acquisition'
IMAGE_REL = 'http://opds-spec.org/image'
THUMBNAIL_REL = 'http://opds-spec.org/image/thumbnail'

# What a book file is served as. The OPDS link and the HTTP header say the same.
MEDIA_TYPES = {
    'epub': 'application/epub+zip',
    'kepub': 'application/kepub+zip',
    'azw3': 'application/x-mobi8-ebook',
    'mobi': 'application/x-mobipocket-ebook',
    'fb2': 'application/x-fictionbook+xml',
    'fbz': 'application/x-zip-compressed-fb2',
    'pdf': 'application/pdf',
    'cbz': 'application/vnd.comicbook+zip',
    'cbr': 'application/vnd.comicbook-rar',
    'txt': 'text/plain',
}
FORMAT_NAMES = {'epub': 'EPUB', 'kepub': 'Kobo EPUB', 'azw3': 'AZW3', 'mobi': 'MOBI',
                'fb2': 'FB2', 'fbz': 'FB2 (zip)', 'pdf': 'PDF', 'cbz': 'CBZ', 'cbr': 'CBR',
                'txt': 'TXT'}
IMAGE_TYPES = {'.jpg': 'image/jpeg', '.jpeg': 'image/jpeg', '.png': 'image/png',
               '.gif': 'image/gif', '.webp': 'image/webp', '.avif': 'image/avif',
               '.bmp': 'image/bmp'}

_ID = re.compile(r'[1-9][0-9]{0,9}\Z')
_HOST = re.compile(r'(\[[0-9A-Fa-f:.]+\]|[A-Za-z0-9.-]+)(:[0-9]{1,5})?\Z')
_INVALID_XML = re.compile('[\x00-\x08\x0b\x0c\x0e-\x1f￾￿]')
_TAGS = re.compile(r'<[^>]*>')


class NotFound(Exception):
    """No such page, book or file."""


# -- the network ------------------------------------------------------------------------------

def _usable(address):
    try:
        ip = ipaddress.ip_address(address)
    except ValueError:
        return False
    return ip.is_private and not ip.is_loopback and not ip.is_link_local


def _interface_addresses():
    """IPv4 addresses of this computer's interfaces (Linux's SIOCGIFADDR), skipping
    loopback and container, virtual-machine and VPN bridges."""
    try:
        import fcntl
        import struct
    except ImportError:  # pragma: no cover - not Linux
        return []
    found = []
    skip = ('lo', 'docker', 'veth', 'br-', 'virbr', 'vnet', 'tun', 'tap', 'wg', 'podman',
            'cni', 'flannel', 'zt')
    try:
        names = [name for _index, name in socket.if_nameindex()]
    except OSError:
        return []
    with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as probe:
        for name in names:
            if name.startswith(skip):
                continue
            try:
                packed = fcntl.ioctl(probe.fileno(), 0x8915,  # SIOCGIFADDR
                                     struct.pack('256s', name[:15].encode()))
            except OSError:
                continue  # no IPv4 address
            found.append(socket.inet_ntoa(packed[20:24]))
    return found


def lan_addresses():
    """This computer's private IPv4 addresses, the one the default route leaves from
    first (asked of the kernel: no packet is sent)."""
    found = []
    with contextlib.suppress(OSError), \
            socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as probe:
        probe.connect(('192.0.2.1', 9))  # TEST-NET-1: never reached
        found.append(probe.getsockname()[0])
    found += _interface_addresses()
    result = []
    for address in found:
        if _usable(address) and address not in result:
            result.append(address)
    return result


def client_allowed(address):
    """Whether a request from this address is answered: only the local network's."""
    try:
        ip = ipaddress.ip_address(address)
    except ValueError:
        return False
    if ip.version == 6 and ip.ipv4_mapped is not None:
        ip = ip.ipv4_mapped
    return ip.is_private or ip.is_loopback or ip.is_link_local


def host_allowed(host):
    """Whether a Host header names this server the way a device on the network would:
    an address, a name without dots, or a local-network name (see the module)."""
    if not host:
        return True  # HTTP/1.0
    match = _HOST.match(host)
    if match is None:
        return False
    name = match.group(1).lower().rstrip('.')
    if name.startswith('['):
        return True
    try:
        ipaddress.ip_address(name)
        return True
    except ValueError:
        pass
    return '.' not in name or name.endswith(LOCAL_SUFFIXES) or name == 'localhost'


def generate_password():
    """A password easy to type on an e-reader's keyboard: 'kq7m-x2fa-9tbe'."""
    return '-'.join(''.join(secrets.choice(PASSWORD_ALPHABET) for _ in range(4))
                    for _ in range(3))


# -- what the pages list ------------------------------------------------------------------------

@dataclasses.dataclass
class Item:
    """A link to another list: an author, a series, a tag, a shelf, a section."""
    title: str
    path: str  # '/author/3', without the /opds prefix
    count: int = 0
    books: bool = True  # leads to books (else to more links)
    summary: str = ''


@dataclasses.dataclass
class Listing:
    title: str
    path: str  # '/authors'
    items: list = dataclasses.field(default_factory=list)  # [Item]
    books: list = dataclasses.field(default_factory=list)  # [(Book, [BookFile])]
    page: int = 1
    pages: int = 1
    total: int = 0
    page_size: int = PAGE_SIZE
    query: str = ''
    up: str = '/'


def _book_files(library, book):
    """The book's files the server can send, one per format, the reading one first."""
    result, seen = [], set()
    for file in library.files(book.id):
        if file.missing or file.format in seen:
            continue
        seen.add(file.format)
        result.append(file)
    return result


def _page(page, total, size):
    pages = max(1, math.ceil(total / size))
    if page > pages:
        raise NotFound(page)
    return pages


def _books_listing(library, title, path, page, sort='title', up='/', query='', **filters):
    total = library.count(query=query, **filters)
    pages = _page(page, total, PAGE_SIZE)
    books = library.books(query=query, sort=sort, limit=PAGE_SIZE,
                          offset=(page - 1) * PAGE_SIZE, **filters)
    return Listing(title=title, path=path, page=page, pages=pages, total=total, query=query,
                   up=up, books=[(book, _book_files(library, book)) for book in books])


def _groups_listing(title, path, page, items):
    pages = _page(page, len(items), GROUP_PAGE_SIZE)
    start = (page - 1) * GROUP_PAGE_SIZE
    return Listing(title=title, path=path, page=page, pages=pages, total=len(items),
                   page_size=GROUP_PAGE_SIZE, items=items[start:start + GROUP_PAGE_SIZE])


def _count(count):
    return ngettext('{count} book', '{count} books', count).format(count=count)


def root_listing(library):
    """The first page: the ways into the library."""
    reading = library.count(status='reading')
    total = library.count()
    items = [
        Item(_('Recently Added'), '/recent', total, summary=_('The newest books first')),
        Item(_('Currently Reading'), '/reading', reading, summary=_count(reading)),
        Item(_('All Books'), '/all', total, summary=_count(total)),
        _group_item(_('Authors'), '/authors', len(library.authors()),
                    ngettext('{count} author', '{count} authors', len(library.authors()))),
        _group_item(_('Series'), '/series', len(library.series()),
                    ngettext('{count} series', '{count} series', len(library.series()))),
        _group_item(_('Tags'), '/tags', len(library.tags()),
                    ngettext('{count} tag', '{count} tags', len(library.tags()))),
    ]
    shelves = library.shelves()
    if shelves:
        items.append(_group_item(_('Shelves'), '/shelves', len(shelves),
                                 ngettext('{count} shelf', '{count} shelves', len(shelves))))
    return Listing(title=_('Library'), path='/', items=items, total=len(items), up='')


def _group_item(title, path, count, summary):
    return Item(title, path, count, books=False, summary=summary.format(count=count))


def listing(library, segments, page=1, query=''):
    """The Listing a path names (segments: ['author', '3']); NotFound for none."""
    if page < 1:
        raise NotFound(page)
    if not segments:
        return root_listing(library)
    name, rest = segments[0], segments[1:]
    if not rest:
        if name == 'recent':
            return _books_listing(library, _('Recently Added'), '/recent', page, sort='added')
        if name == 'reading':
            return _books_listing(library, _('Currently Reading'), '/reading', page,
                                  sort='last-read', status='reading')
        if name == 'all':
            return _books_listing(library, _('All Books'), '/all', page)
        if name == 'search':
            query = ' '.join(query.split())
            if not query:
                return Listing(title=_('Search'), path='/search')
            # Translators: a list's title; {query} is what was searched for.
            return _books_listing(library, _('Search: {query}').format(query=query),
                                  '/search', page, query=query)
        if name == 'authors':
            return _groups_listing(_('Authors'), '/authors', page, [
                Item(group.name, f'/author/{group.id}', group.count, summary=_count(
                    group.count)) for group in library.authors()])
        if name == 'series':
            return _groups_listing(_('Series'), '/series', page, [
                Item(group.name, f'/series/{group.id}', group.count, summary=_count(
                    group.count)) for group in library.series()])
        if name == 'tags':
            return _groups_listing(_('Tags'), '/tags', page, [
                Item(group.name, f'/tag/{group.id}', group.count, summary=_count(
                    group.count)) for group in library.tags()])
        if name == 'shelves':
            return _groups_listing(_('Shelves'), '/shelves', page, [
                Item(shelf.name, f'/shelf/{shelf.id}', shelf.count,
                     summary=_count(library.count(shelf=shelf)))
                for shelf in library.shelves()])
        raise NotFound(name)
    if len(rest) != 1 or not _ID.match(rest[0]):
        raise NotFound(segments)
    ident = int(rest[0])
    path = f'/{name}/{ident}'
    if name == 'author':
        group = _group(library.authors(), ident)
        return _books_listing(library, group.name, path, page, sort='series',
                              up='/authors', author=ident)
    if name == 'series':
        group = _group(library.series(), ident)
        return _books_listing(library, group.name, path, page, sort='series',
                              up='/series', series=ident)
    if name == 'tag':
        group = _group(library.tags(), ident)
        return _books_listing(library, group.name, path, page, up='/tags', tag=ident)
    if name == 'shelf':
        shelf = library.shelf(ident)
        if shelf is None:
            raise NotFound(path)
        return _books_listing(library, shelf.name, path, page, up='/shelves', shelf=shelf)
    raise NotFound(name)


def _group(groups, ident):
    for group in groups:
        if group.id == ident:
            return group
    raise NotFound(ident)


def shared_book(library, book_id):
    """The book if the server may send it: in the library, not opened without adding."""
    from .library import OPENED

    book = library.book(book_id)
    if book is None or book.source == OPENED:
        raise NotFound(book_id)
    return book


def download_path(book, file):
    """The URL path of a book's file: its id, format and a readable file name (which the
    server ignores; e-readers and Kindle's browser go by the suffix)."""
    from . import exporting, formats

    name = exporting.copy_name(book, formats.suffix_of(file.path))
    return f'/get/{book.id}/{file.format}/{urllib.parse.quote(name)}'


def _image_type(path):
    return IMAGE_TYPES.get(os.path.splitext(path)[1].lower(), 'application/octet-stream')


def _iso(timestamp):
    return time.strftime('%Y-%m-%dT%H:%M:%SZ', time.gmtime(timestamp or 0))


def _plain(text):
    """Text for XML: no characters XML forbids."""
    return _INVALID_XML.sub('', str(text or ''))


def _x(text):
    return html.escape(_plain(text), quote=True)


def _description(text, limit=4000):
    """A book's description (HTML) as plain text."""
    text = html.unescape(_TAGS.sub(' ', text or ''))
    text = ' '.join(text.split())
    return text if len(text) <= limit else text[:limit - 1].rstrip() + '…'


def _size(size):
    return GLib.format_size(size) if size else ''


# -- OPDS (Atom) ------------------------------------------------------------------------------

def _query(path, page=1, query=''):
    parameters = {}
    if query:
        parameters['q'] = query
    if page > 1:
        parameters['page'] = page
    return path + ('?' + urllib.parse.urlencode(parameters) if parameters else '')


def _link(rel, href, kind, title=''):
    extra = f' title="{_x(title)}"' if title else ''
    return f'<link rel="{_x(rel)}" href="{_x(href)}" type="{_x(kind)}"{extra}/>'


def atom(result, covers, base=''):
    """The OPDS 1.2 feed of a Listing, as bytes. `base` is 'http://host:port', for the
    search template (the rest of the links are relative to the server)."""
    is_books = bool(result.books) or result.path in ('/search', '/recent', '/reading',
                                                     '/all') or result.path.count('/') > 1
    kind = ACQUISITION if is_books else NAVIGATION
    prefix = '/opds'
    self_path = '/opds' + (result.path if result.path != '/' else '')
    now = _iso(time.time())
    lines = [
        '<?xml version="1.0" encoding="UTF-8"?>',
        f'<feed xmlns="{ATOM_NS}" xmlns:dc="http://purl.org/dc/terms/" '
        'xmlns:opds="http://opds-spec.org/2010/catalog" '
        'xmlns:opensearch="http://a9.com/-/spec/opensearch/1.1/" '
        'xmlns:calibre_md="http://calibre.kovidgoyal.net/2009/metadata">',
        f'<id>urn:bookcase:{_x(self_path)}</id>',
        f'<title>{_x(result.title)}</title>',
        f'<updated>{now}</updated>',
        '<author><name>Bookcase</name></author>',
        _link('self', _query(self_path, result.page, result.query), kind),
        _link('start', '/opds', NAVIGATION, _('Library')),
        _link('search', '/opds/opensearch.xml', OPENSEARCH_TYPE, _('Search')),
        _link('search', (base or '') + '/opds/search?q={searchTerms}', 'application/atom+xml',
              _('Search')),
    ]
    if result.up:
        up = '/opds' + (result.up if result.up != '/' else '')
        lines.append(_link('up', up, NAVIGATION))
    if result.pages > 1:
        lines.append(_link('first', _query(self_path, 1, result.query), kind))
        lines.append(_link('last', _query(self_path, result.pages, result.query), kind))
    if result.page > 1:
        lines.append(_link('previous', _query(self_path, result.page - 1, result.query), kind))
    if result.page < result.pages:
        lines.append(_link('next', _query(self_path, result.page + 1, result.query), kind))
    if is_books:
        lines += [f'<opensearch:totalResults>{result.total}</opensearch:totalResults>',
                  f'<opensearch:itemsPerPage>{result.page_size}</opensearch:itemsPerPage>',
                  '<opensearch:startIndex>'
                  f'{(result.page - 1) * result.page_size + 1}</opensearch:startIndex>']
    for item in result.items:
        item_kind = ACQUISITION if item.books else NAVIGATION
        lines += [
            '<entry>',
            f'<title>{_x(item.title)}</title>',
            f'<id>urn:bookcase:{_x(prefix + item.path)}</id>',
            f'<updated>{now}</updated>',
        ]
        if item.summary:
            lines.append(f'<content type="text">{_x(item.summary)}</content>')
        lines += [_link('subsection', prefix + item.path, item_kind), '</entry>']
    for book, files in result.books:
        lines += _atom_entry(book, files, covers)
    lines.append('</feed>')
    return '\n'.join(lines).encode('utf-8')


def _atom_entry(book, files, covers):
    lines = [
        '<entry>',
        f'<title>{_x(book.title)}</title>',
        f'<id>urn:uuid:{_x(book.uuid)}</id>',
        f'<updated>{_iso(book.modified or book.added)}</updated>',
    ]
    for name in book.authors:
        lines.append(f'<author><name>{_x(name)}</name></author>')
    if book.language:
        lines.append(f'<dc:language>{_x(book.language)}</dc:language>')
    if book.publisher:
        lines.append(f'<dc:publisher>{_x(book.publisher)}</dc:publisher>')
    if book.published:
        lines.append(f'<dc:issued>{_x(book.published)}</dc:issued>')
    isbn = book.identifiers.get('isbn') if book.identifiers else None
    if isbn:
        lines.append(f'<dc:identifier>urn:isbn:{_x(isbn)}</dc:identifier>')
    if book.series:
        lines.append(f'<calibre_md:series>{_x(book.series)}</calibre_md:series>')
        if book.series_index:
            lines.append('<calibre_md:series_index>'
                         f'{book.series_index:g}</calibre_md:series_index>')
    for tag in book.tags:
        lines.append(f'<category term="{_x(tag)}" label="{_x(tag)}"/>')
    summary = _description(book.description)
    if summary:
        lines.append(f'<summary type="text">{_x(summary)}</summary>')
    if book.has_cover:
        cover = covers.path(book) if covers is not None else None
        if cover:
            version = f'?v={book.cover_version}'
            lines.append(_link(IMAGE_REL, f'/cover/{book.id}{version}', _image_type(cover)))
            lines.append(_link(THUMBNAIL_REL, f'/thumbnail/{book.id}{version}', 'image/png'))
    for file in files:
        media = MEDIA_TYPES.get(file.format, 'application/octet-stream')
        title = FORMAT_NAMES.get(file.format, file.format.upper())
        lines.append(f'<link rel="{ACQUISITION_REL}" href="{_x(download_path(book, file))}" '
                     f'type="{media}" title="{_x(title)}"'
                     + (f' length="{file.size}"' if file.size else '') + '/>')
    lines.append('</entry>')
    return lines


def opensearch(base):
    """The OpenSearch description the catalogue's search link names."""
    template = _x(base + '/opds/search?q={searchTerms}')
    return (
        '<?xml version="1.0" encoding="UTF-8"?>\n'
        '<OpenSearchDescription xmlns="http://a9.com/-/spec/opensearch/1.1/">\n'
        '<ShortName>Bookcase</ShortName>\n'
        f'<Description>{_x(_("Search the library"))}</Description>\n'
        '<InputEncoding>UTF-8</InputEncoding>\n'
        '<OutputEncoding>UTF-8</OutputEncoding>\n'
        f'<Url type="{ACQUISITION}" template="{template}"/>\n'
        f'<Url type="application/atom+xml" template="{template}"/>\n'
        '</OpenSearchDescription>\n').encode()


# -- the HTML pages ---------------------------------------------------------------------------

STYLE = """
body{font-family:sans-serif;margin:0 auto;max-width:44em;padding:0 12px 24px;line-height:1.4;
color:#222;background:#fff}
@media (prefers-color-scheme:dark){body{color:#ddd;background:#1e1e1e}a{color:#8cf}}
header{padding:12px 0;border-bottom:1px solid #8888}
header a.home{font-weight:bold;font-size:1.2em;text-decoration:none;color:inherit}
form{margin:8px 0 0}
input[type=search]{width:60%;font-size:1em;padding:4px}
button{font-size:1em;padding:4px 10px}
h1{font-size:1.4em;margin:16px 0 8px}
ul{list-style:none;padding:0;margin:0}
li{padding:10px 0;border-bottom:1px solid #8884;overflow:hidden}
li.nav a{font-size:1.1em}
.count{float:right;opacity:.7}
img{float:left;margin:0 12px 0 0;border:1px solid #8886}
.title{font-weight:bold}
.meta{opacity:.75;font-size:.95em}
.get a{display:inline-block;margin:6px 10px 0 0;padding:4px 10px;border:1px solid #888;
border-radius:4px;text-decoration:none}
.pages{margin:16px 0;text-align:center}
.pages a{margin:0 12px}
footer{margin-top:24px;font-size:.9em;opacity:.75}
"""

PAGE_SECURITY = ("default-src 'none'; img-src 'self'; style-src 'unsafe-inline'; "
                 "form-action 'self'; base-uri 'none'; frame-ancestors 'none'")


def _h(text):
    return html.escape(str(text or ''), quote=True)


def page(result, covers, base=''):
    """The HTML page of a Listing, as bytes: plain markup any browser shows (Kindle's and
    Kobo's included), no scripts."""
    language = GLib.get_language_names()[0].split('.')[0].split('@')[0].replace('_', '-')
    if language in ('C', 'POSIX', ''):
        language = 'en'
    title = result.title if result.path != '/' else 'Bookcase'
    out = [
        '<!DOCTYPE html>',
        f'<html lang="{_h(language)}"><head><meta charset="utf-8">',
        '<meta name="viewport" content="width=device-width, initial-scale=1">',
        '<meta name="robots" content="noindex">',
        f'<title>{_h(title)}</title>',
        f'<link rel="alternate" type="{NAVIGATION}" href="/opds" title="OPDS">',
        f'<style>{STYLE}</style></head><body>',
        '<header><a class="home" href="/">Bookcase</a>',
        '<form action="/search" method="get" role="search">'
        f'<input type="search" name="q" value="{_h(result.query)}" '
        f'aria-label="{_h(_("Search"))}" placeholder="{_h(_("Title, author, tag…"))}"> '
        f'<button type="submit">{_h(_("Search"))}</button></form></header>',
        f'<h1>{_h(result.title)}</h1>',
    ]
    if result.items:
        out.append('<ul>')
        for item in result.items:
            count = (f'<span class="count">{item.count}</span>'
                     if item.count and item.path != '/recent' else '')
            out.append(f'<li class="nav">{count}<a href="{_h(item.path)}">{_h(item.title)}</a>'
                       '</li>')
        out.append('</ul>')
    if result.books:
        out.append('<ul>')
        for book, files in result.books:
            out.append(_html_book(book, files, covers))
        out.append('</ul>')
    elif result.path == '/search' and not result.query:
        out.append(f'<p>{_h(_("Search for a title, an author, a series or a tag."))}</p>')
    elif result.path != '/' and not result.items:
        out.append(f'<p>{_h(_("No books"))}</p>')
    if result.pages > 1:
        links = []
        if result.page > 1:
            links.append(f'<a href="{_h(_query(result.path, result.page - 1, result.query))}"'
                         f' rel="prev">{_h(_("Previous"))}</a>')
        # Translators: under a long list; {page} and {pages} are numbers.
        links.append(_h(_('Page {page} of {pages}').format(page=result.page,
                                                           pages=result.pages)))
        if result.page < result.pages:
            links.append(f'<a href="{_h(_query(result.path, result.page + 1, result.query))}"'
                         f' rel="next">{_h(_("Next"))}</a>')
        out.append(f'<p class="pages">{" ".join(links)}</p>')
    if result.path == '/':
        address = (base or '') + '/opds'
        out.append('<footer>' + _h(_('Reading apps that open OPDS catalogues (KOReader, '
                                     'Readest, Thorium…) can use this address:'))
                   + f' <code>{_h(address)}</code></footer>')
    out.append('</body></html>')
    return '\n'.join(out).encode('utf-8')


def _html_book(book, files, covers):
    parts = ['<li>']
    if book.has_cover and covers is not None and covers.path(book):
        parts.append(f'<img src="/thumbnail/{book.id}?v={book.cover_version}" width="60" '
                     'alt="" loading="lazy">')
    parts.append(f'<div class="title">{_h(book.title)}</div>')
    meta = [book.author] if book.authors else []
    if book.series:
        if book.series_index:
            # Translators: a book's series and its number in it: "The Tides, book 2".
            meta.append(_('{series}, book {number}').format(series=book.series,
                                                             number=f'{book.series_index:g}'))
        else:
            meta.append(book.series)
    if meta:
        parts.append(f'<div class="meta">{_h(" · ".join(meta))}</div>')
    if files:
        links = []
        for file in files:
            name = FORMAT_NAMES.get(file.format, file.format.upper())
            size = _size(file.size)
            label = f'{name} ({size})' if size else name
            links.append(f'<a href="{_h(download_path(book, file))}">{_h(label)}</a>')
        parts.append(f'<div class="get">{"".join(links)}</div>')
    else:
        parts.append(f'<div class="meta">{_h(_("The book’s file cannot be found"))}</div>')
    parts.append('</li>')
    return ''.join(parts)


# -- the server ---------------------------------------------------------------------------------

class _Failures:
    """Wrong passwords by address, for the lockout."""

    def __init__(self):
        self._lock = threading.Lock()
        self._times = {}

    def blocked(self, address, now=None):
        now = time.monotonic() if now is None else now
        with self._lock:
            times = [moment for moment in self._times.get(address, ())
                     if now - moment < FAILURE_WINDOW + LOCKOUT]
            if times:
                self._times[address] = times
            else:
                self._times.pop(address, None)
            recent = [moment for moment in times if now - moment < FAILURE_WINDOW]
            return len(recent) >= MAX_FAILURES or (
                len(times) >= MAX_FAILURES and now - times[-1] < LOCKOUT)

    def failed(self, address, now=None):
        now = time.monotonic() if now is None else now
        with self._lock:
            if len(self._times) > 1000:  # many addresses: forget the oldest
                self._times.clear()
            self._times.setdefault(address, []).append(now)

    def succeeded(self, address):
        with self._lock:
            self._times.pop(address, None)


class Server:
    """The HTTP server; see the module docstring. Requests run in threads of their own, each
    with a library connection from a small pool (Library.open_worker's kind)."""

    def __init__(self, library_path, covers, cache_dir, host='127.0.0.1', port=0,
                 username='', password=None, lan_only=True):
        self.library_path = library_path
        self.covers = covers
        self.cache_dir = str(cache_dir)
        self.host = host
        self.requested_port = port
        self.lan_only = lan_only
        self.credentials = None
        self.set_credentials(username, password)
        self.failures = _Failures()
        self._pool = queue.LifoQueue()
        self._httpd = None
        self._thread = None
        self._stopped = False
        self._cache_lock = threading.Lock()
        self._export_locks = {}

    @property
    def port(self):
        return self._httpd.server_address[1] if self._httpd is not None else None

    def set_credentials(self, username, password):
        """Require these (None: no password at all)."""
        self.credentials = None if password is None else (username or '', password)

    def start(self):
        from http.server import ThreadingHTTPServer

        handler = _handler_class()

        class HTTPServer(ThreadingHTTPServer):
            daemon_threads = True
            allow_reuse_address = True
            request_queue_size = 32

            def __init__(server, *args, **kwargs):  # noqa: N805
                server.slots = threading.BoundedSemaphore(MAX_CONNECTIONS)
                server.connections = set()  # the open ones, cut when the server stops
                server.connections_lock = threading.Lock()
                super().__init__(*args, **kwargs)

            def process_request(server, request, client_address):  # noqa: N805
                if not server.slots.acquire(blocking=False):
                    server.shutdown_request(request)  # too many at once
                    return
                try:
                    super().process_request(request, client_address)
                except BaseException:
                    server.slots.release()
                    raise

            def process_request_thread(server, request, client_address):  # noqa: N805
                with server.connections_lock:
                    server.connections.add(request)
                try:
                    super().process_request_thread(request, client_address)
                finally:
                    with server.connections_lock:
                        server.connections.discard(request)
                    server.slots.release()

            def cut_connections(server):  # noqa: N805
                """End every open connection: a kept-alive one, a download under way."""
                with server.connections_lock:
                    connections = list(server.connections)
                for connection in connections:
                    with contextlib.suppress(OSError):
                        connection.shutdown(socket.SHUT_RDWR)

            def handle_error(server, request, client_address):  # noqa: N805
                log.debug('sharing: a request failed', exc_info=True)

        os.makedirs(self.cache_dir, exist_ok=True)
        httpd = HTTPServer((self.host, self.requested_port), handler)
        httpd.bookcase = self
        self._httpd = httpd
        self._thread = threading.Thread(target=httpd.serve_forever,
                                        kwargs={'poll_interval': POLL_INTERVAL},
                                        name='bookcase-sharing', daemon=True)
        self._thread.start()
        log.info('sharing the library on port %s', self.port)
        return self

    def stop(self):
        self._stopped = True
        httpd, self._httpd = self._httpd, None
        if httpd is not None:
            httpd.shutdown()
            httpd.server_close()
            httpd.cut_connections()
        while True:
            try:
                library = self._pool.get_nowait()
            except queue.Empty:
                break
            library.close()
        shutil.rmtree(self.cache_dir, ignore_errors=True)

    @contextlib.contextmanager
    def library(self):
        from .library import Library

        try:
            library = self._pool.get_nowait()
        except queue.Empty:
            library = Library(self.library_path, worker=True)
        try:
            yield library
        finally:
            if self._stopped or self._pool.qsize() >= POOL_SIZE:
                library.close()
            else:
                self._pool.put(library)

    def covers_for(self, library):
        return self.covers.with_library(library) if self.covers is not None else None

    def book_file(self, library, book, file):
        """(path, file name) of what to send for a book's file: an EPUB copy with the
        library's metadata (cached), else the file itself."""
        from . import exporting, formats

        name = exporting.copy_name(book, formats.suffix_of(file.path))
        if file.format not in EMBEDDABLE:
            if not os.path.isfile(file.path):
                raise NotFound(book.id)
            return file.path, name
        key = f'{book.id}-{file.id}-{int(book.modified)}-{book.cover_version}-{file.size}'
        with self._cache_lock:
            lock = self._export_locks.setdefault(key, threading.Lock())
        with lock:
            folder = os.path.join(self.cache_dir, key)
            copy = os.path.join(folder, 'book' + formats.suffix_of(file.path))
            if os.path.isfile(copy):
                os.utime(folder)
                return copy, name
            temporary = tempfile.mkdtemp(dir=self.cache_dir, prefix='.export-')
            try:
                path = exporting.export_copy(library, self.covers_for(library), book.id,
                                             temporary, format=file.format, name='book')
                os.rename(path, os.path.join(temporary, os.path.basename(copy)))
                os.rename(temporary, folder)
            except exporting.ExportError as error:
                shutil.rmtree(temporary, ignore_errors=True)
                raise NotFound(book.id) from error
            except OSError:
                shutil.rmtree(temporary, ignore_errors=True)
                if not os.path.isfile(copy):
                    raise
        self._trim_cache()
        return copy, name

    def _trim_cache(self):
        with self._cache_lock:
            try:
                entries = [entry for entry in os.scandir(self.cache_dir)
                           if entry.is_dir() and not entry.name.startswith('.')]
            except OSError:
                return
            entries.sort(key=lambda entry: entry.stat().st_mtime, reverse=True)
            for entry in entries[CACHE_FILES:]:
                shutil.rmtree(entry.path, ignore_errors=True)
                self._export_locks.pop(entry.name, None)


_HANDLER = None


def _handler_class():
    """The request handler (http.server is imported only when sharing starts)."""
    global _HANDLER
    if _HANDLER is not None:
        return _HANDLER
    from http.server import BaseHTTPRequestHandler

    class Handler(BaseHTTPRequestHandler):
        server_version = 'Bookcase'
        sys_version = ''
        protocol_version = 'HTTP/1.1'
        timeout = REQUEST_TIMEOUT

        def version_string(self):
            return self.server_version

        def log_message(self, format, *args):  # noqa: A002 - http.server's name
            pass  # the path and query may hold what the user searched for

        def log_error(self, format, *args):  # noqa: A002
            log.debug('sharing: %s', format % args if args else format)

        def do_GET(self):  # noqa: N802
            self._serve(head=False)

        def do_HEAD(self):  # noqa: N802
            self._serve(head=True)

        @property
        def bookcase(self):
            return self.server.bookcase

        # -- answering ------------------------------------------------------------------

        def _headers(self, status, content_type, length, extra=None, cache='no-store'):
            self.send_response(status)
            self.send_header('Content-Type', content_type)
            self.send_header('Content-Length', str(length))
            self.send_header('Cache-Control', cache)
            self.send_header('X-Content-Type-Options', 'nosniff')
            self.send_header('Referrer-Policy', 'no-referrer')
            for name, value in (extra or {}).items():
                self.send_header(name, value)
            self.end_headers()

        def _send(self, status, body, content_type, head, extra=None, cache='no-store'):
            self._headers(status, content_type, len(body), extra, cache)
            if not head:
                self.wfile.write(body)

        def _error(self, status, head, message='', extra=None):
            body = (message or self.responses.get(status, ('',))[0]).encode('utf-8')
            self._send(status, body, 'text/plain; charset=utf-8', head, extra)

        def _serve(self, head):
            server = self.bookcase
            if server._stopped:  # a connection kept alive past the server's end
                self.close_connection = True
                return
            address = self.client_address[0]
            if server.lan_only and not client_allowed(address):
                log.info('sharing: refused a request from outside the local network')
                self._error(403, head)
                return
            if not host_allowed(self.headers.get('Host', '')):
                self._error(421, head)
                return
            if not self._authorized(address, head):
                return
            try:
                self._route(head)
            except NotFound:
                self._error(404, head)
            except (BrokenPipeError, ConnectionResetError):
                pass
            except Exception:
                log.exception('sharing: answering a request')
                self.close_connection = True
                with contextlib.suppress(OSError):
                    self._error(500, head)

        def _authorized(self, address, head):
            credentials = self.bookcase.credentials
            if credentials is None:
                return True
            failures = self.bookcase.failures
            if failures.blocked(address):
                self._error(429, head, extra={'Retry-After': str(LOCKOUT)})
                return False
            header = self.headers.get('Authorization', '')
            challenge = {'WWW-Authenticate': 'Basic realm="Bookcase", charset="UTF-8"'}
            if not header:
                self._error(401, head, extra=challenge)
                return False
            scheme, _space, value = header.partition(' ')
            given = None
            if scheme.lower() == 'basic':
                with contextlib.suppress(ValueError, UnicodeDecodeError):
                    given = base64.b64decode(value.strip(), validate=True).decode('utf-8')
            user, _colon, password = (given or '').partition(':')
            same_user = hmac.compare_digest(user.encode(), credentials[0].encode())
            same_password = hmac.compare_digest(password.encode(), credentials[1].encode())
            if given is not None and same_user and same_password:
                failures.succeeded(address)
                return True
            failures.failed(address)
            log.info('sharing: a wrong user name or password from %s', address)
            self._error(401, head, extra=challenge)
            return False

        def _base(self):
            host = self.headers.get('Host', '')
            if not host or not _HOST.match(host):
                host = f'{self.server.server_address[0]}:{self.server.server_address[1]}'
            return f'http://{host}'

        def _route(self, head):
            parts = urllib.parse.urlsplit(self.path)
            segments = [urllib.parse.unquote(part) for part in parts.path.split('/') if part]
            try:
                parameters = urllib.parse.parse_qs(parts.query, max_num_fields=8)
            except ValueError:  # too many fields
                self._error(400, head)
                return
            query = (parameters.get('q') or [''])[0][:500]
            page_text = (parameters.get('page') or ['1'])[0]
            page_number = int(page_text) if _ID.match(page_text) else 0
            server = self.bookcase
            if segments[:1] == ['opds']:
                segments = segments[1:]
                if segments == ['opensearch.xml']:
                    self._send(200, opensearch(self._base()),
                               OPENSEARCH_TYPE + '; charset=utf-8', head)
                    return
                with server.library() as library:
                    result = listing(library, segments, page_number, query)
                    body = atom(result, server.covers_for(library), self._base())
                kind = NAVIGATION if not result.books and result.items else ACQUISITION
                self._send(200, body, kind + ';charset=utf-8', head)
                return
            if len(segments) == 2 and segments[0] in ('cover', 'thumbnail') and \
                    _ID.match(segments[1]):
                self._image(segments[0], int(segments[1]), head)
                return
            if len(segments) == 4 and segments[0] == 'get' and _ID.match(segments[1]):
                self._download(int(segments[1]), segments[2], head)
                return
            if segments == ['favicon.ico'] or segments == ['robots.txt']:
                raise NotFound(segments)
            with server.library() as library:
                result = listing(library, segments, page_number, query)
                body = page(result, server.covers_for(library), self._base())
            self._send(200, body, 'text/html; charset=utf-8', head,
                       {'Content-Security-Policy': PAGE_SECURITY})

        def _image(self, kind, book_id, head):
            server = self.bookcase
            with server.library() as library:
                book = shared_book(library, book_id)
                covers = server.covers_for(library)
                if covers is None:
                    raise NotFound(book_id)
                if kind == 'cover':
                    path = covers.path(book)
                    content_type = _image_type(path or '')
                else:
                    path = covers.thumbnail_path(book, THUMBNAIL_WIDTH)
                    content_type = 'image/png'
            if not path:
                raise NotFound(book_id)
            self._file(path, content_type, head, cache='private, max-age=86400')

        def _download(self, book_id, file_format, head):
            server = self.bookcase
            with server.library() as library:
                book = shared_book(library, book_id)
                files = [file for file in _book_files(library, book)
                         if file.format == file_format]
                if not files:
                    raise NotFound(book_id)
                path, name = server.book_file(library, book, files[0])
            ascii_name = name.encode('ascii', 'replace').decode().replace('?', '_') \
                .replace('"', '_').replace('\\', '_')
            disposition = (f'attachment; filename="{ascii_name}"; '
                           f"filename*=UTF-8''{urllib.parse.quote(name)}")
            self._file(path, MEDIA_TYPES.get(file_format, 'application/octet-stream'), head,
                       {'Content-Disposition': disposition}, ranges=True)

        def _file(self, path, content_type, head, extra=None, cache='no-store',
                  ranges=False):
            try:
                file = open(path, 'rb')  # noqa: SIM115 - closed below
            except OSError as error:
                raise NotFound(path) from error
            with file:
                size = os.fstat(file.fileno()).st_size
                extra = dict(extra or {})
                start, end, status = 0, size - 1, 200
                if ranges:
                    extra['Accept-Ranges'] = 'bytes'
                    wanted = parse_range(self.headers.get('Range', ''), size)
                    if wanted == 'unsatisfiable':
                        self._error(416, head, extra={'Content-Range': f'bytes */{size}'})
                        return
                    if wanted is not None:
                        start, end = wanted
                        status = 206
                        extra['Content-Range'] = f'bytes {start}-{end}/{size}'
                length = max(0, end - start + 1)
                self._headers(status, content_type, length, extra, cache)
                if head:
                    return
                file.seek(start)
                left = length
                while left > 0:
                    chunk = file.read(min(CHUNK, left))
                    if not chunk:  # the file shrank: the length sent was wrong
                        self.close_connection = True
                        break
                    self.wfile.write(chunk)
                    left -= len(chunk)

    _HANDLER = Handler
    return Handler


def parse_range(header, size):
    """(start, end) of a Range header's one byte range within `size`, None to send the
    whole file (no header, several ranges, or one this does not read), or 'unsatisfiable'."""
    header = header.strip()
    if not header.startswith('bytes=') or ',' in header:
        return None
    first, dash, last = header[6:].strip().partition('-')
    if not dash:
        return None
    try:
        if first:
            start = int(first)
            if start >= size:
                return 'unsatisfiable'
            end = int(last) if last else size - 1
        else:
            if not last:
                return None
            start = max(0, size - int(last))
            end = size - 1
    except ValueError:
        return None
    if start < 0 or end < start:
        return None
    return start, min(end, size - 1)


# -- DNS-SD through Avahi -------------------------------------------------------------------

AVAHI = 'org.freedesktop.Avahi'
SERVICES = (('_opds._tcp', b'path=/opds'), ('_http._tcp', b'path=/'))


def avahi_publish(name, port):
    """Advertise the server with Avahi on the system bus: the entry group's object path,
    or None when Avahi is not there (nothing else happens then)."""
    try:
        bus = Gio.bus_get_sync(Gio.BusType.SYSTEM, None)
        group = bus.call_sync(AVAHI, '/', AVAHI + '.Server', 'EntryGroupNew', None,
                              GLib.VariantType('(o)'), Gio.DBusCallFlags.NO_AUTO_START,
                              2000, None).unpack()[0]
        for service, text in SERVICES:
            bus.call_sync(AVAHI, group, AVAHI + '.EntryGroup', 'AddService',
                          GLib.Variant('(iiussssqaay)', (-1, 0, 0, name, service, '', '',
                                                         port, [list(text)])),
                          None, Gio.DBusCallFlags.NONE, 2000, None)
        bus.call_sync(AVAHI, group, AVAHI + '.EntryGroup', 'Commit', None, None,
                      Gio.DBusCallFlags.NONE, 2000, None)
        return group
    except GLib.Error as error:
        log.debug('sharing: not advertised with Avahi: %s', error.message)
        return None


def avahi_withdraw(group):
    if group is None:
        return
    with contextlib.suppress(GLib.Error):
        bus = Gio.bus_get_sync(Gio.BusType.SYSTEM, None)
        bus.call_sync(AVAHI, group, AVAHI + '.EntryGroup', 'Free', None, None,
                      Gio.DBusCallFlags.NONE, 1000, None)


# -- the app's side -----------------------------------------------------------------------------

class SharingKeyring:
    """The sharing password in the keyring (or in memory: tests, --demo)."""

    def __init__(self, backend=None):
        self.backend = backend or passwords.Keyring(SECRET_SCHEMA)

    def lookup(self):
        return self.backend.lookup(KEYRING_ACCOUNT)

    def store(self, password):
        return self.backend.store(KEYRING_ACCOUNT, _('Bookcase library sharing'), password)


def cache_dir():
    """Where EPUB copies wait to be sent: the cache's sharing folder."""
    from .opds import cache_dir as bookcase_cache

    return os.path.join(bookcase_cache(), 'sharing')


class Sharing(GObject.Object):
    """Library sharing as the app runs it; see the module docstring."""

    __gtype_name__ = 'BookcaseSharing'
    __gsignals__ = {'changed': (GObject.SignalFlags.RUN_FIRST, None, ())}

    def __init__(self, settings, library, covers, keyring=None, cache_dir=None,
                 advertise=True):
        super().__init__()
        self.settings = settings
        self.library = library
        self.covers = covers
        self.keyring = SharingKeyring(keyring)
        self.cache_dir = cache_dir or globals()['cache_dir']()
        self.advertise = advertise
        self.state = 'off'
        self.error = ''
        self.password = None
        self.server = None
        self._addresses = []
        self._group = None
        self._stopping = None  # the thread stopping the last server
        self._generation = 0
        # Starts run one at a time (each may wait on the keyring), so two servers never
        # try the port at once; a server started but not yet taken up by _started() waits
        # in _unadopted, where the next start (or shutdown) stops it when it is stale.
        self._start_lock = threading.Lock()
        self._handoff_lock = threading.Lock()
        self._unadopted = None  # (server, Avahi group)
        self._handlers = [settings.connect('changed::' + key, self._on_setting)
                          for key in ('sharing-enabled', 'sharing-scope', 'sharing-port',
                                      'sharing-require-password', 'sharing-username')]
        if settings.get_boolean('sharing-enabled'):
            self.start()

    def username(self):
        return self.settings.get_string('sharing-username')

    def addresses(self):
        return list(self._addresses) if self.state == 'on' else []

    def _on_setting(self, settings, key):
        if key == 'sharing-enabled':
            if settings.get_boolean(key):
                self.start()
            else:
                self.stop()
        elif key == 'sharing-username':
            if self.server is not None and self.password is not None and \
                    settings.get_boolean('sharing-require-password'):
                self.server.set_credentials(self.username(), self.password)
            self.emit('changed')
        elif self.state in ('on', 'starting', 'failed') and \
                settings.get_boolean('sharing-enabled'):
            self.start()  # again, as the settings say now

    def start(self):
        """Start (or restart) the server in a thread: the keyring may wait on an unlock
        prompt, and Avahi on the system bus."""
        previous = self._detach()
        stopping = self._stopping
        self._generation += 1
        generation = self._generation
        self.state, self.error = 'starting', ''
        self.emit('changed')
        settings = self.settings
        scope = settings.get_string('sharing-scope')
        local = scope == 'local'
        port = settings.get_int('sharing-port')
        require = settings.get_boolean('sharing-require-password')
        username = self.username()
        library_path = self.library.path
        covers = self.covers

        def stale():
            return generation != self._generation

        def work():
            with self._start_lock:
                begin()

        def begin():
            if previous is not None:
                _stop_quietly(*previous)  # first: it may hold the port
            if stopping is not None:
                stopping.join()
            orphan = self._take_unadopted()
            if orphan is not None:  # started for an earlier generation: it holds the port
                _stop_quietly(*orphan)
            if stale():
                return
            password = None
            if require:
                password = self.keyring.lookup()
                if not password:
                    password = generate_password()
                    try:
                        self.keyring.store(password)
                    except passwords.KeyringError as error:
                        log.warning('sharing: the password lasts this session: %s', error)
            server = Server(library_path, covers, self.cache_dir,
                            host='127.0.0.1' if local else '0.0.0.0', port=port,
                            username=username, password=password)
            try:
                server.start()
            except OSError as error:
                GLib.idle_add(self._failed, generation, _start_error(error, port))
                return
            addresses = ['127.0.0.1'] if local else lan_addresses()
            group = None
            if self.advertise and not local:
                group = avahi_publish(_('Bookcase on {host}').format(
                    host=GLib.get_host_name()), server.port)
            if stale():  # stopped, restarted or shut down meanwhile
                _stop_quietly(server, group)
                return
            with self._handoff_lock:
                self._unadopted = (server, group)
            GLib.idle_add(self._started, generation, server, password, addresses, group)

        threading.Thread(target=work, name='bookcase-sharing-start', daemon=True).start()

    def _take_unadopted(self, server=None):
        """The server started but not yet taken up (only `server`, when given), taken."""
        with self._handoff_lock:
            unadopted = self._unadopted
            if unadopted is None or (server is not None and unadopted[0] is not server):
                return None
            self._unadopted = None
            return unadopted

    def _started(self, generation, server, password, addresses, group):
        if generation != self._generation:  # stopped or restarted meanwhile
            def stop_stale():
                # Between starts: a later start may have stopped it already.
                with self._start_lock:
                    stale = self._take_unadopted(server)
                    if stale is not None:
                        _stop_quietly(*stale)

            threading.Thread(target=stop_stale, daemon=True).start()
            return GLib.SOURCE_REMOVE
        if self._take_unadopted(server) is None:  # stopped by shutdown()
            return GLib.SOURCE_REMOVE
        self.server, self.password, self._group = server, password, group
        self._addresses = [f'http://{address}:{server.port}/' for address in addresses]
        self.state = 'on'
        self.emit('changed')
        return GLib.SOURCE_REMOVE

    def _failed(self, generation, message):
        if generation == self._generation:
            self.state, self.error = 'failed', message
            self.emit('changed')
        return GLib.SOURCE_REMOVE

    def _detach(self):
        """The running (server, Avahi group), taken off this object (None when off)."""
        server, group = self.server, self._group
        self.server, self._group, self.password, self._addresses = None, None, None, []
        return (server, group) if server is not None else None

    def stop(self):
        self._generation += 1
        previous = self._detach()
        if previous is not None:  # in a thread: it waits for the server's loop
            self._stopping = threading.Thread(target=_stop_quietly, args=previous,
                                              daemon=True)
            self._stopping.start()
        self.state, self.error = 'off', ''
        self.emit('changed')

    def set_password(self, password, done=None):
        """Make `password` the one to give (stored in the keyring in a thread; the running
        server takes it at once). done(error or None) on the main loop."""
        self.password = password if self.state == 'on' else self.password
        if self.server is not None and self.settings.get_boolean('sharing-require-password'):
            self.server.set_credentials(self.username(), password)

        def work():
            error = None
            try:
                self.keyring.store(password)
            except passwords.KeyringError as failure:
                error = failure
            GLib.idle_add(finish, error)

        def finish(error):
            self.emit('changed')
            if done is not None:
                done(error)
            return GLib.SOURCE_REMOVE

        threading.Thread(target=work, name='bookcase-sharing-password', daemon=True).start()

    def lookup_password(self, done):
        """done(password or None) on the main loop: the keyring's, whether or not sharing
        is on."""
        def work():
            password = self.keyring.lookup()
            GLib.idle_add(finish, password)

        def finish(password):
            done(password)
            return GLib.SOURCE_REMOVE

        threading.Thread(target=work, name='bookcase-sharing-lookup', daemon=True).start()

    def shutdown(self):
        """At quit: stop serving, let go of the settings."""
        for handler in self._handlers:
            self.settings.disconnect(handler)
        self._handlers = []
        self._generation += 1
        previous = self._detach()
        if previous is not None:
            _stop_quietly(*previous)
        orphan = self._take_unadopted()
        if orphan is not None:
            _stop_quietly(*orphan)
        self.state = 'off'


def _stop_quietly(server, group):
    avahi_withdraw(group)
    try:
        server.stop()
    except Exception:
        log.exception('sharing: stopping the server')


def _start_error(error, port):
    import errno

    if error.errno == errno.EADDRINUSE:
        return _('Port {port} is in use by another program: choose another').format(port=port)
    if error.errno == errno.EACCES:
        return _('Port {port} cannot be used: choose one above 1024').format(port=port)
    return _('Sharing could not start: {error}').format(error=error.strerror or error)
