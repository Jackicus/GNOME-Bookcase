# SPDX-License-Identifier: GPL-2.0-or-later
# SPDX-FileCopyrightText: 2026 Jack Tully

"""Adding books with progress and a summary; also scanning a watched folder and linking a
Calibre library, which show the same.

    present(app, parent, paths, copy=True)       # files or folders (paths or Gio.Files)
    present_scan(app, parent, folder_path)       # a watched folder, read in place
    present_link_calibre(app, parent, path)      # a Calibre library, read in place

Each starts the work through app.importer's *_async API at once and returns the dialog,
which is presented only if the work takes longer than a moment (DELAY): a quick add of one
book just toasts. The dialog shows "n of m", a progress bar and the file being read, with
Cancel (closing the dialog cancels too; what was added stays). When the work is done:
one book added and nothing else to say closes it with a toast "Added “Title”" and Show;
otherwise a summary: the books added (Show), formats added to books already there,
books already in the library (each with Show), files that could not be added (each with
its reason), and for a scan or a Calibre library the books updated, moved and missing.
"""

import logging
import os
from gettext import gettext as _
from gettext import ngettext

from gi.repository import Adw, Gio, GLib, Gtk

from ..widgets.util import connect_weak_call
from . import watch_dialog

log = logging.getLogger(__name__)

DELAY = 400  # ms before the dialog shows itself
LISTED = 50  # rows listed per section of the summary


def _path(item):
    if isinstance(item, Gio.File):
        return item.get_path()
    return os.fspath(item)


