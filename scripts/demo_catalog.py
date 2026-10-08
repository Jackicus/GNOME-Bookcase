#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-2.0-or-later
# SPDX-FileCopyrightText: 2026 Jack Tully

"""An invented OPDS catalogue served from a thread on 127.0.0.1, for screenshots of the
Discover pages without the internet.

    server = demo_catalog.serve()        # started; server.url('/opds') is its first page
    server.catalog()                     # (title, url, description) for the catalogs setting
    server.stop()

Its first page lists sections (New Arrivals, Popular, By Subject), New Arrivals is a page of
books with filters and a next page, and there is a search (OpenSearch). Every book is made
up; the covers are drawn with demo_library.py's cover styles. A book's file is a small
generated EPUB (tests/support.py's make_epub), so a download works too.
"""

import html
import http.server
import os
import sys
import tempfile
import threading

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)

import demo_library  # noqa: E402

TITLE = 'Harbour Lane Library'

BOOKS = [
    dict(title='The Salt Archivist', authors=['Odile Marsh'], style='waves', palette=1,
         series='The Saltmarsh Papers', index=1, published='2023-02-09', subject='Fantasy',
         blurb='An archivist who catalogues the sea’s lost letters finds one addressed to '
               'her, sealed forty years before she was born.'),
    dict(title='Nine Bells for Orrin', authors=['Felix Tamsin'], style='deco', palette=0,
         published='2021-05-13', subject='Mystery',
         blurb='A bell-ringer goes missing on the night the cathedral rings nine.'),
    dict(title='Under a Paper Moon', authors=['Hana Lisle'], style='stars', palette=1,
         published='2024-10-01', subject='Science Fiction',
         blurb='The moon was folded from paper. Somebody has started to unfold it.'),
    dict(title='The Orchard Year', authors=['Ruth Calloway'], style='botanical', palette=0,
         published='2022-08-18', subject='Nature',
         blurb='Twelve months in an old orchard, one tree at a time.'),
    dict(title='Copper Hill Road', authors=['Jonah Vey'], style='hills', palette=1,
         published='2020-03-26', subject='Fiction',
         blurb='Three families, one road, and the summer the mine closed.'),
    dict(title='A Compass for Strangers', authors=['Mireille Ash'], style='compass',
         palette=0, published='2019-11-07', subject='Travel',
         blurb='Walking the old pilgrim roads with nothing but a borrowed compass.'),
    dict(title='The Glass Meteorologist', authors=['Odile Marsh'], style='clouds',
         palette=0, series='The Saltmarsh Papers', index=2, published='2024-04-11',
         subject='Fantasy', blurb='The weather of the marsh is kept in jars. One has broken.'),
    dict(title='Small Engines', authors=['Pell Dunmore'], style='bauhaus', palette=1,
         published='2018-06-21', subject='Science Fiction',
         blurb='Stories of machines that only wanted to be useful.'),
    dict(title='The River Accounts', authors=['Ines Gallow'], style='river', palette=0,
         published='2023-09-14', subject='History',
         blurb='How one river kept the books of a city for six hundred years.'),
    dict(title='Lantern Weather', authors=['Hana Lisle'], style='sun', palette=1,
         published='2022-01-20', subject='Poetry',
         blurb='Poems for the hour when the lamps come on.'),
    dict(title='The Last Orbit of Tess Avery', authors=['Pell Dunmore'], style='orbits',
         palette=0, published='2025-02-27', subject='Science Fiction',
         blurb='A cargo pilot with one more delivery and no fuel to come home.'),
    dict(title='Flock', authors=['Ruth Calloway'], style='flock', palette=0,
         published='2021-10-28', subject='Nature',
         blurb='A year with the starlings of the estuary.'),
]


def _book_id(number):
    return f'urn:uuid:5e1f0000-0000-4000-8000-{number:012d}'


def _entry(number, book):
    authors = ''.join(f'<author><name>{html.escape(a)}</name></author>'
                      for a in book['authors'])
    series = ''
    if book.get('series'):
        series = (f'<calibre:series>{html.escape(book["series"])}</calibre:series>'
                  f'<calibre:series_index>{book["index"]}</calibre:series_index>')
    return f"""<entry>
<title>{html.escape(book['title'])}</title>
<id>{_book_id(number)}</id>
{authors}
<summary type="text">{html.escape(book['blurb'])}</summary>
<category term="{html.escape(book['subject'])}" label="{html.escape(book['subject'])}"/>
<dcterms:language>en</dcterms:language>
<dcterms:issued>{book['published']}</dcterms:issued>
<dcterms:publisher>Harbour Lane Press</dcterms:publisher>
{series}
<link rel="http://opds-spec.org/image" href="/covers/{number}.jpg" type="image/jpeg"/>
<link rel="http://opds-spec.org/image/thumbnail" href="/covers/{number}.jpg" type="image/jpeg"/>
<link rel="http://opds-spec.org/acquisition/open-access" href="/get/{number}.epub"
      type="application/epub+zip" length="{40000 + number * 917}"/>
<link rel="http://opds-spec.org/acquisition/open-access" href="/get/{number}.pdf"
      type="application/pdf"/>
</entry>"""


HEAD = ('<?xml version="1.0" encoding="utf-8"?>\n'
        '<feed xmlns="http://www.w3.org/2005/Atom" '
        'xmlns:opds="http://opds-spec.org/2010/catalog" '
        'xmlns:dcterms="http://purl.org/dc/terms/" '
        'xmlns:thr="http://purl.org/syndication/thread/1.0" '
        'xmlns:calibre="http://calibre.kovidgoyal.net/2009/metadata">')
