# SPDX-License-Identifier: GPL-2.0-or-later
# SPDX-FileCopyrightText: 2026 Jack Tully

"""Send to Kindle by e-mail: books mailed over SMTP to the user's @kindle.com address.

    account = Account.from_settings(settings)   # the mail-* and kindle-address keys
    account.configured                          # server, sender and Kindle address are set
    account.save(settings)
    account.key                                 # the keyring's account for its password
    PRESETS['gmail']                            # Preset(name, server, port, security, note)
    destination = KindleDestination(account)    # what Send to Device lists beside devices:
                                                # .id, .name, .kind 'email', .plan(formats,
                                                # sizes=None), .why_not(formats, sizes=None)
    with Sender(account, password) as sender:   # one connection; MailError when refused
        sender.send_book(library, covers, book_id)   # exports, checks the size, mails it
        sender.send(message)
    send_test(account, password)                # a short mail to the sender's own address
    build_message(account, path, filename, subject)   # an EmailMessage with the book

Amazon takes EPUB, PDF, Word, TXT, RTF, HTML and images by e-mail, not MOBI or AZW3 (no
longer since 2022) and at most 50 MB in one mail (docs/research/calibre.md §4.1). Of the
formats Bookcase keeps, that is EPUB (a kepub goes as the EPUB it is), PDF and TXT, in that
order of preference. The EPUB is the exporting.export_copy() of the book, so its edited
metadata and cover go with it.

Each book goes in a mail of its own, over one SMTP connection: a book Amazon rejects (its
EPUB intake refuses some valid files) does not take the others with it, and a mail stays
under the sending provider's own size limit (Gmail's is 25 MB) as far as it can. The
attachment's file name is ASCII ('The Glass Estuary - Imogen Vale.epub'); Amazon names the
book from its metadata, not the file name.

Security: 'starttls' (port 587), 'ssl' (port 465) or 'none'. TLS certificates are checked,
except on this computer (localhost: Proton Mail Bridge's own certificate). With 'none' no
password is sent except to this computer. Amazon drops mail from senders not on the
account's Approved Personal Document E-mail List without a word (APPROVED_LIST_URL).

The password lives in the keyring (passwords.py, schema io.github.jackicus.Bookcase.Mail),
never in GSettings. Everything here blocks on the network: call it from a thread.
"""

import dataclasses
import email.message
import email.utils
import logging
import os
import re
import shutil
import smtplib
import socket
import ssl
import tempfile
import unicodedata
from gettext import gettext as _

log = logging.getLogger(__name__)

TIMEOUT = 60  # seconds, per SMTP command (a large attachment is one)
MAX_BYTES = 50 * 1000 * 1000  # Amazon's limit for one mail
# The library formats Amazon takes by e-mail, preferred first; what each goes as.
FORMATS = ('epub', 'kepub', 'pdf', 'txt')
SENT_AS = {'epub': 'epub', 'kepub': 'epub', 'pdf': 'pdf', 'txt': 'txt'}
MEDIA_TYPES = {'epub': ('application', 'epub+zip'), 'pdf': ('application', 'pdf'),
               'txt': ('text', 'plain')}
REFUSED = ('mobi', 'azw3')
SECURITIES = ('starttls', 'ssl', 'none')
LOCAL_HOSTS = ('localhost', '127.0.0.1', '::1')
KINDLE_DOMAINS = ('kindle.com', 'free.kindle.com', 'kindle.cn')
APPROVED_LIST_URL = ('https://www.amazon.com/gp/help/customer/display.html'
                     '?nodeId=GX9XLEVV8G4DB28H')
SETTINGS_URL = 'https://www.amazon.com/mycd'
_ADDRESS = re.compile(r'^[^@\s]+@[^@\s]+\.[^@\s]+$')


@dataclasses.dataclass(frozen=True)
class Preset:
    name: str
    server: str
    port: int
    security: str
    note: str  # what the password must be, a sentence


def presets():
    """The providers the setup offers, in its order (translated notes)."""
    return {
        'gmail': Preset('Gmail', 'smtp.gmail.com', 587, 'starttls',
                        _('Gmail needs an app password, made in your Google Account’s '
                          'security settings (2-Step Verification must be on).')),
        'outlook': Preset('Outlook.com', 'smtp-mail.outlook.com', 587, 'starttls',
                          _('Use your Microsoft account password, or an app password when '
                            'two-step verification is on.')),
        'fastmail': Preset('Fastmail', 'smtp.fastmail.com', 465, 'ssl',
                           _('Fastmail needs an app password with SMTP access, made in '
                             'Settings → Privacy & Security.')),
        'icloud': Preset('iCloud Mail', 'smtp.mail.me.com', 587, 'starttls',
                         _('iCloud needs an app-specific password, made at '
                           'account.apple.com.')),
        'proton': Preset('Proton Mail Bridge', '127.0.0.1', 1025, 'starttls',
                         _('Proton Mail Bridge must be running; use the user name and '
                           'password Bridge shows, not your Proton password.')),
        'custom': Preset(_('Other'), '', 587, 'starttls',
                         _('Your provider’s outgoing (SMTP) server, as it documents it.')),
    }


