# SPDX-License-Identifier: GPL-2.0-or-later
# SPDX-FileCopyrightText: 2026 Jack Tully

"""What a book file says about itself: metadata and cover, per format.

    info = formats.read(path)          # BookInfo; raises FormatError for a broken file
    formats.format_of(path)            # 'epub', 'mobi', 'azw3', 'pdf', 'cbz', 'cbr', 'fb2',
                                       # 'fbz', 'txt', 'kepub', or None when not a book
    formats.SUFFIXES                   # the file suffixes Bookcase takes, for file choosers
    formats.parse_filename(path)       # (title, [authors]) from a file name
    formats.image_type(data)           # 'jpeg', 'png', 'gif', 'webp', 'avif' or None

Each module (epub, mobi, pdf, comic, fb2, txt) has read(path) -> BookInfo; epub also has
write(path, info, cover=None, dest=None), which writes a copy (exporting.py uses it; the
user's file is never rewritten). A reader trusts what the file says and falls back on the
file name ("Author - Title.epub", "Title (Author).pdf") for what it does not say.

read() never lets anything but FormatError out for a file it cannot make sense of (and
OSError for one it cannot open), so a folder of junk can be scanned safely. A '.kepub.epub'
is an EPUB of format 'kepub'; '.azw' and '.prc' are MOBI; '.fb2.zip' is FBZ.
"""

import dataclasses
import html
import importlib
import logging
import os
import re

log = logging.getLogger(__name__)

# Longest first: format_of() takes the first suffix the name ends with.
_SUFFIX_FORMATS = (
    ('.kepub.epub', 'kepub'),
    ('.fb2.zip', 'fbz'),
    ('.epub', 'epub'),
    ('.azw3', 'azw3'),
    ('.azw', 'mobi'),
    ('.mobi', 'mobi'),
    ('.prc', 'mobi'),
    ('.pdf', 'pdf'),
    ('.cbz', 'cbz'),
    ('.cbr', 'cbr'),
    ('.fb2', 'fb2'),
    ('.fbz', 'fbz'),
    ('.txt', 'txt'),
)
SUFFIXES = tuple(suffix for suffix, _format in _SUFFIX_FORMATS)

_MODULES = {
    'epub': 'epub', 'kepub': 'epub', 'mobi': 'mobi', 'azw3': 'mobi', 'pdf': 'pdf',
    'cbz': 'comic', 'cbr': 'comic', 'fb2': 'fb2', 'fbz': 'fb2', 'txt': 'txt',
}


@dataclasses.dataclass
class BookInfo:
    title: str = ''
    authors: list = dataclasses.field(default_factory=list)
    series: str = ''
    series_index: float = 0.0
    tags: list = dataclasses.field(default_factory=list)
    publisher: str = ''
    published: str = ''  # 'YYYY', 'YYYY-MM' or 'YYYY-MM-DD'
    language: str = ''  # ISO 639-1 where known ('en'), else as given
    description: str = ''  # HTML
    identifiers: dict = dataclasses.field(default_factory=dict)  # {'isbn': '978…', 'uuid': …}
    cover: bytes | None = None  # the image's bytes (JPEG, PNG, …)
    format: str = ''


class FormatError(Exception):
    """A file that cannot be read as the book it claims to be."""


def format_of(path):
    """The format of a book file by its name, or None when Bookcase does not take it."""
    name = os.path.basename(str(path)).lower()
    for suffix, format in _SUFFIX_FORMATS:
        if name.endswith(suffix) and len(name) > len(suffix):
            return format
    return None


def suffix_of(path):
    """The book suffix a file name ends with ('.kepub.epub', '.pdf'), or its last suffix."""
    name = os.path.basename(str(path)).lower()
    for suffix, _format in _SUFFIX_FORMATS:
        if name.endswith(suffix) and len(name) > len(suffix):
            return suffix
    return os.path.splitext(name)[1]


