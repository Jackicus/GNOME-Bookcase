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

