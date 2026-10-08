# SPDX-License-Identifier: GPL-2.0-or-later
# SPDX-FileCopyrightText: 2026 Jack Tully

"""Reading-position sync over KOReader's progress sync protocol (kosync).

    client = Client(server, username='', key='')    # one HTTP conversation, blocking
    client.register(username, password) / client.login() / client.health()
    client.push(document, progress, percentage, device, device_id) -> server timestamp
    client.pull(document) -> Remote or None
    key_for(password)                   # md5 hex of the password: what the server stores
    document_ids(path, method, copies)  # the kosync ids of a book's file (and its copies)
    should_offer(remote, fraction, location, last_read, device_id)   # the "Go There" rule
    jump_target(remote, fmt) -> ('cfi', cfi) | ('page', n) | ('fraction', f)
    progress_for(fmt, fraction, location)   # what is pushed as `progress`
    status_text(synced, waiting, error, now)                         # "Synced 2 min ago"

    sync = Sync(settings, store_path)   # the app's service: app.sync (main.py)
    sync.signed_in() / sync.username() / sync.server()
    sync.sign_in(server, username, password, create, callback)       # callback(error)
    sync.sign_out() / sync.check(callback)                           # callback(error)
    sync.pull(book_id, path, fmt, callback)   # callback(Remote or None, error), ≤ 2 s
    sync.position(book_id, path, fmt, fraction, location)   # pushed after 30 s of quiet
    sync.flush(book_id=None)            # push what is waiting now (closing, idle, suspend)
    sync.remember_copy(book_id, path)   # a copy sent to a device: its ids are the book's too
    sync.status(book_id) -> (synced time or 0, waiting, error or '')
    signal 'status-changed' (book id; 0 for the account)

The protocol (koreader-sync-server; KOReader's plugins/kosync.koplugin/KOSyncClient.lua):
every request sends `Accept: application/vnd.koreader.v1+json`; an account is
POST /users/create {username, password: md5(password)} (201; 402 code 2002 when the name is
taken, 2005 when the server takes no new accounts); signing in is GET /users/auth with the
headers x-auth-user and x-auth-key = md5(password) (200 {authorized: OK}, 401 code 2001);
PUT /syncs/progress {document, progress, percentage, device, device_id} answers 200
{document, timestamp}; GET /syncs/progress/<document> answers 200 with {document, progress,
percentage, device, device_id, timestamp} or {} when nothing is stored. Timestamps are the
server's, in seconds. `percentage` is 0–1 and the common ground between apps: KOReader's
`progress` is an XPointer for a reflowable book (/body/DocFragment[12]/body/p[3]/text().0) and
a page number for a PDF. Bookcase sends its CFI (EPUB and the other reflowable formats) or
the page number (PDF), so another Bookcase (or any foliate-js reader) lands on the exact
place and everything else uses the percentage.

A document is named by KOReader's partial MD5 of the file (importing.partial_md5, KOReader's
default) or, with the sync-document-match setting `filename`, by the MD5 of the file's name
(KOReader's "file name" option). A copy Bookcase sent to an e-reader differs from the
library's file (its metadata written in, maybe a kepub), so remember_copy() keeps the copy's
two ids as more names of the book: a pull reads them all and takes the newest, a push writes
them all.

The account: the server and user name in GSettings (sync-server, sync-username), the key
(md5 of the password; kosync sends nothing stronger) in the keyring (passwords.py, schema
io.github.jackicus.Bookcase.Sync, account "<user> on <server>"), the device's
name (sync-device-name, else the computer's name) and a random id made once
(sync-device-id). Pushes run one at a time in a worker thread; one that cannot reach the
server waits in the store (a JSON file beside the library) and is tried again when the
network comes back, every five minutes, and at the next start. Results come back on the
main loop through GLib.idle_add.
"""

import dataclasses
import hashlib
import json
import logging
import os
import queue
import socket
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
import uuid
from gettext import gettext as _
from gettext import ngettext

from gi.repository import Gio, GLib, GObject

from . import passwords

log = logging.getLogger(__name__)