SEARCH = ('<link rel="search" href="/opds/search.xml" '
          'type="application/opensearchdescription+xml"/>')


def root_feed():
    sections = [('New Arrivals', 'Books added this month', '/opds/new', len(BOOKS)),
                ('Popular', 'What readers download most', '/opds/new?sort=popular', 120),
                ('By Subject', 'Fiction, nature, poetry and more', '/opds/subjects', 9),
                ('By Author', 'Every writer in the library', '/opds/authors', 54)]
    entries = ''.join(
        f'<entry><title>{title}</title><id>urn:harbour:{n}</id>'
        f'<content type="text">{text}</content>'
        f'<link rel="subsection" href="{href}" thr:count="{count}" '
        f'type="application/atom+xml;profile=opds-catalog;kind=acquisition"/></entry>'
        for n, (title, text, href, count) in enumerate(sections))
    return (f'{HEAD}<id>urn:harbour</id><title>{TITLE}</title>'
            f'<subtitle>An invented library</subtitle>{SEARCH}{entries}</feed>')


def books_feed(start=0, count=None, next_page=True):
    count = len(BOOKS) if count is None else count
    facets = ''.join(
        f'<link rel="http://opds-spec.org/facet" href="/opds/new?{query}" title="{title}" '
        f'opds:facetGroup="{group}"{active}/>'
        for group, title, query, active in (
            ('Sort', 'Newest', 'sort=new', ' opds:activeFacet="true"'),
            ('Sort', 'Title', 'sort=title', ''),
            ('Language', 'English', 'lang=en', ''),
            ('Language', 'French', 'lang=fr', '')))
    entries = ''.join(_entry(n, BOOKS[n]) for n in range(start, start + count))
    following = ('<link rel="next" href="/opds/new?page=2" '
                 'type="application/atom+xml;profile=opds-catalog"/>' if next_page else '')
    return (f'{HEAD}<id>urn:harbour:new</id><title>New Arrivals</title>{SEARCH}{following}'
            f'{facets}{entries}</feed>')


OPENSEARCH = ('<?xml version="1.0" encoding="UTF-8"?>'
              '<OpenSearchDescription xmlns="http://a9.com/-/spec/opensearch/1.1/">'
              '<ShortName>Harbour Lane</ShortName>'
              '<Url type="application/atom+xml" template="/opds/search?q={searchTerms}"/>'
              '</OpenSearchDescription>')


class DemoCatalog:

    def __init__(self):
        self.files = tempfile.mkdtemp(prefix='bookcase-demo-catalog-')
        self.covers = {}
        server = self

        class Handler(http.server.BaseHTTPRequestHandler):

            def log_message(self, *_args):
                pass

            def do_GET(self):
                body, kind = server.route(self.path)
                if body is None:
                    self.send_error(404)
                    return
                self.send_response(200)
                self.send_header('Content-Type', kind)
                self.send_header('Content-Length', str(len(body)))
                self.end_headers()
                self.wfile.write(body)

        self.httpd = http.server.ThreadingHTTPServer(('127.0.0.1', 0), Handler)
        self.httpd.daemon_threads = True
        self.port = self.httpd.server_address[1]

    def url(self, path='/opds'):
        return f'http://127.0.0.1:{self.port}{path}'

    def catalog(self):
        return TITLE, self.url('/opds'), 'An invented library, served for screenshots'

    def cover(self, number):
        if number not in self.covers:
            book = dict(BOOKS[number])
            book.pop('series', None)
            self.covers[number] = demo_library.jpeg_bytes(demo_library.draw_cover(book))
        return self.covers[number]

    def route(self, path):
        feed = 'application/atom+xml;profile=opds-catalog'
        base, _q, query = path.partition('?')
        if base == '/opds':
            return root_feed().encode(), feed
        if base == '/opds/new' and query == 'page=2':
            return books_feed(8, 4, next_page=False).encode(), feed
        if base in ('/opds/new', '/opds/search'):
            return books_feed().encode(), feed
        if base == '/opds/search.xml':
            return OPENSEARCH.encode(), 'application/opensearchdescription+xml'
        if base.startswith('/covers/'):
            number = int(base[8:].split('.')[0])
            return (self.cover(number), 'image/jpeg') if number < len(BOOKS) else (None, '')
        if base.startswith('/get/') and base.endswith('.epub'):
            number = int(base[5:].split('.')[0])
            return self.epub(number), 'application/epub+zip'
        return None, ''

    def epub(self, number):
        sys.path.insert(0, os.path.dirname(HERE))
        from tests.support import make_epub

        book = BOOKS[number]
        path = make_epub(os.path.join(self.files, f'{number}.epub'), title=book['title'],
                         authors=tuple(book['authors']), description=book['blurb'],
                         cover=self.cover(number))
        with open(path, 'rb') as file:
            return file.read()

    def start(self):
        threading.Thread(target=self.httpd.serve_forever, daemon=True).start()
        return self

    def stop(self):
        self.httpd.shutdown()


def serve():
    return DemoCatalog().start()


if __name__ == '__main__':
    demo = serve()
    print(demo.url('/opds'))
    try:
        threading.Event().wait()
    except KeyboardInterrupt:
        demo.stop()
