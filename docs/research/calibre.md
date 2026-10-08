# Calibre: research for a GNOME-native replacement

Researched 2026-10-08. Calibre's current release is **9.15** (18 Sep 2026); master is `numeric_version = (9, 15, 101)`.
Schema facts below come from calibre `master` source on GitHub (`resources/metadata_sqlite.sql`,
`src/calibre/db/backend.py`, `cache.py`, `write.py`, `tables.py`, `constants.py`, `utils/filenames.py`,
`ebooks/metadata/__init__.py`, `utils/lock.py`). I checked them against Python's stdlib `sqlite3`
(SQLite 3.53.4) on this machine. I also ran live tests against the metadata APIs on the same date.

Sources are cited inline. "Tested" means I ran it.

---

## 0. TL;DR for decision-making

1. **You can read and write a Calibre library with plain `sqlite3` if you register 2 functions first.** Every `INSERT` or `UPDATE` on `books` fires triggers that call `title_sort()` and `uuid4()`. Without them, the statement fails at prepare time with `no such function: title_sort`, even when no rows would change (tested). Register `concat`, `sortconcat`, `books_list_filter` and friends as well, only if you query the legacy `meta` and `tag_browser_*` views.
2. **Calibre keeps the entire `metadata.db` in an in-memory cache, and it does not notice changes made by another process.** All Calibre programs that write share one "db" lock: the GUI, `calibre-server` and `calibredb`. On Linux that lock is the abstract unix socket `\0calibre-singleinstance-<euid>-db`. Our app should **bind the same socket** while it has a library open for writing. That makes Calibre refuse to start, and it lets us detect a running Calibre and drop to read-only (§3.10).
3. **Library layout:** `<Author>/<Title> (<id>)/<Title> - <Author>.<ext>`, plus `cover.jpg` and `metadata.opf`.
   - `<Author>` is the *first* author only.
   - Names are transliterated to ASCII (unidecode) and Windows-sanitized.
   - Each component is limited to about 100 chars on Linux.
   - The DB stores the real paths (`books.path`, `data.name`), so Calibre accepts any path we write. We only need to match its naming if we want to be indistinguishable.
4. **Kindle:**
   - **Simplest:** e-mail an EPUB to `name@kindle.com` (Send to Kindle). Amazon converts it.
   - MOBI is no longer accepted by e-mail, and AZW3 never was.
   - USB/MTP sideloading of **EPUB does not work**. Calibre always converts to AZW3/MOBI/KFX for Kindle.
   - 2024+ Kindles use **MTP**, not mass storage.
5. **Kobo:** USB mass storage. Copying `.epub` into the device root works with zero conversion. For the better Kobo renderer, convert to `.kepub.epub` with **kepubify** (MIT, Go, single static binary, 40-80x faster than Calibre). Alternatively, reimplement its span/div injection in Python.
6. **Metadata sources in 2026:**
   - **Open Library** works keyless with a rate limit: 1 req/s, or 3 req/s with a `User-Agent` that carries contact info.
   - **Google Books API v1 keyless is dead:** HTTP 429 with `quota_limit_value: "0"` (tested twice). It needs a user-supplied API key.
   - Calibre itself uses the **legacy GData Atom feed** `books.google.com/books/feeds/volumes`, which still works keyless (tested).
   - **Amazon and Goodreads scraping broke in mid-2026.** r/Calibre threads are full of failed metadata downloads, and the community now begs users not to bulk-download.
7. **Complaints, ranked:**
   1. Dated, cluttered UI: the toolbar, nested menus, a non-native Qt look on GNOME.
   2. Preferences and conversion-option overload.
   3. The forced library folder structure and ASCII "filename mangling".
   4. Device-sync friction, especially Kindle.
   5. Broken metadata downloads.
   6. Slowness with big libraries.
   7. A weak viewer.

   Praise: "it just works", conversion, metadata editing, search, Kobo support, and free/offline.

   Strongest demand signal: the Calibre 9.0 **Bookshelf view** post got 1,526 upvotes, and "Calibre Zen", a modern UI fork, got 507. People love the engine and want a visual, simple front-end.

---

## 1. Feature set

Main reference: https://calibre-ebook.com/about and the user manual at https://manual.calibre-ebook.com/.

