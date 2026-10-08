# SPDX-License-Identifier: GPL-2.0-or-later
# SPDX-FileCopyrightText: 2026 Jack Tully

"""Highlights in and out: a Kindle's My Clippings.txt imported, Markdown exported.

    add_actions(app)                    # app.import-clippings, app.export-highlights
    present_import(app, parent, text)   # the ClippingsDialog for a My Clippings.txt's text
    choose_clippings_file(app, parent)  # Import Kindle Highlights…: a file chooser, then that
    import_file(app, parent, path)      # a Kindle's documents/My Clippings.txt (device page)
    choose_book(app, parent, query, callback)   # a book picked from the library: callback(id)
    export_book(app, parent, book_id)   # Export Highlights…: a Markdown file, saved where asked
    markdown(app, book_id)              # annotations.to_markdown of a book
    copy_book(app, widget, book_id)     # Copy as Markdown, to the clipboard; True when copied
    export_all(app, parent)             # Export All Highlights…: one .md a book, in a folder

The import dialog lists the books the clippings came from (annotations.clipping_groups): a
matched book with its counts and how many are new, checked when any are; a book not in the
library dimmed, with Choose Book… to pick one (and then checked). Import adds the checked
books' new clippings in one undo step (annotations.import_clippings) and toasts with Undo.
Bookmarks are not imported (a Kindle location is no place in Bookcase's copy of the book).
"""

import logging
import os
from gettext import gettext as _
from gettext import ngettext

from gi.repository import Adw, Gio, GLib, Gtk

from .. import annotations
from . import watch_dialog

log = logging.getLogger(__name__)

MAX_CLIPPINGS_BYTES = 50 * 1024 * 1024


def add_actions(app):
    for name, callback in (('import-clippings',
                            lambda *_args: choose_clippings_file(app, app.window())),
                           ('export-highlights', lambda *_args: export_all(app, app.window()))):
        action = Gio.SimpleAction.new(name, None)
        action.connect('activate', callback)
        app.add_action(action)


# -- importing ---------------------------------------------------------------------------------

def _read_text(path):
    with open(path, 'rb') as file:
        data = file.read(MAX_CLIPPINGS_BYTES)
    return data.decode('utf-8-sig', errors='replace')


def import_file(app, parent, path):
    try:
        text = _read_text(path)
    except OSError as error:
        app.report(error, _('Could not read the highlights'))
        return None
    return present_import(app, parent, text)


def choose_clippings_file(app, parent):
    text_files = Gtk.FileFilter(name=_('Kindle Clippings'))
    text_files.add_suffix('txt')
    filters = Gio.ListStore.new(Gtk.FileFilter)
    filters.append(text_files)
    dialog = Gtk.FileDialog(title=_('Import Kindle Highlights'), accept_label=_('_Import'),
                            filters=filters, default_filter=text_files, modal=True)

    def chosen(dialog, result):
        try:
            file = dialog.open_finish(result)
        except GLib.Error:
            return  # dismissed
        if file is not None and file.get_path():
            import_file(app, parent, file.get_path())

    dialog.open(parent, None, chosen)
    return dialog


def present_import(app, parent, text):
    clippings = annotations.parse_kindle_clippings(text)
    matches = annotations.match_clippings(app.library, clippings)
    groups = annotations.clipping_groups(app.library, matches)
    if not groups:
        app.toast(_('No highlights found in that file'))
        return None
    dialog = ClippingsDialog(app, groups)
    watch_dialog(dialog, parent)
    dialog.present(parent)
    return dialog


def counts_text(group, new=None):
    """'12 highlights · 3 notes', with how many are new when not all."""
    parts = [ngettext('{n} highlight', '{n} highlights', group.highlights).format(
        n=group.highlights)] if group.highlights else []
    if group.notes:
        parts.append(ngettext('{n} note', '{n} notes', group.notes).format(n=group.notes))
    text = ' · '.join(parts)
    if new is not None and new < len(group.matches):
        if new == 0:
            # Translators: after the counts of a book's clippings.
            return _('{counts}, all imported before').format(counts=text)
        return _('{counts}, {n} new').format(counts=text, n=new)
    return text


