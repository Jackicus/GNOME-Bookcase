# SPDX-License-Identifier: GPL-2.0-or-later
# SPDX-FileCopyrightText: 2026 Jack Tully

"""The application: what the app is made of, its app.* actions and its lifecycle.

    app = Application(version, app_id, base_id, profile)   # the launcher's main()
    app.run(sys.argv)

    app.library, app.covers, app.importer, app.devices, app.settings, app.data_dir
    app.sync                            # kosync.Sync: reading positions with KOReader's sync
    app.toast(text, undo=False)         # a toast on the library window (Undo runs app.undo)
    app.report(error, context=None)     # an error, in a sentence, and logged
    app.undo()                          # puts the newest change back; False when none
    app.open_book(book_id)              # the reader window (reader_window.open)
    app.add_files(gio_files)            # imports files (copied into the library folder)
    app.window()                        # the library window, or None

do_handle_local_options reads --demo (an invented library in build/demo, never the real
one; BOOKCASE_DATA_DIR overrides it), --debug and --version first; do_startup opens the
library in the data directory (data_dir(): BOOKCASE_DATA_DIR, else bookcase/ or
bookcase-devel/ in the user data directory), the cover store and the importer (whose library
folder is the library-folder setting, ~/Books when empty: made on the first add, not here),
starts the device monitor (devices.py; the app works without it), adds the app.* actions and
their accelerators (shortcuts.ACCELS) and the style sheet, and a few seconds later rescans
the watched folders and linked Calibre libraries in the background, one after the other.
do_activate builds the library window (imported only then).

Book files opened from Files (HANDLES_OPEN) or with Open File… are read at once, without
adding them: a file the library has (by path, else by content hash) opens as its book;
another becomes a book opened without adding (importing.open_in_place, in a thread: read
where it is, kept out of the library's lists, its place and highlights kept), and the reader
offers Add to Library (app.keep_book). Files that are not books are toasted. app.* actions:
add-books (a file chooser of formats.SUFFIXES), open-file (read without adding), add-folder
(a folder to watch, scanned at once), link-calibre (a folder holding metadata.db),
preferences, shortcuts, about, undo, quit.

    app.open_path(path)                 # read a file from outside, without adding it
    app.keep_book(book_id)              # add a book opened without adding (copied in)
"""

import logging
import os
import pathlib
import sys
import threading
from gettext import gettext as _

import gi

gi.require_version('Gtk', '4.0')
gi.require_version('Adw', '1')

from gi.repository import Adw, Gdk, Gio, GLib, Gtk  # noqa: E402

from . import formats  # noqa: E402
from .library import Library, LibraryError, library_path  # noqa: E402
from .shortcuts import ACCELS  # noqa: E402

log = logging.getLogger(__name__)

RESOURCE_PATH = '/io/github/jackicus/Bookcase'
RESCAN_DELAY_S = 3
MAX_OPEN = 8  # reader windows opened at once from Files
TOAST_TIMEOUT_S = 5
UNDO_TIMEOUT_S = 8
ERROR_TIMEOUT_S = 10


def default_data_dir(profile='default'):
    """BOOKCASE_DATA_DIR, else $XDG_DATA_HOME/bookcase (bookcase-devel for the .Devel
    build): library.library_path()'s directory."""
    return library_path(development=profile == 'development').parent


def library_folder(settings):
    """The folder added books are copied into: the library-folder setting, or ~/Books."""
    folder = settings.get_string('library-folder').strip() if settings is not None else ''
    if folder:
        return os.path.expanduser(folder)
    return os.path.join(GLib.get_home_dir(), 'Books')


