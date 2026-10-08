# SPDX-License-Identifier: GPL-2.0-or-later
# SPDX-FileCopyrightText: 2026 Jack Tully

"""opds.py: OPDS 1.2 and 2.0 feeds (navigation, books, pagination, facets, prices and DRM),
search templates, the format chosen, matching the library, the catalogue list, and over a
local HTTP server: credentials, redirects, errors, downloading and adding to the library.
Every catalogue and book is invented (tests/opds_fixtures.py); nothing goes online."""

import os
import pathlib
import shutil
import tempfile
import unittest

from gi.repository import GLib

from tests import ROOT  # noqa: F401
from tests import opds_fixtures as fx
from tests.opds_fixtures import CatalogServer, Route
from tests.support import add_book, make_epub, temporary_library
from bookcase import opds
from bookcase.covers import CoverStore
from bookcase.importing import Importer

BASE = 'https://books.example/opds/new?page=1'


class TestAtom(unittest.TestCase):

    def test_navigation(self):
        feed = opds.parse(fx.ATOM_NAVIGATION, 'https://books.example/opds')
        self.assertEqual(feed.title, 'Harbour Lane Library')
        self.assertEqual(feed.subtitle, 'Books from a small invented library')
        self.assertEqual(feed.icon, 'https://books.example/static/icon.png')
        self.assertEqual([e.title for e in feed.navigation], ['New Arrivals', 'By Author'])
        self.assertEqual(feed.navigation[0].href, 'https://books.example/new?page=1')
        self.assertEqual(feed.navigation[0].count, 3)
        self.assertEqual(feed.navigation[0].summary, 'The latest books')
        # A link with no rel is a way in; an entry's own detail link is not.
        self.assertEqual(feed.navigation[1].href, 'https://books.example/opds/authors')
        self.assertEqual(feed.books, [])
        self.assertEqual(feed.search, opds.Search(
            'opensearch', 'https://books.example/opds/search.xml'))

    def test_books(self):
        feed = opds.parse(fx.ATOM_BOOKS, BASE)
        self.assertEqual(feed.next, 'https://books.example/opds/new?page=2')
        self.assertEqual(feed.total, 5)
        self.assertEqual(feed.search, opds.Search(
            'template', 'https://books.example/opds/search?q={searchTerms}'))
        lantern, salt, borrowed = feed.books
        self.assertEqual(lantern.authors, ['Ada Lark', 'Tomas Wren'])
        self.assertEqual(lantern.author, 'Ada Lark, Tomas Wren')
        self.assertEqual(lantern.summary, 'A keeper of lights<br>on a coast that has none.')
        self.assertEqual(lantern.categories, ['Fiction', 'sea'])
        self.assertEqual((lantern.language, lantern.issued, lantern.publisher),
                         ('en', '2019-04-02', 'Gull Press'))
        self.assertEqual((lantern.series, lantern.series_index), ('Coastal Tales', 2.0))
        self.assertEqual(lantern.identifiers, {'isbn': '9780000000019',
                                               'uuid': '0b8e7a52-1111-4c3a-9d55-000000000001'})
        self.assertEqual(lantern.cover, 'https://books.example/covers/1.jpg')
        self.assertEqual(lantern.thumbnail, 'https://books.example/covers/1-small.jpg')
        self.assertEqual([a.format for a in lantern.acquisitions], ['pdf', 'epub', 'kepub'])
        self.assertEqual(lantern.acquisitions[1].size, 2048)
        self.assertIn('<b>sisters</b>', salt.summary)
        self.assertIn('<i>nobody</i>', borrowed.summary)
        self.assertNotIn('xmlns', borrowed.summary)

    def test_prices_and_drm(self):
        feed = opds.parse(fx.ATOM_BOOKS, BASE)
        _lantern, salt, borrowed = feed.books
        buy = salt.acquisitions[0]
        self.assertEqual((buy.kind, buy.price, buy.currency, buy.format),
                         ('buy', 4.99, 'EUR', 'epub'))
        self.assertFalse(buy.available)
        loan = borrowed.acquisitions[0]
        self.assertEqual(loan.kind, 'borrow')
        self.assertTrue(loan.drm)
        self.assertIsNone(opds.best_acquisition(salt))
        self.assertIsNone(opds.best_acquisition(borrowed))

    def test_facets(self):
        feed = opds.parse(fx.ATOM_BOOKS, BASE)
        sort, language = feed.facets
        self.assertEqual(sort.title, 'Sort')
        self.assertEqual([f.title for f in sort.facets], ['Title', 'Newest'])
        self.assertEqual(sort.active.title, 'Title')
        self.assertEqual(sort.facets[0].count, 5)
        self.assertEqual(language.facets[0].href, 'https://books.example/opds/new?lang=en')
        self.assertIsNone(language.active)

    def test_not_a_feed(self):
        for data in (b'<html><body>Hello</body></html>', b'not xml at all', b'{"a": 1}',
                     b'<rss version="2.0"><channel/></rss>'):
            with self.assertRaises(opds.NotOpdsError):
                opds.parse(data, BASE)

    def test_entities_are_not_expanded(self):
        data = (b'<?xml version="1.0"?><!DOCTYPE feed [<!ENTITY x SYSTEM "file:///etc/passwd">]>'
                b'<feed xmlns="http://www.w3.org/2005/Atom"><title>&x;</title></feed>')
        try:
            feed = opds.parse(data, BASE)
        except opds.NotOpdsError:
            return
        self.assertNotIn('root', feed.title)


