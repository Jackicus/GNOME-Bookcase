# SPDX-License-Identifier: GPL-2.0-or-later
# SPDX-FileCopyrightText: 2026 Jack Tully

"""Looking up a word or a phrase from the reader: a definition (offline from the StarDict
dictionaries installed, else Wiktionary) and an encyclopedia summary (Wikipedia).

    service = lookup.service()          # the app's one Lookup (its cache is shared)
    task = service.define(word, language, callback)   # callback(Article, error) on the
                                        # main loop; error an online.OnlineError
    task = service.summarize(text, language, callback)  # callback(Summary, error)
    task.cancel()                       # the callback is not called
    service.remember(kind, text, language, value)       # seeds the cache (the demo)

    lookup.is_word(text)                # one word (what the dictionary is asked for)
    lookup.clean_word(text)             # the word without the punctuation around it
    lookup.parse_definitions(data, word, language)      # Wiktionary's JSON -> Article
    lookup.parse_summary(data)          # Wikipedia's JSON -> Summary
    lookup.StarDict(ifo_path).lookup(word)              # [text] from one dictionary
    lookup.find_dictionaries(dirs=None) # the StarDict dictionaries installed

Wiktionary's REST definition endpoint exists only on the English edition, which describes
words of every language: the entries in the book's language come first. Wikipedia's summary
is asked of the book's language's edition; a phrase that is no article's title is searched
for, and the first title found summarized. Requests send Bookcase's User-Agent, time out
after a few seconds, and are cached in memory (the last CACHE_SIZE answers). Every text that
comes back is plain (the HTML stripped), never markup.

StarDict dictionaries (an .ifo, an .idx or .idx.gz, a .dict or dictzip'd .dict.dz, and an
optional .syn) are read in pure Python: the index into memory on first use, the definition
by seeking (a dictzip's chunks decompressed one by one; a plain gzip read whole).
"""

import collections
import dataclasses
import gzip
import json
import logging
import os
import struct
import threading
import unicodedata
import urllib.error
import urllib.parse
import urllib.request
import zlib
from gettext import gettext as _

from gi.repository import GLib

from .online import OnlineError, html_to_plain, language_code, run_async

log = logging.getLogger(__name__)

USER_AGENT = 'Bookcase/0.2 (https://github.com/Jackicus/GNOME-Bookcase)'
TIMEOUT = 8  # seconds
MAX_BYTES = 2 * 1024 * 1024
CACHE_SIZE = 64
MAX_ENTRIES = 8
MAX_SENSES = 6
MAX_WORD = 60
WIKTIONARY_EDITIONS = {'en'}  # the editions with the REST definition endpoint
STARDICT_DIRS = (os.path.expanduser('~/.local/share/stardict/dic'), '/usr/share/stardict/dic')
PUNCTUATION = '.,;:!?«»“”‘’"\'()[]{}<>—–-…*_/\\|'


@dataclasses.dataclass
class Sense:
    text: str
    examples: tuple = ()


@dataclasses.dataclass
class Entry:
    """A part of speech in a language (Wiktionary), or one dictionary's article."""
    heading: str  # 'Noun', or the dictionary's name
    language: str = ''  # 'English' (Wiktionary's name for it)
    senses: tuple = ()


@dataclasses.dataclass
class Article:
    word: str
    source: str  # 'wiktionary', or the StarDict dictionary's name
    entries: tuple = ()
    url: str = ''  # the page to open in the browser ('' for an offline dictionary)


@dataclasses.dataclass
class Summary:
    title: str
    description: str = ''
    extract: str = ''
    url: str = ''


# -- words -----------------------------------------------------------------------------------

def clean_word(text):
    """The text without whitespace runs and the punctuation around it."""
    return ' '.join((text or '').split()).strip(PUNCTUATION)


def is_word(text):
    """Whether the text is one word (a hyphenated or apostrophized one counts)."""
    word = clean_word(text)
    return bool(word) and len(word) <= MAX_WORD and not any(c.isspace() for c in word) \
        and any(c.isalpha() for c in word)


def _language(language):
    return language_code(language or '') or 'en'


# -- fetching --------------------------------------------------------------------------------

