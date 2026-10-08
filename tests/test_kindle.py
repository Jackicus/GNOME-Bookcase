# SPDX-License-Identifier: GPL-2.0-or-later
# SPDX-FileCopyrightText: 2026 Jack Tully

"""Widget tests for Send to Kindle by e-mail (dialogs/send.py's e-mail destination and
dialogs/kindle_mail.py's setup) and the highlights dialogs (dialogs/highlights.py), over a
temporary library, a fake SMTP server and a keyring in memory. No network."""

from tests import ROOT  # noqa: F401  (registers src/ as bookcase)

import os
import tempfile
import unittest

from bookcase import annotations, mail, passwords
from tests.dialog_support import fake_app
from tests.gtk import pump, requires_gtk, wait_for
from tests.support import add_book, make_epub
from tests.test_annotations import CLIPPINGS
from tests.test_mail import ACCOUNT, FakeSMTP


def add_epub(library, title):
    from bookcase import importing
    from bookcase.formats import read

    path = make_epub(library.path.parent / f'{title}.epub', title=title)
    return library.add_book(read(str(path)), str(path), hash=importing.partial_md5(path),
                            size=os.path.getsize(path))


def mail_app(app):
    app.devices = None
    app.mail_keyring = passwords.MemoryKeyring({ACCOUNT.key: 'secret'})
    app.smtp = FakeSMTP()
    app.mail_smtp = app.smtp.factory
    return app


@requires_gtk
class SendByMailTest(unittest.TestCase):
    def test_not_set_up(self):
        from bookcase.dialogs import send

        with fake_app() as app:
            mail_app(app)
            book_id = add_epub(app.library, 'A Quiet Harbour')
            dialog = send.SendDialog(app, [book_id])
            self.assertEqual(dialog.stack.get_visible_child_name(), 'none')
            self.assertIsInstance(dialog.none_status.get_child(), type(dialog.send_button))

    def test_mail_destination_plans_and_sends(self):
        from bookcase.dialogs import send

        with fake_app() as app:
            mail_app(app)
            ACCOUNT.save(app.settings)
            self.addCleanup(mail.Account.forget, app.settings)
            harbour = add_epub(app.library, 'A Quiet Harbour')
            mobi = add_book(app.library, 'Only Mobi', fmt='mobi')
            dialog = send.SendDialog(app, [harbour, mobi])
            self.assertEqual(dialog.stack.get_visible_child_name(), 'form')
            self.assertEqual(dialog.device.kind, 'email')
            self.assertFalse(dialog.setup_row.get_visible())
            self.assertIn('reader_1@kindle.com', dialog.device_row.get_subtitle())
            plans = dialog.plans()
            self.assertEqual(plans[0][2].label, 'EPUB')
            self.assertIsNone(plans[1][2])
            self.assertEqual(dialog.send_button.get_label(), '_Send 1')
            dialog.send()
            self.assertTrue(wait_for(lambda: app.toasts, 5))
            self.assertEqual(app.toasts[-1][0], 'Sent “A Quiet Harbour” to reader_1@kindle.com')
            self.assertEqual(len(app.smtp.sent), 1)
            self.assertIn(('login', 'reader@example.org', 'secret'), app.smtp.calls)

    def test_refused_login_is_said(self):
        from bookcase.dialogs import send

        with fake_app() as app:
            mail_app(app)
            app.smtp.fail_login = True
            ACCOUNT.save(app.settings)
            self.addCleanup(mail.Account.forget, app.settings)
            dialog = send.SendDialog(app, [add_epub(app.library, 'A Quiet Harbour')])
            dialog.send()
            self.assertTrue(wait_for(lambda: app.toasts, 5))
            self.assertIn('refused the user name or password', app.toasts[-1][0])


