# SPDX-License-Identifier: GPL-2.0-or-later
# SPDX-FileCopyrightText: 2026 Jack Tully

"""The Preferences dialog's Sync page: reading positions over KOReader's progress sync.

    page = SyncPage(app)                # added by dialogs/preferences.py

Signed out: the server (KOReader's public one by default, or one's own), a user name and a
password, Sign In and Create Account (app.sync.sign_in; an error shows under the buttons).
Signed in: who and where, Test Connection (app.sync.check) and Sign Out. Always: how books
are matched (sync-document-match: the file's content, KOReader's default, or its name) and
this computer's name as other devices show it (sync-device-name). Built in code: a page of
its own, no template.
"""

import logging
import urllib.parse
from gettext import gettext as _

from gi.repository import Adw, GLib, Gtk

from .. import kosync
from ..widgets.util import connect_weak

log = logging.getLogger(__name__)

METHODS = ('content', 'filename')


class SyncPage(Adw.PreferencesPage):
    __gtype_name__ = 'BookcaseSyncPage'

    def __init__(self, app):
        super().__init__(name='sync', title=_('Sync'),
                         icon_name='emblem-synchronizing-symbolic')
        self.app = app
        self.sync = getattr(app, 'sync', None)
        self.settings = app.settings
        self._handlers = []
        self._busy = False

        intro = Adw.PreferencesGroup(
            title=_('Reading Position'),
            description=_('Pick up where you left off on a KOReader e-reader, another '
                          'computer, or any app that speaks KOReader’s progress sync. Only '
                          'the place is sent: a fingerprint of the book’s file, how far in '
                          'you are, and this computer’s name.'))
        self.add(intro)

        # Signed out: the account form.
        self.form = Adw.PreferencesGroup()
        self.server_row = Adw.EntryRow(title=_('Server'), input_purpose=Gtk.InputPurpose.URL)
        self.username_row = Adw.EntryRow(title=_('User Name'))
        self.username_row.set_input_hints(Gtk.InputHints.NO_SPELLCHECK)
        self.password_row = Adw.PasswordEntryRow(title=_('Password'))
        for row in (self.server_row, self.username_row, self.password_row):
            self.form.add(row)
        self.password_row.connect('entry-activated', lambda *_args: self.sign_in())
        self.username_row.connect('entry-activated',
                                  lambda *_args: self.password_row.grab_focus())

        buttons = Gtk.Box(spacing=12, halign=Gtk.Align.CENTER, margin_top=18,
                          homogeneous=True)
        self.sign_in_button = Gtk.Button(label=_('_Sign In'), use_underline=True)
        self.sign_in_button.add_css_class('pill')
        self.sign_in_button.add_css_class('suggested-action')
        self.sign_in_button.connect('clicked', lambda *_args: self.sign_in())
        self.create_button = Gtk.Button(label=_('_Create Account'), use_underline=True)
        self.create_button.add_css_class('pill')
        self.create_button.connect('clicked', lambda *_args: self.sign_in(create=True))
        buttons.append(self.sign_in_button)
        buttons.append(self.create_button)
        self.form.add(buttons)
        self.error_label = Gtk.Label(wrap=True, justify=Gtk.Justification.CENTER,
                                     margin_top=12, visible=False)
        self.error_label.add_css_class('error')
        self.form.add(self.error_label)
        self.add(self.form)

        # Signed in: the account and its buttons.
        self.account = Adw.PreferencesGroup()
        self.account_row = Adw.ActionRow()
        self.account_row.add_prefix(Gtk.Image(icon_name='avatar-default-symbolic',
                                              accessible_role=Gtk.AccessibleRole.PRESENTATION))
        self.sign_out_button = Gtk.Button(label=_('Sign _Out'), use_underline=True,
                                          valign=Gtk.Align.CENTER)
        self.sign_out_button.connect('clicked', lambda *_args: self.sign_out())
        self.account_row.add_suffix(self.sign_out_button)
        self.account.add(self.account_row)
        self.test_row = Adw.ActionRow(title=_('Connection'))
        self.test_spinner = Adw.Spinner(visible=False)
        self.test_row.add_suffix(self.test_spinner)
        self.test_button = Gtk.Button(label=_('_Test'), use_underline=True,
                                      valign=Gtk.Align.CENTER)
        self.test_button.connect('clicked', lambda *_args: self.test())
        self.test_row.add_suffix(self.test_button)
        self.account.add(self.test_row)
        self.add(self.account)

        options = Adw.PreferencesGroup(title=_('Options'))
        self.method_row = Adw.ComboRow(
            title=_('Match Books By'),
            subtitle=_('The same as KOReader’s setting; content is its default'),
            model=Gtk.StringList.new([_('Content'), _('File Name')]))
        self.method_row.connect('notify::selected', self._on_method_selected)
        options.add(self.method_row)
        self.device_row = Adw.EntryRow(title=_('This Computer’s Name'),
                                       show_apply_button=True)
        self.device_row.connect('apply', self._on_device_name)
        options.add(self.device_row)
        self.add(options)

        help_group = Adw.PreferencesGroup()
        link = Gtk.Label(
            label=_('Set up the same account on your e-reader in KOReader: Tools → Progress '
                    'sync. <a href="{url}">KOReader’s guide</a>').format(
                url=GLib.markup_escape_text(kosync.DOCS_URL)),
            use_markup=True, wrap=True, xalign=0)
        link.add_css_class('dimmed')
        help_group.add(link)
        self.add(help_group)

        # The service and the settings outlive the page: held weakly, let go on unrealize.
        if self.sync is not None:
            self._watch(self.sync, 'status-changed', self._on_changed)
        for key in ('sync-document-match', 'sync-device-name'):
            self._watch(self.settings, 'changed::' + key, self._on_changed)
        self.connect('unrealize', self._on_unrealize)
        self.server_row.set_text(self.settings.get_string('sync-server')
                                 or kosync.DEFAULT_SERVER)
        self.refresh()

    def _watch(self, obj, signal, method):
        self._handlers.append((obj, connect_weak(obj, signal, method)))

    def _on_changed(self, *_args):
        self.refresh()

    def _on_unrealize(self, _widget):
        for obj, handler in self._handlers:
            if obj.handler_is_connected(handler):
                obj.disconnect(handler)
        self._handlers = []

    # -- showing ----------------------------------------------------------------------------

    def refresh(self):
        sync = self.sync
        signed_in = sync is not None and sync.signed_in()
        self.form.set_visible(not signed_in)
        self.account.set_visible(signed_in)
        if signed_in:
            self.account_row.set_title(GLib.markup_escape_text(
                _('Signed in as {user}').format(user=sync.username())))
            self.account_row.set_subtitle(GLib.markup_escape_text(_host(sync.server())))
        method = self.settings.get_string('sync-document-match')
        index = METHODS.index(method) if method in METHODS else 0
        if self.method_row.get_selected() != index:
            self.method_row.set_selected(index)
        name = sync.device_name() if sync is not None else kosync.default_device_name()
        if self.device_row.get_text() != name and not self.device_row.has_focus():
            self.device_row.set_text(name)
        self._set_busy(self._busy)

    def _set_busy(self, busy):
        self._busy = busy
        available = self.sync is not None
        for widget in (self.sign_in_button, self.create_button, self.server_row,
                       self.username_row, self.password_row, self.sign_out_button,
                       self.test_button):
            widget.set_sensitive(available and not busy)

    def _show_error(self, error):
        self.error_label.set_label(str(error) if error else '')
        self.error_label.set_visible(bool(error))

    # -- the account ------------------------------------------------------------------------

    def sign_in(self, create=False):
        if self.sync is None or self._busy:
            return
        username = self.username_row.get_text().strip()
        password = self.password_row.get_text()
        if not username or not password:
            self._show_error(_('Enter a user name and a password'))
            return
        self._show_error(None)
        self._set_busy(True)
        self.sync.sign_in(self.server_row.get_text(), username, password, create,
                          self._signed_in)

    def _signed_in(self, error):
        self._set_busy(False)
        if error is not None:
            self._show_error(error)
            return
        self.password_row.set_text('')
        self._show_error(None)
        self.test_row.set_subtitle(_('Connected'))
        self.refresh()

    def sign_out(self):
        if self.sync is None:
            return
        self.server_row.set_text(self.sync.server())
        self.username_row.set_text(self.sync.username())
        self.test_row.set_subtitle('')
        self.sync.sign_out()
        self.refresh()

    def test(self):
        if self.sync is None or self._busy:
            return
        self._set_busy(True)
        self.test_spinner.set_visible(True)
        self.test_row.set_subtitle(_('Checking…'))
        self.sync.check(self._tested)

    def _tested(self, error):
        self._set_busy(False)
        self.test_spinner.set_visible(False)
        self.test_row.set_subtitle(GLib.markup_escape_text(
            str(error) if error else _('Connected')))
        if error is not None:
            self.test_row.add_css_class('error')
        else:
            self.test_row.remove_css_class('error')

    # -- options ----------------------------------------------------------------------------

    def _on_method_selected(self, row, _pspec):
        index = row.get_selected()
        if 0 <= index < len(METHODS) and \
                self.settings.get_string('sync-document-match') != METHODS[index]:
            self.settings.set_string('sync-document-match', METHODS[index])

    def _on_device_name(self, row):
        name = row.get_text().strip()
        default = kosync.default_device_name()
        self.settings.set_string('sync-device-name', '' if name in ('', default) else name)
        if not name:
            row.set_text(default)


def _host(server):
    parts = urllib.parse.urlsplit(server)
    return (parts.netloc or server) + (parts.path if parts.path not in ('', '/') else '')