def http_json(url):
    """The JSON at `url`; OnlineError (offline=True when the server could not be reached),
    with `not_found` set on a 404."""
    host = urllib.parse.urlsplit(url).hostname or url
    request = urllib.request.Request(url, headers={'User-Agent': USER_AGENT,
                                                   'Accept': 'application/json'})
    try:
        with urllib.request.urlopen(request, timeout=TIMEOUT) as response:
            data = response.read(MAX_BYTES + 1)
    except urllib.error.HTTPError as error:
        failure = OnlineError(_('Nothing found') if error.code == 404 else
                              _('{host} answered with an error ({code}).').format(
                                  host=host, code=error.code))
        failure.not_found = error.code == 404
        raise failure from error
    except (urllib.error.URLError, TimeoutError, OSError) as error:
        raise OnlineError(_('Could not reach {host}. Check your internet connection.').format(
            host=host), offline=True) from error
    if len(data) > MAX_BYTES:
        raise OnlineError(_('The answer from {host} is too large.').format(host=host))
    try:
        return json.loads(data.decode('utf-8'))
    except (UnicodeDecodeError, ValueError) as error:
        raise OnlineError(_('The answer from the server could not be read.')) from error


def not_found(error):
    return bool(getattr(error, 'not_found', False))


def wiktionary_page(word, language='en'):
    edition = _language(language)
    return f'https://{edition}.wiktionary.org/wiki/{urllib.parse.quote(word)}'


def definition_url(word, language='en'):
    edition = _language(language)
    edition = edition if edition in WIKTIONARY_EDITIONS else 'en'
    return (f'https://{edition}.wiktionary.org/api/rest_v1/page/definition/'
            f'{urllib.parse.quote(word, safe="")}')


def summary_url(title, language='en'):
    title = title.replace(' ', '_')
    return (f'https://{_language(language)}.wikipedia.org/api/rest_v1/page/summary/'
            f'{urllib.parse.quote(title, safe="")}')


def wikipedia_search_url(text, language='en'):
    return (f'https://{_language(language)}.wikipedia.org/w/rest.php/v1/search/title?'
            + urllib.parse.urlencode({'q': text, 'limit': 1}))


def wikipedia_page(text, language='en'):
    return (f'https://{_language(language)}.wikipedia.org/w/index.php?'
            + urllib.parse.urlencode({'search': text}))


# -- parsing ---------------------------------------------------------------------------------

def strip_html(markup):
    """Plain text from a fragment of HTML, on one line: tags (and style and script
    elements) dropped, entities decoded, whitespace collapsed."""
    text = html_to_plain(markup or '')
    return ' '.join(text.split())


def parse_definitions(data, word, language='en'):
    """An Article from Wiktionary's definition JSON ({language code: [{partOfSpeech,
    language, definitions: [{definition, examples, parsedExamples}]}]}): the entries in the
    book's language first, then English, then the rest; empty senses dropped."""
    language = _language(language)
    order = [language] + (['en'] if language != 'en' else [])
    codes = [code for code in order if code in (data or {})]
    codes += [code for code in (data or {}) if code not in codes]
    entries = []
    for code in codes:
        items = data.get(code)
        if not isinstance(items, list):
            continue
        for item in items:
            if not isinstance(item, dict):
                continue
            senses = []
            for definition in item.get('definitions') or []:
                if not isinstance(definition, dict):
                    continue
                text = strip_html(definition.get('definition'))
                if not text:
                    continue
                parsed = definition.get('parsedExamples') or []
                examples = [strip_html(e.get('example')) for e in parsed if isinstance(e, dict)]
                if not examples:
                    examples = [strip_html(e) for e in definition.get('examples') or []
                                if isinstance(e, str)]
                senses.append(Sense(text, tuple(e for e in examples if e)[:2]))
                if len(senses) >= MAX_SENSES:
                    break
            if senses:
                entries.append(Entry(strip_html(item.get('partOfSpeech')) or _('Meaning'),
                                     strip_html(item.get('language')), tuple(senses)))
            if len(entries) >= MAX_ENTRIES:
                break
        if len(entries) >= MAX_ENTRIES:
            break
    return Article(word, 'wiktionary', tuple(entries), wiktionary_page(word))