def read(path):
    """The BookInfo of a book file; FormatError when it is not one Bookcase can read."""
    path = str(path)
    format = format_of(path)
    if format is None:
        raise FormatError(f'Not a book file: {os.path.basename(path)}')
    module = importlib.import_module(f'{__name__}.{_MODULES[format]}')
    try:
        info = module.read(path)
    except (FormatError, OSError):
        raise
    except Exception as error:  # a parser meeting junk: never more than a FormatError
        log.debug('Cannot read %s', path, exc_info=True)
        raise FormatError(f'Cannot read {os.path.basename(path)}: {error}') from error
    info.format = format
    complete(info, path)
    return info


def complete(info, path):
    """Fill what the file did not say from its name, and tidy the fields."""
    title, authors = parse_filename(path)
    info.title = _clean(info.title) or title
    info.authors = _unique([_clean(a) for a in info.authors if _clean(a)]) or authors
    info.tags = _unique([_clean(t) for t in info.tags if _clean(t)])
    info.series = _clean(info.series)
    info.publisher = _clean(info.publisher)
    info.published = normalize_date(info.published)
    info.language = normalize_language(info.language)
    info.description = text_to_html(info.description)
    info.identifiers = {k: str(v).strip() for k, v in info.identifiers.items()
                        if k and v and str(v).strip()}
    return info


def _clean(text):
    return re.sub(r'\s+', ' ', text or '').strip()


def _unique(items):
    seen = set()
    result = []
    for item in items:
        if item.casefold() not in seen:
            seen.add(item.casefold())
            result.append(item)
    return result


def stem_of(path):
    """A file name without its book suffix."""
    name = os.path.basename(str(path))
    suffix = suffix_of(name)
    return name[:len(name) - len(suffix)] if suffix and name.lower().endswith(suffix) else name


def split_authors(text):
    """'Ada Lark & Ben Ross', 'Ada Lark; Ben Ross', 'Ada Lark and Ben Ross' -> names."""
    parts = re.split(r'\s*(?:;|&|\band\b)\s*', text or '')
    return [p.strip() for p in parts if p.strip()]


def parse_filename(path):
    """(title, [authors]) from a file name: 'Author - Title', 'Title (Author)', 'Title';
    underscores are spaces. 'Title - Author' cannot be told from 'Author - Title': the
    first part is taken as the author, as Calibre and most libraries name files."""
    stem = stem_of(path).replace('_', ' ')
    stem = _clean(stem)
    match = re.fullmatch(r'(.+?)\s*\(([^()\d]*[^\W\d][^()\d]*)\)', stem)
    if match:
        return match.group(1).strip(), split_authors(match.group(2))
    if ' - ' in stem:
        left, right = stem.split(' - ', 1)
        if left.strip() and right.strip() and not re.search(r'\d', left):
            return right.strip(), split_authors(left)
    return stem, []


def normalize_date(text):
    """'2019-04-02T00:00:00+00:00' -> '2019-04-02'; 'April 2019' -> '2019'; unknown -> ''."""
    text = (text or '').strip()
    match = re.match(r'(\d{4})(?:-(\d{1,2})(?:-(\d{1,2}))?)?', text)
    if not match:
        match = re.search(r'\b(\d{4})\b', text)
        return match.group(1) if match and match.group(1) > '0101' else ''
    year, month, day = match.groups()
    if year <= '0101':  # Calibre's 'no date'
        return ''
    if month and 1 <= int(month) <= 12:
        if day and 1 <= int(day) <= 31:
            return f'{year}-{int(month):02d}-{int(day):02d}'
        return f'{year}-{int(month):02d}'
    return year


