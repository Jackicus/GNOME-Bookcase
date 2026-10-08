# SPDX-License-Identifier: GPL-2.0-or-later
# SPDX-FileCopyrightText: 2026 Jack Tully

"""Reading goals: books a year and minutes a day, either 0 for none.

    present(app, parent)        # returns the dialog (an Adw.AlertDialog)

The settings are `goal-books` and `goal-minutes`, written on Save; Cancel and Escape change
nothing. The Statistics page (pages/stats.py) and the home page's goal card follow them.
"""

from gettext import gettext as _

from gi.repository import Adw, Gtk

from . import watch_dialog


def present(app, parent):
    settings = app.settings
    dialog = Adw.AlertDialog(
        heading=_('Reading Goals'),
        body=_('Goals are for you alone: progress shows on the Statistics page, and nothing '
               'reminds you of them. Set a goal to 0 to turn it off.'))
    books = Adw.SpinRow.new_with_range(0, 1000, 1)
    books.set_title(_('Books a Year'))
    books.set_value(settings.get_int('goal-books'))
    minutes = Adw.SpinRow.new_with_range(0, 1440, 5)
    minutes.set_title(_('Minutes a Day'))
    minutes.set_value(settings.get_int('goal-minutes'))
    rows = Gtk.ListBox(selection_mode=Gtk.SelectionMode.NONE)
    rows.add_css_class('boxed-list')
    rows.append(books)
    rows.append(minutes)
    dialog.set_extra_child(rows)
    dialog.add_response('cancel', _('_Cancel'))
    dialog.add_response('save', _('_Save'))
    dialog.set_response_appearance('save', Adw.ResponseAppearance.SUGGESTED)
    dialog.set_default_response('save')
    dialog.set_close_response('cancel')
    dialog.books_row = books
    dialog.minutes_row = minutes

    def on_response(_dialog, response):
        if response == 'save':
            books.update()
            minutes.update()
            settings.set_int('goal-books', int(books.get_value()))
            settings.set_int('goal-minutes', int(minutes.get_value()))

    dialog.connect('response', on_response)
    watch_dialog(dialog, parent)
    dialog.present(parent)
    return dialog
