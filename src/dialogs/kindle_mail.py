# SPDX-License-Identifier: GPL-2.0-or-later
# SPDX-FileCopyrightText: 2026 Jack Tully

"""Send to Kindle's setup: the Kindle's address, the mail account books go from, and its
password (in the keyring).

    present_setup(app, parent, on_saved=None)   # the KindleSetupDialog (an Adw.Dialog)
    preferences_group(app)              # the "Send to Kindle" group for Preferences
    account(app)                        # mail.Account from app.settings
    keyring(app)                        # app.mail_keyring (tests, screenshots) or the real one
    explain_once(app, parent)           # Amazon's approved sender list, said once
    run_in_thread(func, callback)       # callback(result, error) on the main loop

The dialog: the Kindle's address (…@kindle.com); the provider (mail.PRESETS: choosing one
fills in the server, port and security, and its note says what password it wants), the
sending address and its password; Server Settings (server, port, security, user name),
open for Other; a row that opens Amazon's page on the Approved Personal Document E-mail
List; Send a Test (a mail to the sending address with what is typed, unsaved); Remove
Account. Save stores the settings (mail-*, kindle-address) and the password (in a thread),
then the first time explains the approved list (an alert with a link) and calls on_saved().
"""

import logging
import threading
from gettext import gettext as _

from gi.repository import Adw, GLib, Gtk

from .. import mail, passwords
from ..widgets.util import connect_weak, connect_weak_call
from . import watch_dialog

log = logging.getLogger(__name__)

PRESET_KEYS = tuple(mail.PRESETS)
SECURITY_LABELS = (('starttls', 'STARTTLS'), ('ssl', 'SSL/TLS'), ('none', None))


def _security_names():
    return [label or _('None') for _key, label in SECURITY_LABELS]


def account(app):
    return mail.Account.from_settings(getattr(app, 'settings', None))


def keyring(app):
    found = getattr(app, 'mail_keyring', None)
    if found is None:
        found = passwords.Keyring(passwords.MAIL_SCHEMA)
        try:
            app.mail_keyring = found
        except AttributeError:
            pass
    return found


def run_in_thread(func, callback):
    """func() in a daemon thread; callback(result, error) on the main loop."""
    def run():
        try:
            result, error = func(), None
        except Exception as failure:  # handed to the callback, which says it
            if not isinstance(failure, (mail.MailError, passwords.KeyringError)):
                log.exception('send to kindle')
            result, error = None, failure
        GLib.idle_add(lambda: callback(result, error) and False)

    threading.Thread(target=run, name='bookcase-mail', daemon=True).start()


def open_uri(parent, uri):
    root = parent.get_root() if parent is not None and hasattr(parent, 'get_root') else None
    Gtk.UriLauncher(uri=uri).launch(root, None, None, None)


def present_setup(app, parent, on_saved=None):
    dialog = KindleSetupDialog(app, on_saved=on_saved)
    watch_dialog(dialog, parent)
    dialog.present(parent)
    return dialog


def explain_once(app, parent, sender=''):
    """The first time Send to Kindle is saved: Amazon's approved sender list, with a link.
    Returns the alert, or None when it was explained before."""
    settings = getattr(app, 'settings', None)
    if settings is None or settings.get_boolean('kindle-mail-explained'):
        return None
    settings.set_boolean('kindle-mail-explained', True)
    dialog = Adw.AlertDialog(
        heading=_('One More Step at Amazon'),
        body=_('Amazon delivers books only from addresses on your account’s Approved '
               'Personal Document E-mail List, and drops other mail without a word. Add '
               '{sender} to it under Manage Your Content and Devices → Preferences → '
               'Personal Document Settings.').format(sender=sender or _('your address')))
    dialog.add_response('later', _('_Later'))
    dialog.add_response('open', _('_Open Amazon’s Help'))
    dialog.set_response_appearance('open', Adw.ResponseAppearance.SUGGESTED)
    dialog.set_default_response('open')
    dialog.set_close_response('later')

    def on_response(_dialog, response):
        if response == 'open':
            open_uri(parent, mail.APPROVED_LIST_URL)

    dialog.connect('response', on_response)
    dialog.present(parent)
    return dialog