class ClippingsDialog(Adw.Dialog):
    __gtype_name__ = 'BookcaseClippingsDialog'

    def __init__(self, app, groups):
        super().__init__(title=_('Import Kindle Highlights'), content_width=480,
                         content_height=600)
        self.app = app
        self.groups = groups
        self.chosen = {}  # group key -> book id picked for an unmatched group
        self.rows = []
        header = Adw.HeaderBar(show_start_title_buttons=False, show_end_title_buttons=False)
        cancel = Gtk.Button(label=_('_Cancel'), use_underline=True)
        cancel.connect('clicked', lambda *_args: self.close())
        header.pack_start(cancel)
        self.import_button = Gtk.Button(label=_('_Import'), use_underline=True)
        self.import_button.add_css_class('suggested-action')
        self.import_button.connect('clicked', lambda *_args: self.run_import())
        header.pack_end(self.import_button)
        page = Adw.PreferencesPage()
        total = sum(len(group.matches) for group in groups)
        self.group = Adw.PreferencesGroup(
            title=_('Books'),
            description=ngettext('{n} highlight or note from {books} book. Already '
                                 'imported ones are skipped.',
                                 '{n} highlights and notes from {books} books. Already '
                                 'imported ones are skipped.', len(groups)).format(
                n=total, books=len(groups)))
        self.listbox = Gtk.ListBox(selection_mode=Gtk.SelectionMode.NONE)
        self.listbox.add_css_class('boxed-list')
        self.group.add(self.listbox)
        page.add(self.group)
        note = Adw.PreferencesGroup(description=_(
            'Kindle locations are not places in your copy of a book: Bookcase finds each '
            'highlight’s place when you next open the book.'))
        page.add(note)
        for group in groups:
            row = self._row(group)
            self.rows.append(row)
            self.listbox.append(row)
        view = Adw.ToolbarView(content=page)
        view.add_top_bar(header)
        self.set_child(view)
        self._update()

    def book_for(self, group):
        return self.chosen.get(group.key, group.book_id)

    def _row(self, group):
        row = Adw.ActionRow(use_markup=False, title_lines=2, subtitle_lines=2)
        row.clipping_group = group
        check = Gtk.CheckButton(valign=Gtk.Align.CENTER)
        check.update_property([Gtk.AccessibleProperty.LABEL], [group.title])
        check.connect('toggled', lambda *_args: self._update())
        row.add_prefix(check)
        row.set_activatable_widget(check)
        row.check = check
        choose = Gtk.Button(label=_('Choose Book…'), valign=Gtk.Align.CENTER)
        choose.add_css_class('flat')
        choose.connect('clicked', self._on_choose, row)
        row.add_suffix(choose)
        row.choose_button = choose
        self._show_row(row, initial=True)
        return row

    def _show_row(self, row, initial=False):
        group = row.clipping_group
        book_id = self.book_for(group)
        book = self.app.library.book(book_id) if book_id is not None else None
        if book is None:
            row.set_title(group.title)
            author = f'{group.author} · ' if group.author else ''
            row.set_subtitle(_('{author}Not in your library · {counts}').format(
                author=author, counts=counts_text(group)))
            row.add_css_class('dimmed')
            row.check.set_active(False)
            row.check.set_sensitive(False)
            row.choose_button.set_label(_('Choose Book…'))
            row.new = 0
            return
        new = group.new(self.app.library, book_id)
        row.new = new
        row.remove_css_class('dimmed')
        row.set_title(book.title)
        row.set_subtitle(f'{book.author} · {counts_text(group, new)}')
        row.check.set_sensitive(new > 0)
        if initial or group.key in self.chosen:
            row.check.set_active(new > 0)
        row.choose_button.set_label(_('Change…'))
        row.choose_button.set_visible(group.book_id is None)

    def _on_choose(self, _button, row):
        group = row.clipping_group
        ref = self.weak_ref()

        def picked(book_id):
            dialog = ref()
            if dialog is not None:
                dialog.chosen[group.key] = book_id
                dialog._show_row(row)
                dialog._update()

        choose_book(self.app, self, group.title, picked)

    def checked_groups(self):
        return [row.clipping_group for row in self.rows if row.check.get_active()]

    def plan(self):
        return annotations.import_plan(self.app.library, self.checked_groups(), self.chosen)

    def _update(self):
        new = sum(row.new for row in self.rows if row.check.get_active())
        self.import_button.set_sensitive(new > 0)
        self.import_button.set_label(ngettext('_Import {n}', '_Import {n}', new).format(n=new)
                                     if new else _('_Import'))

    def run_import(self):
        plan = self.plan()
        try:
            count = annotations.import_clippings(self.app.library, plan)
        except Exception as error:
            self.app.report(error, _('Could not import the highlights'))
            return 0
        if count:
            self.app.toast(ngettext('Imported {n} highlight into {books} book',
                                    'Imported {n} highlights into {books} books',
                                    count).format(n=count, books=len(plan)) if len(plan) != 1
                           else ngettext('Imported {n} highlight into “{title}”',
                                         'Imported {n} highlights into “{title}”',
                                         count).format(
                               n=count, title=self.app.library.book(next(iter(plan))).title),
                           undo=True)
        else:
            self.app.toast(_('No new highlights'))
        self.force_close()
        return count


# -- choosing a book ---------------------------------------------------------------------------

