# SPDX-License-Identifier: GPL-2.0-or-later
# SPDX-FileCopyrightText: 2026 Jack Tully

"""The dialogs of Send to Kindle, Kindle highlights and Find Metadata for many books, set up
for scripts/screenshot.py's --dialog kindle-setup, send-kindle, clippings and bulk: an
invented mail account (settings in memory, the password in a keyring in memory), invented
clippings of the demo library's books, and canned Open Library answers. Nothing reaches the
network or the desktop's keyring.
"""

import json
import time
import urllib.parse

from gi.repository import GLib

NAMES = ('kindle-setup', 'send-kindle', 'clippings', 'bulk', 'bulk-progress', 'bulk-choice')

CLIPPINGS = """The Glass Estuary (Imogen Vale)
- Your Highlight on page 14 | Location 201-204 | Added on Monday, 3 March 2025 10:12:01

The tide tables were wrong again, and the estuary did not care.
==========
The Glass Estuary (Imogen Vale)
- Your Highlight on page 40 | Location 610-612 | Added on Monday, 3 March 2025 10:30:00

Glass remembers the shape of the fire that made it.
==========
Halcyon Drift (Priya Anand-Holt)
- Your Highlight on Location 1200-1203 | Added on Friday, 7 March 2025 21:00:00

Every ship is a small argument against the dark.
==========
Halcyon Drift (Priya Anand-Holt)
- Your Note on Location 1203 | Added on Friday, 7 March 2025 21:01:00

This is where it turns.
==========
The Harbourmaster's Ledger (Wilhelmina Strand)
- Your Highlight on Location 88-90 | Added on Sunday, 9 March 2025 08:00:00

The ledger balanced; the sea never did.
==========
"""


def _account():
    from bookcase import mail

    return mail.Account(server='smtp.gmail.com', port=587, security='starttls',
                        sender='reader.lark@example.org', kindle='reader_lark_7@kindle.com',
                        preset='gmail')


def _mail_ready(app):
    from bookcase import passwords

    account = _account()
    account.save(app.settings)
    app.settings.set_boolean('kindle-mail-explained', True)
    app.mail_keyring = passwords.MemoryKeyring({account.key: 'invented-app-password'})


def _ids(app, *titles):
    ids = []
    for title in titles:
        books = app.library.books(query=f'title:"{title}"', limit=1)
        if books:
            ids.append(books[0].id)
    return ids


def canned_fetch(app, delay=0.0):
    """online's fetch answering from the demo library itself: a search finds each book
    with a publisher, a date, subjects and a description it may lack, and a cover drawn for
    another book; one title is not found and one is only a likely match."""
    covers = [app.covers.data(book) for book in app.library.books(limit=40)
              if book.has_cover]
    covers = [data for data in covers if data]

    def fetch(url):
        time.sleep(delay)
        parts = urllib.parse.urlsplit(url)
        query = dict(urllib.parse.parse_qsl(parts.query))
        if parts.path == '/search.json':
            title, author = query.get('title', ''), query.get('author', '')
            if 'Ferry' in title:
                return json.dumps({'docs': []}).encode()
            others = []
            if 'Orchard' in title:
                title = 'Murder in the Orchard'
                # Three likely books: the review offers a choice.
                others = [{'key': '/works/OL2W', 'title': 'The Orchard Murders: A Novel',
                           'author_name': [author], 'first_publish_year': 2019,
                           'publisher': ['Lantern House'], 'number_of_pages_median': 288},
                          {'key': '/works/OL3W', 'title': 'Orchard Murders',
                           'author_name': ['T. Wren'], 'first_publish_year': 2021}]
            doc = {'key': '/works/OL1W', 'title': title, 'author_name': [author],
                   'first_publish_year': 2014, 'publisher': ['Tidewater Press'],
                   'language': ['eng'], 'cover_i': 7 + len(title) % 5,
                   'number_of_pages_median': 240 + len(title) * 3,
                   'subject': ['Fiction', 'Coastal towns', 'Families']}
            return json.dumps({'docs': [doc] + others}).encode()
        if parts.path.startswith('/works/'):
            return json.dumps({'description': 'A family, a harbour and the winter that '
                               'changed both. Told across three generations of keepers.'}
                              ).encode()
        if parts.path.startswith('/b/id/') and covers:
            return covers[int(parts.path.split('/')[-1].split('-')[0]) % len(covers)]
        from bookcase.online import OnlineError

        raise OnlineError('Not found')

    return fetch


def open_dialog(app, window, name, book_id):
    if name == 'kindle-setup':
        from bookcase.dialogs import kindle_mail

        _mail_ready(app)
        return kindle_mail.present_setup(app, window)
    if name == 'send-kindle':
        from bookcase.dialogs import send

        _mail_ready(app)
        ids = _ids(app, 'The Glass Estuary', 'Notes on Letterpress',
                   "The Lamplighter's Moth, Volume One")
        dialog = send.present(app, window, ids)
        email = next((index for index, device in enumerate(dialog.devices)
                      if device.kind == 'email'), None)
        if email is not None:
            dialog.device_combo.set_selected(email)
        return dialog
    if name == 'clippings':
        from bookcase.dialogs import highlights

        return highlights.present_import(app, window, CLIPPINGS)
    if name in ('bulk', 'bulk-progress', 'bulk-choice'):
        from bookcase.dialogs import bulk_metadata

        ids = _ids(app, 'Winter at Aldmoor House', 'The Small Hours Hotel',
                   'The Last Ferry to Inchcolm', 'The Orchard Murders',
                   'Paper Boats on the Ouse', 'Starlings over Wexcombe')
        dialog = bulk_metadata.present(app, window, ids, fetch=canned_fetch(
            app, delay=0.3 if name == 'bulk-progress' else 0.0))
        if name == 'bulk-progress':
            return dialog

        def review():
            if dialog.finished:
                dialog.review()
                # The demo books lack nothing: show what replacing would do.
                dialog.replace_row.set_active(True)
                rows = dialog._review_rows
                if name == 'bulk-choice':  # the book with three likely matches
                    rows = [row for row in rows if len(next(
                        lookup for lookup in dialog.lookups
                        if lookup.book_id == row.book_id).choices) > 1] or rows
                rows[0].set_expanded(True)
                return GLib.SOURCE_REMOVE
            return GLib.SOURCE_CONTINUE

        GLib.timeout_add(300, review)
        return dialog
    raise ValueError(name)
