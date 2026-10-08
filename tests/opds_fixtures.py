# SPDX-License-Identifier: GPL-2.0-or-later
# SPDX-FileCopyrightText: 2026 Jack Tully

"""Invented OPDS catalogues for the tests, and a local HTTP server to serve them.

    ATOM_BOOKS, ATOM_NAVIGATION, OPDS2_FEED, GUTENBERG_LIST, GUTENBERG_BOOK, OPENSEARCH
    with CatalogServer({'/path': Route(body, content_type, …)}) as server:
        server.url('/path'); server.requests     # [(path, headers)]

Every book, author and identifier here is made up. The server runs on 127.0.0.1 in a thread;
a route can ask for HTTP Basic credentials (auth=('user', 'secret')), redirect (location=),
or send a Content-Disposition.
"""

import base64
import dataclasses
import http.server
import threading

from tests import ROOT  # noqa: F401

ATOM_NAVIGATION = """<?xml version="1.0" encoding="utf-8"?>
<feed xmlns="http://www.w3.org/2005/Atom" xmlns:thr="http://purl.org/syndication/thread/1.0">
  <id>urn:uuid:7d1f0c4e-0000-4000-8000-000000000001</id>
  <title>Harbour Lane Library</title>
  <subtitle>Books from a small invented library</subtitle>
  <icon>/static/icon.png</icon>
  <link rel="self" href="/opds" type="application/atom+xml;profile=opds-catalog;kind=navigation"/>
  <link rel="search" href="/opds/search.xml" type="application/opensearchdescription+xml"/>
  <entry>
    <title>New Arrivals</title>
    <id>urn:harbour:new</id>
    <content type="text">The latest books</content>
    <link rel="subsection" href="new?page=1" thr:count="3"
          type="application/atom+xml;profile=opds-catalog;kind=acquisition"/>
  </entry>
  <entry>
    <title>By Author</title>
    <id>urn:harbour:authors</id>
    <link href="/opds/authors" type="application/atom+xml"/>
  </entry>
  <entry>
    <title>A detail link only</title>
    <id>urn:harbour:detail</id>
    <link rel="alternate" href="/opds/book/9"
          type="application/atom+xml;type=entry;profile=opds-catalog"/>
  </entry>
</feed>
"""

