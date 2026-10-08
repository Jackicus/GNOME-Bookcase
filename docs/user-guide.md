# Bookcase: user guide

Bookcase keeps your e-books in one place and lets you read them. It shows your books as a
shelf of covers, keeps track of where you are in each, sends books to your e-reader, and
reads a Calibre library without changing it. It never moves, renames or rewrites your files.

## Adding books

The first time, with nothing in the library yet, Bookcase looks around your home folder for
books you already have and offers them in a row each: a Calibre library (`~/Calibre
Library`, `~/calibre`, `~/Documents/Calibre Library`, or the one Calibre itself was last
using) to **Link**; a folder of books (`~/Books`, `~/Documents/Books`, `~/eBooks`) to
**Add** where it is (watched), or, from the arrow beside it, to copy in; and the e-books in
your Downloads and Documents folders (EPUB, MOBI, AZW3, FB2 and comics; not PDFs or text
files, which there are rarely books) to copy in. It only counts the files, quickly and
without opening them. Once the first books are in, a tip on Home points at smart shelves and
e-readers, once.

**Add Books…** (the + button, or Ctrl+O) adds book files. You can also drag files, or a
whole folder of them, onto the window. Added books are **copied** into your library folder, `~/Books` unless you choose another in Preferences, as
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

### Reading a file without adding it

A book opened from Files with Bookcase (double-click it, or *Open With*), or with **Read a
File Without Adding…** in the main menu (Ctrl+Alt+O), opens straight away in the reader
without joining your library: nothing is copied, and the file is read where it is. A banner
over the page offers **Add to Library**, which copies it in as Add Books does, keeping your
place and highlights. Until then the book appears in no list, search or count, but Bookcase
remembers where you were in it and your highlights, so opening the same file again carries on
where you left off. A book file you already have opens as that book. Opening a folder from
Files adds its books instead.

## The window

The sidebar lists:

- **Home:** your reading goal at the top (once you have set one, or finished a book this
  year), the books you are reading, most recent first, with how far you are and how long is
  left; and the books added lately.
- **All Books**, and **Authors**, **Series** and **Tags**, each a list to pick from. A
  series lists its books in order.
