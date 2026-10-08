# Not done yet

Bookcase 0.1.0 was built in one day (2026-10-08). What it does is in docs/user-guide.md;
this is what it does not do yet, roughly in the order it should be done.

- **Reading sync, what is left.** Built (kosync.py, reader_sync.py, dialogs/sync_prefs.py),
  tested against a fake kosync server on 127.0.0.1; never against sync.koreader.rocks or a
  real KOReader device here. Not yet: KOReader's XPointers turned into CFIs (Readest does
  this; Bookcase jumps by percentage, which lands within a page or so); a position pushed
  while Bookcase is suspended mid-push is retried at the next start, not before sleep (no
  logind inhibitor is taken); highlights and reading time are not synced (kosync carries
  only the position); a book opened from Files without adding syncs under its file's id
  only.
- **OPDS, what is left.** Browsing, searching, filters and downloading are built (opds.py,
  pages/discover.py, pages/catalog.py, dialogs/catalog_entry.py, dialogs/add_catalog.py),
  tested against invented feeds on a local server and tried by hand against Project
  Gutenberg, ManyBooks, Wolne Lektury and Cantook's OPDS 2 feed; never against a real
  Calibre-Web, Kavita or Komga server. Not yet: OPDS-PSE page streaming (Komga, Kavita);
  catalogues that need a login form or OAuth rather than HTTP Basic (Standard Ebooks'
  Patrons Circle uses Basic and should work, untested); a download queue view (downloads
  only toast); an entry's full record (`type=entry` detail links) is not fetched; a
  catalogue page holds its books in a FlowBox, fine for pages of 25–100 but not for
  thousands scrolled through; serving the library as OPDS.
- **Send to Kindle by e-mail, what is left.** Built (mail.py, passwords.py,
  dialogs/kindle_mail.py, a destination in dialogs/send.py). Never sent through a real
  server or to a real Kindle here (tests use a fake SMTP class). Not yet: OAuth (Gmail and
  Outlook.com may refuse password logins for some accounts; app passwords work today); a
  Cancel that interrupts a mail being sent (it stops between books); Amazon's per-mail
  "convert" subject for PDFs; sending several books in one mail.
- **Writing back to Calibre.** Linked Calibre libraries are read-only by design (decisions.md).
  An opt-in "keep Calibre in step" would need Calibre's SQL functions, its single-instance
  lock and read-only fallback while Calibre runs (calibre.md §3.4 and §3.10).
- **Conversion** beyond the Kindle case: a "Convert with Calibre…" action through
  `ebook-convert` when it is installed (MOBI to EPUB, EPUB to AZW3 for export).
- **PDF, what is left.** PDFs open in widgets/pdf_view.py (Poppler). Not yet: selecting
  across pages (a selection stays on the page it started on); tiles at high zoom (beyond
  MAX_PIXELS a page's texture is scaled, so 400% on a big page blurs); remembering the zoom
  per book; forms and the PDF's own annotations (drawn, not editable); right-to-left page
  order for spreads; Read Aloud for PDFs.
- **Read Aloud, what is left.** Built (speech.py, widgets/read_aloud.py, reader.js's tts*):
  speech-dispatcher through python-speechd or `spd-say`, a sentence at a time. Never heard
  here (no engine on the development machine: the tests use stand-ins and a fake
  `spd-say`). Not yet: choosing a voice; word-by-word highlighting (speech-dispatcher's
  index marks through speechd); Spiel or Piper directly; skipping a sentence back or forward;
  reading fixed layouts (PDF text through Poppler); stopping when the user jumps elsewhere
  (it goes on from the new page). Pausing re-speaks the sentence from its start.
- **Look Up, what is left.** Built (lookup.py, widgets/lookup_popover.py): StarDict
  offline, Wiktionary's REST definitions (English edition only: the other editions have no
  definition endpoint), Wikipedia summaries. Not yet: dictd files; a setting to never send
  words online (or to look up only on request rather than on every word selected); the
  dictionaries listed and chosen in Preferences; lemmatizing (a plural or a conjugated form
  is looked up as it stands, Wiktionary's own "plural of" entry aside).
- **Reading statistics, what is left.** The Statistics page and goals are built
  (pages/stats.py). Not yet: editing a book's page count (books.pages is set by nothing but
  the demo; Open Library's `number_of_pages` could fill it in Find Metadata); finished books
  per year for past years (a year picker); a year-in-review; time read on another device
  (a synced reader's sessions); the page refreshing itself when the day turns over while it
  is open.
- **Kindle highlights, what is left.** Import (dialogs/highlights.py) and Markdown export
  are built; an imported highlight's place is found by its text when the book is next opened
  (reader.js findTexts). Not yet: importing Kindle bookmarks (a location, no text); the
  text search misses a highlight whose words the Kindle's copy and Bookcase's copy spell
  differently (it stays listed, without a place); clippings in other languages' date
  formats keep no date.
- **Find Metadata for many books, what is left.** Built (bulk_metadata.py,
  dialogs/bulk_metadata.py). Not yet: choosing between several candidates for a likely
  match (only the best is offered); title and author corrections in bulk.
- **MTP readers.** 2024 Kindles mount over MTP (gvfs, no local path); devices.py skips them.
- **Kobo collections.** Shelves could become Kobo collections, which means writing
  `KoboReader.sqlite`; not done, and not without a backup and a schema check.
- **Flathub.** The manifest in build-aux/flatpak is untested (lxml is built from git there);
  the app ID and metainfo are ready. CI runs the tests on Arch under Xvfb only, with WebKit
  off (bubblewrap cannot start in the container), so the reader is not tested there.
- **Accessibility pass.** Orca has not walked the library or the reader; the reader's page
  is WebKit's accessibility tree, which foliate-js's paginated columns may confuse.
- **Performance.** scripts/perf.py measures a 10,000-book invented library: the window's
  first frame comes about 0.2 s (All Books) to 0.35 s (Home) after the window is made, but
  the process start before it (Python, GTK, the imports) is another second and more, which
  a lazier import of the pages and dialogs would cut. The first import of a large Calibre
  library has no estimate of time left.
- **Library polish not done:** grouping the grid by series (stacks, as the Series page
  draws them); a book opened without adding cannot yet be forgotten (its row stays,
  invisible); a 360 px welcome shows the found rows below a large icon (scroll).
- **Translations:** the strings are marked; no catalogue exists yet.
