# Decisions

Settled choices and why. The research they rest on is in `research/`: `calibre.md` (Calibre's
features, its users' complaints, its library format), `competitors.md` (Foliate, KOReader,
Readest, Apple Books, Kobo and the rest, and foliate-js in depth) and `tech.md` (formats,
WebKitGTK from Python, devices, metadata sources).

## One library window, a reader window per book

Calibre is a toolbar of thirty buttons over a table, a tag browser and a cover pane, with a
separate viewer and editor; its users' first complaint is that it looks dated and cluttered.
Foliate is a fine reader with no library to speak of. Bookcase is one library window, like a
GNOME core app (a sidebar of places, pages in a navigation view, dialogs for editing and
preferences), and each book opens in a window of its own, so two books can be read side by
side and the library stays where it was. Covers come first, because the most upvoted
Calibre post of the year was its new bookshelf view.

## The user's files are theirs

The most repeated demand across Foliate, Readest and Alexandria's trackers is "don't touch my
files", and Calibre's forced `Author/Title (id)/` layout with ASCII-mangled names is its third
most frequent complaint. So a book file is never moved, renamed or rewritten in place. Books
added through the app are copied into the library folder (as `Author/Title.ext`, readable
names, nothing appended), and the originals are left as they were; watched folders and
Calibre libraries are read where they are. Edits to metadata live in the library database,
and a copy sent to a device or exported carries them. Removing a book from the library
leaves its file; Move to Trash is separate, asks first, and uses the trash, so it can be
undone in Files.

## Calibre libraries are read in place, never written

People leaving Calibre have years of curation in `metadata.db`, and many keep Calibre for
conversion or a device it handles better. Bookcase links a Calibre library: it opens the
database read-only (a copy when Calibre holds it locked), takes its metadata, ratings and
covers, and reads the files in place. Writing back would mean registering Calibre's SQL
functions, taking its single-instance lock and keeping up with its schema, and one bug would
damage the library people trust most; Calibre also caches the database in memory and would
not see our writes. Edits made in Bookcase stay in Bookcase. A rescan at startup picks up
what Calibre changed (by its `last_modified`).

## Keep Calibre in Step: the one opt-in exception

Some people keep Calibre for its plugins, its server or a device, and want the curation they
do in Bookcase to show there. So a linked library can be opted in (Preferences → Library →
the library's row → Keep Calibre in Step, confirmed once, with a count of earlier edits
that go too). It is off by default and per library, and calibre_write.py is the only code
that writes to a Calibre library. What it does and why:

- **Exactly what Calibre writes, nothing more.** Title (and its sort), authors (`,` stored
  as `|`, links in display order, new authors' sort by Calibre's own rule), author sort,
  series and index, tags, publisher, pubdate, languages (ISO 639-3; Calibre's further
  languages kept), comments, rating (through the ratings table), identifiers, cover.jpg; a
  new `last_modified` and a `metadata_dirtied` row, so Calibre rewrites `metadata.opf`
  itself the next time it runs (we do not write OPFs: Calibre's own writer stays the one
  source of them). Items matching only in case are renamed as Calibre does; items left
  unused are deleted as Calibre does, unless Calibre keeps a note on them. Custom columns,
  plugin data, annotations, conversion options and `user_version` are never touched.
- **No renaming.** Calibre names a book's folder after its title and first author, but finds
  it by `books.path` whatever that says, and renames it the next time the title or author
  changes in Calibre. Renaming folders under Calibre is the riskiest write there is
  (calibre.md §3.10.5), so Bookcase leaves the path as it is.
- **Dates at noon UTC.** A date-only pubdate written at midnight UTC shows as the day before
  west of Greenwich; noon is the same day in every time zone.
- **Never while Calibre runs.** Calibre caches the whole database in memory and would
  overwrite our changes, so Bookcase binds Calibre's own single-instance lock (the abstract
  socket `\0calibre-singleinstance-<euid>-db`, verified in calibre `utils/lock.py`, held by
  the GUI, calibre-server and calibredb) for the whole write: if it is taken, the edits
  wait and Preferences says how many; while Bookcase holds it, Calibre refuses to start.
  The lock is per network namespace: a sandbox without the host network cannot see it.
- **Refuse what we do not know.** A `user_version` above 28, a missing table or column, a
  foreign `application_id`, or a failing `quick_check`, and nothing is written.
