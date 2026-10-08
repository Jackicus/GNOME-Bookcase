# SPDX-License-Identifier: GPL-2.0-or-later
# SPDX-FileCopyrightText: 2026 Jack Tully

"""Metadata and covers from Open Library (no key needed) and Google Books (with the user's
API key, the google-books-key setting).

    candidates = online.search(title='…', authors=['…'], isbn='', google_key='')
                                        # [Candidate], best match first; raises OnlineError
                                        # when every source failed (offline, refused, busy)
    candidate = online.complete(candidate)   # a copy with what search leaves out (Open
                                        # Library's description, subjects and series, from
                                        # the work and the edition)
    data = online.fetch_cover(url)      # the image's bytes; OnlineError when not an image
    task = online.run_async(func, callback, *args, **kwargs)
                                        # func(*args, **kwargs) in a thread; on the main
                                        # loop callback(result, error) (one is None) unless
    task.cancel()                       # cancelled first

With an ISBN, search asks Open Library's books API (one flattened edition) and Google Books
for that ISBN, and falls back on title and author when neither knows it. Otherwise it asks
Open Library's search (works with their best edition) and Google Books for the title and
author. Candidates from two sources with the same ISBN are merged (the first source's
values win, the other fills its gaps), then ranked by how close their title and authors are
to the ones given (an ISBN match first).

Every function takes `fetch=`, a function url -> bytes (the tests pass canned JSON);
the default sends Bookcase's User-Agent with a timeout and spaces Open Library's API
requests about a second apart, as it asks. Descriptions come back as HTML (Open Library's
plain text made into paragraphs); languages as ISO 639-1 codes where known; dates as
'YYYY', 'YYYY-MM' or 'YYYY-MM-DD'.
"""

import dataclasses
import difflib
import html
import json
import logging
import re
import threading
import time
import unicodedata
import urllib.error
import urllib.parse
import urllib.request
from gettext import gettext as _

from gi.repository import GLib

log = logging.getLogger(__name__)

USER_AGENT = 'Bookcase/0.1 (https://github.com/Jackicus/GNOME-Bookcase)'
TIMEOUT = 15  # seconds
MAX_COVER_BYTES = 15 * 1024 * 1024
LIMIT = 10
OPEN_LIBRARY = 'https://openlibrary.org'
OPEN_LIBRARY_COVERS = 'https://covers.openlibrary.org'
GOOGLE_BOOKS = 'https://www.googleapis.com/books/v1/volumes'
OPEN_LIBRARY_INTERVAL = 1.0  # seconds between API requests (covers by id are not limited)

SOURCE_NAMES = {'openlibrary': 'Open Library', 'google': 'Google Books'}

# ISO 639-2 (bibliographic and terminology) to 639-1, for the languages books are most often
# in; others stay as given.
LANGUAGE_CODES = {
    'ara': 'ar', 'bul': 'bg', 'cat': 'ca', 'ces': 'cs', 'cze': 'cs', 'chi': 'zh', 'zho': 'zh',
    'dan': 'da', 'deu': 'de', 'ger': 'de', 'ell': 'el', 'gre': 'el', 'eng': 'en',
    'epo': 'eo', 'est': 'et', 'eus': 'eu', 'baq': 'eu', 'fas': 'fa', 'per': 'fa', 'fin': 'fi',
    'fra': 'fr', 'fre': 'fr', 'gle': 'ga', 'glg': 'gl', 'heb': 'he', 'hin': 'hi', 'hrv': 'hr',
    'hun': 'hu', 'ind': 'id', 'isl': 'is', 'ice': 'is', 'ita': 'it', 'jpn': 'ja',
    'kor': 'ko', 'lat': 'la', 'lit': 'lt', 'lav': 'lv', 'nld': 'nl', 'dut': 'nl', 'nor': 'no',
    'nob': 'nb', 'nno': 'nn', 'pol': 'pl', 'por': 'pt', 'ron': 'ro', 'rum': 'ro', 'rus': 'ru',
    'slk': 'sk', 'slo': 'sk', 'slv': 'sl', 'spa': 'es', 'srp': 'sr', 'swe': 'sv', 'tha': 'th',
    'tur': 'tr', 'ukr': 'uk', 'vie': 'vi', 'cym': 'cy', 'wel': 'cy',
}
MONTHS = {name: number for number, name in enumerate(
    ('jan', 'feb', 'mar', 'apr', 'may', 'jun', 'jul', 'aug', 'sep', 'oct', 'nov', 'dec'), 1)}