class TestGutenbergShape(unittest.TestCase):

    def test_list(self):
        feed = opds.parse(fx.GUTENBERG_LIST, 'https://books.example/ebooks/search.opds/')
        self.assertEqual([e.title for e in feed.navigation],
                         ['The Glass Meadow', 'Letters from the Weir'])
        self.assertEqual(feed.navigation[0].summary, 'Hester Vane')
        self.assertTrue(feed.navigation[0].thumbnail.startswith('data:image/png'))
        self.assertTrue(feed.next.endswith('start_index=26'))

    def test_editions_are_merged(self):
        feed = opds.parse(fx.GUTENBERG_BOOK, 'https://books.example/ebooks/90001.opds')
        book = feed.single_book
        self.assertIsNotNone(book)
        self.assertEqual(len(book.acquisitions), 5)
        self.assertEqual(book.issued, '1999-01-01')
        self.assertEqual(book.cover, 'https://books.example/cache/90001.cover.medium.jpg')
        best = opds.best_acquisition(book)
        self.assertEqual(best.href, 'https://books.example/ebooks/90001.epub3.images')
        formats = [a.format for a in opds.available_acquisitions(book)]
        self.assertEqual(formats, ['epub', 'epub', 'epub', 'mobi', 'mobi'])


class TestManyBooksShape(unittest.TestCase):

    def test_books_as_sections(self):
        data = """<?xml version="1.0"?>
<feed xmlns="http://www.w3.org/2005/Atom">
  <title>Search Results</title>
  <entry>
    <title type="xhtml"><div xmlns="http://www.w3.org/1999/xhtml">Moth Season</div></title>
    <id>urn:example:1</id>
    <author><name>Iris Fallow</name></author>
    <author><name/></author>
    <link type="application/atom+xml" href="https://books.example/opds/title_detail/7"/>
    <link rel="http://opds-spec.org/thumbnail" type="image/jpeg" href="/img/moth.jpg"/>
  </entry>
  <entry>
    <title>Second</title>
    <link type="text/html" href="https://books.example/opds/title_detail/8"/>
  </entry>
  <entry>
    <title>A web page only</title>
    <link type="text/html" href="https://books.example/about"/>
  </entry>
</feed>"""
        feed = opds.parse(data, 'https://books.example/opds/search?q=moth')
        moth, second = feed.navigation
        self.assertEqual(moth.title, 'Moth Season')
        self.assertEqual(moth.authors, ['Iris Fallow'])
        self.assertTrue(moth.looks_like_book)
        self.assertEqual(second.href, 'https://books.example/opds/title_detail/8')
        self.assertFalse(second.looks_like_book)


