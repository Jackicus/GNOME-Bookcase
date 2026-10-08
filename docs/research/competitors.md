# Competitor & Adjacent-App Research — GNOME E-book Library Manager + Reader

Researched 2026-10-08. Target: native GTK4/libadwaita (Python) app that replaces Calibre for GNOME users,
with an embedded foliate-js reader in a `WebKit.WebView` (WebKitGTK 6.0).

Legend: ★ = GitHub stars on research date. [GK] = from general knowledge, not re-verified with a URL.

---

## 0. TL;DR — decision-relevant findings

1. **The gap is real.** No GTK4/libadwaita app on Flathub manages an e-book *library* (metadata, shelves, devices, conversion). Flathub only has readers (Foliate, Readest, Koodo, KOReader, Arianna, Alexandria-btpf). Bookworm (the one GTK library+reader) is GTK3/WebKit2GTK-4.0, last release 2019, and **EOL-delisted from Flathub**.
2. **Closest design reference is Citadel** (Tauri, not GTK, MIT, 1.3k★) — <https://github.com/everydaythingssoftware/citadel>. Its premise, "Calibre must be able to read any library that Citadel has edited", is the right compatibility bar.
3. **#1 cross-project demand: don't touch my files.** People want to read in place and use watch folders rather than having an import step copy their books (Foliate #1320, Readest #1925, Alexandria #56). Calibre refuses this outright in its FAQ.
4. **KOReader sync (kosync) has become the common sync protocol.** Readest, Koodo, Komga, CWA, Grimmory and BookOrbit all speak it. Plan a kosync client for v0.1/v0.2.
5. **foliate-js is MIT, has no hard dependencies, and is proven inside WebKitGTK 6** (Foliate 3.x). I tested a custom URI scheme in PyGObject on WebKitGTK 2.52: ES-module imports work, the page is a secure context, and `crypto.subtle` works. There are two upstreams: johnfactotum/foliate-js (slow, last push 2026-05) and **readest/foliate-js** (MIT fork, 251 commits ahead, very active, much better PDF/fixed-layout/scrolled support).
6. **Users punish paywalls and governance drama.** Readest moved WebDAV sync behind a paywall. BookLore's maintainer deleted the project. Calibre added an AI feature, which led to the clbre/arcalibre forks. Keep everything free and local-first.
7. **Name check:** *Stacks* is taken by a GTK4/libadwaita epub reader. *Folio* is a GNOME Flathub app. *Codex*, *Shelfmark* and *Bindery* are taken in the book/self-host space. **Free enough: Margins, Bookcase, Shelf** (the last is generic). Details in §6.

---

## 1. Per-app findings

### 1.1 Foliate (GNOME; GJS + foliate-js) — the incumbent GNOME reader
- **Repo / state:** <https://github.com/johnfactotum/foliate>. GPL-3.0, 8.8k★. v3.3.0 released 2025-04-01, last push 2026-04-08. Flathub `com.github.johnfactotum.Foliate`.
- **Does well:**
  - Single-window library and reader with a recent-books grid/list.
  - OPDS catalogs (3.1+).
  - Highlights in colours, with notes. Bookmarks.
  - Dictionary, Wikipedia and translate lookup popovers (`src/selection-tools/{wiktionary,wikipedia,translate}.html`).
  - TTS through speech-dispatcher (`src/speech.js`), plus EPUB3 media overlays.
  - Paginated/scrolled toggle (Ctrl+M). Themes (light, sepia, dark, invert) and a full font/spacing/margins panel.
  - Footnote popups. Image viewer. Reading-location dialog (Ctrl+L). Back/forward history (Alt+Left/Right).
