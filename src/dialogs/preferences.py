# SPDX-License-Identifier: GPL-2.0-or-later
# SPDX-FileCopyrightText: 2026 Jack Tully

"""The Preferences dialog: app.preferences (Ctrl+,).

    present(app, parent) -> PreferencesDialog
    library_folder(settings)            # the library-folder setting, ~/Books when empty

Library: the library folder (library-folder; Change… picks another, for books added from
now on), the watched folders and the linked Calibre libraries (library.folders(): each row
reads its folder again or removes it, which leaves its books in the library, with Undo;
the + buttons pick a folder and read it through add_books.present_scan or
present_link_calibre). Reading: the reader's defaults, the same keys its Display popover
sets (reader-theme, reader-font, reader-custom-font, reader-font-size, reader-line-height,
reader-margin, reader-max-width, reader-justify, reader-hyphenate, reader-publisher-styles,
reader-scrolled, reader-two-pages, reader-animate). Devices: send-kepub. Online: the Google
Books API key (google-books-key).

Rows are bound with Gio.Settings.bind (numbers included: GSettings maps a spin row's double
to an integer key), the two enum combo rows by hand; the bindings and the library handler
are let go when the dialog closes.
"""

import logging
import os
from gettext import gettext as _

from gi.repository import Adw, Gio, GLib, Gtk, Pango

from . import add_books, watch_dialog

log = logging.getLogger(__name__)

SWITCHES = (
    ('scrolled_row', 'reader-scrolled'),
    ('two_pages_row', 'reader-two-pages'),
    ('animate_row', 'reader-animate'),
    ('publisher_styles_row', 'reader-publisher-styles'),
    ('justify_row', 'reader-justify'),
    ('hyphenate_row', 'reader-hyphenate'),
    ('kepub_row', 'send-kepub'),
)
SPINS = (
    ('font_size_row', 'reader-font-size'),
    ('line_height_row', 'reader-line-height'),
    ('margin_row', 'reader-margin'),
    ('max_width_row', 'reader-max-width'),
)
# The enum combo rows: (template child, key, the nicks in the row's order).
COMBOS = (
    ('theme_row', 'reader-theme', ('auto', 'light', 'sepia', 'dark', 'black')),
    ('font_row', 'reader-font', ('publisher', 'serif', 'sans', 'custom')),
)


def library_folder(settings):
    path = settings.get_string('library-folder').strip() if settings else ''
    return os.path.expanduser(path) if path else os.path.join(os.path.expanduser('~'),
                                                               'Books')


def pretty_path(path):
    """The path with the home folder as ~."""
    path, home = str(path), os.path.expanduser('~')
    if home and (path == home or path.startswith(home + os.sep)):
        return '~' + path[len(home):]
    return path


def present(app, parent):
    dialog = PreferencesDialog(app)
    watch_dialog(dialog, parent)
    dialog.present(parent)
    return dialog


