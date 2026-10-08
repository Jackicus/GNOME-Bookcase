# SPDX-License-Identifier: GPL-2.0-or-later
# SPDX-FileCopyrightText: 2026 Jack Tully

"""A linked Calibre library's row in Preferences → Library, with Keep Calibre in Step.

    library_row(app, folder, title, subtitle) -> Adw.ExpanderRow

The row expands to a switch (Keep Calibre in Step: edits made in Bookcase are written to
the library's metadata.db, calibre_write.py) and, while it is on, a line saying how it
stands (app.calibre.status(): up to date, '3 books' changes waiting for Calibre to close',
a refusal). Turning it on asks first (an AdwAlertDialog that says what is written, how, and
how many books edited earlier go too); turning it off does not.
"""

from gettext import gettext as _
from gettext import ngettext

from gi.repository import Adw, GLib

from ..widgets.util import connect_weak


def library_row(app, folder, title, subtitle):
    return CalibreLibraryRow(app, folder, title=title, subtitle=subtitle)


class CalibreLibraryRow(Adw.ExpanderRow):
    __gtype_name__ = 'BookcaseCalibreLibraryRow'

    def __init__(self, app, folder, **properties):
        super().__init__(**properties)
        self.app = app
        self.folder = folder
        self._quiet = False
        self.switch_row = Adw.SwitchRow(
            title=_('Keep Calibre in Step'),
            subtitle=_('Write the details you edit in Bookcase to this Calibre library'))
        self.add_row(self.switch_row)
        self.status_row = Adw.ActionRow(title=_('Changes'), subtitle_selectable=True)
        self.status_row.add_css_class('property')
        self.add_row(self.status_row)
        connect_weak(self.switch_row, 'notify::active', self._on_switched)
        sync = getattr(app, 'calibre', None)
        if sync is not None:
            connect_weak(sync, 'changed', self._on_sync_changed)
        else:
            self.switch_row.set_sensitive(False)
        self.update()

    def _sync(self):
        return getattr(self.app, 'calibre', None)

    def update(self):
        sync = self._sync()
        enabled = sync is not None and sync.is_enabled(self.folder.path)
        self._quiet = True
        try:
            self.switch_row.set_active(enabled)
        finally:
            self._quiet = False
        self.status_row.set_visible(enabled)
        if not enabled:
            return
        status = sync.status(self.folder.path)
        self.status_row.set_subtitle(GLib.markup_escape_text(status.describe()))
        for name in ('warning', 'error'):
            self.status_row.remove_css_class(name)
        if status.state in ('refused', 'error'):
            self.status_row.add_css_class('error')
        elif status.state == 'waiting':
            self.status_row.add_css_class('warning')

    def _on_sync_changed(self, _sync):
        self.update()

    def _on_switched(self, switch_row, _pspec):
        if self._quiet:
            return
        sync = self._sync()
        if sync is None:
            return
        if not switch_row.get_active():
            sync.set_enabled(self.folder.path, False)
            return
        # Not on until the user says so.
        self._quiet = True
        switch_row.set_active(False)
        self._quiet = False
        self.confirm()

    def confirm(self):
        """Ask before turning Keep Calibre in Step on; returns the dialog."""
        sync = self._sync()
        earlier = sync.count_pending(self.folder.path)
        body = _('Details you edit in Bookcase — title, authors, series, tags, publisher, '
                 'date, languages, description, rating, identifiers and cover — will be '
                 'written to this library’s metadata.db, the way Calibre writes them. Files '
                 'and folders are never renamed.\n\nBookcase writes only while Calibre is '
                 'closed, backs up metadata.db first (once a day, beside it) and checks the '
                 'database after each change. Changes made while Calibre is open wait until '
                 'it closes.')
        if earlier:
            body += '\n\n' + ngettext(
                '{n} book you edited in Bookcase earlier will be written too.',
                '{n} books you edited in Bookcase earlier will be written too.',
                earlier).format(n=earlier)
        dialog = Adw.AlertDialog(heading=_('Keep Calibre in Step?'), body=body)
        dialog.add_response('cancel', _('_Cancel'))
        dialog.add_response('keep', _('_Write to Calibre'))
        dialog.set_response_appearance('keep', Adw.ResponseAppearance.SUGGESTED)
        dialog.set_default_response('cancel')
        dialog.set_close_response('cancel')
        connect_weak(dialog, 'response', self._on_response)
        dialog.present(self)  # over the Preferences dialog
        return dialog

    def _on_response(self, _dialog, response):
        sync = self._sync()
        if response == 'keep' and sync is not None:
            sync.set_enabled(self.folder.path, True)
        self.update()