class TestOpds2(unittest.TestCase):

    def setUp(self):
        self.feed = opds.parse(fx.OPDS2_FEED, 'https://fern.example/v2/home.json',
                               'application/opds+json')

    def test_feed(self):
        feed = self.feed
        self.assertEqual(feed.title, 'Fern Hill Books')
        self.assertEqual(feed.next, 'https://fern.example/v2/home.json?page=2')
        self.assertEqual([e.title for e in feed.navigation], ['New', 'Popular', 'Staff Picks'])
        self.assertEqual(feed.navigation[1].count, 40)
        self.assertEqual(feed.navigation[2].href, 'https://fern.example/v2/picks.json')
        self.assertEqual([b.title for b in feed.books],
                         ['Quiet Engines', 'Paper Lanterns', 'Tidewater'])
        language = feed.facets[0]
        self.assertEqual(language.title, 'Language')
        self.assertEqual(language.active.title, 'All')
        self.assertEqual(language.facets[0].count, 7)

    def test_publication(self):
        engines, lanterns, tidewater = self.feed.books
        self.assertEqual(engines.authors, ['Ilse Varga', 'Pell Dunmore'])
        self.assertEqual(engines.categories, ['Science Fiction', 'Robots'])
        self.assertEqual((engines.series, engines.series_index), ('The Engine Cycle', 1.0))
        self.assertEqual(engines.publisher, 'Brass Owl')
        self.assertEqual(engines.issued, '2021-06-01')
        self.assertEqual(engines.identifiers, {'isbn': '9780000000026'})
        self.assertEqual(engines.cover, 'https://fern.example/v2/img/qe.jpg')
        self.assertEqual(engines.thumbnail, 'https://fern.example/v2/img/qe.jpg')
        self.assertIn('<em>listen</em>', engines.summary)
        free, paid = engines.acquisitions
        self.assertTrue(free.available)
        self.assertEqual((paid.price, paid.currency, paid.available), (7.5, 'USD', False))
        self.assertTrue(lanterns.acquisitions[0].drm)
        self.assertIsNone(opds.best_acquisition(lanterns))
        self.assertEqual(lanterns.authors, ['Ola Brisk'])
        self.assertEqual(opds.best_acquisition(tidewater).format, 'pdf')
        self.assertEqual(tidewater.authors, ['Ruth Marrow'])

    def test_search_template(self):
        self.assertEqual(self.feed.search.kind, 'opds2')
        url = opds.expand_template(self.feed.search.href, 'brass & owls')
        self.assertEqual(url, 'https://fern.example/v2/search?query=brass%20%26%20owls')


class TestSearch(unittest.TestCase):

    def test_opensearch(self):
        template = opds.parse_opensearch(fx.OPENSEARCH, 'https://books.example/opds/s.xml')
        self.assertEqual(template, 'https://books.example/opds/search?q={searchTerms}'
                                   '&page={startPage?}&lang={language}')
        self.assertEqual(opds.expand_template(template, ' the lantern '),
                         'https://books.example/opds/search?q=the%20lantern&page=&lang=*')

    def test_opensearch_without_a_feed_url(self):
        data = ('<OpenSearchDescription xmlns="http://a9.com/-/spec/opensearch/1.1/">'
                '<Url type="text/html" template="/s?q={searchTerms}"/></OpenSearchDescription>')
        with self.assertRaises(opds.NotOpdsError):
            opds.parse_opensearch(data, BASE)

    def test_templates(self):
        self.assertEqual(opds.expand_template('/s{?query}', 'a b'), '/s?query=a%20b')
        self.assertEqual(opds.expand_template('/s?x=1{&query,author}', 'é'),
                         '/s?x=1&query=%C3%A9')
        self.assertEqual(opds.expand_template('/s/{query}/{startIndex}', 'x'), '/s/x/1')
        self.assertEqual(opds.expand_template('/s?q={searchTerms}&n={count?}', 'x'),
                         '/s?q=x&n=')


class TestChoosing(unittest.TestCase):

    def test_format_order(self):
        lantern = opds.parse(fx.ATOM_BOOKS, BASE).books[0]
        self.assertEqual([a.format for a in opds.available_acquisitions(lantern)],
                         ['epub', 'kepub', 'pdf'])

    def test_sample_last(self):
        entry = opds.Entry('Sample', acquisitions=[
            opds.Acquisition('/s.epub', 'application/epub+zip', 'epub', kind='sample'),
            opds.Acquisition('/f.pdf', 'application/pdf', 'pdf')])
        self.assertEqual(opds.best_acquisition(entry).format, 'pdf')

    def test_unknown_format(self):
        entry = opds.Entry('Odd', acquisitions=[
            opds.Acquisition('/f.lit', 'application/x-ms-reader', None)])
        self.assertIsNone(opds.best_acquisition(entry))
        self.assertEqual(opds._format_of('application/zip', '/a/b.cbz'), 'cbz')
        self.assertEqual(opds._format_of('application/x-mobipocket-ebook', '/b.azw3'), 'azw3')


