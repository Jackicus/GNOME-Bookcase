# SPDX-License-Identifier: GPL-2.0-or-later
# SPDX-FileCopyrightText: 2026 Jack Tully

"""Preferences → Reading → Look Up: when words are looked up, whether online, and the
StarDict dictionaries installed (each on or off, in the order they are asked).

    groups = preferences_groups(settings, service=None)   # [Adw.PreferencesGroup], for the
                                                          # Reading page
    groups[1].refresh()                                   # the dictionaries listed again

lookup-automatic: a selected word's definition shows in the selection popover (else only
when Look Up is chosen). lookup-online: Wiktionary and Wikipedia are asked (else only the
dictionaries on this computer). lookup-dictionary-order and lookup-dictionaries-off: the
dictionaries' .ifo paths. The dictionaries come from lookup.service() (lookup.STARDICT_DIRS);
Get Free Dictionaries opens lookup.FREE_DICTIONARIES in the browser.
"""

import os
from gettext import gettext as _
from gettext import ngettext

from gi.repository import Adw, GLib, Gtk

from .. import lookup
from ..widgets.lookup_popover import configure_service
from ..widgets.util import connect_weak


def preferences_groups(settings, service=None):
    return [_when_group(settings), DictionariesGroup(settings, service)]


def _switch(settings, key, title, subtitle):
    row = Adw.SwitchRow(title=title, subtitle=subtitle, active=settings.get_boolean(key))

    def toggled(row, _pspec):
        if settings.get_boolean(key) != row.get_active():
            settings.set_boolean(key, row.get_active())

    row.connect('notify::active', toggled)
    return row


def _when_group(settings):
    group = Adw.PreferencesGroup(title=_('Look Up'))
    group.automatic_row = _switch(
        settings, 'lookup-automatic', _('Look Up Words When Selected'),
        _('When off, words are looked up only when you choose Look Up'))
    group.add(group.automatic_row)
    group.online_row = _switch(
        settings, 'lookup-online', _('Use Online Dictionaries'),
        _('Wiktionary and Wikipedia. When off, words are looked up only in the dictionaries '
          'on this computer and never leave it'))
    group.add(group.online_row)
    return group


def _home(path):
    home = os.path.expanduser('~')
    return '~' + path[len(home):] if home and path.startswith(home + os.sep) else path


class DictionariesGroup(Adw.PreferencesGroup):
    __gtype_name__ = 'BookcaseDictionariesGroup'

    def __init__(self, settings, service=None):
        super().__init__(title=_('Dictionaries'),
                         description=_('StarDict dictionaries on this computer, asked from '
                                       'the top. Add them to {folder}.').format(
                             folder=_home(lookup.STARDICT_DIRS[0])))
        self.settings = settings
        self.service = service or lookup.service()
        self._rows = []
        self.refresh()

    def _paths(self):
        configure_service(self.service, self.settings)
        return [d.ifo_path for d in self.service.all_dictionaries()]

    def refresh(self):
        for row in self._rows:
            self.remove(row)
        self._rows = []
        configure_service(self.service, self.settings)
        dictionaries = self.service.all_dictionaries()
        off = set(self.settings.get_strv('lookup-dictionaries-off'))
        if not dictionaries:
            empty = Adw.ActionRow(title=_('No Dictionaries Installed'),
                                  subtitle=_('Without one, words are looked up online'))
            self._add(empty)
        for number, dictionary in enumerate(dictionaries):
            count = dictionary.info.get('wordcount', '')
            row = Adw.SwitchRow(title=GLib.markup_escape_text(dictionary.name),
                                active=dictionary.ifo_path not in off)
            if count.isdigit():
                row.set_subtitle(ngettext('{n} word', '{n} words', int(count)).format(
                    n=f'{int(count):,}'))
            row.ifo_path = dictionary.ifo_path
            connect_weak(row, 'notify::active', self._on_toggled)
            for icon, tooltip, step, sensitive in (
                    ('go-up-symbolic', _('Move Up'), -1, number > 0),
                    ('go-down-symbolic', _('Move Down'), 1, number < len(dictionaries) - 1)):
                button = Gtk.Button(icon_name=icon, tooltip_text=tooltip, sensitive=sensitive,
                                    valign=Gtk.Align.CENTER)
                button.add_css_class('flat')
                connect_weak(button, 'clicked', self._on_move, dictionary.ifo_path, step)
                row.add_suffix(button)
            self._add(row)
        link = Adw.ActionRow(title=_('Get Free Dictionaries'),
                             subtitle=_('FreeDict’s dictionaries, in StarDict format'),
                             activatable=True)
        link.add_suffix(Gtk.Image(icon_name='adw-external-link-symbolic',
                                  accessible_role=Gtk.AccessibleRole.PRESENTATION))
        connect_weak(link, 'activated', self._on_link)
        self.link_row = link
        self._add(link)

    def _add(self, row):
        self.add(row)
        self._rows.append(row)

    @property
    def dictionary_rows(self):
        return [row for row in self._rows if hasattr(row, 'ifo_path')]

    def _on_toggled(self, row, _pspec):
        off = [path for path in self.settings.get_strv('lookup-dictionaries-off')
               if path != row.ifo_path]
        if not row.get_active():
            off.append(row.ifo_path)
        self.settings.set_strv('lookup-dictionaries-off', off)

    def move(self, path, step):
        """Ask the dictionary at `path` a place earlier (step -1) or later (1)."""
        paths = self._paths()
        if path not in paths:
            return
        index = paths.index(path)
        other = index + step
        if not 0 <= other < len(paths):
            return
        paths[index], paths[other] = paths[other], paths[index]
        self.settings.set_strv('lookup-dictionary-order', paths)
        GLib.idle_add(self._refresh_once)

    def _refresh_once(self):
        self.refresh()
        return GLib.SOURCE_REMOVE

    def _on_move(self, _button, path, step):
        self.move(path, step)

    def _on_link(self, _row):
        root = self.get_root()
        Gtk.UriLauncher.new(lookup.FREE_DICTIONARIES).launch(
            root if isinstance(root, Gtk.Window) else None, None, None, None)