PRESETS = presets()


def valid_address(text):
    return bool(_ADDRESS.match((text or '').strip()))


def is_kindle_address(text):
    """Whether an address is one of Amazon's Send to Kindle addresses."""
    text = (text or '').strip().lower()
    return valid_address(text) and text.rsplit('@', 1)[1] in KINDLE_DOMAINS


def guess_preset(address):
    """The preset for an e-mail address's provider, or 'custom'."""
    domain = (address or '').strip().lower().rpartition('@')[2]
    if domain in ('gmail.com', 'googlemail.com'):
        return 'gmail'
    if domain in ('outlook.com', 'hotmail.com', 'live.com', 'msn.com'):
        return 'outlook'
    if domain in ('fastmail.com', 'fastmail.fm'):
        return 'fastmail'
    if domain in ('icloud.com', 'me.com', 'mac.com'):
        return 'icloud'
    if domain in ('proton.me', 'protonmail.com', 'pm.me'):
        return 'proton'
    return 'custom'


class MailError(Exception):
    """A failure to tell the user in a sentence (str(error) is translated). `fatal` when no
    further mail can go over this connection (refused login, lost connection)."""

    def __init__(self, message, fatal=False):
        super().__init__(message)
        self.fatal = fatal


# -- the account -------------------------------------------------------------------------------

@dataclasses.dataclass(frozen=True)
class Account:
    server: str = ''
    port: int = 587
    security: str = 'starttls'
    username: str = ''  # '' means the sender's address
    sender: str = ''  # the From address: the one Amazon must have approved
    kindle: str = ''  # name@kindle.com
    preset: str = 'custom'

    KEYS = {'server': 'mail-server', 'port': 'mail-port', 'security': 'mail-security',
            'username': 'mail-username', 'sender': 'mail-sender', 'kindle': 'kindle-address',
            'preset': 'mail-provider'}

    @classmethod
    def from_settings(cls, settings):
        if settings is None:
            return cls()
        return cls(server=settings.get_string('mail-server').strip(),
                   port=settings.get_int('mail-port'),
                   security=settings.get_string('mail-security'),
                   username=settings.get_string('mail-username').strip(),
                   sender=settings.get_string('mail-sender').strip(),
                   kindle=settings.get_string('kindle-address').strip(),
                   preset=settings.get_string('mail-provider'))

    def save(self, settings):
        for field, key in self.KEYS.items():
            value = getattr(self, field)
            if isinstance(value, int):
                settings.set_int(key, value)
            else:
                settings.set_string(key, value)

    @classmethod
    def forget(cls, settings):
        for key in cls.KEYS.values():
            settings.reset(key)

    @property
    def configured(self):
        return bool(self.server and valid_address(self.sender)
                    and valid_address(self.kindle))

    @property
    def login(self):
        return self.username or self.sender

    @property
    def key(self):
        """The keyring account its password is stored under."""
        return f'{self.login} on {self.server}:{self.port}'

    @property
    def local(self):
        return self.server.strip('[]').lower() in LOCAL_HOSTS


# -- what can be sent --------------------------------------------------------------------------

def _plan_class():
    from .devices import Plan

    return Plan


def plan(formats, sizes=None):
    """A devices.Plan for mailing a book with these library formats, or None. `sizes`
    ({format: bytes}) leaves out a file over Amazon's limit."""
    sizes = sizes or {}
    Plan = _plan_class()
    for fmt in FORMATS:
        if fmt in formats and sizes.get(fmt, 0) <= MAX_BYTES:
            target = SENT_AS[fmt]
            return Plan(fmt, target, target.upper())
    return None


def why_not(formats, sizes=None):
    """Why plan() found nothing: a sentence."""
    sizes = sizes or {}
    if any(fmt in formats and sizes.get(fmt, 0) > MAX_BYTES for fmt in FORMATS):
        return _('Larger than the 50 MB Amazon takes by e-mail')
    if any(fmt in formats for fmt in REFUSED):
        return _('Amazon no longer takes MOBI or AZW3 books by e-mail, only EPUB, PDF '
                 'and TXT')
    return _('No format Amazon takes by e-mail (EPUB, PDF or TXT)')