@requires_gtk
class KindleSetupTest(unittest.TestCase):
    def test_fill_test_save_remove(self):
        from bookcase.dialogs import kindle_mail

        with fake_app() as app:
            mail_app(app)
            app.settings.set_boolean('kindle-mail-explained', True)
            self.addCleanup(app.settings.reset, 'kindle-mail-explained')
            self.addCleanup(mail.Account.forget, app.settings)
            dialog = kindle_mail.KindleSetupDialog(app)
            self.assertEqual(dialog.server_row.get_text(), 'smtp.gmail.com')  # Gmail first
            self.assertFalse(dialog.save_button.get_sensitive())
            dialog.kindle_row.set_text('reader_1@kindle.com')
            dialog.sender_row.set_text('reader@example.org')
            dialog.provider_row.set_selected(kindle_mail.PRESET_KEYS.index('icloud'))
            self.assertEqual(dialog.server_row.get_text(), 'smtp.mail.me.com')
            self.assertEqual(dialog.password_row.get_title(), 'App Password')
            dialog.provider_row.set_selected(kindle_mail.PRESET_KEYS.index('custom'))
            self.assertTrue(dialog.server_expander.get_expanded())
            dialog.server_row.set_text('smtp.example.org')
            dialog.port_row.set_value(465)
            dialog.security_row.set_selected(1)
            dialog.password_row.set_text('pw')
            self.assertTrue(dialog.save_button.get_sensitive())
            dialog.send_test()
            self.assertTrue(wait_for(lambda: app.smtp.sent, 5))
            self.assertEqual(app.smtp.sent[0]['To'], 'reader@example.org')
            wait_for(lambda: not dialog._busy, 5)
            saved = []
            dialog.on_saved = saved.append
            dialog.save()
            self.assertTrue(wait_for(lambda: saved, 5))
            account = mail.Account.from_settings(app.settings)
            self.assertEqual((account.server, account.port, account.security, account.preset),
                             ('smtp.example.org', 465, 'ssl', 'custom'))
            self.assertEqual(app.mail_keyring.lookup(account.key), 'pw')
            again = kindle_mail.KindleSetupDialog(app)
            self.assertTrue(wait_for(lambda: again.password_row.get_text() == 'pw', 5))
            self.assertTrue(again.remove_row.get_visible())
            again.remove()
            self.assertFalse(mail.Account.from_settings(app.settings).configured)
            self.assertTrue(wait_for(lambda: app.mail_keyring.lookup(account.key) is None, 5))

    def test_preferences_group(self):
        from bookcase.dialogs import kindle_mail

        with fake_app() as app:
            group = kindle_mail.preferences_group(app)
            self.assertEqual(group.row.get_title(), 'Set Up Send to Kindle…')
            ACCOUNT.save(app.settings)
            self.addCleanup(mail.Account.forget, app.settings)
            group = kindle_mail.preferences_group(app)
            self.assertEqual(group.row.get_title(), 'reader_1@kindle.com')


@requires_gtk
class HighlightsDialogTest(unittest.TestCase):
    def test_import_with_a_chosen_book(self):
        from bookcase.dialogs import highlights

        with fake_app() as app:
            harbour = add_book(app.library, 'The Quiet Harbour', ('Ada Lark',))
            other = add_book(app.library, 'A Different Book', ('Cara Moss',))
            clippings = annotations.parse_kindle_clippings(CLIPPINGS)
            groups = annotations.clipping_groups(
                app.library, annotations.match_clippings(app.library, clippings))
            dialog = highlights.ClippingsDialog(app, groups)
            rows = dialog.rows
            self.assertTrue(rows[0].check.get_active())
            self.assertFalse(rows[1].check.get_sensitive())  # not in the library
            self.assertIn('Not in your library', rows[1].get_subtitle())
            self.assertEqual(dialog.import_button.get_label(), '_Import 2')
            dialog.chosen[groups[1].key] = other
            dialog._show_row(rows[1])
            dialog._update()
            self.assertTrue(rows[1].check.get_active())
            self.assertEqual(dialog.import_button.get_label(), '_Import 3')
            self.assertEqual(dialog.run_import(), 3)
            self.assertEqual(app.toasts[-1], ('Imported 3 highlights into 2 books', True))
            self.assertEqual(len(app.library.annotations(harbour)), 2)
            again = highlights.ClippingsDialog(app, groups)
            self.assertIn('all imported before', again.rows[0].get_subtitle())
            self.assertFalse(again.import_button.get_sensitive())

    def test_choose_book(self):
        from bookcase.dialogs import highlights

        with fake_app() as app:
            add_book(app.library, 'The Quiet Harbour', ('Ada Lark',))
            wanted = add_book(app.library, 'Fog Over Ashby', ('Ben Ross',))
            picked = []
            dialog = highlights.choose_book(app, None, 'Fog', picked.append)
            # Shown in a window of its own: closed with its entry focused, the input method's
            # events for that entry, still on their way, crash a later test. So it is mapped,
            # and its focus let go, before it closes.
            wait_for(dialog.get_mapped, 2)
            dialog.get_root().set_focus(None)
            pump(200)
            self.addCleanup(pump, 300)
            row = dialog.listbox.get_row_at_index(0)
            self.assertEqual(row.get_title(), 'Fog Over Ashby')
            self.assertIsNone(dialog.listbox.get_row_at_index(1))
            dialog.listbox.emit('row-activated', row)
            self.assertEqual(picked, [wanted])

    def test_write_all(self):
        from bookcase.dialogs import highlights

        with fake_app() as app:
            one = add_book(app.library, 'The Quiet Harbour', ('Ada Lark',))
            add_book(app.library, 'No Notes', ('Ben Ross',))
            app.library.add_annotation(one, 'highlight', 'epubcfi(/6/2!/4/2)', text='Lamps.')
            with tempfile.TemporaryDirectory() as folder:
                written = highlights.write_all(app.library, folder)
                self.assertEqual([os.path.basename(path) for path in written],
                                 ['The Quiet Harbour - Ada Lark.md'])
                with open(written[0], encoding='utf-8') as file:
                    self.assertIn('> Lamps.', file.read())
            self.assertIn('# The Quiet Harbour', highlights.markdown(app, one))


if __name__ == '__main__':
    unittest.main()
