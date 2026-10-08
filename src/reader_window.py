# SPDX-License-Identifier: GPL-2.0-or-later
# SPDX-FileCopyrightText: 2026 Jack Tully

"""The reader window: one per open book.

    reader_window.open(app, book_id)    # the book's window, raised if open; returns it
    window.show_annotations()           # the sidebar open on highlights and bookmarks
    window.book_id
    window.view                         # its view: the BookView, or a PdfView for a PDF
    window.is_pdf

The book (library.reading_file()) shows in a BookView (WebKit and foliate-js); a PDF in a
widgets/pdf_view.PdfView (Poppler), which has the same methods and signals, so the window
drives either through `self.view` (`self.book_view` is the template's BookView, swapped out
of the stack for a PDF). A TXT or CBR is converted first (converting.py, in a thread: a
spinner meanwhile) into an EPUB or CBZ in the cache; its place is the book's as for any
other. The header bar has the title and the
chapter, the sidebar button, a bookmark toggle, the Text and Layout popover (the reader-*
settings, applied as they change: the paper theme, typeface, size, spacing, margins, width,
justification, hyphenation, pages or scrolling, two pages, the publisher's styles; for a
PDF the paper, the zoom, Fit Width or Fit Page, pages or scrolling, right to left and the
cover alone, kept per book by reader_pdf.py, which adds Print…) and the main menu (Open
With… hands the file to another app). The
sidebar (an Adw.OverlaySplitView, docked when the window is wide) has the
contents (the current chapter selected), the highlights and bookmarks (click to go, edit a
note, change a colour, remove with Undo) and the search (results as they come, Ctrl+G and
Ctrl+Shift+G through them). The bottom bar has the scrubber (with a mark per chapter) and a
label that cycles, on click, through the percentage, the page, the time left in the chapter
and in the book (stats.time_left, else foliate-js's estimate): the reader-progress-label
setting. After a jump (the contents, a search result, a link, the scrubber) a button goes
back to where the reader was.

Selecting text opens a popover: a highlight colour, Add Note… (dialogs/note.py), Copy, Look
Up and Search; clicking a highlight opens it for that highlight, with Remove. In a wide
window a selected word's definition shows in the popover (widgets/lookup_popover.py: an
offline dictionary or Wiktionary, and Wikipedia), and Look Up shows a longer selection's
Wikipedia summary there; a narrow window's Look Up opens them in a bottom sheet. With a
speech engine installed (speech.py), Read Aloud (the main menu, Ctrl+Shift+S) reads from
the page shown, a sentence at a time, with a bar of controls (widgets/read_aloud.py).
Clicking the middle of the page hides or shows the bars; F11 is fullscreen, the bars hidden
until the pointer reaches the top edge.

A book opened without adding it (from Files, or Open File…: library.OPENED) has a banner
over the page offering Add to Library (app.keep_book), gone once it is added.

The keys are shortcuts.READER, read by the window's own key controller in the capture phase
(the web view takes no focus); Ctrl+scroll changes the text size (a PDF's zoom, as do the
bigger and smaller keys); Ctrl+C copies the selected text; mouse buttons 8 and 9 go back and
forward.

The place is saved with library.set_progress() a second after the last move and on closing
(the library marks an unread book as reading); the time spent is logged with
library.log_session() on closing and after five idle minutes (reading.SessionClock).
When signed in to a KOReader sync server, reader_sync.py offers another device's newer place
in a banner (Go There) and pushes this one (kosync.py; Sync Now in the menu).
Reaching the end marks the book finished, with Undo. A book whose file is missing gets a
status page with Locate File…; a format Bookcase cannot show (CB7), a PDF without Poppler, a
CBR without bsdtar and any book without WebKitGTK get a status page with Open With….
"""

import logging
import os
import threading
import time
from gettext import gettext as _
from gettext import ngettext
from xml.sax.saxutils import escape

from gi.repository import Adw, Gdk, Gio, GLib, Gtk, Pango

from . import converting, lookup, pdf_location, reader_pdf, reader_sync, reading, speech, stats
from .formats import FormatError
from .library import COLORS, OPENED, LibraryError
from .shortcuts import READER
from .widgets import book_view as book_view_module
from .widgets import lookup_popover, pdf_view, read_aloud, reader_theme
from .widgets.book_view import BookView  # noqa: F401  (the template's child)
from .widgets.util import connect_weak

log = logging.getLogger(__name__)

SCHEMA_ID = 'io.github.jackicus.Bookcase'
SAVE_DELAY_MS = 1000
FONT_SIZES = (10, 40)
FINISHED_FRACTION = 0.995
RETURN_HIDE_TURNS = 3  # pages turned after a jump before the return button goes
REVEAL_EDGE = 8  # pixels from the top edge that show the bars in fullscreen
MAX_SCALE_MARKS = 60
# The page keys a list or a popover uses too, when the keyboard has moved into it.
NAVIGATION = ('next', 'previous', 'scroll-down', 'scroll-up', 'start', 'end')


def color_names():
    return {'yellow': _('Yellow'), 'green': _('Green'), 'blue': _('Blue'), 'pink': _('Pink'),
            'purple': _('Purple')}


def theme_names():
    return {'auto': _('Follow System Style'), 'light': _('Light'), 'sepia': _('Sepia'),
            'dark': _('Dark'), 'black': _('Black'), 'custom': _('Custom')}


def open(app, book_id):  # noqa: A001  (the module's entry point, called as reader_window.open)
    """The reader window of a book: the open one raised, else a new one. Returns it."""
    for window in app.get_windows():
        if isinstance(window, ReaderWindow) and window.book_id == book_id:
            window.present()
            return window
    window = ReaderWindow(app, book_id)
    window.present()
    return window


def _keymap():
    """{(keyval, modifiers): name} of shortcuts.READER."""
    keys = {}
    for name, accels in READER.items():
        for accel in accels:
            ok, keyval, mods = Gtk.accelerator_parse(accel)
            if ok and keyval:
                keys[(Gdk.keyval_to_lower(keyval), int(mods))] = name
    return keys


