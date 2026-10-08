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
  Patrons Circle uses Basic and should work, untested); downloads are listed for the session
  only (the Downloads popover; a restart forgets them, and a download cut off by quitting is
  not resumed); an entry's full record (`type=entry` detail links, OPDS 2's publication
  `self` link) is fetched when its sheet opens, never tried against a real server's; a
  catalogue page holds its books in a FlowBox, fine for pages of 25–100 but not for
  thousands scrolled through.
- **Library sharing, what is left.** Built (sharing.py, qr.py, dialogs/sharing_prefs.py,
  widgets/qr_code.py), tested with urllib and opds.py's own client against the server on
  127.0.0.1, the QR code against libqrencode's matrices and zbarimg; never tried with a real
  KOReader, Kobo or Kindle browser, Readest or Thorium here. Not yet: HTTPS (a self-signed
  certificate would make every e-reader browser refuse; Calibre's server is plain HTTP on a
  LAN too); IPv6 (it listens on IPv4 only); a pinned "Kindle" view with only the formats a
  Kindle's browser takes; OPDS 2.0 (JSON) feeds; OPDS-PSE page streaming for comics; drawn
  covers for books without one (they show none); a choice of which shelves to share; KOReader
  and Readest do not browse DNS-SD for OPDS, so the Avahi announcement (_opds._tcp,
  _http._tcp) helps only apps that do; the Flatpak needs `--system-talk-name=org.freedesktop.Avahi`
  for it (added to the Devel manifest).
- **Send to Kindle by e-mail, what is left.** Built (mail.py, passwords.py,
  dialogs/kindle_mail.py, a destination in dialogs/send.py). Never sent through a real
  server or to a real Kindle here (tests use a fake SMTP class). Not yet: OAuth (Gmail and
  Outlook.com may refuse password logins for some accounts; app passwords work today); a
  Cancel that interrupts a mail being sent (it stops between books); Amazon's per-mail
  "convert" subject for PDFs; sending several books in one mail.
- **Keep Calibre in Step, what is left** (calibre_write.py). Verified against Calibre's schema
  only (calibredb was not installed here; tests/test_calibre_write.py runs it when it is).
  Not yet: renaming the book's folder and files the way Calibre does after a title or author
  change (left to Calibre); writing metadata.opf ourselves (left to Calibre's
  metadata_dirtied); per-language title sort (English articles, as Calibre's trigger); a
  Flatpak sandbox without the host network cannot see Calibre's lock socket, so there only
  SQLite's own locking guards a write; Calibre's Check Library may list the
  `metadata.db.bookcase-backup-*` files as extra files in the library folder; the backup is
  not restored automatically when the check after a write fails (the row says which to
  restore); a rescan and a write take turns (calibre.LINK_LOCK) but a book edited during a
  write is written at the next pass.
- **Convert…, what is left** (converting.py, dialogs/convert.py). Not yet: Calibre's Flatpak
  (`flatpak run --command=ebook-convert com.calibre_ebook.calibre`) or, inside our own
  Flatpak, the host's ebook-convert through flatpak-spawn; several books at once; Undo
  leaves the converted file in the library folder; converting into a Calibre library (the
  new format goes to Bookcase's library folder, not Calibre's).
- **PDF, what is left.** PDFs open in widgets/pdf_view.py (Poppler), with selection across
  pages, tiles at high zoom, the zoom and layout kept per book (library book_state),
  right-to-left spreads and the cover alone, Read Aloud and Print. Not yet: forms and the
  PDF's own annotations (drawn, not editable; highlights are Bookcase's, not written into
  the file); a right-to-left catalogue compressed in an object stream is not seen (Poppler's
  GObject API has no direction: pdf_location.declares_rtl scans the file; the switch is
  there); Read Aloud reads running heads and folios as sentences, and a sentence cut by a
  page break is joined only when the next page starts with it (lower case); no OCR for
  scanned PDFs; Print… prints the whole PDF as the file has it (no paper theme, no page
  range preset); a drag past a line's end selects as Poppler does (on to the next line);
  page thumbnails or a page grid; reading sync of the zoom.
