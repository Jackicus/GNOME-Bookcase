# SPDX-License-Identifier: GPL-2.0-or-later
# SPDX-FileCopyrightText: 2026 Jack Tully

"""Convert…: another format of a book, made and added to it (book.convert).

    present(app, parent, book_id) -> ConvertDialog

The formats are converting.TARGETS, one choice each; one the book has, or that cannot be
made, is greyed with the reason. EPUB to Kobo EPUB is Bookcase's own and always there; the
others need Calibre's ebook-convert on PATH, and without it the dialog says how to get it.
Convert runs converting.convert_book() in a thread (on a worker library, the book's files
only read), with ebook-convert's progress in a bar and Cancel stopping it; closing the
dialog cancels too. The new file is added on the main library (an undo step), with a toast.
"""

import logging
import os
import threading
from gettext import gettext as _

from gi.repository import Adw, GLib, Gtk, Pango

from .. import converting, importing
from ..widgets.util import connect_weak, connect_weak_call
from . import watch_dialog

log = logging.getLogger(__name__)

NAMES = {
    'epub': (_('EPUB'), _('For most e-readers and apps')),
    'kepub': (_('Kobo EPUB'), _('An EPUB with Kobo’s page numbers and statistics')),
    'azw3': (_('AZW3'), _('For Kindles')),
    'mobi': (_('MOBI'), _('For older Kindles')),
    'pdf': (_('PDF'), _('Fixed pages, for printing')),
    'fb2': (_('FB2'), _('FictionBook, for some readers')),
}


def present(app, parent, book_id, program=None):
    dialog = ConvertDialog(app, book_id, program=program)
    watch_dialog(dialog, parent)
    dialog.present(parent)
    return dialog


