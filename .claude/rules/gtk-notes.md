---
paths:
  - "src/**/*.blp"
  - "src/window.py"
  - "src/sidebar.py"
  - "src/reader_window.py"
  - "src/pages/**"
  - "src/widgets/**"
  - "src/dialogs/**"
---

# GTK 4.22, libadwaita 1.9 and PyGObject 3.56: behaviour this code depends on

Each of these was checked against the toolkit, or found the hard way building this app or
its sibling (Retain, by the same author, on the same stack).

## Widgets and lifetimes

- A widget that can be dropped (a pushed page, a dialog, a row) connects a child's or an
  owned object's signal through `widgets.util.connect_weak(obj, signal, self._method)`, never
  `obj.connect(signal, self._method)`: the cycle through C keeps the widget alive for ever.
  Follow a widget's lifetime with a GObject weak reference (`obj.weak_ref()`), not `weakref`:
  PyGObject drops a widget's Python wrapper while the widget lives and makes a new one.
- A Python subclass of `Gtk.TextChildAnchor` crashes on insert (GTK builds the anchor's
  segment in `gtk_text_child_anchor_new`, which `g_object_new` skips): use a plain anchor and
  hang a Python attribute on it; PyGObject keeps it alive through a toggle reference.
- The value `Gsk.RoundedRect().init_from_rect(...)` returns wraps a dead temporary (garbage
  bounds, nothing drawn, pixman warnings): keep the instance and call `init_from_rect` on it.
- A Python `do_measure` on a widget with a layout manager is never called: set the layout
  manager to None and implement both `do_measure` and `do_size_allocate`.
- Reparenting a widget during allocation (a breakpoint's `apply` handler moving a toggle
  group into a bottom bar) leaves a stale frame: do it from an idle.
- `Adw.NavigationView` refuses a second page with the same tag: a page that can be pushed
  over another of its kind (a book's details over a filtered list over a book's details)
  has no tag.
- `Adw.Dialog` opens as a bottom sheet only in a window of at most 450×360 px; at 360×640 it
  is a window of its own (which a screenshot of the main window misses: scripts/screenshot.py
  shoots the dialog's window). Keyed window actions are disabled while a dialog is open
  (`window.set_dialog_open`), so a dialog's entry gets its keys.
- `Adw.Sidebar` (1.9) cannot nest or indent. Its `setup-menu` signal says which item a
  section's `menu-model` opens on.
- A `Gtk.Builder` cannot load a `.ui` that declares a template, so a menu two pages share is
  built in code, not pulled out of a template's `.ui`.
- A `WebKit.WebView` that has the focus takes the keys: the reader window's key controller
  runs in the capture phase on the window, so page turns work whichever widget is focused,
  and lets keys through to an entry (the search bar) that has the focus. Without WebKit, or
  with `BOOKCASE_NO_WEBKIT=1` (CI: bubblewrap cannot start in the container), no view is
  made, and the tests that need one skip.
- In headless screenshots the accent colour is green (no Settings portal), so the accent and
  the success colour look alike there; on a desktop they differ.

## Lists

- A grid or list of books sorts by re-querying the library with an SQL `ORDER BY`, not
  through a `Gtk.Sorter` over the model: a library may hold tens of thousands of books.
- A `Gtk.MultiSelection` loses its selection when the model is refilled: a page keeps the
  selected book ids and selects them again after a refresh.
- `Gtk.GridView` recycles its item widgets: a widget bound to a `BookItem` drops whatever it
  loaded for the previous item on `unbind` (a cover still loading for another book), or it
  shows the wrong cover for a moment when it is scrolled back into view.
- A `Gtk.ScrolledWindow` that scrolls only sideways (the home page's rows) gets its child's
  minimum height, not its natural one: a wrapping label whose minimum is one line clips what
  is under it. A book tile's title reserves two lines (widgets/book_tile.py), so minimum and
  natural agree. `set_size_request` counts CSS margins: a margin a size depends on goes in
  the widget's `margin-*` properties instead.
- A wrapping label in a fixed-width tile has `max-width-chars` and `width-chars` 1, or its
  natural width (the whole text on one line) widens the tile.