@dataclasses.dataclass
class Candidate:
    """One book as a source describes it. Lists are tuples; empty means unknown."""
    source: str  # 'openlibrary' or 'google'
    title: str = ''
    authors: tuple = ()
    series: str = ''
    series_index: float = 0.0
    publisher: str = ''
    published: str = ''
    language: str = ''
    description: str = ''  # HTML
    tags: tuple = ()
    identifiers: dict = dataclasses.field(default_factory=dict)  # {'isbn': …, 'google': …}
    cover_url: str = ''  # the largest image offered
    thumbnail_url: str = ''  # a small one, for a list
    pages: int = 0
    key: str = ''  # the source's own id ('/works/OL…W', a volume id)
    edition_key: str = ''  # Open Library's edition ('/books/OL…M')
    sources: tuple = ()  # every source merged into it
    score: float = 0.0

    @property
    def source_name(self):
        return ' + '.join(SOURCE_NAMES.get(source, source)
                          for source in (self.sources or (self.source,)))


class OnlineError(Exception):
    """A lookup that failed, said in a sentence (translated). `offline` when no server could
    be reached at all."""

    def __init__(self, message, offline=False):
        super().__init__(message)
        self.offline = offline


# -- fetching --------------------------------------------------------------------------------

_throttle_lock = threading.Lock()
_last_open_library = [0.0]


def _throttle(url):
    host = urllib.parse.urlsplit(url).hostname or ''
    if host != 'openlibrary.org':
        return
    with _throttle_lock:
        wait = _last_open_library[0] + OPEN_LIBRARY_INTERVAL - time.monotonic()
        if wait > 0:
            time.sleep(wait)
        _last_open_library[0] = time.monotonic()


def http_get(url, limit=MAX_COVER_BYTES):
    """The body at `url` (redirects followed), at most `limit` bytes; OnlineError otherwise."""
    host = urllib.parse.urlsplit(url).hostname or url
    if urllib.parse.urlsplit(url).scheme.lower() not in ('http', 'https'):
        # A cover address from an answer is never a file on this computer.
        raise OnlineError(_('Not found'))
    _throttle(url)
    request = urllib.request.Request(url, headers={'User-Agent': USER_AGENT,
                                                   'Accept': '*/*'})
    try:
        with urllib.request.urlopen(request, timeout=TIMEOUT) as response:
            data = response.read(limit + 1)
    except urllib.error.HTTPError as error:
        if error.code == 404:
            raise OnlineError(_('Not found')) from error
        if error.code == 429:
            raise OnlineError(_('{host} is busy. Try again in a minute.').format(
                host=host)) from error
        if error.code in (400, 401, 403) and 'googleapis' in host:
            raise OnlineError(_('Google Books refused the request. Check the API key in '
                                'Preferences.')) from error
        raise OnlineError(_('{host} answered with an error ({code}).').format(
            host=host, code=error.code)) from error
    except (urllib.error.URLError, TimeoutError, OSError) as error:
        raise OnlineError(_('Could not reach {host}. Check your internet connection.').format(
            host=host), offline=True) from error
    if len(data) > limit:
        raise OnlineError(_('The answer from {host} is too large.').format(host=host))
    return data


def _json(fetch, url):
    data = fetch(url)
    try:
        return json.loads(data.decode('utf-8'))
    except (UnicodeDecodeError, ValueError) as error:
        raise OnlineError(_('The answer from the server could not be read.')) from error


# -- text helpers ----------------------------------------------------------------------------

def _str(value):
    """A string from a JSON value that should be one (or {'value': …}, Open Library's text)."""
    if isinstance(value, dict):
        value = value.get('value', '')
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        return str(value)
    return value.strip() if isinstance(value, str) else ''


def _list(value):
    if isinstance(value, list):
        return value
    return [value] if value not in (None, '', {}) else []


def _names(values):
    """Names from a list of strings or {'name': …} objects, without blanks or repeats."""
    names = []
    for value in _list(values):
        name = _str(value.get('name')) if isinstance(value, dict) else _str(value)
        if name and name not in names:
            names.append(name)
    return names


