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
"""

import hashlib
import html
import logging
import os
import re
import zipfile

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

    part = dest + '.part'
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
