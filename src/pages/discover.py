# SPDX-License-Identifier: GPL-2.0-or-later
# SPDX-FileCopyrightText: 2026 Jack Tully

"""Discover: the online catalogues (OPDS) as cards, each opening its first page
(pages/catalog.py), and Add Catalogue… (dialogs/add_catalog.py).

    page = DiscoverPage()               # the root page of the 'discover' sidebar item
    page.open_catalog(catalog_id)       # pushes the catalogue's page

A card shows the catalogue's icon (once its first page has been seen: the icon it names,
kept in the cache) or its initials, its name and what it offers (its description, else
its address). Its menu edits it or removes it; removing shows a toast with Undo (the list
is the `catalogs` setting, not the library, so the toast undoes it itself). The free
catalogues Bookcase comes with can be removed like the others, and brought back from the
empty page. The cards follow the setting while the page is shown.
"""

import logging
import os
import urllib.parse
from gettext import gettext as _

from gi.repository import Adw, Gdk, Gio, GLib, Gtk, Pango

from .. import opds
from ..widgets.util import connect_weak
from . import app
from .catalog import CatalogPage, forget_client, icon_path, load_catalogs, save_catalogs

log = logging.getLogger(__name__)

CARD_WIDTH = 280  # the narrowest a card gets: one column in a narrow window


def card_subtitle(catalog):
    if catalog.description:
        return catalog.description
    parts = urllib.parse.urlsplit(catalog.url)
    return parts.netloc or catalog.url


class _CatalogCard(Gtk.FlowBoxChild):
    """A catalogue: its icon or initials, name and description, and a menu."""

    def __init__(self, catalog):
        super().__init__()
        self.catalog = catalog
        box = Gtk.Box(spacing=12, valign=Gtk.Align.FILL, width_request=CARD_WIDTH)
        box.add_css_class('card')
        box.add_css_class('catalog-card')
        self.avatar = Adw.Avatar(size=48, text=catalog.title, show_initials=True,
                                 valign=Gtk.Align.CENTER,
                                 accessible_role=Gtk.AccessibleRole.PRESENTATION)
        path = icon_path(catalog)
        if os.path.exists(path):
            try:
                texture = Gdk.Texture.new_from_filename(path)
                if texture.get_width() >= 16:
                    self.avatar.set_custom_image(texture)
            except GLib.Error as error:
                log.info('the icon of %s: %s', catalog.title, error.message)
        box.append(self.avatar)
        text = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=2, hexpand=True,
                       valign=Gtk.Align.CENTER)
        title = Gtk.Label(label=catalog.title, xalign=0, ellipsize=Pango.EllipsizeMode.END,
                          max_width_chars=1, width_chars=1)
        title.add_css_class('heading')
        text.append(title)
        subtitle = Gtk.Label(label=card_subtitle(catalog), xalign=0, wrap=True, lines=2,
                             ellipsize=Pango.EllipsizeMode.END, max_width_chars=1,
                             width_chars=1, wrap_mode=Pango.WrapMode.WORD_CHAR)
        subtitle.add_css_class('caption')
        subtitle.add_css_class('dimmed')
        text.append(subtitle)
        box.append(text)
        menu = Gio.Menu()
        menu.append(_('_Edit…'), Gio.Action.print_detailed_name(
            'discover.edit', GLib.Variant.new_string(catalog.id)))
        menu.append(_('_Remove'), Gio.Action.print_detailed_name(
            'discover.remove', GLib.Variant.new_string(catalog.id)))
        button = Gtk.MenuButton(icon_name='view-more-symbolic', menu_model=menu,
                                valign=Gtk.Align.CENTER, tooltip_text=_('Catalogue Menu'))
        button.add_css_class('flat')
        box.append(button)
        self.set_child(box)
        self.update_property([Gtk.AccessibleProperty.LABEL],
                             [f'{catalog.title}, {card_subtitle(catalog)}'])


@Gtk.Template(resource_path='/io/github/jackicus/Bookcase/discover.ui')
class DiscoverPage(Adw.NavigationPage):
    __gtype_name__ = 'BookcaseDiscoverPage'

    stack = Gtk.Template.Child()
    flow_box = Gtk.Template.Child()

    def __init__(self):
        super().__init__()
        self.settings = app().settings
        self._add_actions()
        connect_weak(self.flow_box, 'child-activated', self._on_card_activated)
        connect_weak(self.settings, 'changed::catalogs', self._on_catalogs_changed)
        self.connect('map', lambda *_args: self.refresh())
        self.refresh()

    def _add_actions(self):
        group = Gio.SimpleActionGroup()
        for name, callback, parameter in (
                ('add', self._on_add, None),
                ('restore', self._on_restore, None),
                ('edit', self._on_edit, GLib.VariantType.new('s')),
                ('remove', self._on_remove, GLib.VariantType.new('s'))):
            action = Gio.SimpleAction.new(name, parameter)
            connect_weak(action, 'activate', callback)
            group.add_action(action)
        self.insert_action_group('discover', group)

    def refresh(self):
        self.flow_box.remove_all()
        catalogs = load_catalogs()
        for catalog in catalogs:
            self.flow_box.append(_CatalogCard(catalog))
        self.stack.set_visible_child_name('catalogs' if catalogs else 'empty')

    def _on_catalogs_changed(self, *_args):
        self.refresh()

    def _on_card_activated(self, _box, card):
        self.open_catalog(card.catalog.id)

    def open_catalog(self, catalog_id):
        catalog = next((c for c in load_catalogs() if c.id == catalog_id), None)
        if catalog is None:
            return None
        page = CatalogPage(catalog)
        self.get_root().push(page)
        return page

    def _on_add(self, *_args):
        from ..dialogs import add_catalog as catalog_dialog

        ref = self.weak_ref()

        def added(catalog):
            page = ref()
            if page is not None and page.get_root() is not None:
                page.open_catalog(catalog.id)

        catalog_dialog.present_add(app(), self.get_root(), done=added)

    def _on_restore(self, *_args):
        catalogs = load_catalogs()
        known = {catalog.id for catalog in catalogs}
        catalogs.extend(c for c in opds.builtin_catalogs() if c.id not in known)
        save_catalogs(catalogs)

    def _on_edit(self, _action, value):
        from ..dialogs import add_catalog as catalog_dialog

        catalog = next((c for c in load_catalogs() if c.id == value.get_string()), None)
        if catalog is not None:
            catalog_dialog.present_edit(app(), self.get_root(), catalog)

    def _on_remove(self, _action, value):
        remove_catalog(value.get_string())


def remove_catalog(catalog_id):
    """Remove a catalogue, with a toast to undo it; its password goes when the toast does."""
    before = load_catalogs()
    catalog = next((c for c in before if c.id == catalog_id), None)
    if catalog is None:
        return None
    save_catalogs([c for c in before if c.id != catalog_id])
    forget_client(catalog)
    application = app()
    toast = application.toast(_('Removed “{title}”').format(title=catalog.title))
    if toast is None:
        return None
    undone = []

    def undo(*_args):
        undone.append(True)
        current = load_catalogs()
        if all(c.id != catalog.id for c in current):
            index = min(next((i for i, c in enumerate(before) if c.id == catalog.id), 0),
                        len(current))
            current.insert(index, catalog)
            save_catalogs(current)

    def dismissed(*_args):
        if not undone and catalog.username:
            opds.run_async(opds.keyring.clear, lambda *_args: None, catalog.url,
                           catalog.username)

    toast.set_button_label(_('Undo'))
    toast.connect('button-clicked', undo)
    toast.connect('dismissed', dismissed)
    return toast
