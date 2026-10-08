# SPDX-License-Identifier: GPL-2.0-or-later
# SPDX-FileCopyrightText: 2026 Jack Tully

"""Every keyboard shortcut, in one place: the application and window accelerators (main.py
sets them), the reader's keys (reader_window.py handles them itself: a bare key is never an
application accelerator, GTK would run it before a focused entry saw it), the library grid's,
and the sections the Keyboard Shortcuts dialog shows (dialogs/shortcuts.py).

    ACCELS                  {action name: [accelerators]} for Gtk.Application
    READER, GRID            {key name: [accelerators]} the windows and pages bind themselves
    sections()              [(title, [(description, accelerator or keys)])] for the dialog
    accelerator(key)        the accelerators of a key name, in any table, space-separated
"""

from gettext import gettext as _

ACCELS = {
    'app.add-books': ['<primary>o'],
    'app.add-folder': ['<primary><shift>o'],
    'app.open-file': ['<primary><alt>o'],
    'app.undo': ['<primary>z'],
    'app.preferences': ['<primary>comma'],
    'app.shortcuts': ['<primary>question'],
    'app.quit': ['<primary>q'],
    'win.search': ['<primary>f'],
    'win.quick-open': ['<primary>k'],
    'win.home': ['<primary>1'],
    'win.all': ['<primary>2'],
    'win.authors': ['<primary>3'],
    'win.series': ['<primary>4'],
    'win.stats': ['<primary>5'],
    'win.back': ['<alt>Left'],
    'win.toggle-sidebar': ['F9'],
    'win.view-grid': ['<primary>g'],
    'win.view-list': ['<primary>l'],
    'win.close': ['<primary>w'],
}

# The reader window's own keys (reader_window.py).
READER = {
    'next': ['Right', 'space', 'Page_Down', 'l'],
    'previous': ['Left', '<shift>space', 'Page_Up', 'h'],
    'scroll-down': ['Down', 'j'],
    'scroll-up': ['Up', 'k'],
    'start': ['Home'],
    'end': ['End'],
    'next-chapter': ['bracketright', '<primary>End'],
    'previous-chapter': ['bracketleft', '<primary>Home'],
    'back': ['<alt>Left'],
    'forward': ['<alt>Right'],
    'contents': ['<primary>t', 'F9'],
    'search': ['<primary>f', 'slash'],
    'search-next': ['<primary>g'],
    'search-previous': ['<primary><shift>g'],
    'bookmark': ['<primary>d'],
    'copy': ['<primary>c'],
    'annotations': ['<primary>b'],
    'bigger': ['<primary>plus', '<primary>equal'],
    'smaller': ['<primary>minus'],
    'reset-size': ['<primary>0'],
    'fullscreen': ['F11'],
    'leave-fullscreen': ['Escape'],
    'go-to': ['<primary>j'],
    'info': ['<primary>i'],
    'print': ['<primary>p'],
    'read-aloud': ['<primary><shift>s'],
    'read-aloud-next': ['<primary><shift>Right'],
    'read-aloud-previous': ['<primary><shift>Left'],
    'close': ['<primary>w'],
}

# The book grid's and list's keys (pages/books.py), on the view.
GRID = {
    'open': ['Return'],
    'details': ['<alt>Return'],
    'edit': ['<primary>e'],
    'remove': ['Delete'],
    'select-all': ['<primary>a'],
    'select-none': ['<primary><shift>a'],
}


def accelerator(key):
    """The accelerators of a key name ('app.add-books', 'next'), space-separated."""
    for table in (ACCELS, READER, GRID):
        if key in table:
            return ' '.join(table[key])
    raise KeyError(key)


def sections():
    """The Keyboard Shortcuts dialog's sections: (title, [(what it does, accelerators)])."""
    return [
        (_('General'), [
            (_('Add Books'), accelerator('app.add-books')),
            (_('Add a Folder'), accelerator('app.add-folder')),
            (_('Read a File Without Adding It'), accelerator('app.open-file')),
            (_('Search'), accelerator('win.search')),
            (_('Go to a Book, Author, Series or Shelf'), accelerator('win.quick-open')),
            (_('Home'), accelerator('win.home')),
            (_('All Books'), accelerator('win.all')),
            (_('Authors'), accelerator('win.authors')),
            (_('Series'), accelerator('win.series')),
            (_('Statistics'), accelerator('win.stats')),
            (_('Show as Covers'), accelerator('win.view-grid')),
            (_('Show as a List'), accelerator('win.view-list')),
            (_('Undo'), accelerator('app.undo')),
            (_('Show or Hide the Sidebar'), accelerator('win.toggle-sidebar')),
            (_('Go Back'), accelerator('win.back')),
            (_('Preferences'), accelerator('app.preferences')),
            (_('Keyboard Shortcuts'), accelerator('app.shortcuts')),
            (_('Close the Window'), accelerator('win.close')),
            (_('Quit'), accelerator('app.quit')),
        ]),
        (_('Books'), [
            (_('Read'), accelerator('open')),
            (_('Details'), accelerator('details')),
            (_('Edit Details'), accelerator('edit')),
            (_('Remove from Library'), accelerator('remove')),
            (_('Select All'), accelerator('select-all')),
            (_('Select None'), accelerator('select-none')),
        ]),
        (_('Reading'), [
            (_('Next Page'), accelerator('next')),
            (_('Previous Page'), accelerator('previous')),
            (_('Next Chapter'), accelerator('next-chapter')),
            (_('Previous Chapter'), accelerator('previous-chapter')),
            (_('Start of the Book'), accelerator('start')),
            (_('End of the Book'), accelerator('end')),
            (_('Back to Where You Were'), accelerator('back')),
            (_('Forward Again'), accelerator('forward')),
            (_('Contents'), accelerator('contents')),
            (_('Search the Book'), accelerator('search')),
            (_('Next Result'), accelerator('search-next')),
            (_('Previous Result'), accelerator('search-previous')),
            (_('Add a Bookmark'), accelerator('bookmark')),
            (_('Copy the Selected Text'), accelerator('copy')),
            (_('Highlights and Bookmarks'), accelerator('annotations')),
            (_('Larger Text (Zoom In, for a PDF or a Comic)'), accelerator('bigger')),
            (_('Smaller Text (Zoom Out)'), accelerator('smaller')),
            (_('Reset Text Size (Zoom)'), accelerator('reset-size')),
            (_('Go to a Location'), accelerator('go-to')),
            (_('Book Details'), accelerator('info')),
            (_('Print (a PDF)'), accelerator('print')),
            (_('Read Aloud'), accelerator('read-aloud')),
            (_('Next Sentence, Reading Aloud'), accelerator('read-aloud-next')),
            (_('Previous Sentence, Reading Aloud'), accelerator('read-aloud-previous')),
            (_('Fullscreen'), accelerator('fullscreen')),
        ]),
    ]