class TestInLibrary(unittest.TestCase):

    def test_matching(self):
        with temporary_library() as library:
            by_title = add_book(library, 'The Lantern Keeper', authors=('Tomas Wren',))
            other = add_book(library, 'Quiet Engines', authors=('Somebody Else',))
            library.update_book(other, identifiers={'isbn': '9780000000026'})
            feed = opds.parse(fx.ATOM_BOOKS, BASE)
            self.assertEqual(opds.find_in_library(library, feed.books[0]), by_title)
            self.assertIsNone(opds.find_in_library(library, feed.books[1]))
            engines = opds.parse(fx.OPDS2_FEED, 'https://fern.example/').books[0]
            self.assertEqual(opds.find_in_library(library, engines), other)
            book = library.book(other)
            entry = opds.Entry('Another Title', identifiers={'uuid': book.uuid.upper()})
            self.assertEqual(opds.find_in_library(library, entry), other)


class TestCatalogs(unittest.TestCase):

    def test_builtin_when_empty(self):
        catalogs = opds.load_catalogs('')
        self.assertEqual(catalogs[0].id, 'gutenberg')
        self.assertTrue(all(c.url.startswith('https://') for c in catalogs))

    def test_round_trip(self):
        catalogs = opds.load_catalogs('')[1:]
        catalogs.append(opds.Catalog('x1', 'Home Server', 'https://192.0.2.4/opds', 'reader'))
        text = opds.dump_catalogs(catalogs)
        loaded = opds.load_catalogs(text)
        self.assertEqual(loaded, catalogs)
        self.assertNotIn('gutenberg', text)
        self.assertNotIn('password', text)

    def test_broken_setting(self):
        self.assertEqual(opds.load_catalogs('{oops'), opds.builtin_catalogs())
        self.assertEqual(opds.load_catalogs('[{"title": "no url"}]'), [])

    def test_normalise_url(self):
        self.assertEqual(opds.normalise_url(' books.example/opds '), 'https://books.example/opds')
        self.assertEqual(opds.normalise_url('http://a/b'), 'http://a/b')


class TestNames(unittest.TestCase):

    class Response:
        def __init__(self, url, disposition=''):
            self.url = url
            self.headers = {'Content-Disposition': disposition} if disposition else {}

    def name(self, url, format, disposition='', title='The Lantern Keeper'):
        acquisition = opds.Acquisition(url, format=format)
        return opds.download_name(self.Response(url, disposition), acquisition, title)

    def test_names(self):
        self.assertEqual(self.name('https://x/get/1', 'epub', 'attachment; filename="A b.epub"'),
                         'A b.epub')
        self.assertEqual(self.name('https://x/get/1', 'epub',
                                   "attachment; filename*=UTF-8''%C3%89t%C3%A9.epub"), 'Été.epub')
        self.assertEqual(self.name('https://x/files/Night%20Ferry.epub', 'epub'),
                         'Night Ferry.epub')
        self.assertEqual(self.name('https://x/ebooks/9.epub3.images', 'epub'),
                         'The Lantern Keeper.epub')
        self.assertEqual(self.name('https://x/get/1', 'kepub', 'attachment; filename=k.epub'),
                         'k.kepub.epub')
        self.assertEqual(self.name('https://x/b.azw3', 'mobi'), 'b.azw3')
        self.assertEqual(self.name('https://x/get', 'epub', 'attachment; filename="../../x"'),
                         'x.epub')
        self.assertEqual(self.name('https://x/get', 'pdf', title='a/b: c?'), 'a b c.pdf')


class ServerTestCase(unittest.TestCase):

    def setUp(self):
        self.directory = pathlib.Path(tempfile.mkdtemp(prefix='bookcase-opds-'))
        self.epub = make_epub(self.directory / 'source.epub', title='Winter Orchard',
                              authors=('Ada Lark',)).read_bytes()

    def tearDown(self):
        shutil.rmtree(self.directory, ignore_errors=True)