DEFAULT_SERVER = 'https://sync.koreader.rocks'
ACCEPT = 'application/vnd.koreader.v1+json'
USER_AGENT = 'Bookcase/0.1 (https://github.com/Jackicus/GNOME-Bookcase)'
SECRET_SCHEMA = 'io.github.jackicus.Bookcase.Sync'
DOCS_URL = 'https://koreader.rocks/user_guide/#L2-progresssync'
PULL_TIMEOUT = 2  # seconds: opening a book never waits longer for the server
TIMEOUT = 10
PUSH_DELAY_S = 30  # a position is pushed after this long without a page turn
RETRY_S = 300
SAME_PLACE = 0.0005  # fractions closer than this are the same place
MATCH_METHODS = ('content', 'filename')

# kosync error codes (koreader-sync-server)
UNAUTHORIZED = 2001
USER_EXISTS = 2002
INVALID = 2003
NO_DOCUMENT = 2004
REGISTRATION_CLOSED = 2005


class SyncError(Exception):
    """A sync failure, as a sentence for the user. `offline` when the server could not be
    reached (worth trying again later), `unauthorized` when the name or key is wrong."""

    def __init__(self, message, offline=False, unauthorized=False, status=0, code=0):
        super().__init__(message)
        self.offline = offline
        self.unauthorized = unauthorized
        self.status = status
        self.code = code


@dataclasses.dataclass(frozen=True)
class Remote:
    """A position stored on the server."""
    document: str
    percentage: float
    progress: str = ''
    device: str = ''
    device_id: str = ''
    timestamp: float = 0.0

    @property
    def page(self):
        """The page number of a paged document's progress (KOReader's PDFs), or None."""
        text = self.progress.strip()
        return int(text) if text.isdigit() else None

    @property
    def cfi(self):
        """The progress when it is an EPUB CFI (Bookcase, foliate-js readers), else None."""
        text = self.progress.strip()
        return text if text.startswith('epubcfi(') else None


def key_for(password):
    """What kosync sends and stores for a password: its MD5, in hex."""
    return hashlib.md5(password.encode()).hexdigest()


def normalize_server(url):
    """The server's base URL: https:// added when no scheme is given, no trailing slash."""
    url = (url or '').strip()
    if not url:
        return DEFAULT_SERVER
    if '://' not in url:
        url = 'https://' + url
    return url.rstrip('/')


def filename_digest(path):
    """KOReader's "file name" document id: the MD5 of the file's name (no folder)."""
    return hashlib.md5(os.path.basename(path).encode()).hexdigest()


def content_digest(path):
    from .importing import partial_md5

    return partial_md5(path)


def document_ids(path, method='content', copies=()):
    """The kosync ids of a book: its file's, then its copies' ({'hash', 'name'} each, as
    remember_copy() keeps them), without repeats. `method` is 'content' (partial MD5) or
    'filename'."""
    ids = []
    if path:
        try:
            ids.append(filename_digest(path) if method == 'filename' else content_digest(path))
        except OSError as error:
            log.warning('cannot read %s for its sync id: %s', path, error)
    for copy in copies:
        value = (hashlib.md5(copy.get('name', '').encode()).hexdigest()
                 if method == 'filename' else copy.get('hash', ''))
        if value and copy.get('name' if method == 'filename' else 'hash'):
            ids.append(value)
    return list(dict.fromkeys(ids))


def should_offer(remote, fraction, location, last_read, device_id):
    """Whether to offer a jump to `remote`: it is another device's, newer than this
    library's last reading of the book (when the server gives a time), and somewhere
    else."""
    if remote is None or remote.percentage is None:
        return False
    if device_id and remote.device_id == device_id:
        return False
    if location and remote.progress and remote.progress == location:
        return False
    if abs(remote.percentage - (fraction or 0.0)) < SAME_PLACE:
        return False
    if remote.timestamp and last_read and remote.timestamp <= last_read:
        return False
    return True


