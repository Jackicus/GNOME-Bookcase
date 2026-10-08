# SPDX-License-Identifier: GPL-2.0-or-later
# SPDX-FileCopyrightText: 2026 Jack Tully

"""Library sharing: the server on 127.0.0.1 with an invented library, asked with urllib and
read back with Bookcase's own OPDS client (opds.py), as a reading app would."""

import base64
import gc
import os
import pathlib
import shutil
import socket
import tempfile
import unittest
import urllib.error
import urllib.request
import xml.etree.ElementTree as ElementTree
from unittest import mock

from tests import ROOT  # noqa: F401  (registers bookcase)
from tests.gtk import wait_for
from tests.support import add_book, make_epub, make_png, temporary_library

from bookcase import formats, opds, passwords, sharing
from bookcase.covers import CoverStore

ATOM = '{http://www.w3.org/2005/Atom}'
USER, PASSWORD = 'ada', 'tide-lamp-gull'


def basic(user=USER, password=PASSWORD):
    token = base64.b64encode(f'{user}:{password}'.encode()).decode()
    return {'Authorization': f'Basic {token}'}


class Response:
    def __init__(self, status, headers, body):
        self.status, self.headers, self.body = status, headers, body


class ServerCase(unittest.TestCase):
    password = PASSWORD

    def setUp(self):
        # Garbage of earlier widget tests is collected here, on the main thread: a GTK
        # object finalized by the collector in a server thread aborts the process.
        gc.collect()
        patch = mock.patch.object(sharing, 'POLL_INTERVAL', 0.02)
        patch.start()
        self.addCleanup(patch.stop)
        context = temporary_library()
        self.library = context.__enter__()
        self.addCleanup(context.__exit__, None, None, None)
        self.folder = pathlib.Path(self.library.path.parent)
        self.covers = CoverStore(self.folder, self.library)
        self.addCleanup(self.covers.shutdown)
        self.harbour = self._epub('A Quiet Harbour', ('Ada Lark',), series='The Tides',
                                  series_index=2, tags=['Sea'])
        self.covers.save(self.harbour, make_png(30, 45, (40, 90, 160)))
        self.lantern = self._epub('Lanterns <b>& Rope</b>', ('Ben Ross',))
        pdf = self.folder / 'atlas.pdf'
        pdf.write_bytes(b'%PDF-1.4\n' + bytes(range(256)) * 40)
        self.atlas = add_book(self.library, 'An Atlas of Coves', ('Cy Moor',), path=pdf,
                              fmt='pdf')
        opened_path = make_epub(self.folder / 'opened.epub', title='Opened Elsewhere')
        self.opened = self.library.add_opened(formats.read(str(opened_path)),
                                              str(opened_path), hash='opened', size=10)
        self.cache = self.folder / 'cache'
        self.server = sharing.Server(self.library.path, self.covers, self.cache,
                                     username=USER, password=self.password).start()
        self.addCleanup(self.server.stop)
        self.base = f'http://127.0.0.1:{self.server.port}'

    def _epub(self, title, authors, **fields):
        path = make_epub(self.folder / f'{len(os.listdir(self.folder))}.epub', title=title,
                         authors=authors)
        return add_book(self.library, title, authors, path=path, **fields)

    def get(self, path, headers=None, auth=True, method='GET'):
        all_headers = dict(basic() if auth else {})
        all_headers.update(headers or {})
        request = urllib.request.Request(self.base + path, headers=all_headers, method=method)
        try:
            with urllib.request.urlopen(request, timeout=10) as response:
                return Response(response.status, response.headers, response.read())
        except urllib.error.HTTPError as error:
            with error:
                return Response(error.code, error.headers, error.read())

    def feed(self, path):
        response = self.get(path)
        self.assertEqual(response.status, 200, path)
        self.assertIn('application/atom+xml', response.headers['Content-Type'])
        root = ElementTree.fromstring(response.body)  # well-formed
        self.assertEqual(root.tag, ATOM + 'feed')
        for name in ('id', 'title', 'updated'):
            self.assertIsNotNone(root.find(ATOM + name), name)
        for entry in root.findall(ATOM + 'entry'):
            for name in ('id', 'title', 'updated'):
                self.assertTrue(entry.findtext(ATOM + name), name)
        return opds.parse(response.body, self.base + path, response.headers['Content-Type'])