class TestClient(ServerTestCase):

    def test_feed_pagination_and_cache(self):
        routes = {'/opds/new': Route(fx.ATOM_BOOKS),
                  '/opds/new?page=2': Route(fx.ATOM_BOOKS_PAGE_2)}
        with CatalogServer(routes) as server:
            client = opds.Client()
            feed = client.feed(server.url('/opds/new'))
            self.assertEqual(len(feed.books), 3)
            page = client.feed(feed.next)
            self.assertEqual([b.title for b in page.books], ['Winter Orchard'])
            self.assertEqual(page.previous, server.url('/opds/new?page=1'))
            client.feed(server.url('/opds/new'))
            self.assertEqual(len(server.requests), 2)  # the first page came from the cache
            client.feed(server.url('/opds/new'), refresh=True)
            self.assertEqual(len(server.requests), 3)
            self.assertEqual(server.requests[0][1]['User-Agent'], opds.USER_AGENT)

    def test_search_through_opensearch(self):
        routes = {'/opds': Route(fx.ATOM_NAVIGATION),
                  '/opds/search.xml': Route(fx.OPENSEARCH, 'application/opensearchdescription+xml'),
                  '/opds/search': Route(fx.ATOM_BOOKS_PAGE_2)}
        with CatalogServer(routes) as server:
            client = opds.Client()
            feed = client.feed(server.url('/opds'))
            url = client.search_url(feed, 'winter')
            self.assertEqual(url, server.url('/opds/search?q=winter&page=&lang=*'))
            self.assertEqual(client.feed(url).books[0].title, 'Winter Orchard')
            client.search_url(feed, 'again')
            self.assertEqual(sum(path == '/opds/search.xml' for path, _h in server.requests), 1)
            self.assertIsNone(client.search_url(feed, '  '))

    def test_credentials(self):
        routes = {'/opds': Route(fx.ATOM_NAVIGATION, auth=('reader', 's3cret'))}
        with CatalogServer(routes) as server:
            with self.assertRaises(opds.AuthError):
                opds.Client().feed(server.url('/opds'))
            with self.assertRaises(opds.AuthError):
                opds.Client('reader', 'wrong', server.url('/')).feed(server.url('/opds'))
            feed = opds.Client('reader', 's3cret', server.url('/')).feed(server.url('/opds'))
            self.assertEqual(feed.title, 'Harbour Lane Library')

    def test_credentials_stay_with_their_host(self):
        routes = {'/away': Route('', location='http://localhost:{port}/opds'),
                  '/opds': Route(fx.ATOM_NAVIGATION)}
        with CatalogServer(routes) as server:
            client = opds.Client('reader', 's3cret', server.url('/'))
            client.feed(server.url('/away'))
            first, second = server.requests
            self.assertIn('Authorization', first[1])
            self.assertNotIn('Authorization', second[1])

    def test_errors(self):
        routes = {'/html': Route('<html><head><title>x</title></head></html>', 'text/html'),
                  '/page': Route('<html><head><link rel="alternate" '
                                 'type="application/atom+xml;profile=opds-catalog" '
                                 'href="/opds"></head></html>', 'text/html'),
                  '/opds': Route(fx.ATOM_NAVIGATION),
                  '/busy': Route('', status=503)}
        with CatalogServer(routes) as server:
            client = opds.Client()
            with self.assertRaises(opds.NotOpdsError):
                client.feed(server.url('/html'))
            with self.assertRaises(opds.OpdsError) as caught:
                client.feed(server.url('/missing'))
            self.assertIn('nothing at this address', str(caught.exception))
            with self.assertRaises(opds.OpdsError):
                client.feed(server.url('/busy'))
            self.assertEqual(client.feed(server.url('/page')).title, 'Harbour Lane Library')
            port = server.port
        with self.assertRaises(opds.OfflineError):
            opds.Client(timeout=2).feed(f'http://127.0.0.1:{port}/opds')

    def test_download(self):
        routes = {'/get/4.epub': Route(self.epub, 'application/epub+zip',
                                       disposition='attachment; filename="Winter.epub"'),
                  '/login': Route('<html>Sign in</html>', 'text/html')}
        with CatalogServer(routes) as server:
            client = opds.Client()
            seen = []
            acquisition = opds.Acquisition(server.url('/get/4.epub'), 'application/epub+zip',
                                           'epub')
            path = client.download(acquisition, self.directory / 'dl',
                                   progress=lambda done, total: seen.append((done, total)))
            self.assertEqual(os.path.basename(path), 'Winter.epub')
            self.assertEqual(pathlib.Path(path).read_bytes(), self.epub)
            self.assertEqual(seen[-1], (len(self.epub), len(self.epub)))
            again = client.download(acquisition, self.directory / 'dl')
            self.assertEqual(os.path.basename(again), 'Winter (2).epub')
            with self.assertRaises(opds.Cancelled):
                client.download(acquisition, self.directory / 'dl', cancelled=lambda: True)
            login = opds.Acquisition(server.url('/login'), 'application/epub+zip', 'epub')
            with self.assertRaises(opds.OpdsError):
                client.download(login, self.directory / 'dl')
            self.assertEqual(sorted(os.listdir(self.directory / 'dl')),
                             ['Winter (2).epub', 'Winter.epub'])

    def test_thumbnails(self):
        routes = {'/c.png': Route(b'\x89PNG fake', 'image/png')}
        with CatalogServer(routes) as server:
            cache = opds.ThumbnailCache(str(self.directory / 'covers'))
            client = opds.Client()
            path = cache.fetch(server.url('/c.png'), client)
            self.assertEqual(pathlib.Path(path).read_bytes(), b'\x89PNG fake')
            self.assertEqual(cache.fetch(server.url('/c.png'), client), path)
            self.assertEqual(len(server.requests), 1)
            data = cache.fetch('data:image/png;base64,iVBORw0KGgo=', client)
            self.assertEqual(pathlib.Path(data).read_bytes()[:4], b'\x89PNG')


