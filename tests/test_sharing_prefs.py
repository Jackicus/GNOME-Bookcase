# SPDX-License-Identifier: GPL-2.0-or-later
# SPDX-FileCopyrightText: 2026 Jack Tully

"""The Sharing preferences page, the main menu's sharing line and the QR code widget, with
sharing on 127.0.0.1 and an in-memory keyring."""

import gc
import shutil
import socket
import tempfile
import unittest

from tests import ROOT  # noqa: F401  (registers bookcase)
from tests.dialog_support import fake_app
from tests.gtk import pump, requires_gtk, wait_for

from bookcase import passwords, sharing

KEYS = ('sharing-enabled', 'sharing-scope', 'sharing-port', 'sharing-require-password',
        'sharing-username')


def _free_port():
    with socket.socket() as probe:
        probe.bind(('127.0.0.1', 0))
        return probe.getsockname()[1]


@requires_gtk
class SharingPageTest(unittest.TestCase):

    def _sharing(self, app):
        # Garbage of earlier widget tests is collected here, on the main thread: a GTK
        # object finalized by the collector in a server thread aborts the process.
        gc.collect()
        for key in KEYS:
            app.settings.reset(key)
        self.addCleanup(lambda: [app.settings.reset(key) for key in KEYS])
        app.settings.set_string('sharing-scope', 'local')
        app.settings.set_int('sharing-port', _free_port())
        cache = tempfile.mkdtemp(prefix='bookcase-sharing-')
        self.addCleanup(shutil.rmtree, cache, True)
        self.keyring = passwords.MemoryKeyring()
        app.sharing = sharing.Sharing(app.settings, app.library, app.covers,
                                      keyring=self.keyring, cache_dir=cache, advertise=False)
        self.addCleanup(app.sharing.shutdown)
        return app.sharing

    def test_turning_sharing_on_shows_the_address_and_code(self):
        from gi.repository import Gio, Gtk

        from bookcase.dialogs.sharing_prefs import SharingPage, attach_menu

        with fake_app() as app:
            service = self._sharing(app)
            page = SharingPage(app)
            window = Gtk.Window(child=page)
            self.addCleanup(window.destroy)
            menu = Gio.Menu()
            handler = attach_menu(menu, service)
            self.addCleanup(service.disconnect, handler)
            self.assertFalse(page.connect_group.get_visible())
            self.assertEqual(page.switch_row.get_subtitle(), 'Off')
            self.assertEqual(menu.get_item_link(0, 'section').get_n_items(), 0)

            page.switch_row.set_active(True)
            self.assertTrue(app.settings.get_boolean('sharing-enabled'))
            self.assertTrue(wait_for(lambda: service.state == 'on'))
            pump()
            port = app.settings.get_int('sharing-port')
            address = f'http://127.0.0.1:{port}/'
            self.assertTrue(page.connect_group.get_visible())
            self.assertEqual([row.address for row in page._address_rows], [address])
            self.assertEqual(page.catalogue_row.address, address + 'opds')
            self.assertEqual(page.qr_code.text, address)
            self.assertTrue(page.qr_code.get_visible())
            self.assertIn(f'127.0.0.1:{port}', page.switch_row.get_subtitle())
            self.assertEqual(page.password_row.get_text(), service.password)
            section = menu.get_item_link(0, 'section')
            self.assertEqual(section.get_n_items(), 1)
            label = section.get_item_attribute_value(0, 'label').unpack()
            self.assertEqual(label, f'Sharing on 127.0.0.1:{port}')

            page.password_row.set_text('new-secret')
            page._on_password(page.password_row)
            self.assertTrue(wait_for(
                lambda: self.keyring.lookup(sharing.KEYRING_ACCOUNT) == 'new-secret'))
            self.assertEqual(service.server.credentials, ('reader', 'new-secret'))

            page.password_switch.set_active(False)
            self.assertFalse(page.password_row.get_sensitive())
            self.assertTrue(page.note_label.get_label())

            page.switch_row.set_active(False)
            pump()
            self.assertEqual(service.state, 'off')
            self.assertFalse(page.connect_group.get_visible())
            self.assertEqual(section.get_n_items(), 0)

    def test_scope_row_follows_the_setting(self):
        from gi.repository import Gtk

        from bookcase.dialogs.sharing_prefs import SharingPage

        with fake_app() as app:
            self._sharing(app)
            page = SharingPage(app)
            window = Gtk.Window(child=page)
            self.addCleanup(window.destroy)
            self.assertEqual(page.scope_row.get_selected(), 1)  # local
            page.scope_row.set_selected(0)
            self.assertEqual(app.settings.get_string('sharing-scope'), 'network')
            self.assertIn('trust', page.note_label.get_label())

    def test_qr_code_widget(self):
        from bookcase.widgets.qr_code import QrCode

        code = QrCode(text='http://192.168.1.20:8095/', module_size=4)
        self.assertEqual(code.get_size_request(), ((25 + 8) * 4, (25 + 8) * 4))
        self.assertEqual(code.texture.get_width(), 33)
        code.set_text('')
        self.assertFalse(code.get_visible())


if __name__ == '__main__':
    unittest.main()