class CatalogueTest(ServerCase):

    def test_root_is_a_navigation_feed_with_search(self):
        feed = self.feed('/opds')
        titles = [entry.title for entry in feed.navigation]
        self.assertEqual(titles[:6], ['Recently Added', 'Currently Reading', 'All Books',
                                      'Authors', 'Series', 'Tags'])
        self.assertNotIn('Shelves', titles)  # none yet
        self.assertIsNotNone(feed.search)
        self.assertTrue(all(entry.href.startswith(self.base + '/opds/')
                            for entry in feed.navigation))

    def test_books_have_acquisitions_covers_and_metadata(self):
        feed = self.feed('/opds/all')
        by_title = {entry.title: entry for entry in feed.books}
        self.assertEqual(set(by_title), {'A Quiet Harbour', 'Lanterns <b>& Rope</b>',
                                         'An Atlas of Coves'})  # not the opened book
        harbour = by_title['A Quiet Harbour']
        self.assertEqual(harbour.authors, ['Ada Lark'])
        self.assertEqual(harbour.series, 'The Tides')
        self.assertEqual(harbour.series_index, 2)
        self.assertIn('Sea', harbour.categories)
        acquisition = opds.best_acquisition(harbour)
        self.assertEqual(acquisition.type, 'application/epub+zip')
        self.assertTrue(acquisition.href.endswith('.epub'))
        self.assertTrue(harbour.cover.startswith(self.base + '/cover/'))
        self.assertTrue(harbour.thumbnail.startswith(self.base + '/thumbnail/'))
        atlas = by_title['An Atlas of Coves']
        self.assertEqual(atlas.acquisitions[0].type, 'application/pdf')
        self.assertFalse(atlas.cover)

    def test_navigation_to_an_author_and_a_series(self):
        authors = self.feed('/opds/authors')
        names = {entry.title: entry for entry in authors.navigation}
        self.assertEqual(set(names), {'Ada Lark', 'Ben Ross', 'Cy Moor'})
        books = self.feed(names['Ada Lark'].href[len(self.base):])
        self.assertEqual([entry.title for entry in books.books], ['A Quiet Harbour'])
        series = self.feed('/opds/series')
        self.assertEqual([entry.title for entry in series.navigation], ['The Tides'])

    def test_shelves_appear_once_there_is_one(self):
        shelf = self.library.add_shelf('Summer')
        self.library.add_to_shelf(shelf, [self.atlas])
        root = self.feed('/opds')
        self.assertIn('Shelves', [entry.title for entry in root.navigation])
        books = self.feed(f'/opds/shelf/{shelf}')
        self.assertEqual([entry.title for entry in books.books], ['An Atlas of Coves'])
        self.assertEqual(self.get('/opds/shelf/999').status, 404)

    def test_pages_link_to_the_next(self):
        with mock.patch.object(sharing, 'PAGE_SIZE', 2):
            first = self.feed('/opds/all')
            self.assertEqual(len(first.books), 2)
            self.assertIsNotNone(first.next)
            second = self.feed(first.next[len(self.base):])
            self.assertEqual(len(second.books), 1)
            self.assertFalse(second.next)
            self.assertEqual(self.get('/opds/all?page=3').status, 404)

    def test_search_through_opensearch_with_the_opds_client(self):
        client = opds.Client(username=USER, password=PASSWORD, origin_url=self.base)
        root = client.feed(self.base + '/opds')
        url = client.search_url(root, 'author:lark')
        self.assertTrue(url.startswith(self.base + '/opds/search?q='))
        results = client.feed(url)
        self.assertEqual([entry.title for entry in results.books], ['A Quiet Harbour'])

    def test_opensearch_description(self):
        response = self.get('/opds/opensearch.xml')
        self.assertEqual(response.status, 200)
        root = ElementTree.fromstring(response.body)
        templates = [url.get('template') for url in
                     root.findall('{http://a9.com/-/spec/opensearch/1.1/}Url')]
        self.assertIn(self.base + '/opds/search?q={searchTerms}', templates)

    def test_a_reading_app_downloads_through_the_client(self):
        client = opds.Client(username=USER, password=PASSWORD, origin_url=self.base)
        feed = client.feed(self.base + '/opds/all')
        entry = next(entry for entry in feed.books if entry.title == 'A Quiet Harbour')
        with tempfile.TemporaryDirectory() as folder:
            path = client.download(opds.best_acquisition(entry), folder, title=entry.title)
            self.assertEqual(os.path.basename(path), 'A Quiet Harbour - Ada Lark.epub')
            self.assertEqual(formats.read(path).title, 'A Quiet Harbour')