class KindleDestination:
    """Send to Kindle by e-mail, as Send to Device lists it beside the e-readers."""

    id = 'kindle-email'
    kind = 'email'

    def __init__(self, account):
        self.account = account
        self.name = _('Kindle by E-mail')
        self.formats = FORMATS
        self.book_ids = set()

    def plan(self, formats, kepub=True, sizes=None):
        return plan(formats, sizes)

    def why_not(self, formats, sizes=None):
        return why_not(formats, sizes)

    def space(self):
        return 0, 0


def attachment_name(title, author, fmt):
    """'Title - Author.epub' in ASCII (accents dropped, other characters left out)."""
    stem = f'{title} - {author}' if author else (title or '')
    stem = unicodedata.normalize('NFKD', stem).encode('ascii', 'ignore').decode('ascii')
    stem = re.sub(r'[^A-Za-z0-9 ._,()\'&!-]+', ' ', stem)
    stem = ' '.join(stem.split()).strip(' .')[:120] or 'book'
    return f'{stem}.{fmt}'


# -- messages ----------------------------------------------------------------------------------

def _base_message(account, to, subject):
    message = email.message.EmailMessage()
    message['From'] = account.sender
    message['To'] = to
    message['Subject'] = subject
    message['Date'] = email.utils.formatdate(localtime=True)
    domain = account.sender.rpartition('@')[2] or None
    message['Message-ID'] = email.utils.make_msgid(domain=domain)
    return message


def build_message(account, path, filename, subject):
    """A mail from the account's sender to its Kindle address with the file at `path`
    attached as `filename` (its suffix gives the media type)."""
    message = _base_message(account, account.kindle, subject)
    message.set_content(_('Sent from Bookcase.') + '\n')
    fmt = filename.rsplit('.', 1)[-1].lower()
    maintype, subtype = MEDIA_TYPES.get(fmt, ('application', 'octet-stream'))
    with open(path, 'rb') as file:
        data = file.read()
    message.add_attachment(data, maintype=maintype, subtype=subtype, filename=filename)
    return message


def build_test_message(account):
    """A short mail to the sender's own address, saying the setup works."""
    message = _base_message(account, account.sender, _('Bookcase: Send to Kindle works'))
    message.set_content(_(
        'This is a test from Bookcase. Your mail server accepted the message, so books '
        'can be sent from {sender} to {kindle}.\n\n'
        'Amazon only delivers books from addresses on the Approved Personal Document '
        'E-mail List of your Amazon account:\n{url}\n').format(
            sender=account.sender, kindle=account.kindle, url=APPROVED_LIST_URL))
    return message


# -- sending -----------------------------------------------------------------------------------

def _tls_context(account):
    context = ssl.create_default_context()
    if account.local:  # Proton Mail Bridge signs its own certificate
        context.check_hostname = False
        context.verify_mode = ssl.CERT_NONE
    return context


def default_smtp(account, timeout):
    """An smtplib connection to the account's server (TLS from the start for 'ssl')."""
    if account.security == 'ssl':
        return smtplib.SMTP_SSL(account.server, account.port, timeout=timeout,
                                context=_tls_context(account))
    return smtplib.SMTP(account.server, account.port, timeout=timeout)