@Gtk.Template(resource_path='/io/github/jackicus/Bookcase/reader_window.ui')
class ReaderWindow(Adw.ApplicationWindow):
    __gtype_name__ = 'BookcaseReaderWindow'

    toast_overlay = Gtk.Template.Child()
    split_view = Gtk.Template.Child()
    sidebar_stack = Gtk.Template.Child()
    contents_stack = Gtk.Template.Child()
    toc_list = Gtk.Template.Child()
    annotations_stack = Gtk.Template.Child()
    annotations_list = Gtk.Template.Child()
    search_entry = Gtk.Template.Child()
    search_stack = Gtk.Template.Child()
    search_list = Gtk.Template.Child()
    search_status = Gtk.Template.Child()
    toolbar_view = Gtk.Template.Child()
    header_bar = Gtk.Template.Child()
    sidebar_button = Gtk.Template.Child()
    window_title = Gtk.Template.Child()
    typography_button = Gtk.Template.Child()
    bookmark_button = Gtk.Template.Child()
    menu_button = Gtk.Template.Child()
    content_stack = Gtk.Template.Child()
    book_view = Gtk.Template.Child()
    status_page = Gtk.Template.Child()
    status_buttons = Gtk.Template.Child()
    return_revealer = Gtk.Template.Child()
    return_button = Gtk.Template.Child()
    return_content = Gtk.Template.Child()
    bottom_bar = Gtk.Template.Child()
    prev_button = Gtk.Template.Child()
    next_button = Gtk.Template.Child()
    progress_scale = Gtk.Template.Child()
    progress_button = Gtk.Template.Child()
    progress_label = Gtk.Template.Child()
    typography_popover = Gtk.Template.Child()
    theme_box = Gtk.Template.Child()
    font_group = Gtk.Template.Child()
    smaller_button = Gtk.Template.Child()
    size_label = Gtk.Template.Child()
    bigger_button = Gtk.Template.Child()
    line_height_row = Gtk.Template.Child()
    margin_row = Gtk.Template.Child()
    max_width_row = Gtk.Template.Child()
    layout_group = Gtk.Template.Child()
    two_pages_row = Gtk.Template.Child()
    justify_row = Gtk.Template.Child()
    hyphenate_row = Gtk.Template.Child()
    publisher_row = Gtk.Template.Child()

    def __init__(self, app, book_id):
        super().__init__(application=app)
        self.app = app
        self.library = app.library
        self.settings = getattr(app, 'settings', None) or Gio.Settings.new(SCHEMA_ID)
        self.book_id = book_id
        self.book = self.library.book(book_id)
        self.view = self.book_view
        self.file = None
        self.add_css_class('reader')
        self.set_default_size(self.settings.get_int('reader-width'),
                              self.settings.get_int('reader-height'))

        self._place = None  # the last relocated message
        self._loaded = None  # the loaded message
        self._section_fractions = []
        self._clock = None
        self._save_source = 0
        self._style_source = 0
        self._scrub_source = 0
        self._scrub_fraction = None
        self._return_cfi = None
        self._turns_since_jump = 0
        self._finished_marked = False
        self._annotations = []
        self._selection = None  # the selection or highlight the popover is for
        self._toc_rows = []
        self._chapter_labels = {}  # href -> the label shown for it, where not the book's
        self._announced_chapter = None  # the chapter a screen reader was last told of
        self._search_rows = []  # [(row, cfi)]
        self._search_index = -1
        self._search_count = 0
        self._chrome_visible = True
        self._peeking = False  # the bars shown by the pointer at the top edge, in fullscreen
        self._bookmark_cfi = None  # the bookmark on the page shown
        self._dialog_open = False
        self._closed = False
        self._rate = None  # the reader's pace in this book (fraction per second), or None
        self._keys = _keymap()
        self._pdf = None  # a PDF's reader_pdf.ReaderPdf: its kept layout, Print…
        self._fxl_zoom = None  # a fixed layout's zoom: {'fit', 'percent'}

        self._build_actions()
        self._build_controllers()
        self._build_typography()
        self._build_selection_popover()
        self._build_read_aloud()
        self._build_keep_banner()
        self._connect_signals()
        self._apply_theme_classes()
        self._update_title()
        self._open_book()
        self._sync = reader_sync.ReaderSync(self)  # kosync: the banner, pushes, the menu

    # -- setting up ------------------------------------------------------------------------

    def _build_actions(self):
        for name, callback in (('fullscreen', self._toggle_fullscreen),
                               ('open-with', self._launch_file),
                               ('go-to', self._go_to_location),
                               ('info', self._show_details),
                               ('export-highlights', self._export_highlights),
                               ('copy-highlights', self._copy_highlights),
                               ('close', self._close)):
            action = Gio.SimpleAction.new(name, None)
            action.connect('activate', _weak_callback(self, callback))
            self.add_action(action)
        for name, callback in (('edit-note', self._edit_note_of),
                               ('remove-annotation', self._remove_annotation_of),
                               ('annotation-color', self._color_of)):
            parameter = GLib.VariantType.new('(xs)' if name == 'annotation-color' else 'x')
            action = Gio.SimpleAction.new(name, parameter)
            action.connect('activate', _weak_callback(self, callback, argument=1))
            self.add_action(action)

    def _build_controllers(self):
        keys = Gtk.EventControllerKey(propagation_phase=Gtk.PropagationPhase.CAPTURE)
        connect_weak(keys, 'key-pressed', self._on_key)
        self.add_controller(keys)

        scroll = Gtk.EventControllerScroll(
            flags=Gtk.EventControllerScrollFlags.VERTICAL | Gtk.EventControllerScrollFlags.DISCRETE,
            propagation_phase=Gtk.PropagationPhase.CAPTURE)
        connect_weak(scroll, 'scroll', self._on_scroll)
        self.add_controller(scroll)

        buttons = Gtk.GestureClick(button=0, propagation_phase=Gtk.PropagationPhase.CAPTURE)
        connect_weak(buttons, 'pressed', self._on_button)
        self.add_controller(buttons)

        motion = Gtk.EventControllerMotion(propagation_phase=Gtk.PropagationPhase.CAPTURE)
        connect_weak(motion, 'motion', self._on_motion)
        self.add_controller(motion)

    def _connect_signals(self):
        self._connect_view(self.view)
        connect_weak(self.toc_list, 'row-activated', self._on_toc_activated)
        connect_weak(self.annotations_list, 'row-activated', self._on_annotation_row)
        connect_weak(self.search_list, 'row-activated', self._on_search_row)
        connect_weak(self.search_entry, 'search-changed', self._on_search_changed)
        connect_weak(self.search_entry, 'activate', self._on_search_activate)
        connect_weak(self.search_entry, 'stop-search', self._on_stop_search)
        connect_weak(self.bookmark_button, 'clicked', self._on_bookmark_clicked)
        connect_weak(self.return_button, 'clicked', self._on_return)
        self.prev_button.connect('clicked', _weak_callback(self, self._go_left))
        self.next_button.connect('clicked', _weak_callback(self, self._go_right))
        connect_weak(self.progress_scale, 'change-value', self._on_scrub)
        connect_weak(self.progress_button, 'clicked', self._on_progress_clicked)
        connect_weak(self, 'close-request', self._on_close_request)
        # Disconnected on its first call: a widget is disposed again when it is finalized,
        # and a Python handler run from the garbage collector's finalizing crashes.
        self._destroy_handler = connect_weak(self, 'destroy', self._release_popover)
        connect_weak(self, 'notify::fullscreened', self._on_fullscreened)
        connect_weak(self.split_view, 'notify::show-sidebar', self._on_sidebar_shown)
        # The library, the settings and the style manager outlive the window: held weakly,
        # disconnected on closing.
        self._library_handler = connect_weak(self.library, 'changed', self._on_library_changed)
        self._settings_handler = connect_weak(self.settings, 'changed', self._on_setting_changed)
        self._style_handler = connect_weak(Adw.StyleManager.get_default(), 'notify::dark',
                                           self._on_dark_changed)
        self._contrast_handler = connect_weak(Adw.StyleManager.get_default(),
                                              'notify::high-contrast', self._on_dark_changed)

    def _connect_view(self, view):
        connect_weak(view, 'loaded', self._on_loaded)
        connect_weak(view, 'toc-ready', self._on_toc)
        connect_weak(view, 'relocated', self._on_relocated)
        connect_weak(view, 'selection', self._on_selection)
        connect_weak(view, 'annotation-activated', self._on_annotation_activated)
        connect_weak(view, 'search-result', self._on_search_result)
        connect_weak(view, 'search-done', self._on_search_done)
        connect_weak(view, 'error', self._on_view_error)
        connect_weak(view, 'toggle-chrome', self._on_toggle_chrome)

    # -- opening ---------------------------------------------------------------------------

    def _open_book(self):
        book = self.book
        if book is None:
            self._show_status('dialog-question-symbolic', _('Book Not Found'),
                              _('This book is no longer in the library'))
            return
        files = self.library.files(self.book_id)
        self.file = self.library.reading_file(self.book_id)
        if self.file is not None and not os.path.exists(self.file.path):
            self.file = None
        if self.file is None:
            if files:
                missing = files[0]
                self._show_status(
                    'dialog-question-symbolic', _('File Not Found'),
                    _('The book was at {path}. Locate it to keep reading.').format(
                        path=missing.path),
                    [(_('Locate File…'), True, self._locate_file, {'missing': missing})])
            else:
                self._show_status('dialog-question-symbolic', _('No File to Read'),
                                  _('Only the details of this book are in the library'))
            return
        fmt = (self.file.format or '').lower()
        if fmt == 'pdf':
            if not pdf_view.available():
                self._show_status('dialog-warning-symbolic', _('Reading PDFs Needs Poppler'),
                                  _('Install Poppler and its GObject bindings to read PDFs '
                                    'in Bookcase'),
                                  [(_('Open With…'), True, self._launch_file)])
                return
            self._use_pdf_view()
            self._show_book(self.file.path, fmt)
            return
        if not reading.readable(fmt) and not converting.needs_conversion(fmt):
            self._show_status(
                'x-office-document-symbolic', _('Format Not Supported'),
                _('Bookcase cannot show {format} books').format(format=fmt.upper()),
                [(_('Open With…'), True, self._launch_file)])
            return
        if not book_view_module.available():
            self._show_status('dialog-warning-symbolic', _('Reading Needs WebKitGTK'),
                              _('Install WebKitGTK 6.0 to read books in Bookcase'),
                              [(_('Open With…'), True, self._launch_file)])
            return
        if converting.needs_conversion(fmt):
            self._convert(self.file.path, fmt)
            return
        self._show_book(self.file.path, fmt)

    def _show_book(self, path, fmt):
        """Open the file in the view, at the saved place."""
        book = self.book
        self._annotations = self.library.annotations(self.book_id)
        self._refresh_annotation_list()
        self.content_stack.set_visible_child_name('book')
        location, fraction = book.location or None, book.progress or None
        if (fraction or 0) >= FINISHED_FRACTION and book.status != 'finished':
            # read to the end, then marked unread or reading again: read again from the start
            location = fraction = None
        self.view.open(path, fmt, location=location, fraction=fraction,
                       annotations=self._highlights(), bookmarks=self._bookmarks(),
                       style=self._style())
        self._clock = reading.SessionClock(fraction or 0.0, time.time())
        self._update_rate()

    def _use_pdf_view(self):
        """Show the book in a PdfView (Poppler) in place of the BookView (WebKit)."""
        if self.is_pdf:
            return
        view = pdf_view.PdfView()
        self.content_stack.remove(self.book_view)
        self.content_stack.add_named(view, 'book')
        self.view = view
        self._connect_view(view)
        connect_weak(view, 'zoom-changed', self._on_zoom_changed)
        popover = self._selection_popover
        popover.unparent()
        popover.set_parent(view)
        self._show_pdf_controls()
        self._pdf = reader_pdf.ReaderPdf(self)

    @property
    def is_pdf(self):
        return self.view is not self.book_view

    def _convert(self, path, fmt):
        """A TXT or CBR is converted (converting.py, in a thread) before it opens."""
        self.content_stack.set_visible_child_name('loading')
        book = self.book
        title, language = book.title, book.language or ''
        author = ', '.join(getattr(book, 'authors', None) or ())
        done = _weak_callback(self, self._converted, argument=0)

        def work():
            try:
                result = converting.prepare(path, fmt, title=title, author=author,
                                            language=language)
            except (FormatError, OSError) as error:
                result = error
            GLib.idle_add(done, result)

        threading.Thread(target=work, name='bookcase-convert', daemon=True).start()

    def _converted(self, result):
        if self._closed:
            return GLib.SOURCE_REMOVE
        if isinstance(result, Exception):
            log.warning('converting %s: %s', self.file.path if self.file else '?', result)
            needs_bsdtar = 'bsdtar' in str(result)
            self._show_status(
                'dialog-warning-symbolic',
                _('Reading CBR Comics Needs bsdtar') if needs_bsdtar
                else _('This Book Cannot Be Opened'),
                _('Install libarchive (bsdtar) to read CBR comics in Bookcase') if needs_bsdtar
                else _('The file may be damaged, or in a format Bookcase cannot read'),
                [(_('Open With…'), True, self._launch_file)])
            return GLib.SOURCE_REMOVE
        path, fmt = result
        self._show_book(path, fmt)
        return GLib.SOURCE_REMOVE

    def _show_status(self, icon, title, description, buttons=()):
        self.status_page.set_icon_name(icon)
        self.status_page.set_title(title)
        self.status_page.set_description(description)
        while (child := self.status_buttons.get_first_child()) is not None:
            self.status_buttons.remove(child)
        for label, suggested, callback, *kwargs in buttons:
            button = Gtk.Button(label=label, halign=Gtk.Align.CENTER)
            button.add_css_class('pill')
            if suggested:
                button.add_css_class('suggested-action')
            button.connect('clicked', _weak_callback(self, callback, **(kwargs or [{}])[0]))
            self.status_buttons.append(button)
        self.content_stack.set_visible_child_name('status')
        for widget in (self.bookmark_button, self.typography_button, self.sidebar_button):
            widget.set_sensitive(False)
        self.bottom_bar.set_visible(False)

    def _locate_file(self, missing):
        dialog = Gtk.FileDialog(title=_('Locate File'), modal=True)
        ref = self.weak_ref()

        def done(dialog, result):
            window = ref()
            try:
                gfile = dialog.open_finish(result)
            except GLib.Error:
                return
            if window is None or gfile is None or gfile.get_path() is None:
                return
            try:
                window.library.set_file_path(missing.id, gfile.get_path())
            except LibraryError as error:
                window.toast(str(error))
                return
            window.book = window.library.book(window.book_id)
            for widget in (window.bookmark_button, window.typography_button,
                           window.sidebar_button):
                widget.set_sensitive(True)
            window.bottom_bar.set_visible(True)
            window._open_book()

        dialog.open(self, None, done)

    def _launch_file(self):
        files = self.library.files(self.book_id)
        if not files:
            return
        launcher = Gtk.FileLauncher.new(Gio.File.new_for_path(files[0].path))
        launcher.set_always_ask(True)
        launcher.launch(self, None, None)

    def _highlights(self):
        return [{'cfi': a.location, 'color': a.color} for a in self._annotations
                if a.kind == 'highlight' and a.location]

    def _bookmarks(self):
        return [a.location for a in self._annotations if a.kind == 'bookmark' and a.location]

    # -- the style -------------------------------------------------------------------------

    def _style(self):
        dark = Adw.StyleManager.get_default().get_dark()
        title = self.book.title if self.book else ''
        style = reading.build_style(self._setting, dark, title,
                                    Adw.StyleManager.get_default().get_high_contrast())
        if self.is_pdf:
            style['flow'] = 'scrolled' if self._layout_key_value() else 'paginated'
        return style

    def _layout_key(self):
        """The setting of the Pages/Scrolled switch: PDFs have their own."""
        return 'reader-pdf-scrolled' if self.is_pdf else 'reader-scrolled'

    def _layout_key_value(self):
        if self._pdf is not None:
            return self._pdf.scrolled()  # kept per PDF
        return self.settings.get_boolean(self._layout_key())

    def _setting(self, key):
        return self.settings.get_value(key).unpack() if key not in (
            'reader-theme', 'reader-font') else self.settings.get_string(key)

    def _apply_style(self):
        self._style_source = 0
        self.view.set_style(self._style())
        self._apply_theme_classes()
        return GLib.SOURCE_REMOVE

    def _apply_theme_classes(self):
        manager = Adw.StyleManager.get_default()
        dark = manager.get_dark()
        name = reading.theme_colors(self.settings.get_string('reader-theme'), dark,
                                    high_contrast=manager.get_high_contrast())['name']
        for theme in (*reading.THEMES, 'custom'):
            self.toolbar_view.remove_css_class(f'theme-{theme}')
        self.toolbar_view.add_css_class('reader-page')
        self.toolbar_view.add_css_class(f'theme-{name}')

    def _on_setting_changed(self, _settings, key):
        if (key in reading.STYLE_KEYS or key == 'reader-pdf-scrolled') \
                and not self._style_source:
            self._style_source = GLib.idle_add(self._apply_style)
        if key == 'reader-font-size':
            self._update_size_label()
        elif key == self._layout_key():
            self._update_layout_group()
        elif key == 'reader-theme':
            self._update_theme_chips()
        elif key == 'reader-custom-theme':
            reader_theme.apply_css(self.settings)
        elif key == 'reader-progress-label':
            self._update_progress_label()

    def _on_dark_changed(self, _manager, _pspec):
        if not self._style_source:
            self._style_source = GLib.idle_add(self._apply_style)

    # -- the typography popover ------------------------------------------------------------

    def _build_typography(self):
        self._theme_chips = {}
        group = None
        names = theme_names()
        reader_theme.apply_css(self.settings)
        for name in (*reading.THEME_NAMES, 'custom'):
            chip = Gtk.ToggleButton(tooltip_text=names[name], group=group,
                                    width_request=36, height_request=36)
            chip.update_property([Gtk.AccessibleProperty.LABEL], [names[name]])
            chip.add_css_class('reader-theme-chip')
            chip.add_css_class(f'theme-{name}')
            check = Gtk.Image(icon_name='object-select-symbolic', can_target=False)
            chip.set_child(check)
            group = group or chip
            chip.connect('toggled', _weak_callback(self, self._on_theme_chip, argument=0,
                                                   name=name))
            self.theme_box.append(chip)
            self._theme_chips[name] = chip
        self._update_theme_chips()
        self.custom_theme_button = Gtk.Button(label=_('Custom Colours…'),
                                              halign=Gtk.Align.CENTER)
        self.custom_theme_button.add_css_class('flat')
        self.custom_theme_button.connect('clicked',
                                         _weak_callback(self, self._edit_custom_theme))
        self.theme_box.get_parent().insert_child_after(self.custom_theme_button,
                                                       self.theme_box)

        flags = Gio.SettingsBindFlags.DEFAULT
        self.settings.bind('reader-font', self.font_group, 'active-name', flags)
        self.settings.bind('reader-line-height', self.line_height_row, 'value', flags)
        self.settings.bind('reader-margin', self.margin_row, 'value', flags)
        self.settings.bind('reader-max-width', self.max_width_row, 'value', flags)
        self.settings.bind('reader-two-pages', self.two_pages_row, 'active', flags)
        self.settings.bind('reader-justify', self.justify_row, 'active', flags)
        self.settings.bind('reader-hyphenate', self.hyphenate_row, 'active', flags)
        self.settings.bind('reader-publisher-styles', self.publisher_row, 'active', flags)
        self._update_layout_group()
        connect_weak(self.layout_group, 'notify::active-name', self._on_layout_changed)
        self.smaller_button.connect('clicked',
                                    _weak_callback(self, self._change_font_size, step=-1))
        self.bigger_button.connect('clicked',
                                   _weak_callback(self, self._change_font_size, step=1))
        self._update_size_label()

    def _update_theme_chips(self):
        current = self.settings.get_string('reader-theme')
        for name, chip in self._theme_chips.items():
            chip.set_active(name == current)
            chip.get_child().set_visible(name == current)

    def _on_theme_chip(self, chip, name):
        chip.get_child().set_visible(chip.get_active())
        if chip.get_active() and self.settings.get_string('reader-theme') != name:
            self.settings.set_string('reader-theme', name)

    def _edit_custom_theme(self):
        self.typography_popover.popdown()
        reader_theme.present(self, self.settings)

    def _update_layout_group(self):
        name = 'scrolled' if self._layout_key_value() else 'paginated'
        if self.layout_group.get_active_name() != name:
            self.layout_group.set_active_name(name)
        # scrolling: one column (a fixed layout's pages turn, whichever)
        self.two_pages_row.set_sensitive(name == 'paginated' or self._fixed_layout())

    def _on_layout_changed(self, group, _pspec):
        scrolled = group.get_active_name() == 'scrolled'
        if self._pdf is not None:
            self._pdf.set_scrolled(scrolled)
        elif self._layout_key_value() != scrolled:
            self.settings.set_boolean(self._layout_key(), scrolled)

    def _show_pdf_controls(self):
        """The Text and Layout popover for a PDF: the paper, the zoom (out, in, fit width
        or page), pages or scrolling and two pages; no typeface or spacing."""
        self.typography_button.set_tooltip_text(_('Zoom and Layout'))
        for widget in (self.font_group, self.line_height_row.get_parent(), self.justify_row,
                       self.hyphenate_row, self.publisher_row):
            widget.set_visible(False)
        self._add_zoom_controls()
        self.two_pages_row.set_subtitle(_('Side by side when the window is wide, the first '
                                          'page alone'))
        self._update_layout_group()
        self._update_size_label()

    def _add_zoom_controls(self):
        """The size buttons as Zoom Out and Zoom In, and Fit Width or Fit Page under them
        (a PDF, a fixed layout)."""
        if getattr(self, '_fit_group', None) is not None:
            return
        for button, icon, tooltip in ((self.smaller_button, 'zoom-out-symbolic', _('Zoom Out')),
                                      (self.bigger_button, 'zoom-in-symbolic', _('Zoom In'))):
            button.set_icon_name(icon)
            button.set_tooltip_text(tooltip)
            button.remove_css_class('reader-size-smaller')
            button.remove_css_class('reader-size-bigger')
        self.size_label.set_tooltip_text(_('Zoom'))
        self._fit_group = Adw.ToggleGroup()
        for name, label in (('width', _('Fit Width')), ('page', _('Fit Page'))):
            self._fit_group.add(Adw.Toggle(name=name, label=label))
        self._sync_fit_group()
        connect_weak(self._fit_group, 'notify::active-name', self._on_fit_changed)
        size_box = self.size_label.get_parent()
        size_box.get_parent().insert_child_after(self._fit_group, size_box)

    def _show_fixed_layout_controls(self):
        """A fixed layout (a comic, a picture book) has pages drawn as they are: the Text and
        Layout popover has the paper, the zoom (out, in, fit width or page: the size keys and
        Ctrl+scroll zoom too) and two pages; nothing reads aloud."""
        self.typography_button.set_tooltip_text(_('Zoom and Layout'))
        for widget in (self.font_group, self.line_height_row.get_parent(), self.layout_group,
                       self.justify_row, self.hyphenate_row, self.publisher_row):
            widget.set_visible(False)
        self._fxl_zoom = (self._loaded or {}).get('zoom') or {'fit': 'page', 'percent': 100}
        self._add_zoom_controls()
        self.two_pages_row.set_subtitle(_('Side by side when the window is wider than tall'))
        self.two_pages_row.set_sensitive(True)
        self._update_size_label()
        action = self.lookup_action('read-aloud')
        if action is not None:
            action.set_enabled(False)

    def _fixed_layout(self):
        return bool((self._loaded or {}).get('fixedLayout')) and not self.is_pdf

    def _on_fit_changed(self, group, _pspec):
        name = group.get_active_name()
        if name in ('width', 'page') and self._fixed_layout():
            if (self._fxl_zoom or {}).get('fit') != name:
                self._zoom_fixed_layout('fit-' + name)
        elif name in ('width', 'page') and self.view.fit != name:
            self.view.set_fit(name)

    def _zoom_fixed_layout(self, action):
        """A fixed layout's zoom: 'in', 'out', 'fit-page' or 'fit-width' (BookView.zoom)."""
        zoom = getattr(self.view, 'zoom', None)
        if zoom is not None:
            zoom(action, _weak_callback(self, self._on_fixed_zoom, argument=0))

    def _on_fixed_zoom(self, state):
        if state:
            self._fxl_zoom = state
            self._on_zoom_changed(self.view)

    def _on_zoom_changed(self, _view):
        self._update_size_label()
        self._sync_fit_group()

    def _sync_fit_group(self):
        """Fit Width or Fit Page active as the view fits; neither for the automatic zoom
        or a percentage."""
        fit = (self._fxl_zoom or {}).get('fit') if self._fixed_layout() else self.view.fit
        if fit in ('width', 'page'):
            if self._fit_group.get_active_name() != fit:
                self._fit_group.set_active_name(fit)
        elif self._fit_group.get_active() != Gtk.INVALID_LIST_POSITION:
            self._fit_group.set_active(Gtk.INVALID_LIST_POSITION)

    def _update_size_label(self):
        if self.is_pdf:
            percent = self.view.zoom_percent
            # Translators: a percentage ("45%").
            self.size_label.set_label(_('{percent}%').format(percent=percent))
            self.smaller_button.set_sensitive(percent > round(pdf_view.ZOOM_STEPS[0] * 100))
            self.bigger_button.set_sensitive(percent < round(pdf_view.ZOOM_STEPS[-1] * 100))
            return
        if self._fixed_layout() and self._fxl_zoom:
            percent = self._fxl_zoom.get('percent') or 100
            # Translators: a percentage ("45%").
            self.size_label.set_label(_('{percent}%').format(percent=percent))
            self.smaller_button.set_sensitive(self._fxl_zoom.get('fit') != 'page')
            self.bigger_button.set_sensitive(percent < 790)
            return
        size = self.settings.get_int('reader-font-size')
        self.size_label.set_label(_('{size} px').format(size=size))
        self.smaller_button.set_sensitive(size > FONT_SIZES[0])
        self.bigger_button.set_sensitive(size < FONT_SIZES[1])

    def _change_font_size(self, step=0):
        """Bigger or smaller text (step 1 or -1; 0 resets it); a PDF zooms instead (0: the
        automatic zoom)."""
        if self.is_pdf:
            if step > 0:
                self.view.zoom_in()
            elif step < 0:
                self.view.zoom_out()
            else:
                self.view.set_fit('auto')
            return
        if self._fixed_layout():  # no text to size: the pages zoom
            self._zoom_fixed_layout('in' if step > 0 else 'out' if step < 0 else 'fit-page')
            return
        if step == 0:
            self.settings.reset('reader-font-size')
            return
        size = self.settings.get_int('reader-font-size') + step
        self.settings.set_int('reader-font-size', max(FONT_SIZES[0], min(FONT_SIZES[1], size)))

    # -- the page's messages ---------------------------------------------------------------

    def _on_loaded(self, _view, loaded):
        self._loaded = loaded
        self._section_fractions = loaded.get('sectionFractions') or []
        scale = self.progress_scale
        scale.clear_marks()
        if 1 < len(self._section_fractions) <= MAX_SCALE_MARKS:
            for fraction in self._section_fractions[1:]:
                scale.add_mark(min(1.0, fraction), Gtk.PositionType.BOTTOM, None)
        if loaded.get('fixedLayout') and not self.is_pdf:
            self._show_fixed_layout_controls()
        rtl = loaded.get('dir') == 'rtl'
        scale.set_inverted(rtl)
        # The arrows turn left and right: in a right-to-left book, left is forward.
        self.prev_button.set_tooltip_text(_('Next Page') if rtl else _('Previous Page'))
        self.next_button.set_tooltip_text(_('Previous Page') if rtl else _('Next Page'))
        self._find_imported_highlights()

    def _comic(self):
        return self.file is not None and (self.file.format or '').lower() in ('cbz', 'cbr')

    def _on_toc(self, _view, toc):
        while (row := self.toc_list.get_first_child()) is not None:
            self.toc_list.remove(row)
        self._toc_rows = []
        if self._comic():
            # A comic's contents are its pictures' file names ("012.jpg"): pages read better.
            toc = [{**item, 'label': _('Page {page}').format(page=number)}
                   for number, item in enumerate(toc, 1)]
            self._chapter_labels = {item['href']: item['label'] for item in toc}

        def add(items, depth):
            for item in items:
                label = Gtk.Label(label=item.get('label') or _('Untitled'), xalign=0,
                                  ellipsize=Pango.EllipsizeMode.END,
                                  tooltip_text=item.get('label') or None,
                                  margin_start=6 + 18 * depth, margin_top=4,
                                  margin_bottom=4)
                if depth:
                    label.add_css_class('dimmed')
                row = Gtk.ListBoxRow(child=label)
                row.href = item.get('href') or ''
                self.toc_list.append(row)
                self._toc_rows.append(row)
                add(item.get('subitems') or [], depth + 1)

        add(toc, 0)
        self.contents_stack.set_visible_child_name('list' if self._toc_rows else 'empty')
        if self._place:
            self._select_toc(self._place)

    def _on_relocated(self, _view, place):
        if place.get('fraction') is None or self._closed:
            return  # before the first layout, or after closing (saved then)
        previous = self._place
        self._place = place
        fraction = place['fraction']
        now = time.time()
        if self._clock is not None:
            ended = self._clock.activity(now, fraction)
            if ended is not None:
                self._log_session(ended)
        self._schedule_save()
        if self._scrub_source == 0:
            self.progress_scale.set_value(fraction)
        self._update_progress_label()
        self._update_title()
        self._select_toc(place)
        self._set_bookmark_state(place.get('bookmark'))
        if place.get('jumpedFrom') and previous is not None \
                and place['jumpedFrom'] != place.get('cfi'):  # not to the page shown
            self._show_return(place['jumpedFrom'], previous)
        elif place.get('reason') in ('page', 'scroll', 'snap') and self._return_cfi:
            self._turns_since_jump += 1
            if self._turns_since_jump >= RETURN_HIDE_TURNS:
                self._hide_return()
        # Reaching the end, not opening there (a book marked unread or reading again stays so).
        if place.get('atEnd') and fraction >= 0.5 and previous is not None:
            self._mark_finished()
        if place.get('ttsMoved') and self._read_aloud is not None:
            self._read_aloud.moved()  # reading aloud goes on from the new page
        self._sync.relocated(place)

    def _on_view_error(self, _view, message):
        log.warning('the book could not be opened: %s', message)
        self._clock = None  # no reading session in a book that did not open
        self._show_status('dialog-warning-symbolic', _('This Book Cannot Be Opened'),
                          _('The file may be damaged, or in a format Bookcase cannot read'),
                          [(_('Open in Another App'), False, self._launch_file)])

    # -- progress, sessions, finishing -----------------------------------------------------

    def _schedule_save(self):
        if self._save_source:
            GLib.source_remove(self._save_source)
        self._save_source = GLib.timeout_add(SAVE_DELAY_MS, self._save_progress)

    def _save_progress(self):
        self._save_source = 0
        place = self._place
        if place is None or self.book is None:
            return GLib.SOURCE_REMOVE
        try:
            self.library.set_progress(self.book_id, place['fraction'], place.get('cfi') or '')
        except (LibraryError, OSError) as error:
            log.warning('saving the place in book %s: %s', self.book_id, error)
        return GLib.SOURCE_REMOVE

    def _log_session(self, session):
        try:
            self.library.log_session(self.book_id, session.started, session.seconds,
                                     session.start_fraction, session.end_fraction)
        except (LibraryError, OSError) as error:
            log.warning('logging a reading session: %s', error)
        self._update_rate()

    def _update_rate(self):
        try:
            left = stats.time_left(self.library, self.book_id, 0.0)
        except Exception:  # noqa: BLE001  (an estimate is never worth failing over)
            log.exception('estimating the reading speed')
            left = None
        self._rate = 1.0 / left.book if left is not None and left.book > 0 else None

    def _mark_finished(self):
        if self._finished_marked:
            return
        self._finished_marked = True
        book = self.library.book(self.book_id)
        if book is None or book.status == 'finished':
            return
        try:
            self.library.set_status([self.book_id], 'finished')
        except LibraryError as error:
            log.warning('marking book %s finished: %s', self.book_id, error)
            return
        self.toast(_('Marked as finished'), undo=True)

    # -- the bottom bar --------------------------------------------------------------------

    def _times_left(self, place):
        """(chapter, book) minutes left at the reader's pace, or (None, None)."""
        if self._rate is None:
            return None, None
        fraction = place.get('fraction') or 0.0
        section = place.get('section') or {}
        index = section.get('current')
        chapter = None
        if index is not None and index + 1 < len(self._section_fractions):
            end = self._section_fractions[index + 1]
            chapter = max(0.0, end - fraction) / self._rate / 60
        return chapter, (1.0 - fraction) / self._rate / 60

    def _update_progress_label(self):
        place = self._place
        if place is None:
            self.progress_label.set_label('')
            return
        kind = self.settings.get_string('reader-progress-label')
        chapter, book = self._times_left(place)
        if self._scrub_fraction is not None:
            text = reading.progress_text('percent', {'fraction': self._scrub_fraction})
        else:
            text = reading.progress_text(kind, place, chapter, book)
        self.progress_label.set_label(text)
        self.progress_scale.set_tooltip_text(
            reading.progress_text('percent', place))

    def _on_progress_clicked(self, _button):
        kind = self.settings.get_string('reader-progress-label')
        self.settings.set_string('reader-progress-label', reading.next_label(kind))

    def _on_scrub(self, _scale, _scroll, value):
        self._scrub_fraction = max(0.0, min(1.0, value))
        self._update_progress_label()
        if self._scrub_source:
            GLib.source_remove(self._scrub_source)
        self._scrub_source = GLib.timeout_add(150, self._scrub_done)
        return False

    def _scrub_done(self):
        self._scrub_source = 0
        fraction, self._scrub_fraction = self._scrub_fraction, None
        if fraction is not None:
            self.view.go_to_fraction(fraction)
        return GLib.SOURCE_REMOVE

    def _update_title(self):
        title = self.book.title if self.book else _('Bookcase')
        self.set_title(title)
        self.window_title.set_title(title)
        chapter = (self._place or {}).get('chapter') or {}
        label = self._chapter_labels.get(chapter.get('href'), chapter.get('label'))
        self.window_title.set_subtitle(label or '')
        # A screen reader hears the chapter when it changes (not every page: the page is
        # WebKit's to read), not the one the book opens at.
        if label and label != self._announced_chapter:
            if self._announced_chapter is not None:
                self.announce(label, Gtk.AccessibleAnnouncementPriority.MEDIUM)
            self._announced_chapter = label

    # -- the return button -----------------------------------------------------------------

    def _show_return(self, cfi, previous):
        self._return_cfi = cfi
        self._turns_since_jump = 0
        if previous.get('pages'):  # a PDF's page, without "of 300"
            label = _('Page {page}').format(page=previous['page'])
        else:
            label = reading.progress_text('page' if previous.get('page') else 'percent',
                                          previous)
        self.return_content.set_label(_('Back to {place}').format(place=label))
        self.return_revealer.set_reveal_child(True)

    def _hide_return(self):
        self._return_cfi = None
        self.return_revealer.set_reveal_child(False)

    def _on_return(self, _button):
        cfi = self._return_cfi
        self._hide_return()
        if cfi:
            self.view.go_to(cfi)
            # going back is a jump too: hide the button it brings
            GLib.timeout_add(400, _weak_callback(self, self._hide_return_once))

    def _hide_return_once(self):
        self._hide_return()
        return GLib.SOURCE_REMOVE

    # -- contents --------------------------------------------------------------------------

    def _select_toc(self, place):
        chapter = place.get('chapter') or {}
        href = chapter.get('href')
        row = next((row for row in self._toc_rows if href and row.href == href), None)
        if row is None:
            self.toc_list.unselect_all()
            return
        if self.toc_list.get_selected_row() is not row:
            self.toc_list.select_row(row)

    def _on_toc_activated(self, _list, row):
        if row.href:
            self.view.go_to(row.href)
        if self.split_view.get_collapsed():
            self.split_view.set_show_sidebar(False)

    def _close(self):
        self.close()

    def _go_left(self):
        self.view.go_left()

    def _go_right(self):
        self.view.go_right()

    def _show_sidebar(self, page):
        shown = self.sidebar_stack.get_visible_child_name()
        if self.split_view.get_show_sidebar() and shown == page:
            self.split_view.set_show_sidebar(False)
            return
        self.sidebar_stack.set_visible_child_name(page)
        self.split_view.set_show_sidebar(True)
        if page == 'search':
            self.search_entry.grab_focus()

    def _on_sidebar_shown(self, split_view, _pspec):
        if not split_view.get_show_sidebar():
            self.set_focus(None)

    # -- highlights and bookmarks ----------------------------------------------------------

    def _on_library_changed(self, _library, kind):
        if kind == 'annotations':
            self._annotations = self.library.annotations(self.book_id)
            self._refresh_annotation_list()
            self.view.set_annotations(self._highlights())
            self.view.set_bookmarks(self._bookmarks(),
                                         _weak_callback(self, self._set_bookmark_state,
                                                        argument=0))
        elif kind == 'books':
            book = self.library.book(self.book_id)
            if book is not None:
                self.book = book
                self._update_title()
                self._keep_banner.set_revealed(book.source == OPENED and not self._keeping)

    def _build_keep_banner(self):
        """A book opened without adding (library.OPENED: from Files, or Open File…)
        offers Add to Library over the page (app.keep_book)."""
        self._keeping = False  # Add to Library clicked
        self._keep_banner = Adw.Banner(
            title=_('This book is not in your library'), button_label=_('_Add to Library'),
            revealed=self.book is not None and self.book.source == OPENED, use_markup=False,
            button_style=Adw.BannerButtonStyle.SUGGESTED)
        self._keep_banner.connect('button-clicked', _weak_callback(self, self._keep_book))
        self.toolbar_view.add_top_bar(self._keep_banner)

    def _keep_book(self):
        keep = getattr(self.app, 'keep_book', None)
        if keep is not None:
            # gone at once, not when the copy is made: a second click would add it twice
            self._keeping = True
            self._keep_banner.set_revealed(False)
            keep(self.book_id)

    def _refresh_annotation_list(self):
        listbox = self.annotations_list
        while (row := listbox.get_first_child()) is not None:
            listbox.remove(row)
        for annotation in self._annotations:
            listbox.append(self._annotation_row(annotation))
        self.annotations_stack.set_visible_child_name('list' if self._annotations else 'empty')

    def _annotation_row(self, annotation):
        box = Gtk.Box(spacing=12, margin_top=6, margin_bottom=6)
        if annotation.kind == 'highlight':
            mark = Gtk.Box(width_request=4, valign=Gtk.Align.FILL)
            mark.add_css_class('reader-highlight-mark')
            mark.add_css_class(f'color-{annotation.color}')
        else:
            mark = Gtk.Image(icon_name='user-bookmarks-symbolic', valign=Gtk.Align.START,
                             accessible_role=Gtk.AccessibleRole.PRESENTATION)
        box.append(mark)
        texts = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=4, hexpand=True)
        if annotation.kind == 'highlight':
            text = Gtk.Label(label=' '.join(annotation.text.split()) or _('Highlight'),
                             xalign=0, wrap=True,
                             wrap_mode=Pango.WrapMode.WORD_CHAR, lines=4,
                             ellipsize=Pango.EllipsizeMode.END, max_width_chars=30)
        else:
            text = Gtk.Label(label=annotation.text or _('Bookmark'), xalign=0,
                             ellipsize=Pango.EllipsizeMode.END)
        texts.append(text)
        if annotation.note:
            note = Gtk.Label(label=annotation.note, xalign=0, wrap=True, lines=3,
                             wrap_mode=Pango.WrapMode.WORD_CHAR,
                             ellipsize=Pango.EllipsizeMode.END, max_width_chars=30)
            note.add_css_class('dimmed')
            texts.append(note)
        page = pdf_location.parse(annotation.location)
        where = Gtk.Label(label=_('Page {page}').format(page=page.page) if page is not None
                          else reading.progress_text('percent',
                                                     {'fraction': annotation.position}),
                          xalign=0)
        where.add_css_class('caption')
        where.add_css_class('dimmed')
        where.add_css_class('numeric')
        texts.append(where)
        box.append(texts)

        menu = Gio.Menu()
        if annotation.kind == 'highlight':
            menu.append(_('Edit Note…') if annotation.note else _('Add Note…'),
                        f'win.edit-note({annotation.id})')
            colors = Gio.Menu()
            for color, name in color_names().items():
                colors.append(name, f"win.annotation-color(({annotation.id}, '{color}'))")
            menu.append_submenu(_('Colour'), colors)
        menu.append(_('Remove'), f'win.remove-annotation({annotation.id})')
        button = Gtk.MenuButton(icon_name='view-more-symbolic', menu_model=menu,
                                valign=Gtk.Align.START, tooltip_text=_('More'))
        button.add_css_class('flat')
        box.append(button)
        row = Gtk.ListBoxRow(child=box)
        row.annotation_id = annotation.id
        row.location = annotation.location
        row.position = annotation.position
        return row

    def _find_imported_highlights(self):
        """Highlights imported without a place (a Kindle's): the page finds their text and
        the library keeps the CFI (not an undo step), which draws them."""
        items = [{'id': a.id, 'text': a.text} for a in self._annotations
                 if a.kind == 'highlight' and not a.location and a.text.strip()]
        find = getattr(self.view, 'find_texts', None)
        if items and find is not None:
            find(items, _weak_callback(self, self._on_texts_found, argument=0))

    def _on_texts_found(self, found):
        for item in found or ():
            try:
                self.library.set_annotation_location(int(item['id']), item['cfi'],
                                                     item.get('fraction'))
            except (KeyError, TypeError, ValueError):
                log.warning('a found highlight without its place: %r', item)

    def _export_highlights(self):
        from .dialogs import highlights

        highlights.export_book(self.app, self, self.book_id, toast=self.toast)

    def _copy_highlights(self):
        from .dialogs import highlights

        if highlights.copy_book(self.app, self, self.book_id):
            self.toast(_('Highlights copied as Markdown'))

    def _annotation(self, annotation_id):
        return next((a for a in self._annotations if a.id == annotation_id), None)

    def _on_annotation_row(self, _list, row):
        if row.location:
            self.view.go_to(row.location)
        elif row.position:  # an imported highlight not found in the book: about there
            self.view.go_to_fraction(row.position)
        if self.split_view.get_collapsed():
            self.split_view.set_show_sidebar(False)

    def _edit_note_of(self, annotation_id):
        annotation = self._annotation(annotation_id)
        if annotation is not None:
            self._edit_note(annotation.text, annotation.note,
                            lambda note: self._update_annotation(annotation_id, note=note))

    def _edit_note(self, text, note, done):
        from .dialogs import note as note_dialog

        note_dialog.present(self.app, self, text, note, done)

    def _color_of(self, value):
        annotation_id, color = value
        self._update_annotation(annotation_id, color=color)

    def _update_annotation(self, annotation_id, note=None, color=None):
        try:
            self.library.update_annotation(annotation_id, note=note, color=color)
        except LibraryError as error:
            self.toast(str(error))

    def _remove_annotation_of(self, annotation_id):
        annotation = self._annotation(annotation_id)
        if annotation is None:
            return
        try:
            self.library.remove_annotation(annotation_id)
        except LibraryError as error:
            self.toast(str(error))
            return
        self.toast(_('Highlight removed') if annotation.kind == 'highlight'
                   else _('Bookmark removed'), undo=True)

    def _set_bookmark_state(self, cfi):
        self._bookmark_cfi = cfi
        self.bookmark_button.set_active(bool(cfi))
        self.bookmark_button.set_tooltip_text(_('Remove Bookmark') if cfi
                                              else _('Bookmark This Page'))

    def _on_bookmark_clicked(self, _button):
        self._toggle_bookmark()

    def _toggle_bookmark(self):
        place = self._place
        if place is None:
            return
        cfi = self._bookmark_cfi
        if cfi:
            annotation = next((a for a in self._annotations
                               if a.kind == 'bookmark' and a.location == cfi), None)
            if annotation is not None:
                self._remove_annotation_of(annotation.id)
            return
        start = place.get('start') or place.get('cfi')
        if not start:
            return
        chapter = place.get('chapter') or {}
        chapter = self._chapter_labels.get(chapter.get('href'), chapter.get('label')) \
            or self.book.title
        try:
            self.library.add_annotation(self.book_id, 'bookmark', start, text=chapter,
                                        position=place.get('fraction') or 0.0)
        except LibraryError as error:
            self.toast(str(error))

    # -- reading aloud ---------------------------------------------------------------------

    def _build_read_aloud(self):
        """Read Aloud (widgets/read_aloud.py): its bar, action and menu item, only when a
        speech engine is installed."""
        self._read_aloud = None
        if not read_aloud.available(self.book_view):
            return
        self._read_aloud = read_aloud.ReadAloudBar(self.book_view, speech.engine(),
                                                   self.settings, self._lookup_language())
        self.toolbar_view.add_bottom_bar(self._read_aloud)
        action = Gio.SimpleAction.new('read-aloud', None)
        action.connect('activate', _weak_callback(self, self._toggle_read_aloud))
        self.add_action(action)
        section = self.menu_button.get_menu_model().get_item_link(0, Gio.MENU_LINK_SECTION)
        if isinstance(section, Gio.Menu):
            section.append(_('Read Aloud'), 'win.read-aloud')

    def _toggle_read_aloud(self):
        if self._read_aloud is None or self.content_stack.get_visible_child_name() != 'book' \
                or self._fixed_layout():
            return False
        self._read_aloud.toggle()
        return True

    # -- the selection popover -------------------------------------------------------------

    def _build_selection_popover(self):
        popover = Gtk.Popover(position=Gtk.PositionType.TOP, autohide=True)
        popover.add_css_class('reader-selection')
        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=6)
        colors = Gtk.Box(spacing=6, halign=Gtk.Align.CENTER)
        self._color_buttons = {}
        names = color_names()
        for color in COLORS:
            button = Gtk.Button(tooltip_text=names[color], width_request=30, height_request=30)
            button.update_property([Gtk.AccessibleProperty.LABEL],
                                   [_('Highlight in {color}').format(color=names[color])])
            button.add_css_class('circular')
            button.add_css_class('reader-color-chip')
            button.add_css_class(f'color-{color}')
            button.set_child(Gtk.Image(icon_name='object-select-symbolic', visible=False,
                                       can_target=False))
            button.connect('clicked', _weak_callback(self, self._on_color, color=color))
            colors.append(button)
            self._color_buttons[color] = button
        box.append(colors)
        actions = Gtk.Box(spacing=2, halign=Gtk.Align.CENTER)
        self._selection_buttons = {}
        for name, icon, tooltip, callback in (
                ('note', 'document-edit-symbolic', _('Add Note…'), self._on_note),
                ('copy', 'edit-copy-symbolic', _('Copy'), self._on_copy),
                ('lookup', 'accessories-dictionary-symbolic', _('Look Up'), self._on_lookup),
                ('search', 'edit-find-symbolic', _('Search the Book'), self._on_search_selection),
                ('remove', 'user-trash-symbolic', _('Remove Highlight'), self._on_remove)):
            button = Gtk.Button(icon_name=icon, tooltip_text=tooltip)
            button.add_css_class('flat')
            button.connect('clicked', _weak_callback(self, callback))
            actions.append(button)
            self._selection_buttons[name] = button
        box.append(actions)
        self._lookup_panel = lookup_popover.LookupPanel(
            on_search=_weak_callback(self, self._search_for, argument=0), visible=False,
            settings=self.settings)
        box.append(self._lookup_panel)
        popover.set_child(box)
        popover.set_parent(self.view)
        popover.connect('closed', _weak_callback(self, self._on_selection_closed))
        self._selection_popover = popover

    def _popup_selection(self, rect, annotation=None):
        popover = self._selection_popover
        self._show_lookup((self._selection or {}).get('text') if annotation is None else None)
        for color, button in self._color_buttons.items():
            button.get_child().set_visible(annotation is not None and annotation.color == color)
        self._selection_buttons['remove'].set_visible(annotation is not None)
        self._selection_buttons['note'].set_tooltip_text(
            _('Edit Note…') if annotation is not None and annotation.note else _('Add Note…'))
        if rect:
            area = Gdk.Rectangle()
            area.x, area.y = int(rect.get('x', 0)), int(rect.get('y', 0))
            area.width = max(1, int(rect.get('width', 1)))
            area.height = max(1, int(rect.get('height', 1)))
            popover.set_pointing_to(area)
            # Above the line, unless it is too near the top of the page (for the popover with
            # a definition in it, the upper half).
            room = 480 if self._lookup_panel.get_visible() else 140
            popover.set_position(Gtk.PositionType.TOP if area.y > room
                                 else Gtk.PositionType.BOTTOM)
        popover.popup()

    def _on_selection(self, _view, selection):
        if selection is None:
            if self._selection is not None and self._selection.get('annotation_id') is None:
                self._selection = None
                self._selection_popover.popdown()
            return
        self._selection = {'cfi': selection['cfi'], 'text': selection.get('text', ''),
                           'fraction': selection.get('fraction') or 0.0,
                           'annotation_id': None}
        self._popup_selection(selection.get('rect'))

    def _on_annotation_activated(self, _view, message):
        annotation = next((a for a in self._annotations
                           if a.kind == 'highlight' and a.location == message.get('cfi')), None)
        if annotation is None:
            return
        self._selection = {'cfi': annotation.location, 'text': annotation.text,
                           'fraction': annotation.position, 'annotation_id': annotation.id}
        self._popup_selection(message.get('rect'), annotation)

    def _on_selection_closed(self):
        if self._selection is not None and self._selection.get('annotation_id') is None:
            self.view.clear_selection()
        self._selection = None
        self._lookup_panel.cancel()

    def _take_selection(self):
        selection = self._selection
        self._selection_popover.popdown()
        return selection

    def _add_highlight(self, selection, color='yellow', note=''):
        try:
            self.library.add_annotation(self.book_id, 'highlight', selection['cfi'],
                                        text=selection['text'], note=note, color=color,
                                        position=selection['fraction'])
        except LibraryError as error:
            self.toast(str(error))
        self.view.clear_selection()

    def _on_color(self, color):
        selection = self._take_selection()
        if selection is None:
            return
        if selection['annotation_id'] is not None:
            self._update_annotation(selection['annotation_id'], color=color)
        else:
            self._add_highlight(selection, color)

    def _on_note(self):
        selection = self._take_selection()
        if selection is None:
            return
        annotation_id = selection['annotation_id']
        if annotation_id is not None:
            self._edit_note_of(annotation_id)
        else:
            self._edit_note(selection['text'], '',
                            lambda note: self._add_highlight(selection, note=note))

    def _on_copy(self):
        selection = self._take_selection()
        if selection is not None and selection['text']:
            self.get_clipboard().set(selection['text'])
            self.toast(_('Copied'))

    def _lookup_language(self):
        language = ((self.book.language if self.book else '') or 'en').split('-')[0][:3]
        return language.lower() if language.isalpha() else 'en'

    def _show_lookup(self, text, force=False):
        """The definition of a selected word in the popover, in a wide window (else Look Up
        opens a bottom sheet); with force, the summary of a longer selection too."""
        shown = bool(text) and lookup_popover.inline(self) and (force or (
            lookup.is_word(text) and self.settings.get_boolean('lookup-automatic')))
        self._lookup_panel.set_visible(shown)
        self._selection_buttons['lookup'].set_visible(not shown)
        if shown:
            self._lookup_panel.show_text(text, self._lookup_language())
        else:
            self._lookup_panel.cancel()

    def _on_lookup(self):
        selection = self._selection
        if selection is None or not selection['text']:
            return
        if lookup_popover.inline(self):
            self._show_lookup(selection['text'], force=True)
            return
        self._take_selection()
        lookup_popover.present_sheet(self, selection['text'], self._lookup_language(),
                                     _weak_callback(self, self._search_for, argument=0),
                                     settings=self.settings)

    def _search_for(self, text):
        self._selection_popover.popdown()
        self.sidebar_stack.set_visible_child_name('search')
        self.split_view.set_show_sidebar(True)
        self.search_entry.set_text(' '.join(text.split())[:100])
        self._start_search()

    def _on_search_selection(self):
        selection = self._take_selection()
        if selection is None or not selection['text']:
            return
        self.sidebar_stack.set_visible_child_name('search')
        self.split_view.set_show_sidebar(True)
        self.search_entry.set_text(' '.join(selection['text'].split())[:100])
        self._start_search()

    def _on_remove(self):
        selection = self._take_selection()
        if selection is not None and selection['annotation_id'] is not None:
            self._remove_annotation_of(selection['annotation_id'])

    # -- search ----------------------------------------------------------------------------

    def _on_search_changed(self, _entry):
        self._start_search()

    def _on_search_activate(self, _entry):
        if self._search_rows:
            self._search_step(1)

    def _on_stop_search(self, entry):
        if entry.get_text():
            entry.set_text('')
        else:
            self.set_focus(None)
            if self.split_view.get_collapsed():
                self.split_view.set_show_sidebar(False)

    def _start_search(self):
        text = self.search_entry.get_text().strip()
        while (row := self.search_list.get_first_child()) is not None:
            self.search_list.remove(row)
        self._search_rows = []
        self._search_index = -1
        self._search_count = 0
        self.search_status.set_visible(False)
        if not text:
            self.view.clear_search()
            self.search_stack.set_visible_child_name('start')
            return
        self.search_status.set_label(_('Searching…'))
        self.search_status.set_visible(True)
        self.view.search(text)

    def _on_search_result(self, _view, result):
        heading = Gtk.Label(label=result.get('label') or _('Untitled'), xalign=0,
                            ellipsize=Pango.EllipsizeMode.END, margin_top=6)
        heading.add_css_class('heading')
        header = Gtk.ListBoxRow(child=heading, activatable=False, selectable=False)
        self.search_list.append(header)
        for item in result.get('items') or []:
            markup = (escape(item.get('pre', '')) + '<b>' + escape(item.get('match', ''))
                      + '</b>' + escape(item.get('post', '')))
            label = Gtk.Label(use_markup=True, label=markup, xalign=0, wrap=True,
                              wrap_mode=Pango.WrapMode.WORD_CHAR, lines=3,
                              ellipsize=Pango.EllipsizeMode.END, max_width_chars=30,
                              margin_top=2, margin_bottom=2)
            row = Gtk.ListBoxRow(child=label)
            row.cfi = item.get('cfi')
            self.search_list.append(row)
            self._search_rows.append(row)
            self._search_count += 1
        self.search_stack.set_visible_child_name('list')
        self.search_status.set_label(ngettext('{n} result so far', '{n} results so far',
                                              self._search_count).format(n=self._search_count))

    def _on_search_done(self, _view, done):
        if done.get('query') != self.search_entry.get_text().strip():
            return
        count = done.get('count') or 0
        if count == 0:
            self.search_stack.set_visible_child_name('empty')
            self.search_status.set_visible(False)
            return
        self.search_status.set_label(
            ngettext('{n} result', '{n} results', count).format(n=count))

    def _on_search_row(self, _list, row):
        cfi = getattr(row, 'cfi', None)
        if cfi:
            self._search_index = self._search_rows.index(row)
            self.view.select(cfi)
            if self.split_view.get_collapsed():
                self.split_view.set_show_sidebar(False)

    def _search_step(self, step):
        if not self._search_rows:
            return
        self._search_index = (self._search_index + step) % len(self._search_rows)
        row = self._search_rows[self._search_index]
        self.search_list.select_row(row)
        self.view.select(row.cfi)

    # -- keys, the pointer, chrome ---------------------------------------------------------

    def set_dialog_open(self, is_open):
        """A dialog over the window takes the keys while it is open (dialogs/note.py)."""
        self._dialog_open = bool(is_open)

    def _on_key(self, _controller, keyval, _keycode, state):
        if self._dialog_open:
            return False
        mods = int(state & Gtk.accelerator_get_default_mod_mask())
        lower = Gdk.keyval_to_lower(keyval)
        name = self._keys.get((lower, mods))
        if name is None:
            name = self._keys.get((lower, mods & ~int(Gdk.ModifierType.SHIFT_MASK)))
        if name is None:
            return False
        focus = self.get_focus()
        typing = isinstance(focus, Gtk.Text | Gtk.TextView | Gtk.Editable)
        if typing and not (mods & (Gdk.ModifierType.CONTROL_MASK | Gdk.ModifierType.ALT_MASK)) \
                and name not in ('fullscreen', 'leave-fullscreen'):
            return False
        if typing and name in ('leave-fullscreen', 'copy', 'read-aloud-next',
                               'read-aloud-previous'):
            return False  # the search entry's stop-search, its own copy
        if name == 'leave-fullscreen' and focus is not None \
                and focus.get_ancestor(Gtk.Popover) is not None:
            return False  # Escape closes the popover first
        if not mods and name in NAVIGATION and self._navigating(focus):
            return False  # the arrows move through a list or a popover reached by Tab
        if self.content_stack.get_visible_child_name() != 'book' and name not in (
                'fullscreen', 'leave-fullscreen', 'close', 'info'):
            return False
        return self._run_key(name, lower) is not False

    def _navigating(self, focus):
        """Whether the keyboard is moving through the sidebar or a popover: the focus there,
        and shown (it was reached with the keyboard, not left there by a click)."""
        if focus is None or not self.get_focus_visible():
            return False
        if focus.get_ancestor(Gtk.Popover) is not None:
            return True
        sidebar = self.split_view.get_sidebar()
        return self.split_view.get_show_sidebar() and (
            focus is sidebar or focus.is_ancestor(sidebar))

    def _copy_selection(self):
        """Ctrl+C: the selected text to the clipboard; False when nothing is selected."""
        text = (self._selection or {}).get('text') or ''
        if not text and self.is_pdf:
            text = self.view.selected_text()
        if not text:
            return False
        self.get_clipboard().set(text)
        self.toast(_('Copied'))
        return True

    def _run_key(self, name, keyval):
        view = self.view
        if name == 'copy':
            return self._copy_selection()
        if name == 'next':
            if keyval in (Gdk.KEY_Right, Gdk.KEY_l):
                view.go_right()
            else:
                view.next()
        elif name == 'previous':
            if keyval in (Gdk.KEY_Left, Gdk.KEY_h):
                view.go_left()
            else:
                view.prev()
        elif name == 'scroll-down':
            view.scroll(1)
        elif name == 'scroll-up':
            view.scroll(-1)
        elif name == 'start':
            view.start()
        elif name == 'end':
            view.end()
        elif name == 'next-chapter':
            view.next_section()
        elif name == 'previous-chapter':
            view.prev_section()
        elif name == 'back':
            view.back()
        elif name == 'forward':
            view.forward()
        elif name == 'contents':
            self._show_sidebar('contents')
        elif name == 'annotations':
            self._show_sidebar('annotations')
        elif name == 'search':
            self.sidebar_stack.set_visible_child_name('search')
            self.split_view.set_show_sidebar(True)
            self.search_entry.grab_focus()
        elif name == 'search-next':
            self._search_step(1)
        elif name == 'search-previous':
            self._search_step(-1)
        elif name == 'bookmark':
            self._toggle_bookmark()
        elif name == 'bigger':
            self._change_font_size(1)
        elif name == 'smaller':
            self._change_font_size(-1)
        elif name == 'reset-size':
            self._change_font_size(0)
        elif name == 'fullscreen':
            self._toggle_fullscreen()
        elif name == 'leave-fullscreen':
            if self.is_fullscreen():
                self.unfullscreen()
            elif self.split_view.get_collapsed() and self.split_view.get_show_sidebar():
                self.split_view.set_show_sidebar(False)
            elif not self._chrome_visible:
                self._set_chrome(True)
            else:
                return False
        elif name == 'go-to':
            self._go_to_location()
        elif name == 'info':
            self._show_details()
        elif name == 'read-aloud':
            return self._toggle_read_aloud()
        elif name == 'print':
            return self._pdf is not None and self._pdf.print()
        elif name in ('read-aloud-next', 'read-aloud-previous'):
            bar = self._read_aloud
            if bar is None or bar.state == 'stopped':
                return False
            return bar.skip(1 if name == 'read-aloud-next' else -1)
        elif name == 'close':
            self.close()
        else:
            return False
        return True

    def _on_scroll(self, controller, _dx, dy):
        state = controller.get_current_event_state()
        if not state & Gdk.ModifierType.CONTROL_MASK or dy == 0:
            return False
        self._change_font_size(-1 if dy > 0 else 1)
        return True

    def _on_button(self, gesture, _n_press, _x, _y):
        button = gesture.get_current_button()
        if button == 8:
            self.view.back()
        elif button == 9:
            self.view.forward()
        else:
            return
        gesture.set_state(Gtk.EventSequenceState.CLAIMED)

    def _on_motion(self, _controller, _x, y):
        if not self.is_fullscreen() or self._chrome_visible:
            if self.is_fullscreen() and self._chrome_visible and self._peeking:
                bar = self.header_bar.get_height() + 24
                bottom = self.get_height() - self.bottom_bar.get_height() - 24
                if bar < y < bottom:
                    self._peeking = False
                    self._set_chrome(False)
            return
        if y <= REVEAL_EDGE:
            self._peeking = True
            self._set_chrome(True)

    def _on_toggle_chrome(self, _view):
        self._peeking = False
        self._set_chrome(not self._chrome_visible)

    def _set_chrome(self, visible):
        self._chrome_visible = visible
        self.toolbar_view.set_reveal_top_bars(visible)
        self.toolbar_view.set_reveal_bottom_bars(visible)
        if self.content_stack.get_visible_child_name() == 'book':
            self.view.show_progress(not visible)

    def _toggle_fullscreen(self):
        if self.is_fullscreen():
            self.unfullscreen()
        else:
            self.fullscreen()

    def _on_fullscreened(self, _window, _pspec):
        fullscreen = self.is_fullscreen()
        self.toolbar_view.set_extend_content_to_top_edge(fullscreen)
        self.toolbar_view.set_extend_content_to_bottom_edge(fullscreen)
        self._peeking = False
        self._set_chrome(not fullscreen)

    # -- the menu's other items ------------------------------------------------------------

    def _go_to_location(self):
        if self._place is None:
            return
        pages = self._place.get('pages') if self.is_pdf else None  # a PDF goes by its pages
        items = self._page_items()  # an EPUB with the printed book's page numbers
        if pages:
            body = _('A page number, from 1 to {pages}').format(pages=pages)
            text = str(self._place.get('page') or 1)
        elif items:
            pages = items
            body = _('A page number of the printed book, from {first} to {last}, or a '
                     'percentage (50%)').format(first=items[0]['label'],
                                                last=items[-1]['label'])
            text = str(self._place.get('page') or items[0]['label'])
        else:
            body = _('A percentage of the book, from 0 to 100')
            text = str(int((self._place.get('fraction') or 0) * 100))
        dialog = Adw.AlertDialog(heading=_('Go to Page') if pages else _('Go to Location'),
                                 body=body)
        entry = Gtk.Entry(input_purpose=Gtk.InputPurpose.NUMBER, activates_default=True,
                          text=text)
        dialog.set_extra_child(entry)
        dialog.add_response('cancel', _('Cancel'))
        dialog.add_response('go', _('Go'))
        dialog.set_response_appearance('go', Adw.ResponseAppearance.SUGGESTED)
        dialog.set_default_response('go')
        dialog.set_close_response('cancel')
        ref = self.weak_ref()

        def response(_dialog, answer):
            window = ref()
            window.set_dialog_open(False) if window is not None else None
            if window is None or answer != 'go':
                return
            window._go_to_entered(entry.get_text(), pages)

        dialog.connect('response', response)
        self.set_dialog_open(True)
        dialog.present(self)
        entry.grab_focus()

    def _page_items(self):
        """The book's page list ([{label, href}], an EPUB's printed pages), else []."""
        if self.is_pdf:
            return []
        return [item for item in (self._loaded or {}).get('pageItems') or ()
                if item.get('label') and item.get('href')]

    def _go_to_entered(self, text, pages=None):
        """Go where the Go to Location dialog says: a page of `pages` (a PDF's count, or an
        EPUB's page list: a page label, or a percentage ending in %), else a percentage.
        False when it is neither."""
        if isinstance(pages, list):
            wanted = text.strip().casefold()
            item = next((i for i in pages if i['label'].casefold() == wanted), None)
            if item is not None:
                self.view.go_to(item['href'])
                return True
            if not text.strip().endswith('%'):
                self.toast(_('This book has no page {page}').format(page=text.strip()))
                return False
            pages = None
        try:
            number = float(text.strip().rstrip('%').replace(',', '.'))
        except ValueError:
            return False
        if pages:
            page = max(1, min(int(pages), round(number)))
            self.view.go_to(pdf_location.location(page))
        else:
            self.view.go_to_fraction(max(0.0, min(100.0, number)) / 100)
        return True

    def _show_details(self):
        get_window = getattr(self.app, 'window', None)
        window = get_window() if callable(get_window) else None
        if window is None or not hasattr(window, 'show_book'):
            return
        window.show_book(self.book_id)
        window.present()

    def show_annotations(self):
        """The sidebar open on the highlights and bookmarks (the book details page's
        Highlights button)."""
        self.sidebar_stack.set_visible_child_name('annotations')
        self.split_view.set_show_sidebar(True)
        self.present()

    def toast(self, text, undo=False):
        """A toast in this window; with Undo, app.undo() puts the last change back."""
        toast = Adw.Toast(title=text, timeout=5 if undo else 2)
        if undo:  # high priority: it replaces the toast before, as the app's do
            toast.set_priority(Adw.ToastPriority.HIGH)
            toast.set_button_label(_('Undo'))
            app = self.app
            toast.connect('button-clicked', lambda *_args: app.undo())
        self.toast_overlay.add_toast(toast)
        return toast

    def add_toast(self, toast):
        self.toast_overlay.add_toast(toast)

    # -- closing ---------------------------------------------------------------------------

    def _on_close_request(self, _window):
        if self._closed:
            return False
        self._closed = True
        if self._save_source:
            GLib.source_remove(self._save_source)
            self._save_source = 0
        self._save_progress()
        self._sync.close()
        if self._clock is not None:
            session = self._clock.finish(time.time())
            if session is not None:
                self._log_session(session)
            self._clock = None
        for source in (self._style_source, self._scrub_source):
            if source:
                GLib.source_remove(source)
        self._style_source = self._scrub_source = 0
        if not self.is_maximized() and not self.is_fullscreen():
            width, height = self.get_default_size()
            if width > 0 and height > 0:
                self.settings.set_int('reader-width', width)
                self.settings.set_int('reader-height', height)
        for obj, handler in ((self.library, self._library_handler),
                             (self.settings, self._settings_handler),
                             (Adw.StyleManager.get_default(), self._style_handler),
                             (Adw.StyleManager.get_default(), self._contrast_handler)):
            if obj.handler_is_connected(handler):
                obj.disconnect(handler)
        self._release_popover()
        if self._read_aloud is not None:
            self._read_aloud.close()
        self.view.close()
        if self.view is not self.book_view:
            self.book_view.close()
        return False


    def _release_popover(self, *_args):
        """Take the selection popover off the view, which a popover does not leave on its
        own: a view finalized with one still attached frees it twice. On close, and on
        destroy for a window that is never closed (tests)."""
        handler, self._destroy_handler = self._destroy_handler, 0
        if handler and self.handler_is_connected(handler):
            self.disconnect(handler)
        popover = self._selection_popover
        if popover is not None and popover.get_parent() is not None:
            popover.unparent()


def _weak_callback(widget, method, argument=None, **kwargs):
    """A signal handler (or GLib callback) calling `method` (a bound method of `widget`, or
    of its class) while `widget` lives, without keeping it alive: method(**kwargs), or
    method(value, **kwargs) with the handler's argument at index `argument` (an action's
    parameter is unpacked)."""
    ref = widget.weak_ref()
    function = method.__func__

    def call(*args):
        instance = ref()
        if instance is None:
            return False
        if argument is None:
            return function(instance, **kwargs)
        value = args[argument] if len(args) > argument else None
        if isinstance(value, GLib.Variant):
            value = value.unpack()
        return function(instance, value, **kwargs)

    return call