@Gtk.Template(resource_path='/io/github/jackicus/Bookcase/preferences.ui')
class PreferencesDialog(Adw.PreferencesDialog):
    __gtype_name__ = 'BookcasePreferencesDialog'

    library_folder_row = Gtk.Template.Child()
    library_folder_button = Gtk.Template.Child()
    watched_group = Gtk.Template.Child()
    add_watched_button = Gtk.Template.Child()
    watched_list = Gtk.Template.Child()
    calibre_group = Gtk.Template.Child()
    add_calibre_button = Gtk.Template.Child()
    calibre_list = Gtk.Template.Child()
    theme_row = Gtk.Template.Child()
    scrolled_row = Gtk.Template.Child()
    two_pages_row = Gtk.Template.Child()
    animate_row = Gtk.Template.Child()
    publisher_styles_row = Gtk.Template.Child()
    font_row = Gtk.Template.Child()
    custom_font_row = Gtk.Template.Child()
    custom_font_button = Gtk.Template.Child()
    font_size_row = Gtk.Template.Child()
    line_height_row = Gtk.Template.Child()
    margin_row = Gtk.Template.Child()
    max_width_row = Gtk.Template.Child()
    justify_row = Gtk.Template.Child()
    hyphenate_row = Gtk.Template.Child()
    kepub_row = Gtk.Template.Child()
    google_key_row = Gtk.Template.Child()

    def __init__(self, app):
        super().__init__()
        self.app = app
        self.settings = settings = app.settings
        self._handlers = []  # (object, handler id), disconnected on close
        self._quiet = False
        for child, key in SWITCHES:
            settings.bind(key, getattr(self, child), 'active', Gio.SettingsBindFlags.DEFAULT)
        for child, key in SPINS:
            settings.bind(key, getattr(self, child), 'value', Gio.SettingsBindFlags.DEFAULT)
        settings.bind('google-books-key', self.google_key_row, 'text',
                      Gio.SettingsBindFlags.DEFAULT)
        for child, key, nicks in COMBOS:
            row = getattr(self, child)
            self._set_combo(row, key, nicks)
            row.connect('notify::selected', self._on_combo_selected, key, nicks)
            self._watch(settings, 'changed::' + key,
                        lambda *_args, row=row, key=key, nicks=nicks:
                        self._set_combo(row, key, nicks))
        self._set_font_button()
        self.custom_font_button.connect('notify::font-desc', self._on_font_chosen)
        self._watch(settings, 'changed::reader-custom-font', lambda *_args:
                    self._set_font_button())
        self._watch(settings, 'changed::library-folder', lambda *_args:
                    self._show_library_folder())
        self.library_folder_button.connect('clicked', lambda *_args:
                                           self.choose_library_folder())
        self.add_watched_button.connect('clicked', lambda *_args: self.choose_watched())
        self.add_calibre_button.connect('clicked', lambda *_args: self.choose_calibre())
        library = getattr(app, 'library', None)
        if library is not None:
            self._watch(library, 'changed', self._on_library_changed)
        self.connect('closed', self._on_closed)
        self._show_library_folder()
        self.show_folders()

    def _watch(self, obj, signal, handler):
        self._handlers.append((obj, obj.connect(signal, handler)))

    def _on_closed(self, _dialog):
        for child, _key in SWITCHES:
            Gio.Settings.unbind(getattr(self, child), 'active')
        for child, _key in SPINS:
            Gio.Settings.unbind(getattr(self, child), 'value')
        Gio.Settings.unbind(self.google_key_row, 'text')
        for obj, handler in self._handlers:
            if obj.handler_is_connected(handler):
                obj.disconnect(handler)
        self._handlers = []

    # -- enum rows ---------------------------------------------------------------------------

    def _set_combo(self, row, key, nicks):
        nick = self.settings.get_string(key)
        self._quiet = True
        try:
            row.set_selected(nicks.index(nick) if nick in nicks else 0)
        finally:
            self._quiet = False
        if key == 'reader-font':
            self.custom_font_row.set_visible(nick == 'custom')

    def _on_combo_selected(self, row, _pspec, key, nicks):
        if self._quiet:
            return
        index = row.get_selected()
        if 0 <= index < len(nicks) and self.settings.get_string(key) != nicks[index]:
            self.settings.set_string(key, nicks[index])

    def _set_font_button(self):
        family = self.settings.get_string('reader-custom-font')
        self._quiet = True
        try:
            if family:
                self.custom_font_button.set_font_desc(Pango.FontDescription.from_string(family))
        finally:
            self._quiet = False

    def _on_font_chosen(self, button, _pspec):
        if self._quiet:
            return
        description = button.get_font_desc()
        family = description.get_family() if description else ''
        if family and family != self.settings.get_string('reader-custom-font'):
            self.settings.set_string('reader-custom-font', family)

    # -- folders -----------------------------------------------------------------------------

    def _show_library_folder(self):
        self.library_folder_row.set_subtitle(pretty_path(library_folder(self.settings)))

    def _choose_folder(self, title, callback, initial=None):
        chooser = Gtk.FileDialog(title=title, modal=True)
        if initial and os.path.isdir(initial):
            chooser.set_initial_folder(Gio.File.new_for_path(initial))

        def chosen(_chooser, result):
            try:
                folder = chooser.select_folder_finish(result)
            except GLib.Error:
                return  # dismissed
            if folder is not None and folder.get_path():
                callback(folder.get_path())

        root = self.get_root()
        chooser.select_folder(root if isinstance(root, Gtk.Window) else None, None, chosen)

    def choose_library_folder(self):
        self._choose_folder(_('Choose Library Folder'), self.set_library_folder,
                            library_folder(self.settings))

    def set_library_folder(self, path):
        default = os.path.join(os.path.expanduser('~'), 'Books')
        self.settings.set_string('library-folder', '' if path == default else path)
        importer = getattr(self.app, 'importer', None)
        if importer is not None and hasattr(importer, 'library_folder'):
            importer.library_folder = path

    def choose_watched(self):
        self._choose_folder(_('Choose Folder to Watch'), self.add_watched)

    def add_watched(self, path):
        return add_books.present_scan(self.app, self._parent_window(), path)

    def choose_calibre(self):
        self._choose_folder(_('Choose Calibre Library'), self.add_calibre)

    def add_calibre(self, path):
        if not os.path.exists(os.path.join(path, 'metadata.db')):
            self.add_toast(Adw.Toast(title=_('This folder is not a Calibre library: it has '
                                             'no metadata.db')))
            return None
        return add_books.present_link_calibre(self.app, self._parent_window(), path)

    def _parent_window(self):
        root = self.get_root()
        return root if isinstance(root, Gtk.Window) else None

    def _on_library_changed(self, _library, kind, *_rest):
        if kind == 'folders':
            self.show_folders()

    def show_folders(self):
        library = getattr(self.app, 'library', None)
        folders = library.folders() if library is not None else []
        for listbox, kind in ((self.watched_list, 'watched'), (self.calibre_list, 'calibre')):
            listbox.remove_all()
            for folder in folders:
                if folder.kind == kind:
                    listbox.append(self._folder_row(folder))

    def _folder_row(self, folder):
        row = Adw.ActionRow(title=GLib.markup_escape_text(os.path.basename(folder.path)
                                                          or folder.path),
                            subtitle=GLib.markup_escape_text(pretty_path(folder.path)))
        row.set_subtitle_lines(1)
        row.set_tooltip_text(folder.path)
        if not os.path.isdir(folder.path):
            row.set_subtitle(GLib.markup_escape_text(
                _('Not found: {path}').format(path=pretty_path(folder.path))))
            row.add_css_class('warning')
        refresh = Gtk.Button(icon_name='view-refresh-symbolic', valign=Gtk.Align.CENTER,
                             tooltip_text=_('Read Again'))
        refresh.add_css_class('flat')
        refresh.connect('clicked', self._on_refresh, folder)
        row.add_suffix(refresh)
        remove = Gtk.Button(icon_name='user-trash-symbolic', valign=Gtk.Align.CENTER,
                            tooltip_text=_('Remove'))
        remove.add_css_class('flat')
        remove.connect('clicked', self._on_remove, folder)
        row.add_suffix(remove)
        return row

    def _on_refresh(self, _button, folder):
        if folder.kind == 'calibre':
            add_books.present_link_calibre(self.app, self._parent_window(), folder.path)
        else:
            add_books.present_scan(self.app, self._parent_window(), folder.path)

    def _on_remove(self, _button, folder):
        try:
            self.app.library.remove_folder(folder.id)
        except Exception as error:
            self.app.report(error, _('Could not remove the folder'))
            return
        self.show_folders()
        name = os.path.basename(folder.path) or folder.path
        toast = Adw.Toast(title=_('“{}” removed. Its books stay in the library.').format(name),
                          button_label=_('Undo'), use_markup=False)
        toast.connect('button-clicked', lambda *_args: self._undo())
        self.add_toast(toast)

    def _undo(self):
        self.app.undo()
        self.show_folders()