def normalize(text):
    """Lower case, no accents, punctuation or leading article, single spaces: for
    comparing titles and names."""
    text = unicodedata.normalize('NFKD', text or '')
    text = ''.join(char for char in text if not unicodedata.combining(char)).lower()
    text = re.sub(r'[^\w\s]', ' ', text)
    text = re.sub(r'^(the|a|an)\s+', '', text.strip())
    return ' '.join(text.split())


def normalize_isbn(text):
    """An ISBN's digits (and a final X), or '' when `text` is not a valid ISBN-10 or -13;
    an ISBN-10 is turned into its ISBN-13."""
    digits = re.sub(r'[^0-9Xx]', '', text or '').upper()
    if len(digits) == 13 and digits.isdigit():
        total = sum(int(d) * (1 if i % 2 == 0 else 3) for i, d in enumerate(digits[:12]))
        return digits if (10 - total % 10) % 10 == int(digits[12]) else ''
    if len(digits) == 10 and digits[:9].isdigit():
        total = sum(int(d) * (10 - i) for i, d in enumerate(digits[:9]))
        check = (11 - total % 11) % 11
        if ('X' if check == 10 else str(check)) != digits[9]:
            return ''
        body = '978' + digits[:9]
        total = sum(int(d) * (1 if i % 2 == 0 else 3) for i, d in enumerate(body))
        return body + str((10 - total % 10) % 10)
    return ''


def _best_isbn(values):
    """The first valid ISBN-13 among `values`, else the first ISBN-10 made one."""
    isbns = [normalize_isbn(_str(value)) for value in _list(values)]
    return next((isbn for isbn in isbns if isbn), '')


def language_code(value):
    """ISO 639-1 from '/languages/eng', 'eng', 'en' or 'en-GB'; '' when unknown."""
    value = _str(value).rsplit('/', 1)[-1].strip().lower()
    if not value:
        return ''
    value = value.split('-')[0].split('_')[0]
    if len(value) == 2:
        return value
    return LANGUAGE_CODES.get(value, value)


def parse_date(text):
    """'YYYY', 'YYYY-MM' or 'YYYY-MM-DD' from what sources write: '2004', '2004-05-01',
    'May 1, 2004', '1 May 2004', 'May 2004', 'c2004'; '' when no year is found."""
    text = _str(text)
    match = re.match(r'^(\d{4})(?:-(\d{1,2})(?:-(\d{1,2}))?)?', text)
    if match:
        year, month, day = match.groups()
        if month and 1 <= int(month) <= 12:
            if day and 1 <= int(day) <= 31:
                return f'{year}-{int(month):02d}-{int(day):02d}'
            return f'{year}-{int(month):02d}'
        return year
    year = re.search(r'(?<!\d)(1[5-9]\d\d|20\d\d)(?!\d)', text)
    if not year:
        return ''
    month = next((number for name, number in MONTHS.items()
                  if re.search(rf'\b{name}', text, re.IGNORECASE)), None)
    if month is None:
        return year.group(1)
    day = re.search(r'\b(\d{1,2})(?:st|nd|rd|th)?\b', text.replace(year.group(1), ''))
    if day and 1 <= int(day.group(1)) <= 31:
        return f'{year.group(1)}-{month:02d}-{int(day.group(1)):02d}'
    return f'{year.group(1)}-{month:02d}'


def plain_to_html(text):
    """Paragraphs (separated by blank lines) as <p>…</p>, line breaks inside them as <br>."""
    paragraphs = [part.strip() for part in re.split(r'\n\s*\n', (text or '').strip())]
    return ''.join('<p>' + html.escape(part).replace('\n', '<br>') + '</p>'
                   for part in paragraphs if part)


