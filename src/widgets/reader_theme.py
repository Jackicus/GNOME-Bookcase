# SPDX-License-Identifier: GPL-2.0-or-later
# SPDX-FileCopyrightText: 2026 Jack Tully

"""The reader's custom paper: its colours chosen in a small dialog, and the CSS that gives
them to the reader window's bars and its theme chip.

    present(window, settings)       # the Custom Colours dialog; returns it
    apply_css(settings)             # the custom paper's CSS, for every reader window
    hex_color(rgba)                 # a Gdk.RGBA as #rrggbb

The colours are reader-custom-theme (JSON, reading.custom_theme()); choosing one also sets
reader-theme to 'custom', so the page shows what is being chosen. The page itself takes them
from reading.build_style() (EPUBs through reader.js, PDFs through pdf_view's colour matrix,
which takes any theme dict). style.css has the fixed papers; the custom one is the user's
colours, so its CSS is made here, in one provider for the display.
"""

from gettext import gettext as _

from gi.repository import Adw, Gdk, Gtk

from .. import reading

_provider = []


def hex_color(rgba):
    return '#{:02x}{:02x}{:02x}'.format(*(max(0, min(255, round(c * 255)))
                                          for c in (rgba.red, rgba.green, rgba.blue)))


def css(colors):
    """The custom paper's CSS (as style.css's .reader-page.theme-* and chips)."""
    return (f'.reader-page.theme-custom {{ --reader-bg: {colors["bg"]}; '
            f'--reader-fg: {colors["fg"]}; }}\n'
            f'.reader-theme-chip.theme-custom {{ background: {colors["bg"]}; '
            f'color: {colors["fg"]}; }}\n'
            f'.reader-custom-preview {{ background: {colors["bg"]}; color: {colors["fg"]}; }}\n'
            f'.reader-custom-preview .link {{ color: {colors["link"]}; }}\n')


def apply_css(settings):
    display = Gdk.Display.get_default()
    if display is None:
        return
    if not _provider:
        provider = Gtk.CssProvider()
        Gtk.StyleContext.add_provider_for_display(
            display, provider, Gtk.STYLE_PROVIDER_PRIORITY_APPLICATION + 1)
        _provider.append(provider)
    colors = reading.custom_theme(settings.get_string('reader-custom-theme'))
    _provider[0].load_from_string(css(colors))


def present(window, settings):
    """The Custom Colours dialog over `window` (a reader window: its keys go to the dialog
    while it is open)."""
    dialog = Adw.Dialog(title=_('Custom Colours'), content_width=360)
    toolbar = Adw.ToolbarView()
    toolbar.add_top_bar(Adw.HeaderBar())
    box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=18, margin_start=12,
                  margin_end=12, margin_top=6, margin_bottom=18)
    preview = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=4)
    preview.add_css_class('card')
    preview.add_css_class('reader-custom-preview')
    sample = Gtk.Label(label=_('The lamps along the harbour wall were lit one by one.'),
                       wrap=True, xalign=0, margin_start=12, margin_end=12, margin_top=12)
    sample.add_css_class('body')
    link = Gtk.Label(label=_('A link in the book'), xalign=0, margin_start=12,
                     margin_bottom=12)
    link.add_css_class('link')
    preview.append(sample)
    preview.append(link)
    preview.update_property([Gtk.AccessibleProperty.LABEL], [_('Preview')])
    box.append(preview)

    group = Adw.PreferencesGroup()
    colors = reading.custom_theme(settings.get_string('reader-custom-theme'))
    buttons = {}
    for key, title in (('bg', _('Background')), ('fg', _('Text')), ('link', _('Links'))):
        rgba = Gdk.RGBA()
        rgba.parse(colors[key])
        button = Gtk.ColorDialogButton(dialog=Gtk.ColorDialog(with_alpha=False,
                                                              title=title),
                                       rgba=rgba, valign=Gtk.Align.CENTER)
        row = Adw.ActionRow(title=title, activatable_widget=button)
        row.add_suffix(button)
        group.add(row)
        buttons[key] = button
    box.append(group)
    toolbar.set_content(box)
    dialog.set_child(toolbar)
    dialog.buttons = buttons

    def changed(_button, _pspec):
        chosen = {key: hex_color(button.get_rgba()) for key, button in buttons.items()}
        text = reading.custom_theme_json(chosen['bg'], chosen['fg'], chosen['link'])
        if text != settings.get_string('reader-custom-theme'):
            settings.set_string('reader-custom-theme', text)
        if settings.get_string('reader-theme') != 'custom':
            settings.set_string('reader-theme', 'custom')
        apply_css(settings)

    for button in buttons.values():
        button.connect('notify::rgba', changed)
    set_open = getattr(window, 'set_dialog_open', None)
    if set_open is not None:
        set_open(True)
        dialog.connect('closed', lambda _dialog: set_open(False))
    apply_css(settings)
    dialog.present(window)
    return dialog
