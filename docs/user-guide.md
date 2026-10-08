# Bookcase: user guide

Bookcase keeps your e-books in one place and lets you read them. It shows your books as a
shelf of covers, keeps track of where you are in each, sends books to your e-reader, and
reads a Calibre library without changing it. It never moves, renames or rewrites your files.

## Adding books

**Add Books…** (the + button, or Ctrl+O) adds book files. You can also drag files, or a
whole folder of them, onto the window, or open a book from Files with Bookcase. Added books
are **copied** into your library folder, `~/Books` unless you choose another in Preferences, as
`Author/Title.epub`; the files you added them from are left alone.

Bookcase takes EPUB, Kobo EPUB (`.kepub.epub`), MOBI and AZW3 (and `.azw`, `.prc`), FB2,
CBZ and CBR comics, PDF and plain text. It reads each book's title, authors, series, tags,
publisher, date, language, description and cover from the file itself, and falls back on the
file name ("Author - Title.epub") for what the file does not say.

A book already in the library is not added twice: a file with the same content is skipped,
and a new format of a book you have (the EPUB of a book you have as a PDF) joins that book
rather than making another.

Two other ways to bring books in, both read **in place** (nothing is copied):

- **Add a Folder…** (Ctrl+Shift+O) watches a folder: its books are added where they are,
  and new books put in it later appear the next time Bookcase reads it
  (when it starts, or with *Read Again* in Preferences). A book moved or renamed inside the
  folder is found again; one deleted is marked as missing.
