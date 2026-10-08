# SPDX-License-Identifier: GPL-2.0-or-later
# SPDX-FileCopyrightText: 2026 Jack Tully

"""Send to Device: copies of books, with their library metadata, onto a connected e-reader.

    dialog = present(app, parent, book_ids)   # the SendDialog (an Adw.Dialog)
    dialog.device                             # the devices.Device chosen, or None
    dialog.send()                             # what the Send button does

With no e-reader connected the dialog says to connect one by USB, and fills in when one
appears (the device monitor's added/removed signals). Otherwise it shows the device (a
choice when there are several, with its free space), for a Kobo the Kobo EPUB switch (the
`send-kepub` setting), and each book with what will be sent ("EPUB → Kobo EPUB", "PDF") or
why it cannot be. Send copies the books in a thread (devices.Device.send(), with a library
connection of its own), showing progress; Cancel stops it between chunks, and the books
sent so far stay. The dialog closes when done, with a toast ("Sent 3 books to Kobo Clara"),
and tells the monitor the device's books changed.
"""

import logging
import threading
from gettext import gettext as _
from gettext import ngettext

from gi.repository import Adw, Gio, GLib, Gtk

from ..widgets.util import connect_weak

log = logging.getLogger(__name__)


def present(app, parent, book_ids):
    dialog = SendDialog(app, book_ids)
    if parent is not None and hasattr(parent, 'set_dialog_open'):
        parent.set_dialog_open(True)
        dialog.connect('closed', lambda *_args: parent.set_dialog_open(False))
    dialog.present(parent)
    return dialog