ATOM_BOOKS = """<?xml version="1.0" encoding="utf-8"?>
<feed xmlns="http://www.w3.org/2005/Atom" xmlns:opds="http://opds-spec.org/2010/catalog"
      xmlns:dcterms="http://purl.org/dc/terms/" xmlns:dc="http://purl.org/dc/elements/1.1/"
      xmlns:calibre="http://calibre.kovidgoyal.net/2009/metadata"
      xmlns:opensearch="http://a9.com/-/spec/opensearch/1.1/"
      xmlns:thr="http://purl.org/syndication/thread/1.0">
  <id>urn:harbour:new</id>
  <title>New Arrivals</title>
  <opensearch:totalResults>5</opensearch:totalResults>
  <link rel="next" href="new?page=2" type="application/atom+xml;profile=opds-catalog"/>
  <link rel="search" href="/opds/search?q={searchTerms}" type="application/atom+xml"/>
  <link rel="http://opds-spec.org/facet" href="new?sort=title" title="Title"
        opds:facetGroup="Sort" opds:activeFacet="true" thr:count="5"/>
  <link rel="http://opds-spec.org/facet" href="new?sort=new" title="Newest"
        opds:facetGroup="Sort"/>
  <link rel="http://opds-spec.org/facet" href="new?lang=en" title="English"
        opds:facetGroup="Language"/>
  <entry>
    <title>The Lantern Keeper</title>
    <id>urn:uuid:0b8e7a52-1111-4c3a-9d55-000000000001</id>
    <author><name>Ada Lark</name></author>
    <author><name>Tomas Wren</name></author>
    <summary type="text">A keeper of lights
on a coast that has none.</summary>
    <category term="fiction" label="Fiction"/>
    <category term="sea"/>
    <dcterms:language>en</dcterms:language>
    <dcterms:issued>2019-04-02</dcterms:issued>
    <dcterms:publisher>Gull Press</dcterms:publisher>
    <dc:identifier>urn:isbn:978-0-00-000001-9</dc:identifier>
    <calibre:series>Coastal Tales</calibre:series>
    <calibre:series_index>2</calibre:series_index>
    <link rel="http://opds-spec.org/image" href="/covers/1.jpg" type="image/jpeg"/>
    <link rel="http://opds-spec.org/image/thumbnail" href="/covers/1-small.jpg"
          type="image/jpeg"/>
    <link rel="http://opds-spec.org/acquisition" href="/get/1.pdf" type="application/pdf"/>
    <link rel="http://opds-spec.org/acquisition" href="/get/1.epub" length="2048"
          type="application/epub+zip" title="EPUB"/>
    <link rel="http://opds-spec.org/acquisition" href="/get/1.kepub.epub"
          type="application/kepub+zip"/>
  </entry>
  <entry>
    <title>Salt and Cedar</title>
    <id>urn:harbour:2</id>
    <author><name>Mira Holt</name></author>
    <content type="html">&lt;p&gt;Two &lt;b&gt;sisters&lt;/b&gt; and a boat.&lt;/p&gt;</content>
    <link rel="http://opds-spec.org/acquisition/buy" href="/buy/2" type="text/html">
      <opds:price currencycode="EUR">4.99</opds:price>
      <opds:indirectAcquisition type="application/epub+zip"/>
    </link>
  </entry>
  <entry>
    <title>The Borrowed Map</title>
    <id>urn:harbour:3</id>
    <author><name>Ned Quill</name></author>
    <content type="xhtml"><div xmlns="http://www.w3.org/1999/xhtml"><p>A map <i>nobody</i>
      returned.</p></div></content>
    <link rel="http://opds-spec.org/acquisition/borrow" href="/borrow/3"
          type="application/atom+xml;type=entry;profile=opds-catalog">
      <opds:indirectAcquisition type="application/vnd.adobe.adept+xml">
        <opds:indirectAcquisition type="application/epub+zip"/>
      </opds:indirectAcquisition>
    </link>
  </entry>
</feed>
"""

ATOM_BOOKS_PAGE_2 = """<?xml version="1.0" encoding="utf-8"?>
<feed xmlns="http://www.w3.org/2005/Atom">
  <id>urn:harbour:new:2</id>
  <title>New Arrivals</title>
  <link rel="previous" href="new?page=1" type="application/atom+xml;profile=opds-catalog"/>
  <entry>
    <title>Winter Orchard</title>
    <id>urn:harbour:4</id>
    <author><name>Ada Lark</name></author>
    <link rel="http://opds-spec.org/acquisition/open-access" href="/get/4.epub"
          type="application/epub+zip"/>
  </entry>
</feed>
"""

OPENSEARCH = """<?xml version="1.0" encoding="UTF-8"?>
<OpenSearchDescription xmlns="http://a9.com/-/spec/opensearch/1.1/">
  <ShortName>Harbour Lane</ShortName>
  <Url type="text/html" template="/search?q={searchTerms}"/>
  <Url type="application/atom+xml"
       template="/opds/search?q={searchTerms}&amp;page={startPage?}&amp;lang={language}"/>
</OpenSearchDescription>
"""