def html_to_plain(markup):
    """Text from HTML: paragraphs and headings separated by blank lines, <br> as a line
    break, list items on lines of their own, other tags dropped, entities decoded. Text
    without tags comes back as it is."""
    markup = markup or ''
    if not re.search(r'<[a-zA-Z/!]', markup):
        return html.unescape(markup).strip()
    markup = re.sub(r'(?is)<(script|style)\b.*?</\1\s*>', '', markup)
    markup = re.sub(r'(?i)<br\s*/?>', '\n', markup)
    markup = re.sub(r'(?i)<li\b[^>]*>', '\n• ', markup)
    markup = re.sub(r'(?i)</(p|div|h[1-6]|blockquote|ul|ol|tr|table)\s*>', '\n\n', markup)
    markup = re.sub(r'(?i)<(p|div|h[1-6]|blockquote|ul|ol|table)\b[^>]*>', '\n\n', markup)
    markup = re.sub(r'<[^>]*>', '', markup)
    text = html.unescape(markup).replace('\xa0', ' ')
    lines = [' '.join(line.split()) for line in text.split('\n')]
    text = '\n'.join(lines)
    return re.sub(r'\n{3,}', '\n\n', text).strip()


def _description_html(text):
    """Open Library's description (plain text, sometimes with Markdown links and a
    '----------' line before notes about the edition) as HTML."""
    text = _str(text).replace('\r\n', '\n')
    text = re.split(r'\n-{4,}\s*\n', text)[0]
    text = re.sub(r'\[([^\]]+)\]\([^)]*\)', r'\1', text)  # Markdown links: their text
    if re.search(r'<(p|br|b|i|em)\b', text, re.IGNORECASE):
        return text.strip()
    return plain_to_html(text)


def _series(value):
    """(name, number) from Open Library's 'Name ; 3', 'Name, #3', 'Name (3)' or 'Name'."""
    text = _str(value[0] if isinstance(value, list) and value else value)
    match = re.match(r'^(.*?)[\s,;:(]*(?:no\.?|#|vol\.?|book)?\s*(\d+(?:\.\d+)?)\)?\s*$',
                     text, re.IGNORECASE)
    if match and match.group(1).strip():
        return match.group(1).strip(' ,;:'), float(match.group(2))
    return text, 0.0


# -- Open Library ----------------------------------------------------------------------------

SEARCH_FIELDS = ('key,title,subtitle,author_name,first_publish_year,publish_date,isbn,cover_i,'
                 'edition_key,cover_edition_key,publisher,language,subject,'
                 'number_of_pages_median,editions,editions.key,editions.title,'
                 'editions.subtitle,editions.publisher,editions.publish_date,editions.isbn,'
                 'editions.language,editions.cover_i,editions.number_of_pages_median')


def _cover_urls(cover_id):
    if not cover_id:
        return '', ''
    base = f'{OPEN_LIBRARY_COVERS}/b/id/{cover_id}'
    return f'{base}-L.jpg?default=false', f'{base}-M.jpg?default=false'


def _title(data):
    title, subtitle = _str(data.get('title')), _str(data.get('subtitle'))
    return f'{title}: {subtitle}' if title and subtitle else title


def parse_open_library_books(data):
    """Candidates from the books API (bibkeys=ISBN:…&jscmd=data): {'ISBN:…': edition}."""
    candidates = []
    if not isinstance(data, dict):
        return candidates
    for bibkey, book in data.items():
        if not isinstance(book, dict):
            continue
        identifiers = book.get('identifiers') if isinstance(book.get('identifiers'),
                                                            dict) else {}
        isbn = (_best_isbn(identifiers.get('isbn_13')) or _best_isbn(identifiers.get('isbn_10'))
                or normalize_isbn(bibkey.partition(':')[2]))
        olid = next(iter(_names(identifiers.get('openlibrary'))), '')
        cover = book.get('cover') if isinstance(book.get('cover'), dict) else {}
        found = Candidate(
            source='openlibrary',
            title=_title(book),
            authors=tuple(_names(book.get('authors'))),
            publisher=next(iter(_names(book.get('publishers'))), ''),
            published=parse_date(book.get('publish_date')),
            tags=tuple(_names(book.get('subjects'))[:10]),
            identifiers={key: value for key, value in (('isbn', isbn),
                                                       ('openlibrary', olid)) if value},
            cover_url=_str(cover.get('large')) or _str(cover.get('medium')),
            thumbnail_url=_str(cover.get('medium')) or _str(cover.get('small')),
            pages=_int(book.get('number_of_pages')),
            key=_str(book.get('key')),
            edition_key=_str(book.get('key')) if '/books/' in _str(book.get('key')) else (
                f'/books/{olid}' if olid else ''),
        )
        if found.title:
            candidates.append(found)
    return candidates