class ThreadTest(ServerCase):

    def test_requests_never_use_the_main_library(self):
        import threading

        main = threading.get_ident()
        db = self.library.db
        others = []

        class Watched:
            def __getattr__(self, name):
                if threading.get_ident() != main:
                    others.append(name)
                return getattr(db, name)

        self.library.db = Watched()
        self.addCleanup(setattr, self.library, 'db', db)
        for path in ('/opds', '/opds/all', '/search?q=harbour', f'/cover/{self.harbour}',
                     f'/thumbnail/{self.harbour}', f'/get/{self.harbour}/epub/x',
                     f'/get/{self.atlas}/pdf/x'):
            self.assertEqual(self.get(path).status, 200, path)
        self.assertEqual(others, [])


class DownloadTest(ServerCase):

    def _path(self, book_id, fmt):
        return f'/get/{book_id}/{fmt}/x'

    def test_an_epub_carries_the_edited_metadata(self):
        self.library.update_book(self.harbour, title='A Quieter Harbour', series='Tides')
        response = self.get(self._path(self.harbour, 'epub'))
        self.assertEqual(response.status, 200)
        self.assertEqual(response.headers['Content-Type'], 'application/epub+zip')
        disposition = response.headers['Content-Disposition']
        self.assertIn('attachment; filename="A Quieter Harbour - Ada Lark.epub"', disposition)
        self.assertIn("filename*=UTF-8''A%20Quieter%20Harbour%20-%20Ada%20Lark.epub",
                      disposition)
        with tempfile.TemporaryDirectory() as folder:
            path = os.path.join(folder, 'got.epub')
            pathlib.Path(path).write_bytes(response.body)
            info = formats.read(path)
        self.assertEqual(info.title, 'A Quieter Harbour')
        self.assertEqual(info.series, 'Tides')
        self.assertIsNotNone(info.cover)
        # The library's own file is untouched.
        original = self.library.files(self.harbour)[0].path
        self.assertEqual(formats.read(original).title, 'A Quiet Harbour')

    def test_a_pdf_is_the_file_itself_with_ranges(self):
        data = (self.folder / 'atlas.pdf').read_bytes()
        whole = self.get(self._path(self.atlas, 'pdf'))
        self.assertEqual(whole.status, 200)
        self.assertEqual(whole.body, data)
        self.assertEqual(whole.headers['Accept-Ranges'], 'bytes')
        part = self.get(self._path(self.atlas, 'pdf'), {'Range': 'bytes=10-19'})
        self.assertEqual(part.status, 206)
        self.assertEqual(part.body, data[10:20])
        self.assertEqual(part.headers['Content-Range'], f'bytes 10-19/{len(data)}')
        tail = self.get(self._path(self.atlas, 'pdf'), {'Range': 'bytes=-5'})
        self.assertEqual(tail.body, data[-5:])
        open_ended = self.get(self._path(self.atlas, 'pdf'), {'Range': 'bytes=10000-'})
        self.assertEqual(open_ended.body, data[10000:])
        beyond = self.get(self._path(self.atlas, 'pdf'), {'Range': f'bytes={len(data)}-'})
        self.assertEqual(beyond.status, 416)
        self.assertEqual(beyond.headers['Content-Range'], f'bytes */{len(data)}')

    def test_ranges_of_an_epub_come_from_one_copy(self):
        whole = self.get(self._path(self.harbour, 'epub')).body
        part = self.get(self._path(self.harbour, 'epub'), {'Range': 'bytes=100-199'})
        self.assertEqual(part.body, whole[100:200])

    def test_head_sends_no_body(self):
        response = self.get(self._path(self.atlas, 'pdf'), method='HEAD')
        self.assertEqual(response.status, 200)
        self.assertEqual(response.body, b'')
        self.assertEqual(int(response.headers['Content-Length']),
                         (self.folder / 'atlas.pdf').stat().st_size)

    def test_covers_and_thumbnails(self):
        cover = self.get(f'/cover/{self.harbour}')
        self.assertEqual(cover.status, 200)
        self.assertEqual(cover.headers['Content-Type'], 'image/png')
        self.assertTrue(cover.body.startswith(b'\x89PNG'))
        thumbnail = self.get(f'/thumbnail/{self.harbour}?v=1')
        self.assertEqual(thumbnail.status, 200)
        self.assertTrue(thumbnail.body.startswith(b'\x89PNG'))
        self.assertEqual(self.get(f'/cover/{self.atlas}').status, 404)  # none

    def test_a_book_opened_without_adding_is_never_served(self):
        for path in (self._path(self.opened, 'epub'), f'/cover/{self.opened}',
                     f'/thumbnail/{self.opened}'):
            self.assertEqual(self.get(path).status, 404, path)
        html = self.get('/all').body.decode()
        self.assertNotIn('Opened Elsewhere', html)

    def test_no_path_reaches_a_file(self):
        for path in ('/get/1/epub/../../../../etc/passwd', '/get/..%2F..%2Fetc%2Fpasswd/epub/x',
                     '/cover/..%2F..%2Fcovers', '/%2e%2e/%2e%2e/etc/passwd',
                     '/get/0/epub/x', '/get/1/../../etc/passwd', '/get/-1/epub/x',
                     '/get/1/pdf/x', '/opds/author/../../etc', '/thumbnail/1.png',
                     '/get/99999999999/epub/x', '/opds/author/1/2'):
            response = self.get(path)
            self.assertIn(response.status, (400, 404), path)
            self.assertNotIn(b'root:', response.body)

    def test_read_only(self):
        for method in ('POST', 'PUT', 'DELETE'):
            self.assertIn(self.get('/', method=method).status, (405, 501), method)


