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

## PDFs open in the PDF reader

foliate-js reads PDFs through PDF.js, 13 MB of vendored code for a format GNOME already reads
well: Document Viewer (Papers) and Evince use Poppler. Bookcase lists PDFs with their covers
and metadata (from Poppler's typelib, when installed) and opens them in the PDF reader.

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