def book_files(gio_files):
    """The paths of the book files among Gio.Files (directories walked), and the names of
    the files that are not books."""
    paths, refused = [], []
    for gio_file in gio_files:
        path = gio_file.get_path()
        if path is None:
            refused.append(gio_file.get_basename() or gio_file.get_uri())
            continue
        if os.path.isdir(path):
            for directory, names, files in os.walk(path):
                names[:] = [name for name in names if not name.startswith('.')]
                for name in sorted(files):
                    if not name.startswith('.') and formats.format_of(name):
                        paths.append(os.path.join(directory, name))
        elif formats.format_of(path):
            paths.append(path)
        else:
            refused.append(os.path.basename(path))
    return paths, refused


def text_undo(window):
    """Undo the typing in the text field that has the focus in `window`, if one has: True
    when there was one (whether or not it had anything to undo)."""
    focus = window.get_focus() if window is not None else None
    while focus is not None and not isinstance(focus, (Gtk.Text, Gtk.TextView)):
        if not isinstance(focus, Gtk.Editable):
            return False
        focus = focus.get_delegate() if focus.get_delegate() is not focus else None
    if focus is None or not focus.get_editable():
        return False
    focus.activate_action('text.undo', None)
    return True


class Application(Adw.Application):
    """The app. `demo` is true under --demo: the library is build/demo's."""

    def __init__(self, version, app_id, base_id, profile):
        super().__init__(
            application_id=app_id,
            flags=Gio.ApplicationFlags.HANDLES_OPEN,
            resource_base_path=RESOURCE_PATH,
        )
        self.version = version
        self.base_id = base_id
        self.profile = profile
        self.demo = False
        self.data_dir = None
        self.settings = None  # in do_startup: GSettings
        self.library = None  # in do_startup
        self.sync = None  # in do_startup: kosync.Sync
        self.covers = None
        self.importer = None
        self.devices = None  # devices.DeviceMonitor, when it could start
        self._rescan_source = None
        self._undo_toast = None  # the Undo toast shown, while it is
        self.add_main_option('demo', 0, GLib.OptionFlags.NONE, GLib.OptionArg.NONE,
                             _('Show an invented library (build/demo), not yours'), None)
        self.add_main_option('debug', 0, GLib.OptionFlags.NONE, GLib.OptionArg.NONE,
                             _('Log what the app does'), None)
        self.add_main_option('version', 0, GLib.OptionFlags.NONE, GLib.OptionArg.NONE,
                             _('Print the version and exit'), None)

    # -- lifecycle ---------------------------------------------------------------------------

    def do_handle_local_options(self, options):
        """The options, before startup (so --demo decides which library opens)."""
        if options.contains('version'):
            sys.stdout.write(f'bookcase {self.version}\n')
            return 0
        if options.contains('debug'):
            logging.getLogger('bookcase').setLevel(logging.DEBUG)
        if options.contains('demo'):
            self.demo = True
        return -1

    def do_startup(self):
        Adw.Application.do_startup(self)
        self.settings = Gio.Settings.new(self.base_id)
        self.data_dir = self._data_dir()
        self.data_dir.mkdir(parents=True, exist_ok=True)
        self.library = Library(self.data_dir / 'library.sqlite')
        from .covers import CoverStore
        from .importing import Importer

        self.covers = CoverStore(self.data_dir, self.library)
        self.importer = Importer(self.library, self.covers, self.library_folder())
        self.settings.connect('changed::library-folder', self._on_library_folder_changed)
        self._start_devices()
        self._start_sync()
        self._add_actions()
        for name, accels in ACCELS.items():
            self.set_accels_for_action(name, accels)
        self._load_css()
        self._rescan_source = GLib.timeout_add_seconds(RESCAN_DELAY_S, self._rescan_folders)

    def _start_sync(self):
        """app.sync; under --demo its key lives in memory only, so the demo library's
        invented books never reach a real sync account."""
        from . import kosync

        self.sync = kosync.Sync(self.settings, self.data_dir / 'sync.json',
                                keyring=kosync.MemoryKeyring() if self.demo else None)

    def library_folder(self):
        """The folder added books are copied into: build/demo/Books under --demo (never the
        real ~/Books), else library_folder(settings)."""
        if self.demo:
            return str(self.data_dir / 'Books')
        return library_folder(self.settings)

    def _data_dir(self):
        if self.demo:
            override = os.environ.get('BOOKCASE_DATA_DIR')
            if override:
                return pathlib.Path(override)
            demo = _demo_dir()
            if demo is None or not (demo / 'library.sqlite').exists():
                sys.exit(_('--demo needs a demo library: run scripts/demo_library.py '
                           'or set BOOKCASE_DATA_DIR'))
            return demo
        return default_data_dir(self.profile)

    def _start_devices(self):
        try:
            from .devices import DeviceMonitor

            self.devices = DeviceMonitor()
        except Exception:
            log.exception('the device monitor could not start; sending to devices is off')
            self.devices = None

    def do_activate(self):
        window = self.window()
        if window is None:
            from .window import Window

            window = Window(application=self)
        window.present()

    def do_open(self, files, _hint):
        """Book files open in the reader alone (the library window stays closed if it was);
        folders are added, as dropping them on the window does."""
        folders = [gio_file for gio_file in files
                   if gio_file.get_path() and os.path.isdir(gio_file.get_path())]
        if folders:
            self.do_activate()
            self.add_files(folders)
        paths, refused = book_files([gio_file for gio_file in files if gio_file not in folders])
        if refused:
            self.do_activate()
            self.toast(_('“{name}” is not a book Bookcase can open').format(name=refused[0]))
        for path in paths[:MAX_OPEN]:
            self.hold()

            def opened(book_id):
                if book_id is None:
                    self.do_activate()  # for the toast that says why
                self.release()

            self.open_path(path, done=opened)

    def open_path(self, path, done=None):
        """Read a book file from outside without adding it: the library's book when it has
        the file, else a book opened without adding (importing.open_in_place, in a thread).
        `done(book_id or None)` is called after the reader opens."""
        path = os.path.abspath(path)
        found = self.library.find_file(path)
        if found is not None:
            self.open_book(found.book_id)
            if done is not None:
                done(found.book_id)
            return None
        library, covers, folder = self.library, self.covers, self.library_folder()

        def work():
            from .importing import Importer

            worker = library.open_worker()
            try:
                importer = Importer(worker, covers.with_library(worker), folder)
                result = importer.open_in_place(path)
            except Exception as error:
                log.info('opening %s: %s', path, error)
                result = error
            finally:
                worker.close()
            GLib.idle_add(finish, result)

        def finish(result):
            if isinstance(result, Exception):
                if done is not None:
                    done(None)
                self.toast(_('Could not open “{name}”: {error}').format(
                    name=os.path.basename(path), error=result))
                return GLib.SOURCE_REMOVE
            if self.library is not None:
                self.open_book(result)
            if done is not None:
                done(result)
            return GLib.SOURCE_REMOVE

        thread = threading.Thread(target=work, name='bookcase-open', daemon=True)
        thread.start()
        return thread

    def keep_book(self, book_id, done=None):
        """Add a book opened without adding to the library: copied into the library folder
        in a thread (Importer.copy_into_library, which changes nothing in the library), then
        made the book's file on the main library (Library.keep_book, the undo step), so the
        toast's Undo puts back this and nothing else. `done(ok)` is called at the end."""
        book = self.library.book(book_id)
        if book is None or self.library.reading_file(book_id) is None:
            self.toast(_('The book’s file cannot be found'))
            return None
        library, covers, folder = self.library, self.covers, self.library_folder()

        def work():
            from .importing import Importer

            worker = library.open_worker()
            try:
                importer = Importer(worker, covers.with_library(worker), folder)
                result = importer.copy_into_library(book_id)
            except Exception as error:
                log.info('copying book %s into the library: %s', book_id, error)
                result = error
            finally:
                worker.close()
            GLib.idle_add(finish, result)

        def finish(result):
            ok = False
            if isinstance(result, Exception):
                self.toast(_('Could not add “{title}”: {error}').format(
                    title=book.title, error=result))
            elif self.library is not None:
                path, file_hash, size, file_format = result
                try:
                    ok = self.library.keep_book(book_id, path=path, hash=file_hash,
                                                size=size, format=file_format)
                except Exception as error:
                    log.warning('keeping book %s: %s', book_id, error)
                    self.toast(_('Could not add “{title}”: {error}').format(
                        title=book.title, error=error))
                if ok:
                    self.toast(_('“{title}” was added to your library').format(
                        title=book.title), undo=True)
                else:
                    _remove_quietly(path)  # the copy is nobody's
            if done is not None:
                done(ok)
            return GLib.SOURCE_REMOVE

        thread = threading.Thread(target=work, name='bookcase-keep', daemon=True)
        thread.start()
        return thread

    def do_shutdown(self):
        if self._rescan_source is not None:
            GLib.source_remove(self._rescan_source)
            self._rescan_source = None
        stop = getattr(self.devices, 'stop', None)
        if stop is not None:
            try:
                stop()
            except Exception:
                log.exception('stopping the device monitor')
        if getattr(self, 'sync', None) is not None:
            try:
                self.sync.shutdown()
            except Exception:
                log.exception('stopping sync')
        if self.library is not None:
            try:
                self.library.close()
            except Exception:
                log.exception('closing the library')
            self.library = None
        Adw.Application.do_shutdown(self)

    def _load_css(self):
        provider = Gtk.CssProvider()
        provider.load_from_resource(RESOURCE_PATH + '/style.css')
        Gtk.StyleContext.add_provider_for_display(
            Gdk.Display.get_default(), provider, Gtk.STYLE_PROVIDER_PRIORITY_APPLICATION)

    def _on_library_folder_changed(self, settings, _key):
        self.importer.library_folder = os.path.abspath(self.library_folder())

    # -- background rescans ------------------------------------------------------------------

    def _rescan_folders(self):
        """Rescan the watched folders and linked Calibre libraries, one after the other, in
        threads; a toast only when something was found or went missing."""
        self._rescan_source = None
        if self.library is None:
            return GLib.SOURCE_REMOVE
        queue = [folder for folder in self.library.folders()
                 if folder.kind in ('watched', 'calibre') and os.path.isdir(folder.path)]
        self._rescan_next(queue)
        return GLib.SOURCE_REMOVE

    def _rescan_next(self, queue):
        if not queue or self.library is None:
            return
        folder = queue.pop(0)

        def done(report):
            if report.added or report.missing or report.updated:
                from .importing import describe

                self.toast(describe(report))
            self._rescan_next(queue)

        log.debug('rescanning %s', folder.path)
        if folder.kind == 'calibre':
            self.importer.link_calibre_async(folder.path, done=done)
        else:
            self.importer.scan_async(folder.path, done=done)

    # -- actions -----------------------------------------------------------------------------

    def _add_actions(self):
        for name, callback in (
                ('add-books', self.on_add_books), ('open-file', self.on_open_file),
                ('add-folder', self.on_add_folder),
                ('link-calibre', self.on_link_calibre), ('undo', self.on_undo),
                ('preferences', self.on_preferences), ('shortcuts', self.on_shortcuts),
                ('about', self.on_about), ('quit', self.on_quit)):
            action = Gio.SimpleAction.new(name, None)
            action.connect('activate', callback)
            self.add_action(action)
        from .dialogs import highlights

        highlights.add_actions(self)  # app.import-clippings, app.export-highlights
        self._update_undo()
        self.library.connect('changed', lambda *_args: self._update_undo())

    def _update_undo(self):
        self.lookup_action('undo').set_enabled(self.library.can_undo())

    def _book_filters(self, dialog):
        books = Gtk.FileFilter(name=_('E-books'))
        for suffix in formats.SUFFIXES:
            books.add_suffix(suffix.lstrip('.'))
        everything = Gtk.FileFilter(name=_('All Files'))
        everything.add_pattern('*')
        filters = Gio.ListStore(item_type=Gtk.FileFilter)
        filters.append(books)
        filters.append(everything)
        dialog.set_filters(filters)
        dialog.set_default_filter(books)

    def on_open_file(self, *_args):
        dialog = Gtk.FileDialog(title=_('Open File'), accept_label=_('_Open'), modal=True)
        self._book_filters(dialog)

        def chosen(dialog, result):
            try:
                files = dialog.open_multiple_finish(result)
            except GLib.Error:
                return  # dismissed
            self.do_open([files.get_item(index) for index in range(files.get_n_items())],
                         '')

        dialog.open_multiple(self.get_active_window(), None, chosen)

    def on_add_books(self, *_args):
        dialog = Gtk.FileDialog(title=_('Add Books'), accept_label=_('_Add'), modal=True)
        self._book_filters(dialog)

        def chosen(dialog, result):
            try:
                files = dialog.open_multiple_finish(result)
            except GLib.Error:
                return  # dismissed
            self.add_files([files.get_item(index) for index in range(files.get_n_items())])

        dialog.open_multiple(self.window(), None, chosen)

    def add_files(self, gio_files):
        """Add book files (Gio.Files; directories are walked): the import dialog shows the
        progress and what was added (dialogs/add_books.py)."""
        paths, refused = book_files(gio_files)
        if not paths:
            if refused:
                self.toast(_('“{name}” is not a book Bookcase can open').format(
                    name=refused[0]))
            else:
                self.toast(_('No books found'))
            return None
        from .dialogs import add_books

        return add_books.present(self, self.window(), paths)

    def on_add_folder(self, *_args):
        dialog = Gtk.FileDialog(title=_('Add a Folder'), accept_label=_('_Add Folder'),
                                modal=True)

        def chosen(dialog, result):
            try:
                folder = dialog.select_folder_finish(result)
            except GLib.Error:
                return
            path = folder.get_path() if folder is not None else None
            if path:
                self.add_folder(path)

        dialog.select_folder(self.window(), None, chosen)

    def add_folder(self, path):
        """Watch a folder: its books are read where they are, and found again at startup
        (dialogs/add_books.py shows the progress)."""
        from .dialogs import add_books

        return add_books.present_scan(self, self.window(), path)

    def on_link_calibre(self, *_args):
        dialog = Gtk.FileDialog(title=_('Link a Calibre Library'),
                                accept_label=_('_Link Library'), modal=True)

        def chosen(dialog, result):
            try:
                folder = dialog.select_folder_finish(result)
            except GLib.Error:
                return
            path = folder.get_path() if folder is not None else None
            if path:
                self.link_calibre(path)

        dialog.select_folder(self.window(), None, chosen)

    def link_calibre(self, path):
        """Read a Calibre library where it is (its metadata.db is never written)."""
        if not os.path.exists(os.path.join(path, 'metadata.db')):
            self.toast(_('That folder is not a Calibre library: it has no metadata.db'))
            return None
        from .dialogs import add_books

        return add_books.present_link_calibre(self, self.window(), path)

    def on_undo(self, *_args):
        # Ctrl+Z is an application accelerator, run before a focused entry sees the key:
        # in an entry (a search, a dialog's field) it undoes the typing, not a library change.
        if not text_undo(self.get_active_window()):
            self.undo()

    def undo(self):
        """Put the newest change back and say what it was; False when there was none."""
        self._dismiss_undo_toast()
        label = self.library.undo()
        if label is None:
            return False
        window = self.window()
        if window is not None:
            window.undone(label)
        # Translators: a toast after Ctrl+Z; {action} is what was undone ("Edit Book").
        self.toast(_('Undone: {action}').format(action=label))
        return True

    def on_preferences(self, *_args):
        from .dialogs import preferences

        preferences.present(self, self.get_active_window())

    def on_shortcuts(self, *_args):
        from .dialogs import shortcuts

        shortcuts.present(self, self.get_active_window())

    def on_about(self, *_args):
        from .dialogs import about

        about.present(self, self.get_active_window())

    def on_quit(self, *_args):
        for window in list(self.get_windows()):
            window.close()
        self.quit()

    # -- windows -----------------------------------------------------------------------------

    def window(self):
        """The library window (not a reader's), or None before the first activation."""
        from .window import Window

        for window in self.get_windows():
            if isinstance(window, Window):
                return window
        return None

    def open_book(self, book_id):
        """Open a book in its reader window (raised when it is open already)."""
        try:
            from .reader_window import open as open_reader
        except ImportError as error:
            self.report(error, _('Could not open the book'))
            return None
        try:
            return open_reader(self, book_id)
        except LibraryError as error:
            self.report(error)
        except Exception as error:
            self.report(error, _('Could not open the book'))
        return None

    # -- messages ----------------------------------------------------------------------------

    def toast(self, text, undo=False, timeout=None):
        """Show a toast on the library window; with `undo`, an Undo button (app.undo).

        A toast goes after TOAST_TIMEOUT_S (UNDO_TIMEOUT_S with Undo) unless `timeout` says
        otherwise (0: until closed). An Undo toast is shown at once, and replaces the one
        before it: Undo puts back the newest change, which is the one the newest Undo toast
        is about."""
        window = self.window()
        if window is None:
            log.info('toast without a window: %s', text)
            return None
        if timeout is None:
            timeout = UNDO_TIMEOUT_S if undo else TOAST_TIMEOUT_S
        toast = Adw.Toast(title=GLib.markup_escape_text(text), timeout=timeout)
        if undo:
            toast.set_button_label(_('Undo'))
            toast.set_action_name('app.undo')
            toast.set_priority(Adw.ToastPriority.HIGH)
            self._dismiss_undo_toast()
            self._undo_toast = toast
            toast.connect('dismissed', self._on_undo_toast_dismissed)
        window.add_toast(toast)
        return toast

    def _on_undo_toast_dismissed(self, toast):
        if self._undo_toast is toast:
            self._undo_toast = None

    def _dismiss_undo_toast(self):
        toast, self._undo_toast = self._undo_toast, None
        if toast is not None:
            toast.dismiss()

    def report(self, error, context=None):
        """Tell the user an error in a sentence and log the rest."""
        if isinstance(error, LibraryError):
            message = str(error)
        elif isinstance(error, GLib.Error):
            message = error.message
        elif isinstance(error, OSError) and error.strerror:
            message = error.strerror
        else:
            message = _('Something went wrong: {error}').format(error=error)
        if context:
            message = f'{context}: {message}'
        log.warning('reported: %s', message,
                    exc_info=not isinstance(error, (LibraryError, GLib.Error, OSError)))
        self.toast(message, timeout=ERROR_TIMEOUT_S)


def _remove_quietly(path):
    try:
        os.remove(path)
    except OSError as error:
        log.info('removing %s: %s', path, error)


def _demo_dir():
    """build/demo of the source tree the running module sits in, when it is one."""
    here = pathlib.Path(__file__).resolve()
    for parent in here.parents:
        if (parent / 'meson.build').exists() and (parent / 'scripts').is_dir():
            return parent / 'build' / 'demo'
    return None


def main(version, app_id, base_id, profile):
    logging.basicConfig(level=logging.INFO, format='%(name)s: %(message)s', stream=sys.stderr)
    logging.getLogger('bookcase').setLevel(logging.INFO)
    app = Application(version, app_id, base_id, profile)
    return app.run(sys.argv)
