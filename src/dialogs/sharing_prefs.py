# SPDX-License-Identifier: GPL-2.0-or-later
# SPDX-FileCopyrightText: 2026 Jack Tully

"""The Preferences dialog's Sharing page, and the main menu's line while sharing is on.

    page = SharingPage(app)             # added by dialogs/preferences.py (name 'sharing')
    handler = attach_menu(menu, sharing)   # window.py: 'Sharing on 192.168.1.20:8095'

The page: Share Library (sharing-enabled) and how it is going; while on, the addresses to
type (each with Copy, the first with Open in Browser), the catalogue address for reading
apps and a QR code of the first address; then who can connect (sharing-scope), Require
Password (sharing-require-password), the user name (sharing-username) and the password (in
the keyring, through app.sharing.set_password), the port (sharing-port), and a word on what
a password over plain HTTP protects. Built in code, like the Sync page.
"""

import logging
from gettext import gettext as _

from gi.repository import Adw, Gdk, Gio, GLib, Gtk

from ..widgets.qr_code import QrCode
from ..widgets.util import connect_weak, connect_weak_call

log = logging.getLogger(__name__)

SCOPES = ('network', 'local')


def short_address(url):
    """'http://192.168.1.20:8095/' as '192.168.1.20:8095'."""
    return url.split('://', 1)[-1].rstrip('/')


def attach_menu(menu, sharing):
    """Keep a section at the top of `menu` (a Gio.Menu) saying where the library is shared
    while it is (app.sharing opens the Sharing page). Returns the handler id on `sharing`,
    for the window to disconnect."""
    section = Gio.Menu()
    menu.prepend_section(None, section)

    def update(*_args):
        section.remove_all()
        addresses = sharing.addresses()
        if sharing.state == 'on':
            # Translators: the main menu while the library is shared; {address} is like
            # 192.168.1.20:8095.
            label = (_('Sharing on {address}').format(address=short_address(addresses[0]))
                     if addresses else _('Sharing On'))
            section.append(label, 'app.sharing')

    update()
    return sharing.connect('changed', update)