OPDS2_FEED = """{
  "metadata": {"title": "Fern Hill Books", "numberOfItems": 2},
  "links": [
    {"rel": "self", "href": "/v2/home.json", "type": "application/opds+json"},
    {"rel": "next", "href": "/v2/home.json?page=2", "type": "application/opds+json"},
    {"rel": "search", "href": "/v2/search{?query,title,author}", "templated": true,
     "type": "application/opds+json"}
  ],
  "navigation": [
    {"href": "/v2/new.json", "title": "New", "type": "application/opds+json"},
    {"href": "/v2/popular.json", "title": "Popular", "type": "application/opds+json",
     "properties": {"numberOfItems": 40}}
  ],
  "facets": [
    {"metadata": {"title": "Language"},
     "links": [{"href": "/v2/home.json?lang=fr", "title": "French",
                "properties": {"numberOfItems": 7}},
               {"href": "/v2/home.json", "title": "All", "rel": "self"}]}
  ],
  "publications": [
    {"metadata": {"@type": "http://schema.org/Book", "title": "Quiet Engines",
                  "identifier": "urn:isbn:9780000000026",
                  "author": [{"name": "Ilse Varga"}, "Pell Dunmore"],
                  "description": "<p>Machines that <em>listen</em>.</p>",
                  "subject": [{"name": "Science Fiction"}, "Robots"],
                  "language": "en", "published": "2021-06-01",
                  "publisher": {"name": "Brass Owl"},
                  "belongsTo": {"series": {"name": "The Engine Cycle", "position": 1}}},
     "links": [{"rel": "http://opds-spec.org/acquisition/open-access",
                "href": "/v2/get/qe.epub", "type": "application/epub+zip"},
               {"rel": "http://opds-spec.org/acquisition/buy", "href": "/v2/buy/qe",
                "type": "application/epub+zip",
                "properties": {"price": {"value": 7.5, "currency": "USD"}}}],
     "images": [{"href": "/v2/img/qe-small.jpg", "type": "image/jpeg", "width": 120},
                {"href": "/v2/img/qe.jpg", "type": "image/jpeg", "width": 600}]},
    {"metadata": {"title": "Paper Lanterns", "author": "Ola Brisk"},
     "links": [{"rel": "http://opds-spec.org/acquisition",
                "href": "/v2/get/pl", "type": "application/vnd.readium.lcp.license.v1.0+json",
                "properties": {"indirectAcquisition": [{"type": "application/epub+zip"}]}}]}
  ],
  "groups": [
    {"metadata": {"title": "Staff Picks"},
     "links": [{"rel": "self", "href": "/v2/picks.json", "type": "application/opds+json"}],
     "publications": [
       {"metadata": {"title": "Tidewater", "author": {"name": "Ruth Marrow"}},
        "links": [{"rel": "http://opds-spec.org/acquisition/open-access",
                   "href": "/v2/get/tw.pdf", "type": "application/pdf"}]}]}
  ]
}
"""

# The shape of Project Gutenberg's feeds (written here, with invented books): a list whose
# entries lead to a book's own feed, which lists its editions as separate entries.
GUTENBERG_LIST = """<?xml version="1.0" encoding="utf-8"?>
<feed xmlns="http://www.w3.org/2005/Atom" xmlns:opensearch="http://a9.com/-/spec/opensearch/1.1/">
<id>http://books.example/ebooks/search.opds/?sort_order=downloads</id>
<title>Popular</title>
<link rel="search" type="application/opensearchdescription+xml" href="/catalog/osd-books.xml"/>
<link rel="next" type="application/atom+xml;profile=opds-catalog"
      href="/ebooks/search.opds/?sort_order=downloads&amp;start_index=26"/>
<entry>
<id>http://books.example/ebooks/90001.opds</id>
<title>The Glass Meadow</title>
<content type="text">Hester Vane</content>
<link type="application/atom+xml;profile=opds-catalog" rel="subsection" href="/ebooks/90001.opds"/>
<link type="image/png" rel="http://opds-spec.org/image/thumbnail"
      href="data:image/png;base64,iVBORw0KGgo="/>
</entry>
<entry>
<id>http://books.example/ebooks/90002.opds</id>
<title>Letters from the Weir</title>
<content type="text">Owen Pike</content>
<link type="application/atom+xml;profile=opds-catalog" rel="subsection" href="/ebooks/90002.opds"/>
</entry>
</feed>
"""

