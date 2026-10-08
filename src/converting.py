# SPDX-License-Identifier: GPL-2.0-or-later
# SPDX-FileCopyrightText: 2026 Jack Tully

"""Books the reader page cannot open as they are, converted into a cache it can: plain
text into a small EPUB, a CBR (RAR) into a CBZ (zip). The user's file is only read.

    prepare(path, fmt, title='', author='', language='')  # -> (path, fmt) for the reader
    needs_conversion(fmt)                                 # 'txt' and 'cbr'
    cache_dir()                                           # where the copies are kept
    txt_to_epub(path, dest, title='', author='', language='')
    text_blocks(text) / chapters(blocks)                  # the TXT heuristics, for tests

The cache is `$XDG_CACHE_HOME/bookcase/converted/` ($BOOKCASE_DATA_DIR/converted when that is
set: the tests and the demo), one `<hash>.epub` or `.cbz` per file, the hash of its path,
size, modification time and VERSION: a changed file (or a new converter) is converted again,
and the same file always converts the same way, so the reader's saved places (CFIs into the
copy) stay good. The oldest copies go when the cache passes CACHE_LIMIT bytes.

Plain text: decoded by formats.txt.decode() (UTF-8, UTF-16 with a BOM, else Windows-1252);
paragraphs are split on blank lines (each line is one when the file has almost no blank
lines), hard-wrapped lines joined, short-lined blocks (verse, addresses) kept as lines.
A heading is a short block of one line that starts with Chapter, Part, Book, Prologue…, or is
a Roman or Arabic number alone, or is in capitals (unless capitals are everywhere); a short
line after a "Chapter 1" is its title ("Chapter 1: The Harbour"). Each heading starts a
section of the EPUB and an entry of its contents.

A CBR becomes a CBZ through formats.comic.cbr_to_cbz() (bsdtar); a '.cbr' that is really a
zip opens as it is. Raises formats.FormatError when it cannot (no bsdtar: the message says so).

Convert… (dialogs/convert.py) makes another format of a book and adds it to the book:

    ebook_convert()                       # Calibre's ebook-convert on PATH, or None
    targets(formats, program)             # [(format, None or why it cannot be made)]
    convert_book(library, covers, book_id, target, library_folder, program=None,
                 progress=None, cancelled=None, add=True) -> path   # blocking: a thread
    run_ebook_convert(src, dest, program, progress, cancelled)

TARGETS are EPUB, kepub, AZW3, MOBI, PDF and FB2. EPUB to kepub is kepub.py's, pure Python,
always there; everything else runs Calibre's ebook-convert (in a session of its own, so
Cancel stops it and its children: SIGTERM, then SIGKILL after STOP_WAIT_S), whose 'NN% what'
lines are the progress. What is converted is a copy of the book's best file carrying the
library's metadata (exporting.export_copy); the result goes into the library folder as
'Author/Title.ext' (a free name, claimed with O_EXCL so nothing there is ever written over;
written as a temporary '.part' and renamed onto it) and is added as the book's new format
(an undoable 'Add Format'). The book's own files are only read. Raises ConversionError, or
ConversionCancelled.
"""

import collections
import contextlib
import errno
import hashlib
import html
import logging
import os
import re
import shutil
import signal
import subprocess
import tempfile
import threading
import zipfile
from gettext import gettext as _

from gi.repository import GLib

from .formats import FormatError, comic, txt

log = logging.getLogger(__name__)

VERSION = 1
CACHE_LIMIT = 1024 * 1024 * 1024
SECTION_BLOCKS = 400  # a text without headings is cut into sections of this many paragraphs
MAX_HEADING = 70
ZIP_DATE = (2000, 1, 1, 0, 0, 0)

_WORDS = (r'chapter|chap\.|part|book|volume|vol\.|section|prologue|epilogue|preface|'
          r'foreword|introduction|afterword|appendix|interlude|letter|act|scene|canto|stave|'
          r'kapitel|chapitre|cap[ií]tulo|capitolo|teil|partie|parte|livre|libro|buch')