def parse_open_library_search(data):
    """Candidates from search.json: each work with its best-matching edition when the
    answer carries editions."""
    candidates = []
    docs = data.get('docs') if isinstance(data, dict) else None
    for doc in docs if isinstance(docs, list) else []:
        if not isinstance(doc, dict):
            continue
        editions = doc.get('editions') if isinstance(doc.get('editions'), dict) else {}
        edition_docs = editions.get('docs') if isinstance(editions.get('docs'), list) else []
        edition = edition_docs[0] if edition_docs and isinstance(edition_docs[0], dict) else {}

        def pick(field, edition=edition, doc=doc):
            value = edition.get(field)
            return value if value not in (None, '', []) else doc.get(field)

        cover_url, thumbnail_url = _cover_urls(_int(pick('cover_i')))
        isbn = _best_isbn(pick('isbn'))
        edition_key = _str(edition.get('key'))
        if not edition_key and doc.get('cover_edition_key'):
            edition_key = '/books/' + _str(doc.get('cover_edition_key'))
        identifiers = {'isbn': isbn} if isbn else {}
        if edition_key:
            identifiers['openlibrary'] = edition_key.rsplit('/', 1)[-1]
        languages = [language_code(code) for code in _list(pick('language'))]
        published = (parse_date(next(iter(_list(edition.get('publish_date'))), ''))
                     or _str(doc.get('first_publish_year')))
        found = Candidate(
            source='openlibrary',
            title=_title(edition) if edition.get('title') else _title(doc),
            authors=tuple(_names(doc.get('author_name'))),
            publisher=next(iter(_names(pick('publisher'))), ''),
            published=published,
            language=languages[0] if len(set(languages)) == 1 else (
                'en' if 'en' in languages else (languages[0] if languages else '')),
            tags=tuple(_names(doc.get('subject'))[:10]),
            identifiers=identifiers,
            cover_url=cover_url,
            thumbnail_url=thumbnail_url,
            pages=_int(pick('number_of_pages_median')),
            key=_str(doc.get('key')),
            edition_key=edition_key,
        )
        if found.title:
            candidates.append(found)
    return candidates


def _int(value):
    try:
        return int(value)
    except (TypeError, ValueError):
        return 0


def _float(value):
    try:
        return float(_str(value) or 0)
    except ValueError:
        return 0.0


def _open_library_isbn(isbn, fetch):
    url = (f'{OPEN_LIBRARY}/api/books?' + urllib.parse.urlencode(
        {'bibkeys': f'ISBN:{isbn}', 'format': 'json', 'jscmd': 'data'}))
    return parse_open_library_books(_json(fetch, url))


def _open_library_search(title, authors, fetch):
    query = {'fields': SEARCH_FIELDS, 'limit': str(LIMIT)}
    if title:
        query['title'] = title
    if authors:
        query['author'] = authors[0]
    url = f'{OPEN_LIBRARY}/search.json?' + urllib.parse.urlencode(query)
    return parse_open_library_search(_json(fetch, url))


def complete(candidate, fetch=None):
    """A copy of an Open Library candidate with the description, subjects, series and
    language its edition and work give (a Google Books candidate is returned as it is).
    Failures leave the fields as they were."""
    fetch = fetch or http_get
    if candidate.source != 'openlibrary':
        return candidate
    found = dataclasses.replace(candidate, identifiers=dict(candidate.identifiers))
    work_key = candidate.key if candidate.key.startswith('/works/') else ''
    if candidate.edition_key:
        try:
            edition = _json(fetch, f'{OPEN_LIBRARY}{candidate.edition_key}.json')
        except OnlineError as error:
            log.info('edition %s: %s', candidate.edition_key, error)
            edition = {}
        if isinstance(edition, dict):
            if edition.get('series') and not found.series:
                found.series, found.series_index = _series(edition.get('series'))
            languages = edition.get('languages')
            if languages and not found.language:
                first = languages[0] if isinstance(languages, list) else languages
                found.language = language_code(first.get('key') if isinstance(first, dict)
                                               else first)
            if edition.get('description') and not found.description:
                found.description = _description_html(edition.get('description'))
            if not found.published and edition.get('publish_date'):
                found.published = parse_date(edition.get('publish_date'))
            works = edition.get('works')
            if not work_key and isinstance(works, list) and works and isinstance(works[0], dict):
                work_key = _str(works[0].get('key'))
    if work_key and not found.description:
        try:
            work = _json(fetch, f'{OPEN_LIBRARY}{work_key}.json')
        except OnlineError as error:
            log.info('work %s: %s', work_key, error)
            work = {}
        if isinstance(work, dict):
            if work.get('description'):
                found.description = _description_html(work.get('description'))
            if not found.tags and work.get('subjects'):
                found.tags = tuple(_names(work.get('subjects'))[:10])
    return found


