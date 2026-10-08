# SPDX-License-Identifier: GPL-2.0-or-later
# SPDX-FileCopyrightText: 2026 Jack Tully

"""The Keyboard Shortcuts dialog lists every key, and the accelerators are sane."""

import unittest

from tests import ROOT  # noqa: F401
from bookcase import shortcuts

# Accelerators the HIG gives a meaning the app must honour if it binds them.
STANDARD = {'<primary>o': 'app.add-books', '<primary>q': 'app.quit', '<primary>w': 'win.close',
            '<primary>f': 'win.search', '<primary>comma': 'app.preferences',
            '<primary>question': 'app.shortcuts', '<primary>z': 'app.undo'}


class ShortcutsTest(unittest.TestCase):

    def test_the_dialog_lists_every_accelerator(self):
        listed = set()
        for _title, items in shortcuts.sections():
            for _description, accel in items:
                listed.update(accel.split())
        for name, accels in shortcuts.ACCELS.items():
            for accel in accels:
                self.assertIn(accel, listed, f'{name} ({accel}) is not in the dialog')
        for table in (shortcuts.READER, shortcuts.GRID):
            for key, accels in table.items():
                if key in ('scroll-down', 'scroll-up', 'leave-fullscreen', 'close'):
                    continue  # the arrows and Escape go without saying; Ctrl+W is listed
                for accel in accels:
                    self.assertIn(accel, listed, f'{key} ({accel}) is not in the dialog')

    def test_standard_accelerators_keep_their_meaning(self):
        for accel, action in STANDARD.items():
            owners = [name for name, accels in shortcuts.ACCELS.items() if accel in accels]
            self.assertEqual(owners, [action], accel)

    def test_no_accelerator_is_bound_twice(self):
        seen = {}
        for name, accels in shortcuts.ACCELS.items():
            for accel in accels:
                self.assertNotIn(accel, seen, f'{accel}: {name} and {seen.get(accel)}')
                seen[accel] = name

    def test_accelerator_lookup(self):
        self.assertEqual(shortcuts.accelerator('bookmark'), '<primary>d')
        self.assertEqual(shortcuts.accelerator('app.add-books'), '<primary>o')
        with self.assertRaises(KeyError):
            shortcuts.accelerator('nothing')


if __name__ == '__main__':
    unittest.main()