- **Read Aloud, what is left.** Built (speech.py, widgets/read_aloud.py, reader.js's tts*):
  speech-dispatcher through python-speechd or `spd-say`, a sentence at a time; a voice per
  language (the Voice menu); a sentence back and forward (Ctrl+Shift+Left/Right); pausing
  that resumes mid-sentence (speechd's pause/resume); the word being said underlined (SSML
  index marks through speechd); going on from the new page when the reader moves. Never
  heard here (no engine on the development machine: the tests use a stand-in speechd module
  and a fake `spd-say`), so whether espeak-ng and Piper report marks, and whether speechd's
  pause lands on a word, is untested. Not yet: Spiel or Piper directly; word marks and
  pause through `spd-say` (it has neither); a voice list grouped by speech module; reading
  fixed-layout EPUBs and comics (no text).
- **Look Up, what is left.** Built (lookup.py, widgets/lookup_popover.py,
  dialogs/lookup_prefs.py): StarDict offline, Wiktionary's REST definitions (English edition
  only), Wikipedia summaries; Preferences → Reading → Look Up (look up on selection or only
  when asked; online on or off; the dictionaries on or off and ordered; a link to
  FreeDict's StarDict downloads); English inflections (-s, -es, -ies, -ed, -ing, -er, -est)
  tried offline when the word itself is not found. Not yet: dictd files; lemmatizing other
  languages or irregular forms (went, mice); dictionaries installed while the app runs show
  after a restart (they are found once).
- **Fixed layouts, what is left.** Zoom (fit page, fit width, − and +), panning and single
  or two-page spreads are built (reader.js over foliate-fxl). Not yet: pinch to zoom on a
  touchpad; the zoom remembered per book; a spread's right-to-left first page for manga
  set by the user (the book's own direction is followed).
- **Reading statistics, what is left.** The Statistics page, goals, past years (the year
  drop-down), Year in Review (pages/year_review.py, saved as a PNG) and page counts (Edit
  Details, Find Metadata) are built. Not yet: time read on another device (a synced
  reader's sessions); a Year in Review for the year still going (offered for past years
  only); the day's refresh is a timeout, so after a suspend across 4 am it comes late
  (logind's PrepareForSleep is not watched); a Calibre library's #pages custom column is not
  read into books.pages.
- **Kindle highlights, what is left.** Import (dialogs/highlights.py) and Markdown export
  are built; an imported highlight's place is found by its text when the book is next opened
  (reader.js findTexts). Not yet: importing Kindle bookmarks (a location, no text); the
  text search misses a highlight whose words the Kindle's copy and Bookcase's copy spell
  differently (it stays listed, without a place); clippings in other languages' date
  formats keep no date.
- **Find Metadata for many books, what is left.** Built (bulk_metadata.py,
  dialogs/bulk_metadata.py), with a choice of up to three likely matches per book. Not yet:
  title and author corrections in bulk.
- **MTP readers, what is left.** Built: devices.py reaches every device through Gio
  (devices.Storage), so mtp:// mounts (2024+ Kindles, Android readers) are detected, listed,
  sent to, cleaned and read (My Clippings.txt) like USB drives. Never tried on a real MTP
  device here: tested with file:// URIs and a fake Gio layer (tests/fake_mtp.py). Not yet:
  a book on an MTP reader is known by its file name only (no metadata or content hash: that
  would mean fetching every file), so a book the user renamed may not match; Kindle cover
  thumbnails (`system/thumbnails`) and APNX page numbers are not written, over USB or MTP;
  an Android phone with a `Books` folder counts as a reader.
- **Kobo collections and progress, what is left.** Built (kobo.py; the device page's switch,
  opt-in per Kobo, and Bring Reading Progress From Kobo). Tested only on synthetic databases
  built from tests/fixtures/kobo_reader.sql, a schema put together from Calibre's KoboTouch
  driver and a MobileRead dump of the Shelf table, never on a real Kobo's KoboReader.sqlite.
  Not yet: collections on an SD card's books (`file:///mnt/sd/`); a shelf renamed in the
  library leaves its old collection on the Kobo; removing a library shelf leaves its
  collection; series and read status written to the Kobo (only read back); a restore button
  for the backup (it is copied back by hand).
- **Flathub.** The Flatpak builds, runs and passes its linters (GNOME 51; the reader, PDFs
  and covers checked in the sandbox); submitting is the user's (build-aux/flatpak/FLATHUB.md):
  rename the repository to Bookcase (the app ID's URL), tag a release, open the PR. In the
  Flatpak: no ebook-convert (Convert…), no push before suspend, folders outside the granted
  places come through the portal with `/run/user/…/doc/` paths (pages/book.py could show
  the host path, from the document portal's `user.document-portal.host-path` xattr). CI runs
  the tests on Arch with WebKit off; the WebKit tests run only locally in the Flatpak.
- **Accessibility, what is left.** tests/test_accessibility.py walks the library pages, a
  book's details, the dialogs and the reader under GTK's test accessibility backend (names,
  images, progress bars, fields, keyboard reach); the reader announces chapter changes;
  High Contrast gives the "auto" paper black on white and the covers' overlays an outline;
  `scripts/screenshot.py --text-scale 1.5` shows Large Text. Not yet: Orca has not walked
  the app by ear; the reader's page is WebKit's own accessibility tree (foliate-js's
  paginated iframes), unchecked over AT-SPI (the headless session has no registry); Tab's
  real order is checked by position only (GTK's focus moves cannot be driven headless);
  the headless harness cannot force High Contrast, so it has no screenshot.
- **Performance.** scripts/perf.py measures a 10,000-book invented library: the first frame
  about 0.65 s (All Books) to 0.75 s (Home) from the process start, of which libadwaita's
  own start is about 0.3 s (a hello-world Adw window takes 0.55 s here). The second the
  earlier figures added was headless.sh's desktop portal starting on GTK's first call;
  perf.py starts it before the clock. Left: kosync.py's urllib import (20 ms) at startup;
  the Home page's drawn covers (25 ms for 48). The first import of a large Calibre library
  has no estimate of time left.
- **Library polish not done:** Group Series stacks only All Books' covers (not shelves,
  reading states or the list); Recently Opened shows the five latest (older ones appear as
  newer ones are forgotten or added) and is not on the welcome, so a library with only
  opened books does not list them; a 360 px welcome shows the found rows below a large icon
  (scroll).
- **Translations:** po/bookcase.pot is made (`meson compile -C build bookcase-pot`) and
  tests/test_i18n.py keeps the strings translatable; no language has a catalogue yet.
  xgettext warns that it reads `.blp` files as C (which is what reads their `_("…")`).