_COUNT = (r'[0-9]+|[ivxlcdm]+|one|two|three|four|five|six|seven|eight|nine|ten|eleven|twelve|'
          r'thirteen|fourteen|fifteen|sixteen|seventeen|eighteen|nineteen|twenty[a-z-]*|'
          r'thirty[a-z-]*|forty[a-z-]*|fifty[a-z-]*|first|second|third|fourth|fifth|sixth|'
          r'seventh|eighth|ninth|tenth|last')
# A heading word alone ("Prologue"), or with a number and maybe a title ("Book II: The Sea").
_HEADING_WORD = re.compile(rf'^(?:{_WORDS})(?:\s+(?:{_COUNT})\b.*)?$', re.I)
_NUMBERED = re.compile(rf'^(?:{_WORDS})\s+(?:{_COUNT})\.?$', re.I)
_NUMBER = re.compile(r'^(?:[0-9]{1,4}|[IVXLCDM]{1,8})\.?$')
_SENTENCE_END = re.compile(r'[.,;!?"”’]$')


def needs_conversion(fmt):
    return (fmt or '').lower() in ('txt', 'cbr')


def cache_dir():
    override = os.environ.get('BOOKCASE_DATA_DIR')
    if override:
        return os.path.join(override, 'converted')
    return os.path.join(GLib.get_user_cache_dir(), 'bookcase', 'converted')


def _key(path):
    status = os.stat(path)
    text = f'{os.path.abspath(path)}\0{status.st_size}\0{status.st_mtime_ns}\0{VERSION}'
    return hashlib.sha256(text.encode('utf-8', 'surrogateescape')).hexdigest()[:32]


def prepare(path, fmt, title='', author='', language='', directory=None):
    """The file to give the reader for a book at `path` in format `fmt`, and its format:
    (path, fmt) as they are when the reader opens it, else the converted copy in the
    cache, made now if needed (this can take a while: call it off the main thread)."""
    fmt = (fmt or '').lower()
    if not needs_conversion(fmt):
        return path, fmt
    if fmt == 'cbr':
        with open(path, 'rb') as file:
            if file.read(2) == b'PK':
                return path, 'cbz'
    directory = directory or cache_dir()
    os.makedirs(directory, exist_ok=True)
    target_fmt = 'epub' if fmt == 'txt' else 'cbz'
    dest = os.path.join(directory, f'{_key(path)}.{target_fmt}')
    if os.path.exists(dest):
        try:
            os.utime(dest)  # recently used: kept longest
        except OSError:
            pass
        return dest, target_fmt
    if fmt == 'txt':
        txt_to_epub(path, dest, title=title, author=author, language=language)
    else:
        comic.cbr_to_cbz(path, dest)
    prune(directory, keep=dest)
    return dest, target_fmt


def prune(directory, limit=CACHE_LIMIT, keep=None):
    """Remove the least recently used copies until the cache holds at most `limit` bytes."""
    try:
        entries = []
        for name in os.listdir(directory):
            full = os.path.join(directory, name)
            if os.path.isfile(full) and full != keep:
                status = os.stat(full)
                entries.append((status.st_mtime, status.st_size, full))
        total = sum(size for _mtime, size, _path in entries)
        if keep and os.path.exists(keep):
            total += os.path.getsize(keep)
        for _mtime, size, full in sorted(entries):
            if total <= limit:
                break
            os.remove(full)
            total -= size
    except OSError as error:
        log.info('pruning the converted books: %s', error)


# -- plain text --------------------------------------------------------------------------------

def text_blocks(text):
    """The paragraphs of a text: [[line]] per block."""
    text = text.replace('\r\n', '\n').replace('\r', '\n').replace('\f', '\n\n')
    lines = [line.rstrip() for line in text.split('\n')]
    blocks, block = [], []
    for line in lines:
        if line.strip():
            block.append(line.strip())
        elif block:
            blocks.append(block)
            block = []
    if block:
        blocks.append(block)
    # No blank lines to speak of: each line is a paragraph.
    nonblank = sum(len(b) for b in blocks)
    if nonblank > 20 and len(blocks) * 15 < nonblank:
        blocks = [[line] for b in blocks for line in b]
    return blocks