def parse_summary(data):
    """A Summary from Wikipedia's page summary JSON; None when it is no article (missing,
    or empty)."""
    if not isinstance(data, dict):
        return None
    title = strip_html(data.get('displaytitle')) or strip_html(data.get('title'))
    extract = ' '.join(str(data.get('extract') or '').split())
    if not title or not extract:
        return None
    urls = ((data.get('content_urls') or {}).get('desktop') or {})
    url = urls.get('page') or ''
    if urllib.parse.urlsplit(url).scheme != 'https':
        url = ''
    return Summary(title, strip_html(data.get('description')), extract, url)


# -- StarDict --------------------------------------------------------------------------------

class StarDictError(Exception):
    pass


def _fold(word):
    return unicodedata.normalize('NFC', word).casefold()


class DictZip:
    """Random access to a dictzip file (gzip with an RA extra field listing its chunks);
    a plain gzip is decompressed whole, once."""

    def __init__(self, path):
        self.path = path
        self._whole = None
        self._chunks = None  # [(offset in file, compressed size)]
        self._chunk_length = 0
        with open(path, 'rb') as stream:
            header = stream.read(10)
            if len(header) < 10 or header[:2] != b'\x1f\x8b':
                raise StarDictError(f'{path} is not gzip')
            flags = header[3]
            extra = b''
            if flags & 4:
                (length,) = struct.unpack('<H', stream.read(2))
                extra = stream.read(length)
            if flags & 8:
                self._skip_string(stream)
            if flags & 16:
                self._skip_string(stream)
            if flags & 2:
                stream.read(2)
            start = stream.tell()
        position = 0
        while position + 4 <= len(extra):
            ident, length = extra[position:position + 2], struct.unpack(
                '<H', extra[position + 2:position + 4])[0]
            body = extra[position + 4:position + 4 + length]
            if ident == b'RA' and len(body) >= 6:
                _version, chunk_length, count = struct.unpack('<HHH', body[:6])
                sizes = struct.unpack(f'<{count}H', body[6:6 + 2 * count])
                self._chunk_length = chunk_length
                self._chunks = []
                offset = start
                for size in sizes:
                    self._chunks.append((offset, size))
                    offset += size
            position += 4 + length

    @staticmethod
    def _skip_string(stream):
        while stream.read(1) not in (b'\x00', b''):
            pass

    def read(self, offset, size):
        if self._chunks is None:
            if self._whole is None:
                with gzip.open(self.path, 'rb') as stream:
                    self._whole = stream.read()
            return self._whole[offset:offset + size]
        first = offset // self._chunk_length
        last = (offset + size - 1) // self._chunk_length
        data = bytearray()
        with open(self.path, 'rb') as stream:
            for index in range(first, min(last, len(self._chunks) - 1) + 1):
                position, length = self._chunks[index]
                stream.seek(position)
                data += zlib.decompressobj(-15).decompress(stream.read(length))
        start = offset - first * self._chunk_length
        return bytes(data[start:start + size])


class PlainFile:
    def __init__(self, path):
        self.path = path

    def read(self, offset, size):
        with open(self.path, 'rb') as stream:
            stream.seek(offset)
            return stream.read(size)


def _open_maybe_gzip(path):
    with open(path, 'rb') as stream:
        data = stream.read()
    return gzip.decompress(data) if data[:2] == b'\x1f\x8b' else data


