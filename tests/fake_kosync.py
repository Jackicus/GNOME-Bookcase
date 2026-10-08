# SPDX-License-Identifier: GPL-2.0-or-later
# SPDX-FileCopyrightText: 2026 Jack Tully

"""A KOReader progress sync server on 127.0.0.1, for tests and screenshots.

    with FakeServer() as server:        # server.url, server.users, server.progress,
        ...                             # server.requests [(method, path, headers, body)]

It answers as koreader-sync-server does: POST /users/create (201, 402 code 2002 when the name
is taken, 2005 when `closed`), GET /users/auth (200, 401 code 2001), PUT /syncs/progress (200
{document, timestamp}), GET /syncs/progress/<document> (200, {} when nothing is stored) and
GET /healthcheck. `down = True` makes it answer 503.
"""

import json
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer


class FakeServer:
    def __init__(self):
        self.users = {}  # name -> key
        self.progress = {}  # (user, document) -> record
        self.requests = []
        self.closed = False
        self.down = False
        self.delay = 0.0
        self.clock = None  # a function giving the timestamp, else time.time
        server = self

        class Handler(BaseHTTPRequestHandler):
            def log_message(self, *_args):
                pass

            def _answer(self, status, body):
                data = json.dumps(body).encode()
                self.send_response(status)
                self.send_header('Content-Type', 'application/json')
                self.send_header('Content-Length', str(len(data)))
                self.end_headers()
                try:
                    self.wfile.write(data)
                except BrokenPipeError:
                    pass  # the client gave up (a timeout test)

            def _body(self):
                length = int(self.headers.get('Content-Length') or 0)
                raw = self.rfile.read(length) if length else b''
                try:
                    return json.loads(raw or b'{}')
                except ValueError:
                    return None

            def _authorized(self):
                user = self.headers.get('x-auth-user')
                key = self.headers.get('x-auth-key')
                if not user or server.users.get(user) != key:
                    self._answer(401, {'code': 2001, 'message': 'Unauthorized'})
                    return None
                return user

            def _handle(self, method):
                body = self._body() if method in ('POST', 'PUT') else None
                server.requests.append((method, self.path,
                                        {k.lower(): v for k, v in self.headers.items()}, body))
                if server.delay:
                    time.sleep(server.delay)
                if server.down:
                    return self._answer(503, {'message': 'down'})
                if method == 'GET' and self.path == '/healthcheck':
                    return self._answer(200, {'state': 'OK'})
                if method == 'POST' and self.path == '/users/create':
                    if server.closed:
                        return self._answer(402, {'code': 2005,
                                                  'message': 'User registration is disabled.'})
                    name, key = (body or {}).get('username'), (body or {}).get('password')
                    if not name or not key:
                        return self._answer(403, {'code': 2003, 'message': 'Invalid request'})
                    if name in server.users:
                        return self._answer(402, {'code': 2002,
                                                  'message': 'Username is already registered.'})
                    server.users[name] = key
                    return self._answer(201, {'username': name})
                if method == 'GET' and self.path == '/users/auth':
                    if self._authorized():
                        self._answer(200, {'authorized': 'OK'})
                    return None
                if method == 'PUT' and self.path == '/syncs/progress':
                    user = self._authorized()
                    if not user:
                        return None
                    body = body or {}
                    if not body.get('document'):
                        return self._answer(403, {'code': 2004,
                                                  'message': "Field 'document' not provided."})
                    if (not isinstance(body.get('percentage'), (int, float))
                            or not body.get('progress') or not body.get('device')):
                        return self._answer(403, {'code': 2003, 'message': 'Invalid request'})
                    stamp = int((server.clock or time.time)())
                    server.progress[(user, body['document'])] = {
                        'percentage': body['percentage'], 'progress': body['progress'],
                        'device': body['device'], 'device_id': body.get('device_id', ''),
                        'timestamp': stamp}
                    return self._answer(200, {'document': body['document'], 'timestamp': stamp})
                if method == 'GET' and self.path.startswith('/syncs/progress/'):
                    user = self._authorized()
                    if not user:
                        return None
                    document = self.path[len('/syncs/progress/'):]
                    record = server.progress.get((user, document))
                    return self._answer(200, dict(record, document=document) if record else {})
                return self._answer(404, {'message': 'not found'})

            def do_GET(self):  # noqa: N802
                self._handle('GET')

            def do_POST(self):  # noqa: N802
                self._handle('POST')

            def do_PUT(self):  # noqa: N802
                self._handle('PUT')

        self.httpd = ThreadingHTTPServer(('127.0.0.1', 0), Handler)
        self.httpd.daemon_threads = True
        self.url = f'http://127.0.0.1:{self.httpd.server_address[1]}'
        self.thread = None

    def start(self):
        self.thread = threading.Thread(target=self.httpd.serve_forever, daemon=True)
        self.thread.start()
        return self

    def stop(self):
        self.httpd.shutdown()
        self.httpd.server_close()

    def __enter__(self):
        return self.start()

    def __exit__(self, *_args):
        self.stop()

    def store(self, user, document, percentage, progress, device, device_id, timestamp):
        """Put a position on the server, as another device would."""
        self.progress[(user, document)] = {
            'percentage': percentage, 'progress': progress, 'device': device,
            'device_id': device_id, 'timestamp': timestamp}