_LANGUAGES = {
    'eng': 'en', 'fre': 'fr', 'fra': 'fr', 'ger': 'de', 'deu': 'de', 'spa': 'es', 'ita': 'it',
    'por': 'pt', 'rus': 'ru', 'jpn': 'ja', 'chi': 'zh', 'zho': 'zh', 'dut': 'nl', 'nld': 'nl',
    'swe': 'sv', 'nor': 'no', 'nob': 'nb', 'dan': 'da', 'fin': 'fi', 'pol': 'pl', 'cze': 'cs',
    'ces': 'cs', 'gre': 'el', 'ell': 'el', 'tur': 'tr', 'ara': 'ar', 'heb': 'he', 'hin': 'hi',
    'kor': 'ko', 'ukr': 'uk', 'hun': 'hu', 'rum': 'ro', 'ron': 'ro', 'cat': 'ca', 'lat': 'la',
    'gle': 'ga', 'wel': 'cy', 'cym': 'cy', 'ice': 'is', 'isl': 'is', 'vie': 'vi', 'tha': 'th',
    'ind': 'id', 'per': 'fa', 'fas': 'fa', 'bul': 'bg', 'hrv': 'hr', 'srp': 'sr', 'slo': 'sk',
    'slk': 'sk', 'slv': 'sl', 'est': 'et', 'lav': 'lv', 'lit': 'lt', 'epo': 'eo',
    'english': 'en', 'french': 'fr', 'german': 'de', 'spanish': 'es', 'italian': 'it',
    'portuguese': 'pt', 'russian': 'ru', 'japanese': 'ja', 'chinese': 'zh', 'dutch': 'nl',
}


def normalize_language(text):
    """'en-GB', 'eng', 'English' -> 'en'; an unknown code stays as given (lowercased);
    'und' and 'zxx' (undetermined, no language) -> ''."""
    text = (text or '').strip().lower().replace('_', '-')
    if not text:
        return ''
    primary = text.split('-')[0]
    if primary in ('und', 'zxx', 'mul'):
        return ''
    if len(primary) == 2:
        return primary
    return _LANGUAGES.get(primary, primary)


_TAG = re.compile(r'</?[a-zA-Z][a-zA-Z0-9]*(\s[^<>]*)?/?>')


def text_to_html(text):
    """A description as HTML: HTML stays as it is, plain text is escaped into paragraphs."""
    text = (text or '').strip()
    if not text or _TAG.search(text):
        return text
    paragraphs = [p.strip() for p in re.split(r'\n\s*\n', text) if p.strip()]
    return ''.join('<p>{}</p>'.format(html.escape(p).replace('\n', '<br>'))
                   for p in paragraphs)


def image_type(data):
    """The image format from its magic bytes: 'jpeg', 'png', 'gif', 'webp', 'avif', 'bmp' or
    None."""
    if not data:
        return None
    if data[:3] == b'\xff\xd8\xff':
        return 'jpeg'
    if data[:8] == b'\x89PNG\r\n\x1a\n':
        return 'png'
    if data[:6] in (b'GIF87a', b'GIF89a'):
        return 'gif'
    if data[:4] == b'RIFF' and data[8:12] == b'WEBP':
        return 'webp'
    if data[4:12] in (b'ftypavif', b'ftypavis'):
        return 'avif'
    if data[:2] == b'BM':
        return 'bmp'
    return None


IMAGE_SUFFIXES = {'jpeg': '.jpg', 'png': '.png', 'gif': '.gif', 'webp': '.webp',
                  'avif': '.avif', 'bmp': '.bmp'}
IMAGE_MEDIA_TYPES = {'jpeg': 'image/jpeg', 'png': 'image/png', 'gif': 'image/gif',
                     'webp': 'image/webp', 'avif': 'image/avif', 'bmp': 'image/bmp'}


def isbn_valid(text):
    """Whether a string of digits (and a final X) is an ISBN-10 or ISBN-13 by its checksum."""
    digits = re.sub(r'[\s-]', '', text or '').upper()
    if re.fullmatch(r'\d{13}', digits):
        total = sum(int(d) * (1 if i % 2 == 0 else 3) for i, d in enumerate(digits))
        return total % 10 == 0
    if re.fullmatch(r'\d{9}[\dX]', digits):
        total = sum((10 - i) * (10 if d == 'X' else int(d)) for i, d in enumerate(digits))
        return total % 11 == 0
    return False


def clean_isbn(text):
    """An ISBN without 'urn:isbn:', spaces and hyphens."""
    text = (text or '').strip()
    text = re.sub(r'^(urn:)?isbn:?', '', text, flags=re.I)
    return re.sub(r'[\s-]', '', text).upper()
