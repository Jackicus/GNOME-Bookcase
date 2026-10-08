---
paths:
  - "src/**/*.blp"
  - "src/window.py"
  - "src/sidebar.py"
  - "src/reader_window.py"
  - "src/pages/**"
  - "src/widgets/**"
  - "src/dialogs/**"
  - "src/style.css"
---

# Window, pages, widgets and dialogs

Platform behaviour worth knowing is in `gtk-notes.md`; the reader's in `reader.md`.

- The app's seams (main.py): `app.library` (library.Library), `app.covers`
  (covers.CoverStore), `app.settings`, `app.devices` (devices.DeviceMonitor), `app.importer`
  (importing.Importer), `app.toast(text, undo=False)`, `app.report(error, context=None)`,
  `app.undo()`, `app.open_book(book_id)` (the reader window: one per book, raised when open),
  `app.add_files(gio_files)` (the import, with progress), `app.window()` (the library window).
  `app.open_path(path, done=None)` (read a file without adding it: library.OPENED),
  `app.keep_book(book_id)` (Add to Library for such a book).
  app.* actions: add-books, open-file, add-folder, link-calibre, preferences, shortcuts, about,
  undo, quit;
  import-clippings and export-highlights (dialogs/highlights.add_actions).
- The library window's seams (window.py): `show_root(key)`, `push(page)`, `pop()`,
  `show_book(book_id)` (pushes pages/book.py), `show_books(title, **filters)` (pushes a
  pages/books.py for an author, a series, a tag, a search), `search(query)`, `add_toast(toast)`,
  `set_dialog_open(bool)`, `undone(label)`. Root keys: `home`, `all`, `missing`, `authors`, `series`,
  `tags`, `status:reading`, `status:unread`, `status:finished`, `stats`, `discover`, `shelf:ID`,
  `device:ID`.
  A page finds the app through `pages.app()`. Dialog modules expose `present*(app, parent,
  …)` functions returning the dialog; a dialog calls `parent.set_dialog_open()` on map and
  close.
- A root page is an `Adw.NavigationPage` with its own `Adw.ToolbarView` and `Adw.HeaderBar`,
  made by `pages.make_root()` on the first visit and kept by the window; it refreshes from
  `app.library`'s `changed` signal while mapped (connected weakly on map, disconnected on
  unmap, debounced) and holds no state the library does not.
- Covers: `widgets/cover.py`'s `Cover` widget shows a book's thumbnail (loaded off the main
  thread through `app.covers.load_thumbnail(book, width, callback)`) and, for a book without
  a cover, draws one: the title and author on a colour picked from the title. Book grids are
  `Gtk.GridView`s over a `Gio.ListStore` of `widgets/book_item.py`'s `BookItem` (a GObject
  holding a book id and its `Book`), never a FlowBox: libraries reach 10,000 books.
- Every change to the library goes through its undoable methods and shows a toast with Undo
  (`app.toast(text, undo=True)`); Move to Trash and removing a shelf with books ask first.
- libadwaita widgets and style classes before CSS (`card`, `boxed-list`, `pill`, `title-1` to
  `title-4`, `heading`, `caption`, `dimmed`, `numeric`, `accent`, `success`, `warning`,
  `error`); CSS only in `src/style.css`, one commented block per widget; no hard-coded colours
  except the highlight colours, the drawn covers' palette and the reader's paper themes.
- Every page fits a 360 px wide window (a breakpoint in the page's `Adw.BreakpointBin`).
  Icon-only buttons have `tooltip-text`; decorative images are `accessible-role:
  presentation`; a cover is role IMG labelled with the title.
- Build, install and screenshot: `meson compile -C build && meson install -C build --quiet &&
  scripts/headless.sh scripts/screenshot.py build/NAME.png --light --page KEY` (also
  `--book TITLE`, `--read TITLE`, `--dialog NAME`, `--search QUERY`, `--size 360x640`), then
  look at the PNG with the Read tool. Sessions building side by side wrap the build and
  install in `flock build/.lock sh -c '…'`.