class SharingPage(Adw.PreferencesPage):
    __gtype_name__ = 'BookcaseSharingPage'

    def __init__(self, app):
        super().__init__(name='sharing', title=_('Sharing'),
                         icon_name='network-wireless-symbolic')
        self.app = app
        self.sharing = getattr(app, 'sharing', None)
        self.settings = app.settings
        self._handlers = []
        self._address_rows = []

        intro = Adw.PreferencesGroup(
            description=_('Get books onto an e-reader or a phone over Wi-Fi: while Bookcase '
                          'is open, devices on the same network can browse your library and '
                          'download its books, in a web browser or a reading app. Nothing '
                          'can be changed or added from them.'))
        self.switch_row = Adw.SwitchRow(title=_('Share Library'))
        self.settings.bind('sharing-enabled', self.switch_row, 'active',
                           Gio.SettingsBindFlags.DEFAULT)
        intro.add(self.switch_row)
        self.add(intro)

        # While on: where to connect.
        self.connect_group = Adw.PreferencesGroup(
            title=_('Connect'),
            description=_('On the e-reader or phone, open this address in the web browser'))
        self.addresses_box = Gtk.ListBox(selection_mode=Gtk.SelectionMode.NONE)
        self.addresses_box.add_css_class('boxed-list')
        self.connect_group.add(self.addresses_box)
        self.qr_code = QrCode(halign=Gtk.Align.CENTER, margin_top=18)
        self.connect_group.add(self.qr_code)
        self.qr_caption = Gtk.Label(label=_('Or point a phone’s camera at this code'),
                                    wrap=True, justify=Gtk.Justification.CENTER,
                                    margin_top=6)
        self.qr_caption.add_css_class('caption')
        self.qr_caption.add_css_class('dimmed')
        self.connect_group.add(self.qr_caption)
        self.add(self.connect_group)

        self.apps_group = Adw.PreferencesGroup(
            title=_('Reading Apps'),
            description=_('In KOReader (Search → OPDS catalog), Readest, Thorium or any app '
                          'that reads OPDS catalogues, add this address with the user name '
                          'and password below'))
        self.catalogue_row = self._address_row(_('Catalogue Address'), '', copy_only=True)
        self.apps_group.add(self.catalogue_row)
        self.add(self.apps_group)

        # Who can connect, and how.
        access = Adw.PreferencesGroup(title=_('Access'))
        self.scope_row = Adw.ComboRow(
            title=_('Who Can Connect'),
            model=Gtk.StringList.new([_('Devices on This Network'), _('This Computer Only')]))
        connect_weak(self.scope_row, 'notify::selected', self._on_scope_selected)
        access.add(self.scope_row)
        self.password_switch = Adw.SwitchRow(title=_('Require Password'))
        self.settings.bind('sharing-require-password', self.password_switch, 'active',
                           Gio.SettingsBindFlags.DEFAULT)
        access.add(self.password_switch)
        self.username_row = Adw.EntryRow(title=_('User Name'), show_apply_button=True)
        self.username_row.set_input_hints(Gtk.InputHints.NO_SPELLCHECK)
        connect_weak(self.username_row, 'apply', self._on_username)
        access.add(self.username_row)
        self.password_row = Adw.PasswordEntryRow(title=_('Password'), show_apply_button=True)
        connect_weak(self.password_row, 'apply', self._on_password)
        access.add(self.password_row)
        self.port_row = Adw.SpinRow.new_with_range(1024, 65535, 1)
        self.port_row.set_title(_('Port'))
        self.port_row.set_numeric(True)
        self.settings.bind('sharing-port', self.port_row, 'value',
                           Gio.SettingsBindFlags.DEFAULT)
        access.add(self.port_row)
        self.add(access)

        note = Adw.PreferencesGroup()
        self.note_label = Gtk.Label(wrap=True, xalign=0)
        self.note_label.add_css_class('dimmed')
        note.add(self.note_label)
        self.add(note)

        if self.sharing is not None:
            self._watch(self.sharing, 'changed', self._on_changed)
        for key in ('sharing-scope', 'sharing-require-password', 'sharing-username'):
            self._watch(self.settings, 'changed::' + key, self._on_changed)
        self.connect('unrealize', self._on_unrealize)
        self.username_row.set_text(self.settings.get_string('sharing-username'))
        self._show_password()
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
        sharing = self.sharing
        state = sharing.state if sharing is not None else 'off'
        addresses = sharing.addresses() if sharing is not None else []
        self.switch_row.set_sensitive(sharing is not None)
        self.switch_row.remove_css_class('error')
        if state == 'on':
            subtitle = (_('On at {address}').format(address=short_address(addresses[0]))
                        if addresses else _('On, but this computer is not on a network'))
        elif state == 'starting':
            subtitle = _('Starting…')
        elif state == 'failed':
            subtitle = sharing.error
            self.switch_row.add_css_class('error')
        else:
            subtitle = _('Off')
        self.switch_row.set_subtitle(GLib.markup_escape_text(subtitle))

        on = state == 'on' and bool(addresses)
        self.connect_group.set_visible(on)
        self.apps_group.set_visible(on)
        if on:
            self._show_addresses(addresses)
            self.catalogue_row.set_subtitle(GLib.markup_escape_text(addresses[0] + 'opds'))
            self.catalogue_row.address = addresses[0] + 'opds'
            self.qr_code.set_text(addresses[0])

        scope = self.settings.get_string('sharing-scope')
        index = SCOPES.index(scope) if scope in SCOPES else 0
        if self.scope_row.get_selected() != index:
            self.scope_row.set_selected(index)
        required = self.settings.get_boolean('sharing-require-password')
        self.username_row.set_sensitive(required)
        self.password_row.set_sensitive(required)
        name = self.settings.get_string('sharing-username')
        if self.username_row.get_text() != name and not self.username_row.has_focus():
            self.username_row.set_text(name)
        if state == 'on' and sharing.password and not self.password_row.get_text():
            self.password_row.set_text(sharing.password)
        if scope == 'local':
            note = _('Only this computer can connect: other devices are refused.')
        elif required:
            note = _('Use sharing on networks you trust, such as your home’s. As with '
                     'Calibre’s content server, the address is plain HTTP: the password '
                     'keeps others out, but is not encrypted on the way.')
        else:
            note = _('Without a password, anyone on this network can download your books.')
        self.note_label.set_label(note)
        self.note_label.remove_css_class('warning')
        if scope != 'local' and not required:
            self.note_label.add_css_class('warning')

    def _show_addresses(self, addresses):
        shown = [row.address for row in self._address_rows]
        if shown == addresses:
            return
        for row in self._address_rows:
            self.addresses_box.remove(row)
        self._address_rows = []
        for index, address in enumerate(addresses):
            row = self._address_row(_('Web Address'), address, copy_only=index > 0)
            self.addresses_box.append(row)
            self._address_rows.append(row)

    def _address_row(self, title, address, copy_only=False):
        row = Adw.ActionRow(title=title, subtitle=GLib.markup_escape_text(address))
        row.add_css_class('property')
        row.set_subtitle_selectable(True)
        row.address = address
        copy = Gtk.Button(icon_name='edit-copy-symbolic', valign=Gtk.Align.CENTER,
                          tooltip_text=_('Copy Address'))
        copy.add_css_class('flat')
        connect_weak_call(copy, 'clicked', self.copy_address, row)
        row.add_suffix(copy)
        if not copy_only:
            browse = Gtk.Button(icon_name='adw-external-link-symbolic',
                                valign=Gtk.Align.CENTER, tooltip_text=_('Open in Browser'))
            browse.add_css_class('flat')
            connect_weak_call(browse, 'clicked', self.open_address, row)
            row.add_suffix(browse)
        return row

    def copy_address(self, row):
        Gdk.Display.get_default().get_clipboard().set(row.address)
        dialog = self.get_ancestor(Adw.PreferencesDialog)
        if dialog is not None:
            dialog.add_toast(Adw.Toast(title=_('Address copied')))

    def open_address(self, row):
        root = self.get_root()
        Gtk.UriLauncher.new(row.address).launch(
            root if isinstance(root, Gtk.Window) else None, None, None)

    # -- settings ---------------------------------------------------------------------------

    def _on_scope_selected(self, row, _pspec):
        index = row.get_selected()
        if 0 <= index < len(SCOPES) and \
                self.settings.get_string('sharing-scope') != SCOPES[index]:
            self.settings.set_string('sharing-scope', SCOPES[index])

    def _on_username(self, row):
        name = row.get_text().strip()
        if not name:
            row.set_text(self.settings.get_string('sharing-username'))
            return
        self.settings.set_string('sharing-username', name)

    def _show_password(self):
        if self.sharing is None:
            return
        if self.sharing.password:
            self.password_row.set_text(self.sharing.password)
            return
        ref = self.weak_ref()

        def found(password):
            page = ref()
            if page is not None and password and not page.password_row.get_text():
                page.password_row.set_text(password)

        self.sharing.lookup_password(found)

    def _on_password(self, row):
        password = row.get_text()
        if not password or self.sharing is None:
            return
        ref = self.weak_ref()

        def stored(error):
            page = ref()
            dialog = page.get_ancestor(Adw.PreferencesDialog) if page is not None else None
            if dialog is not None:
                dialog.add_toast(Adw.Toast(title=str(error) if error else
                                           _('Password changed')))

        self.sharing.set_password(password, stored)