| Area | What Calibre does | Typical user | Power user |
|---|---|---|---|
| **Library** | One folder per library; multiple libraries; virtual libraries (saved-search views); tag browser (left pane facets); powerful search language (`tags:"=sf" and rating:>3`); saved searches; sort by any column; duplicate detection on add; **custom columns** (text, series, enum, rating, date, int, float, bool, comments, composite/template); full-text search over book contents (`full-text-search.db`); notes on authors/tags (`.calnotes/`); trash (`.caltrash/`, 14-day expiry); **Bookshelf view** (9.0, Jan 2026: spines on shelves); cover grid; "Check library"; read/progress columns sourced from the viewer (9.5) | Add books, browse covers, search, series/author grouping, read status | Custom columns, templates, virtual libraries, multiple libraries, FTS |
| **Metadata** | Edit single/bulk; title/author/author_sort/title_sort auto-rules (tweaks); series + index; tags; publisher; pubdate; languages; identifiers (isbn, amazon, google, goodreads…); rating 0-5 stars with half stars; comments (HTML); bulk search-and-replace with regex; "Polish books" to embed metadata into files | Fix title/author/series, add cover | Regex bulk edit, templates, embedding metadata |
| **Covers** | Download, generate (text-based placeholder covers), trim, set from file or from book; `cover.jpg` per book | High | — |
| **Metadata download** | Built-in sources: Amazon, Google, Google Images (covers), Open Library (covers only), Edelweiss. Plugins add Goodreads, Kobo, B&N, ISBNdb, Fantastic Fiction, Hardcover, Douban, etc. Source code is hot-updated from calibre's server between releases (`sources/update.py`) | High, but now often broken (§2) | Source priority, field selection |
| **Conversion** | Any of ~25 input formats to EPUB/AZW3/MOBI/KFX (plugin)/KEPUB (native since 8.0)/PDF/DOCX/TXT etc.; huge option tree (look & feel, heuristics, page setup, structure detection, ToC, per-format options); stored per-book in `conversion_options` table | Medium (mostly EPUB→Kindle) | Heuristics, regex, ToC XPath |
| **Device sync** | Auto-detects Kindle (USB mass storage + MTP), Kobo (KoboTouch driver: collections→shelves, read status, KEPUB on send), Nook, PocketBook, Tolino, Android/iOS apps via "Smart device"/wireless; auto-convert to device's preferred format on send; Kindle cover-thumbnail fix and APNX page numbers; e-mail sending (SMTP) incl. Send-to-Kindle | **High:** a core reason people install Calibre | Collections management, Kobo Utilities plugin |
| **Content server** | Built-in web server: browse/read/download, edit metadata, OPDS feed, users/auth | Medium (read on phone) | Remote libraries, `calibredb` over HTTP |
| **E-book editor** | Full EPUB/AZW3 IDE: code view, live preview, checker, spellcheck, ToC editor, regex/function search-replace, KEPUB editing (8.0) | Low | High (but Sigil competes) |
| **E-book viewer** | Separate app; highlights/annotations synced into DB (`annotations` table), read-aloud (Piper TTS), reading stats (9.4), page numbers | Medium | — |
| **News** | Recipes (Python) that scrape news sites into e-books and auto-send to device on a schedule | Low (niche but loved) | Custom recipes |
| **Plugins** | Python plugin system: metadata sources, devices, file-type actions, GUI actions, conversion (DeDRM is the most-used third-party plugin; Kobo Utilities, KFX Input/Output, Goodreads, Find Duplicates, Count Pages) | Medium (DeDRM) | High |
| **CLI** | `calibredb` (list/add/remove/set_metadata/search/export/fts…), `ebook-convert`, `ebook-meta`, `ebook-polish`, `calibre-server`, `calibre-debug` (https://manual.calibre-ebook.com/generated/en/calibredb.html) | — | High |

**What matters most for a GNOME replacement.** This is inferred from the r/Calibre topic counts in §2.2 and the HN threads.
- **Must have:** add/import books, a visual cover and shelf browser, search and filter, editing title/author/series/tags/cover, metadata and cover download, sending to Kindle and Kobo, read status.
- **Valued:** series view, a ratings and "read" column, duplicate detection, open with the system reader (Foliate), basic OPDS.
- **Power or niche** (can be deferred or delegated to Calibre): the conversion engine, the editor, news recipes, templates and composite columns, full-text search, multiple libraries, plugins. DRM removal is legally fraught and should stay out of scope.

---

## 2. Complaints and praise

### 2.1 Method

Reddit blocks both our search tooling and direct fetches with a 403. So I pulled posts through the Arctic Shift Reddit archive API (`arctic-shift.photon-reddit.com`):
- **2,900 r/Calibre posts** (2025-10-28 → 2026-10-07)
- **2,100 r/kobo posts** (since 2026-05)
- **1,900 r/kindle posts** (since 2026-07)

I classified titles and bodies by keyword regex, then read comments on the top threads. The HN and MobileRead material was fetched directly. Thread URLs are `https://www.reddit.com/r/<sub>/comments/<id>/`.

### 2.2 r/Calibre topic frequency (12 months, 2,900 posts; one post can hit several buckets)

| Bucket | Posts | Notes |
|---|---|---|
| Kindle (any) | 820 | Mostly Amazon lock-down (DRM, firmware, Kindle for PC dead 2026-06, MTP) |
| Crashes/errors/update/won't open | 624 | Overlaps heavily with DeDRM breakage |
| Conversion | 556 | EPUB→AZW3/KFX for Kindle, PDF conversion |
| Metadata download | 555 | Amazon/Goodreads blocked; "does nothing" |
| Kobo / KEPUB | 520 | |
| Series/tags/custom columns/sorting/collections | 397 | "How do you organize?" |
| Viewer/reading/annotations | 381 | |
| DRM | 374 | |
| Plugins | 329 | |
| Library folder/files/duplicates/rename | 246 | |
| Sync/cloud/NAS/Dropbox | 234 | Library on network drives (Calibre FAQ forbids) |
| Covers | 229 | |
| Content server/Calibre-Web/OPDS/wireless | 220 | |
| UI/look/theme | 114 | Low count, but the posts get very high scores |
| Editor | 90 | |
| News | 62 | |

### 2.3 Complaints ranked by frequency × intensity, with quotes

**1. The UI is dated, cluttered and confusing.** This is the most-repeated theme on HN, MobileRead and Slashdot over 13 years.
- "Calibre has the worst possible UI, it's just that nobody knows how to improve it." (9dev) / "Calibre tries to cram every workflow under the sun into tree menus … meanwhile, stuff like the book details panel is completely underused." (HN, Calibre 8.0 thread, https://news.ycombinator.com/item?id=43434421)
- "The UI is a mess of buttons and menus strewn about everywhere." (stavros); "Calibre has a hilariously bad UI, minus the hilarity." (cxr); "The UI feels very dated to me and the functionality is pretty slow and awkward." (NelsonMinar) (https://news.ycombinator.com/item?id=38316846)
- "I use Calibre a few times a year, and I'm quite confused every time." (InsideOutSanta, https://news.ycombinator.com/item?id=43432890)
- "It reminds me a bit of something you would find running on solaris in the 90s." (colechristensen, https://news.ycombinator.com/item?id=26960976)
- MobileRead (2011-13): the UI is "slanted very heavily towards the power user"; "still so so so technical and obscure". (https://www.mobileread.com/forums/showthread.php?p=1487530, https://www.mobileread.com/forums/showthread.php?p=1718942)
- r/Calibre 2026-08: "Calibre having UI that's anything modern? … that's unfathomable" / "15 seconds ago, or 15 years ago. Both would look pretty much identical." (schrodinger-the-cat, 50 and 41 points, thread 1vs7oo1)
- Demand signal: "**Calibre Zen** – a modern interface revamp of Calibre for people who love it" (507 upvotes, 1wi7tbw, https://zen.purplecandy.dev/). Its author notes that the usual reply to redesign ideas "was mostly that's what you can get out of QT." Also "I built a Calibre alternative that looks like Plex … I love what it does but I could never get past the UI" (1sfhtxs).
- Toolbar: "Titles are shown underneath main toolbar actions, but not in the statusbar or modal windows" (9dev). Defenders say the toolbar is customizable: "can be made fairly minimal with a dark theme and removed toolbar buttons" (HN 38316846).
- Plugins make it worse: "the interface is somewhat complicated and gets worse as you add plugins." (arkitaip, HN 38316846)

**2. Preferences and conversion settings overload.**
- "The preference pages remain a bit of a headache to navigate" (Fluorescence); "Calibre can be a real pain in the ass to set up the first time." (Gareth321) (HN 38316846)
- The conversion dialog exposes hundreds of options across about 10 tabs. Users mostly want one thing: "make this work on my Kindle/Kobo". r/Calibre conversion questions (556) are mostly about format choice ("AZW3 vs KFX", 1w84nqe) and blank or broken output ("Converted file to Kindle paper white showing up as blank", 1tv2l9w).
- "it does glitch sometimes in strange and assorted ways" (steelframe, HN 26960976).

**3. Forced library folder structure and filename "mangling".**
- "The one thing I really don't like about Calibre is how it will copy all of my ebooks into its own file organization." (thrower123, HN 26960976)
- "Still not possible to use your own directory architecture manifestly" (poulpy123); the counterpoint: "I see the folder as an exposed database which I shouldn't muck with." (bayindirh) (HN 43432890)
- anarcat (Debian dev): Calibre "enforces this `Author/Title/Title.epub` folder structure which is really *heavy* and annoying … It feels like iTunes", with a "recurring pattern of 'my way or the highway'" (https://anarc.at/software/desktop/calibre/)
- r/Calibre 2026-06 "Any way to prevent calibre from mangling filenames?": "replacing colons and question marks with underscores … this is an ext4 filesystem which can represent colons and question marks just fine" (1u9a843). CJK titles get transliterated to pinyin or romaji gibberish (https://github.com/funai/calibre-rename).
- "both the main application and the ebook reader insist on modifying the source file even when only viewing it" (lxgr, HN 43432890)

**4. Non-native look on GNOME (Qt).**
- Flathub issue "Make calibre use the system theme": "calibre doesn't really use the system theme but use the built-in one which is not good-looking." The workaround `CALIBRE_USE_SYSTEM_THEME=true` exists, but the issue is labelled *wontfix* (https://github.com/flathub/com.calibre_ebook.calibre/issues/34).
- "What is the proper way to tell calibre to use the system theme?": the system-style option "is imaginary at best on 2 different linux distros" (https://www.mobileread.com/forums/showthread.php?t=254507).
- Wayland: the Flatpak "doesn't start on gnome wayland session" without `QT_QPA_PLATFORM` tweaks (https://github.com/flathub/com.calibre_ebook.calibre/issues/57); redraw issues (https://forum.manjaro.org/t/calibre-rendering-issues-in-wayland/148617).

**5. Device sync friction, mostly Kindle.**
- The Amazon lock-down dominates 2026:
  - "Kindle for PC app dead today" (1u6tucw, 77 comments).
  - Firmware updates made "the drive capabilities vanish" on old Kindles, which switched to MTP (1s7nret, 116 comments).
  - Worry that the 2026 Kindles drop USB sideloading: "Sideloading Newest Kindles (2026) with Calibre?" (1wvbpu8, 85 comments). Commenters quote Amazon's 2026 setup page, which still documents USB-C file transfer.
- "MOBI and AZW3 Can no longer be sent to Kindle": "now Send to Kindle only accepts EPUB" (r/kindle 1wcjhir).
- Covers missing on sideloaded books, an old Amazon bug that Calibre works around by writing `system/thumbnails/thumbnail_<uuid>_<cdetype>_portrait.jpg`. 2026 threads 1wxmkhb and 1x0b613 recommend sending KFX or setting the ASIN identifier.
- Kobo: Calibre's own FAQ says "The Kobo has very buggy firmware. Connecting to it has been known to fail at random" (https://manual.calibre-ebook.com/faq.html). "Books won't add to series" (r/kobo 1wi11qo). "it required a lot of frustrating work" (KennyBlanken, HN 38316846).

**6. Metadata download broken or unreliable (new in 2026).**
- "It's not you – its Amazon": "Nothing I can do can get the metadata from Goodreads or Amazon into Calibre … they changed the way they format their html pages" (222 points, 1u580gh, 2026-06).
- "Please stop using 'Download Metadata and Covers' on more than 20 books at one time … we lost Amazon, we lost Fantastic Fiction, Goodreads was down in June … some ip's have been flagged" (536 points, 1vs7oo1, 2026-08). The top reply (466): "Shouldn't Calibre set limitations if there's an issue?" Another: "It needs to be limited in the UI" (168).
- "'Download Metadata and covers' does nothing" (1qnksq8). Also: "Calibre accepts to write metadata that is obviously wrong." (idoubtit, HN 38316846)

**7. Performance with large libraries and startup.**
- MobileRead: about 10k books gives "startup easily more than 10 minutes" (old hardware), and ">10,000 volumes … startup time is more than a minute, … basic searches can take more than 30 seconds". Causes: composite custom columns, the tag browser, antivirus scanning (https://www.mobileread.com/forums/showthread.php?p=2228013, https://www.mobileread.com/forums/showthread.php?p=1145442).
- "it was annoyingly slow on a low grade laptop" (HN 38316846). Even the viewer is slow to launch: "running the plain ebook reader takes a while" (ocdtrekkie, HN 26960976). Others report it "responds instantly and briskly" on Linux, so the picture is mixed.

**8. Viewer quality.**
- "rendering a page is very slow … Other ebook readers … give you a slider." (Macha, HN 43432890); "the renderer on Windows/Mac/Linux is an afterthought" (pasc1878). Calibre 9.x added reading stats, go-to-page and so on.

**9. Other:**
- Data-loss anecdotes: "had it so many data loses over the years" (PurpleRamen).
- Libraries on network drives or cloud sync are forbidden by the FAQ: "Do not put your calibre library on a networked drive", "Google Drive is incompatible with calibre". Users want this anyway (234 posts).
- No cross-library search.
- Too-frequent updates (weekly releases).
- Upstream attitude (anarcat).

### 2.4 Praise (what we must not lose)

- "Calibre is my favourite product – been using it for more than a decade" (HN 26960976); "one of those pieces of software that just works".
- "any format I just throw in there and Calibre fixes it" (apexalpha); "chews through weird ebooks and metadata without a hitch" (troupo).
- Search: "a search that is both powerful and works exactly how you would think it works"; "Fulltext search over the content of an entire library is a killer feature." (HN 38316846)
- Kobo: "it made the Kobo so much more useful" (arkitaip). Kindle transfer by drag-and-drop onto the device icon.
- Free, offline, no subscriptions; the author (Kovid) responds fast.
- The **Bookshelf view** (9.0): "Loving the new bookshelf view! It makes my collection feel less like a list of files and more like an actual library." (**1,526 upvotes**, 1rmwtgr, the top r/Calibre post of the year). This is a direct signal that a visual, "library-like" presentation is what users crave.

### 2.5 Existing alternatives (context)

- **Calibre-Web / Calibre-Web Automated:** web UIs on top of `metadata.db`. They are popular as a "modern face" and as a Kobo sync server. They are weaker on custom columns and virtual libraries.
- **Citadel** (Calibre-compatible, Tauri).
- **Bookx** (GTK4/Rust, stale since 2023).
- **Foliate** and **GNOME Books**: readers, not managers.
- **Koodo**, **Readest**: cross-platform readers.
- anarcat's survey (https://anarc.at/software/desktop/calibre/) found **no good replacement for the conversion engine or the metadata editor**.

This leaves a clear gap for a GNOME-native *manager*, not just a reader.

---

## 3. The Calibre library format (read/write compatibility spec)

### 3.1 On-disk layout

```
<Library>/
  metadata.db                     # SQLite, application_id 0x63616c69 ("cali"), user_version 28
  metadata_db_prefs_backup.json   # JSON dump of the `preferences` table
  full-text-search.db             # optional FTS of book text (separate SQLite, attached by calibre)
  .calnotes/notes.db (+ resources)# notes on authors/tags/series (calibre 7+)
  .caltrash/b/<book_id>/          # deleted books (whole folder); .caltrash/f/<book_id>/<ext> + metadata.json for deleted formats; expires after 14 days
  <Author>/
    <Title> (<book_id>)/
      <Title> - <Author>.epub
      <Title> - <Author>.azw3      # one file per format, same base name
      cover.jpg                    # books.has_cover=1
      metadata.opf                 # OPF 2.0 backup of the DB row, regenerated by calibre
      data/                        # optional "extra files" attached to the book
```
Constants are in `src/calibre/db/constants.py`: `COVER_FILE_NAME='cover.jpg'`, `METADATA_FILE_NAME='metadata.opf'`, `TRASH_DIR_NAME='.caltrash'`, `NOTES_DIR_NAME='.calnotes'`, `DATA_DIR_NAME='data'`, `BOOK_ID_PATH_TEMPLATE=' ({})'`, `DEFAULT_TRASH_EXPIRY_TIME_SECONDS = 14*86400`.

`books.path` stores `Author/Title (id)` using `/` separators. `data.name` stores the file base name without extension. The file on disk is `<library>/<books.path>/<data.name>.<format.lower()>`.

### 3.2 Core schema (verbatim from `resources/metadata_sqlite.sql`)

```sql
CREATE TABLE books ( id      INTEGER PRIMARY KEY AUTOINCREMENT,
                     title     TEXT NOT NULL DEFAULT 'Unknown' COLLATE NOCASE,
                     sort      TEXT COLLATE NOCASE,                 -- title sort, set by trigger
                     timestamp TIMESTAMP DEFAULT CURRENT_TIMESTAMP, -- "date added"
                     pubdate   TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                     series_index REAL NOT NULL DEFAULT 1.0,
                     author_sort TEXT COLLATE NOCASE,
                     path TEXT NOT NULL DEFAULT '',
                     uuid TEXT,                                     -- set by trigger
                     has_cover BOOL DEFAULT 0,
                     last_modified TIMESTAMP NOT NULL DEFAULT '2000-01-01 00:00:00+00:00');

CREATE TABLE authors ( id INTEGER PRIMARY KEY, name TEXT NOT NULL COLLATE NOCASE,
                       sort TEXT COLLATE NOCASE, link TEXT NOT NULL DEFAULT '', UNIQUE(name));
CREATE TABLE books_authors_link ( id INTEGER PRIMARY KEY, book INTEGER NOT NULL,
                       author INTEGER NOT NULL, UNIQUE(book, author));
CREATE TABLE tags ( id INTEGER PRIMARY KEY, name TEXT NOT NULL COLLATE NOCASE,
                    link TEXT NOT NULL DEFAULT '', UNIQUE (name));
CREATE TABLE books_tags_link ( id INTEGER PRIMARY KEY, book INTEGER NOT NULL, tag INTEGER NOT NULL, UNIQUE(book, tag));
CREATE TABLE series ( id INTEGER PRIMARY KEY, name TEXT NOT NULL COLLATE NOCASE, sort TEXT COLLATE NOCASE,
                      link TEXT NOT NULL DEFAULT '', UNIQUE (name));
CREATE TABLE books_series_link ( id INTEGER PRIMARY KEY, book INTEGER NOT NULL, series INTEGER NOT NULL, UNIQUE(book));
CREATE TABLE publishers ( id INTEGER PRIMARY KEY, name TEXT NOT NULL COLLATE NOCASE, sort TEXT COLLATE NOCASE,
                          link TEXT NOT NULL DEFAULT '', UNIQUE(name));
CREATE TABLE books_publishers_link ( id INTEGER PRIMARY KEY, book INTEGER NOT NULL, publisher INTEGER NOT NULL, UNIQUE(book));
CREATE TABLE ratings ( id INTEGER PRIMARY KEY, rating INTEGER CHECK(rating > -1 AND rating < 11),
                       link TEXT NOT NULL DEFAULT '', UNIQUE (rating));
CREATE TABLE books_ratings_link ( id INTEGER PRIMARY KEY, book INTEGER NOT NULL, rating INTEGER NOT NULL, UNIQUE(book, rating));
CREATE TABLE languages ( id INTEGER PRIMARY KEY, lang_code TEXT NOT NULL COLLATE NOCASE,
                         link TEXT NOT NULL DEFAULT '', UNIQUE(lang_code));
CREATE TABLE books_languages_link ( id INTEGER PRIMARY KEY, book INTEGER NOT NULL, lang_code INTEGER NOT NULL,
                         item_order INTEGER NOT NULL DEFAULT 0, UNIQUE(book, lang_code));
CREATE TABLE identifiers ( id INTEGER PRIMARY KEY, book INTEGER NOT NULL,
                           type TEXT NOT NULL DEFAULT 'isbn' COLLATE NOCASE, val TEXT NOT NULL COLLATE NOCASE, UNIQUE(book, type));
CREATE TABLE comments ( id INTEGER PRIMARY KEY, book INTEGER NOT NULL, text TEXT NOT NULL COLLATE NOCASE, UNIQUE(book));
CREATE TABLE data ( id INTEGER PRIMARY KEY, book INTEGER NOT NULL, format TEXT NOT NULL COLLATE NOCASE,
                    uncompressed_size INTEGER NOT NULL, name TEXT NOT NULL, UNIQUE(book, format));
CREATE TABLE custom_columns ( id INTEGER PRIMARY KEY AUTOINCREMENT, label TEXT NOT NULL, name TEXT NOT NULL,
                    datatype TEXT NOT NULL, mark_for_delete BOOL DEFAULT 0 NOT NULL, editable BOOL DEFAULT 1 NOT NULL,
                    display TEXT DEFAULT '{}' NOT NULL, is_multiple BOOL DEFAULT 0 NOT NULL, normalized BOOL NOT NULL, UNIQUE(label));
CREATE TABLE conversion_options ( id INTEGER PRIMARY KEY, format TEXT NOT NULL COLLATE NOCASE, book INTEGER,
                    data BLOB NOT NULL, UNIQUE(format,book));
CREATE TABLE books_plugin_data(id INTEGER PRIMARY KEY, book INTEGER NOT NULL, name TEXT NOT NULL, val TEXT NOT NULL, UNIQUE(book,name));
CREATE TABLE feeds ( id INTEGER PRIMARY KEY, title TEXT NOT NULL, script TEXT NOT NULL, UNIQUE(title));
CREATE TABLE library_id ( id INTEGER PRIMARY KEY, uuid TEXT NOT NULL, UNIQUE(uuid));
CREATE TABLE metadata_dirtied(id INTEGER PRIMARY KEY, book INTEGER NOT NULL, UNIQUE(book));   -- books whose metadata.opf needs rewrite
CREATE TABLE annotations_dirtied(id INTEGER PRIMARY KEY, book INTEGER NOT NULL, UNIQUE(book));
CREATE TABLE preferences(id INTEGER PRIMARY KEY, key TEXT NOT NULL, val TEXT NOT NULL, UNIQUE(key));  -- JSON values
CREATE TABLE last_read_positions ( id INTEGER PRIMARY KEY, book INTEGER NOT NULL, format TEXT NOT NULL COLLATE NOCASE,
        user TEXT NOT NULL, device TEXT NOT NULL, cfi TEXT NOT NULL, epoch REAL NOT NULL, pos_frac REAL NOT NULL DEFAULT 0,
        UNIQUE(user, device, book, format));
CREATE TABLE annotations ( id INTEGER PRIMARY KEY, book INTEGER NOT NULL, format TEXT NOT NULL COLLATE NOCASE,
        user_type TEXT NOT NULL, user TEXT NOT NULL, timestamp REAL NOT NULL, annot_id TEXT NOT NULL, annot_type TEXT NOT NULL,
        annot_data TEXT NOT NULL, searchable_text TEXT NOT NULL DEFAULT '',
        UNIQUE(book, user_type, user, format, annot_type, annot_id));
CREATE VIRTUAL TABLE annotations_fts USING fts5(searchable_text, content='annotations', content_rowid='id',
        tokenize='unicode61 remove_diacritics 2');
CREATE VIRTUAL TABLE annotations_fts_stemmed USING fts5(searchable_text, content='annotations', content_rowid='id',
        tokenize='porter unicode61 remove_diacritics 2');
-- New in schema v27/v28 (calibre 8.x/9.x):
CREATE TABLE books_pages_link ( book INTEGER PRIMARY KEY, pages INTEGER DEFAULT 0 NOT NULL, algorithm INTEGER DEFAULT 0 NOT NULL,
        format TEXT DEFAULT '' NOT NULL COLLATE NOCASE, format_size INTEGER DEFAULT 0 NOT NULL,
        timestamp TIMESTAMP DEFAULT CURRENT_TIMESTAMP, needs_scan INTEGER NOT NULL DEFAULT 0 CHECK(needs_scan IN (0, 1)),
        FOREIGN KEY (book) REFERENCES books(id) ON DELETE CASCADE);
CREATE TABLE book_storage ( id INTEGER PRIMARY KEY, book INTEGER NOT NULL, format TEXT NOT NULL COLLATE NOCASE,
        user_type TEXT NOT NULL, user TEXT NOT NULL, timestamp REAL NOT NULL, data TEXT NOT NULL DEFAULT '{}',
        UNIQUE(book, format, user_type, user), FOREIGN KEY (book) REFERENCES books(id) ON DELETE CASCADE);
PRAGMA application_id = 0x63616c69;
pragma user_version=28;
```
Full file: https://github.com/kovidgoyal/calibre/blob/master/resources/metadata_sqlite.sql. It also contains about 30 indexes, the `meta` view and ten `tag_browser_*` views. Older libraries are upgraded step by step by `src/calibre/db/schema_upgrades.py`. An existing library may be on an older `user_version`, and Calibre upgrades it when it opens the library.

### 3.3 Triggers (what fires on write)

- `books_insert_trg AFTER INSERT ON books`: `UPDATE books SET sort=title_sort(NEW.title), uuid=uuid4() WHERE id=NEW.id;`
- `books_update_trg AFTER UPDATE ON books`: `UPDATE books SET sort=title_sort(NEW.title) WHERE id=NEW.id AND OLD.title <> NEW.title;`
- `series_insert_trg` / `series_update_trg`: `UPDATE series SET sort=title_sort(NEW.name) WHERE id=NEW.id;`
- `books_pages_link_create_trigger AFTER INSERT ON books`: inserts a `books_pages_link(book)` row.
- `books_delete_trg AFTER DELETE ON books`: deletes the book's rows from all link tables, `data`, `comments`, `identifiers`, `conversion_options`, `books_plugin_data`, `last_read_positions` and `annotations`. It does *not* delete orphaned authors, tags or series; Calibre's Python code cleans those up.
- `fkc_*` triggers emulate foreign keys. Inserting into a link table with a missing book or item does `RAISE(ABORT,'Foreign key violation: …')`. Deleting an author, tag, series, publisher or language that is still linked aborts. Custom-column tables get the same triggers.
- `annotations_fts_{insert,delete,update}_trg` keep both FTS5 tables in sync.
- **`authors.sort` has no trigger.** The app must compute it. `books.author_sort` is also set by the app: Calibre uses `' & '.join(author_to_author_sort(a) for a in authors)`, or the per-author `authors.sort` values.

### 3.4 Custom SQL functions and collations: register them before writing

From `backend.py`, `class Connection(apsw.Connection)`:
```python
self.setbusytimeout(10000)
self.execute('PRAGMA cache_size=-5000; PRAGMA temp_store=2; PRAGMA foreign_keys=ON;')
self.createcollation('PYNOCASE', ...)            # not referenced by the stock schema
self.createscalarfunction('title_sort', title_sort)            # 1 arg  -- REQUIRED (books/series triggers)
self.createscalarfunction('author_to_author_sort', ..., 1)
self.createscalarfunction('uuid4', lambda *a: str(uuid.uuid4()), 0)   # REQUIRED (books insert trigger)
self.createscalarfunction('books_list_filter', lambda x: 1, 1)  # used by tag_browser_* views
self.createcollation('icucollate', icu_collator)
self.createaggregatefunction('sortconcat', SortedConcatenate, 2)      # used by `meta` view
self.createaggregatefunction('sortconcat_bar', ..., 2); ('sortconcat_amper', ..., 2)
self.createaggregatefunction('identifiers_concat', IdentifiersConcat, 2)
self.createaggregatefunction('concat', Concatenate, 1)               # used by `meta` view
self.createaggregatefunction('aum_sortconcat', AumSortedConcatenate, 4)
plugins.load_apsw_extension(self, 'sqlite_extension')  # C++ ICU FTS5 tokenizer (see pitfall 3)
```

**Tested with stdlib `sqlite3` on a fresh DB built from `metadata_sqlite.sql`:**
- `INSERT INTO books(title) …` → `OperationalError: no such function: title_sort`
- `UPDATE books SET title='x' WHERE id=1` on an *empty* table → the same error (prepare-time)
- `SELECT * FROM meta` → `no such function: sortconcat`
- After registering `title_sort` and `uuid4`, inserts work. `sort` and `uuid` get filled in, and the `books_pages_link` row is created.
- Inserting an annotation and then deleting the book works with stock FTS5.

Minimal Python registration:
```python
import sqlite3, uuid, re
ARTICLES = re.compile(r'^(A|The|An)\s+', re.I)   # calibre default for 'eng'
def title_sort(title, *_):
    title = (title or '').strip()
    m = ARTICLES.search(title)          # (calibre also strips one pair of leading/trailing quotes first)
    return (title[len(m.group(1)):] + ', ' + m.group(1)).strip() if m else title
def _concat(sep=','):
    class C:
        def __init__(s): s.v = []
        def step(s, x):
            if x is not None: s.v.append(x)
        def finalize(s): return sep.join(s.v) if s.v else None
    return C
class SortConcat:
    def __init__(s): s.d = {}
    def step(s, ndx, v):
        if v is not None: s.d[ndx] = v
    def finalize(s): return ','.join(s.d[k] for k in sorted(s.d)) if s.d else None
con = sqlite3.connect(path, timeout=10)     # match calibre's 10 s busy timeout
con.create_function('title_sort', -1, title_sort, deterministic=True)
con.create_function('uuid4', 0, lambda: str(uuid.uuid4()))
con.create_function('author_to_author_sort', 1, author_to_author_sort)
con.create_function('books_list_filter', 1, lambda x: 1)
con.create_aggregate('concat', 1, _concat())
con.create_aggregate('sortconcat', 2, SortConcat)
con.create_collation('PYNOCASE', lambda a, b: (a.lower() > b.lower()) - (a.lower() < b.lower()))
con.create_collation('icucollate', ...)   # e.g. locale.strcoll / PyICU
con.execute('PRAGMA foreign_keys=ON')
```

### 3.5 Sort rules

**title_sort** (`ebooks/metadata/__init__.py`, tweak `title_series_sorting='library_order'`):
- Strip the title.
- If it starts with a quote character from `quote_pairs`, remove the opening quote and its matching closing quote.
- Match the per-language article regex. The default comes from the UI language; English is `(r'A\s+', r'The\s+', r'An\s+')`.
- Move the article to the end: `"The Hobbit"` → `"Hobbit, The"`.
- `per_language_title_sort_articles` in `resources/default_tweaks.py` has lists for about 20 languages: `deu`: Der/Die/Das/Ein/Eine, `fra`: Le/La/Les/L'/Un/Une, `spa`, and others.
- When Calibre sets a title it calls `title_sort(title, lang=book_language)`. The trigger, though, uses the default language.

**author_to_author_sort** (tweak `author_sort_copy_method='comma'`):
1. If `method=='copy'`, return the name unchanged. If the name already contains a comma (`method=='comma'`), return it unchanged.
2. Remove bracketed text, then split on whitespace. A single token is returned unchanged.
3. If any token is in `author_name_copywords`, return the name unchanged. Copywords: Agency, Corporation, Company, Co., Council, Committee, Inc., Institute, National, Society, Club, Team, Software, Games, Entertainment, Media, Studios.
4. Skip leading prefixes: Mr, Mrs, Ms, Dr, Prof, with or without a trailing ".".
5. Peel trailing suffixes: Jr, Sr, Inc, Ph.D, Phd, MD, M.D, I, II, III, IV, Junior, Senior.
6. Result = `"<Last>, <First Middle>[ <suffix>]"`: `"J. R. R. Tolkien"` → `"Tolkien, J. R. R."`, `"Martin Luther King Jr."` → `"King, Martin Luther Jr."`.
7. `author_use_surname_prefixes` is False by default. When enabled, it keeps da/de/di/la/le/van/von attached to the surname.

`books.author_sort` = `' & '.join(sorts of authors in order)`.

### 3.6 Path and file-name construction (`backend.construct_path_name` / `construct_file_name`)

```
PATH_LIMIT = 100 (Linux/mac) / 40 (Windows)
book_id_str = ' (%d)' % id
# directory
l = PATH_LIMIT - len(book_id_str)//2 - 2
author = ascii_filename(first_author)[:l]; strip trailing ' ' and '.'; '' -> 'Unknown'
if author.upper() in {CON, PRN, AUX, NUL, COM1..9, LPT1..9}: author += 'w'
title  = ascii_filename(title.lstrip())[:l].rstrip() or 'Unknown'
path = f'{author}/{title}{book_id_str}'
# file base name
extlen = max(len(longest_fmt)+1, 14); l = (PATH_LIMIT - extlen - 2)//2    # = 42 on Linux
name = ascii_filename(title.lstrip())[:l].rstrip() + ' - ' + ascii_filename(first_author)[:l]; strip trailing '.'
```
`ascii_filename(s)`:
- `unidecode`-style transliteration to ASCII. Calibre ships its own `udc`, so CJK becomes pinyin.
- `?` becomes `_`, and control characters become `_`.
- Then `sanitize_file_name`: replaces `\ | ? * < " : > + /` and chars < 32 with `_`, collapses whitespace, turns `..` into `_`, replaces a trailing `.` or space with `_`, and replaces a leading `.` with `_`.

When the title or first author changes, Calibre renames the folder and the format files and updates `books.path` and `data.name`. That logic is `update_path` and is case-insensitive-FS aware. It **only uses `authors[0]`**.

### 3.7 Value encodings

- **Timestamps** (`timestamp`, `pubdate`, `last_modified`, datetime custom columns) are ISO-8601 text with a space separator in UTC: `isoformat(dt, sep=' ')` → `2026-10-08 04:07:26.123456+00:00`. Microseconds are optional.
  - "Undefined" dates (e.g. an unknown pubdate) are `UNDEFINED_DATE = 0101-01-01 00:00:00+00:00`.
  - On read, Calibre's C parser tolerates bare `YYYY-MM-DD HH:MM:SS` (the SQLite `CURRENT_TIMESTAMP` default, no tz, treated as UTC). If an integer like `2001` slipped in, it reads that as a year.
  - Always write full `+00:00` strings.
  - `pubdate` is date-only semantically: Calibre's `adapt_date` uses `parse_only_date`.
- **Ratings:** `ratings.rating` is an INTEGER 0-10, where **stars × 2** (half stars allowed). `books_ratings_link.rating` holds the `ratings.id`, not the value. Rating 0 or None means *remove the link*.
- **Authors:** names containing commas are stored with `|` instead of `,` (`tables.py` serialize `x.replace(',', '|')`). The display order of authors is the **`books_authors_link.id` order**. The `meta` view uses `sortconcat(bal.id, name)`, so insert links in display order.
- **Languages:** ISO 639-2/T three-letter codes (`eng`, `deu`, `fra`) in `languages.lang_code`. Order goes in `books_languages_link.item_order`. Calibre drops `und`, `zxx`, `mis`, `mul`.
- **Identifiers:** `type` is lower-case with `:` and `,` stripped. In `val`, `,` becomes `|`. Common types: `isbn`, `amazon`, `amazon_xx`, `google`, `goodreads`, `openlibrary`, `mobi-asin`, `uri`, `doi`.
- **Tags / series / publishers** are case-insensitive unique (`COLLATE NOCASE`). Calibre reuses an existing item that differs only in case. `series_index` is a REAL with default 1.0.
- **Comments** are HTML.
- **Format** (`data.format`) is upper case, e.g. `EPUB`. `uncompressed_size` is the file size in bytes.
- **has_cover** is `1` when `cover.jpg` exists in the book dir.
- **preferences** values are JSON. Notable keys: `field_metadata`, `library_view books view state`, `user_categories`, `saved_searches`, `virtual_libraries`, `column_color_rules`, `news_to_be_synced`. They are mirrored to `metadata_db_prefs_backup.json`.

### 3.8 Custom columns

A row in `custom_columns`:
- `label`: lower-case `[a-z][a-z0-9_]*`; the UI shows `#label`.
- `datatype`: one of `text, comments, series, enumeration, datetime, int, float, bool, rating, composite`.
- `display`: JSON, e.g. `{"is_names": true}`, enum `enum_values`, `composite_template`, `number_format`.
- `normalized = datatype not in (datetime, comments, int, bool, float, composite)`.

Tables per column `N`:
- **normalized:** `custom_column_N(id, value <type> NOT NULL [COLLATE NOCASE], link TEXT, UNIQUE(value))` plus `books_custom_column_N_link(id, book, value, [extra REAL for series index], UNIQUE(book, value))`, with fkc triggers and `tag_browser_custom_column_N` views.
- **not normalized:** `custom_column_N(id, book, value, UNIQUE(book))`.
- **composite:** no table; the value is computed from a template at runtime.

Deleting a column only sets `mark_for_delete=1`. Calibre drops the tables on the next start.

### 3.9 metadata.opf backups

- Calibre writes `metadata.opf` with `opf2.metadata_to_opf(mi)`. Format: OPF 2.0 `<package version="2.0">`.
- Contents: `dc:identifier opf:scheme="calibre"` (book id) and `uuid_id`, `dc:title`, `dc:creator opf:file-as=… opf:role="aut"`, `dc:date`, `dc:description`, `dc:publisher`, `dc:identifier opf:scheme="ISBN"`, `dc:language`, `dc:subject`, and `<meta name="calibre:series|series_index|rating|timestamp|title_sort|author_link_map|user_metadata:#col">`. Plus `<guide><reference type="cover" href="cover.jpg"/>`.
- Calibre queues books in `metadata_dirtied` and rewrites the OPFs in the background. **These OPFs are what "Restore database" uses to rebuild a corrupted `metadata.db`.**
- If we change metadata, either rewrite `metadata.opf` ourselves or `INSERT OR IGNORE INTO metadata_dirtied(book) VALUES(?)` so Calibre regenerates it next time it runs.

### 3.10 Pitfalls when both Calibre and our app touch a library

1. **Missing functions break writes.** Register `title_sort` and `uuid4` before any write to `books` or `series` (§3.4). Register the aggregates if you use the views.
2. **Calibre's in-memory cache.**
   - Calibre loads the whole DB into RAM (`Cache` is "An in-memory cache of the metadata.db file", https://manual.calibre-ebook.com/db_api.html). It never re-reads rows changed externally.
   - Its next write can clobber ours, and paths we renamed will look like missing files to it.
   - Calibre guards against itself with `singleinstance('db')`, held by the GUI, `calibre-server` and `calibredb`. `calibredb` exits with: "Another calibre program … is running. Having multiple programs that can make changes to a calibre library running at the same time is a bad idea."
   - On Linux the lock is an **abstract unix socket** named `\0calibre-singleinstance-<euid>-db` (`utils/lock.py`). We should:
     - **try to bind that socket on library open.** If binding fails with EADDRINUSE, Calibre is running: open read-only and show a banner.
     - **hold the socket while we have the library open for writing.** Calibre then refuses to start: "Cannot start calibre … Another calibre program that can modify calibre libraries … is already running".
   - Caveat: abstract sockets are per network namespace. A Flatpak with `--share=network` sees the host namespace. A sandbox without network access, or a different user, will not.
   - Calibre's content server can be bypassed with `CALIBRE_NO_SI_DANGER_DANGER`.
3. **FTS tokenizer mismatch.**
   - Calibre's `sqlite_extension` **replaces the FTS5 tokenizers named `unicode61` and `porter`** with its own ICU tokenizer (`db/sqlite_extension.cpp`: `xCreateTokenizer(fts5api, "unicode61", …)`).
   - If we delete a book or annotation with stock SQLite, the `annotations_fts` 'delete' runs with a different tokenizer than the one that indexed it. That can leave stale or inconsistent index entries.
   - Mitigation: avoid writing to `annotations`; after deletes, run `INSERT INTO annotations_fts(annotations_fts) VALUES('rebuild')` (and the same for `annotations_fts_stemmed`); or tell users to run Calibre's *Check library → rebuild annotations index* (added in 9.2.1).
   - `full-text-search.db` holds book-text FTS keyed by book id. Calibre reconciles it on its own.
4. **Schema version.** Read `PRAGMA user_version`. Refuse to write, or open read-only, if it is **greater** than the newest version we know (28 today). Calibre's schema upgrades are one-way. Never change `application_id`. Don't switch `journal_mode` to WAL: it persists in the file. Calibre uses the default rollback journal and warns against network shares.
5. **Keep paths consistent.** Our rename logic must move the folder and files and update `books.path` and `data.name` **in the same transaction-ish step**. Calibre trusts the DB path. If the files aren't there, "Check library" reports them missing. Remove the now-empty author folders.
6. **Update `last_modified`** (UTC ISO string) on every metadata change. The content server, Calibre-Web, Kobo sync and device drivers use it to detect changes.
7. **Clean up orphans** in authors, tags, series and publishers when you unlink them, or leave them for Calibre. The fkc delete triggers stop you deleting items that are still linked. Calibre removes unused items itself.
8. **Deleting books.** Calibre moves them to `.caltrash/b/<id>` (whole folder) and `.caltrash/f/<id>/` (single formats plus `metadata.json`). Either mimic this, so Calibre's "Restore from trash" works, or use the GNOME Trash (`Gio.File.trash`) and delete the DB row.
9. **IDs.** `books.id` is AUTOINCREMENT, so ids are never reused. The id is baked into the folder name. Never renumber.
10. **Unknown tables and columns.** Leave `books_plugin_data`, `conversion_options`, `book_storage`, custom columns we don't render, notes and the like untouched. Plugins such as Kobo Utilities and Goodreads Sync store state there.
11. **Cloud-synced or networked libraries.** The FAQ says: "Do not put your calibre library on a networked drive" and "Google Drive is incompatible with calibre". Users do it anyway. Detect `fuse`/`nfs`/`smb` mounts and warn.

---

## 4. Sending to Kindle and Kobo

### 4.1 Kindle

**How Calibre does it:**
- **USB mass storage** (pre-2024 Kindles; VID `0x1949`): copies into `documents/`.
  - Formats: `azw, mobi, azw3, prc, azw1, tpz, azw4, kfx, pobi, pdf, txt`, with **no EPUB** (`devices/kindle/driver.py`). An EPUB source is auto-converted to AZW3 or MOBI first.
  - Also uploads the cover thumbnail to `system/thumbnails/thumbnail_<ASIN-or-uuid>_<cdetype>_portrait.jpg`. This works around Amazon's sideloaded-cover bug and needs MOBI/AZW3/KFX EXTH data.
  - Also uploads `.apnx` page-number sidecars, into `<book>.sdr/` on newer devices.
- **MTP** (Kindle Scribe after FW 5.16.3; the 2024 Paperwhite 12th gen, Colorsoft and basic Kindle; the 2026 models): Calibre's MTP driver, with defaults `format_map ['azw3','mobi','azw','azw1','azw4','kfx','pdf']` and `send_to ['documents','kindle','books']`. Still no EPUB. Same thumbnail and APNX handling (`devices/mtp/defaults.py`, https://blog.the-ebook-reader.com/2023/09/01/kindle-scribe-now-supports-mtp-instead-of-usb-mass-storage-after-update/). Calibre 8.7 added APNX over MTP (https://www.howtogeek.com/calibre-ebook-manager-now-has-better-kindle-support/).
- **E-mail (Send to Kindle):**
  - Calibre's SMTP "Connect/share → Email" sends to `xxx@kindle.com`.
  - The FAQ: Amazon "stop[ped] accepting MOBI files emailed to @kindle.com", and "does not allow email delivery of AZW3 and new style (KF8) MOBI". Set the e-mail format to **EPUB**. Amazon's EPUB intake "is very flawed" and rejects some valid EPUBs (https://manual.calibre-ebook.com/faq.html).
  - r/kindle Sept 2026: "now Send to Kindle only accepts EPUB" (1wcjhir).
  - Accepted by e-mail: EPUB, PDF, DOC/DOCX, TXT, RTF, HTM/HTML, PNG/GIF/JPG/BMP. Up to 25 attachments, **50 MB per e-mail**. The web uploader allows 200 MB.
  - The sender must be on the "Approved Personal Document E-mail List" (Manage Content & Devices → Preferences). Mail from anyone else is silently dropped.
  - Amazon converts to KFX in the cloud, and the book syncs across devices as a "personal document".

**State of play in 2026:**
- New Kindles are MTP-only.
- Amazon killed "Kindle for PC" downloads (June 2026) and is tightening DRM.
- Rumours that the Oct-2026 Kindles drop USB transfer were debunked in-thread: Amazon's 2026 setup page still says "You can also use the USB-C to USB-C cable to transfer files to your Kindle" (r/Calibre 1wvbpu8, r/kindle 1wvccvg).
- Covers on sideloaded books remain flaky. Users fix it by converting to KFX or setting the `amazon` identifier (ASIN) (r/kindle 1wxmkhb).

**What is simplest for us, with no conversion engine:**
1. **Send to Kindle by e-mail, EPUB as-is.** Our app needs:
   - an SMTP account: GOA mail account, or the user's SMTP server plus a token in libsecret;
   - the user's `@kindle.com` address;
   - a one-time hint to approve the sender address.

   Optionally use a `xdg-email`/portal fallback (`org.freedesktop.portal.Email`) that pre-attaches the file in the user's mail client. That needs zero credentials and is the most GNOME-native route.
2. **USB/MTP:** only for books that already have a Kindle format (AZW3/MOBI/KFX/PDF/TXT). Copy them into `documents/` over GVfs MTP (`mtp://` via `Gio`) or a mass-storage mount. Write the `system/thumbnails` JPEG ourselves: about 330×500, name built from the EXTH ASIN (113) or uuid and the cdetype (`EBOK`/`PDOC`), which we read from the MOBI header. For EPUB-only books, either shell out to `ebook-convert` if Calibre is installed, or offer e-mail.
3. Do not attempt KFX.

### 4.2 Kobo

**How Calibre does it** (KoboTouch driver):
- The Kobo mounts as **USB mass storage** (label `KOBOeReader`). Books can go anywhere under the root. Calibre places them with its per-device save template; the manual's example device template is `{author_sort}/{title}/{title} - {authors}`.
- The device's DB is `.kobo/KoboReader.sqlite`:
  - `content` table: one row per book (`ContentType=6`) plus chapter rows (`ContentType=9/899`). `ContentID` for sideloaded books is `file:///mnt/onboard/<path>`.
  - Columns include `Title`, `Attribution`, `Series`, `SeriesNumber`, `SeriesNumberFloat`, `ReadStatus`, `___PercentRead`, `DateLastRead`, `ImageId`.
  - Shelves live in `Shelf` / `ShelfContent`.
- The Kobo **imports new files itself** on unplug and creates the `content` rows. Calibre then edits the rows on the next connection: collections→shelves, series, read status. It also writes `.kobo-images` cover thumbnails.
- Since **Calibre 8.0** (March 2025), EPUB is **converted to KEPUB automatically on send**. Right-click the Kobo icon to configure. KEPUB editing and viewing is native (https://linuxiac.com/calibre-8-0-brings-major-kobo-upgrade-and-kepub-support-arrive/). This replaced most uses of the KoboTouchExtended plugin.
- Kobo files are recognised as KEPUB by the **`.kepub.epub`** extension.
- FAQ: "do not keep large collections on the Kobo"; "The Kobo has very buggy firmware" (https://manual.calibre-ebook.com/faq.html).

**KEPUB / kepubify** (https://pgaskin.net/kepubify/, https://github.com/pgaskin/kepubify, **MIT**, Go, v4.0.4; repo active to Dec 2025):
- Why bother: "Page turns, font changes, highlighting, and searching are much more responsive on KEPUBs". Reading stats, footnote pop-ups and correct cover display need it. "Fixed-layout, page spreads, MathML, HTML5, and other EPUB 3 features are only supported on the KEPUB reader."
- "converts most books in a fraction of a second (40-80x faster than Calibre)".
- Transform (`kepub/transform.go`), applied to each XHTML file:
  1. Lenient HTML parse.
  2. Add a `<style id="kobostylehacks">div#book-inner { margin-top: 0; margin-bottom: 0;}</style>`.
  3. Wrap the body content in `div#book-columns > div#book-inner`.
  4. Wrap each sentence fragment in `<span class="koboSpan" id="kobo.<para>.<seg>">`. Highlights and bookmarks don't work without these.
  5. Optional punctuation smartening and cleanup (Adobe Adept meta, MS Office junk).
  6. Render as polyglot XHTML in UTF-8.
- OPF tweaks: mark the cover image `properties="cover-image"` and drop calibre bookmark files.
- The output name convention is `<name>.kepub.epub`.
- Sister tools:
  - `covergen` pre-generates `.kobo-images` thumbnails from the ImageId hash of the ContentID.
  - `seriesmeta` adds a `_seriesmeta` table plus triggers on `content` so series survive re-imports. NickelSeries, a device-side mod, is the alternative.

**What is simplest for us:**
1. **Plain EPUB to `<mount>/Books/…epub`** via `Gio`. That needs zero conversion and works today, using the Adobe RMSDK renderer.
2. **Optional KEPUB:**
   - bundle the `kepubify` binary in the Flatpak (MIT, static Go, small), or
   - port the span/div logic to Python with `lxml`/`html5lib`. That is a few hundred lines; the sentence-splitting state machine is the fiddly part.
3. **Series and read status:** after the Kobo has imported, optionally `UPDATE content SET Series=?, SeriesNumber=?, SeriesNumberFloat=?` where `ContentID='file:///mnt/onboard/…'`. Work on the live file only while mounted, and expect the Kobo to own the schema. Reading `ReadStatus` (0 unread / 1 reading / 2 finished) and `___PercentRead` back is an easy "sync reading progress to library" win.

---

## 5. Metadata and cover sources (tested 2026-10-08)

**Calibre's built-ins** (`src/calibre/ebooks/metadata/sources/`):
- `amazon.py`: HTML scraping, broken mid-2026 per r/Calibre.
- `google.py`: legacy GData feed (see below).
- `google_images.py`: covers via search-engine scraping.
- `openlibrary.py`: **covers only**: `https://covers.openlibrary.org/b/isbn/%s-L.jpg?default=false`.
- `edelweiss.py`.

Third-party plugins cover Goodreads, Kobo, B&N, ISBNdb (paid key), Hardcover (GraphQL, needs a user token), Fantastic Fiction (blocked 2026) and Douban. Calibre hot-updates source code from its own server between releases. The community now asks users to stay under about 20 books per batch (r/Calibre 1vs7oo1).

### 5.1 Open Library: keyless, recommended primary

Rules (https://openlibrary.org/developers/api):
- Rate limits: "1 request per second" by default and **"3 requests per second"** for identified requests. Identify with `User-Agent: AppName/1.0 (contact@example.org)`.
- "Please do not use our APIs to bulk download metadata"; use `search.json` for batches.
- No API key.

| Purpose | Endpoint | Notes / response shape (tested) |
|---|---|---|
| Search | `GET https://openlibrary.org/search.json?q=the+hobbit+tolkien&limit=5&fields=key,title,author_name,first_publish_year,isbn,cover_i,edition_key,language,publisher,subject` (also `title=`, `author=`, `isbn=`) | `{"numFound":204,"start":0,"docs":[{"key":"/works/OL27482W","title":…,"author_name":["J.R.R. Tolkien"],"cover_i":14627509,"edition_key":[…],"isbn":[…],"first_publish_year":1937,…}]}`; one doc per *work*. Use `fields=` to keep payload small. |
| Edition by ISBN | `GET https://openlibrary.org/isbn/9780547928227.json` (302 → `/books/OL33891995M.json`) | `{"title":"The Hobbit","publishers":["Mariner Books"],"publish_date":"2012","isbn_10":[…],"isbn_13":[…],"covers":[12003329],"works":[{"key":"/works/OL27482W"}],"languages":[{"key":"/languages/eng"}],"number_of_pages":300,"description":{"type":"/type/text","value":"…"},"identifiers":{"goodreads":["44293307"]},"oclc_numbers":[…]}`. Authors only as keys; `description` may be a string or `{type,value}`. |
| Edition, denormalized | `GET https://openlibrary.org/api/books?bibkeys=ISBN:9780547928227&format=json&jscmd=data` | `{"ISBN:…":{"title","authors":[{"name","url"}],"publishers":[{"name"}],"publish_date","identifiers":{"isbn_13":[…],"openlibrary":[…],"goodreads":[…],"oclc":[…]},"subjects":[{"name","url"}],"number_of_pages","cover":{"small","medium","large"}}}`. Best single call for "fill metadata from ISBN"; accepts comma-separated bibkeys (batch). |
| Work (description, subjects) | `GET https://openlibrary.org/works/OL27482W.json` | `description`, `subjects`, `covers`, `authors[{author:{key}}]` |
| Author | `GET https://openlibrary.org/authors/OL26320A.json` | `name`, `personal_name`, `bio`, `photos` |
| Cover | `https://covers.openlibrary.org/b/{isbn\|olid\|id\|oclc\|lccn}/{value}-{S\|M\|L}.jpg[?default=false]` | Tested: `/b/isbn/…-L.jpg?default=false` → **302** (follow redirect to archive.org), unknown ISBN → **404**; `/b/id/12003329-L.jpg` → 200, 42 KB. **ISBN/OCLC/LCCN lookups are limited to 100 req / 5 min / IP (403 when exceeded); by `id`/`olid` unlimited**, so resolve `cover_i`/`covers[0]` first and fetch by id (https://openlibrary.org/dev/docs/api/covers). |

Coverage is strong for print ISBNs and classics and weaker for recent e-book-only and indie releases. Descriptions are often missing at edition level, so fall back to the work.

### 5.2 Google Books

- **API v1 keyless: effectively disabled.**
  - `GET https://www.googleapis.com/books/v1/volumes?q=isbn:9780547928227` returned **HTTP 429 `RESOURCE_EXHAUSTED`** with `"quota_limit":"defaultPerDayPerProject","quota_limit_value":"0"` on two consecutive attempts, and for `/volumes/{id}` too (tested 2026-10-08).
  - The shared keyless project has a 0/day quota, so a **user-supplied API key** (free Google Cloud key, `&key=…`) is needed.
  - Response shape with a key: `{"totalItems":N,"items":[{"id":"LLSpngEACAAJ","volumeInfo":{"title","subtitle","authors":[],"publisher","publishedDate","description","industryIdentifiers":[{"type":"ISBN_13","identifier":"…"}],"pageCount","categories":[],"language":"en","imageLinks":{"smallThumbnail","thumbnail"},"averageRating"}}]}`.
  - Query operators: `intitle:`, `inauthor:`, `isbn:`, `inpublisher:`, `subject:`. `fields=` does partial responses; `maxResults` ≤ 40. The default keyed quota is commonly cited as 1,000/day; check the Cloud console.
- **Legacy GData Atom feed: keyless, works** (this is what Calibre uses):
  - `GET https://books.google.com/books/feeds/volumes?q=isbn:9780547928227&max-results=20&start-index=1&min-viewability=none`
  - Title/author queries: `q=intitle:hobbit+inauthor:tolkien`.
  - Returns Atom XML. Each `<entry>` has `<id>…/volumes/LLSpngEACAAJ</id>`, `<dc:title>`, `<dc:creator>`, `<dc:date>2012</dc:date>`, `<dc:description>`, `<dc:format>300 pages</dc:format>`, `<dc:identifier>ISBN:9780547928227</dc:identifier>`, `<dc:language>en</dc:language>`, `<dc:publisher>`, `<dc:subject>`, and `<link rel="http://schemas.google.com/books/2008/thumbnail" href="…books/content?id=…&printsec=frontcover&img=1&zoom=5…">`.
  - Calibre fetches full details from `https://www.google.com/books/feeds/volumes/<id>`.
  - It is undocumented and deprecated, so it could disappear at any time. Treat it as best-effort.
- **Covers:** `https://books.google.com/books/content?id=<google_id>&printsec=frontcover&img=1&zoom=1` gave 200 image/jpeg, 12 KB, keyless (tested). `zoom=0`/`1`/`2`/`3` give different sizes. A missing cover returns a small "image not available" PNG, so check the dimensions or the hash.

### 5.3 Others

- **Amazon / Goodreads:** HTML scraping only, broken and IP-flagging in 2026. Avoid.
- **Hardcover.app:** GraphQL API, needs a per-user token. A good opt-in source for series data.
- **ISBNdb:** paid.
- **Wikidata SPARQL:** keyless, good for author and series facts.
- **Library of Congress SRU**, **DNB SRU** (German), **BnF**: keyless MARC/XML for national-library ISBNs.

**Recommendation:**
- Default: Open Library, then the Google GData feed as fallback.
- Optional: a Google API key and a Hardcover token, entered in Preferences.
- Sequential queue with at most 1-3 req/s and an identifying User-Agent. Throttle bulk downloads in the UI; the community is explicitly asking Calibre to do this.
- Always show results for confirmation instead of blindly overwriting (answers "Calibre accepts … metadata that is obviously wrong").

---

## 6. Implications for our design (opinionated)

1. **Open existing libraries in place**, with the §3.4 function registrations and the §3.10 lock. Make "Calibre is running → read-only" a first-class state.
2. **Write the Calibre path layout exactly.** It is cheap and avoids Calibre "Check library" noise. Expose a friendly *view* (shelves, series, authors) instead of fighting the folder structure, which is the #3 complaint. "Export/Save to folder with template" covers the people who want their own tree.
3. **Ship without a conversion engine.**
   - Kindle = e-mail EPUB, plus USB/MTP copy of existing AZW3/MOBI/PDF.
   - Kobo = EPUB copy plus optional bundled kepubify.
   - If `ebook-convert` is on `$PATH` (or the Calibre Flatpak), offer "Convert with Calibre" as an advanced action.
4. **Metadata:** Open Library first, a rate-limited queue, a review dialog, and cover picking from several sources.
5. **UI targets** from user feedback: a cover/shelf grid by default (the Bookshelf-view enthusiasm); one adaptive sidebar instead of the toolbar plus tag browser; at most one preferences window with sane defaults; no visible conversion options; fast startup with lazy cover thumbnails (cache thumbnails outside the library, e.g. `~/.cache`).
6. **Leave untouched** the tables we don't understand (plugins, conversion options, annotations, notes, book_storage). Preserve unknown custom columns. Never bump `user_version`.