- **Backed up and checked.** The first write of a day copies `metadata.db` beside it as
  `metadata.db.bookcase-backup-YYYYMMDD` (three kept); each write is one transaction, checked
  with `integrity_check` before the commit and after.
- **No queue to lose.** A linked book's `source_values` already say what Calibre last held, so
  the pending edits are the fields whose Bookcase value differs from it. They survive a
  restart, and an undo after a write is simply written back. A field Calibre changed and
  Bookcase did not still comes in through the rescan; one changed in both gets Bookcase's.

It is verified against Calibre's published schema (a library built from
`metadata_sqlite.sql`, read back row by row), not yet against a running Calibre.

## The library database lives in XDG data, not beside the books

Calibre's FAQ warns against keeping its library on a network or cloud drive, because the
database sits in the books folder. Bookcase's `library.sqlite`, covers and thumbnails live in
`$XDG_DATA_HOME/bookcase/`, so the books folder can be anywhere (a NAS, Nextcloud, a second
disk) and the database stays on a local disk.

## Books are keyed by their content

Bookworm loses a book's bookmarks when its file moves, because it keys books by path. A book
here is found by a hash of its content: KOReader's partial MD5 (a few 1 KiB samples at fixed
offsets, fast on large files), which also catches duplicates on import and finds a moved file
in a watched folder again. It is the document id KOReader's sync server (kosync) uses, so a
kosync client can come later without a second hash.

## foliate-js, vendored and pinned

foliate-js renders EPUB, MOBI, AZW3, FB2 and CBZ with pagination, CFIs, search and overlays,
is MIT-licensed, and is proven inside WebKitGTK 6 by Foliate itself; a native renderer would
be years of work and worse. Its README says the API is not stable and to vendor it, so it is
copied into `src/reader/foliate/` at a pinned commit (recorded in its README) and wrapped in
one bridge file (`src/reader/reader.js`): moving to a newer commit, or to the readest fork, is
a change in one place. Book content is untrusted: the page has a strict content security
policy, the book's scripts are off, there is no network and no navigation, and only the
`bookcase://` scheme serves the reader and the open book.

## PDFs open in Bookcase, drawn by Poppler

Users want their PDFs read where their other books are, with the same progress, highlights
and search, not handed to Document Viewer. foliate-js would read them through PDF.js, 13 MB
of vendored JavaScript; Poppler, which Papers and Evince use, is already on every GNOME
system. So widgets/pdf_view.py draws pages with Poppler (a render thread with its own
document, textures cached) behind the same interface as the WebKit BookView, and the reader
window drives either. A PDF's place is `page:N@offset` (pdf_location.py) where an EPUB's is
a CFI; its highlights are `page:N#rects` in the same `location` column, so no schema change.
PDFs scroll by default (their own setting, reader-pdf-scrolled): pages turn badly when a
page is taller than the window. The dark papers invert lightness and keep hues, as other
readers' night modes do; the light ones leave the page white (Apple Books keeps PDFs white).

## TXT and CBR are converted for the reader

Plain text becomes a small EPUB, a CBR a CBZ, in the cache (converting.py), so every reader
feature (pages, typography, search, highlights) works on them without a second code path.
The conversion is deterministic, keyed by the file's path, size and time, so saved CFIs
stay good.

## Open Library by default, Google Books with a key

Calibre's metadata downloads broke in 2026 (Amazon and Goodreads scraping), and Google Books'
API refuses keyless requests (HTTP 429, a quota of zero, tested twice). Open Library needs no
key, answers ISBN and title searches, and has covers; it is the default, asked at most about
once a second as it requests, with a User-Agent naming the app. Google Books is used as well
when the user gives a key of their own. Nothing is fetched without the user asking, and every
found value is shown against the current one before it replaces it.

## Kobo EPUBs for a Kobo, no conversion engine

A Kobo shows page numbers and reading statistics only for its own kind of EPUB. kepubify's
transformation (spans around sentences, two wrapper divs) is small, so `kepub.py` does it in
Python on the copy sent; the book in the library is not changed, and the user can send plain
EPUBs instead. Bookcase has no conversion engine: Calibre's is a codebase of its own, and
its option tree is its second most frequent complaint. A Kindle reads AZW3 and MOBI over USB
but not EPUB, so an EPUB-only book is converted with Calibre's `ebook-convert` when it is
installed, and otherwise cannot be sent by cable.