class HtmlTest(ServerCase):

    def test_home_page(self):
        response = self.get('/')
        self.assertEqual(response.status, 200)
        self.assertEqual(response.headers['Content-Type'], 'text/html; charset=utf-8')
        self.assertIn("default-src 'none'", response.headers['Content-Security-Policy'])
        text = response.body.decode()
        self.assertTrue(text.startswith('<!DOCTYPE html>'))
        for link in ('href="/recent"', 'href="/authors"', 'action="/search"'):
            self.assertIn(link, text)
        self.assertNotIn('<script', text)
        self.assertIn(self.base + '/opds', text)

    def test_search_page_lists_downloads_and_escapes(self):
        text = self.get('/search?q=lanterns').body.decode()
        self.assertIn('Lanterns &lt;b&gt;&amp; Rope&lt;/b&gt;', text)
        self.assertNotIn('<b>&', text)
        self.assertIn(f'href="/get/{self.lantern}/epub/', text)
        harbour = self.get('/search?q=harbour').body.decode()
        self.assertIn('The Tides, book 2', harbour)
        self.assertIn(f'src="/thumbnail/{self.harbour}?v=1"', harbour)

    def test_an_empty_search_and_no_results(self):
        self.assertIn('Search for a title', self.get('/search').body.decode())
        self.assertIn('No books', self.get('/search?q=zzzz').body.decode())

    def test_unknown_pages(self):
        for path in ('/nothing', '/author/x', '/favicon.ico', '/opds/nothing'):
            self.assertEqual(self.get(path).status, 404, path)


