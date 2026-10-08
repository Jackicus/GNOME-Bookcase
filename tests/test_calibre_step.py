# SPDX-License-Identifier: GPL-2.0-or-later
# SPDX-FileCopyrightText: 2026 Jack Tully

"""dialogs/calibre_step.py: a linked Calibre library's row in Preferences, whose Keep
Calibre in Step switch asks before it turns on and then shows how the writes stand."""

import unittest

from tests import ROOT  # noqa: F401
from tests.gtk import requires_gtk


@requires_gtk
class TestCalibreLibraryRow(unittest.TestCase):

    def test_the_switch_asks_first(self):
        from tests.dialog_support import fake_app
        from bookcase.calibre_write import SETTING, CalibreSync, Status
        from bookcase.dialogs import calibre_step

        with fake_app() as app:
            app.settings.set_strv(SETTING, [])
            app.calibre = CalibreSync(app.library, app.covers, app.settings)
            folder_id = app.library.add_folder('/invented/Calibre Library', 'calibre')
            folder = next(f for f in app.library.folders() if f.id == folder_id)
            row = calibre_step.library_row(app, folder, 'Calibre Library', '~/Calibre Library')
            self.assertFalse(row.switch_row.get_active())
            self.assertFalse(row.status_row.get_visible())
            asked = []
            row.confirm = lambda: asked.append(True)
            row.switch_row.set_active(True)
            self.assertEqual(asked, [True])
            self.assertFalse(row.switch_row.get_active())  # not on until confirmed
            self.assertFalse(app.calibre.is_enabled(folder.path))
            row._on_response(None, 'keep')
            self.assertTrue(app.calibre.is_enabled(folder.path))
            self.assertEqual(app.settings.get_strv(SETTING), [folder.path])
            self.assertTrue(row.switch_row.get_active())
            self.assertTrue(row.status_row.get_visible())
            app.calibre._statuses[folder.path] = Status(True, 'waiting', 3)
            app.calibre.emit('changed')
            self.assertIn('3', row.status_row.get_subtitle())
            self.assertTrue(row.status_row.has_css_class('warning'))
            row.switch_row.set_active(False)  # off at once, no question
            self.assertEqual(app.settings.get_strv(SETTING), [])
            app.calibre.shutdown()
