# SPDX-License-Identifier: GPL-2.0-or-later
# SPDX-FileCopyrightText: 2026 Jack Tully

"""kosync.py against a fake KOReader sync server on 127.0.0.1 (tests/fake_kosync.py); no
internet. Invented users, devices and books."""

import hashlib
import json
import os
import tempfile
import time
import unittest

from tests import ROOT  # noqa: F401  (registers bookcase)
from tests.fake_kosync import FakeServer
from tests.gtk import wait_for

from gi.repository import Gio

from bookcase import importing, kosync

SCHEMA_ID = 'io.github.jackicus.Bookcase'
KEYS = ('sync-server', 'sync-username', 'sync-document-match', 'sync-device-name',
        'sync-device-id')


def make_book(folder, name='Harbour Lights.epub', size=40000, seed=7):
    path = os.path.join(folder, name)
    with open(path, 'wb') as file:
        file.write(bytes((seed * i) % 251 for i in range(size)))
    return path


class ProtocolTest(unittest.TestCase):
    """The pure parts: ids, the offer rule, jump targets, texts."""

    def test_key_is_md5_of_password(self):
        self.assertEqual(kosync.key_for('tide'), hashlib.md5(b'tide').hexdigest())

    def test_normalize_server(self):
        self.assertEqual(kosync.normalize_server(''), kosync.DEFAULT_SERVER)
        self.assertEqual(kosync.normalize_server(' sync.example.org/ '),
                         'https://sync.example.org')
        self.assertEqual(kosync.normalize_server('http://10.0.0.5:7200/'),
                         'http://10.0.0.5:7200')

    def test_document_ids(self):
        with tempfile.TemporaryDirectory() as folder:
            path = make_book(folder)
            self.assertEqual(kosync.document_ids(path), [importing.partial_md5(path)])
            self.assertEqual(kosync.document_ids(path, 'filename'),
                             [hashlib.md5(b'Harbour Lights.epub').hexdigest()])
            copies = [{'hash': 'a' * 32, 'name': 'Harbour Lights - Ada Lark.kepub.epub'},
                      {'hash': importing.partial_md5(path), 'name': 'Harbour Lights.epub'}]
            self.assertEqual(kosync.document_ids(path, 'content', copies),
                             [importing.partial_md5(path), 'a' * 32])
            self.assertEqual(len(kosync.document_ids(path, 'filename', copies)), 2)
            self.assertEqual(kosync.document_ids(os.path.join(folder, 'gone.epub')), [])

    def test_should_offer(self):
        remote = kosync.Remote('d', 0.62, '/body/DocFragment[12]/body/p[3]/text().0',
                               'Kobo Libra', 'KOBO1', 2000)
        self.assertTrue(kosync.should_offer(remote, 0.30, 'epubcfi(/6/4)', 1000, 'ME'))
        # older than the library's last reading: this computer is ahead
        self.assertFalse(kosync.should_offer(remote, 0.30, '', 3000, 'ME'))
        # this computer's own position
        self.assertFalse(kosync.should_offer(remote, 0.30, '', 1000, 'KOBO1'))
        # the same place
        self.assertFalse(kosync.should_offer(remote, 0.6202, '', 1000, 'ME'))
        self.assertFalse(kosync.should_offer(None, 0.3, '', 0, 'ME'))
        # newer and behind (read again from the start elsewhere): offered too
        behind = kosync.Remote('d', 0.05, 'x', 'Kobo Libra', 'KOBO1', 2000)
        self.assertTrue(kosync.should_offer(behind, 0.80, '', 1000, 'ME'))
        # an old server without timestamps: the percentage decides nothing more
        self.assertTrue(kosync.should_offer(kosync.Remote('d', 0.5), 0.2, '', 1000, 'ME'))

    def test_jump_target(self):
        cfi = 'epubcfi(/6/14!/4/2/10,/1:0,/1:20)'
        self.assertEqual(kosync.jump_target(kosync.Remote('d', 0.4, cfi), 'epub'),
                         ('cfi', cfi))
        xpointer = kosync.Remote('d', 0.4, '/body/DocFragment[3]/body/p[1]/text().0')
        self.assertEqual(kosync.jump_target(xpointer, 'epub'), ('fraction', 0.4))
        self.assertEqual(kosync.jump_target(kosync.Remote('d', 0.4, '57'), 'pdf'),
                         ('page', 57))
        self.assertEqual(kosync.jump_target(kosync.Remote('d', 0.4, cfi), 'pdf'),
                         ('fraction', 0.4))

    def test_progress_for(self):
        self.assertEqual(kosync.progress_for('epub', 0.25, 'epubcfi(/6/4)'), 'epubcfi(/6/4)')
        self.assertEqual(kosync.progress_for('pdf', 0.25, '12'), '12')
        self.assertEqual(kosync.progress_for('pdf', 0.25, 'page:12@0.25'), '12')
        self.assertEqual(kosync.progress_for('pdf', 0.25, ''), '0.2500')
        self.assertEqual(kosync.progress_for('epub', 0.25, ''), '0.2500')
        self.assertEqual(kosync.round_percent(0.123456), 0.1235)
        self.assertEqual(kosync.round_percent(1.7), 1.0)

    def test_status_text(self):
        now = 100000
        self.assertEqual(kosync.status_text(0, now=now), 'Not synced yet')
        self.assertEqual(kosync.status_text(now - 10, now=now), 'Synced just now')
        self.assertEqual(kosync.status_text(now - 130, now=now), 'Synced 2 min ago')
        self.assertEqual(kosync.status_text(now - 7300, now=now), 'Synced 2 hours ago')
        self.assertEqual(kosync.status_text(now - 10, waiting=True, now=now),
                         'Waiting for a connection')
        self.assertEqual(kosync.status_text(now, error='Wrong', now=now), 'Wrong')