Convert… (a book's menu) makes another format of a book through `ebook-convert` when it is
installed, with no options shown: the format is the only choice. The result is a new file in
the library folder added to the book; the source is a copy carrying the library's details,
and the book's own files are only read. EPUB to Kobo EPUB is kepub.py's and always works.
Without `ebook-convert` the dialog says how to get it rather than hiding the formats.

## No DRM removal, ever

A book with DRM can sit in the library with its details, but Bookcase cannot open it, and it
neither removes DRM nor points to tools that do. Removing DRM is illegal in many countries,
and an app that ships or links to it cannot be on Flathub or in a distribution.

## Smart shelves are saved searches

Calibre has virtual libraries, saved searches, user categories and a tag browser, four ways
to say "these books". Kobo users complain of flat collections. Bookcase has shelves: a manual
shelf holds the books put on it, and a smart shelf is a saved search in the same syntax as
the search entry (`tag:fantasy status:unread`), so there is one thing to learn, and the
reading states, authors, series and tags are already places in the sidebar.

## Undo instead of confirmations

Every change to the library is an undo step (`Library.undoable`), so editing, removing books
from the library or from a shelf, removing a shelf and deleting a highlight show a toast with
Undo rather than a question. The exceptions are the ones the HIG allows for real losses:
Move to Trash (the files leave the library folder) and removing a shelf that has books on it.
Reading progress is not an undo step; it changes on every page turn.

## Python, like Retain and Music Sleeve

The author's other GNOME apps are Python (Music Sleeve, Retain). A library manager is not
CPU-bound: SQLite does the querying, WebKit the rendering, GdkPixbuf the thumbnails, and
everything slow (imports, scans, lookups, copies to a device) runs in a thread with its own
database connection. The model has no GTK and is tested without a display.

## Reading goals that do not nag

Apple Books, Kindle and Goodreads all keep a yearly goal and a streak, and the complaint
about each is guilt: a broken streak shouted at, "you are 4 books behind". Bookcase keeps
the goal (books a year, minutes a day, both off by default) and says where the year stands
without judging it: "2 books ahead of schedule", "right on schedule", or, behind, what is
left and the time there is ("3 books to go in 12 weeks"). Nothing is sent, no notification,
no badge: the Statistics page and a small card on Home show it. A book counts in the year it
was marked finished (`books.finished`, kept while it is read again, cleared when it is
marked unread); a reading day starts at 4 am, as in Retain, so reading past midnight keeps
the evening's streak.

## Look Up in the reader, offline dictionaries first

Kindle, Apple Books and KOReader show a word's definition the moment it is selected, and
readers expect it. Bookcase does the same in a wide window (the selection popover grows a
definition), and opens a bottom sheet from Look Up in a narrow one, where a tall popover
would cover the page. Installed StarDict dictionaries are asked first, read in pure Python
(no sdcv, no dictd server), so a reader who installs one never sends a word anywhere;
otherwise the word goes to Wiktionary (whose REST definitions exist only on the English
edition, which covers every language: the book's language's entries come first) and a
phrase to the book's language's Wikipedia. Answers are plain text, never the sites' HTML.

## Read Aloud through speech-dispatcher, a sentence at a time

speech-dispatcher is what GNOME's screen reader and every distribution's voices (espeak-ng,
piper, RHVoice) already go through, so Bookcase speaks through it rather than bundling a
voice: its Python client when installed, else `spd-say`. The page (foliate-js's tts.js, at
sentence granularity) gives one sentence, highlighted and turned to; Python speaks it and
asks for the next. A sentence is short enough that pausing and playing again restarts it.
Without an engine the feature is not offered at all, and the user guide says what to install.

## Send to Kindle by e-mail, one book a mail, through the user's own account

Amazon's e-mail intake is the only way to put an EPUB on every Kindle (2024 and later
models mount over MTP, and none reads EPUB by cable). Bookcase sends through the user's own
SMTP account, not a service of ours: nothing of theirs passes through a third party, and
Amazon only accepts mail from addresses the user approved anyway. Each book goes in a mail of
its own over one connection, so one EPUB Amazon rejects does not take the others with it and
each mail stays under the provider's own size limit (Gmail's is 25 MB, Amazon's 50). The
password lives in the keyring (libsecret), never in GSettings, which any app can read.

