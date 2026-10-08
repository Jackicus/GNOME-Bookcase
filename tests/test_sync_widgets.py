# SPDX-License-Identifier: GPL-2.0-or-later
# SPDX-FileCopyrightText: 2026 Jack Tully

"""The Sync preferences page and the reader's sync banner and menu, against the fake
KOReader sync server (tests/fake_kosync.py) on 127.0.0.1. Invented users and devices."""

import os
import pathlib
import shutil
import tempfile
import time
import unittest
from unittest import mock

from tests import ROOT  # noqa: F401  (registers bookcase)
from tests.dialog_support import fake_app
from tests.fake_kosync import FakeServer
from tests.gtk import pump, requires_gtk, wait_for
from tests.support import make_epub

from bookcase import importing, kosync

KEYS = ('sync-server', 'sync-username', 'sync-document-match', 'sync-device-name',
        'sync-device-id')


def _sync(settings, folder):
    for key in KEYS:
        settings.reset(key)
    return kosync.Sync(settings, os.path.join(folder, 'sync.json'),
                       keyring=kosync.MemoryKeyring(), watch=False)


@requires_gtk
class SyncPageTest(unittest.TestCase):
    def setUp(self):
        self.server = FakeServer().start()
        self.addCleanup(self.server.stop)
        self.server.users['ada'] = kosync.key_for('tide')

    def test_sign_in_test_and_sign_out(self):
        from gi.repository import Gtk

        from bookcase.dialogs.sync_prefs import SyncPage

        with fake_app() as app, tempfile.TemporaryDirectory() as folder:
            app.sync = _sync(app.settings, folder)
            self.addCleanup(app.sync.shutdown)
            self.addCleanup(lambda: [app.settings.reset(key) for key in KEYS])
            page = SyncPage(app)
            window = Gtk.Window(child=page)
            self.addCleanup(window.destroy)
            self.assertTrue(page.form.get_visible())
            self.assertFalse(page.account.get_visible())
            self.assertEqual(page.server_row.get_text(), kosync.DEFAULT_SERVER)

            page.server_row.set_text(self.server.url)
            page.username_row.set_text('ada')
            page.sign_in()
            self.assertTrue(page.error_label.get_visible())  # no password
            page.password_row.set_text('wrong')
            page.sign_in()
            self.assertFalse(page.sign_in_button.get_sensitive())  # busy
            self.assertTrue(wait_for(lambda: page.error_label.get_visible()))
            self.assertIn('password is wrong', page.error_label.get_label())

            page.password_row.set_text('tide')
            page.sign_in()
            self.assertTrue(wait_for(lambda: page.account.get_visible()))
            self.assertFalse(page.form.get_visible())
            self.assertEqual(page.account_row.get_title(), 'Signed in as ada')
            self.assertEqual(page.password_row.get_text(), '')

            page.test()
            self.assertTrue(wait_for(lambda: page.test_row.get_subtitle() == 'Connected'))

            page.method_row.set_selected(1)
            self.assertEqual(app.settings.get_string('sync-document-match'), 'filename')
            page.device_row.set_text('Study Laptop')
            page.device_row.emit('apply')
            self.assertEqual(app.sync.device_name(), 'Study Laptop')

            page.sign_out()
            self.assertTrue(page.form.get_visible())
            self.assertEqual(page.username_row.get_text(), 'ada')
            self.assertFalse(app.sync.signed_in())

    def test_create_account(self):
        from bookcase.dialogs.sync_prefs import SyncPage

        with fake_app() as app, tempfile.TemporaryDirectory() as folder:
            app.sync = _sync(app.settings, folder)
            self.addCleanup(app.sync.shutdown)
            self.addCleanup(lambda: [app.settings.reset(key) for key in KEYS])
            page = SyncPage(app)
            page.server_row.set_text(self.server.url)
            page.username_row.set_text('ben')
            page.password_row.set_text('sea')
            page.sign_in(create=True)
            self.assertTrue(wait_for(lambda: page.account.get_visible()))
            self.assertEqual(self.server.users['ben'], kosync.key_for('sea'))


_apps = []


def _app(library):
    """The application for these tests (registered once: an id of its own, beside
    test_reader_window's)."""
    from gi.repository import Adw, Gio

    if not _apps:
        app = Adw.Application(application_id='io.github.jackicus.Bookcase.SyncTest',
                              flags=Gio.ApplicationFlags.NON_UNIQUE)
        app.settings = Gio.Settings.new('io.github.jackicus.Bookcase')
        app.register(None)
        _apps.append(app)
    app = _apps[0]
    app.library = library
    return app


class StubView:
    def __init__(self):
        self.calls = []

    def go_to(self, target, callback=None):
        self.calls.append(('go_to', target))

    def go_to_fraction(self, fraction):
        self.calls.append(('go_to_fraction', fraction))

    def __getattr__(self, name):
        return lambda *_args, **_kwargs: None  # close(), show_progress(), …


