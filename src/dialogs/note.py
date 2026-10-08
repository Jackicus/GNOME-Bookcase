# SPDX-License-Identifier: GPL-2.0-or-later
# SPDX-FileCopyrightText: 2026 Jack Tully

"""A highlight's note: the highlighted text, quoted, over a text box.

    present(app, parent, text, note, done)   # done(note) on Save; returns the dialog

Save (or Ctrl+Enter) calls done() with the note as typed, stripped (an empty note removes
it); Cancel and Escape change nothing. The parent's set_dialog_open() is told while the
dialog is open, so the reader window's keys leave the text box alone.
"""

from gettext import gettext as _

from gi.repository import Adw, Gdk, Gtk, Pango


class NoteDialog(Adw.Dialog):
    __gtype_name__ = 'BookcaseNoteDialog'

    def __init__(self, text, note, done):
        super().__init__(title=_('Note'), content_width=440, content_height=360)
        self._done = done
        header = Adw.HeaderBar(show_start_title_buttons=False, show_end_title_buttons=False)
        cancel = Gtk.Button(label=_('Cancel'))
        cancel.connect('clicked', lambda *_args: self.close())
        header.pack_start(cancel)
        save = Gtk.Button(label=_('Save'))
        save.add_css_class('suggested-action')
        save.connect('clicked', lambda *_args: self.save())
        header.pack_end(save)

        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=12, margin_start=12,
                      margin_end=12, margin_top=6, margin_bottom=12)
        if text:
            quote = Gtk.Label(label=text, xalign=0, wrap=True, lines=4, selectable=False,
                              wrap_mode=Pango.WrapMode.WORD_CHAR,
                              ellipsize=Pango.EllipsizeMode.END)
            quote.add_css_class('dimmed')
            quote.add_css_class('reader-note-quote')
            box.append(quote)
        self.text_view = Gtk.TextView(wrap_mode=Gtk.WrapMode.WORD_CHAR, top_margin=12,
                                      bottom_margin=12, left_margin=12, right_margin=12,
                                      accepts_tab=False)
        self.text_view.update_property([Gtk.AccessibleProperty.LABEL], [_('Note')])
        self.text_view.get_buffer().set_text(note or '')
        scrolled = Gtk.ScrolledWindow(child=self.text_view, vexpand=True,
                                      hscrollbar_policy=Gtk.PolicyType.NEVER)
        scrolled.add_css_class('card')
        scrolled.add_css_class('reader-note-text')
        box.append(scrolled)

        view = Adw.ToolbarView(content=box)
        view.add_top_bar(header)
        self.set_child(view)
        self.set_focus(self.text_view)

        keys = Gtk.EventControllerKey()
        keys.connect('key-pressed', self._on_key)
        self.add_controller(keys)

    def _on_key(self, _controller, keyval, _keycode, state):
        if keyval in (Gdk.KEY_Return, Gdk.KEY_KP_Enter) and state & Gdk.ModifierType.CONTROL_MASK:
            self.save()
            return True
        return False

    @property
    def note(self):
        buffer = self.text_view.get_buffer()
        return buffer.get_text(buffer.get_start_iter(), buffer.get_end_iter(), False).strip()

    def save(self):
        done, self._done = self._done, None
        self.close()
        if done is not None:
            done(self.note)


def present(app, parent, text, note, done):
    dialog = NoteDialog(text, note, done)
    if hasattr(parent, 'set_dialog_open'):
        parent.set_dialog_open(True)
        dialog.connect('closed', lambda *_args: parent.set_dialog_open(False))
    dialog.present(parent)
    return dialog