## Find Metadata for many books: slow, reviewed, one undo step

r/Calibre's most-upvoted complaint of 2026 is bulk metadata downloads hammering the sources
until they block everyone. Bookcase looks books up one at a time at Open Library's own rate
(a request a second), ISBN first, and changes nothing until the user has seen each book's
changes (old → new, by group) and pressed Apply, which is one undo step. By default only
empty fields are filled, titles and authors are never changed in bulk, and tags are only
added: a wrong match costs little and Ctrl+Z takes it all back.

## Reading sync speaks KOReader's protocol, and jumps by percentage

Readers ask for their place to follow them between devices; the one open protocol many apps
already speak is KOReader's progress sync (kosync: KOReader, Readest, Koodo, Komga and
Calibre-Web-Automated). Bookcase is a kosync client against KOReader's free server or one of
the user's own, not a sync service of ours. A position is a document id, a `progress` string
and a `percentage`; `progress` means something only to the app that wrote it (KOReader's
XPointers, our CFIs), so a place from another app is reached by its percentage, and only a
foliate-js reader's CFI (another Bookcase) or a PDF's page number is used exactly. Document
ids are KOReader's own (the partial MD5 of the file, or the MD5 of its name, as the user has
KOReader set), and since a copy Bookcase sends to an e-reader is a different file (its
metadata written in, often a kepub), Bookcase remembers the copies' ids and reads and writes
all of a book's ids. Opening a book never pushes: a place is sent only after the reader moves
(30 s after the last page turn, on losing focus, on closing), so a forgotten window never
overwrites the e-reader's newer place; a newer place from elsewhere is offered in a banner,
never jumped to without asking. The key (MD5 of the password, all kosync sends) lives in the
keyring; positions that cannot be sent wait in sync.json and go when the network is back.

## Opening a file reads it; adding is a separate step

Before, a book opened from Files was copied into the library and then opened: one
double-click made a copy the user may not have wanted, the opposite of what every other
viewer does. Now opening reads the file where it is, at once. The book still gets a row in
the library (source `opened`), so everything the reader does (the place, highlights,
bookmarks, reading sessions) works unchanged and is there the next time the same file is
opened, found by path or content hash; but `Library._where` (and the author, series, tag,
publisher and language lists) leave `opened` books out, so they appear in no list, count,
search, smart shelf or Home row. A banner in the reader offers Add to Library, which copies
the file in (as Add Books does) and makes the row a library book, keeping its place and
highlights, as one undo step; adding the same file through Add Books, or a watched folder
finding it, does the same. A separate table for opened files was considered and rejected:
it would have doubled the reader's paths for progress and annotations. Opened books with
their file gone stay as they are; they cost a row each and are invisible. Opening a folder
from Files still adds it (folders are not read). Open File… is Ctrl+Alt+O: Ctrl+O stays Add
Books, the library's main verb, and Ctrl+Shift+O Add a Folder.

## The welcome looks for the user's books, and only counts them

A new user's first question is "where are my books?". On an empty library the welcome
(existing_books.py) looks for Calibre libraries (the usual folders and Calibre's own
`global.py.json`) and folders with books (~/Books, ~/eBooks, Downloads, Documents), in a
thread, three seconds at most, three folders deep, never following links, counting file
names without opening them. Downloads and Documents count only e-book formats (not PDFs and
text files, mostly not books there) and are offered as a copy of just those files; a folder
of books is offered to watch (read in place), with copying as the other choice. Nothing is
added until the user presses a button. `BOOKCASE_HOME_HINTS` points the search at an
invented home for screenshots and tests.

## Merging duplicates keeps everything and moves nothing

Duplicates are the same normalised title (titles.title_key) with an author in common, the
rule importing already uses for a new format. Merge keeps one book (the one with most to
lose, picked by Library.richest, changeable) and moves the others' files, annotations,
sessions and shelf places to it, fills the details it lacks from them, keeps the best rating,
the most recent reading place and the earliest date added, then removes the others' rows;
files never move on disk. It is one undo step (a cover taken from another copy included).
Two files of one format may then belong to one book; the reader opens the first that exists.

## Filters are searches