class StarDict:
    """One StarDict dictionary, from its .ifo file's path. `name` is its bookname;
    lookup(word) returns its articles for the word (case folded) as plain text."""

    def __init__(self, ifo_path):
        self.ifo_path = ifo_path
        self.info = self._read_ifo(ifo_path)
        self.name = self.info.get('bookname') or os.path.basename(ifo_path)[:-4]
        self.types = self.info.get('sametypesequence', '')
        base = ifo_path[:-4]
        self._idx_path = next((p for p in (base + '.idx', base + '.idx.gz')
                               if os.path.exists(p)), None)
        dict_path = next((p for p in (base + '.dict', base + '.dict.dz') if os.path.exists(p)),
                         None)
        if self._idx_path is None or dict_path is None:
            raise StarDictError(f'{ifo_path}: no .idx or .dict beside it')
        self._syn_path = base + '.syn' if os.path.exists(base + '.syn') else None
        self._data = DictZip(dict_path) if dict_path.endswith('.dz') else PlainFile(dict_path)
        self._index = None  # folded word -> [(offset, size)]
        self._lock = threading.Lock()

    @staticmethod
    def _read_ifo(path):
        with open(path, encoding='utf-8', errors='replace') as stream:
            lines = stream.read().splitlines()
        if not lines or not lines[0].startswith("StarDict's dict ifo file"):
            raise StarDictError(f'{path} is not a StarDict .ifo file')
        info = {}
        for line in lines[1:]:
            key, sep, value = line.partition('=')
            if sep:
                info[key.strip()] = value.strip()
        return info

    def _load(self):
        with self._lock:
            if self._index is not None:
                return
            data = _open_maybe_gzip(self._idx_path)
            wide = self.info.get('idxoffsetbits') == '64'
            number = struct.Struct('>QI' if wide else '>II')
            index = {}
            order = []  # (offset, size) by the index's position, for the .syn file
            position = 0
            while position < len(data):
                end = data.find(b'\x00', position)
                if end < 0 or end + 1 + number.size > len(data):
                    break
                word = data[position:end].decode('utf-8', errors='replace')
                place = number.unpack_from(data, end + 1)
                index.setdefault(_fold(word), []).append(place)
                order.append(place)
                position = end + 1 + number.size
            if self._syn_path:
                synonyms = _open_maybe_gzip(self._syn_path)
                position = 0
                while position < len(synonyms):
                    end = synonyms.find(b'\x00', position)
                    if end < 0 or end + 5 > len(synonyms):
                        break
                    word = synonyms[position:end].decode('utf-8', errors='replace')
                    (number_in_index,) = struct.unpack_from('>I', synonyms, end + 1)
                    if number_in_index < len(order):
                        places = index.setdefault(_fold(word), [])
                        if order[number_in_index] not in places:
                            places.append(order[number_in_index])
                    position = end + 5
            self._index = index

    def lookup(self, word):
        """The articles for `word` as plain text ([] when it has none)."""
        self._load()
        articles = []
        for offset, size in self._index.get(_fold(word), []):
            text = self._article(self._data.read(offset, size))
            if text:
                articles.append(text)
        return articles

    def _article(self, data):
        """The text of an article's fields: text kinds kept, markup kinds (h, g, x) stripped,
        binary kinds (images, sounds, wiki) skipped."""
        fields = []
        position = 0
        types = self.types
        index = 0
        while position < len(data):
            if types:
                if index >= len(types):
                    break
                kind = types[index]
                last = index == len(types) - 1
            else:
                kind = chr(data[position])
                position += 1
                last = False
            index += 1
            if kind.islower():
                end = len(data) if last else data.find(b'\x00', position)
                end = len(data) if end < 0 else end
                raw = data[position:end]
                position = end + 1
                text = raw.decode('utf-8', errors='replace')
                if kind in 'hgx':
                    text = html_to_plain(text.replace('\n', '<br>') if kind != 'h' else text)
                if kind in 'mlhgxtyk':
                    fields.append(text.strip())
            else:
                if last:
                    position = len(data)
                    continue
                if position + 4 > len(data):
                    break
                (size,) = struct.unpack_from('>I', data, position)
                position += 4 + size
        return '\n'.join(field for field in fields if field)


def find_dictionaries(dirs=None):
    """The StarDict dictionaries in `dirs` (STARDICT_DIRS by default; each searched one level
    of folders deep), by name; a broken one is logged and skipped."""
    found = []
    for directory in dirs if dirs is not None else STARDICT_DIRS:
        if not os.path.isdir(directory):
            continue
        paths = []
        for dirpath, dirnames, filenames in os.walk(directory):
            paths += [os.path.join(dirpath, name) for name in filenames if name.endswith('.ifo')]
            if dirpath != directory:
                dirnames[:] = []
        for path in sorted(paths):
            try:
                found.append(StarDict(path))
            except (OSError, StarDictError) as error:
                log.warning('skipping the dictionary %s: %s', path, error)
    return found


# -- the service -----------------------------------------------------------------------------