- **Link a Calibre Library…** in the main menu: see [Calibre libraries](#calibre-libraries).

## The window

The sidebar lists:

- **Home:** the books you are reading, most recent first, with how far you are and how long
  is left; and the books added lately.
- **All Books**, and **Authors**, **Series** and **Tags**, each a list to pick from. A
  series lists its books in order.
- **Currently Reading**, **Unread** and **Finished**.
- **Shelves:** your own (see [Shelves](#shelves)).
- **Devices:** an e-reader while it is plugged in.

Books show as covers or as a list (Ctrl+G, Ctrl+L, or the sort and view menu, which also
sorts them by title, author, series, date added, date published, last read or rating). A
cover with a bar under it is a book you have started.

Click a book to select it (Ctrl and Shift add to the selection, and so does dragging a
box around covers); double-click it, or press Enter, to read it, and Alt+Enter shows its
details. Right-click a book (or long-press it on a touchscreen) for everything else: Read, Details,
Edit Details…, Add to Shelf, Mark as Reading, Finished or Unread, Send to Device…,
Export…, Show in Files, Remove from Library and Move to Trash…. With several books
selected (Ctrl+A selects them all), the menu acts on all of them.

## A book's details

The details page shows the cover, title, authors and series (each a link to the rest of the
author's or the series' books), the rating, how far you have read and how long you have
spent reading it, the description, tags, publisher, date, language, identifiers, its files
and formats, the shelves it is on, and its highlights and notes. **Read** (or **Continue
Reading**) opens it.

## Reading

A book opens in a window of its own, at the page you left it on; open several books side by
side if you like. The header shows the chapter, the bar at the foot how far you are, and
the label beside it the percentage, the page, or the time left in the chapter and the book
(click it to change what it shows). Time left is learned from how fast you read.

Turn pages with the arrow keys, Space, Page Up and Down, or by clicking or tapping the
left and right edges of the page; scroll or swipe on a touchpad. Click the middle of the
page to hide the header and the bar. Links to footnotes and other chapters work; after
following one, **Back** (Alt+←) returns you to where you were. A link out of the book opens
in your browser, only when you click it.

The sidebar (Ctrl+T, or the button at the top left) has the book's **contents**, your
**highlights and bookmarks**, and **search**: every place a word or phrase appears, with
the words around it.

**Text and layout** (the button with the "Aa"): the typeface (the book's own, serif, sans
serif, or any font you have), text size, line spacing, margins, the widest a column of text
gets, justification and hyphenation, and the colours of the page: *Follow System*, Light,
Sepia, Dark or Black. *Keep the Book's Own Styles* lets the book's own fonts and spacing
win; turn it off to make every book look the way you set it. **Two Pages** shows facing
pages when the window is wide enough; **Scrolled** reads the book as one long page per
chapter instead of turning pages. These settings are the same for every book.

### Highlights, notes and bookmarks

Select some text to highlight it in yellow, green, blue, pink or purple. The same popover
can add a note, copy the text, **Look Up** a word (Wiktionary) or a phrase (Wikipedia) in your
browser, or search the book for it. Click a highlight to change its colour, edit its note
or remove it. **Ctrl+D** bookmarks the page. All of them are listed in the sidebar and on the
book's details page, in reading order; Ctrl+Z undoes any of them.

### Comics and PDFs

Comics (CBZ) open in the reader like any book. A CBR comic is turned
into a CBZ when it is added, if `bsdtar` (libarchive) is installed. PDFs are listed in the
library with their covers and details, but open in Document Viewer (or your PDF reader),
which reads them better than a book reader can.

### Reader keys

| Key | Does |
|---|---|
| → Space Page Down L | Next page |
| ← Shift+Space Page Up H | Previous page |
| ↓ J, ↑ K | Scroll (in scrolled mode) |
| ] Ctrl+End, [ Ctrl+Home | Next, previous chapter |
| Home, End | Start, end of the book |
| Alt+←, Alt+→ | Back to where you were, forward again |
| Ctrl+T or F9 | Contents, highlights and search |
| Ctrl+F or / | Search the book |
| Ctrl+G, Ctrl+Shift+G | Next, previous result |
| Ctrl+D | Bookmark this page |
| Ctrl+B | Highlights and bookmarks |
| Ctrl++ Ctrl+− Ctrl+0 | Larger, smaller, reset text size |
| Ctrl+J | Go to a location (a percentage of the book) |
| Ctrl+I | Book details |
| F11, Escape | Fullscreen, leave fullscreen |
| Ctrl+W | Close the book |

Reading progress is saved as you go; the book is marked as reading when you open it, and
**Mark as Finished** on its menu or details page files it under Finished.

## Editing details

**Edit Details…** (Ctrl+E, or the pencil on a book's page) edits the title, authors (the
first is the main one), how the title and author are sorted, the series and the book's number
in it, publisher, date, language, rating, tags, description and identifiers (ISBN and
others), and the cover: the menu on the cover chooses an image file, pastes one, finds one
online or removes it. The sort forms follow the title and authors until you change them.
When you opened the book from a list, the arrows in the header save and step to the
previous or next book.

With several books selected, the dialog changes the authors, series (numbered in the order
shown, if you like), publisher, language, rating and reading state of all of them, and adds
or removes tags without touching the others; a field left empty, or on *Leave Unchanged*,
keeps each book's own value.

**Find Metadata…**, in the same dialog, searches Open Library (and Google Books, if you have set a key in
Preferences) by the book's ISBN, or by its title and author. Pick the right match, and
tick which of its details and which cover to take; nothing changes until you press Save.
**Find Cover Online…** shows the covers the search found, to pick one.

Edits are kept in Bookcase's library, not written into the book's file. A copy sent to an
e-reader or exported carries them. Every edit can be undone with Ctrl+Z.

## Shelves

**New Shelf…** at the foot of the sidebar makes a shelf. A shelf is either:

- **a shelf of your own**, which you fill with Add to Shelf on a book's menu (a book can
  sit on several), or
- **a smart shelf**, which holds whatever matches a search, and fills itself: `status:unread
  tag:"science fiction"`, say, or `rating:>=4`. See [Searching](#searching). As you type the
  search, the dialog says how many books it finds.

Right-click a shelf for **Edit…** (its name, and a smart shelf's search) and **Remove…**.
Removing a shelf leaves its books in the library; a shelf with books on it asks first.

## Searching

Press Ctrl+F (or start typing on a list of books) to search the library. Words match the
title, authors, series, tags and publisher, in any order: `harbour lark` finds books whose
title, author, series, tags or publisher hold both words. Case and accents do not matter,
and a word matches inside a word (`harb` finds "Harbour").

| Search | Finds |
|---|---|
| `"a quiet harbour"` | the words together, as a phrase |
| `-poetry` `-tag:poetry` `-"night train"` | books that do *not* match |
| `lark or ross` | books matching either; `(lark or ross) -tag:poetry` groups |
| `title:` `author:` `series:` `tag:` `publisher:` `shelf:` | a word in that part of the book: `author:"ada lark"`, `shelf:holiday` |
| `tag:=fantasy` | the whole value (the tag "Fantasy", not "Urban Fantasy") |
| `language:en` | the language code (`en` also matches `en-GB`) |
| `format:epub` | books with a file in that format (epub, kepub, pdf, mobi, azw3, cbz…) |
| `isbn:9780000000000` | an ISBN (dashes ignored; part of a number matches) |
| `status:unread` `status:reading` `status:finished` | by reading state |
| `rating:4` `rating:>=4` `rating:<3` `rating:4.5` `rating:0` | by stars (0: no rating) |
| `added:<30d` `added:>1y` `added:>=2026-01-01` `added:2025` | by when it was added (`d` days, `w` weeks, `m` months, `y` years) |
| `read:<7d` | opened in the last week (the same forms as `added:`) |
| `published:>=2000` `published:<1900-06` `published:1999` | by publication date |
| `has:cover` `has:series` `has:rating` `has:tags` `has:description` `has:annotations` | books that have one |

Searches side by side must all match (`and` may be written, and changes nothing). A word
with a colon that is not one of these fields is searched as a word (`re:zero`), and a
value Bookcase cannot understand (`rating:many`) matches no book.

## E-readers

Plug an e-reader in with its USB cable (and, on the reader, choose to connect to the
computer). It appears under **Devices** in the sidebar with its free space, the books on it
that are in your library, and those that are not, which you can **Add** to the library.

**Send to Device…** on a book's menu copies it to the reader, with the details you have
edited. What is sent depends on the reader:

- **Kobo:** books go into a `Bookcase` folder on the reader, one folder per author. An EPUB
  is sent as a **Kobo EPUB** (`.kepub.epub`), which gives page numbers and reading
  statistics on the Kobo; turn *Send EPUBs as Kobo EPUBs* off in the dialog or in
  Preferences to send plain EPUBs. Bookcase never writes to the Kobo's own database.
- **Kindle:** books go into `documents/`. A Kindle reads AZW3, MOBI, PDF and text over USB,
  but not EPUB: an EPUB-only book is converted to AZW3 if Calibre's `ebook-convert` is
  installed, and otherwise cannot be sent by cable.
- **Other readers** (PocketBook, Tolino, Boox and others that show a `Books`, `eBooks` or
  `Digital Editions` folder): books go into that folder, in a format the reader takes.

To delete books from the reader, press the select button on its page, tick them, and
**Remove…**; it asks first, and your library is not changed. Eject the reader with the button at the top of its page before you unplug it.
Readers that connect only over MTP (some Kindles from 2024 on) are not supported yet.

## Calibre libraries

**Link a Calibre Library…** in the main menu adds the books of a Calibre library (the
folder holding `metadata.db`) to Bookcase, with Calibre's titles, authors, series, tags,
ratings, descriptions and covers. The files are read where they are, and `metadata.db` is
opened read-only: Bookcase never changes a Calibre library, so you can keep using Calibre
beside it, or stop.

When Calibre changes a book, Bookcase takes the change the next time it reads the library:
when it starts, or with *Read Again* in Preferences. A book you edit in Bookcase keeps your
edits there; they are not written back to Calibre. Preferences lists linked libraries;
removing one there stops Bookcase reading it (the Calibre library itself is untouched).

## Removing books

**Remove from Library** (Delete) takes a book out of Bookcase and leaves its file where it
is; Ctrl+Z puts it back, with its progress, highlights and shelves. **Move to Trash…** also
moves the book's files to the trash, after asking; they can be restored from the trash in
Files.

A book whose file has gone (deleted, or on a drive that is not plugged in) is shown as
missing. **Locate File…** on its page points Bookcase to where it is now.

## Exporting

**Export…** copies the selected books to a folder you choose, as `Title - Author.epub`,
with the details and cover you have edited written into the copy (for EPUBs; other formats
are copied as they are). The books in your library are not changed.

## Preferences

Ctrl+, opens them: the **library folder** added books are copied into; **watched folders**
and **linked Calibre libraries**, with *Read Again*; the reader's text, layout and colours;
whether EPUBs go to a Kobo as Kobo EPUBs; and a **Google Books API key** for Find Metadata (free
from the Google Cloud console: create a project, turn on the Books API, and make an API key
under Credentials).

## Where your data is

The library (`library.sqlite`), covers and thumbnails live in `~/.local/share/bookcase/`,
not beside your books. Your books live in your library folder, your watched folders and your
Calibre libraries. There is no account and no cloud sync: to move to another computer, copy
both the books and that folder.

Bookcase goes online only when you ask it to: Find Metadata (Open Library, and Google Books
with a key), a link out of a book, and Look Up.

## Keyboard shortcuts

Ctrl+? shows them all. In the library:

| Key | Does |
|---|---|
| Ctrl+O, Ctrl+Shift+O | Add books, add a folder |
| Ctrl+F | Search |
| Ctrl+1 to Ctrl+4 | Home, All Books, Authors, Series |
| Ctrl+G, Ctrl+L | Show as covers, as a list |
| Enter, Alt+Enter | Read, details |
| Ctrl+E | Edit details |
| Delete | Remove from library |
| Ctrl+A | Select all |
| Ctrl+Z | Undo |
| F9 | Show or hide the sidebar |
| Alt+← | Go back |
| Ctrl+, | Preferences |
| Ctrl+W, Ctrl+Q | Close the window, quit |
