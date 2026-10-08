# Bookcase

An e-book library and reader for GNOME, meant to replace Calibre for GNOME users: Python and
PyGObject, GTK 4 and libadwaita, Blueprint for the UI, SQLite for the library, WebKitGTK and
foliate-js for reading, Meson, gettext. GPL-2.0-or-later. App ID `io.github.jackicus.Bookcase`
(`io.github.jackicus.Bookcase.Devel` with `-Dprofile=development`), resource base path
`/io/github/jackicus/Bookcase`, gettext domain and binary `bookcase`. It should feel like a
GNOME core app (one library window, a reader window per book, the HIG followed), carry
Calibre's substance (metadata, covers, series, online lookup, devices, Calibre libraries) and
drop its clutter: it never moves or rewrites the user's files unless asked.

Developed on GTK 4.22, libadwaita 1.9, WebKitGTK 2.52 (API 6.0), Python 3.14 and PyGObject
3.56. The minimums are Python 3.12 (`pyproject.toml`) and, in `meson.build`, GTK 4.20, GLib
2.84, libadwaita 1.9, PyGObject 3.50, Meson 1.2: use no newer API without raising them there
and in README.md. lxml is a dependency (EPUB metadata); Poppler's GI typelib is optional
(PDF covers and metadata, and the reader's PDF view). This file holds rules and pointers: a
module's API is in its docstring, product decisions and their reasons in `docs/decisions.md`, the research behind
them in `docs/research/` (calibre.md, competitors.md, tech.md: read the part you need).

## Architecture

One thread runs GTK; SQLite work is quick enough to run on it. Long work (importing,
scanning folders, thumbnails, online lookups, copying to a device) runs in a thread with a
library connection of its own (`Library.open_worker()`) and reports through `GLib.idle_add`.

```
Application (main.py)    app.library, app.covers, app.settings, app.devices; app.* actions;
│                        app.toast(), app.report(), app.undo(), app.open_book(id)
├─ Window (window.py)    AdwToastOverlay > AdwNavigationSplitView; sidebar.py fills the
│  │                     AdwSidebar (Home, All Books, Authors, Series, Tags; the reading
│  │                     states, Statistics; the user's shelves; connected devices)
│  ├─ content            AdwNavigationView: a sidebar item replaces the stack with its root
│  │                     page (pages/: home, books, groups, device, stats); a book's details
│  │                     (pages/book.py) and a filtered list are pushed
│  └─ dialogs/           edit_metadata, fetch_metadata, add_books, shelf, send, preferences,
│                        about, shortcuts, goals
├─ ReaderWindow (reader_window.py)  one Adw.ApplicationWindow per open book: the book view,
│                        contents/annotations/search sidebar, typography popover, progress bar
│   └─ BookView (widgets/book_view.py)  a WebKit.WebView on the bookcase:// scheme running
│                        src/reader/reader.html: our bridge (reader.js) over the vendored
│                        foliate-js (src/reader/foliate/, MIT, pinned in its README)
│   └─ PdfView (widgets/pdf_view.py)  a PDF's pages drawn by Poppler, BookView's interface;
│                        places are pdf_location.py's 'page:N' strings; TXT and CBR open as
│                        converting.py's cached EPUB and CBZ copies
├─ Library (library.py)  the SQLite library (schema.py): books, files, authors, series, tags,
│                        identifiers, shelves, annotations, reading sessions, folders; the
│                        undo stack; the `changed` signal
├─ search.py             the search syntax (title:, author:, tag:, -word, "a phrase", or) to SQL;
│                        a smart shelf is a saved search
├─ formats/              per-format metadata and cover readers (epub also writes): epub, mobi,
│                        pdf, comic, fb2, txt; formats.read(path) -> BookInfo
├─ importing.py          adding files (copied into the library folder), scanning watched
│                        folders in place, content hashes, duplicates
├─ calibre.py            reading a Calibre library (metadata.db) to link its books in place
├─ existing_books.py     the welcome's look for a user's Calibre libraries and book folders
├─ covers.py             the cover store and its thumbnail cache
├─ online.py             metadata and covers from Open Library (and Google Books with a key)
├─ opds.py               online catalogues (OPDS 1.2 and 2.0): feeds, search, downloads into
│                        the library (pages/discover.py, pages/catalog.py)
├─ bulk_metadata.py      Find Metadata for many books: a rate-kept queue, the changes, one
│                        undo step (dialogs/bulk_metadata.py)
├─ lookup.py             Look Up: StarDict dictionaries, Wiktionary, Wikipedia summaries
├─ speech.py             Read Aloud: the speech engine (speech-dispatcher) and the player
├─ devices.py, kepub.py  e-readers on USB (Kobo, Kindle, any reader with a books folder);
│                        sending a book, as kepub for a Kobo
├─ mail.py, passwords.py Send to Kindle by e-mail (SMTP); passwords in the keyring (libsecret)
├─ kosync.py             reading sync over KOReader's protocol: app.sync (reader_sync.py,
│                        dialogs/sync_prefs.py)
├─ exporting.py          copies of books with their metadata written in (export, devices)
├─ annotations.py        highlights and notes to Markdown; Kindle's My Clippings.txt in
└─ stats.py              reading time, speed (time left), streaks, pages and goals, from
                         reading sessions and books' finished dates
```

Data lives in `$XDG_DATA_HOME/bookcase/` (`library.sqlite`, `covers/`, `thumbnails/`), the
.Devel build in `bookcase-devel/`; `BOOKCASE_DATA_DIR` overrides both (the tests set it to a
temporary directory, `--demo` to build/demo). Book files live where the user keeps them: books
added through the app are copied into the library folder (the `library-folder` setting,
`~/Books` by default) as `Author/Title.ext`; watched folders and linked Calibre libraries are
read in place. GSettings: one schema for both builds.

## Rules

- **The user's files are theirs**: a book file is never moved, renamed or rewritten in place.
  Added books are copied; metadata edits live in the library database; a copy sent to a
  device or exported carries the edited metadata. A linked Calibre library's metadata.db is
  read, never written. Removing a book from the library leaves its file; Move to Trash is a
  separate, confirmed action that trashes it (Gio, recoverable).
- **Model code has no GTK**: library.py, schema.py, search.py, formats/, importing.py,
  calibre.py, covers.py, online.py, opds.py, lookup.py, speech.py, devices.py, kepub.py, exporting.py,
  annotations.py, stats.py, converting.py, pdf_location.py, mail.py, passwords.py,
  kosync.py, bulk_metadata.py and existing_books.py import Gio/GLib/GObject/GdkPixbuf/Poppler (passwords.py: Secret) at most,
  and are tested without a display. Pages and widgets call them; they never reach into
  widgets.
- **Everything undoable**: a change to the library goes through `Library.undoable(label)`, so
  `app.undo()` (Ctrl+Z) can put it back; a destructive action shows a toast with Undo, not a
  confirmation, except Move to Trash and removing a shelf with books (an `AdwAlertDialog`).
  Reading progress and sessions are not undo steps.
- **Book content is untrusted**: the reader's page has a strict CSP, no network, no book
  scripts, no navigation (links out open in the browser after the user clicks them), no
  local storage. Only `bookcase://` serves the reader's files and the open book.
- **Blueprint 0.22** (`template $BookcaseName: Parent { … }`); a `.blp` compiles to a `.ui` of
  the same bare name, flat in build/src, listed in src/meson.build and
  src/bookcase.gresource.xml (tests/test_build_lists.py checks the lists, and po/POTFILES.in).
- **libadwaita first**: its widgets and style classes (`card`, `boxed-list`, `pill`,
  `title-1` to `title-4`, `heading`, `caption`, `dimmed`, `numeric`, `accent`, `success`,
  `warning`, `error`) before CSS; CSS only in `src/style.css`; no hard-coded colours (the
  accent is the user's) except the highlight colours and the reader's paper themes. Every
  page fits a 360 px wide window.
- **Strings**: `from gettext import gettext as _` in Python, `_("…")` in Blueprint; header
  capitalization for labels and menu items, sentence case for descriptions; a menu item that
  opens a dialog ends in `…`. The app's name in a string is "Bookcase"; "Calibre" names the
  other app ("Calibre library").
- **Actions and shortcuts**: `app.*` in main.py, `win.*` in window.py and reader_window.py;
  every shortcut goes in `src/shortcuts.py`, which feeds the accelerators and the Keyboard
  Shortcuts dialog (tests/test_shortcuts.py). The reader's page keys (arrows, Space, h/l…) are
  the reader window's own key controller, never application accelerators.
- **Style**: `ruff check .` clean (`pyproject.toml`: 100 columns, single quotes). 4-space
  indents, no type annotations, a docstring where a module or function is not obvious,
  comments that describe the code as it is. Every source file starts with the two SPDX lines
  (tests/test_spdx.py), except the vendored foliate-js, which keeps its own. `log =
  logging.getLogger(__name__)`; no `print` in `src/`.
- **Tests**: stdlib `unittest` in `tests/test_<module>.py`, each starting with
  `from tests import ROOT` (it registers `src/` as `bookcase` and isolates settings and
  data). Logic is tested with a `Library` on a temporary path (`tests/support.py`'s
  `temporary_library()`); books for tests are made by `tests/support.py`'s `make_epub()` and
  friends, never checked-in copyrighted books. Widget tests use `tests/gtk.py`'s
  `@requires_gtk` and import template modules inside the test. Fixtures are invented: no real
  libraries, names or IDs.
- **Privacy**: the repository is public; no real library data in code, tests, fixtures, docs
  or screenshots. `scripts/run.sh --demo` and the scripts use build/demo, never the real
  library.

## More

- `.claude/rules/ui.md` (the window's seams, pages, dialogs, style), `reader.md` (the reader
  window, the bridge and foliate-js) and `gtk-notes.md` (toolkit behaviour found the hard
  way), loaded when the Read tool reads a file of that area; working through a shell, read
  them first.
- `docs/`: `user-guide.md` (for users), `decisions.md` (settled choices and why),
  `research/` (what was read before designing). `TODO.md`: what is not done.
- `scripts/`: `check.sh`, `run.sh [--demo]`, `headless.sh`, `screenshot.py`, `harness.py`,
  `demo_library.py` (the invented library in build/demo: generated EPUBs with drawn covers,
  for screenshots and --demo).

## Verifying a change

1. `scripts/check.sh` passes (byte-compile, ruff, meson build, unit tests, data validation).
   CI runs it in an Arch Linux container under Xvfb.
2. Anything visible: `scripts/headless.sh scripts/screenshot.py build/x.png --page PAGE`
   (also `--light`, `--size 360x640`, `--book TITLE`, `--read TITLE`), each looked at with
   the Read tool. Sessions building side by side wrap the build and install in
   `flock build/.lock sh -c '…'`.
3. `docs/user-guide.md` describes what the app does; a change that makes a line there or here
   wrong fixes it in the same commit.