class StoreTest(unittest.TestCase):

    def test_a_store_that_is_not_an_object_is_started_afresh(self):
        with tempfile.TemporaryDirectory() as directory:
            path = os.path.join(directory, 'sync.json')
            for text in ('[1, 2]', '"text"', '12', 'null', '{"pending": [1]}', '{'):
                with self.subTest(text=text):
                    with open(path, 'w', encoding='utf-8') as file:
                        file.write(text)
                    store = kosync.Store(path)
                    self.assertEqual(store.pending(), {})
                    store.set_pending('doc', {'percentage': 0.5})
                    self.assertEqual(kosync.Store(path).pending(),
                                     {'doc': {'percentage': 0.5}})
            self.assertEqual(sorted(os.listdir(directory)), ['sync.json'])


class ClientTest(unittest.TestCase):
    def setUp(self):
        self.server = FakeServer().start()
        self.addCleanup(self.server.stop)

    def test_register_login_push_pull(self):
        client = kosync.Client(self.server.url)
        client.register('ada', 'tide')
        self.assertEqual(self.server.users['ada'], kosync.key_for('tide'))
        client.login()
        method, path, headers, _body = self.server.requests[-1]
        self.assertEqual((method, path), ('GET', '/users/auth'))
        self.assertEqual(headers['accept'], 'application/vnd.koreader.v1+json')
        self.assertEqual(headers['x-auth-user'], 'ada')
        self.assertEqual(headers['x-auth-key'], kosync.key_for('tide'))
        self.assertIsNone(client.pull('0123abcd'))  # {}: nothing stored
        stamp = client.push('0123abcd', 'epubcfi(/6/4)', 0.4321, 'Desk', 'DEV1')
        self.assertGreater(stamp, 0)
        body = self.server.requests[-1][3]
        self.assertEqual(body, {'document': '0123abcd', 'progress': 'epubcfi(/6/4)',
                                'percentage': 0.4321, 'device': 'Desk', 'device_id': 'DEV1'})
        remote = client.pull('0123abcd')
        self.assertEqual((remote.percentage, remote.cfi, remote.device, remote.device_id),
                         (0.4321, 'epubcfi(/6/4)', 'Desk', 'DEV1'))
        self.assertTrue(client.health())

    def _redirector(self, location):
        """A server on 127.0.0.1 answering every GET with a 302 to `location`."""
        import threading
        from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

        seen = []

        class Handler(BaseHTTPRequestHandler):
            def log_message(self, *_args):
                pass

            def do_GET(self):
                seen.append(dict((k.lower(), v) for k, v in self.headers.items()))
                self.send_response(302)
                self.send_header('Location', location)
                self.send_header('Content-Length', '0')
                self.end_headers()

        httpd = ThreadingHTTPServer(('127.0.0.1', 0), Handler)
        threading.Thread(target=httpd.serve_forever, daemon=True).start()
        self.addCleanup(httpd.server_close)
        self.addCleanup(httpd.shutdown)
        return f'http://127.0.0.1:{httpd.server_address[1]}', seen

    def test_the_key_never_follows_a_redirect_to_another_host(self):
        client = kosync.Client(self.server.url)
        client.register('ada', 'tide')
        elsewhere = self.server.url.replace('127.0.0.1', 'localhost') + '/users/auth'
        url, seen = self._redirector(elsewhere)
        with self.assertRaises(kosync.SyncError) as caught:
            kosync.Client(url, 'ada', kosync.key_for('tide')).login()
        self.assertTrue(caught.exception.unauthorized)  # it arrived without the key
        self.assertEqual(seen[0]['x-auth-key'], kosync.key_for('tide'))
        headers = self.server.requests[-1][2]
        self.assertNotIn('x-auth-key', headers)
        self.assertNotIn('x-auth-user', headers)

    def test_the_key_follows_a_redirect_on_its_own_server(self):
        import urllib.request

        handler = kosync._KeyRedirect()
        request = urllib.request.Request('http://sync.example/users/auth', headers={
            'x-auth-user': 'ada', 'x-auth-key': 'k'})
        for target, kept in (('http://sync.example/v1/users/auth', True),
                             ('https://sync.example/users/auth', True),
                             ('http://sync.example:8080/users/auth', False),
                             ('http://other.example/users/auth', False)):
            with self.subTest(target=target):
                new = handler.redirect_request(request, None, 302, 'Found', {}, target)
                names = {name.lower() for name in new.headers}
                self.assertEqual('x-auth-key' in names, kept)
                self.assertEqual('x-auth-user' in names, kept)
        secure = urllib.request.Request('https://sync.example/users/auth', headers={
            'x-auth-key': 'k'})
        downgraded = handler.redirect_request(secure, None, 302, 'Found', {},
                                              'http://sync.example/users/auth')
        self.assertNotIn('x-auth-key', {name.lower() for name in downgraded.headers})
        import urllib.error

        with self.assertRaises(urllib.error.HTTPError) as caught:
            handler.redirect_request(request, None, 302, 'Found', {}, 'file:///etc/hostname')
        caught.exception.close()

    def test_errors(self):
        client = kosync.Client(self.server.url)
        client.register('ada', 'tide')
        with self.assertRaises(kosync.SyncError) as caught:
            kosync.Client(self.server.url).register('ada', 'other')
        self.assertEqual(caught.exception.code, kosync.USER_EXISTS)
        self.assertIn('taken', str(caught.exception))
        with self.assertRaises(kosync.SyncError) as caught:
            kosync.Client(self.server.url, 'ada', kosync.key_for('wrong')).login()
        self.assertTrue(caught.exception.unauthorized)
        self.server.closed = True
        with self.assertRaises(kosync.SyncError) as caught:
            kosync.Client(self.server.url).register('ben', 'x')
        self.assertEqual(caught.exception.code, kosync.REGISTRATION_CLOSED)
        self.server.down = True
        with self.assertRaises(kosync.SyncError) as caught:
            client.login()
        self.assertTrue(caught.exception.offline)

    def test_unreachable(self):
        url = self.server.url
        self.server.stop()
        self.addCleanup(lambda: None)
        with self.assertRaises(kosync.SyncError) as caught:
            kosync.Client(url, 'ada', 'k', timeout=1).login()
        self.assertTrue(caught.exception.offline)
        self.assertIn('Could not reach', str(caught.exception))
        self.server = FakeServer().start()  # setUp's cleanup stops this one

    def test_timeout(self):
        self.server.users['ada'] = 'k'
        self.server.delay = 1.5
        started = time.monotonic()
        with self.assertRaises(kosync.SyncError) as caught:
            kosync.Client(self.server.url, 'ada', 'k').pull('doc', timeout=0.3)
        self.assertTrue(caught.exception.offline)
        self.assertLess(time.monotonic() - started, 1.4)


