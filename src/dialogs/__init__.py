# SPDX-License-Identifier: GPL-2.0-or-later
# SPDX-FileCopyrightText: 2026 Jack Tully

"""The library window's dialogs. Each module has present*(app, parent, …) functions that
build a dialog, present it over `parent` (a window or a widget in one) and return it.

    watch_dialog(dialog, parent)    # the window's keyed actions step aside while it is open
"""


def watch_dialog(dialog, parent):
    """Call the parent window's set_dialog_open(True) when the dialog maps and (False) when
    it closes, so its entries get the keys the window's actions would take. A dialog over
    another dialog leaves it to that one."""
    from gi.repository import Adw

    if isinstance(parent, Adw.Dialog):
        return
    window = parent
    if window is not None and not hasattr(window, 'set_dialog_open'):
        window = window.get_root() if hasattr(window, 'get_root') else None
    if window is None or not hasattr(window, 'set_dialog_open'):
        return
    dialog.connect('map', lambda *_args: window.set_dialog_open(True))
    dialog.connect('closed', lambda *_args: window.set_dialog_open(False))