def _is_verse(block):
    return len(block) > 1 and all(len(line) < 48 for line in block)


def _letters(line):
    return [c for c in line if c.isalpha()]


def _heading_kind(block, caps_allowed):
    """'numbered' ("Chapter 1"), 'heading' (any other heading) or None."""
    if len(block) != 1:
        return None
    line = block[0]
    if len(line) > MAX_HEADING:
        return None
    if _NUMBERED.match(line) or _NUMBER.match(line):
        return 'numbered'
    if _HEADING_WORD.match(line) and not line.endswith((',', ';')) and len(line.split()) <= 12:
        return 'heading'
    letters = _letters(line)
    if caps_allowed and len(letters) >= 3 and all(c.isupper() for c in letters if c.isalpha()) \
            and len(line.split()) <= 8 and not line.endswith(','):
        return 'heading'
    return None


def chapters(blocks, title=''):
    """[(heading or '', [block])]: the text cut at its headings."""
    caps_lines = sum(1 for block in blocks if len(block) == 1 and len(_letters(block[0])) >= 3
                     and all(c.isupper() for c in _letters(block[0])))
    caps_allowed = caps_lines <= max(3, len(blocks) // 8)
    sections = [('', [])]
    index = 0
    while index < len(blocks):
        block = blocks[index]
        kind = _heading_kind(block, caps_allowed)
        if kind is None:
            sections[-1][1].append(block)
            index += 1
            continue
        heading = block[0]
        index += 1
        following = blocks[index] if index < len(blocks) else None
        if (kind == 'numbered' and following and len(following) == 1
                and len(following[0]) <= MAX_HEADING and not _SENTENCE_END.search(following[0])
                and _heading_kind(following, caps_allowed) != 'numbered'):
            heading = f'{heading.rstrip(".")}: {following[0]}'
            index += 1
        sections.append((heading, []))
    if not sections[0][1]:
        sections.pop(0)
    # A long text without headings is cut, so the reader loads it a part at a time.
    cut = []
    for heading, body in sections:
        if len(body) <= SECTION_BLOCKS:
            cut.append((heading, body))
            continue
        for start in range(0, len(body), SECTION_BLOCKS):
            cut.append((heading if start == 0 else '', body[start:start + SECTION_BLOCKS]))
    return cut or [('', [])]


def _paragraph(block):
    if _is_verse(block):
        return '<p class="lines">' + '<br/>'.join(html.escape(line) for line in block) + '</p>'
    return '<p>' + html.escape(' '.join(block)) + '</p>'


def _xhtml(title, body, language):
    return ('<?xml version="1.0" encoding="utf-8"?>\n<!DOCTYPE html>\n'
            f'<html xmlns="http://www.w3.org/1999/xhtml" xmlns:epub="http://www.idpf.org/2007/ops"'
            f' xml:lang="{language}" lang="{language}">\n<head><meta charset="utf-8"/>'
            f'<title>{html.escape(title)}</title>'
            '<link rel="stylesheet" type="text/css" href="style.css"/></head>\n'
            f'<body>\n{body}\n</body>\n</html>\n')


STYLE = """p { margin: 0; text-indent: 1.5em; }
h2 + p, p.lines, p.lines + p, p:first-child { text-indent: 0; }
p.lines { margin: 0.8em 0; }
h2 { text-align: center; margin: 2em 0 1.5em; font-weight: 600; }
"""


def txt_to_epub(path, dest, title='', author='', language=''):
    """Write an EPUB 3 at `dest` with the text of the file at `path`."""
    with open(path, 'rb') as file:
        data = file.read()
    if b'\0' in data[:txt.SAMPLE] and not data.startswith((b'\xff\xfe', b'\xfe\xff')):
        raise FormatError('Not a text file')
    text = txt.decode(data)
    title = title or os.path.splitext(os.path.basename(path))[0]
    language = re.sub(r'[^A-Za-z0-9-]', '', language or '') or 'en'
    sections = chapters(text_blocks(text), title)
    identifier = 'urn:bookcase:' + hashlib.sha256(data).hexdigest()[:32]

    files = []  # (name, label or '')
    for number, (heading, body) in enumerate(sections, 1):
        content = ''
        if heading:
            content += f'<h2>{html.escape(heading)}</h2>\n'
        content += '\n'.join(_paragraph(block) for block in body)
        files.append((f'text{number:04d}.xhtml', heading, _xhtml(heading or title, content,
                                                                 language)))
    entries = [(name, label) for name, label, _body in files if label]
    if not entries:
        entries = [(files[0][0], title)]
    nav_items = '\n'.join(f'<li><a href="{name}">{html.escape(label)}</a></li>'
                          for name, label in entries)
    nav = _xhtml(title, f'<nav epub:type="toc" id="toc"><h1>{html.escape(title)}</h1>'
                        f'<ol>\n{nav_items}\n</ol></nav>', language)
    manifest = '\n'.join(f'<item id="t{n}" href="{name}" media-type="application/xhtml+xml"/>'
                         for n, (name, _label, _body) in enumerate(files, 1))
    spine = '\n'.join(f'<itemref idref="t{n}"/>' for n in range(1, len(files) + 1))
    creator = f'<dc:creator>{html.escape(author)}</dc:creator>' if author else ''
    opf = ('<?xml version="1.0" encoding="utf-8"?>\n'
           '<package xmlns="http://www.idpf.org/2007/opf" version="3.0" '
           'unique-identifier="id">\n<metadata xmlns:dc="http://purl.org/dc/elements/1.1/">'
           f'<dc:identifier id="id">{identifier}</dc:identifier>'
           f'<dc:title>{html.escape(title)}</dc:title>{creator}'
           f'<dc:language>{language}</dc:language>'
           '<meta property="dcterms:modified">2000-01-01T00:00:00Z</meta></metadata>\n'
           '<manifest>\n<item id="nav" href="nav.xhtml" media-type="application/xhtml+xml" '
           'properties="nav"/>\n<item id="style" href="style.css" media-type="text/css"/>\n'
           f'{manifest}\n</manifest>\n<spine>\n{spine}\n</spine>\n</package>\n')
    container = ('<?xml version="1.0" encoding="utf-8"?>\n<container version="1.0" '
                 'xmlns="urn:oasis:names:tc:opendocument:xmlns:container"><rootfiles>'
                 '<rootfile full-path="OEBPS/content.opf" '
                 'media-type="application/oebps-package+xml"/></rootfiles></container>\n')

    # A temporary name of its own: two readers opening the same text at once never write
    # into one file.
    fd, part = tempfile.mkstemp(dir=os.path.dirname(os.path.abspath(dest)),
                                prefix=os.path.basename(dest) + '.', suffix='.part')
    os.close(fd)
    try:
        with zipfile.ZipFile(part, 'w') as archive:
            def add(name, content, compress=zipfile.ZIP_DEFLATED):
                info = zipfile.ZipInfo(name, ZIP_DATE)
                info.compress_type = compress
                archive.writestr(info, content)

            add('mimetype', 'application/epub+zip', zipfile.ZIP_STORED)
            add('META-INF/container.xml', container)
            add('OEBPS/content.opf', opf)
            add('OEBPS/nav.xhtml', nav)
            add('OEBPS/style.css', STYLE)
            for name, _label, body in files:
                add(f'OEBPS/{name}', body)
        os.replace(part, dest)
    finally:
        if os.path.exists(part):
            os.remove(part)
    return dest


# -- Convert…: another format of a book, into the library -----------------------------------------

TARGETS = ('epub', 'kepub', 'azw3', 'mobi', 'pdf', 'fb2')
SUFFIXES = {'epub': '.epub', 'kepub': '.kepub.epub', 'azw3': '.azw3', 'mobi': '.mobi',
            'pdf': '.pdf', 'fb2': '.fb2'}
# The best input for ebook-convert first: reflowable, with the most structure.
SOURCE_ORDER = ('epub', 'kepub', 'azw3', 'mobi', 'fb2', 'fbz', 'txt', 'cbz', 'cbr', 'pdf')
_PROGRESS = re.compile(r'^\s*(\d{1,3})%\s*(.*)$')
STOP_WAIT_S = 5


class ConversionError(Exception):
    """A conversion that failed; str() is a sentence for the user."""


class ConversionCancelled(ConversionError):
    pass


def ebook_convert():
    """Calibre's ebook-convert, when it is on PATH, else None."""
    return shutil.which('ebook-convert')


def source_format(formats, target):
    """The format of a book's files to convert from to `target`, or None."""
    formats = [fmt for fmt in formats if fmt != target]
    if target == 'kepub' and 'epub' in formats:
        return 'epub'
    return next((fmt for fmt in SOURCE_ORDER if fmt in formats), None)


def targets(formats, program=None):
    """What Convert… offers a book with these formats: [(format, None when it can be made,
    else why not)]. EPUB to kepub is Bookcase's own (kepub.py); the rest is ebook-convert's."""
    offers = []
    for target in TARGETS:
        source = source_format(formats, target)
        if target in formats:
            reason = _('Already in the library')
        elif source is None:
            reason = _('No file to convert from')
        elif target == 'kepub' and source == 'epub':
            reason = None
        elif program is None:
            reason = _('Needs Calibre’s ebook-convert')
        else:
            reason = None
        offers.append((target, reason))
    return offers


def run_ebook_convert(src, dest, program=None, progress=None, cancelled=None):
    """Run `ebook-convert src dest` (the formats from the names), reporting its 'NN% what'
    lines as progress(fraction, text); cancelled() is asked every fifth of a second, and
    stops the program (ConversionCancelled). ConversionError with its last lines when it
    fails."""
    program = program or ebook_convert()
    if program is None:
        raise ConversionError(_('Calibre’s ebook-convert is not installed'))
    try:
        # Absolute paths: a name starting with '-' would be read as an option.
        process = subprocess.Popen([program, os.path.abspath(src), os.path.abspath(dest)],
                                   stdin=subprocess.DEVNULL,
                                   stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                                   text=True, errors='replace', start_new_session=True)
    except OSError as error:
        raise ConversionError(_('ebook-convert could not start: {error}').format(error=error)) \
            from error
    tail = collections.deque(maxlen=8)

    def read():
        for line in process.stdout:
            line = line.strip()
            if not line:
                continue
            tail.append(line)
            match = _PROGRESS.match(line)
            if match and progress is not None:
                progress(min(100, int(match.group(1))) / 100, match.group(2))

    reader = threading.Thread(target=read, name='bookcase-ebook-convert', daemon=True)
    reader.start()
    try:
        while True:
            try:
                process.wait(timeout=0.2)
                break
            except subprocess.TimeoutExpired:
                if cancelled is not None and cancelled():
                    _stop(process)
                    raise ConversionCancelled(_('Conversion cancelled')) from None
    finally:
        reader.join(timeout=STOP_WAIT_S)
        process.stdout.close()
    if process.returncode != 0 or not os.path.isfile(dest) or not os.path.getsize(dest):
        last = '\n'.join(list(tail)[-3:])
        raise ConversionError(_('ebook-convert failed: {error}').format(
            error=last or process.returncode))
    return dest


def _claim(importing, library_folder, book, suffix):
    """A free 'Author/Title.ext' in the library folder, made as an empty file at once
    (O_EXCL), so a file that turns up meanwhile (another conversion of the book, a file the
    user saves there) is never written over: the next free name is taken instead."""
    for _attempt in range(100):
        dest = importing.library_path(library_folder, book, suffix)
        os.makedirs(os.path.dirname(dest), exist_ok=True)
        try:
            with open(dest, 'xb'):
                return dest
        except FileExistsError:
            continue
    raise FileExistsError(errno.EEXIST, os.strerror(errno.EEXIST), dest)


def _stop(process):
    for sig, wait in ((signal.SIGTERM, STOP_WAIT_S), (signal.SIGKILL, None)):
        with contextlib.suppress(ProcessLookupError, PermissionError):
            os.killpg(process.pid, sig)
        try:
            process.wait(timeout=wait)
            return
        except subprocess.TimeoutExpired:
            continue


def convert_file(src, src_fmt, dest, target, program=None, progress=None, cancelled=None):
    """Convert the file at src (format src_fmt) to dest in `target`."""
    from . import kepub

    if target != 'kepub':
        return run_ebook_convert(src, dest, program, progress, cancelled)
    if src_fmt not in ('epub', 'kepub'):
        epub_copy = dest + '.source.epub'
        try:
            run_ebook_convert(src, epub_copy, program,
                              (lambda fraction, text: progress(fraction * 0.9, text))
                              if progress is not None else None, cancelled)
            src = epub_copy
            return _kepub(kepub, src, dest, progress)
        finally:
            with contextlib.suppress(OSError):
                os.unlink(epub_copy)
    return _kepub(kepub, src, dest, progress)


def _kepub(kepub, src, dest, progress):
    if progress is not None:
        progress(0.95, _('Making a Kobo EPUB'))
    try:
        kepub.convert(src, dest)
    except (OSError, ValueError, zipfile.BadZipFile) as error:
        raise ConversionError(_('The Kobo EPUB could not be made: {error}').format(error=error)) \
            from error
    return dest


def convert_book(library, covers, book_id, target, library_folder, program=None,
                 progress=None, cancelled=None, add=True):
    """Convert a book to `target` and add the result to it as a new format, in the library
    folder ('Author/Title.ext', as an added book's file). The book's own files are only
    read: a copy carrying the library's metadata (exporting.export_copy) is converted.
    Returns the new file's path. With add=False the file is made but not added (a thread on
    a worker library leaves that to the main library, where it is an undo step). Raises
    ConversionError (ConversionCancelled)."""
    from . import exporting, importing

    book = library.book(book_id)
    if book is None:
        raise ConversionError(_('The book is no longer in the library'))
    if target in book.formats:
        raise ConversionError(_('The book already has this format'))
    available = [file.format for file in library.files(book_id) if not file.missing]
    source = source_format(available, target)
    if source is None:
        raise ConversionError(_('The book’s file cannot be found'))
    with tempfile.TemporaryDirectory(prefix='bookcase-convert-') as directory:
        try:
            copy = exporting.export_copy(library, covers, book_id, directory, format=source,
                                         name='source')
        except exporting.ExportError as error:
            raise ConversionError(str(error)) from error
        out = os.path.join(directory, 'converted' + SUFFIXES[target])
        convert_file(copy, source, out, target, program, progress, cancelled)
        if cancelled is not None and cancelled():
            raise ConversionCancelled(_('Conversion cancelled'))
        part = dest = None
        try:
            dest = _claim(importing, library_folder, book, SUFFIXES[target])
            fd, part = tempfile.mkstemp(dir=os.path.dirname(dest), prefix='.bookcase-',
                                        suffix='.part')
            os.close(fd)
            shutil.copyfile(out, part)
            os.replace(part, dest)  # over the empty file claimed for it
        except OSError as error:
            for path in (part, dest):
                if path is not None:
                    with contextlib.suppress(OSError):
                        os.unlink(path)
            raise ConversionError(_('The converted book could not be saved: {error}')
                                  .format(error=error.strerror or error)) from error
    if add:
        library.add_file(book_id, dest, hash=importing.partial_md5(dest),
                         size=os.path.getsize(dest), format=target)
    return dest
