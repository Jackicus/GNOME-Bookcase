# SPDX-License-Identifier: GPL-2.0-or-later
# SPDX-FileCopyrightText: 2026 Jack Tully

"""Adding an online catalogue, and editing one: its address, name, user name and password.

    dialog = present_add(app, parent, done=None)                  # done(catalog) once saved
    dialog = present_edit(app, parent, catalog, done=None)

Add (or Save) first reads the catalogue's first page (in a thread, a spinner meanwhile; the
password is stored there too, since the keyring may wait on an unlock prompt): a
catalogue that cannot be reached, that asks for a password it was not given, or that is not
a catalogue says so under the fields, and nothing is saved. The name, when left empty, is
the catalogue's own title. The catalogue goes into the `catalogs` setting; the password into
the keyring (opds.keyring), never the setting. Editing without typing a password keeps the
one in the keyring; a catalogue whose address or user name changes takes its password
along, and the old entry is cleared.
"""

import logging
from gettext import gettext as _

from gi.repository import Adw, Gtk

from .. import opds
from ..widgets.util import connect_weak
from . import watch_dialog

log = logging.getLogger(__name__)


@Gtk.Template(resource_path='/io/github/jackicus/Bookcase/add_catalog.ui')
class CatalogDialog(Adw.Dialog):
    __gtype_name__ = 'BookcaseCatalogDialog'

    cancel_button = Gtk.Template.Child()
    save_button = Gtk.Template.Child()
    url_row = Gtk.Template.Child()
    name_row = Gtk.Template.Child()
    username_row = Gtk.Template.Child()
    password_row = Gtk.Template.Child()
    spinner = Gtk.Template.Child()
    message_label = Gtk.Template.Child()

    def __init__(self, app, catalog=None, done=None):
        super().__init__()
        self.app = app
        self.catalog = catalog
        self.done = done
        self._task = None
        self.set_title(_('Edit Catalog') if catalog else _('Add Catalog'))
        self.save_button.set_label(_('_Save') if catalog else _('_Add'))
        if catalog is not None:
            self.url_row.set_text(catalog.url)
            self.name_row.set_text(catalog.title)
            self.username_row.set_text(catalog.username)
            if catalog.username:
                self.password_row.set_title(_('Password (Unchanged When Empty)'))
        connect_weak(self.cancel_button, 'clicked', self._on_cancel)
        connect_weak(self.save_button, 'clicked', self._on_save)
        for row in (self.url_row, self.name_row, self.username_row, self.password_row):
            connect_weak(row, 'changed', self._on_changed)
            connect_weak(row, 'entry-activated', self._on_entry_activated)
        self.connect('closed', self._on_closed)
        self._on_changed()

    def focus_sign_in(self):
        (self.username_row if not self.username_row.get_text()
         else self.password_row).grab_focus()

    def _on_changed(self, *_args):
        self.save_button.set_sensitive(bool(self.url_row.get_text().strip())
                                       and self._task is None)
        self.message_label.set_visible(False)

    def _on_entry_activated(self, *_args):
        if self.save_button.get_sensitive():
            self._on_save()

    def _on_cancel(self, *_args):
        self.close()

    def _on_closed(self, _dialog):
        if self._task is not None:
            self._task.cancel()
            self._task = None

    def _on_save(self, *_args):
        url = opds.normalise_url(self.url_row.get_text())
        username = self.username_row.get_text().strip()
        password = self.password_row.get_text()
        name = self.name_row.get_text().strip()
        old = self.catalog
        keep_password = (old is not None and not password and username
                         and username == old.username)

        def check():
            secret = password
            if keep_password:
                secret = opds.keyring.lookup(old.url, old.username) or ''
            client = opds.Client(username, secret, url)
            feed = client.feed(url, refresh=True)
            if username:
                title = name or feed.title or url
                opds.keyring.store(url, username, secret,
                                   _('Bookcase catalogue: {title}').format(title=title))
            if old is not None and old.username and (old.url, old.username) != (url, username):
                opds.keyring.clear(old.url, old.username)
            return feed, secret

        self._set_busy(True)
        ref = self.weak_ref()

        def checked(result, error):
            dialog = ref()
            if dialog is None:
                return
            dialog._task = None
            dialog._set_busy(False)
            if error is not None:
                dialog._show_error(error, username)
                return
            feed, secret = result
            dialog._save(url, username, secret, feed)

        self._task = opds.run_async(check, checked)

    def _set_busy(self, busy):
        self.spinner.set_visible(busy)
        for row in (self.url_row, self.name_row, self.username_row, self.password_row):
            row.set_sensitive(not busy)
        self.save_button.set_sensitive(not busy and bool(self.url_row.get_text().strip()))
        if busy:
            self.message_label.set_visible(False)

    def _show_error(self, error, username):
        if isinstance(error, opds.AuthError):
            text = (_('The user name or password is not right') if username else
                    _('This catalogue asks for a user name and password'))
            self.focus_sign_in()
        elif isinstance(error, opds.NotOpdsError):
            text = _('Nothing at this address is a book catalogue (OPDS)')
        else:
            text = str(error)
        self.message_label.set_text(text)
        self.message_label.set_visible(True)

    def _save(self, url, username, password, feed):
        from ..pages.catalog import forget_client, load_catalogs, save_catalogs

        title = self.name_row.get_text().strip() or feed.title or url
        old = self.catalog
        catalogs = load_catalogs()
        if old is not None:
            catalog = opds.Catalog(old.id, title, url, username,
                                   old.description if url == old.url else '')
            catalogs = [catalog if c.id == old.id else c for c in catalogs]
            if not any(c.id == old.id for c in catalogs):
                catalogs.append(catalog)
            forget_client(old)
        else:
            catalog = opds.Catalog(opds.new_id(), title, url, username)
            catalogs.append(catalog)
        save_catalogs(catalogs)
        if old is None:
            self.app.toast(_('Added “{title}”').format(title=title))
        self.close()
        if self.done is not None:
            self.done(catalog)


def _present(dialog, parent):
    watch_dialog(dialog, parent)
    dialog.present(parent)
    return dialog


def present_add(app, parent, done=None):
    dialog = _present(CatalogDialog(app, done=done), parent)
    dialog.url_row.grab_focus()
    return dialog


def present_edit(app, parent, catalog, done=None):
    dialog = _present(CatalogDialog(app, catalog, done=done), parent)
    dialog.focus_sign_in()
    return dialog