# -- Google Books ----------------------------------------------------------------------------

def _google_image(url, zoom=None):
    url = _str(url).replace('http://', 'https://').replace('&edge=curl', '')
    if zoom is not None:
        url = re.sub(r'zoom=\d', f'zoom={zoom}', url)
    return url


def parse_google(data):
    """Candidates from the volumes API."""
    candidates = []
    items = data.get('items') if isinstance(data, dict) else None
    for item in items if isinstance(items, list) else []:
        info = item.get('volumeInfo') if isinstance(item, dict) else None
        if not isinstance(info, dict):
            continue
        isbns = {}
        for entry in _list(info.get('industryIdentifiers')):
            if isinstance(entry, dict):
                isbns[_str(entry.get('type'))] = _str(entry.get('identifier'))
        isbn = normalize_isbn(isbns.get('ISBN_13', '')) or normalize_isbn(isbns.get('ISBN_10',
                                                                                    ''))
        identifiers = {'isbn': isbn} if isbn else {}
        volume_id = _str(item.get('id'))
        if volume_id:
            identifiers['google'] = volume_id
        images = info.get('imageLinks') if isinstance(info.get('imageLinks'), dict) else {}
        large = next((images[size] for size in ('extraLarge', 'large', 'medium')
                      if images.get(size)), '')
        thumbnail = images.get('thumbnail') or images.get('smallThumbnail') or ''
        series_info = info.get('seriesInfo') if isinstance(info.get('seriesInfo'), dict) else {}
        description = _str(info.get('description'))
        if description and not re.search(r'<(p|br|b|i|em)\b', description, re.IGNORECASE):
            description = plain_to_html(description)
        found = Candidate(
            source='google',
            title=_title(info),
            authors=tuple(_names(info.get('authors'))),
            series_index=_float(series_info.get('bookDisplayNumber')),
            publisher=_str(info.get('publisher')),
            published=parse_date(info.get('publishedDate')),
            language=language_code(info.get('language')),
            description=description,
            tags=tuple(_names(info.get('categories'))),
            identifiers=identifiers,
            cover_url=_google_image(large) if large else _google_image(thumbnail, zoom=0),
            thumbnail_url=_google_image(thumbnail),
            pages=_int(info.get('pageCount')),
            key=volume_id,
        )
        if found.title:
            candidates.append(found)
    return candidates


def _google(title, authors, isbn, key, fetch):
    if isbn:
        query = f'isbn:{isbn}'
    else:
        parts = [f'intitle:{title}'] if title else []
        if authors:
            parts.append(f'inauthor:{authors[0]}')
        query = ' '.join(parts)
    params = {'q': query, 'maxResults': str(LIMIT), 'printType': 'books', 'key': key}
    return parse_google(_json(fetch, f'{GOOGLE_BOOKS}?' + urllib.parse.urlencode(params)))


# -- merging and ranking ---------------------------------------------------------------------

def similarity(a, b):
    """0-1: how alike two titles or names are, once normalized."""
    a, b = normalize(a), normalize(b)
    if not a or not b:
        return 0.0
    if a == b:
        return 1.0
    return difflib.SequenceMatcher(None, a, b).ratio()


def score(candidate, title='', authors=(), isbn=''):
    """How well a candidate matches: title 60%, first author 40%; 2 more for the ISBN,
    a little for having a cover and a description."""
    value = 0.0
    if title:
        main = candidate.title.split(':')[0]
        value += 0.6 * max(similarity(title, candidate.title), similarity(title, main))
    if authors:
        value += 0.4 * max((similarity(authors[0], name) for name in candidate.authors),
                           default=0.0)
    if isbn and candidate.identifiers.get('isbn') == isbn:
        value += 2.0
    if candidate.cover_url:
        value += 0.05
    if candidate.description:
        value += 0.02
    return value