class AccessTest(ServerCase):

    def test_a_password_is_asked(self):
        response = self.get('/opds', auth=False)
        self.assertEqual(response.status, 401)
        self.assertIn('Basic realm="Bookcase"', response.headers['WWW-Authenticate'])
        self.assertEqual(self.get('/opds', basic(USER, 'wrong'), auth=False).status, 401)
        self.assertEqual(self.get('/opds', basic('eve', PASSWORD), auth=False).status, 401)
        self.assertEqual(self.get('/opds', {'Authorization': 'Basic !!!'}, auth=False)
                         .status, 401)
        self.assertEqual(self.get(f'/get/{self.atlas}/pdf/x', auth=False).status, 401)
        self.assertEqual(self.get('/opds').status, 200)

    def test_wrong_passwords_lock_an_address_out(self):
        for _attempt in range(sharing.MAX_FAILURES):
            self.assertEqual(self.get('/', basic(USER, 'guess'), auth=False).status, 401)
        locked = self.get('/')  # even the right password, for a while
        self.assertEqual(locked.status, 429)
        self.assertEqual(locked.headers['Retry-After'], str(sharing.LOCKOUT))

    def test_new_credentials_apply_at_once(self):
        self.server.set_credentials('ben', 'rope')
        self.assertEqual(self.get('/').status, 401)
        self.assertEqual(self.get('/', basic('ben', 'rope'), auth=False).status, 200)
        self.server.set_credentials('', None)
        self.assertEqual(self.get('/', auth=False).status, 200)

    def test_a_foreign_host_name_is_refused(self):
        self.assertEqual(self.get('/', {'Host': 'evil.example.com'}).status, 421)
        self.assertEqual(self.get('/', {'Host': 'bookshelf.local:8095'}).status, 200)

    def test_nothing_sensitive_is_logged(self):
        with self.assertLogs('bookcase.sharing', 'DEBUG') as logs:
            sharing.log.debug('start')
            self.get('/search?q=secret-search', basic(USER, 'secret-guess'), auth=False)
            self.get('/search?q=secret-search')
        text = '\n'.join(logs.output)
        for secret in ('secret', PASSWORD, 'search'):
            self.assertNotIn(secret, text)


class OpenServerTest(ServerCase):
    password = None

    def test_without_a_password_anyone_on_the_network_reads(self):
        self.assertEqual(self.get('/opds', auth=False).status, 200)


class HelpersTest(unittest.TestCase):

    def test_client_addresses(self):
        for address in ('127.0.0.1', '192.168.1.20', '10.0.0.5', '172.16.3.4', '169.254.1.1',
                        'fe80::1', 'fd00::1', '::1', '::ffff:192.168.1.3'):
            self.assertTrue(sharing.client_allowed(address), address)
        for address in ('8.8.8.8', '2001:4860::8888', '::ffff:8.8.8.8', 'nonsense'):
            self.assertFalse(sharing.client_allowed(address), address)

    def test_host_names(self):
        for host in ('', '192.168.1.20:8095', '[fe80::1]:8095', 'localhost:8095', 'laptop',
                     'laptop.local', 'nas.lan:8095', 'pc.home.arpa'):
            self.assertTrue(sharing.host_allowed(host), host)
        for host in ('evil.example.com', 'attacker.net:8095', 'a b', 'x/y', '127.0.0.1.evil.com'):
            self.assertFalse(sharing.host_allowed(host), host)

    def test_ranges(self):
        self.assertEqual(sharing.parse_range('bytes=0-9', 100), (0, 9))
        self.assertEqual(sharing.parse_range('bytes=90-', 100), (90, 99))
        self.assertEqual(sharing.parse_range('bytes=-10', 100), (90, 99))
        self.assertEqual(sharing.parse_range('bytes=50-500', 100), (50, 99))
        self.assertEqual(sharing.parse_range('bytes=100-', 100), 'unsatisfiable')
        for header in ('', 'bytes=0-1,5-6', 'items=0-1', 'bytes=9-3', 'bytes=x-', 'bytes=-'):
            self.assertIsNone(sharing.parse_range(header, 100), header)

    def test_the_lockout_ends(self):
        failures = sharing._Failures()
        for moment in range(sharing.MAX_FAILURES):
            self.assertFalse(failures.blocked('10.0.0.2', now=moment))
            failures.failed('10.0.0.2', now=moment)
        self.assertTrue(failures.blocked('10.0.0.2', now=10))
        self.assertFalse(failures.blocked('10.0.0.3', now=10))
        later = sharing.MAX_FAILURES + sharing.LOCKOUT + sharing.FAILURE_WINDOW
        self.assertFalse(failures.blocked('10.0.0.2', now=later))

    def test_generated_passwords(self):
        password = sharing.generate_password()
        self.assertRegex(password, r'^[a-z2-9]{4}-[a-z2-9]{4}-[a-z2-9]{4}$')
        self.assertNotEqual(password, sharing.generate_password())

    def test_lan_addresses_are_private(self):
        for address in sharing.lan_addresses():
            self.assertTrue(sharing._usable(address), address)