class Lookup:
    """Definitions and summaries, fetched in a thread and cached. `fetch` (url -> JSON) and
    `dictionaries` (a list, or None to find the installed ones on first use) are for tests."""

    def __init__(self, fetch=None, dictionaries=None):
        self._fetch = fetch or http_json
        self._dictionaries = dictionaries
        self._cache = collections.OrderedDict()
        self._lock = threading.Lock()

    def remember(self, kind, text, language, value):
        """Cache an answer ('define' or 'summarize'), as a lookup does."""
        with self._lock:
            key = (kind, _fold(clean_word(text)), _language(language))
            self._cache[key] = value
            self._cache.move_to_end(key)
            while len(self._cache) > CACHE_SIZE:
                self._cache.popitem(last=False)

    def cached(self, kind, text, language):
        with self._lock:
            return self._cache.get((kind, _fold(clean_word(text)), _language(language)))

    def dictionaries(self):
        if self._dictionaries is None:
            self._dictionaries = find_dictionaries()
        return self._dictionaries

    def define_now(self, word, language='en'):
        """The Article for `word` (blocking): from the offline dictionaries when one has it,
        else Wiktionary. OnlineError when neither has it or Wiktionary cannot be reached."""
        word = clean_word(word)
        cached = self.cached('define', word, language)
        if cached is not None:
            return cached
        article = self._offline(word)
        if article is None:
            article = self._wiktionary(word, language)
        self.remember('define', word, language, article)
        return article

    def _offline(self, word):
        entries = []
        source = ''
        for dictionary in self.dictionaries():
            try:
                texts = dictionary.lookup(word)
            except (OSError, StarDictError, struct.error, zlib.error) as error:
                log.warning('reading the dictionary %s: %s', dictionary.name, error)
                continue
            for text in texts:
                lines = [line.strip() for line in text.split('\n') if line.strip()]
                entries.append(Entry(dictionary.name, '', tuple(Sense(line) for line in lines)))
                source = source or dictionary.name
        if not entries:
            return None
        return Article(word, source, tuple(entries), '')

    def _wiktionary(self, word, language):
        attempts = [word] + ([word.lower()] if word.lower() != word else [])
        error = None
        for attempt in attempts:
            try:
                data = self._fetch(definition_url(attempt, language))
            except OnlineError as failure:
                if not not_found(failure):
                    raise
                error = failure
                continue
            article = parse_definitions(data, attempt, language)
            if article.entries:
                return article
        failure = OnlineError(_('No definition of “{word}” was found').format(word=word))
        failure.not_found = True
        raise failure from error

    def summarize_now(self, text, language='en'):
        """The Wikipedia Summary for `text` (blocking): its article, else the first title a
        search finds. OnlineError when there is none or Wikipedia cannot be reached."""
        text = clean_word(text)[:200]
        cached = self.cached('summarize', text, language)
        if cached is not None:
            return cached
        summary = None
        try:
            summary = parse_summary(self._fetch(summary_url(text, language)))
        except OnlineError as error:
            if not not_found(error):
                raise
        if summary is None:
            found = self._fetch(wikipedia_search_url(text, language))
            pages = (found or {}).get('pages') if isinstance(found, dict) else None
            title = (pages[0] or {}).get('title') if pages else None
            if title:
                try:
                    summary = parse_summary(self._fetch(summary_url(title, language)))
                except OnlineError as error:
                    if not not_found(error):
                        raise
        if summary is None:
            failure = OnlineError(_('Wikipedia has no article on “{text}”').format(text=text))
            failure.not_found = True
            raise failure
        self.remember('summarize', text, language, summary)
        return summary

    def define(self, word, language, callback):
        return self._run(self.define_now, 'define', word, language, callback)

    def summarize(self, text, language, callback):
        return self._run(self.summarize_now, 'summarize', text, language, callback)

    def _run(self, function, kind, text, language, callback):
        cached = self.cached(kind, text, language)
        if cached is not None:
            task = _DoneTask()

            def deliver():
                if not task.cancelled:
                    callback(cached, None)
                return GLib.SOURCE_REMOVE

            GLib.idle_add(deliver)
            return task
        return run_async(function, callback, text, language)


class _DoneTask:
    cancelled = False

    def cancel(self):
        self.cancelled = True


_service = []


def service():
    """The app's Lookup, made on first use."""
    if not _service:
        _service.append(Lookup())
    return _service[0]
