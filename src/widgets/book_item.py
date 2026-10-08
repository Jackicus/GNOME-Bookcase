# SPDX-License-Identifier: GPL-2.0-or-later
# SPDX-FileCopyrightText: 2026 Jack Tully

"""A book in a list model: what the grids and lists of books hold (a Gio.ListStore of
BookItem), so a GridView can show 10,000 books without a widget each.

    item = BookItem(book)          # a library.Book
    item.id, item.book
    item.set_book(newer)           # emits notify::book when it changed (a bound tile redraws)
    BookIds(ids)                   # books being dragged (onto a shelf in the sidebar)
"""

from gi.repository import GObject


class BookItem(GObject.Object):
    __gtype_name__ = 'BookcaseBookItem'

    def __init__(self, book):
        super().__init__()
        self.id = book.id
        self._book = book

    @GObject.Property(type=object, flags=GObject.ParamFlags.READABLE)
    def book(self):
        return self._book

    def set_book(self, book):
        """Replace the book (the same id, newer fields); notify::book when it differs."""
        if book == self._book:
            return False
        self._book = book
        self.notify('book')
        return True

    def __repr__(self):
        return f'<BookItem {self.id} {self._book.title!r}>'


class BookIds(GObject.Object):
    """The books a drag carries, from a grid or list to the sidebar's shelves."""

    __gtype_name__ = 'BookcaseBookIds'

    def __init__(self, ids=()):
        super().__init__()
        self.ids = list(ids)
