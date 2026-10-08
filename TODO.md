# Not done yet

Bookcase 0.1.0 was built in one day (2026-10-08). What it does is in docs/user-guide.md;
this is what it does not do yet, roughly in the order it should be done.

- **Reading sync (kosync).** KOReader's sync protocol is what Readest, Koodo, Komga and
  Calibre-Web-Automated speak; books are already keyed by its partial MD5 document id
  (importing.partial_md5). Needed: a server URL, user and key in Preferences (libsecret),
  push the percentage on close, pull on open, and a "jump to the other device's place?"
  toast.
- **OPDS.** Browsing a catalogue (Standard Ebooks, Project Gutenberg, a Calibre or Kavita
  server) and downloading into the library; foliate-js has an `opds.js` to start from. Later,
  serving the library as OPDS.
- **Send to Kindle by e-mail.** Amazon takes an EPUB mailed to the user's `@kindle.com`
  address; the research (docs/research/tech.md §5) has the SMTP recipe. Needs an SMTP account
  with an app password in libsecret, and an EPUB-only Kindle path when ebook-convert is
  missing.
- **Writing back to Calibre.** Linked Calibre libraries are read-only by design (decisions.md).
  An opt-in "keep Calibre in step" would need Calibre's SQL functions, its single-instance
  lock and read-only fallback while Calibre runs (calibre.md §3.4 and §3.10).
- **Conversion** beyond the Kindle case: a "Convert with Calibre…" action through
  `ebook-convert` when it is installed (MOBI to EPUB, EPUB to AZW3 for export).
- **PDF in the reader.** PDFs open in Document Viewer. A page view in the reader window
  (Poppler rendering into a Gtk.Picture, or foliate-js with PDF.js) would keep the progress
  and highlights in Bookcase.
- **Text to speech.** foliate-js has `tts.js` (SSML by sentence); speech-dispatcher or
  Spiel would speak it, with the sentence highlighted.
- **Dictionary.** Look Up opens Wiktionary or Wikipedia in the browser; an offline
  dictionary (StarDict or dictd files) in a popover would keep the reader in the window.
- **Reading statistics page.** Sessions are recorded (stats.py turns them into time left);
  a page of time read per day, books per month and a streak is not built.
- **Kindle clippings in.** annotations.py parses `My Clippings.txt` and matches books, but
  nothing in the UI imports it yet (a button on a Kindle's device page). Exporting a book's
  highlights as Markdown (annotations.to_markdown) also needs its menu item.
- **MTP readers.** 2024 Kindles mount over MTP (gvfs, no local path); devices.py skips them.
- **Kobo collections.** Shelves could become Kobo collections, which means writing
  `KoboReader.sqlite`; not done, and not without a backup and a schema check.
- **Flathub.** The manifest in build-aux/flatpak is untested (lxml is built from git there);
  the app ID and metainfo are ready. CI runs the tests on Arch under Xvfb only, with WebKit
  off (bubblewrap cannot start in the container), so the reader is not tested there.
- **Accessibility pass.** Orca has not walked the library or the reader; the reader's page
  is WebKit's accessibility tree, which foliate-js's paginated columns may confuse.
- **Performance** on very large libraries (10,000+ books) is untested beyond the virtualised
  grid; the first import of a large Calibre library has no estimate of time left.
- **Translations:** the strings are marked; no catalogue exists yet.