def jump_target(remote, fmt):
    """Where a jump to `remote` goes in a book of format `fmt`: the exact CFI when another
    foliate-js reader stored one, the page of a PDF, else the percentage."""
    fmt = (fmt or '').lower()
    if fmt == 'pdf':
        if remote.page is not None:
            return ('page', remote.page)
    elif remote.cfi:
        return ('cfi', remote.cfi)
    return ('fraction', max(0.0, min(1.0, remote.percentage)))


def progress_for(fmt, fraction, location):
    """What Bookcase pushes as `progress`: the CFI of a reflowable book; for a PDF its page
    number, as KOReader sends (pdf_location's 'page:12@0.25' -> '12'); else the percentage
    as text, since the server wants something."""
    location = str(location or '')
    if (fmt or '').lower() == 'pdf':
        from . import pdf_location

        place = pdf_location.parse(location) if location else None
        location = str(place.page) if place is not None else (
            location if location.isdigit() else '')
    return location or f'{fraction:.4f}'


def round_percent(fraction):
    """The percentage as KOReader sends it: 0–1, four decimals."""
    return round(max(0.0, min(1.0, float(fraction or 0.0))), 4)


def status_text(synced, waiting=False, error='', now=None):
    """The reader menu's line about sync: an error, "Waiting for a connection", "Synced
    just now", "Synced 5 min ago", … or "Not synced yet"."""
    if error:
        return error
    if waiting:
        return _('Waiting for a connection')
    if not synced:
        return _('Not synced yet')
    seconds = max(0, (now if now is not None else time.time()) - synced)
    if seconds < 60:
        return _('Synced just now')
    if seconds < 3600:
        minutes = int(seconds // 60)
        return ngettext('Synced {n} min ago', 'Synced {n} min ago', minutes).format(n=minutes)
    if seconds < 86400:
        hours = int(seconds // 3600)
        return ngettext('Synced {n} hour ago', 'Synced {n} hours ago', hours).format(n=hours)
    days = int(seconds // 86400)
    return ngettext('Synced {n} day ago', 'Synced {n} days ago', days).format(n=days)


def default_device_name():
    """The computer's name as GNOME Settings shows it (the pretty host name), else its
    host name."""
    try:
        with open('/etc/machine-info', encoding='utf-8') as file:
            for line in file:
                key, _sep, value = line.strip().partition('=')
                if key == 'PRETTY_HOSTNAME' and value.strip('"\''):
                    return value.strip('"\'')
    except OSError:
        pass
    return GLib.get_host_name() or socket.gethostname() or 'Bookcase'


# -- the HTTP client -----------------------------------------------------------------------


def _origin(url):
    parts = urllib.parse.urlsplit(url)
    return (parts.scheme.lower(), (parts.hostname or '').lower(),
            parts.port or {'http': 80, 'https': 443}.get(parts.scheme.lower()))


class _KeyRedirect(urllib.request.HTTPRedirectHandler):
    """Redirects keep the account's name and key only on the server's own host (or its
    move to https there); anywhere else they go without them."""

    def redirect_request(self, req, fp, code, msg, headers, newurl):
        target = urllib.parse.urljoin(req.full_url, newurl)
        if urllib.parse.urlsplit(target).scheme.lower() not in ('http', 'https'):
            raise urllib.error.HTTPError(target, code, msg, headers, fp)
        new = super().redirect_request(req, fp, code, msg, headers, newurl)
        if new is None:
            return None
        old, moved = _origin(req.full_url), _origin(new.full_url)
        if moved != old and not (moved[1] == old[1] and moved[0] == 'https'):
            for name in list(new.headers):
                if name.lower() in ('x-auth-user', 'x-auth-key'):
                    del new.headers[name]
        return new


_OPENER = urllib.request.build_opener(_KeyRedirect())


class Client:
    """One account on one kosync server. Every method blocks (call it from a thread) and
    raises SyncError."""

    def __init__(self, server, username='', key='', timeout=TIMEOUT, opener=None):
        self.server = normalize_server(server)
        self.username = username
        self.key = key
        self.timeout = timeout
        self._open = opener or _OPENER.open

    def _request(self, method, path, body=None, auth=True, timeout=None):
        headers = {'Accept': ACCEPT, 'User-Agent': USER_AGENT}
        data = None
        if body is not None:
            data = json.dumps(body).encode()
            headers['Content-Type'] = 'application/json'
        if auth:
            headers['x-auth-user'] = self.username
            headers['x-auth-key'] = self.key
        request = urllib.request.Request(self.server + path, data=data, headers=headers,
                                         method=method)
        try:
            with self._open(request, timeout=timeout or self.timeout) as response:
                status, raw = response.status, response.read()
        except urllib.error.HTTPError as error:
            with error:
                status, raw = error.code, error.read() or b''
        except ValueError as error:  # a URL urllib cannot take
            raise SyncError(_('The server address is not valid')) from error
        except (urllib.error.URLError, TimeoutError, OSError) as error:
            reason = getattr(error, 'reason', error)
            log.info('kosync %s %s: %s', method, path, reason)
            if isinstance(reason, socket.timeout) or isinstance(error, TimeoutError):
                message = _('The sync server did not answer in time')
            else:
                message = _('Could not reach the sync server. Check the address and the '
                            'connection.')
            raise SyncError(message, offline=True) from error
        try:
            answer = json.loads(raw.decode() or '{}') if raw else {}
        except (ValueError, UnicodeDecodeError):
            answer = None
        if not 200 <= status < 300:
            raise _http_error(status, answer if isinstance(answer, dict) else {})
        if not isinstance(answer, dict):
            raise SyncError(_('The server did not answer like a KOReader sync server'))
        return status, answer

    def health(self):
        """True when the server answers its /healthcheck (optional in the protocol)."""
        _status, answer = self._request('GET', '/healthcheck', auth=False)
        return answer.get('state') == 'OK'

    def register(self, username, password):
        """Create an account (POST /users/create) and use it."""
        username = username.strip()
        if not username or not password:
            raise SyncError(_('Enter a user name and a password'))
        key = key_for(password)
        self._request('POST', '/users/create', {'username': username, 'password': key},
                      auth=False)
        self.username, self.key = username, key

    def login(self):
        """Check the name and key (GET /users/auth)."""
        if not self.username or not self.key:
            raise SyncError(_('Sign in to sync'), unauthorized=True)
        self._request('GET', '/users/auth')

    def push(self, document, progress, percentage, device, device_id):
        """Store a position (PUT /syncs/progress); the server's timestamp."""
        _status, answer = self._request('PUT', '/syncs/progress', {
            'document': document, 'progress': str(progress),
            'percentage': round_percent(percentage), 'device': device,
            'device_id': device_id})
        return _number(answer.get('timestamp')) or time.time()

    def pull(self, document, timeout=None):
        """The position stored for a document (GET /syncs/progress/<id>), or None."""
        _status, answer = self._request(
            'GET', '/syncs/progress/' + urllib.parse.quote(document, safe=''),
            timeout=timeout)
        percentage = _number(answer.get('percentage'))
        if percentage is None:
            return None  # nothing stored ({}), or not a position
        return Remote(document=str(answer.get('document') or document),
                      percentage=max(0.0, min(1.0, percentage)),
                      progress=str(answer.get('progress') or ''),
                      device=str(answer.get('device') or ''),
                      device_id=str(answer.get('device_id') or ''),
                      timestamp=_number(answer.get('timestamp')) or 0.0)


def _number(value):
    if isinstance(value, bool):
        return None
    if isinstance(value, (int, float)):
        return float(value)
    if isinstance(value, str):
        try:
            return float(value)
        except ValueError:
            return None
    return None


def _http_error(status, answer):
    code = answer.get('code') if isinstance(answer.get('code'), int) else 0
    if status == 401 or code == UNAUTHORIZED:
        return SyncError(_('The user name or password is wrong'), unauthorized=True,
                         status=status, code=code)
    if code == USER_EXISTS:
        return SyncError(_('That user name is taken. Sign in instead, or choose another.'),
                         status=status, code=code)
    if code == REGISTRATION_CLOSED:
        return SyncError(_('This server does not take new accounts'), status=status,
                         code=code)
    if status in (402, 403) or code in (INVALID, NO_DOCUMENT):
        return SyncError(_('The sync server refused the request'), status=status, code=code)
    if status == 404:
        return SyncError(_('No KOReader sync server answers at this address'),
                         status=status, code=code)
    return SyncError(_('The sync server had a problem (error {status})').format(status=status),
                     offline=status >= 500, status=status, code=code)


# -- the key in the keyring ----------------------------------------------------------------

MemoryKeyring = passwords.MemoryKeyring  # a keyring in a dict: tests, the demo library


def account_name(server, username):
    """The keyring account (passwords.py) of a sync key."""
    return f'{username} on {server}'


# -- the store: copies, waiting pushes, last syncs ------------------------------------------


class Store:
    """sync.json beside the library: {'copies': {book id: [{hash, name}]}, 'pending':
    {document: push body}, 'synced': {book id: time}}. Thread-safe."""

    def __init__(self, path):
        self.path = str(path) if path else None
        self.lock = threading.Lock()
        self.data = {'copies': {}, 'pending': {}, 'synced': {}}
        if self.path and os.path.exists(self.path):
            try:
                with open(self.path, encoding='utf-8') as file:
                    loaded = json.load(file)
                if not isinstance(loaded, dict):
                    raise ValueError('not a JSON object')
                for key in self.data:
                    if isinstance(loaded.get(key), dict):
                        self.data[key] = loaded[key]
            except (OSError, ValueError) as error:
                log.warning('cannot read %s: %s', self.path, error)

    def _save(self):
        if not self.path:
            return
        temporary = self.path + '.tmp'
        try:
            with open(temporary, 'w', encoding='utf-8') as file:
                json.dump(self.data, file)
                file.flush()
                os.fsync(file.fileno())  # a crash leaves the old file or the new, whole
            os.replace(temporary, self.path)
        except OSError as error:
            log.warning('cannot write %s: %s', self.path, error)

    def copies(self, book_id):
        with self.lock:
            return list(self.data['copies'].get(str(book_id), []))

    def add_copy(self, book_id, digest, name):
        with self.lock:
            copies = self.data['copies'].setdefault(str(book_id), [])
            entry = {'hash': digest, 'name': name}
            if entry not in copies:
                copies.append(entry)
                del copies[:-8]  # the last few copies are enough
                self._save()

    def pending(self):
        with self.lock:
            return dict(self.data['pending'])

    def set_pending(self, document, body):
        with self.lock:
            self.data['pending'][document] = body
            self._save()

    def drop_pending(self, document):
        with self.lock:
            if self.data['pending'].pop(document, None) is not None:
                self._save()

    def clear_pending(self):
        with self.lock:
            self.data['pending'] = {}
            self._save()

    def synced(self, book_id):
        with self.lock:
            return float(self.data['synced'].get(str(book_id), 0) or 0)

    def set_synced(self, book_id, when):
        with self.lock:
            self.data['synced'][str(book_id)] = when
            self._save()


# -- the service ---------------------------------------------------------------------------


@dataclasses.dataclass
class _Position:
    book_id: int
    path: str
    fmt: str
    fraction: float
    location: str
    pushed: bool = False
    body: dict = None


class Sync(GObject.Object):
    """The app's sync service (see the module). `settings` is the app's Gio.Settings;
    `store_path` the JSON file for copies and waiting pushes; `keyring` and `opener` (in
    place of urllib's urlopen) are for tests."""

    __gtype_name__ = 'BookcaseSync'
    __gsignals__ = {'status-changed': (GObject.SignalFlags.RUN_FIRST, None, (int,))}

    def __init__(self, settings, store_path, keyring=None, opener=None, watch=True):
        super().__init__()
        self.settings = settings
        self.store = Store(store_path)
        self._keyring = keyring
        self._fallback = {}  # account -> key, when the keyring cannot keep it
        self._opener = opener
        self._key = None  # (server, username, key), read from the keyring once
        self._positions = {}  # book id -> _Position
        self._timers = {}  # book id -> GLib source
        self._errors = {}  # book id -> message
        self._retry_source = 0
        self._jobs = queue.Queue()
        self._worker = None
        self._lock = threading.Lock()
        self._sleep_subscription = None
        self._bus = None
        self._network_handler = None
        self._stopped = False
        self._account = None  # (server, username, method): read on the main loop only
        self._read_account()
        self._settings_handler = settings.connect('changed', self._on_setting_changed)
        if watch:
            self._watch_system()
        if self.store.pending():
            self._schedule_retry(10)

    # -- settings and the account ---------------------------------------------------------

    def _read_account(self, *_args):
        self._account = (normalize_server(self.settings.get_string('sync-server')),
                         self.settings.get_string('sync-username').strip(),
                         self.settings.get_string('sync-document-match'))

    def server(self):
        return self._account[0]

    def username(self):
        return self._account[1]

    def signed_in(self):
        return bool(self.username())

    def method(self):
        method = self._account[2]
        return method if method in MATCH_METHODS else 'content'

    def _on_setting_changed(self, _settings, key):
        if key in ('sync-server', 'sync-username', 'sync-document-match'):
            self._read_account()
            self.emit('status-changed', 0)

    def device_name(self):
        return self.settings.get_string('sync-device-name').strip() or default_device_name()

    def device_id(self):
        device_id = self.settings.get_string('sync-device-id')
        if not device_id:
            device_id = uuid.uuid4().hex.upper()
            self.settings.set_string('sync-device-id', device_id)
        return device_id

    def _keys(self):
        if self._keyring is None:
            self._keyring = passwords.Keyring(SECRET_SCHEMA)
        return self._keyring

    def _lookup_key(self, server, username):
        account = account_name(server, username)
        return self._fallback.get(account) or self._keys().lookup(account)

    def _store_key(self, server, username, key):
        account = account_name(server, username)
        try:
            self._keys().store(account, _('Bookcase reading sync: {account}').format(
                account=account), key)
        except passwords.KeyringError as error:
            log.warning('%s; the sync key lasts until Bookcase quits', error)
            self._fallback[account] = key

    def _clear_key(self, server, username):
        account = account_name(server, username)
        self._fallback.pop(account, None)
        self._keys().clear(account)

    def _client(self, timeout=TIMEOUT):
        """A client with the stored key (looked up in this thread: may block)."""
        server, username = self.server(), self.username()
        with self._lock:
            cached = self._key
        if cached is None or cached[:2] != (server, username):
            key = self._lookup_key(server, username) if username else None
            cached = (server, username, key or '')
            with self._lock:
                self._key = cached
        if not cached[2]:
            raise SyncError(_('Sign in again in Preferences to sync'), unauthorized=True)
        return Client(server, username, cached[2], timeout=timeout, opener=self._opener)

    def sign_in(self, server, username, password, create=False, callback=None):
        """Create the account when `create`, check it, keep the key in the keyring and the
        rest in the settings; callback(error or None) on the main loop."""
        server = normalize_server(server)
        username = (username or '').strip()

        def work():
            client = Client(server, username, key_for(password or ''), opener=self._opener)
            if not username or not password:
                raise SyncError(_('Enter a user name and a password'))
            if create:
                client.register(username, password)
            client.login()
            self._store_key(server, username, client.key)
            return client.key

        def done(key, error):
            if error is None:
                with self._lock:
                    self._key = (server, username, key)
                self.settings.set_string('sync-server', server)
                self.settings.set_string('sync-username', username)
                self._errors.clear()
                self.emit('status-changed', 0)
                self.retry()
            if callback is not None:
                callback(error)

        self._thread(work, done)

    def sign_out(self):
        server, username = self.server(), self.username()
        with self._lock:
            self._key = None
        self.settings.set_string('sync-username', '')
        self.store.clear_pending()
        self._errors.clear()
        if username:
            self._thread(lambda: self._clear_key(server, username), None)
        self.emit('status-changed', 0)

    def check(self, callback):
        """Sign in with the stored key; callback(error or None) on the main loop."""
        self._thread(lambda: self._client().login(), lambda _result, error: callback(error))

    # -- pulling ----------------------------------------------------------------------------

    def ids(self, book_id, path):
        return document_ids(path, self.method(), self.store.copies(book_id))

    def pull(self, book_id, path, fmt, callback, timeout=PULL_TIMEOUT):
        """The newest position stored for the book under any of its ids: callback(Remote or
        None, error or None) on the main loop. Nothing is asked when signed out."""
        if not self.signed_in():
            GLib.idle_add(lambda: callback(None, None) and False)
            return

        def work():
            client = self._client(timeout=timeout)
            newest = None
            for document in self.ids(book_id, path):
                remote = client.pull(document, timeout=timeout)
                if remote is not None and (newest is None
                                           or remote.timestamp > newest.timestamp):
                    newest = remote
            return newest

        def done(remote, error):
            self._record(book_id, error, synced=error is None)
            callback(remote, error)

        self._thread(work, done)

    # -- pushing ----------------------------------------------------------------------------

    def position(self, book_id, path, fmt, fraction, location):
        """The reader is at this place: pushed after PUSH_DELAY_S without another."""
        if not self.signed_in() or fraction is None:
            return
        self._positions[book_id] = _Position(book_id, path, fmt, float(fraction),
                                             str(location or ''))
        source = self._timers.pop(book_id, 0)
        if source:
            GLib.source_remove(source)

        def fire():
            self._timers.pop(book_id, None)
            self.flush(book_id)
            return GLib.SOURCE_REMOVE

        self._timers[book_id] = GLib.timeout_add_seconds(PUSH_DELAY_S, fire)

    def flush(self, book_id=None):
        """Push the waiting positions (of one book, or all) now."""
        for key in [book_id] if book_id is not None else list(self._positions):
            source = self._timers.pop(key, 0)
            if source:
                GLib.source_remove(source)
            position = self._positions.get(key)
            if position is None or position.pushed or not self.signed_in():
                continue
            position.pushed = True
            position.body = {
                'progress': progress_for(position.fmt, position.fraction, position.location),
                'percentage': round_percent(position.fraction),
                'device': self.device_name(), 'device_id': self.device_id()}
            self._enqueue(self._push_job, position)

    def _push_job(self, position):
        body = position.body
        documents = self.ids(position.book_id, position.path)
        if not documents:
            return SyncError(_('The book’s file cannot be read'))
        error = None
        for document in documents:
            error = self._send(document, dict(body, document=document,
                                              book_id=position.book_id)) or error
        return error

    def _send(self, document, body):
        """Push one body; on a failure worth retrying, keep it in the store."""
        try:
            self._client().push(document, body['progress'], body['percentage'],
                                body['device'], body['device_id'])
        except SyncError as error:
            if error.offline:
                self.store.set_pending(document, body)
                GLib.idle_add(self._schedule_retry, RETRY_S)
            else:
                self.store.drop_pending(document)
            return error
        self.store.drop_pending(document)
        return None

    def retry(self):
        """Push the positions that are waiting for the server."""
        if self._retry_source:
            GLib.source_remove(self._retry_source)
            self._retry_source = 0
        pending = self.store.pending()
        if pending and self.signed_in():
            self._enqueue(self._retry_job, pending)

    def _retry_job(self, pending):
        error = None
        for document, body in pending.items():
            failed = self._send(document, body)
            error = failed or error
            book_id = body.get('book_id')
            if book_id is not None:
                GLib.idle_add(self._record, book_id, failed, failed is None)
        return error

    def _schedule_retry(self, seconds):
        if not self._retry_source:
            def fire():
                self._retry_source = 0
                self.retry()
                return GLib.SOURCE_REMOVE
            self._retry_source = GLib.timeout_add_seconds(seconds, fire)
        return GLib.SOURCE_REMOVE

    # -- what the reader shows --------------------------------------------------------------

    def remember_copy(self, book_id, path):
        """A copy of the book now at `path` (sent to an e-reader): its ids name the book
        too. Reads the file: call it from the thread that wrote it."""
        try:
            digest = content_digest(path)
        except OSError as error:
            log.warning('cannot read the copy %s: %s', path, error)
            return
        self.store.add_copy(book_id, digest, os.path.basename(path))

    def status(self, book_id):
        """(the last sync's time or 0, whether a push waits for the server, the last error
        or '')."""
        waiting = any(body.get('book_id') == book_id for body in self.store.pending().values())
        return self.store.synced(book_id), waiting, self._errors.get(book_id, '')

    def _record(self, book_id, error, synced):
        if self._stopped:
            return GLib.SOURCE_REMOVE
        if error is not None and not error.offline:
            self._errors[book_id] = str(error)
        else:
            self._errors.pop(book_id, None)
        if synced:
            self.store.set_synced(book_id, time.time())
        self.emit('status-changed', book_id)
        return GLib.SOURCE_REMOVE

    # -- threads ----------------------------------------------------------------------------

    def _thread(self, work, done):
        def run():
            result, error = None, None
            try:
                result = work()
            except SyncError as caught:
                error = caught
            except Exception as caught:  # a bug: say so in a sentence, log the rest
                log.exception('sync')
                error = SyncError(_('Sync failed: {error}').format(error=caught))
            if done is not None:
                GLib.idle_add(lambda: None if self._stopped else done(result, error) and False)

        threading.Thread(target=run, name='bookcase-sync', daemon=True).start()

    def _enqueue(self, job, argument):
        self._jobs.put((job, argument))
        if self._worker is None or not self._worker.is_alive():
            self._worker = threading.Thread(target=self._work, name='bookcase-sync-push',
                                            daemon=True)
            self._worker.start()

    def _work(self):
        while True:
            try:
                job, argument = self._jobs.get(timeout=5)
            except queue.Empty:
                return
            try:
                error = job(argument)
            except Exception as caught:
                log.exception('sync push')
                error = SyncError(str(caught))
            if job == self._push_job:
                GLib.idle_add(self._record, argument.book_id, error, error is None)
            self._jobs.task_done()

    def wait(self, timeout=5.0):
        """Block until the worker has done its queue (tests, quitting)."""
        deadline = time.monotonic() + timeout
        while self._jobs.unfinished_tasks and time.monotonic() < deadline:
            time.sleep(0.01)

    # -- the system: network back, going to sleep ------------------------------------------

    def _watch_system(self):
        try:
            monitor = Gio.NetworkMonitor.get_default()
            self._network_handler = (monitor, monitor.connect('network-changed',
                                                              self._on_network_changed))
        except GLib.Error:
            pass
        try:
            self._bus = Gio.bus_get_sync(Gio.BusType.SYSTEM, None)
            self._sleep_subscription = self._bus.signal_subscribe(
                'org.freedesktop.login1', 'org.freedesktop.login1.Manager', 'PrepareForSleep',
                '/org/freedesktop/login1', None, Gio.DBusSignalFlags.NONE,
                self._on_prepare_for_sleep)
        except GLib.Error:
            self._bus = None

    def _on_network_changed(self, _monitor, available):
        if available and self.store.pending():
            self._schedule_retry(5)

    def _on_prepare_for_sleep(self, _bus, _sender, _path, _interface, _signal, parameters):
        if parameters.unpack()[0]:
            self.flush()

    def shutdown(self):
        """Push what waits (briefly) and stop watching the system."""
        self.flush()
        self.wait(3.0)
        self._stopped = True  # results still on their way are dropped
        for source in list(self._timers.values()) + [self._retry_source]:
            if source:
                GLib.source_remove(source)
        self._timers.clear()
        self._retry_source = 0
        if self.settings.handler_is_connected(self._settings_handler):
            self.settings.disconnect(self._settings_handler)
        if self._network_handler is not None:
            monitor, handler = self._network_handler
            monitor.disconnect(handler)
            self._network_handler = None
        if self._bus is not None and self._sleep_subscription is not None:
            self._bus.signal_unsubscribe(self._sleep_subscription)
            self._sleep_subscription = None