GUTENBERG_BOOK = """<?xml version="1.0" encoding="utf-8"?>
<feed xmlns="http://www.w3.org/2005/Atom" xmlns:dcterms="http://purl.org/dc/terms/">
<id>http://books.example/ebooks/90001.opds</id>
<title>The Glass Meadow by Hester Vane</title>
<link rel="search" type="application/opensearchdescription+xml" href="/catalog/osd-books.xml"/>
<entry>
<title>The Glass Meadow</title>
<content type="xhtml"><div xmlns="http://www.w3.org/1999/xhtml"><p>This edition had all images
removed.</p><p>Summary: a meadow of glass.</p></div></content>
<id>urn:examplebooks:90001:2</id>
<published>1999-01-01T00:00:00+00:00</published>
<author><name>Vane, Hester</name></author>
<dcterms:language>en</dcterms:language>
<link type="application/epub+zip" rel="http://opds-spec.org/acquisition"
      title="EPUB (older e-readers, no images)" href="/ebooks/90001.epub.noimages"/>
<link type="application/x-mobipocket-ebook" rel="http://opds-spec.org/acquisition"
      title="Kindle (no images)" href="/ebooks/90001.kindle.noimages"/>
<link type="image/jpeg" rel="http://opds-spec.org/image" href="/cache/90001.cover.medium.jpg"/>
<link type="application/atom+xml;profile=opds-catalog" rel="related" href="/ebooks/author/7.opds"
      title="By Vane, Hester…"/>
</entry>
<entry>
<title>The Glass Meadow</title>
<content type="xhtml"><div xmlns="http://www.w3.org/1999/xhtml"><p>This edition has
images.</p></div></content>
<id>urn:examplebooks:90001:3</id>
<author><name>Vane, Hester</name></author>
<link type="application/epub+zip" rel="http://opds-spec.org/acquisition"
      title="EPUB3 (E-readers incl. Send-to-Kindle)" href="/ebooks/90001.epub3.images"/>
<link type="application/epub+zip" rel="http://opds-spec.org/acquisition"
      title="EPUB (older e-readers)" href="/ebooks/90001.epub.images"/>
<link type="application/x-mobipocket-ebook" rel="http://opds-spec.org/acquisition"
      title="Kindles (kf8)" href="/ebooks/90001.kf8.images"/>
</entry>
</feed>
"""

GUTENBERG_OSD = """<?xml version="1.0" encoding="UTF-8"?>
<OpenSearchDescription xmlns="http://a9.com/-/spec/opensearch/1.1/">
   <ShortName>Example Books</ShortName>
   <Url type="text/html" template="/ebooks/search/?query={searchTerms}"/>
   <Url type="application/atom+xml" template="/ebooks/search.opds/?query={searchTerms}"/>
</OpenSearchDescription>
"""


@dataclasses.dataclass
class Route:
    body: bytes | str
    content_type: str = 'application/atom+xml;profile=opds-catalog'
    status: int = 200
    auth: tuple | None = None
    location: str = ''
    disposition: str = ''


class CatalogServer:
    """A threaded HTTP server on 127.0.0.1 serving `routes` (path with query -> Route)."""

    def __init__(self, routes):
        self.routes = routes
        self.requests = []
        server = self

        class Handler(http.server.BaseHTTPRequestHandler):

            def log_message(self, *_args):
                pass

            def do_GET(self):
                server.requests.append((self.path, dict(self.headers)))
                route = server.routes.get(self.path) or server.routes.get(
                    self.path.split('?')[0])
                if route is None:
                    self.send_error(404)
                    return
                if route.auth is not None:
                    token = base64.b64encode(':'.join(route.auth).encode()).decode()
                    if self.headers.get('Authorization') != f'Basic {token}':
                        self.send_response(401)
                        self.send_header('WWW-Authenticate', 'Basic realm="books"')
                        self.send_header('Content-Length', '0')
                        self.end_headers()
                        return
                if route.location:
                    self.send_response(302)
                    self.send_header('Location', route.location.format(port=server.port))
                    self.send_header('Content-Length', '0')
                    self.end_headers()
                    return
                body = route.body.encode('utf-8') if isinstance(route.body, str) else route.body
                self.send_response(route.status)
                self.send_header('Content-Type', route.content_type)
                self.send_header('Content-Length', str(len(body)))
                if route.disposition:
                    self.send_header('Content-Disposition', route.disposition)
                self.end_headers()
                self.wfile.write(body)

        self.httpd = http.server.ThreadingHTTPServer(('127.0.0.1', 0), Handler)
        self.httpd.daemon_threads = True
        self.port = self.httpd.server_address[1]
        self.thread = threading.Thread(target=self.httpd.serve_forever, daemon=True)

    def url(self, path='/'):
        return f'http://127.0.0.1:{self.port}{path}'

    def __enter__(self):
        self.thread.start()
        return self

    def __exit__(self, *_args):
        self.httpd.shutdown()
        self.httpd.server_close()