The filter bar's choices (format, status, rating, language) become search terms
(search.filter_query) added to the typed search, so they combine with it and with any
page's own filter with no second query path, and a smart shelf could be made of them.

## Sharing the library: a read-only catalogue on the LAN, off until asked

Calibre's content server is mostly used to get books onto a phone or an e-reader over Wi-Fi
(calibre.md §2.2), so Bookcase serves exactly that: an OPDS 1.2 catalogue for reading apps
(KOReader, Readest, Thorium) and plain HTML pages, without scripts, for any browser down to
a Kindle's; no reader in the browser, no editing, no uploads, no users. The server is the
standard library's (http.server, a thread per request with a library connection of its own
from a small pool), so it brings no dependency. Choices made for safety:

- **Off by default**, a switch in Preferences; it stops when the app quits. When on, it
  starts with the app again, as Calibre's "run the server automatically" does.
- **A password by default.** HTTP Basic over plain HTTP, like Calibre's server: every
  e-reader browser and OPDS app speaks it, and a self-signed certificate would be refused by
  them. The password is generated (14 characters from an unambiguous alphabet, typed easily
  on an e-reader) and kept in the keyring under its own schema
  (io.github.jackicus.Bookcase.Sharing), never in GSettings. Passwords are compared in
  constant time; five wrong ones in a minute lock the address out for a minute (429).
- **Local networks only.** It listens on all IPv4 interfaces ("Devices on This Network") or
  on 127.0.0.1 ("This Computer Only"), and answers only private, loopback and link-local
  client addresses, so a computer with a public address does not serve the internet.
- **No DNS rebinding.** A Host header must be an address, a single-label name or a
  .local/.lan/.home.arpa/.internal name: a web page on another domain cannot point its own
  name at the server and read the library through the user's browser.
- **Ids, never paths.** URLs name a book by its id and a format; the file is found through
  the library. Books opened without adding (library.OPENED) are never served.
- **Edits travel.** An EPUB is sent as exporting.export_copy makes it (metadata and cover
  written in), cached for the session so a resumed (Range) download gets the same bytes.
- **Quiet logs.** No request line, search, path or credential is logged; only refused
  addresses and failures.
- **Discoverable, cheaply.** The addresses are shown with a QR code (qr.py: a small encoder,
  byte mode, versions 1-10, checked against libqrencode, since no QR library is in the GNOME
  runtime) and announced through Avahi on the system bus when it runs.
- **The QR code is black on white** whatever the style: phone cameras read light-on-dark
  codes badly. It is the one place the sharing page draws its own colours.

## Devices through Gio, so MTP readers work like drives

2024 and later Kindles, and Android readers, mount over MTP: gvfs gives an mtp:// location and
no local path (a FUSE path at best, slow and flaky). Rather than a second code path for them,
devices.py does all its device I/O through Gio.File (devices.Storage: detect, list, copy with
progress, rename, delete, free space, read), and a USB drive is the file:// case of the same
code, with an fsync where there is a local path. Over MTP a file is written under a temporary
name and then renamed (gvfs's MTP backend may refuse a move, so the old copy is deleted first
then), and a folder is deleted only once empty (deleting a full folder over MTP may take its
contents, or not). Books on an MTP reader are known by file name, not read, as reading each
would mean fetching it. A Kindle over MTP still takes no EPUB by cable; the reason shown
points to Send to Kindle by e-mail.

## Kobo collections: opt-in per Kobo, backed up, checked

Shelves as Kobo collections means writing the Kobo's own database, which also holds its
reading progress, highlights and store account; Calibre's FAQ calls the Kobo's firmware buggy,
and its schema is the Kobo's to change with any update. So it is off until turned on for a
particular Kobo (remembered by serial number), and every write is guarded: the expected tables
and columns are checked first (anything else and nothing is written); nothing is written while
a `-journal` file or another program's lock says the database is in use; a copy is made beside
it (`.kobo/KoboReader.sqlite.bookcase-backup`, the state before Bookcase's latest change,
replaced each write, so it never holds stale reading progress for long); the change is one
transaction; `PRAGMA integrity_check` runs after, and a failure puts the copy back. Bookcase
only touches collections named after its shelves, and in them only books it knows (on the
Kobo and in the library), so collections and entries the user made on the Kobo stay. Rows are
written as Calibre's KoboTouch driver writes them (`Type` 'UserTag', 'true'/'false' flags,
ContentID `file:///mnt/onboard/<path>`), the most-used writer of this database. Reading
progress goes the other way read-only: the Kobo is opened `mode=ro`, and Bring Reading
Progress From Kobo changes only the library, only where the Kobo is further on.


