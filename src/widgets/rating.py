# SPDX-License-Identifier: GPL-2.0-or-later
# SPDX-FileCopyrightText: 2026 Jack Tully

"""A rating as five stars, half stars included: the library's 0-10 (Calibre's scale; 0 is
none).

    rating = Rating(value=7)                 # three and a half stars, read-only
    rating = Rating(editable=True)           # the details page, the metadata dialog
    rating.connect('notify::value', …)       # an edit: a click on a star, or the keys
    star_icons(7)                            # ['starred', 'starred', 'starred', 'semi-starred',
                                             #  'non-starred'] (+ '-symbolic')
    rating_text(7)                           # '3.5 stars' (translated)

Editable, a click on the left half of a star gives a half star, on the right half a whole
one; clicking the value already set clears it. Left and Right (Minus and Plus) step by half
a star, 0 to 5 set whole stars, Delete or BackSpace clears.
"""

from gettext import gettext as _
from gettext import ngettext

from gi.repository import Gdk, GObject, Gtk

STAR = 'starred-symbolic'
HALF = 'semi-starred-symbolic'
EMPTY = 'non-starred-symbolic'


def star_icons(value):
    """The five icons for a 0-10 rating."""
    value = max(0, min(10, int(value or 0)))
    icons = []
    for star in range(5):
        filled = value - star * 2
        icons.append(STAR if filled >= 2 else HALF if filled == 1 else EMPTY)
    return icons


def rating_text(value):
    """The rating in words: 'No rating', '1 star', '3.5 stars'."""
    value = max(0, min(10, int(value or 0)))
    if value == 0:
        return _('No rating')
    stars = value / 2
    shown = f'{stars:g}'
    if value % 2:
        # Translators: a rating with a half star; {stars} is "3.5".
        return _('{stars} stars').format(stars=shown)
    return ngettext('{stars} star', '{stars} stars', value // 2).format(stars=shown)


class Rating(Gtk.Box):
    __gtype_name__ = 'BookcaseRating'

    def __init__(self, value=0, editable=False, pixel_size=16, **kwargs):
        super().__init__(spacing=2, valign=Gtk.Align.CENTER, **kwargs)
        self.add_css_class('rating')
        self._value = 0
        self._editable = False
        self._images = []
        for _star in range(5):
            image = Gtk.Image(icon_name=EMPTY, pixel_size=pixel_size,
                              accessible_role=Gtk.AccessibleRole.PRESENTATION)
            self._images.append(image)
            self.append(image)
        click = Gtk.GestureClick()
        click.connect('released', self._on_click)
        self.add_controller(click)
        keys = Gtk.EventControllerKey()
        keys.connect('key-pressed', self._on_key)
        self.add_controller(keys)
        self.set_editable(editable)
        self.set_value(value)

    @GObject.Property(type=int, minimum=0, maximum=10, default=0)
    def value(self):
        return self._value

    @value.setter
    def value(self, value):
        self._set(value)

    def get_value(self):
        return self._value

    def set_value(self, value):
        self.props.value = max(0, min(10, int(value or 0)))

    def _set(self, value):
        value = max(0, min(10, int(value or 0)))
        changed = value != self._value
        self._value = value
        for image, icon in zip(self._images, star_icons(value), strict=True):
            image.set_from_icon_name(icon)
        text = rating_text(value)
        self.set_tooltip_text(text if not self._editable else None)
        self.update_property([Gtk.AccessibleProperty.LABEL], [_('Rating: {rating}').format(
            rating=text)])
        if self._editable:
            self.update_property([Gtk.AccessibleProperty.VALUE_NOW,
                                  Gtk.AccessibleProperty.VALUE_TEXT], [value / 2, text])
        return changed

    def get_editable(self):
        return self._editable

    def set_editable(self, editable):
        self._editable = bool(editable)
        self.set_focusable(self._editable)
        self.set_cursor_from_name('pointer' if self._editable else None)
        if self._editable:
            self.add_css_class('editable')
            self.update_property([Gtk.AccessibleProperty.VALUE_MIN,
                                  Gtk.AccessibleProperty.VALUE_MAX], [0.0, 5.0])
        else:
            self.remove_css_class('editable')

    def _on_click(self, gesture, _n_press, x, _y):
        if not self._editable:
            return
        self.grab_focus()
        for star, image in enumerate(self._images):
            ok, bounds = image.compute_bounds(self)
            if not ok:
                continue
            left = bounds.get_x()
            right = left + bounds.get_width()
            if x < right + self.get_spacing() / 2 or star == 4:
                half = x < left + bounds.get_width() / 2
                value = star * 2 + (1 if half else 2)
                self.set_value(0 if value == self._value else value)
                gesture.set_state(Gtk.EventSequenceState.CLAIMED)
                return

    def _on_key(self, _controller, keyval, _keycode, _state):
        if not self._editable:
            return False
        if keyval in (Gdk.KEY_Right, Gdk.KEY_plus, Gdk.KEY_KP_Add, Gdk.KEY_equal):
            self.set_value(self._value + 1)
        elif keyval in (Gdk.KEY_Left, Gdk.KEY_minus, Gdk.KEY_KP_Subtract):
            self.set_value(self._value - 1)
        elif Gdk.KEY_0 <= keyval <= Gdk.KEY_5:
            self.set_value((keyval - Gdk.KEY_0) * 2)
        elif keyval in (Gdk.KEY_Delete, Gdk.KEY_BackSpace):
            self.set_value(0)
        else:
            return False
        return True
