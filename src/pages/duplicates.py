# SPDX-License-Identifier: GPL-2.0-or-later
# SPDX-FileCopyrightText: 2026 Jack Tully

"""Find Duplicates (the main menu): the books that look like the same book, pushed.

    page = DuplicatesPage()
    page.refresh()                     # from the library (also on map and on `changed`)
    page.groups                        # [DuplicateGroup], for tests
    page.merge(group)                  # merges a group into its chosen book, with Undo

library.duplicates() finds them (the same normalised title, an author in common). Each group
is a boxed list of its books (cover, formats, the reading state, author, where its files are)
with a radio button for the one to keep, library.richest() chosen at first; Merge
(library.merge_books, an undo step) keeps that one with everything of the others. When the
kept book has no cover and another has, the cover comes with it. Merge All does every group.
"""

import logging
import os
from gettext import gettext as _
from gettext import ngettext

from gi.repository import Adw, GLib, Gtk

from ..widgets.book_tile import progress_text
from ..widgets.cover import Cover
from ..widgets.util import connect_weak
from . import PageListener, app

log = logging.getLogger(__name__)

CHANGE_KINDS = ('books', 'files')
COVER_WIDTH = 40


class DuplicateGroup:
    """A group shown: its book ids, the radio buttons and the one chosen to keep."""

    def __init__(self, ids, widget, checks):
        self.ids = ids
        self.widget = widget
        self.checks = checks  # {book id: Gtk.CheckButton}

    @property
    def keep(self):
        return next((book_id for book_id, check in self.checks.items() if check.get_active()),
                    self.ids[0])

    def choose(self, book_id):
        self.checks[book_id].set_active(True)


@Gtk.Template(resource_path='/io/github/jackicus/Bookcase/duplicates.ui')
class DuplicatesPage(Adw.NavigationPage):
    __gtype_name__ = 'BookcaseDuplicatesPage'

    window_title = Gtk.Template.Child()
    merge_all_button = Gtk.Template.Child()
    stack = Gtk.Template.Child()
    groups_box = Gtk.Template.Child()

    def __init__(self):
        super().__init__()
        self.groups = []
        connect_weak(self.merge_all_button, 'clicked', self._on_merge_all)
        self.listener = PageListener(self, CHANGE_KINDS, DuplicatesPage.refresh)
        self.refresh()

    def refresh(self):
        library = app().library
        chosen = {group.ids[0]: group.keep for group in self.groups}
        for group in self.groups:
            self.groups_box.remove(group.widget)
        self.groups = []
        for ids in library.duplicates():
            keep = chosen.get(ids[0])
            if keep not in ids:
                keep = library.richest(ids)
            self.groups.append(self._group(library, ids, keep))
        for group in self.groups:
            self.groups_box.append(group.widget)
        count = len(self.groups)
        self.window_title.set_subtitle(
            ngettext('{n} group', '{n} groups', count).format(n=f'{count:n}') if count else '')
        self.merge_all_button.set_visible(count > 1)
        self.stack.set_visible_child_name('groups' if count else 'empty')

    def _group(self, library, ids, keep):
        books = [book for book in (library.book(book_id) for book_id in ids) if book]
        title = books[0].title if books else ''
        group_widget = Adw.PreferencesGroup(title=GLib.markup_escape_text(title),
                                            description=ngettext(
                                                '{n} copy', '{n} copies', len(books)).format(
                                                    n=len(books)))
        merge = Gtk.Button(label=_('_Merge'), use_underline=True, valign=Gtk.Align.CENTER)
        merge.add_css_class('suggested-action')
        merge.update_property([Gtk.AccessibleProperty.LABEL],
                              [_('Merge the copies of “{title}”').format(title=title)])
        group_widget.set_header_suffix(merge)
        checks = {}
        first = None
        for book in books:
            title, subtitle = self._texts(library, book)
            row = Adw.ActionRow(title=title, subtitle=subtitle, use_markup=False,
                                subtitle_lines=4)
            cover = Cover(width=COVER_WIDTH, valign=Gtk.Align.CENTER)
            cover.add_css_class('small')
            cover.set_book(book)
            row.add_prefix(cover)
            check = Gtk.CheckButton(valign=Gtk.Align.CENTER, active=book.id == keep,
                                    tooltip_text=_('Keep This One'))
            check.update_property([Gtk.AccessibleProperty.LABEL], [_('Keep This One')])
            if first is None:
                first = check
            else:
                check.set_group(first)
            row.add_suffix(check)
            row.set_activatable_widget(check)
            group_widget.add(row)
            checks[book.id] = check
        group = DuplicateGroup([book.id for book in books], group_widget, checks)
        ref = self.weak_ref()

        def on_merge(_button):
            page = ref()
            if page is not None:
                page.merge(group)

        merge.connect('clicked', on_merge)
        return group

    def _texts(self, library, book):
        """A copy's row: what tells it apart (its formats and how far it has been read) over
        its author and where its files are."""
        files = library.files(book.id)
        parts = [', '.join(dict.fromkeys(file.format.upper() for file in files))
                 or _('No Files'), progress_text(book)]
        lines = [book.author]
        for book_file in files[:2]:
            path = book_file.path
            home = GLib.get_home_dir().rstrip('/')
            if path.startswith(home + '/'):
                path = '~' + path[len(home):]
            folder = os.path.dirname(path)
            lines.append(_('Missing: {path}').format(path=folder) if book_file.missing
                         else folder)
        return ' · '.join(part for part in parts if part), '\n'.join(lines)

    def merge(self, group, toast=True):
        """Merge a group into the book chosen to keep (one undo step); the number merged."""
        library = app().library
        keep = group.keep
        others = [book_id for book_id in group.ids if book_id != keep]
        kept = library.book(keep)
        if kept is None or not others:
            return 0
        with library.undoable(_('Merge Books')):
            if not kept.has_cover:
                donor = next((book for book in (library.book(book_id) for book_id in others)
                              if book is not None and book.has_cover), None)
                data = app().covers.data(donor) if donor is not None else None
                if data:
                    try:
                        app().covers.save(keep, data)
                    except (ValueError, OSError) as error:
                        log.warning('moving the cover of book %s: %s', donor.id, error)
            merged = library.merge_books(keep, others)
        if toast and merged:
            app().toast(ngettext('Merged {n} copy into “{title}”',
                                 'Merged {n} copies into “{title}”', merged).format(
                                     n=merged, title=kept.title), undo=True)
        return merged

    def _on_merge_all(self, _button):
        library = app().library
        groups = list(self.groups)
        merged = 0
        with library.undoable(_('Merge Books')):
            for group in groups:
                merged += self.merge(group, toast=False)
        if merged:
            app().toast(ngettext('Merged {n} copy', 'Merged {n} copies', merged).format(
                n=merged), undo=True)