class SendDialog(Adw.Dialog):
    __gtype_name__ = 'BookcaseSendDialog'

    def __init__(self, app, book_ids):
        super().__init__(title=_('Send to Device'), content_width=440, content_height=560)
        self.app = app
        self.book_ids = list(book_ids)
        self.devices = []
        self.device = None
        self.cancellable = None
        self.sending = False
        self._closed = False
        self._choosing = False
        self._build()
        monitor = getattr(app, 'devices', None)
        if monitor is not None:
            connect_weak(monitor, 'added', self._on_devices_changed)
            connect_weak(monitor, 'removed', self._on_devices_changed)
        self.connect('closed', self._on_closed)
        self._update_devices()

    # -- building ----------------------------------------------------------------------------

    def _build(self):
        header = Adw.HeaderBar(show_start_title_buttons=False, show_end_title_buttons=False)
        self.cancel_button = Gtk.Button(label=_('_Cancel'), use_underline=True)
        connect_weak(self.cancel_button, 'clicked', self._on_cancel)
        header.pack_start(self.cancel_button)
        self.send_button = Gtk.Button(label=_('_Send'), use_underline=True, sensitive=False)
        self.send_button.add_css_class('suggested-action')
        connect_weak(self.send_button, 'clicked', self._on_send)
        header.pack_end(self.send_button)
        self.set_default_widget(self.send_button)

        self.stack = Gtk.Stack(transition_type=Gtk.StackTransitionType.CROSSFADE)
        self.none_status = Adw.StatusPage(
            icon_name='tablet-symbolic', title=_('No E-Reader Connected'),
            description=_('Connect your e-reader with a USB cable'))
        self.stack.add_named(self.none_status, 'none')

        page = Adw.PreferencesPage()
        device_group = Adw.PreferencesGroup()
        self.device_row = Adw.ActionRow(title=_('Device'), use_markup=False)
        self.device_row.add_css_class('property')
        self.device_combo = Adw.ComboRow(title=_('Device'), use_markup=False)
        connect_weak(self.device_combo, 'notify::selected', self._on_device_selected)
        self.kepub_row = Adw.SwitchRow(
            title=_('Kobo EPUB'),
            subtitle=_('Page numbers and reading statistics on a Kobo'))
        settings = getattr(self.app, 'settings', None)
        if settings is not None:
            settings.bind('send-kepub', self.kepub_row, 'active', Gio.SettingsBindFlags.DEFAULT)
        else:
            self.kepub_row.set_active(True)
        connect_weak(self.kepub_row, 'notify::active', self._on_kepub_changed)
        for row in (self.device_row, self.device_combo, self.kepub_row):
            device_group.add(row)
        page.add(device_group)
        self.books_group = Adw.PreferencesGroup()
        self.books_list = Gtk.ListBox(selection_mode=Gtk.SelectionMode.NONE)
        self.books_list.add_css_class('boxed-list')
        self.books_group.add(self.books_list)
        page.add(self.books_group)
        self.stack.add_named(page, 'form')

        progress = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=12,
                           valign=Gtk.Align.CENTER, margin_start=24, margin_end=24)
        self.progress_title = Gtk.Label(wrap=True, justify=Gtk.Justification.CENTER)
        self.progress_title.add_css_class('title-4')
        self.progress_bar = Gtk.ProgressBar()
        self.progress_label = Gtk.Label(wrap=True, justify=Gtk.Justification.CENTER)
        self.progress_label.add_css_class('dimmed')
        for child in (self.progress_title, self.progress_bar, self.progress_label):
            progress.append(child)
        self.stack.add_named(progress, 'progress')

        view = Adw.ToolbarView(content=self.stack)
        view.add_top_bar(header)
        self.set_child(view)

    # -- devices -----------------------------------------------------------------------------

    def _on_devices_changed(self, *_args):
        if not self.sending:
            self._update_devices()

    def _update_devices(self):
        monitor = getattr(self.app, 'devices', None)
        self.devices = list(monitor.devices()) if monitor is not None else []
        previous = self.device
        if not self.devices:
            self.device = None
            self.stack.set_visible_child_name('none')
            self.send_button.set_sensitive(False)
            return
        if previous not in self.devices:
            previous = self.devices[0]
        names = Gtk.StringList.new([device.name for device in self.devices])
        self._choosing = True
        self.device_combo.set_model(names)
        self.device_combo.set_selected(self.devices.index(previous))
        self._choosing = False
        self.device_combo.set_visible(len(self.devices) > 1)
        self.device_row.set_visible(len(self.devices) == 1)
        self._set_device(previous)
        self.stack.set_visible_child_name('form')

    def _on_device_selected(self, *_args):
        if self._choosing:
            return
        index = self.device_combo.get_selected()
        if 0 <= index < len(self.devices):
            self._set_device(self.devices[index])

    def _set_device(self, device):
        self.device = device
        free, _total = device.space()
        subtitle = _('{size} free').format(size=GLib.format_size(free)) if free else ''
        self.device_combo.set_subtitle(subtitle)
        self.device_row.set_subtitle(f'{device.name} · {subtitle}' if subtitle else device.name)
        self.kepub_row.set_visible(device.kind == 'kobo')
        self._fill_books()

    # -- books -------------------------------------------------------------------------------

    def plans(self):
        """[(book_id, Book, Plan or None, formats)] for the chosen device."""
        library = self.app.library
        result = []
        kepub = self.kepub_row.get_active()
        for book_id in self.book_ids:
            book = library.book(book_id)
            if book is None:
                continue
            formats = [file.format for file in library.files(book_id) if not file.missing]
            plan = self.device.plan(formats, kepub=kepub) if self.device else None
            result.append((book_id, book, plan, formats))
        return result

    def _on_kepub_changed(self, *_args):
        self._fill_books()

    def _fill_books(self):
        if self.device is None:
            return
        self.books_list.remove_all()
        plans = self.plans()
        sendable = 0
        for _book_id, book, plan, formats in plans:
            row = Adw.ActionRow(title=book.title, use_markup=False, title_lines=2)
            if plan is not None:
                row.set_subtitle(plan.label)
                sendable += 1
            else:
                row.set_subtitle(self.device.why_not(formats))
                row.set_subtitle_lines(3)
                icon = Gtk.Image(icon_name='dialog-warning-symbolic',
                                 tooltip_text=_('Cannot be sent'))
                icon.add_css_class('warning')
                row.add_suffix(icon)
                row.add_css_class('dimmed')
            self.books_list.append(row)
        self.books_group.set_title(ngettext('{count} Book', '{count} Books', len(plans)).format(
            count=len(plans)))
        self.send_button.set_sensitive(sendable > 0)
        self.send_button.set_label(_('_Send') if sendable == len(plans) else ngettext(
            '_Send {count}', '_Send {count}', sendable).format(count=sendable))

    # -- sending -----------------------------------------------------------------------------

    def _on_send(self, *_args):
        self.send()

    def send(self):
        if self.sending or self.device is None:
            return
        work = [(book_id, book.title) for book_id, book, plan, _formats in self.plans()
                if plan is not None]
        if not work:
            return
        self.sending = True
        self.cancellable = Gio.Cancellable()
        self.send_button.set_sensitive(False)
        self.progress_title.set_text(_('Sending to {device}…').format(device=self.device.name))
        self.progress_bar.set_fraction(0)
        self.stack.set_visible_child_name('progress')
        device = self.device
        kepub = self.kepub_row.get_active()
        thread = threading.Thread(target=self._work, args=(device, work, kepub),
                                  name='bookcase-send', daemon=True)
        thread.start()

    def _work(self, device, work, kepub):
        from .. import devices

        sent, failed, cancelled = [], [], False
        library = None
        try:
            library = self.app.library.open_worker()
            for index, (book_id, title) in enumerate(work):
                if self.cancellable.is_cancelled():
                    cancelled = True
                    break
                GLib.idle_add(self._show_progress, index, len(work), title, 0.0)

                def progress(fraction, index=index, title=title):
                    GLib.idle_add(self._show_progress, index, len(work), title, fraction)

                try:
                    device.send(library, self.app.covers, book_id, kepub=kepub,
                                progress=progress, cancellable=self.cancellable)
                    sent.append(title)
                except devices.Cancelled:
                    cancelled = True
                    break
                except devices.DeviceError as error:
                    failed.append((title, str(error)))
                except OSError as error:
                    log.warning('sending %s: %s', title, error)
                    failed.append((title, error.strerror or str(error)))
        except Exception as error:
            log.exception('sending to %s', device.name)
            failed.append(('', str(error)))
        finally:
            if library is not None:
                library.close()
        GLib.idle_add(self._finished, device, len(work), sent, failed, cancelled)

    def _show_progress(self, index, total, title, fraction):
        self.progress_bar.set_fraction((index + fraction) / max(total, 1))
        # Translators: sending books: the book's title, then how far along the list.
        self.progress_label.set_text(_('“{title}” ({number} of {total})').format(
            title=title, number=index + 1, total=total))
        return GLib.SOURCE_REMOVE

    def _finished(self, device, total, sent, failed, cancelled):
        self.sending = False
        monitor = getattr(self.app, 'devices', None)
        if monitor is not None and sent:
            monitor.books_changed(device.id)
        self.app.toast(result_text(device.name, total, sent, failed, cancelled))
        if not self._closed:
            self.force_close()
        return GLib.SOURCE_REMOVE

    def _on_cancel(self, *_args):
        if self.sending:
            self.cancellable.cancel()
            self.cancel_button.set_sensitive(False)
            self.progress_title.set_text(_('Stopping…'))
        else:
            self.close()

    def _on_closed(self, *_args):
        self._closed = True
        if self.cancellable is not None:
            self.cancellable.cancel()


def result_text(device_name, total, sent, failed, cancelled):
    """The toast after sending: 'Sent 3 books to Kobo Clara', 'Sent “Title” to …', or what
    went wrong."""
    count = len(sent)
    if cancelled:
        return ngettext('Stopped after sending {count} book', 'Stopped after sending {count} '
                        'books', count).format(count=count)
    if failed:
        title, reason = failed[0]
        if not count:
            return _('Could not send “{title}”: {reason}').format(title=title, reason=reason) \
                if title else _('Could not send the books: {reason}').format(reason=reason)
        return _('Sent {count} of {total} books to {device}. “{title}”: {reason}').format(
            count=count, total=total, device=device_name, title=title, reason=reason)
    if count == 1:
        return _('Sent “{title}” to {device}').format(title=sent[0], device=device_name)
    return ngettext('Sent {count} book to {device}', 'Sent {count} books to {device}',
                    count).format(count=count, device=device_name)