## The Flatpak sees the usual places; the file chooser grants the rest

The Flatpak (build-aux/flatpak, GNOME 51 runtime) does not ask for the whole home folder.
`--filesystem=home` (and `home:ro`) is an error in Flathub's linter that needs an exception
argued with reviewers, and most of what Bookcase reads is in a few known places. It gets:
`~/Books` (the default library folder, made when missing), Documents and `~/Calibre Library`
read-write (where people keep books and Calibre libraries: read in place, written by Keep
Calibre in Step, trashed by Move to Trash), Downloads read-only (the welcome counts e-books
there; adding copies them), `~/.config/calibre` read-only (Calibre's record of where its
library is) and `~/.local/share/stardict` read-only (offline dictionaries). Every other folder
comes through the file chooser, which in a Flatpak is the portal: a watched folder, a Calibre
library or a library folder elsewhere is granted when the user picks it, and the grant is kept
between runs (the path is then the portal's, `/run/user/…/doc/…`). The welcome's look finds
only what is in the granted places; the rest is one Add Folder… away.

The rest: the network (Open Library, catalogues, sync, mail, Look Up, Library Sharing's server;
sharing the host's network namespace also lets calibre_write see Calibre's abstract lock
socket); `/run/media` and `/media` with gvfs (`org.gtk.vfs.*`, `xdg-run/gvfs`) for e-readers,
their MTP mounts and eject; the host's speech-dispatcher socket for Read Aloud (its Python
client is built into the Flatpak; the daemon is the host's); Avahi on the system bus to
advertise Library Sharing. Passwords go through libsecret's Secret portal, links and Open
With through the OpenURI portal, so neither needs a bus name. Not given, so these degrade:
logind (`finish-args-login1-system-talk-name` is a linter error), so KOReader sync does not
push just before suspend, only on its usual triggers; and host programs (`flatpak-spawn
--host` would be a sandbox escape), so Calibre's `ebook-convert` is not found and Convert…
and sending offer only EPUB to Kobo EPUB (kepub.py's), as on a system without Calibre. Poppler, lxml
and speechd's client are built into the Flatpak; the runtime has WebKitGTK 6, libsecret and
bsdtar (CBR comics).

## A PDF's zoom and layout are kept per book, in their own table

A PDF read zoomed to fit its width, a manga read right to left, a slide deck read a page at
a time: the layout belongs to the book, so the reader keeps it per book (the zoom or fit,
pages or scrolling, right to left, the cover alone) in `book_state` (schema 4: a JSON object
per book, the PDF's under 'pdf'). Not in the location string: that is the reading position,
which sync, bookmarks and progress share, and a zoom does not move it. Not a column on
books: the reader alone reads it, so Book and its queries stay as they are. Like progress it
is no undo step, but it leaves and comes back with the book (remove_books, Undo). The
reader-pdf-scrolled setting stays as the choice for a PDF never set. Right to left follows
the PDF's /Direction where it can be seen (Poppler's GObject API does not give it, so the
file is scanned) and the switch is always there.

## Series stacks are Kindle's option, off at first; forgetting an opened book is undoable

Apple Books groups a series only inside its collections; Kindle has "Collapse Series" as an
option of the library. Bookcase follows Kindle: **Group Series** in All Books' menu (the
group-series setting, off by default, so nobody's grid changes under them). A stack takes
its first book's place in the order chosen, which keeps every sort meaningful; it stands for
all its books when selected; a search and the list always show books, because a search is
for finding one. The grouping is done in Python over the sorted books (collapse(): 4–14 ms
over 10,000 books, 1,700 series), not in SQL, so it follows whatever sort and filters made
the list. Forgetting a book opened without adding (Recently Opened on Home) removes its row,
place, sessions and highlights as Remove from Library does, so Undo brings it all back;
its reader is closed first so nothing is saved after. The Year in Review is offered for
finished years only (a year still going reads as a verdict on unfinished work), and its
favourites count books finished before time spent.