def choose_book(app, parent, query, callback):
    """A dialog to search the library and pick a book: callback(book_id)."""
    dialog = Adw.Dialog(title=_('Choose Book'), content_width=420, content_height=520)
    header = Adw.HeaderBar()
    entry = Gtk.SearchEntry(text=query or '', hexpand=True,
                            placeholder_text=_('Search your library'))
    entry.update_property([Gtk.AccessibleProperty.LABEL], [_('Search your library')])
    listbox = Gtk.ListBox(selection_mode=Gtk.SelectionMode.NONE)
    listbox.add_css_class('boxed-list')
    empty = Adw.StatusPage(icon_name='edit-find-symbolic', title=_('No Books Found'))
    empty.add_css_class('compact')
    box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=12, margin_top=12,
                  margin_bottom=12, margin_start=12, margin_end=12)
    box.append(entry)
    box.append(listbox)
    box.append(empty)
    clamp = Adw.Clamp(maximum_size=560, child=box)
    view = Adw.ToolbarView(content=Gtk.ScrolledWindow(child=clamp,
                                                      hscrollbar_policy=Gtk.PolicyType.NEVER))
    view.add_top_bar(header)
    dialog.set_child(view)

    def fill(*_args):
        listbox.remove_all()
        text = entry.get_text().strip()
        try:
            books = app.library.books(query=text, sort='title', limit=50) if text else \
                app.library.books(sort='title', limit=50)
        except Exception:  # a query the search syntax refuses: by title words
            books = app.library.books(query=f'title:"{text}"', limit=50)
        if not books and text:
            words = ' or '.join(f'title:{word}' for word in text.split()[:4])
            try:
                books = app.library.books(query=words, sort='title', limit=50)
            except Exception:
                books = []
        for book in books:
            row = Adw.ActionRow(title=book.title, subtitle=book.author, use_markup=False,
                                activatable=True)
            row.book_id = book.id
            listbox.append(row)
        listbox.set_visible(bool(books))
        empty.set_visible(not books)

    def activated(_listbox, row):
        callback(row.book_id)
        dialog.close()

    handler = entry.connect('search-changed', fill)
    dialog.connect('closed', lambda *_args: entry.disconnect(handler))
    listbox.connect('row-activated', activated)
    fill()
    watch_dialog(dialog, parent)
    dialog.present(parent)
    entry.grab_focus()
    dialog.listbox = listbox
    dialog.entry = entry
    return dialog


# -- exporting ---------------------------------------------------------------------------------

def markdown(app, book_id):
    book = app.library.book(book_id)
    if book is None:
        return ''
    return annotations.to_markdown(book, app.library.annotations(book_id))


def markdown_name(book):
    from ..exporting import copy_name

    return copy_name(book, '.md')


def copy_book(app, widget, book_id):
    text = markdown(app, book_id)
    if not text:
        return False
    widget.get_clipboard().set(text)
    return True


def _write(file, text):
    file.replace_contents(text.encode('utf-8'), None, False,
                          Gio.FileCreateFlags.REPLACE_DESTINATION, None)


def export_book(app, parent, book_id, toast=None):
    """Save a book's highlights as Markdown where the user says. toast(text) says how it went
    (app.toast by default)."""
    book = app.library.book(book_id)
    if book is None:
        return None
    toast = toast or app.toast
    markdown_files = Gtk.FileFilter(name=_('Markdown'))
    markdown_files.add_suffix('md')
    filters = Gio.ListStore.new(Gtk.FileFilter)
    filters.append(markdown_files)
    dialog = Gtk.FileDialog(title=_('Export Highlights'), accept_label=_('_Export'),
                            initial_name=markdown_name(book), filters=filters, modal=True)

    def chosen(dialog, result):
        try:
            file = dialog.save_finish(result)
        except GLib.Error:
            return
        try:
            _write(file, markdown(app, book_id))
        except GLib.Error as error:
            app.report(error, _('Could not export the highlights'))
            return
        toast(_('Highlights exported to “{name}”').format(name=file.get_basename()))

    root = parent.get_root() if parent is not None and hasattr(parent, 'get_root') else None
    dialog.save(root, None, chosen)
    return dialog


def write_all(library, folder):
    """One Markdown file a book with highlights or bookmarks, in `folder`; the paths."""
    written = []
    taken = set()
    for book_id in annotations.annotated_books(library):
        book = library.book(book_id)
        if book is None:
            continue
        name = markdown_name(book)
        stem, number = name[:-3], 2
        while name in taken:
            name = f'{stem} ({number}).md'
            number += 1
        taken.add(name)
        path = os.path.join(folder, name)
        with open(path, 'w', encoding='utf-8') as file:
            file.write(annotations.to_markdown(book, library.annotations(book_id)))
        written.append(path)
    return written


def export_all(app, parent):
    if not annotations.annotated_books(app.library):
        app.toast(_('No book has highlights or bookmarks yet'))
        return None
    dialog = Gtk.FileDialog(title=_('Export All Highlights To'), accept_label=_('_Export'),
                            modal=True)

    def chosen(dialog, result):
        try:
            folder = dialog.select_folder_finish(result)
        except GLib.Error:
            return
        if folder is None or folder.get_path() is None:
            return
        try:
            written = write_all(app.library, folder.get_path())
        except OSError as error:
            app.report(error, _('Could not export the highlights'))
            return
        app.toast(ngettext('Exported the highlights of {n} book',
                           'Exported the highlights of {n} books', len(written)).format(
            n=len(written)))

    dialog.select_folder(parent, None, chosen)
    return dialog