class TestDownloads(ServerTestCase):

    def test_download_and_add(self):
        routes = {'/get/4.epub': Route(self.epub, 'application/epub+zip')}
        with temporary_library() as library, CatalogServer(routes) as server:
            root = pathlib.Path(library.path).parent
            covers = CoverStore(root, library)
            importer = Importer(library, covers, root / 'Books')
            downloads = opds.Downloads(importer, folder=str(self.directory / 'partial'))
            entry = opds.parse(fx.ATOM_BOOKS_PAGE_2, server.url('/opds/new?page=2')).books[0]
            acquisition = opds.best_acquisition(entry)
            self.assertEqual(acquisition.href, server.url('/get/4.epub'))
            loop = GLib.MainLoop()
            finished = []

            def on_finished(_downloads, key, book_id, message):
                finished.append((key, book_id, message))
                loop.quit()

            downloads.connect('finished', on_finished)
            downloads.start(entry.key, opds.Client(), acquisition, entry)
            self.assertEqual(downloads.state(entry.key)[0], 'downloading')
            GLib.timeout_add_seconds(20, loop.quit)
            loop.run()
            key, book_id, message = finished[0]
            self.assertEqual((key, message), ('urn:harbour:4', ''))
            self.assertEqual(library.book(book_id).title, 'Winter Orchard')
            self.assertEqual(downloads.state(key), ('done', book_id))
            self.assertTrue(library.files(book_id)[0].path.startswith(str(root / 'Books')))
            self.assertEqual(os.listdir(self.directory / 'partial'), [])
            self.assertEqual(opds.find_in_library(library, entry), book_id)
            covers.shutdown()

    def test_failure(self):
        with temporary_library() as library, CatalogServer({}) as server:
            root = pathlib.Path(library.path).parent
            downloads = opds.Downloads(Importer(library, None, root / 'Books'),
                                       folder=str(self.directory))
            entry = opds.Entry('Gone', id='urn:gone', acquisitions=[opds.Acquisition(
                server.url('/nothing.epub'), 'application/epub+zip', 'epub')])
            loop = GLib.MainLoop()
            finished = []
            downloads.connect('finished', lambda _d, *args: (finished.append(args), loop.quit()))
            downloads.start(entry.key, opds.Client(), entry.acquisitions[0], entry)
            GLib.timeout_add_seconds(20, loop.quit)
            loop.run()
            self.assertEqual(finished[0][:2], ('urn:gone', 0))
            self.assertEqual(downloads.state('urn:gone')[0], 'failed')


class TestKeyring(unittest.TestCase):

    def test_memory(self):
        keyring = opds.memory_keyring()
        self.assertTrue(keyring.store('https://a/opds', 'reader', 'pw'))
        self.assertEqual(keyring.lookup('https://a/opds', 'reader'), 'pw')
        self.assertEqual(keyring.backend.passwords, {'reader on https://a/opds': 'pw'})
        self.assertIsNone(keyring.lookup('https://a/opds', 'other'))
        keyring.clear('https://a/opds', 'reader')
        self.assertIsNone(keyring.lookup('https://a/opds', 'reader'))

    def test_client_for_catalog(self):
        saved = opds.keyring
        opds.keyring = opds.memory_keyring()
        try:
            opds.keyring.store('https://a/opds', 'reader', 'pw')
            client = opds.Client.for_catalog(opds.Catalog('x', 'A', 'https://a/opds', 'reader'))
            self.assertEqual((client.username, client.password), ('reader', 'pw'))
        finally:
            opds.keyring = saved


if __name__ == '__main__':
    unittest.main()
