# SPDX-License-Identifier: GPL-2.0-or-later
# SPDX-FileCopyrightText: 2026 Jack Tully

"""Send to Kindle by e-mail (mail.py) and the keyring wrapper (passwords.py), with a fake
SMTP server: no network."""

from tests import ROOT  # noqa: F401  (registers src/ as bookcase)

import os
import smtplib
import socket
import unittest

from bookcase import mail, passwords
from tests.support import make_epub, temporary_library

ACCOUNT = mail.Account(server='smtp.example.org', port=587, security='starttls',
                       sender='reader@example.org', kindle='reader_1@kindle.com',
                       preset='gmail')


class FakeSMTP:
    """What Sender asks of smtplib.SMTP, recorded."""

    def __init__(self, fail_login=False, fail_send=None):
        self.calls = []
        self.sent = []
        self.fail_login = fail_login
        self.fail_send = fail_send

    def factory(self, account, timeout):
        self.calls.append(('connect', account.server, account.port, timeout))
        return self

    def ehlo(self):
        self.calls.append(('ehlo',))

    def starttls(self, context=None):
        self.calls.append(('starttls', context is not None))

    def login(self, user, password):
        self.calls.append(('login', user, password))
        if self.fail_login:
            raise smtplib.SMTPAuthenticationError(535, b'5.7.8 bad credentials')

    def send_message(self, message):
        if self.fail_send is not None:
            raise self.fail_send
        self.sent.append(message)

    def quit(self):
        self.calls.append(('quit',))

    def close(self):
        self.calls.append(('close',))


class FakeCovers:
    def data(self, _book):
        return None


class MailTest(unittest.TestCase):
    def test_plan_prefers_epub_and_refuses_mobi(self):
        self.assertEqual(mail.plan(['pdf', 'epub']).source, 'epub')
        self.assertEqual(mail.plan(['kepub']).target, 'epub')
        self.assertEqual(mail.plan(['txt', 'azw3']).target, 'txt')
        self.assertIsNone(mail.plan(['mobi', 'azw3']))
        self.assertIn('MOBI', mail.why_not(['mobi']))
        self.assertIsNone(mail.plan(['epub'], {'epub': mail.MAX_BYTES + 1}))
        self.assertIn('50 MB', mail.why_not(['epub'], {'epub': mail.MAX_BYTES + 1}))
        self.assertEqual(mail.plan(['epub', 'pdf'], {'epub': mail.MAX_BYTES + 1}).source,
                         'pdf')

    def test_addresses_and_presets(self):
        self.assertTrue(mail.is_kindle_address('someone_9@kindle.com'))
        self.assertFalse(mail.is_kindle_address('someone@example.org'))
        self.assertFalse(mail.valid_address('nobody'))
        self.assertEqual(mail.guess_preset('a@gmail.com'), 'gmail')
        self.assertEqual(mail.guess_preset('a@example.org'), 'custom')
        self.assertEqual(mail.PRESETS['icloud'].server, 'smtp.mail.me.com')
        self.assertTrue(ACCOUNT.configured)
        self.assertFalse(mail.Account(server='x', sender='a@b.c').configured)
        self.assertEqual(ACCOUNT.login, 'reader@example.org')
        self.assertEqual(mail.attachment_name('Café Noir: Ünë', 'Zoë Lark', 'epub'),
                         'Cafe Noir Une - Zoe Lark.epub')
        self.assertEqual(mail.attachment_name('', '', 'pdf'), 'book.pdf')

    def test_account_settings_round_trip(self):
        from gi.repository import Gio

        from tests.gtk import SCHEMA_ID

        settings = Gio.Settings.new(SCHEMA_ID)
        try:
            ACCOUNT.save(settings)
            self.assertEqual(mail.Account.from_settings(settings), ACCOUNT)
        finally:
            mail.Account.forget(settings)
        self.assertFalse(mail.Account.from_settings(settings).configured)

    def test_build_message(self):
        with temporary_library() as library:
            path = make_epub(library.path.parent / 'book.epub')
            message = mail.build_message(ACCOUNT, path, 'Book - Ada Lark.epub', 'Book')
        self.assertEqual(message['To'], 'reader_1@kindle.com')
        self.assertEqual(message['From'], 'reader@example.org')
        attachments = list(message.iter_attachments())
        self.assertEqual(len(attachments), 1)
        self.assertEqual(attachments[0].get_content_type(), 'application/epub+zip')
        self.assertEqual(attachments[0].get_filename(), 'Book - Ada Lark.epub')
        self.assertTrue(attachments[0].get_content().startswith(b'PK'))

    def test_send_books_over_one_connection(self):
        fake = FakeSMTP()
        with temporary_library() as library:
            from bookcase import importing
            from bookcase.formats import read

            ids = []
            for number in (1, 2):
                path = make_epub(library.path.parent / f'b{number}.epub',
                                 title=f'Harbour {number}')
                ids.append(library.add_book(read(str(path)), str(path),
                                            hash=importing.partial_md5(path),
                                            size=os.path.getsize(path)))
            fractions = []
            with mail.Sender(ACCOUNT, 'secret', smtp=fake.factory) as sender:
                names = [sender.send_book(library, FakeCovers(), book_id,
                                          progress=fractions.append) for book_id in ids]
        self.assertEqual(names, ['Harbour 1 - Ada Lark.epub', 'Harbour 2 - Ada Lark.epub'])
        self.assertEqual(len(fake.sent), 2)
        self.assertEqual([call[0] for call in fake.calls],
                         ['connect', 'ehlo', 'starttls', 'ehlo', 'login', 'quit'])
        self.assertIn(('login', 'reader@example.org', 'secret'), fake.calls)
        self.assertEqual(fractions[-1], 1.0)

    def test_errors_are_sentences(self):
        fake = FakeSMTP(fail_login=True)
        with self.assertRaises(mail.MailError) as caught:
            mail.send_test(ACCOUNT, 'wrong', smtp=fake.factory)
        self.assertTrue(caught.exception.fatal)
        self.assertIn('app password', str(caught.exception))
        self.assertIn(('quit',), fake.calls)
        refused = smtplib.SMTPRecipientsRefused({'reader_1@kindle.com': (550, b'no')})
        fake = FakeSMTP(fail_send=refused)
        with self.assertRaises(mail.MailError) as caught:
            mail.send_test(ACCOUNT, 'pw', smtp=fake.factory)
        self.assertIn('reader_1@kindle.com', str(caught.exception))
        self.assertIn('did not answer', str(mail.mail_error(TimeoutError(), ACCOUNT)))
        self.assertIn('Could not find', str(mail.mail_error(socket.gaierror(), ACCOUNT)))

    def test_no_password_over_plain_text(self):
        account = mail.Account(server='smtp.example.org', port=25, security='none',
                               sender='reader@example.org', kindle='r@kindle.com')
        with self.assertRaises(mail.MailError):
            mail.send_test(account, 'pw', smtp=FakeSMTP().factory)
        fake = FakeSMTP()
        mail.send_test(account, '', smtp=fake.factory)  # no password: allowed
        self.assertEqual(len(fake.sent), 1)
        self.assertNotIn('starttls', [call[0] for call in fake.calls])

    def test_book_without_a_mailable_format(self):
        from tests.support import add_book

        with temporary_library() as library:
            book_id = add_book(library, 'Only Mobi', fmt='mobi')
            with mail.Sender(ACCOUNT, 'pw', smtp=FakeSMTP().factory) as sender:
                with self.assertRaises(mail.MailError) as caught:
                    sender.send_book(library, FakeCovers(), book_id)
        self.assertFalse(caught.exception.fatal)