class SyncServiceTest(unittest.TestCase):
    def setUp(self):
        self.server = FakeServer().start()
        self.addCleanup(self.server.stop)
        self.server.users['ada'] = kosync.key_for('tide')
        self.settings = Gio.Settings.new(SCHEMA_ID)
        for key in KEYS:
            self.settings.reset(key)
        self.addCleanup(lambda: [self.settings.reset(key) for key in KEYS])
        self.folder = tempfile.TemporaryDirectory()
        self.addCleanup(self.folder.cleanup)
        self.book = make_book(self.folder.name)
        self.document = importing.partial_md5(self.book)
        self.keyring = kosync.MemoryKeyring()
        self.sync = self.make_sync()

    def make_sync(self):
        sync = kosync.Sync(self.settings, os.path.join(self.folder.name, 'sync.json'),
                           keyring=self.keyring, watch=False)
        self.addCleanup(sync.shutdown)
        return sync

    def sign_in(self, password='tide', create=False, user='ada'):
        results = []
        self.sync.sign_in(self.server.url, user, password, create, results.append)
        self.assertTrue(wait_for(lambda: results))
        return results[0]

    def test_sign_in_keeps_key_in_keyring(self):
        self.assertIsNotNone(self.sign_in('wrong'))
        self.assertFalse(self.sync.signed_in())
        self.assertIsNone(self.sign_in())
        self.assertTrue(self.sync.signed_in())
        self.assertEqual(self.settings.get_string('sync-username'), 'ada')
        self.assertEqual(self.settings.get_string('sync-server'), self.server.url)
        self.assertEqual(self.keyring.lookup(kosync.account_name(self.server.url, 'ada')),
                         kosync.key_for('tide'))
        checked = []
        self.sync.check(checked.append)
        self.assertTrue(wait_for(lambda: checked))
        self.assertIsNone(checked[0])
        self.sync.sign_out()
        self.assertFalse(self.sync.signed_in())
        self.assertTrue(wait_for(lambda: not self.keyring.passwords))

    def test_create_account(self):
        self.assertIsNone(self.sign_in('sea', create=True, user='ben'))
        self.assertEqual(self.server.users['ben'], kosync.key_for('sea'))
        self.assertIn('taken', str(self.sign_in('x', create=True, user='ben')))

    def test_push_after_quiet_and_pull(self):
        self.sign_in()
        self.sync.position(1, self.book, 'epub', 0.25, 'epubcfi(/6/8)')
        self.assertEqual(len(self.sync._timers), 1)  # waiting for PUSH_DELAY_S of quiet
        self.sync.position(1, self.book, 'epub', 0.26, 'epubcfi(/6/10)')
        self.assertEqual(len(self.sync._timers), 1)
        self.sync.flush(1)
        self.assertTrue(wait_for(lambda: ('ada', self.document) in self.server.progress))
        record = self.server.progress[('ada', self.document)]
        self.assertEqual((record['percentage'], record['progress']), (0.26, 'epubcfi(/6/10)'))
        self.assertEqual(record['device_id'], self.sync.device_id())
        self.assertTrue(record['device'])
        self.assertTrue(wait_for(lambda: self.sync.status(1)[0] > 0))
        # flushing again pushes nothing new
        count = len(self.server.requests)
        self.sync.flush(1)
        self.sync.wait()
        self.assertEqual(len(self.server.requests), count)

        self.server.store('ada', self.document, 0.62, '/body/DocFragment[12]/body/p[3]/text().0',
                          'Kobo Libra', 'KOBO1', time.time() + 5)
        results = []
        self.sync.pull(1, self.book, 'epub', lambda remote, error: results.append(
            (remote, error)))
        self.assertTrue(wait_for(lambda: results))
        remote, error = results[0]
        self.assertIsNone(error)
        self.assertEqual((remote.percentage, remote.device), (0.62, 'Kobo Libra'))
        self.assertTrue(kosync.should_offer(remote, 0.26, 'epubcfi(/6/10)', time.time(),
                                            self.sync.device_id()))

    def test_copies_pull_newest_and_push_all(self):
        self.sign_in()
        copy = make_book(self.folder.name, 'Harbour Lights - Ada Lark.kepub.epub', seed=11)
        self.sync.remember_copy(1, copy)
        copy_id = importing.partial_md5(copy)
        self.assertEqual(self.sync.ids(1, self.book), [self.document, copy_id])
        self.server.store('ada', self.document, 0.1, 'x', 'Old', 'O1', 100)
        self.server.store('ada', copy_id, 0.7, 'y', 'Kobo Libra', 'KOBO1', 200)
        results = []
        self.sync.pull(1, self.book, 'epub', lambda remote, error: results.append(remote))
        self.assertTrue(wait_for(lambda: results))
        self.assertEqual(results[0].device, 'Kobo Libra')
        self.sync.position(1, self.book, 'epub', 0.8, 'epubcfi(/6/20)')
        self.sync.flush()
        self.assertTrue(wait_for(lambda: self.server.progress[('ada', copy_id)]['percentage']
                                 == 0.8 and self.server.progress[('ada', self.document)]
                                 ['percentage'] == 0.8))
        # the copies are kept in the store
        with open(os.path.join(self.folder.name, 'sync.json'), encoding='utf-8') as file:
            self.assertEqual(json.load(file)['copies']['1'][0]['hash'], copy_id)

    def test_filename_matching(self):
        self.settings.set_string('sync-document-match', 'filename')
        self.sign_in()
        self.sync.position(2, self.book, 'epub', 0.5, 'epubcfi(/6/2)')
        self.sync.flush()
        name_id = hashlib.md5(b'Harbour Lights.epub').hexdigest()
        self.assertTrue(wait_for(lambda: ('ada', name_id) in self.server.progress))

    def test_offline_queue_and_retry(self):
        self.sign_in()
        self.server.down = True
        self.sync.position(3, self.book, 'epub', 0.4, 'epubcfi(/6/4)')
        self.sync.flush()
        self.sync.wait()
        self.assertTrue(wait_for(lambda: self.sync.status(3)[1]))  # waiting
        self.assertEqual(kosync.status_text(*self.sync.status(3)), 'Waiting for a connection')
        self.assertIn(self.document, self.sync.store.pending())
        # a new start reads the queue back
        again = self.make_sync()
        self.assertIn(self.document, again.store.pending())
        self.server.down = False
        self.sync.retry()
        self.assertTrue(wait_for(lambda: not self.sync.store.pending()))
        self.assertEqual(self.server.progress[('ada', self.document)]['percentage'], 0.4)
        self.assertTrue(wait_for(lambda: self.sync.status(3)[0] > 0
                                 and not self.sync.status(3)[1]))

    def test_wrong_key_is_an_error_not_a_queue(self):
        self.sign_in()
        self.server.users['ada'] = kosync.key_for('changed elsewhere')
        self.sync.position(4, self.book, 'epub', 0.4, 'epubcfi(/6/4)')
        self.sync.flush()
        self.assertTrue(wait_for(lambda: self.sync.status(4)[2]))
        self.assertFalse(self.sync.store.pending())
        self.assertIn('password', self.sync.status(4)[2])

    def test_signed_out_does_nothing(self):
        self.sync.position(5, self.book, 'epub', 0.4, 'x')
        self.assertFalse(self.sync._timers)
        results = []
        self.sync.pull(5, self.book, 'epub', lambda remote, error: results.append(
            (remote, error)))
        self.assertTrue(wait_for(lambda: results))
        self.assertEqual(results[0], (None, None))
        self.assertFalse(self.server.requests)


if __name__ == '__main__':
    unittest.main()