@requires_gtk
class ReaderSyncTest(unittest.TestCase):
    def setUp(self):
        from bookcase.formats import BookInfo
        from bookcase.library import Library

        self.server = FakeServer().start()
        self.addCleanup(self.server.stop)
        self.server.users['ada'] = kosync.key_for('tide')
        self.directory = pathlib.Path(tempfile.mkdtemp(prefix='bookcase-test-'))
        self.addCleanup(shutil.rmtree, self.directory, ignore_errors=True)
        self.library = Library(self.directory / 'library.sqlite')
        self.addCleanup(self.library.close)
        self.app = _app(self.library)
        self.app.sync = _sync(self.app.settings, str(self.directory))
        self.addCleanup(self._stop_sync)
        path = self.directory / 'A Quiet Harbour.epub'
        make_epub(path, chapters=3)
        self.document = importing.partial_md5(str(path))
        info = BookInfo(title='A Quiet Harbour', authors=['Ada Lark'], format='epub')
        self.book_id = self.library.add_book(info, str(path), hash=self.document, size=100)
        self.library.set_progress(self.book_id, 0.1, 'epubcfi(/6/2)')
        results = []
        self.app.sync.sign_in(self.server.url, 'ada', 'tide', False, results.append)
        self.assertTrue(wait_for(lambda: results))

    def _stop_sync(self):
        self.app.sync.shutdown()
        del self.app.sync
        for key in KEYS:
            self.app.settings.reset(key)

    def open(self):
        from bookcase import reader_window
        from bookcase.widgets import book_view

        with mock.patch.object(book_view, 'available', return_value=False):
            window = reader_window.open(self.app, self.book_id)
        self.addCleanup(lambda: (window.close(), pump()))
        # Stand in for the web view: the book "shown", the view a stub.
        window.content_stack.set_visible_child_name('book')
        window.view = StubView()
        return window

    def test_banner_offers_a_newer_place_from_another_device(self):
        self.server.store('ada', self.document, 0.62,
                          '/body/DocFragment[12]/body/p[3]/text().0', 'Kobo Libra', 'KOBO1',
                          time.time() + 60)
        window = self.open()
        sync = window._sync
        self.assertFalse(sync.banner.get_revealed())
        sync.relocated({'fraction': 0.1, 'cfi': 'epubcfi(/6/2)'})  # opened: pulls
        self.assertTrue(wait_for(lambda: sync.banner.get_revealed(), 2))
        self.assertEqual(sync.banner.get_title(), 'Kobo Libra is at 62%')
        sync.banner.emit('button-clicked')
        self.assertEqual(window.view.calls, [('go_to_fraction', 0.62)])
        self.assertFalse(sync.banner.get_revealed())

        # the menu's section: the status, and Sync Now
        menu = sync._menu
        index = sync._section_index()
        self.assertIsNotNone(index)
        label = menu.get_item_attribute_value(index, 'label', None).unpack()
        self.assertEqual(label, 'Synced just now')

        # moving pushes (on closing at the latest), with this computer's id and the CFI
        sync.relocated({'fraction': 0.63, 'cfi': 'epubcfi(/6/14!/4/2)', 'reason': 'page'})
        window.close()
        self.assertTrue(wait_for(lambda: self.server.progress[('ada', self.document)]
                                 ['device_id'] == self.app.sync.device_id()))
        record = self.server.progress[('ada', self.document)]
        self.assertEqual((record['percentage'], record['progress']),
                         (0.63, 'epubcfi(/6/14!/4/2)'))

    def test_a_bookcase_place_is_exact_and_an_older_one_is_not_offered(self):
        cfi = 'epubcfi(/6/8!/4/2/6)'
        self.server.store('ada', self.document, 0.4, cfi, 'Study Laptop', 'OTHER',
                          time.time() + 60)
        window = self.open()
        sync = window._sync
        sync.relocated({'fraction': 0.1, 'cfi': 'epubcfi(/6/2)'})
        self.assertTrue(wait_for(lambda: sync.banner.get_revealed(), 2))
        sync.banner.emit('button-clicked')
        self.assertEqual(window.view.calls, [('go_to', cfi)])

        self.server.store('ada', self.document, 0.9, 'x', 'Kobo Libra', 'KOBO1', 1000)
        # an older place from a Kobo: Sync Now's pull offers nothing
        sync.remote = None
        sync._pulled = False
        sync._baseline = None
        sync.relocated({'fraction': 0.4, 'cfi': cfi})
        requests = len(self.server.requests)
        self.assertTrue(wait_for(lambda: len(self.server.requests) > requests, 2))
        pump()
        self.assertFalse(sync.banner.get_revealed())

    def test_opening_never_pushes(self):
        window = self.open()
        window._sync.relocated({'fraction': 0.1, 'cfi': 'epubcfi(/6/2)'})
        window.close()
        pump()
        self.app.sync.wait()
        self.assertNotIn(('ada', self.document), self.server.progress)