@Gtk.Template(resource_path='/io/github/jackicus/Bookcase/add_books.ui')
class AddBooksDialog(Adw.Dialog):
    __gtype_name__ = 'BookcaseAddBooksDialog'

    stack = Gtk.Template.Child()
    progress_label = Gtk.Template.Child()
    progress_bar = Gtk.Template.Child()
    file_label = Gtk.Template.Child()
    cancel_button = Gtk.Template.Child()
    summary_icon = Gtk.Template.Child()
    summary_title = Gtk.Template.Child()
    summary_label = Gtk.Template.Child()
    summary_list = Gtk.Template.Child()
    done_button = Gtk.Template.Child()

    def __init__(self, app, parent, kind='add'):
        super().__init__()
        self.app = app
        self.parent = parent
        self.kind = kind  # 'add', 'scan' or 'calibre'
        self.job = None
        self.report = None
        self.presented = False
        self.finished = False
        self._pulse = None
        self.set_title({'add': _('Adding Books'), 'scan': _('Reading Folder'),
                        'calibre': _('Linking Calibre Library')}[kind])
        self.progress_label.set_text(_('Looking for books…'))
        connect_weak_call(self.cancel_button, 'clicked', self.cancel)
        connect_weak_call(self.done_button, 'clicked', self.close)
        self.connect('closed', self._on_closed)
        self._pulse = GLib.timeout_add(120, self._on_pulse)

    def start(self, method, *args, **kwargs):
        """Run app.importer's `method` (add_async, scan_async, link_calibre_async)."""
        importer = self.app.importer
        self.job = getattr(importer, method)(*args, progress=self.on_progress,
                                             done=self.on_done, **kwargs)
        GLib.timeout_add(DELAY, self._present_if_working)
        return self.job

    def _present_if_working(self):
        if not self.finished:
            self.show()
        return GLib.SOURCE_REMOVE

    def show(self):
        if not self.presented:
            self.presented = True
            watch_dialog(self, self.parent)
            self.present(self.parent)

    def cancel(self):
        if self.job is not None and not self.finished:
            self.job.cancel()
            self.cancel_button.set_sensitive(False)
            self.progress_label.set_text(_('Stopping…'))

    def _on_pulse(self):
        if self.finished or self.progress_bar.get_fraction() > 0:
            self._pulse = None
            return GLib.SOURCE_REMOVE
        self.progress_bar.pulse()
        return GLib.SOURCE_CONTINUE

    def _on_closed(self, _dialog):
        self.cancel()

    # -- progress ----------------------------------------------------------------------------

    def on_progress(self, done, total, path):
        if self.finished or (self.job is not None and self.job.cancelled()):
            return
        total = max(total, done, 1)
        # Translators: progress of adding books: "3 of 12".
        self.progress_label.set_text(_('{done} of {total}').format(done=done, total=total))
        self.progress_bar.set_fraction(done / total)
        self.progress_bar.update_property([Gtk.AccessibleProperty.VALUE_TEXT],
                                          [self.progress_label.get_text()])
        self.file_label.set_text(os.path.basename(path or ''))

    def on_done(self, report):
        self.finished = True
        self.report = report
        if self._quiet(report):
            book = self.app.library.book(report.added[0])
            self._toast_added(book)
            if self.presented:
                self.force_close()
            return
        self.show_summary(report)
        self.show()

    def _quiet(self, report):
        """One book added and nothing else to say: a toast is enough."""
        return (self.kind == 'add' and len(report.added) == 1 and not report.failed
                and not report.duplicates and not report.merged and not report.cancelled)

    def _toast_added(self, book):
        title = book.title if book else ''
        window = self._window()
        toast = Adw.Toast(title=_('Added “{title}”').format(title=title) if title
                          else _('Book added'))
        if book is not None and window is not None and hasattr(window, 'show_book'):
            toast.set_button_label(_('Show'))
            toast.set_use_markup(False)
            toast.connect('button-clicked', lambda *_args: window.show_book(book.id))
        if window is not None and hasattr(window, 'add_toast'):
            window.add_toast(toast)
        else:
            self.app.toast(toast.get_title())

    def _window(self):
        window = getattr(self.app, 'window', None)
        window = window() if callable(window) else None
        if window is None and self.parent is not None:
            root = self.parent.get_root() if hasattr(self.parent, 'get_root') else None
            window = root if hasattr(root, 'show_book') else None
        return window

    # -- the summary -------------------------------------------------------------------------

    def show_summary(self, report):
        self.stack.set_visible_child_name('done')
        added = len(report.added)
        nothing = not (report.added or report.merged or report.updated or report.moved)
        if report.cancelled:
            self.summary_title.set_text(_('Stopped'))
            self.summary_icon.set_from_icon_name('process-stop-symbolic')
        elif self.kind == 'add' and nothing:
            self.summary_title.set_text(_('No Books Added'))
            self.summary_icon.set_from_icon_name('dialog-information-symbolic')
        elif self.kind == 'add':
            self.summary_title.set_text(ngettext('{n} Book Added', '{n} Books Added',
                                                 added).format(n=added))
        elif self.kind == 'scan':
            self.summary_title.set_text(_('Folder Read'))
        else:
            self.summary_title.set_text(_('Calibre Library Linked'))
        if report.failed and nothing:
            self.summary_icon.set_from_icon_name('dialog-warning-symbolic')
            self.summary_icon.add_css_class('warning')
        elif not nothing and not report.cancelled:
            self.summary_icon.add_css_class('success')
        if self.kind == 'calibre':
            self.summary_label.set_text(_('Its books are read in place. Calibre stays usable; '
                                          'Bookcase never writes to its library.'))
            self.summary_label.set_visible(True)
        elif self.kind == 'scan':
            self.summary_label.set_text(_('Books in the folder are read where they are.'))
            self.summary_label.set_visible(True)

        rows = []
        if report.added:
            rows.append(self._count_row(
                ngettext('{n} book added', '{n} books added', added).format(n=added),
                _('_Show'), self._show_added))
        if report.merged:
            count = len(report.merged)
            rows.append(self._count_row(ngettext(
                '{n} format added to a book already there',
                '{n} formats added to books already there', count).format(n=count)))
        if report.updated:
            count = len(report.updated)
            rows.append(self._count_row(ngettext('{n} book updated', '{n} books updated',
                                                 count).format(n=count)))
        if report.moved:
            count = len(report.moved)
            rows.append(self._count_row(ngettext('{n} moved file found again',
                                                 '{n} moved files found again',
                                                 count).format(n=count)))
        if report.missing:
            count = len(report.missing)
            rows.append(self._count_row(ngettext('{n} file is missing', '{n} files are missing',
                                                 count).format(n=count)))
        if report.duplicates:
            count = len(report.duplicates)
            expander = Adw.ExpanderRow(title=ngettext(
                '{n} already in the library', '{n} already in the library', count).format(n=count))
            for path, book_id in report.duplicates[:LISTED]:
                row = Adw.ActionRow(title=GLib.markup_escape_text(os.path.basename(path)))
                row.set_subtitle(GLib.markup_escape_text(self._book_title(book_id)))
                row.add_suffix(self._show_button(self._show_book, None, book_id))
                expander.add_row(row)
            rows.append(expander)
        if report.failed:
            count = len(report.failed)
            expander = Adw.ExpanderRow(title=ngettext('{n} file could not be added',
                                                      '{n} files could not be added',
                                                      count).format(n=count))
            expander.add_css_class('add-books-failed')
            for path, message in report.failed[:LISTED]:
                row = Adw.ActionRow(title=GLib.markup_escape_text(os.path.basename(path)),
                                    subtitle=GLib.markup_escape_text(message or ''))
                row.set_subtitle_lines(3)
                row.set_tooltip_text(path)
                expander.add_row(row)
            expander.set_expanded(count <= 3)
            rows.append(expander)
        for row in rows:
            self.summary_list.append(row)
        self.summary_list.set_visible(bool(rows))
        self.done_button.grab_focus()
        self._fit_height()

    def _fit_height(self):
        """Grow to the summary: a dialog keeps the height it had for the progress."""
        width = self.get_content_width()
        _minimum, natural, _b1, _b2 = self.get_child().measure(
            Gtk.Orientation.VERTICAL, width if width > 0 else 460)
        self.set_content_height(min(max(natural, 294), 600))

    def _count_row(self, title, button_label=None, callback=None):
        row = Adw.ActionRow(title=title)
        if button_label:
            row.add_suffix(self._show_button(callback, button_label))
        return row

    @staticmethod
    def _show_button(method, label=None, *args):
        """A Show button that calls method(*args) (a method of the dialog, held weakly)."""
        button = Gtk.Button(label=label or _('_Show'), use_underline=True,
                            valign=Gtk.Align.CENTER)
        connect_weak_call(button, 'clicked', method, *args)
        return button

    def _book_title(self, book_id):
        book = self.app.library.book(book_id)
        return book.title if book else ''

    def _show_book(self, book_id):
        window = self._window()
        self.close()
        if window is not None and hasattr(window, 'show_book'):
            window.show_book(book_id)

    def _show_added(self, *_args):
        report = self.report
        if len(report.added) == 1:
            self._show_book(report.added[0])
            return
        window = self._window()
        self.close()
        if window is not None and hasattr(window, 'show_root'):
            window.show_root('all')  # sorted by date added, newest first by default


def present(app, parent, paths, copy=True):
    """Add files and folders (paths or Gio.Files) to the library; copied into the library
    folder unless copy=False."""
    dialog = AddBooksDialog(app, parent, 'add')
    dialog.start('add_async', [_path(path) for path in paths], copy=copy)
    return dialog


def present_scan(app, parent, folder_path):
    """Read a watched folder (added as one if new) in place."""
    dialog = AddBooksDialog(app, parent, 'scan')
    dialog.start('scan_async', _path(folder_path))
    return dialog


def present_link_calibre(app, parent, path):
    """Link a Calibre library (the folder holding metadata.db) and read its books."""
    dialog = AddBooksDialog(app, parent, 'calibre')
    dialog.start('link_calibre_async', _path(path))
    return dialog