- **Storage model:** one JSON file per book in `~/.local/share/com.github.johnfactotum.Foliate/<encodeURIComponent(identifier)>.json`, holding `lastLocation` (CFI), `annotations[]` and `bookmarks[]`. Covers live in the cache directory. Books are keyed by identifier, not path, which is good. **This is easy to import: offer "Import from Foliate".**
- **Complaints / gaps** (issues sorted by 👍, <https://github.com/johnfactotum/foliate/issues?q=sort%3Areactions-%2B1-desc>):
  - PDF/DjVu: #139, 134👍.
  - Watch folders: #1320. Grouping/collections: #570. Bulk add: #1196. Bulk delete: #1205.
  - Global zoom default: #1158.
  - WebKit crashes/blank page on NVIDIA: #1107. Theme-change crash on Flatpak: #719.
  - "Don't use libadwaita" backlash: #1142.
  - No metadata editing, shelves or devices.
- **Formats:** EPUB, MOBI, KF8/AZW3, FB2/FBZ, CBZ, PDF (experimental).
- **Keyboard (copy these):**
  - Page: Space/Shift+Space, PgUp/PgDn, n/p.
  - Arrow keys or h/j/k/l: left/right page, up/down scroll.
  - Search: Ctrl+F or `/`. Ctrl+G and Ctrl+Shift+G for next/previous result.
  - Panels: Ctrl+T contents, Ctrl+Alt+A annotations, Ctrl+Alt+D bookmarks.
  - Ctrl+D adds a bookmark. Ctrl+L shows the location dialog. Ctrl+I shows book info.
  - Zoom: +, − and 0. Ctrl+P prints. Alt+, opens preferences.

### 1.2 Bookworm (elementary; Vala/GTK3)
- **State:** <https://github.com/babluboy/bookworm>. Last release 1.1.2 on 2019-08-10. Recent activity is translations only. Built on GTK3 + webkit2gtk-4.0 + granite. The Flathub manifest is marked `end-of-life` (<https://github.com/flathub/com.github.babluboy.bookworm>). 145 open issues.
- **Does well:**
  - Library grid/list toggle with sortable columns.
  - Inline metadata editing.
  - **Auto-import from multiple watched folders.**
  - A single reading sidebar with four tabs: Contents, Bookmarks, Search results, Annotations.
  - Three reading profiles (light, sepia, dark). Two-page mode.
- **Complaints:**
  - Runs EPUB JavaScript, so books can phone home (#283).
  - Doesn't restore the exact position, only the chapter start (#392).
  - Converts PDF to HTML, which is poor (#82).
  - Orca accessibility is poor (#399).
  - **Bookmarks are lost when a file moves** because books are keyed by path.
  - Python 3.13 breakage (#397).
- **Formats:** EPUB, MOBI, FB2, PDF, CBR, CBZ.

### 1.3 Apple Books (macOS/iOS)
- **Does well:**
  - Home tab with "Continue reading", Want to Read, and reading goals and streaks.
  - Auto collections: Want to Read, Finished, Books, PDFs, Samples.
  - **Named theme presets** (Original, Quiet, Paper, Bold, Calm, Focus), each with a "Customize" disclosure: font, bold, line/character/word spacing, justification and margins (<https://macrumors.com/2022/06/07/ios-16-books-app-redesign>).
  - Light/Dark/Auto appearance. Optional vertical scroll.
  - Page-turn style selectable: Curl, Slide or None. This came after a backlash (<https://www.macstories.net/linked/apples-taken-the-joy-out-of-its-books-app-with-ios-16/>).
  - [GK] "N pages left in chapter". A Look Up popover with dictionary and Wikipedia. iCloud sync.
- **Complaints:**
  - Metadata editing was removed on Mac. Author sort is a "meaningless jumble" (<https://discussions.apple.com/thread/254970632>, <https://talk.tidbits.com/t/how-apple-s-books-app-has-changed-in-ios-16/20125?page=2>).
  - Missing books and covers after updates (<https://discussions.apple.com/thread/255966349>).
  - Weak annotation export.
- **Formats:** EPUB (incl. fixed layout), PDF, .ibooks, audiobooks. No MOBI/AZW.

### 1.4 Kindle (app + devices)
- **Does well:**
  - **One cycling progress label**: Location, Page, Time left in chapter, Time left in book, None. Time is estimated from measured reading speed (<https://www.bgr.com/2166895/tips-reading-more-kindle/>).
  - **Page Flip**: browse or peek without losing your place (<https://www.thebookseller.com/news/kindle-introduces-page-flip-tool-339586>).
  - X-Ray. Word Wise.
  - Long-press opens a lookup card with Dictionary, Wikipedia and Translate tabs. Vocabulary Builder.
  - "Publisher font" option.
  - `My Clippings.txt` collects every highlight, including on sideloaded books. **Import it.**
- **Complaints:**
  - Popular Highlights are distracting (<https://thenextweb.com/news/how-to-turn-off-popular-highlights-amazon-kindle>).
  - The progress mode resets itself.
  - USB download removed in Feb 2025 (<https://androidcentral.com/apps-software/amazon-kindle-users-to-loose-download-transfer-via-usb-feature>). Kindle for PC killed on 2026-06-30 (<https://blog.the-ebook-reader.com/2026/04/16/amazon-pulling-the-plug-on-kindle-for-pc-on-june-30th/>).
  - No sync for sideloaded books.
  - Home screen is full of recommendations.
- **Formats:** KFX, AZW3, AZW, MOBI, PDF, TXT, DOCX. EPUB only via Send-to-Kindle, which converts it.

### 1.5 Kobo (devices + app)
- **Does well:**
  - Per-chapter "time left in chapter / next chapter" plus a chapter bar graph. Reading stats and awards.
  - Advanced typography: weight, sharpness, justification and "publisher default". Users can add their own fonts.
  - **Series and collections driven from Calibre metadata**, which users call "a godsend" (<https://www.mobileread.com/forums/showthread.php?p=2761327>).
  - KEPUB footnote popups and image zoom.
- **Complaints:**
  - Two renderers (RMSDK and ACCESS) behave differently. An EPUBCheck-valid book failed on one CSS rule (<https://gigazine.net/gsc_news/en/20260616-epub-kobo-adobe>).
  - Stats are wrong for sideloaded books.
  - **Collections are a flat list, with no nesting.**
  - No sync for sideloaded books. No annotation export in the app.
- **Formats:** EPUB2/3, KEPUB, PDF, MOBI, TXT, HTML, RTF, CBZ, CBR, plus Adobe DRM (<https://help.kobo.com/hc/en-us/articles/360017763713>).

### 1.6 Google Play Books
- **Does well:**
  - **Auto-export of notes to one Google Doc per book**, kept updated (<https://support.google.com/googleplay/answer/3165868>).
  - Night Light: an amber filter that follows sunset.
  - Bubble Zoom for comics.
  - Uploaded EPUBs sync like purchased ones. Shelves.
- **Complaints:**
  - Fragile EPUB processing ("could not be processed") (<https://commonsware.com/blog/2017/04/19/fix-google-play-books-epub-issue.html>).
  - Uploads get stuck. Can't highlight PDFs.
- **Formats:** EPUB and PDF uploads.

### 1.7 Moon+ Reader (Android)
- **Does well** (<https://www.moondownload.com/>):
  - **Configurable tap zones and gestures**: 24 inputs mapped to 15 actions. Brightness changes by swiping the left edge.
  - Auto-scroll modes. Day/night auto switch.
  - **WebDAV/Dropbox sync** of position and annotations.
  - Per-book reading statistics. OPDS.
- **Complaints:**
  - An overwhelming settings maze. The UI looks dated (<https://www.mobileread.com/forums/showthread.php?p=2915068>).
  - Ads in the free tier.
- **Formats:** EPUB, PDF, MOBI, AZW/AZW3, FB2, DjVu, DOCX, ODT, RTF, TXT, HTML, MD, CBR/CBZ.

### 1.8 BookFusion
- **Does well:**
  - A Calibre plugin pushes the library to the cloud.
  - Sync of position and highlights. Virtual shelves plus series.
  - Highlight export to CSV, MD, HTML or PDF (<https://www.bookfusion.com/features>).
- **Complaints:**
  - Read status doesn't sync back to Calibre.
  - It writes a BookFusion ID into Calibre metadata.
  - Subscription tiers. The free tier is capped at 10–25 books (<https://www.mobileread.com/forums/showthread.php?p=4441533>).
- **Formats:** EPUB, PDF and CBZ/CBR natively. Other formats are converted server-side.

### 1.9 KOReader
- **Repo / state:** <https://github.com/koreader/koreader>. AGPL-3.0, 30k★, Lua. Very active.
- **Does well:**
  - The deepest typography of any reader, set per book.
  - StarDict dictionaries. Wikipedia.
  - Stats. OPDS. Calibre wireless connection.
  - Highlight export.
  - **kosync**, documented below.
- **kosync in brief:**
  - Server: <https://github.com/koreader/koreader-sync-server>. CC0 conformance spec: <https://github.com/pid1/kosync-conformance>.
  - Auth headers are `x-auth-user` and `x-auth-key`, where the key is MD5(password).
  - Progress is stored as an XPointer plus a percentage plus the device name.
  - The document ID is `partialMD5`: 1 KiB samples at offsets `1024 << 2i` for i = −1..10 (<https://github.com/koreader/koreader/blob/master/frontend/util.lua>).
  - Readest uses the **percentage** as the common ground and converts XPointer to CFI.
- **Complaints:**
  - No metadata-based library view, only a file browser ("Flat/Library view" #8472, 88👍).
  - The UI is e-ink-first.
- **Formats:** PDF, DjVu, EPUB, FB2, MOBI, CBZ, TXT, HTML, RTF, CHM, DOC and more.

### 1.10 Readest (Tauri v2 + Next.js + foliate-js fork)
- **Repo / state:** <https://github.com/readest/readest>. AGPL-3.0, 24.9k★. Very active. Flathub `com.bilingify.readest`.
- **Does well:**
  - Full-text search across the whole shelf.
  - Paginated or scrolled reading.
  - Instant highlight. Custom dictionaries (Yomitan, RDICT). DeepL translation.
  - TTS plus media overlays. Parallel read (two books side by side).
  - kosync. OPDS/Calibre.
  - Readwise, Hardcover and Obsidian integrations.
  - Orca support. A reading ruler.
- **Complaints:**
  - **WebDAV/Drive sync moved behind a paywall** days after shipping free (#5033).
  - Self-hosted WebDAV (#356, 35👍).
  - Must import; can't read in place (#1925).
  - Linux AppImage EGL errors (#190). TTS freezes (#5099).
- **Formats:** EPUB, PDF, MOBI, AZW3, FB2, CBZ, TXT, HTML, MD.

### 1.11 Koodo Reader (Electron)
- **Repo / state:** <https://github.com/koodo-reader/koodo-reader>. AGPL-3.0, 28k★.
- **Does well:**
  - Sync and backup over WebDAV, SMB, S3 or FTP, plus KOReader progress sync.
  - MDX dictionaries.
  - Notes export to CSV, MD, HTML, PDF, Obsidian or Notion. Anki word export.
  - Serves the library as OPDS. Library snapshots.
- **Complaints:**
  - Some sync is Pro-only.
  - A page shadow that can't be turned off.
  - Scrolling stops at every chapter end (#1161).
- **Formats:** EPUB, PDF, MOBI, AZW3, TXT, FB2, CBx, MD, DOCX.

### 1.12 Thorium Reader (EDRLab; Electron + Readium)
- **Repo / state:** <https://github.com/edrlab/thorium-reader>. BSD-3, 2.9k★.
- **Does well:**
  - **Best accessibility**: NVDA, JAWS and VoiceOver. TTS highlights each word and sentence.
  - Sortable library table with tags.
  - OPDS 1/2 with authentication.
  - Dyslexia font.
  - LCP DRM, which libraries such as EBSCO need.
- **Complaints:**
  - Annotations can't really be exported.
  - No Flatpak (#1073). No mouse-wheel page turn (#1436).
- **Formats:** EPUB 2/3, PDF, DAISY, LPF audiobooks, Divina comics, LCP.

### 1.13 Calibre-Web / Calibre-Web-Automated
- **Calibre-Web:** <https://github.com/janeczku/calibre-web>. GPL-3.0, 18k★, Flask over `metadata.db`.
  - **Does well:** treats the Calibre library as the interchange format. Kobo sync, Send-to-Kindle, OPDS, shelves, multi-user.
  - **Top complaints:**
    - No progress sync (#2298, 59👍).
    - No full-text search (#2490, 50👍).
    - Last-read position lost (#1797).
    - No smart shelves (#2619).
    - The UI is "fugly".
- **CWA:** <https://github.com/crocodilestick/Calibre-Web-Automated>. GPL-3.0, 6.4k★.
  - **Does well:**
    - **Ingest folder** with auto-convert to EPUB/KEPUB and **automatic metadata fetch** on ingest.
    - **Fuzzy duplicate detection.**
    - **"Magic Shelves"**, which are rule-based.
    - EPUB fixer. kosync. Polling watcher for NFS/SMB.
  - **Complaints:**
    - The web reader doesn't sync with KOReader (#461).
    - Docker only.
    - Upgrade breakage. Ingest is unreliable for large batches.

### 1.14 Kavita / Komga (self-hosted servers)
- **Kavita:** <https://github.com/Kareadita/Kavita>. GPL-3.0.
  - **Does well:** polished series-centric UI, reading lists, smart filters, OPDS.
  - **Complaints:** a strict folder-layout scanner, EPUB grouping that relies only on embedded metadata, and paywalled Kavita+.
- **Komga:** <https://github.com/gotson/komga>. MIT.
  - **Does well:** comics-first. **Kobo Sync pushes metadata and cover edits** to the device. On-the-fly KEPUB. KOReader sync. OPDS 1/2.
  - **Limitation:** for reflowable EPUB, sync is chapter-granular.
- **Formats (both):** CBZ/CBR/CB7, EPUB, PDF.

### 1.15 Alexandria (btpf; Tauri + Epub.js)
- **Repo / state:** <https://github.com/btpf/Alexandria>. GPL-3.0, 2.7k★. Flathub `io.github.btpf.alexandria`.
  - Effectively dormant. In #77 the maintainer says **Epub.js is too buggy, foliate-js is the better base**, and a rewrite is unlikely.
- **Does well:** split-screen reading, custom themes, highlight export.
- **Formats:** EPUB, AZW/AZW3/MOBI, FB2, CBx, TXT. No PDF (#59).
- **Different app, same name:** a GTK4 Python "Alexandria" scientific-PDF manager is on Flathub as `io.github.pemsley.Alexandria`. It stores **per-PDF JSON sidecars plus a rebuildable SQLite index**, a design worth noting.

### 1.16 "Book Lover's tools" → BookLore / Grimmory / BookOrbit
I found no product with that exact name. The most plausible match is the BookLore family:
- **BookLore** (<https://github.com/booklore-app/booklore>), with features worth copying:
  - Rule-based **Magic Shelves**.
  - Metadata from Google Books, Open Library and Amazon.
  - **BookDrop**: a watch folder with a review queue.
  - Kobo, KOReader and OPDS sync. Send-to-Kindle.
  - Users liked that it keeps metadata separate from the files.
- **What happened:** in 2026 the maintainer imploded over AI PRs and hinted at a license change, then deleted the project (<https://www.xda-developers.com/single-maintainer-open-source-ticking-time-bomb/>). It was forked as **Grimmory** (AGPL, 4.5k★), and the original now points to **BookOrbit** (AGPL, 5.3k★, 14 metadata providers).
- **Complaints:**
  - Heavy RAM use and MariaDB-only storage (Grimmory #22).
  - Users want to "peek inside without saving progress" (Grimmory #153).
- **Trackers, not managers:** BookLogr and BookWyrm.

### 1.17 Calibre itself (what to take / fix)
- **Users can't live without:**
  - Metadata and cover download, plus the bulk metadata editor.
  - Browsing and sorting by author, series and tags.
  - Saved searches and virtual libraries.
  - Custom columns.
  - Conversion (`ebook-convert`).
  - Device send.
  - Content server/OPDS.
  - **DeDRM plugin** (<https://github.com/noDRM/DeDRM_tools>).
  - The `metadata.db` format, which many tools read.
- **What's bad:**
  - **Forced `Author/Title (id)/` layout.** Adding files by hand risks deletion, and the FAQ says "this is not going to change" (<https://manual.calibre-ebook.com/faq.html#why-doesn-t-calibre-let-me-store-books-in-my-own-folder-structure>).
  - **`metadata.db` breaks on NAS or cloud folders** (FAQ).
  - Startup takes over 10 minutes on 10k+ libraries (<https://www.mobileread.com/forums/showthread.php?p=4402792>).
  - Qt on GNOME/Wayland: mis-placed popups and transparent dialogs.
  - The viewer converts books before showing them.
  - Dense 2005-era UI.
  - The 8.16 "Discuss with AI" feature led to forks: clbre (<https://github.com/grimthorpe/clbre>) and arcalibre (<https://lwn.net/Articles/1049886>).

### 1.18 Existing GNOME/GTK library-manager attempts

| Project | Stack | State |
|---|---|---|
| Foliate | GTK4/GJS | Reader with a recent-books grid. No shelves or metadata editing. |
| Bookworm | GTK3/Vala | Stagnant since 2019. EOL on Flathub. |
| GNOME Books (gnome-books) | GTK3 + Tracker | Retired. |
| Bookx <https://github.com/adhadse/Bookx> | GTK4/Rust | Archived in 2023. |
| Hermitage <https://github.com/VirInvictus/Hermitage> | GTK4/Python | Read-only gallery over Calibre `metadata.db`. Brand new (2026-10), 0★. |
| Stacks <https://codeberg.org/robland/stacks> | GTK4/libadwaita | Mobile epub reader with a Jellyfin backend. Early. |
| Arianna (`org.kde.arianna`) | KDE/Kirigami | Reader with a basic library. |
| Komikku <https://codeberg.org/valos/Komikku> | GTK4/libadwaita, **Python** | Manga, active. **The best structural reference for a Python libadwaita library + reader app.** |
| Citadel | Tauri | Full Calibre-compatible manager. The design reference. |

---

## 2. foliate-js deep dive (for embedding in `WebKit.WebView`)

Upstream: <https://github.com/johnfactotum/foliate-js>. **MIT** (© 2022 John Factotum), 1.1k★, last push 2026-05-01. The README warns that the API is not yet stable and recommends using it as a git submodule. It has no npm package and no releases.

**Vendored dependencies:**
- `vendor/zip.js` (BSD-3)
- `vendor/fflate.js` (MIT)
- `vendor/pdfjs/` (Apache-2.0)

A GPL/MIT/Apache mix is fine for a GPL-3 app.

**Fork:** <https://github.com/readest/foliate-js>. MIT, 251 commits ahead and 14 behind upstream, pushed 2026-10-04. Its additions:
- PDF thumbnails, text layer and recolouring.
- Fixed-layout scroll strip, horizontal pan and spreads.
- Continuous-scroll relocate events and sub-pixel scrolling.
- Swipe-performance work.
- TTS sentence fixes. Overlayer fixes.

Choose deliberately. Upstream is what Foliate ships. The fork is better for PDF/CBZ and scrolled mode but tied to Readest's needs.

### 2.1 Modules

| Module | Role |
|---|---|
| `view.js` | `<foliate-view>` custom element and `makeBook()`. The entry point. |
| `epub.js`, `epubcfi.js` | EPUB parsing and CFI handling |
| `mobi.js` | MOBI and KF8/AZW3 |
| `fb2.js` | FB2/FBZ |
| `comic-book.js` | CBZ |
| `pdf.js` | PDF adapter over PDF.js. Experimental upstream. |
| `paginator.js` | `<foliate-paginator>`, the reflowable renderer |
| `fixed-layout.js` | `<foliate-fxl>`, the fixed-layout renderer (CBZ, PDF, pre-paginated EPUB) |
| `overlayer.js` | Annotation and search drawing |
| `progress.js` | Section/TOC/page-list progress |
| `search.js`, `text-walker.js` | Search |
| `tts.js` | Produces SSML; you supply the speech synthesiser |
| `footnotes.js` | Footnote popups |
| `dict.js` | dictd/StarDict dictionaries |
| `opds.js` | OPDS feeds |
| `quote-image.js` | Quote images |

**Formats:** EPUB, MOBI, KF8 (AZW3), FB2, FBZ, CBZ, PDF (experimental). There is **no CBR/CB7, DjVu or TXT** support. You would have to write a book adapter for those, or convert them to CBZ/EPUB.

### 2.2 API (from `view.js` source)

**Opening a book.**
- `makeBook(file)` detects the format by magic bytes and extension.
- `view.open(book)` accepts a `File`, a `Blob`, a URL string, or an object implementing the book interface.
  - A **URL string is fetched entirely into a Blob** (`fetchFile`), so the whole book is held in memory.
  - A `File` gets random access through zip.js `BlobReader`.
- `view.init({ lastLocation, showTextStart })` restores the saved position.

**Navigation.**
- `goTo(target)` takes a CFI, href or section index.
- `goToFraction(0..1)`, `prev(distance)`, `next(distance)`, `goLeft()`, `goRight()`.
- `renderer.prevSection()`, `nextSection()`, `firstSection()`, `lastSection()`.
- `history.back()` and `history.forward()`, with an `index-change` event.

**Location and progress.**
- The `relocate` event's detail holds `{fraction, section:{current,total}, location:{current,next,total}, time:{section,total}, tocItem, pageItem, cfi, range}`.
- `getCFI(index, range)`, `resolveCFI(cfi)`, `getSectionFractions()` (useful for chapter ticks on a slider), `getProgressOf()` and `getTOCItemOf()`.

**Annotations.**
- `addAnnotation({value: cfi, ...})`, `deleteAnnotation()`, `showAnnotation()`.
- Events:
  - `draw-annotation` gives `{draw, annotation, doc, range}`. Call `draw(Overlayer.highlight | underline | squiggly | outline, {color})`.
  - `show-annotation` fires when an annotation is clicked.
  - `create-overlay` fires per section; re-add that section's annotations then.
- The `--overlayer-highlight-blend-mode` CSS variable sets the blend mode: use multiply for light themes and screen for dark ones.

**Search.**
- `search({query, index?, matchCase, matchDiacritics, matchWholeWords})` is an async generator. It yields `{progress}` and `{index, subitems: [{cfi, excerpt}]}`, then `'done'`.
- `clearSearch()` removes the results.

**TTS and media overlays.**
- `initTTS(granularity='word', highlight)` sets up `view.tts`, which has `start()`, `resume()`, `prev()`, `next()` and `setMark()`. These return SSML. Foliate pipes it to speech-dispatcher.
- `startMediaOverlay()` starts EPUB3 read-along.

**Other events.**
- `load` gives `{doc, index}`. Use it to attach selection and keyboard listeners inside each section iframe.
- `link` and `external-link` are both cancelable.

**Renderer configuration.**
- Configure the renderer through **attributes, not properties**.
- `<foliate-paginator>` attributes: `flow="paginated|scrolled"`, `animated`, `margin`, `gap`, `max-inline-size`, `max-block-size`, `max-column-count`.
  - It uses CSS multi-column and can switch flow without reloading.
  - Headers and footers are available as `::part(head)` and `::part(foot)` (paginated only).
  - Upstream has no continuous scroll across chapters; the readest fork improves this.
- `<foliate-fxl>` takes `zoom="fit-width|fit-page|<number>"` and handles spreads.
- Book styles are injected with `renderer.setStyles(css)`.
- A dark-mode invert can go on `foliate-view::part(filter)`, which leaves highlights untouched.

**CFI.**
- `epubcfi.js` parses CFIs into plain arrays without a regex. It handles `fromRange`, `toRange`, `compare` and `collapse`, and supports a filter for injected nodes.
- It works outside the browser too, so CFIs can be sorted in Python by porting it, or kept JS-side.

### 2.3 Security requirements (README)
- **Scripted EPUBs are unsupported.** Blob-URL iframes are same-origin, and WebKit bug 218086 forces `allow-scripts` on the sandbox, so the iframe sandbox is ineffective.
- **You MUST use a CSP.** Foliate's `reader.html` uses:
  `default-src 'self' blob:; script-src 'self'; style-src 'self' blob: 'unsafe-inline'; img-src 'self' blob: data:; connect-src 'self' blob: data:; frame-src blob: data:; object-src blob: data:; form-action 'none';`
- Also disable HTML5 local storage and database, hyperlink auditing, and back/forward gestures, as Foliate does in `book-viewer.js`.
- Intercept `decide-policy` so navigations go to `Gtk.UriLauncher` instead of the WebView.

### 2.4 How Foliate hosts it (src/webview.js, book-viewer.js, reader/reader.js)

**Custom URI schemes.**
- `WebKit.WebContext.get_default().register_uri_scheme('foliate', req => …)` serves files from the **GResource bundle**, using `Gio.File.new_for_uri('resource://…').read()` and `req.finish(stream, -1, mime)`.
- An allowlist restricts the scheme to the `/reader/` and `/foliate-js/` prefixes. Other schemes cover OPDS and selection tools.
- The page is loaded as `foliate:///reader/reader.html`.
- All of foliate-js, plus pdf.js and its cmaps, is listed in `gresource.xml`.

**Messages from the page to the host.**
- The page calls `webkit.messageHandlers.viewer.postMessage(JSON.stringify({type, ...}))`.
- The host uses `UserContentManager.register_script_message_handler(name, null)` and listens to `script-message-received::name`.
- Message types: `ready`, `book-ready`, `book-error`, `relocate`, `create-overlay`, `selection`, `external-link`, `show-image`, `dialog-open`, `history-index-change`, `pinch-zoom`.

**Calls from the host to the page.**
- `evaluate_javascript()` runs the script. For async functions, the host builds a **token-keyed promise store**: the JS posts `{token, ok, payload}` back to resolve the GJS promise.
- `exec('reader.view.goTo', cfi)` and the other wrappers follow this pattern. `iter()` wraps async generators such as search.
- `provide()` is the reverse direction: JS awaits GJS.

**Getting the book file into the page.** This is the clever part.
- The host does **not** stream bytes over IPC. `reader.js` clicks a hidden `<input type=file>`.
- The host handles the WebView's `run-file-chooser` signal with `req.select_files([path])`.
- The page therefore gets a real disk-backed `File` with lazy `slice()`, so zip.js reads only the entries it needs. This is efficient for large CBZ/PDF files.

**Other details.**
- The book-info and cover import path uses a hidden second WebView (`initImport`) to extract metadata and the cover with foliate-js.
- Grid gestures, pinch zoom and mouse back/forward buttons (8 and 9) are handled in GTK and forwarded to JS.

### 2.5 Verified in PyGObject + WebKitGTK 6.0 (local test, WebKitGTK 2.52)
- I registered an `app://` scheme serving an HTML page with a strict CSP and an ES module that imports another module.
- Result: module import worked. `isSecureContext === true` and `crypto.subtle.digest('SHA-1')` worked, **even without** `SecurityManager.register_uri_scheme_as_secure`. This matters because foliate-js needs SHA-1 from Web Crypto for EPUB font deobfuscation.
- Also present in 2.52: `register_script_message_handler_with_reply` and `URISchemeRequest.finish_with_response` (`WebKit.URISchemeResponse`, for status codes and headers).
- **Conclusion: vendoring foliate-js into a gresource and serving it over a custom scheme from Python is fully viable.** Two simplifications over Foliate:
  - `register_script_message_handler_with_reply` removes the need for Foliate's token-promise boilerplate for page-to-host calls.
  - `WebView.call_async_javascript_function()` (2.40+) awaits a JS promise directly, replacing `exec()`.
- Keep Foliate's `run-file-chooser` trick for getting files in. Alternatively, serve the book via `app://book/<id>` with `fetch`, but that holds the whole book in memory.
- **Known WebKitGTK pitfalls:**
  - Blank page or crashes on NVIDIA (Foliate #1107). The usual workaround is `WEBKIT_DISABLE_DMABUF_RENDERER=1` or `WEBKIT_DISABLE_COMPOSITING_MODE`.
  - The web process can crash; handle `web-process-terminated` and reload.
  - Content scale on fractional scaling (foliate-js #2).

---

## 3. Synthesis (a): ranked v0.1 feature list

1. **Library grid of covers** (plus a list/table view toggle) with fast search-as-you-type over title, author, series and tags. Covers are cached as thumbnails, and the grid is virtualized (`Gtk.GridView`) so 10k books open instantly.
2. **Reader built on foliate-js**: EPUB, MOBI/AZW3, FB2, CBZ; PDF marked experimental. Restores the exact position by CFI on reopen.
3. **"Continue reading" row** on the library home (Apple Books/Kindle), with a progress bar on each cover.
4. **Non-destructive library model.** Read books in place and watch folders (inotify, plus a polling fallback for NFS/SMB). Never move or rename files unless asked. Books are keyed by content hash or identifier, not path.
5. **Typography panel**: font family, size, line height, margins, max line width, justification, hyphenation and a **"use publisher styles" toggle**. Settings are global, with an optional per-book override.
6. **Theme presets** (Light, Sepia, Dark, Black/OLED) that follow `AdwStyleManager`, plus "invert images" handling.
7. **Paginated or scrolled** reading, with one- or two-column auto layout depending on width.
8. **Progress indication**: a bottom label that cycles between %, page X of Y, time left in chapter and time left in book; a scrubber with chapter ticks; and a header showing the chapter title.
9. **Table of contents sidebar** (`AdwOverlaySplitView`) with tabs for Contents, Bookmarks, Annotations and Search.
10. **Highlights (four colours), notes and bookmarks**, with **Markdown export** per book and copy-with-citation.
11. **In-book search** with a results list and next/previous.
12. **Shelves/collections** (manual) plus **smart shelves** (rule-based) for Unread, Reading, Finished, Recently added and per series/author/tag.
13. **Metadata editor** for single and bulk edits (title, authors, series and index, tags, cover, description, language, ISBN), stored in our DB. Optional write-back to the OPF.
14. **Online metadata and cover fetch** from Open Library, Google Books or Hardcover, with a manual review step.
15. **Dictionary lookup** on selection: Wiktionary/Wikipedia popover, plus offline StarDict via `dict.js` later.
16. **Calibre library import** (read `metadata.db`, covers and custom columns), read-only at first, with a "keep Calibre compatible" mode later.
17. **Keyboard-complete reader**, following Foliate's map in §4. Includes a shortcuts window.
18. **Duplicate detection** on import, by hash or by fuzzy title and author.
19. **Import from Foliate**: positions and annotations via the per-book JSON.
20. **TTS** via speech-dispatcher with word highlighting.
21. **Send to device**: copy to a mounted Kobo or Kindle over USB MTP/mass storage, optionally converting through `ebook-convert` or `kepubify` when found.
22. **OPDS catalog browser**, and optionally serving our library as OPDS later.
23. **kosync client** to sync position with KOReader devices and Readest. Use the percentage as common ground and partialMD5 document IDs.
24. **Reading stats**: time read per day and per book, with a streak. Also feeds the time-left estimates.
25. **Accessibility**: Orca-readable library, focusable reader. Thorium is the bar.

Cut lines: 1–14 is a credible v0.1. Items 15–20 belong in v0.2. Items 21–25 are the "Calibre replacement" milestone.

## 4. Synthesis (b): reader-UX conventions users expect

- **Keyboard (Foliate, Calibre viewer and browsers converge):**
  - Page turns: Right, Space, PgDn and `l` go forward. Left, Shift+Space, PgUp and `h` go back.
  - Up and Down (`k`/`j`) scroll in scrolled mode.
  - Home/End go to the start or end of the book. Ctrl+Home/Ctrl+End or `[` and `]` go to the previous or next chapter (Calibre).
  - Ctrl+F or `/` searches, with Ctrl+G and Ctrl+Shift+G for next and previous.
  - Ctrl+T contents. Ctrl+D bookmark. Ctrl+B or Ctrl+Alt+A annotations.
  - Ctrl+plus/minus/0 for font size. F11 fullscreen. Esc leaves fullscreen or closes popovers.
  - Alt+Left/Right for back and forward after following a link. Ctrl+L go to location. Ctrl+I book info. Ctrl+W close.
  - **Arrow-key direction must flip for RTL books.** Foliate's `goLeft`/`goRight` handle this.
- **Mouse and touch:**
  - Click or tap zones: left third goes back, right third goes forward, middle toggles chrome.
  - Swipe to turn pages on touchscreens and touchpads.
  - The scroll wheel turns pages in paginated mode (Thorium's lack of this is a complaint).
  - Mouse buttons 8 and 9 go back and forward.
  - Pinch zooms fixed-layout books.
  - Ctrl+scroll changes font size.
- **Chrome:** chrome auto-hides in fullscreen and reappears on mouse move to the top edge. A distraction-free mode. Never show recommendations on home.
- **Progress:**
  - Always show a persistent minimal indicator: % or page in the footer, plus the chapter title in the header.
  - A scrubber with chapter ticks.
  - "Time left in chapter" learned from reading speed.
  - Peek or jump without losing your place: an explicit "return to where you were" chip after jumps (Kindle Page Flip and Grimmory #153).
- **Typography:**
  - Font size in steps. Line spacing (1.2–2.0). Margins. Max line width (about 60–75ch). Justification. Hyphenation.
  - Font family: serif, sans, publisher, or a custom system font.
  - A "publisher defaults" override toggle.
  - Each change previews live.
  - Apple-style named presets with Customize.
- **Night mode:** follow the system dark style by default, with an override. Offer Sepia and Black. Don't invert images (dark-mode invert with a hue-rotate filter that skips images, as Foliate does). An optional warm filter (Play Books Night Light). Highlights stay readable in dark mode (screen blend).
- **Page-turn animation** must be optional: slide or none (Apple's 16.4 reversal).

## 5. Synthesis (c): pitfalls to avoid

1. **Owning or moving the user's files.** This is Calibre's most hated trait. Read in place, and offer an optional "organize" action.
2. **A single fragile SQLite database on network or cloud drives** (Calibre FAQ). Keep the DB in `$XDG_DATA_HOME`, not in the books folder. Consider per-book sidecars (as Alexandria-pemsley does) so the DB can be rebuilt.
3. **Keying books by path.** Bookworm loses bookmarks when a file moves. Use a content hash plus `dc:identifier`.
4. **Running book JavaScript or allowing network access** (Bookworm #283). Enforce a CSP, deny navigation, and turn off local storage.
5. **Imprecise position restore** (Bookworm #392, Calibre-Web #1797). Persist the CFI on every `relocate`, debounced.
6. **Paywalling or removing features after the fact** (Readest WebDAV, Kavita+, Koodo Pro), and governance drama (BookLore, Calibre AI). Be explicit about scope.
7. **A settings maze** (Moon+). Keep a short preferences page and put power options behind disclosures.
8. **Converting before display** (the Calibre viewer). Render natively with foliate-js.
9. **Slow startup on big libraries** (Calibre: 10+ minutes at 10k books). Load lazily, virtualize lists, and generate thumbnails in a background thread.
10. **A flat collections model only** (Kobo complaint). Support series as first-class, plus smart shelves.
11. **Strict folder-layout scanners** (Kavita). Trust embedded metadata, fall back to filename heuristics, and let users fix things.
12. **WebKitGTK GPU and DMABUF crashes on NVIDIA** (Foliate #1107). Ship a fallback env toggle and handle `web-process-terminated`.
13. **Tracking a moving upstream.** foliate-js has no stable API. Pin a commit, vendor it as a submodule, and wrap it behind our own thin JS bridge, so that switching between upstream and the readest fork is a single-file change.
14. **No libadwaita escape hatch for theming.** Foliate #1142 shows people care about custom colours. Allow custom reading themes, even though the app chrome stays Adwaita.
15. **Promising Calibre parity on conversion and DRM in v0.1.** Shell out to `ebook-convert` and kepubify if present. Never bundle DeDRM.

## 6. Synthesis (d): name collisions

I checked the full Flathub app list (`flatpak remote-ls flathub --app`, 3,483 apps), GitHub repository-name search, and the web.

| Name | Status | Collisions found |
|---|---|---|
| **Shelf** | Mostly free | Not on Flathub. GitHub "shelf" is dart-lang/shelf (web middleware). Lots of *-shelf projects nearby: DistroShelf (GTK4, distrobox), KoShelf (KOReader dashboard), ShelfPlayer, Audiobookshelf, shelfarr. Generic, hard to search for, and crowded with "*shelf" book tools. |
| **Bookcase** | Free | Only a dead KDE 3 collection manager from 2003–04 (periapsis.org/bookcase, renamed to Tellico). Nothing on Flathub. Minor iOS/web toys. **Usable.** |
| **Stacks** | **Taken** | codeberg.org/robland/stacks is a GTK4/libadwaita epub reader with a Jellyfin backend and a Flatpak workflow. Same niche, same toolkit. Avoid. |
| **Bindery** | **Taken in niche** | vavallee/bindery (496★, automated book download manager, *arr-style). jarynclouatre/bindery ("drop e-books… get Kobo/Kindle-ready files"). evnbr/bindery (book layout JS). Avoid. |
| **Shelfmark** | **Taken** | Shelfmark is the renamed Calibre-Web-Automated Book Downloader (~2.4k★), well known in the CWA and self-hosted book community. There's also a 1★ "infonality/shelfmark", a personal ebook library and reader. Avoid. |
| **Codex** | **Taken** | OpenAI Codex (128k★, a huge brand). ajslater/codex is a self-hosted comic archive server and reader (GPL-3). Avoid. |
| **Folio** | **Taken on Flathub** | `com.toolstack.Folio`, a GNOME markdown notes app. Also FolioReaderKit/Android (ePub reader frameworks). Avoid. |
| **Margins** | Free | Only a dead 2011 iOS book-notes app, an R package and a few tiny repos. Nothing on Flathub or in the GNOME/Linux reader space. **Usable.** It evokes annotations and reading. |
| **Spine** | Conflicted | Not on Flathub, and no Linux reader by that name. But Esoteric Software's *Spine* is a well-known commercial 2D animation tool, and "spine" is a core EPUB/OPF term, which would confuse code and docs (`book.spine`). Not recommended. (GitHub name search was rate-limited.) |
| **Leaflet** | Conflicted | Not on Flathub, but Leaflet.js is a huge map library, and **`AdwLeaflet` is a (deprecated) libadwaita widget**, which is confusing in GNOME dev contexts. Avoid. |
| **Bookends** | **Taken** | Bookends is a long-running commercial macOS reference manager (Sonny Software). bookends.koplugin is a popular KOReader plugin (493★). Avoid. |

**Free and recommended: Margins, then Bookcase.** Shelf is technically free but generic and crowded.

Other book-related names already on Flathub, for reference: Foliate, Readest, Koodo Reader, KOReader, Arianna, Alexandria (two apps), Kepublicity, StoryReader, Komikku, Cozy, Bookup, Flipbook.

---

## 7. Key sources
- foliate-js: <https://github.com/johnfactotum/foliate-js> · fork: <https://github.com/readest/foliate-js>
- Foliate hosting: <https://github.com/johnfactotum/foliate/blob/main/src/webview.js>, `src/book-viewer.js`, `src/reader/reader.{html,js}`, `src/gresource.xml`
- Calibre FAQ: <https://manual.calibre-ebook.com/faq.html>
- kosync: <https://github.com/koreader/koreader-sync-server>, <https://github.com/pid1/kosync-conformance>
- Citadel: <https://github.com/everydaythingssoftware/citadel>
- Alexandria future: <https://github.com/btpf/Alexandria/issues/77>
- Readest paywall: <https://github.com/readest/readest/issues/5033>
- BookLore saga: <https://www.xda-developers.com/single-maintainer-open-source-ticking-time-bomb/>
- Calibre AI forks: <https://lwn.net/Articles/1049886>
- Per-app URLs are cited inline above.