- **Currently Reading**, **Unread** and **Finished**, and **Statistics** (see
  [Reading goals and statistics](#reading-goals-and-statistics)).
- **Shelves:** your own (see [Shelves](#shelves)).
- **Devices:** an e-reader while it is plugged in.

- **Missing Files**, under All Books, while any book's files cannot be found (see
  [A book's details](#a-books-details) for Locate…).

Books show as covers or as a list (Ctrl+G, Ctrl+L, or the sort and view menu, which also
sorts them by title, author, series, date added, date published, last read or rating;
**Reverse Order** turns any of them round; and a slider sets the size of the covers). A
cover with a bar under it is a book you have started.

The funnel button opens the **filter bar**: Format (EPUB, PDF, comics, Kindle formats, FB2,
text), Status, Rating (5 stars, or 4, 3, 2, 1 and up) and Language, each a menu. Filters
narrow whatever the page shows, together with what you type in the search; Clear, or hiding
the bar, removes them.

**Go To** (Ctrl+K) is one search box for everything: type part of a book's title, an
author, a series, a tag or a shelf, move with the arrow keys, and Enter goes there.

Click a book to select it (Ctrl and Shift add to the selection, and so does dragging a
box around covers); double-click it, or press Enter, to read it, and Alt+Enter shows its
details. Right-click a book (or long-press it on a touchscreen) for everything else: Read, Details,
Edit Details…, Add to Shelf, Mark as Reading, Finished or Unread, Send to Device…,
Export…, Show in Files, Remove from Library and Move to Trash…. With several books
selected (Ctrl+A selects them all, Ctrl+Shift+A none), the menu acts on all of them, and
Delete removes them from the library (with Undo).

### Duplicates

**Find Duplicates** in the main menu lists the books that look like the same book: the
same title and an author in common (the EPUB you added and the PDF a watched folder found,
say). Each group shows its copies, with the one to keep chosen (the one with the most to
lose: a cover, a description, highlights, progress). **Merge** keeps that book and gives it
everything of the others: their files become its formats, their highlights, bookmarks,
reading time and shelves join it, and details it lacks (a description, a series, tags,
identifiers) are taken from them. No file is moved or deleted, and Undo (Ctrl+Z) puts the
books back as they were. **Merge All** does every group at once, as one undo step.

## A book's details

The details page shows the cover, title, authors and series (each a link to the rest of the
author's or the series' books), the rating, how far you have read and how long you have
spent reading it, the description, tags, publisher, date, language, identifiers, its files
and formats, the shelves it is on, and its highlights and notes. **Read** (or **Continue
Reading**) opens it. Under the series, the books before and after this one in it are a click
away; further down, rows of covers show the rest of the series and more by the author.

Each file has **Show in Files** and a menu: **Open With…** (another app: a PDF in Papers,
say), **Open in the Default App** and **Copy Path**. A file Bookcase cannot find any more
is marked missing and has **Locate…**: point at where it is now and Bookcase reads it from
there.

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
can add a note, copy the text, **Look Up** a word or a phrase (below), or search the book for
it. Click a highlight to change its colour, edit its note
or remove it. **Ctrl+D** bookmarks the page. All of them are listed in the sidebar and on the
book's details page, in reading order; Ctrl+Z undoes any of them.

**Export** under the sidebar's list, or the menu beside *Reading* on the book's details page,
saves a book's highlights, notes and bookmarks as a **Markdown** file, or copies them as
Markdown to paste elsewhere. **Export All Highlights…** in the main menu writes one Markdown
file per book into a folder you choose.

**Kindle highlights:** **Import Kindle Highlights…** in the main menu reads a Kindle's
`My Clippings.txt` (on the Kindle, in `documents/`; a plugged-in Kindle's page also offers
**Import Highlights…**). The dialog lists the books the highlights came from with how many
are new; a book Bookcase cannot match is dimmed, and **Choose Book…** picks it from your
library. Import adds the highlights and their notes in one step (Ctrl+Z takes them all
back); importing the same file again adds only what is new. Bookmarks are not imported. A
Kindle location is not a place in your copy of the book, so Bookcase finds each highlight's
text the next time you open the book and marks it there; until then, clicking it in the list
goes to about where it was.

### Look Up

Double-click a word (or select it) and its definition shows in the popover, under the
highlight colours; **Wikipedia** beside **Dictionary** switches to the encyclopedia's
summary. For a longer selection, **Look Up** shows Wikipedia's article on it. **Open in
Browser** opens the whole page; **Search in Book** finds the word in the book. In a narrow
window, Look Up opens the answer in a sheet at the bottom of the window.

Definitions come from Wiktionary (the entries in the book's language first) and summaries
from the Wikipedia in the book's language, so Look Up sends the word to them; offline it says
so. If StarDict dictionaries are installed (in `~/.local/share/stardict/dic` or
`/usr/share/stardict/dic`: the `.ifo`, `.idx` and `.dict` or `.dict.dz` files many
distributions package), Bookcase looks words up in them first, without going online.

### Read Aloud

**Read Aloud** (in the reader's main menu, or **Ctrl+Shift+S**) reads the book from the page
shown, a sentence at a time: the sentence spoken is highlighted and the pages turn with it.
The bar under the page pauses, plays again and stops, and **Speed** sets how fast it reads.
It needs a speech engine: install **speech-dispatcher** with a voice such as **espeak-ng**
or **piper** (and, if you like, its Python module, python-speechd). Without one, Read Aloud
is not in the menu. Books laid out as fixed pages (comics, PDFs) cannot be read aloud.

### PDFs, comics and plain text

**PDFs** open in the reader as pages, drawn sharp for your screen. They scroll from page to
page; in **Zoom and Layout** (the header button) choose **Pages** to see a page at a time,
or two side by side in a wide window (with **Two Pages**, the first page alone, like a
cover). The zoom fits the page's width up to 125% by itself; **Fit Width**, **Fit Page**,
the − and + buttons, Ctrl+plus and Ctrl+minus, Ctrl and the scroll wheel, or pinching on a
touchpad change it, and Ctrl+0 goes back to the automatic zoom. The PDF's own contents
(its outline) are in the sidebar, its links work, and search shows each match with the
words around it, marked on the page. Drag across text to select it: highlight it, add a
note, copy it (or press Ctrl+C), look it up. The paper colours apply too: Sepia tints the
page, Dark and Black turn it light-on-dark (pictures included, as in other PDF readers'
night mode). A PDF locked with a password asks for it. Your place is saved by page.

Comics (CBZ and CBR) open in the reader like any book, a page or two at a time; the
contents list their pages, and the header button keeps only the paper colours. A CBR is
copied into a CBZ the first time it is opened (this needs `bsdtar`, from libarchive).
**Plain text** opens in the reader too: it is set as a book, with its paragraphs, and with
contents made from lines that look like headings ("Chapter 1", "CHAPTER IV", a line in
capitals). The copies made for reading are kept in `~/.cache/bookcase/converted`; your
files are never changed.

**Open With…** in the reader's menu opens the book's file in another app.

### Reader keys

| Key | Does |
|---|---|
| → Space Page Down L | Next page |
| ← Shift+Space Page Up H | Previous page |
| ↓ J, ↑ K | Scroll (in scrolled mode) |
| ] Ctrl+End, [ Ctrl+Home | Next, previous chapter |
| Ctrl+C | Copy the selected text |
| Home, End | Start, end of the book |
| Alt+←, Alt+→ | Back to where you were, forward again |
| Ctrl+T or F9 | Contents, highlights and search |
| Ctrl+F or / | Search the book |
| Ctrl+G, Ctrl+Shift+G | Next, previous result |
| Ctrl+D | Bookmark this page |
| Ctrl+B | Highlights and bookmarks |
| Ctrl++ Ctrl+− Ctrl+0 | Larger, smaller, reset text size |
| Ctrl+J | Go to a location (a percentage of the book; a PDF's page number) |
| Ctrl+I | Book details |
| Ctrl+Shift+S | Read aloud, pause, play again |
| F11, Escape | Fullscreen, leave fullscreen |
| Ctrl+W | Close the book |

When you move into the sidebar or a popover with Tab, the arrow keys, Space, Home and End
work there (moving through the contents, say) instead of turning pages; click the page to
read on.

Reading progress is saved as you go; the book is marked as reading when you open it, and
as finished when you reach its end (with Undo), or with **Mark as Finished** on its menu or
details page. A finished book opens where you left it; one you read to the end and then
marked unread or reading again starts over from the beginning.

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

**Find Metadata for many books:** select books in a list or grid and choose **Find
Metadata…** from their menu. Bookcase looks each up on Open Library, one after another (by
ISBN first; about a book every few seconds, as Open Library asks of apps), and shows each
one's state: *Found*, *Check This Match* (a likely but not certain match), *Not Found*.
**Stop** keeps what has been found; Cancel closes without changing anything. **Review**
lists what each book would get, old → new, in four groups: details (publisher, date,
language, series, ISBN), description, cover and tags, each with a check, and a check for the
book (a likely match starts unchecked). Only empty fields are filled unless you turn on
**Replace Existing Details**; titles and authors are never changed, and tags are only added.
**Apply** writes it all as one change, which Ctrl+Z undoes.

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
| `has:missing` | books whose files cannot be found |

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
  installed, and otherwise cannot be sent by cable (send it by e-mail instead, below).
- **Other readers** (PocketBook, Tolino, Boox and others that show a `Books`, `eBooks` or
  `Digital Editions` folder): books go into that folder, in a format the reader takes.

To delete books from the reader, press the select button on its page, tick them, and
**Remove…**; it asks first, and your library is not changed. Eject the reader with the button at the top of its page before you unplug it.
Readers that connect only over MTP (some Kindles from 2024 on) are not supported yet; send
to them by e-mail.

### Send to Kindle by e-mail

Amazon delivers books mailed to your Kindle's own address (`name@kindle.com`) to the Kindle
and the Kindle apps, over Wi-Fi, whatever the model. **Set Up Send to Kindle…** (in Send to
Device…, or in Preferences → Devices) asks for:

- the **Kindle's e-mail address**: in your Amazon account under *Manage Your Content and
  Devices → Preferences → Personal Document Settings*, or on the Kindle in *Settings → Your
  Account*;
- the **account to send from**: pick the provider (Gmail, Outlook.com, Fastmail, iCloud
  Mail, Proton Mail Bridge, or Other with its server, port and security), your address and
  its password. Gmail, iCloud and Fastmail need an **app password** made in the account's
  security settings, not your usual one. The password is kept in your keyring, never in
  Bookcase's settings.

**Send a Test** mails your own address, to check the server and password. Then, once:
**approve your address at Amazon.** Amazon delivers only mail from addresses on your
account's *Approved Personal Document E-mail List* (same Preferences page) and drops other
mail without telling anyone; the setup has a link to Amazon's help page.

After that, *Kindle by E-mail* is a destination in **Send to Device…**. Each book goes in a
mail of its own, as an **EPUB** with your edited details and cover (or a PDF or text file
when it has no EPUB). Amazon does not take MOBI or AZW3 by e-mail, nor anything over 50 MB;
the dialog says which books cannot go and why. The book arrives a few minutes later.

### Reading sync with KOReader

Bookcase keeps your place in step with **KOReader** (on a Kobo, PocketBook, Kindle or
Android) and with other apps that speak KOReader's *progress sync*, such as Readest, through
a sync server: KOReader's free public one (`https://sync.koreader.rocks`, the default) or one
you run yourself (koreader-sync-server, or the one built into Calibre-Web-Automated, Kavita
or Komga plugins).

1. In Bookcase, **Preferences → Sync**: keep the server or type yours, then a user name and a
   password, and **Create Account** (or **Sign In** if you already have one, from KOReader
   for instance). The password stays in your keyring.
2. On the e-reader, in KOReader: **Tools → Progress sync**, set the same **custom sync
   server** if it is not the public one, and **Login** with the same name and password. Turn
   on *Auto sync* there if you like.
3. Match books the same way on both sides: KOReader's **Document matching method** is
   *Binary* (the file's content) by default, which is Bookcase's **Content**; choose
   **File Name** in both if your copies differ.

Then read. Bookcase sends your place 30 seconds after you stop turning pages, when you
switch away from the book and when you close it. When you open a book that another device
has read further in, more recently, a bar under the header says so (*Kobo Libra 2 is at
62%*) with **Go There**: to the exact place when the other device is Bookcase, else to the
same percentage, since KOReader's own positions mean nothing outside KOReader. Offline, the
place waits and goes when the connection is back. The reader's main menu shows when the
book last synced, and **Sync Now**.

The server sees a fingerprint of each book's file, how far in you are, and this computer's
name (change it in Preferences → Sync); never the book or its title. Books you sent to the
e-reader from Bookcase sync too: Bookcase remembers the fingerprint of the copy it sent.


**Discover** in the sidebar lists online book catalogues (OPDS). Bookcase comes with three
free ones that need no account: **Project Gutenberg**, **ManyBooks** and **Wolne Lektury**
(Polish). Open one to browse its sections; books show as covers, more load as you scroll
down, and a search button appears when the catalogue can be searched. Some catalogues offer
filters (sort order, language) as buttons under the header bar. A book already in your
library carries a check mark.

Click a book for its details: cover, authors, series, what it is about, and its formats.
**Download** takes the best format Bookcase reads (EPUB first, then Kobo EPUB, AZW3, MOBI,
FB2, PDF, CBZ); the Formats list lets you pick another. The book is copied into your library
folder like any book you add, and a notice says "Added “Title”" with **Read**. Books that
are for sale, on loan or protected by DRM are shown with what they are, and cannot be
downloaded in Bookcase.

**Add Catalogue…** (the + button) takes a catalogue's address, and a user name and password
for a server that asks for them. The password is kept in your keyring, sent only to that
server, and never written to Bookcase's settings. Your own server's address is usually:

- **Calibre-Web:** `https://your-server/opds`, with your Calibre-Web user name and password.
- **Kavita:** the OPDS address in your user settings, `https://your-server/api/opds/YOUR-KEY`
  (the key is in the address; no password needed).
- **Komga:** `https://your-server/opds/v1.2/catalog`, with your Komga e-mail and password.
- **Calibre's content server:** `http://your-computer:8080/opds`, with a user name and
  password if you turned them on in Calibre.

Each catalogue's menu (⋮) edits or removes it; removing shows a notice with **Undo**. When
every catalogue is gone, the page offers to bring the free ones back. Standard Ebooks'
catalogue is not built in: it asks for a Patrons Circle account (add it with yours).

## Calibre libraries

**Link a Calibre Library…** in the main menu adds the books of a Calibre library (the
folder holding `metadata.db`) to Bookcase, with Calibre's titles, authors, series, tags,
ratings, descriptions and covers. The files are read where they are, and `metadata.db` is
opened read-only: Bookcase never changes a Calibre library, so you can keep using Calibre
beside it, or stop.

When Calibre changes a book, Bookcase takes the change the next time it reads the library:
when it starts, or with *Read Again* in Preferences. Details you edited in Bookcase win: a
title, a series, tags or a cover you changed here stay as you made them, and only the details
you left alone follow Calibre. Your edits are not written back to Calibre. Preferences lists linked libraries;
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

## Reading goals and statistics

**Statistics** in the sidebar (Ctrl+5) shows your reading, from the time Bookcase's reader
was open on a book:

- **This year's goal:** a ring of the books you have finished out of the books you meant to
  read, and how the year is going ("2 books ahead of schedule", or "3 books to go in 12
  weeks"); under it, the covers of this year's finished books (click one for its details).
  A book counts in the year you marked it finished; marking it unread takes it off.
- **Today** (against your daily minutes, if you set some), your **streak** of days in a row
  with at least a minute of reading (and your longest), the hours read this year, and the
  pages read, estimated, with your pace in pages an hour.
- **Reading days:** the last year as a calendar, each day shaded by the time read.
- **Hours per month** over the last twelve months, and **when you read**: the days of the
  week and the hours of the day.
- **Most read** authors and tags this year, by time (click one for its books).

A day runs from 4 in the morning to 4 the next morning, so a chapter after midnight counts
for the evening it belongs to. Pages are the book's page count when Bookcase knows it, else
an estimate from its file (about 1,500 characters a page; a PDF's or comic's own pages).

The pencil in the header bar sets the goals: **books a year** and **minutes a day**, either
0 for none. They are yours alone: Bookcase shows them and never reminds you of them.

## Preferences

Ctrl+, opens them: the **library folder** added books are copied into; **watched folders**
and **linked Calibre libraries**, with *Read Again*; the reader's text, layout and colours;
whether EPUBs go to a Kobo as Kobo EPUBs, and **Send to Kindle** by e-mail; **Sync** with
KOReader (above); and a **Google Books API key** for Find Metadata (free
from the Google Cloud console: create a project, turn on the Books API, and make an API key
under Credentials).

## Where your data is

The library (`library.sqlite`), covers and thumbnails live in `~/.local/share/bookcase/`,
not beside your books. Your books live in your library folder, your watched folders and your
Calibre libraries. Only reading positions sync, and only when you sign in to a sync server
(above): to move to another computer, copy
both the books and that folder.

Bookcase goes online only when you ask it to: Find Metadata (Open Library, and Google Books
with a key), Send to Kindle (your mail server), reading sync (the server you signed in to),
a link out of a book, and Look Up (Wiktionary and Wikipedia; an installed
StarDict dictionary needs no connection).

## Keyboard shortcuts

Ctrl+? shows them all. In the library:

| Key | Does |
|---|---|
| Ctrl+O, Ctrl+Shift+O | Add books, add a folder |
| Ctrl+Alt+O | Read a file without adding it |
| Ctrl+F | Search |
| Ctrl+K | Go to a book, author, series, tag or shelf |
| Ctrl+1 to Ctrl+5 | Home, All Books, Authors, Series, Statistics |
| Ctrl+G, Ctrl+L | Show as covers, as a list |
| Enter, Alt+Enter | Read, details |
| Ctrl+E | Edit details |
| Delete | Remove from library |
| Ctrl+A, Ctrl+Shift+A | Select all, select none |
| Ctrl+Z | Undo |
| F9 | Show or hide the sidebar |
| Alt+← | Go back |
| Ctrl+, | Preferences |
| Ctrl+W, Ctrl+Q | Close the window, quit |