class KeyringTest(unittest.TestCase):
    def test_memory_keyring(self):
        keyring = passwords.MemoryKeyring()
        self.assertIsNone(keyring.lookup(ACCOUNT.key))
        keyring.store(ACCOUNT.key, 'label', 'pw')
        self.assertEqual(keyring.lookup(ACCOUNT.key), 'pw')
        keyring.clear(ACCOUNT.key)
        self.assertIsNone(keyring.lookup(ACCOUNT.key))

    def test_keyring_without_libsecret(self):
        keyring = passwords.Keyring(passwords.MAIL_SCHEMA)
        original = passwords._secret
        passwords._secret = lambda: None
        try:
            self.assertIsNone(keyring.lookup('x'))
            with self.assertRaises(passwords.KeyringError):
                keyring.store('x', 'label', 'pw')
            keyring.clear('x')
        finally:
            passwords._secret = original

    def test_keyring_calls_libsecret(self):
        calls = []

        class Secret:
            COLLECTION_DEFAULT = 'default'

            class SchemaFlags:
                NONE = 0

            class SchemaAttributeType:
                STRING = 0

            class Schema:
                @staticmethod
                def new(name, flags, attributes):
                    calls.append(('schema', name, tuple(attributes)))
                    return name

            @staticmethod
            def password_store_sync(schema, attributes, collection, label, password, _c):
                calls.append(('store', attributes['account'], password))
                return True

            @staticmethod
            def password_lookup_sync(schema, attributes, _c):
                return 'found'

            @staticmethod
            def password_clear_sync(schema, attributes, _c):
                calls.append(('clear', attributes['account']))

        keyring = passwords.Keyring(passwords.MAIL_SCHEMA)
        original = passwords._secret
        passwords._secret = lambda: Secret
        try:
            self.assertTrue(keyring.store('a', 'label', 'pw'))
            self.assertEqual(keyring.lookup('a'), 'found')
            keyring.clear('a')
        finally:
            passwords._secret = original
        self.assertEqual(calls, [('schema', 'io.github.jackicus.Bookcase.Mail', ('account',)),
                                 ('store', 'a', 'pw'), ('clear', 'a')])


if __name__ == '__main__':
    unittest.main()