def mail_error(error, account):
    """A MailError, in a sentence, for an smtplib, socket or ssl failure."""
    if isinstance(error, MailError):
        return error
    host = account.server
    if isinstance(error, smtplib.SMTPAuthenticationError):
        note = PRESETS.get(account.preset)
        text = _('{host} refused the user name or password.').format(host=host)
        if account.preset in ('gmail', 'icloud', 'fastmail'):
            text += ' ' + note.note
        return MailError(text, fatal=True)
    if isinstance(error, smtplib.SMTPNotSupportedError):
        return MailError(_('{host} does not offer an encrypted connection here. Try the '
                           'other security setting or port.').format(host=host), fatal=True)
    if isinstance(error, smtplib.SMTPSenderRefused):
        if error.smtp_code == 552:
            return MailError(_('The mail is larger than {host} accepts').format(host=host))
        return MailError(_('{host} will not send mail from {sender}').format(
            host=host, sender=account.sender), fatal=True)
    if isinstance(error, smtplib.SMTPRecipientsRefused):
        return MailError(_('{host} refused the address {address}').format(
            host=host, address=', '.join(error.recipients) or account.kindle), fatal=True)
    if isinstance(error, smtplib.SMTPDataError):
        if error.smtp_code in (552, 554) and b'size' in (error.smtp_error or b'').lower():
            return MailError(_('The mail is larger than {host} accepts').format(host=host))
        reason = (error.smtp_error or b'').decode('utf-8', 'replace').strip()
        return MailError(_('{host} refused the mail: {reason}').format(
            host=host, reason=reason or error.smtp_code))
    if isinstance(error, (smtplib.SMTPServerDisconnected, smtplib.SMTPConnectError)):
        return MailError(_('{host} closed the connection').format(host=host), fatal=True)
    if isinstance(error, ssl.SSLCertVerificationError):
        return MailError(_('The certificate of {host} is not valid').format(host=host),
                         fatal=True)
    if isinstance(error, ssl.SSLError):
        return MailError(_('Could not make an encrypted connection to {host}. Check the '
                           'port and security setting.').format(host=host), fatal=True)
    if isinstance(error, socket.gaierror):
        return MailError(_('Could not find the server {host}. Check its name and your '
                           'internet connection.').format(host=host), fatal=True)
    if isinstance(error, TimeoutError):
        return MailError(_('{host} did not answer in time').format(host=host), fatal=True)
    if isinstance(error, ConnectionRefusedError):
        return MailError(_('{host} refused the connection on port {port}').format(
            host=host, port=account.port), fatal=True)
    if isinstance(error, smtplib.SMTPException):
        return MailError(_('{host} answered with an error: {error}').format(
            host=host, error=error), fatal=True)
    if isinstance(error, OSError):
        return MailError(_('Could not reach {host}: {error}').format(
            host=host, error=error.strerror or error), fatal=True)
    return MailError(str(error), fatal=True)


class Sender:
    """One SMTP connection, logged in, for as many mails as there are books.

    `smtp` is a function (account, timeout) -> an smtplib.SMTP-like object; the tests
    pass a fake one."""

    def __init__(self, account, password, smtp=None, timeout=TIMEOUT):
        self.account = account
        self.password = password or ''
        self.smtp_factory = smtp or default_smtp
        self.timeout = timeout
        self.connection = None

    def __enter__(self):
        self.open()
        return self

    def __exit__(self, *_exc):
        self.close()
        return False

    def open(self):
        account = self.account
        if not account.configured:
            raise MailError(_('Send to Kindle is not set up'), fatal=True)
        try:
            connection = self.smtp_factory(account, self.timeout)
            self.connection = connection
            connection.ehlo()
            if account.security == 'starttls':
                connection.starttls(context=_tls_context(account))
                connection.ehlo()
            if self.password:
                if account.security == 'none' and not account.local:
                    raise MailError(_('Bookcase sends a password only over an encrypted '
                                      'connection. Choose STARTTLS or SSL/TLS.'), fatal=True)
                connection.login(account.login, self.password)
        except Exception as error:
            self.close()
            raise mail_error(error, account) from error

    def close(self):
        connection, self.connection = self.connection, None
        if connection is None:
            return
        try:
            connection.quit()
        except Exception:  # already gone
            try:
                connection.close()
            except Exception:
                pass

    def send(self, message):
        if self.connection is None:
            raise MailError(_('Not connected to the mail server'), fatal=True)
        try:
            self.connection.send_message(message)
        except Exception as error:
            raise mail_error(error, self.account) from error

    def send_book(self, library, covers, book_id, progress=None):
        """Mail a book (the export of its best format Amazon takes); returns the attachment's
        name. progress(fraction) is called along the way. Raises MailError."""
        from . import exporting

        book = library.book(book_id)
        if book is None:
            raise MailError(_('The book is no longer in the library'))
        files = [file for file in library.files(book_id) if not file.missing]
        formats = [file.format for file in files]
        sizes = {file.format: file.size for file in files}
        found = plan(formats, sizes)
        if found is None:
            raise MailError(why_not(formats, sizes))
        work = tempfile.mkdtemp(prefix='bookcase-mail-')
        try:
            try:
                copy = exporting.export_copy(library, covers, book_id, work,
                                             format=found.source, embed=True)
            except exporting.ExportError as error:
                raise MailError(str(error)) from error
            if progress is not None:
                progress(0.3)
            if os.path.getsize(copy) > MAX_BYTES:
                raise MailError(why_not([found.source], {found.source: MAX_BYTES + 1}))
            name = attachment_name(book.title, book.authors[0] if book.authors else '',
                                   found.target)
            message = build_message(self.account, copy, name, book.title or name)
            self.send(message)
            if progress is not None:
                progress(1.0)
            return name
        finally:
            shutil.rmtree(work, ignore_errors=True)


def send_test(account, password, smtp=None):
    """Connect, log in and mail the sender a test; MailError when that fails."""
    with Sender(account, password, smtp=smtp) as sender:
        sender.send(build_test_message(account))