def free_port():
    with socket.socket() as probe:
        probe.bind(('127.0.0.1', 0))
        return probe.getsockname()[1]


KEYS = ('sharing-enabled', 'sharing-scope', 'sharing-port', 'sharing-require-password',
        'sharing-username')


class SharingTest(unittest.TestCase):
    """The app's side: the settings start and stop it; the password is made once."""

    def setUp(self):
        from gi.repository import Gio

        # Garbage of earlier widget tests is collected here, on the main thread: a GTK
        # object finalized by the collector in a server thread aborts the process.
        gc.collect()
        context = temporary_library()
        self.library = context.__enter__()
        self.addCleanup(context.__exit__, None, None, None)
        self.settings = Gio.Settings.new('io.github.jackicus.Bookcase')
        for key in KEYS:
            self.settings.reset(key)
        self.addCleanup(lambda: [self.settings.reset(key) for key in KEYS])
        self.settings.set_string('sharing-scope', 'local')
        self.settings.set_int('sharing-port', free_port())
        self.keyring = passwords.MemoryKeyring()
        self.cache = tempfile.mkdtemp(prefix='bookcase-sharing-')
        self.addCleanup(shutil.rmtree, self.cache, True)
        self.sharing = sharing.Sharing(self.settings, self.library, None, keyring=self.keyring,
                                       cache_dir=self.cache, advertise=False)
        self.addCleanup(self.sharing.shutdown)

    def test_on_and_off_with_the_setting(self):
        self.assertEqual(self.sharing.state, 'off')
        self.settings.set_boolean('sharing-enabled', True)
        self.assertTrue(wait_for(lambda: self.sharing.state == 'on'))
        port = self.settings.get_int('sharing-port')
        self.assertEqual(self.sharing.addresses(), [f'http://127.0.0.1:{port}/'])
        password = self.sharing.password
        self.assertRegex(password, r'^[a-z2-9-]{14}$')
        self.assertEqual(self.keyring.lookup(sharing.KEYRING_ACCOUNT), password)
        request = urllib.request.Request(f'http://127.0.0.1:{port}/opds',
                                         headers=basic('reader', password))
        with urllib.request.urlopen(request, timeout=10) as response:
            self.assertEqual(response.status, 200)

        self.settings.set_boolean('sharing-enabled', False)
        self.assertEqual(self.sharing.state, 'off')
        self.assertEqual(self.sharing.addresses(), [])

        self.settings.set_boolean('sharing-enabled', True)  # the same password again
        self.assertTrue(wait_for(lambda: self.sharing.state == 'on'))
        self.assertEqual(self.sharing.password, password)

    def test_a_port_in_use_fails_with_a_sentence(self):
        with socket.socket() as taken:
            taken.bind(('127.0.0.1', 0))
            taken.listen()
            self.settings.set_int('sharing-port', taken.getsockname()[1])
            self.settings.set_boolean('sharing-enabled', True)
            self.assertTrue(wait_for(lambda: self.sharing.state == 'failed'))
        self.assertIn('in use', self.sharing.error)

    def test_a_new_password_reaches_the_server(self):
        self.settings.set_boolean('sharing-enabled', True)
        self.assertTrue(wait_for(lambda: self.sharing.state == 'on'))
        done = []
        self.sharing.set_password('harbour-2', done.append)
        self.assertTrue(wait_for(lambda: done))
        self.assertEqual(done, [None])
        self.assertEqual(self.keyring.lookup(sharing.KEYRING_ACCOUNT), 'harbour-2')
        self.assertEqual(self.sharing.server.credentials, ('reader', 'harbour-2'))
        self.settings.set_string('sharing-username', 'ada')
        self.assertEqual(self.sharing.server.credentials, ('ada', 'harbour-2'))


if __name__ == '__main__':
    unittest.main()
