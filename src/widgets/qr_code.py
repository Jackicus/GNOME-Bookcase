# SPDX-License-Identifier: GPL-2.0-or-later
# SPDX-FileCopyrightText: 2026 Jack Tully

"""A QR code (qr.py) of a text, for a phone's camera: dark modules on light, with the
standard's quiet zone, drawn sharp at any scale.

    code = QrCode(text='http://192.168.1.20:8095/', module_size=5)
    code.set_text(text)                 # '' shows nothing

Always dark on light, whatever the style: phone cameras read a light-on-dark code badly.
Its accessible role is IMG, labelled with the text.
"""

import logging
from gettext import gettext as _

import gi

gi.require_version('Graphene', '1.0')
gi.require_version('Gsk', '4.0')

from gi.repository import Gdk, GLib, Graphene, Gsk, Gtk  # noqa: E402

from .. import qr  # noqa: E402

log = logging.getLogger(__name__)

QUIET_ZONE = 4  # modules
DARK = b'\x00\x00\x00'
LIGHT = b'\xff\xff\xff'


def texture_of(code):
    """A texture of the code with one pixel per module, quiet zone included."""
    side = code.size + 2 * QUIET_ZONE
    light_row = LIGHT * side
    rows = [light_row] * QUIET_ZONE
    margin = LIGHT * QUIET_ZONE
    for row in code.modules:
        rows.append(margin + b''.join(DARK if dark else LIGHT for dark in row) + margin)
    rows += [light_row] * QUIET_ZONE
    data = GLib.Bytes.new(b''.join(rows))
    return Gdk.MemoryTexture.new(side, side, Gdk.MemoryFormat.R8G8B8, data, side * 3)


class QrCode(Gtk.Widget):
    __gtype_name__ = 'BookcaseQrCode'

    def __init__(self, text='', module_size=5, **kwargs):
        super().__init__(accessible_role=Gtk.AccessibleRole.IMG, **kwargs)
        self.module_size = module_size
        self.text = None
        self.texture = None
        self.set_text(text)

    def set_text(self, text):
        if text == self.text:
            return
        self.text = text
        self.texture = None
        if text:
            try:
                self.texture = texture_of(qr.encode(text))
            except ValueError as error:  # too long for a code: nothing shown
                log.info('no QR code: %s', error)
        side = self.texture.get_width() * self.module_size if self.texture else 0
        self.set_size_request(side, side)
        self.set_visible(self.texture is not None)
        self.update_property([Gtk.AccessibleProperty.LABEL],
                             # Translators: what a screen reader says of the QR code.
                             [_('QR code of {text}').format(text=text or '')])
        self.queue_draw()

    def do_snapshot(self, snapshot):
        if self.texture is None:
            return
        side = min(self.get_width(), self.get_height())
        x = (self.get_width() - side) / 2
        y = (self.get_height() - side) / 2
        bounds = Graphene.Rect().init(x, y, side, side)
        snapshot.append_scaled_texture(self.texture, Gsk.ScalingFilter.NEAREST, bounds)