class ConvertDialog(Adw.Dialog):
    __gtype_name__ = 'BookcaseConvertDialog'

    def __init__(self, app, book_id, program=None):
        super().__init__(title=_('Convert'), content_width=420)
        self.app = app
        self.book_id = book_id
        self.program = program if program is not None else converting.ebook_convert()
        self._cancel = None  # threading.Event while converting
        self._radios = {}
        book = app.library.book(book_id)
        self.book_title = book.title if book else ''
        formats = book.formats if book else ()

        toolbar = Adw.ToolbarView()
        toolbar.add_top_bar(Adw.HeaderBar())
        page = Adw.PreferencesPage()
        group = Adw.PreferencesGroup(
            title=_('Format'),
            description=_('A copy of “{title}” is converted and added to it. Its own files '
                          'stay as they are.').format(title=self.book_title))
        first = None
        for target, reason in converting.targets(formats, self.program):
            name, purpose = NAMES[target]
            row = Adw.ActionRow(title=name, subtitle=reason or purpose, use_markup=False)
            radio = Gtk.CheckButton(valign=Gtk.Align.CENTER)
            if first is None:
                first = radio
            else:
                radio.set_group(first)
            row.add_prefix(radio)
            row.set_activatable_widget(radio)
            row.set_sensitive(reason is None)
            group.add(row)
            self._radios[target] = radio
        page.add(group)
        if self.program is None:
            help_group = Adw.PreferencesGroup()
            label = Gtk.Label(
                label=_('Formats other than Kobo EPUB are made by Calibre’s ebook-convert, '
                        'which is not installed. Install Calibre (most distributions call '
                        'the package “calibre”; its command-line tools come with it), then '
                        'open Convert again.'),
                wrap=True, xalign=0, wrap_mode=Pango.WrapMode.WORD_CHAR)
            label.add_css_class('dimmed')
            help_group.add(label)
            page.add(help_group)
        self.progress = Gtk.ProgressBar(show_text=False, visible=False)
        self.status = Gtk.Label(wrap=True, xalign=0, visible=False,
                                wrap_mode=Pango.WrapMode.WORD_CHAR)
        self.status.add_css_class('caption')
        self.button = Gtk.Button(label=_('_Convert'), use_underline=True,
                                 halign=Gtk.Align.CENTER)
        self.button.add_css_class('pill')
        self.button.add_css_class('suggested-action')
        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=12, margin_start=12,
                      margin_end=12, margin_bottom=18)
        box.append(self.progress)
        box.append(self.status)
        box.append(self.button)
        toolbar.set_content(page)
        toolbar.add_bottom_bar(box)
        self.set_child(toolbar)

        possible = [target for target in self._radios if self._possible(target)]
        if possible:
            self._radios[possible[0]].set_active(True)
        connect_weak_call(self.button, 'clicked', self._on_button)
        for radio in self._radios.values():
            connect_weak(radio, 'toggled', self._on_toggled)
        connect_weak(self, 'closed', self._on_closed)
        self._update_button()

    def selected(self):
        return next((target for target, radio in self._radios.items()
                     if radio.get_active() and self._possible(target)), None)

    def _possible(self, target):
        row = self._radios[target].get_ancestor(Adw.ActionRow)
        return row is not None and row.get_sensitive()

    def _on_toggled(self, _radio):
        self._update_button()

    def _update_button(self):
        if self._cancel is not None:
            self.button.set_label(_('_Cancel'))
            self.button.remove_css_class('suggested-action')
            self.button.set_sensitive(True)
        else:
            self.button.set_label(_('_Convert'))
            self.button.add_css_class('suggested-action')
            self.button.set_sensitive(self.selected() is not None)

    def _on_button(self):
        if self._cancel is not None:
            self._cancel.set()
            self.status.set_label(_('Stopping…'))
            return
        target = self.selected()
        if target is not None:
            self.start(target)

    def _on_closed(self, _dialog):
        if self._cancel is not None:
            self._cancel.set()

    def start(self, target):
        """Convert to target in a thread; returns the thread."""
        app = self.app
        cancel = self._cancel = threading.Event()
        for radio in self._radios.values():
            radio.get_ancestor(Adw.ActionRow).set_sensitive(False)
        self.progress.set_visible(True)
        self.progress.set_fraction(0)
        self.status.set_visible(True)
        self.status.set_label(_('Converting…'))
        self._update_button()
        book_id, program, folder = self.book_id, self.program, app.library_folder()
        ref = self.weak_ref()

        def progress(fraction, text):
            GLib.idle_add(_deliver, ref, '_on_progress', fraction, text)

        def work():
            worker = app.library.open_worker()
            try:
                path = converting.convert_book(
                    worker, app.covers.with_library(worker), book_id, target, folder,
                    program=program, progress=progress, cancelled=cancel.is_set, add=False)
                result = (path, importing.partial_md5(path), os.path.getsize(path), None)
            except Exception as error:
                if not isinstance(error, converting.ConversionError):
                    log.exception('converting book %s', book_id)
                result = (None, None, None, error)
            finally:
                worker.close()
            GLib.idle_add(_finish, app, ref, book_id, target, *result)

        thread = threading.Thread(target=work, name='bookcase-convert', daemon=True)
        thread.start()
        return thread

    def _on_progress(self, fraction, text):
        if self._cancel is None or self._cancel.is_set():
            return
        self.progress.set_fraction(fraction)
        if text:
            self.status.set_label(text)

    def _failed(self, error):
        self._cancel = None
        self.progress.set_visible(False)
        self.status.set_label(str(error))
        self.status.add_css_class('error')
        book = self.app.library.book(self.book_id)
        for target, reason in converting.targets(book.formats if book else (), self.program):
            self._radios[target].get_ancestor(Adw.ActionRow).set_sensitive(reason is None)
        self._update_button()


def _deliver(ref, method, *args):
    dialog = ref()
    if dialog is not None:
        getattr(dialog, method)(*args)
    return GLib.SOURCE_REMOVE


def _finish(app, ref, book_id, target, path, file_hash, size, error):
    """On the main loop: add the new file (an undo step) or say what went wrong."""
    dialog = ref()
    if error is None and app.library is not None:
        try:
            app.library.add_file(book_id, path, hash=file_hash, size=size, format=target)
        except Exception as add_error:
            error = add_error
    if error is None:
        book = app.library.book(book_id)
        app.toast(_('Converted “{title}” to {format}').format(
            title=book.title if book else '', format=NAMES[target][0]), undo=True)
        if dialog is not None:
            dialog._cancel = None
            if dialog.get_root() is not None:  # presented
                dialog.close()
    elif isinstance(error, converting.ConversionCancelled):
        if dialog is not None:
            dialog._failed(_('Conversion cancelled'))
    elif dialog is not None:
        dialog._failed(error)
    else:
        app.report(error, _('Could not convert the book'))
    return GLib.SOURCE_REMOVE
