# SPDX-License-Identifier: GPL-2.0-or-later
# SPDX-FileCopyrightText: 2026 Jack Tully

"""What the dialog tests share: a stand-in Application over a temporary library.

    with fake_app() as app:          # app.library, app.covers, app.settings, app.importer
        ...                          # app.toasts: [(text, undo)]; app.reports: [error]
"""

import contextlib

from tests import ROOT  # noqa: F401  (registers bookcase)
from tests.support import temporary_library

SCHEMA_ID = 'io.github.jackicus.Bookcase'


class FakeApp:
    def __init__(self, library):
        from gi.repository import Gio

        from bookcase.covers import CoverStore

        self.library = library
        self.covers = CoverStore(library.path.parent, library)
        self.settings = Gio.Settings.new(SCHEMA_ID)
        self.importer = None
        self.toasts = []
        self.reports = []
        self.undone = []

    def toast(self, text, undo=False):
        self.toasts.append((text, undo))

    def report(self, error, context=None):
        self.reports.append((error, context))

    def undo(self):
        self.undone.append(self.library.undo())

    def window(self):
        return None


@contextlib.contextmanager
def fake_app():
    with temporary_library() as library:
        app = FakeApp(library)
        try:
            yield app
        finally:
            shutdown = getattr(app.covers, 'shutdown', None)
            if shutdown is not None:
                shutdown()