class KindleSetupDialog(Adw.Dialog):
    __gtype_name__ = 'BookcaseKindleSetupDialog'

    def __init__(self, app, on_saved=None):
        super().__init__(title=_('Send to Kindle'), content_width=460, content_height=680)
        self.app = app
        self.on_saved = on_saved
        self.saved_account = account(app)
        self._filling = False
        self._password_typed = False
        self._busy = False
        self._closed = False
        self._build()
        self._fill(self.saved_account)
        self.connect('closed', self._on_closed)
        if self.saved_account.configured:
            self._load_password()

    # -- building ----------------------------------------------------------------------------

    def _build(self):
        header = Adw.HeaderBar(show_start_title_buttons=False, show_end_title_buttons=False)
        cancel = Gtk.Button(label=_('_Cancel'), use_underline=True)
        connect_weak_call(cancel, 'clicked', self.close)
        header.pack_start(cancel)
        self.save_button = Gtk.Button(label=_('_Save'), use_underline=True, sensitive=False)
        self.save_button.add_css_class('suggested-action')
        connect_weak_call(self.save_button, 'clicked', self.save)
        header.pack_end(self.save_button)
        self.set_default_widget(self.save_button)

        page = Adw.PreferencesPage()
        kindle = Adw.PreferencesGroup(
            title=_('Your Kindle'),
            description=_('Its own e-mail address is in your Amazon account under '
                          'Preferences → Personal Document Settings, and on the Kindle in '
                          'Settings → Your Account.'))
        self.kindle_row = Adw.EntryRow(title=_('Kindle E-mail Address'),
                                       input_purpose=Gtk.InputPurpose.EMAIL)
        kindle.add(self.kindle_row)
        page.add(kindle)

        sender = Adw.PreferencesGroup(title=_('Sent From'))
        self.sender_group = sender
        presets = [mail.PRESETS[key].name for key in PRESET_KEYS]
        self.provider_row = Adw.ComboRow(title=_('Provider'),
                                         model=Gtk.StringList.new(presets))
        self.sender_row = Adw.EntryRow(title=_('Your E-mail Address'),
                                       input_purpose=Gtk.InputPurpose.EMAIL)
        self.password_row = Adw.PasswordEntryRow(title=_('Password'))
        for row in (self.provider_row, self.sender_row, self.password_row):
            sender.add(row)
        page.add(sender)

        server = Adw.PreferencesGroup()
        self.server_expander = Adw.ExpanderRow(title=_('Server Settings'))
        self.server_row = Adw.EntryRow(title=_('Outgoing Mail Server'),
                                       input_purpose=Gtk.InputPurpose.URL)
        self.port_row = Adw.SpinRow.new_with_range(1, 65535, 1)
        self.port_row.set_title(_('Port'))
        self.port_row.set_numeric(True)
        self.security_row = Adw.ComboRow(title=_('Security'),
                                         model=Gtk.StringList.new(_security_names()))
        self.username_row = Adw.EntryRow(title=_('User Name, When Not the Address'))
        for row in (self.server_row, self.port_row, self.security_row, self.username_row):
            self.server_expander.add_row(row)
        server.add(self.server_expander)
        page.add(server)

        approve = Adw.PreferencesGroup()
        self.approve_row = Adw.ActionRow(
            title=_('Approve Your Address at Amazon'), activatable=True, subtitle_lines=4,
            subtitle=_('Amazon delivers only mail from addresses on your Approved '
                       'Personal Document E-mail List'))
        self.approve_row.add_prefix(Gtk.Image(icon_name='dialog-information-symbolic',
                                              accessible_role=Gtk.AccessibleRole.PRESENTATION))
        self.approve_row.add_suffix(Gtk.Image(icon_name='adw-external-link-symbolic',
                                              accessible_role=Gtk.AccessibleRole.PRESENTATION))
        self.approve_row.set_tooltip_text(_('Open in the browser'))
        connect_weak_call(self.approve_row, 'activated', self._open_approved_list)
        approve.add(self.approve_row)
        page.add(approve)

        buttons = Adw.PreferencesGroup()
        self.test_row = Adw.ButtonRow(title=_('Send a _Test'), use_underline=True,
                                      start_icon_name='mail-send-symbolic')
        connect_weak_call(self.test_row, 'activated', self.send_test)
        self.remove_row = Adw.ButtonRow(title=_('_Remove Account'), use_underline=True)
        self.remove_row.add_css_class('destructive-action')
        connect_weak_call(self.remove_row, 'activated', self.remove)
        buttons.add(self.test_row)
        buttons.add(self.remove_row)
        page.add(buttons)

        self.toasts = Adw.ToastOverlay(child=page)
        view = Adw.ToolbarView(content=self.toasts)
        view.add_top_bar(header)
        self.set_child(view)

        for row in (self.kindle_row, self.sender_row, self.server_row, self.username_row):
            connect_weak(row, 'changed', self._on_changed)
        connect_weak(self.password_row, 'changed', self._on_password_changed)
        connect_weak(self.port_row, 'notify::value', self._on_changed)
        connect_weak(self.security_row, 'notify::selected', self._on_changed)
        connect_weak(self.provider_row, 'notify::selected', self._on_provider_selected)
        connect_weak(self.sender_row, 'changed', self._on_sender_changed)

    # -- the values --------------------------------------------------------------------------

    def _fill(self, values):
        self._filling = True
        preset = values.preset if values.preset in PRESET_KEYS else 'custom'
        if not values.server and not values.sender:
            preset = 'gmail'
            values = mail.Account(server=mail.PRESETS['gmail'].server,
                                  port=mail.PRESETS['gmail'].port, preset='gmail')
        self.kindle_row.set_text(values.kindle)
        self.sender_row.set_text(values.sender)
        self.provider_row.set_selected(PRESET_KEYS.index(preset))
        self.server_row.set_text(values.server)
        self.port_row.set_value(values.port)
        keys = [key for key, _label in SECURITY_LABELS]
        self.security_row.set_selected(keys.index(values.security)
                                       if values.security in keys else 0)
        self.username_row.set_text(values.username)
        self.server_expander.set_expanded(preset == 'custom')
        self._filling = False
        self._update()

    def values(self):
        """The mail.Account the rows describe now."""
        return mail.Account(
            server=self.server_row.get_text().strip(),
            port=int(self.port_row.get_value()),
            security=SECURITY_LABELS[self.security_row.get_selected()][0],
            username=self.username_row.get_text().strip(),
            sender=self.sender_row.get_text().strip(),
            kindle=self.kindle_row.get_text().strip(),
            preset=PRESET_KEYS[self.provider_row.get_selected()])

    def password(self):
        return self.password_row.get_text()

    def _on_provider_selected(self, *_args):
        if self._filling:
            return
        key = PRESET_KEYS[self.provider_row.get_selected()]
        preset = mail.PRESETS[key]
        self._filling = True
        if key != 'custom':
            self.server_row.set_text(preset.server)
            self.port_row.set_value(preset.port)
            self.security_row.set_selected([k for k, _l in SECURITY_LABELS].index(
                preset.security))
        self.server_expander.set_expanded(key == 'custom')
        self._filling = False
        self._update()

    def _on_sender_changed(self, row):
        if self._filling or self.saved_account.configured:
            return
        guess = mail.guess_preset(row.get_text())
        current = PRESET_KEYS[self.provider_row.get_selected()]
        if guess != 'custom' and guess != current:
            self.provider_row.set_selected(PRESET_KEYS.index(guess))

    def _on_password_changed(self, *_args):
        if not self._filling:
            self._password_typed = True
        self._update()

    def _on_changed(self, *_args):
        if not self._filling:
            self._update()

    def _update(self):
        values = self.values()
        key = values.preset
        self.sender_group.set_description(mail.PRESETS[key].note)
        self.password_row.set_title(_('App Password') if key in ('gmail', 'icloud',
                                                                  'fastmail')
                                    else _('Password'))
        security = SECURITY_LABELS[self.security_row.get_selected()][1] or _('no encryption')
        self.server_expander.set_subtitle(
            f'{values.server} · {values.port} · {security}' if values.server else '')
        for row, ok in ((self.kindle_row, not values.kindle or mail.valid_address(
                            values.kindle)),
                        (self.sender_row, not values.sender or mail.valid_address(
                            values.sender))):
            if ok:
                row.remove_css_class('error')
            else:
                row.add_css_class('error')
        if values.sender:
            self.approve_row.set_subtitle(
                _('Amazon delivers only mail from addresses on your Approved Personal '
                  'Document E-mail List. Add {sender} to it.').format(sender=values.sender))
        self.save_button.set_sensitive(values.configured and not self._busy)
        self.test_row.set_sensitive(values.configured and not self._busy)
        self.remove_row.set_visible(self.saved_account.configured)

    def _load_password(self):
        store = keyring(self.app)
        key = self.saved_account.key
        ref = self.weak_ref()

        def done(password, _error):
            dialog = ref()
            if dialog is not None and password and not dialog._password_typed:
                dialog._filling = True
                dialog.password_row.set_text(password)
                dialog._filling = False

        run_in_thread(lambda: store.lookup(key), done)

    # -- doing -------------------------------------------------------------------------------

    def toast(self, text):
        self.toasts.add_toast(Adw.Toast(title=GLib.markup_escape_text(text), timeout=6))

    def _set_busy(self, busy):
        self._busy = busy
        self._update()

    def send_test(self):
        values, password = self.values(), self.password()
        if not values.configured or self._busy:
            return
        self._set_busy(True)
        self.test_row.set_title(_('Sending…'))
        smtp = getattr(self.app, 'mail_smtp', None)  # tests and screenshots: a fake server
        ref = self.weak_ref()

        def done(_result, error):
            dialog = ref()
            if dialog is None or dialog._closed:
                return
            dialog.test_row.set_title(_('Send a _Test'))
            dialog._set_busy(False)
            if error is None:
                dialog.toast(_('Test sent to {address}. Check your inbox.').format(
                    address=values.sender))
            else:
                dialog.toast(str(error) if isinstance(error, mail.MailError)
                             else _('Could not send the test: {error}').format(error=error))

        run_in_thread(lambda: mail.send_test(values, password, smtp=smtp), done)

    def _open_approved_list(self):
        open_uri(self, mail.APPROVED_LIST_URL)

    def save(self):
        values, password = self.values(), self.password()
        if not values.configured or self._busy:
            return
        store = keyring(self.app)
        old_key = self.saved_account.key if self.saved_account.configured else None
        self._set_busy(True)
        ref = self.weak_ref()

        def work():
            if old_key is not None and old_key != values.key:
                store.clear(old_key)
            if password:
                store.store(values.key, _('Bookcase: Send to Kindle ({address})').format(
                    address=values.login), password)
            else:
                store.clear(values.key)

        def done(_result, error):
            settings = self.app.settings
            values.save(settings)
            dialog = ref()
            if dialog is not None:
                dialog.saved_account = values
                dialog._set_busy(False)
            if error is not None:
                if dialog is not None:
                    dialog.toast(str(error))
                return
            if dialog is not None and not dialog._closed:
                dialog.force_close()
            parent = self.app.window() if hasattr(self.app, 'window') else None
            if parent is not None:
                explain_once(self.app, parent, values.sender)
            if self.on_saved is not None:
                self.on_saved(values)

        run_in_thread(work, done)

    def remove(self):
        saved = self.saved_account
        if not saved.configured:
            return
        store = keyring(self.app)
        run_in_thread(lambda: store.clear(saved.key), lambda *_args: None)
        mail.Account.forget(self.app.settings)
        self.app.toast(_('Send to Kindle was removed'))
        if self.on_saved is not None:
            self.on_saved(mail.Account())
        self.force_close()

    def _on_closed(self, *_args):
        self._closed = True


def preferences_group(app):
    """Preferences' "Send to Kindle" group: a row with the Kindle's address (or Set Up…)
    opening the setup."""
    group = Adw.PreferencesGroup(
        title=_('Send to Kindle'),
        description=_('Mail EPUB, PDF and TXT books to a Kindle through your e-mail account'))
    row = Adw.ActionRow(activatable=True, use_markup=False)
    row.add_suffix(Gtk.Image(icon_name='go-next-symbolic',
                             accessible_role=Gtk.AccessibleRole.PRESENTATION))

    def show(*_args):
        found = account(app)
        if found.configured:
            row.set_title(found.kindle)
            row.set_subtitle(_('From {sender}').format(sender=found.sender))
        else:
            row.set_title(_('Set Up Send to Kindle…'))
            row.set_subtitle(_('Not set up'))

    row.connect('activated', lambda *_args: present_setup(
        app, row.get_ancestor(Adw.Dialog) or row, on_saved=show))
    show()
    group.add(row)
    group.row = row
    return group