def merge(candidates):
    """Candidates with the same ISBN made one: the first keeps its values, the others fill
    its empty fields and add their identifiers."""
    merged = []
    by_isbn = {}
    for candidate in candidates:
        isbn = candidate.identifiers.get('isbn')
        first = by_isbn.get(isbn) if isbn else None
        if first is None or candidate.source in first.sources:
            candidate = dataclasses.replace(candidate, identifiers=dict(candidate.identifiers),
                                            sources=(candidate.source,))
            merged.append(candidate)
            if isbn and isbn not in by_isbn:
                by_isbn[isbn] = candidate
            continue
        for field in dataclasses.fields(Candidate):
            if field.name in ('source', 'sources', 'identifiers', 'score', 'key',
                              'edition_key'):
                continue
            if not getattr(first, field.name) and getattr(candidate, field.name):
                setattr(first, field.name, getattr(candidate, field.name))
        for key, value in candidate.identifiers.items():
            first.identifiers.setdefault(key, value)
        first.sources = first.sources + (candidate.source,)
    return merged


def rank(candidates, title='', authors=(), isbn=''):
    for candidate in candidates:
        candidate.score = score(candidate, title, authors, isbn)
    return sorted(candidates, key=lambda candidate: -candidate.score)


# -- searching -------------------------------------------------------------------------------

def search(title='', authors=(), isbn='', google_key='', fetch=None):
    """Candidates for a book, best first (see the module). OnlineError when every source
    failed; ValueError when there is nothing to search for."""
    fetch = fetch or http_get
    authors = [name for name in (authors or ()) if name.strip()]
    title = (title or '').strip()
    isbn = normalize_isbn(isbn) if isbn else ''
    if not (title or authors or isbn):
        raise ValueError('nothing to search for')
    found, errors = [], []

    def ask(function, *args):
        try:
            found.extend(function(*args))
            return True
        except OnlineError as error:
            log.info('%s: %s', function.__name__, error)
            errors.append(error)
            return False

    if isbn:
        ask(_open_library_isbn, isbn, fetch)
        if google_key:
            ask(_google, '', (), isbn, google_key, fetch)
    if not found and (title or authors):
        ask(_open_library_search, title, authors, fetch)
        if google_key:
            ask(_google, title, authors, '', google_key, fetch)
    if not found and errors:
        offline = [error for error in errors if error.offline]
        raise offline[0] if len(offline) == len(errors) else next(
            (error for error in errors if not error.offline), errors[0])
    return rank(merge(found), title, authors, isbn)


IMAGE_MAGIC = (b'\xff\xd8\xff', b'\x89PNG\r\n\x1a\n', b'GIF87a', b'GIF89a')


def fetch_cover(url, fetch=None):
    """A cover's bytes (JPEG, PNG, GIF or WebP); OnlineError when the answer is no image or
    a placeholder (Open Library's 1×1 GIF)."""
    fetch = fetch or http_get
    data = fetch(url)
    is_webp = data[:4] == b'RIFF' and data[8:12] == b'WEBP'
    if not (data.startswith(IMAGE_MAGIC) or is_webp) or len(data) < 200:
        raise OnlineError(_('No cover found'))
    return data


# -- running in a thread ---------------------------------------------------------------------

class Task:
    """A function running in a thread; see run_async."""

    def __init__(self):
        self._cancelled = threading.Event()

    def cancel(self):
        self._cancelled.set()

    @property
    def cancelled(self):
        return self._cancelled.is_set()


def run_async(func, callback, *args, **kwargs):
    """Run func(*args, **kwargs) in a daemon thread and call callback(result, error) on the
    main loop (error an exception, or None), unless the returned Task was cancelled first.
    An OnlineError or ValueError is passed as it is; anything else is logged and passed."""
    task = Task()

    def deliver(result, error):
        if not task.cancelled:
            callback(result, error)
        return GLib.SOURCE_REMOVE

    def run():
        try:
            result = func(*args, **kwargs)
        except (OnlineError, ValueError) as error:
            GLib.idle_add(deliver, None, error)
        except Exception as error:  # a bug: the callback still hears of it
            log.exception('online lookup')
            GLib.idle_add(deliver, None, error)
        else:
            GLib.idle_add(deliver, result, None)

    threading.Thread(target=run, name='bookcase-online', daemon=True).start()
    return task
