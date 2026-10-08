---
paths:
  - "src/reader_window.py"
  - "src/reader_window.blp"
  - "src/reading.py"
  - "src/widgets/book_view.py"
  - "src/dialogs/note.py"
  - "src/reader/**"
  - "tests/test_book_view.py"
  - "tests/test_reader_window.py"
  - "tests/test_reading.py"
  - "scripts/reader_demo.py"
---

# The reader: window, bridge and foliate-js

- Layers: `reader_window.py` (GTK: bars, sidebar, popovers, keys, saving) → `widgets/
  book_view.py` (`BookView`: the WebKit view, the `bookcase://` scheme, Python↔JS) →
  `src/reader/reader.js` (our bridge) → `src/reader/foliate/` (vendored foliate-js, MIT, pinned
  in its README; never edit it, copy a new commit over it). `reading.py` holds the logic with
  no GTK (style dict, progress label, `SessionClock`) and is what tests cover without a display.
- The scheme: `bookcase://reader/<file>` from the gresource (`/io/github/jackicus/Bookcase/
  reader/`: every file must be listed in `bookcase.gresource.xml`, JS served as
  `text/javascript` or modules fail silently); the book as `bookcase://reader/book/<token>.
  <ext>`: same host as the page (the CSP's `'self'`; another host is another origin) and the
  real extension (foliate-js tells FB2/FBZ/CBZ apart by it; kepub → `.epub`). Books over
  `LARGE_FILE` go in as a `File` through a hidden file input that `run-file-chooser` answers.
- Security: the CSP in reader.html (no `script-src` but `'self'`, so the book's scripts never
  run: tests prove it), an ephemeral network session, no local storage, navigation denied in
  `decide-policy` except the page and `blob:`/`data:`/`about:` frames; links out reach Python
  as `external-link` and open with `Gtk.UriLauncher` (http, https, mailto only).
- Python → JS: `BookView._call(name, args)` = `call_async_javascript_function('return await
  globalThis.reader.<name>(JSON.parse(args))')`; it returns what the promise resolves to.
  Calls before the page says `ready` are queued; in the page every method but `open` awaits
  the open in progress (a call racing the open was lost before). Methods: open, setStyle,
  next, prev, goLeft, goRight, scroll, start, end, nextSection, prevSection, back, forward,
  goTo, goToFraction, select, clearSelection, search, clearSearch, setAnnotations,
  addAnnotation, removeAnnotation, setBookmarks, showProgress, getTOC, getSectionFractions,
  ttsStart, ttsNext (the next sentence's text, highlighted and turned to; null at the end),
  ttsStop, findTexts ([{id, text}] → [{id, cfi, fraction}]: imported Kindle highlights
  found by their words, on load; the window stores the CFI, no undo step) (and `_contents`, `_cfi` for tests and the demo, through `BookView.evaluate`).
- JS → Python: one handler, `bookcase`, JSON `{type, …}`: ready, loaded (title, dir,
  fixedLayout, sectionFractions, toc), relocated (fraction, cfi, start, reason, chapter,
  page, section, location, time, atStart, atEnd, bookmark, jumpedFrom, canGoBack,
  canGoForward), selection (cfi, text, rect, fraction; cfi null when cleared), annotation
  (cfi, rect), search-result (label, items [{cfi, pre, match, post}]), search-progress,
  search-done (query, count), history, toggle-chrome, external-link (href), error (message).
  BookView turns them into signals of the same names ('annotation-activated', 'toc-ready').
- The first `relocated` can carry `fraction: null` (before layout): ignore it. A jump (goTo,
  goToFraction, start/end, select, an internal link) sends `jumpedFrom` once, with the next
  relocation: the window's "Back to …" button.
- Rects are in the page's CSS pixels = the view's widget pixels (zoom 1): a section's rects
  are offset by its iframe's `frameElement` rect.
- Clicks: a third of the width each (back, chrome, forward) in paginated mode, chrome only in
  scrolled mode, after 220 ms (a double click selects a word, not a page turn); a click on a
  highlight or with text selected never turns a page. The wheel turns one page per notch
  (paginated); Ctrl+wheel is the window's (text size).
- Keys never reach the page: the view is non-focusable and the window's key controller runs
  in the capture phase (`shortcuts.READER`), leaving plain keys to a focused entry; Adw.Dialogs
  over the window call `set_dialog_open()` so their text box gets its keys. With the focus
  visible (reached by Tab) in the sidebar or a popover, the plain page keys (arrows, Space,
  Home, End…) are the focused widget's, and Escape in a popover closes it first.
- The paginator hides the running head on a chapter's first page (by design). The footer's
  percentage shows only while the window's bars are hidden (`show_progress`).
- Footnotes: foliate-js's `FootnoteHandler` renders into a second `foliate-view`, which must
  be in the document before it loads: reader.js puts it in `#footnote` on `before-render`.
- Annotations are the library's (`kind` highlight or bookmark, `location` a CFI); the window
  re-reads them on `changed('annotations')` and sends the page the whole set
  (`setAnnotations` diffs), so Undo needs nothing special. Bookmarks are only CFIs to the page:
  `relocated.bookmark` says which is on screen.
- Progress: `library.set_progress()` 1 s after the last relocation and on close (it also moves
  an unread book to reading); sessions via `reading.SessionClock`, logged on close and when
  the reader returns after 5 idle minutes; reaching the end (`atEnd`, not the opening
  relocation) marks the book finished once per window, with an Undo toast. A book at its end
  that is not finished (marked unread or reading again) opens at its start. Nothing is
  saved after closing (the view's late messages are dropped).
- Web process death: reload in an idle, at the last CFI (more than 3 in a minute: an error
  page). Tests: `terminate_web_process()`.
- Paper themes live in `reading.THEMES` and in style.css's `.reader-page.theme-*` (a test checks
  they agree); highlight colours in reader.js's `HIGHLIGHTS` and style.css's `.color-*`.
- PDFs: `widgets/pdf_view.py`'s `PdfView` (Poppler: a render thread with its own document,
  an LRU of textures, a GSK colour matrix for the paper themes) has BookView's methods and
  signals; the window swaps it into `content_stack` for the BookView and drives `self.view`
  (`window.is_pdf`; `self.book_view` stays the template's BookView). Locations are
  `pdf_location` strings (`page:N@offset`, highlights `page:N#x0,y0,x1,y1;…` in PDF points
  from the page's top-left). PDFs have their own reader-pdf-scrolled; the bigger/smaller
  keys and Ctrl+scroll zoom; the Text and Layout popover hides the typography for them.
  relocated adds `pages` (progress_text says "Page 3 of 120").
- TXT and CBR open as EPUB/CBZ copies from `converting.prepare()` (a thread; the 'loading'
  spinner), cached in `$XDG_CACHE_HOME/bookcase/converted`; progress stays the book's.
- Not done: fixed-layout zoom controls for EPUB/CBZ, custom themes.
- Screenshots: `scripts/headless.sh scripts/screenshot.py OUT --read TITLE`, or
  `scripts/reader_demo.py OUT [--light] [--size WxH] [--theme T] [--select] [--lookup]
  [--read-aloud] [--popover] [--search Q] [--sidebar PAGE] [--missing] [--pdf] …` (no
  library window, its own book).
  WebKit tests skip with `BOOKCASE_NO_WEBKIT=1` or when the web process cannot start.
- Look Up (widgets/lookup_popover.py over lookup.py): a selected word's definition in the
  selection popover in a wide window, a bottom sheet (`Adw.Dialog`, BOTTOM_SHEET) in a narrow
  one; any view's 'selection' signal feeds it. A dialog over a window that cannot be resized
  opens in a window of its own (reader_demo.py makes its window resizable for --lookup).
- Read Aloud (widgets/read_aloud.py, speech.py): offered only when speech.engine() finds
  speech-dispatcher (python-speechd, else `spd-say`); the page's TTS (foliate-js tts.js at
  sentence granularity) highlights each sentence with an overlayer key that starts with
  foliate-js's search prefix, so a click on it is no annotation's.
- Sync (reader_sync.py over kosync.py, app.sync): `ReaderSync(window)` adds an Adw.Banner
  top bar (another device's newer place, Go There) and a section in the main menu (status,
  Sync Now); the window calls its `relocated(place)` and `close()`. The first relocation is
  the opening place: it pulls, never pushes. `reader_demo.py --sync` shows the banner from a
  fake server (tests/fake_kosync.py).
