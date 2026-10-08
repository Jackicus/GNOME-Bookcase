# SPDX-License-Identifier: GPL-2.0-or-later
# SPDX-FileCopyrightText: 2026 Jack Tully

"""The search syntax, turned into SQL over the library (schema.py). A smart shelf is a saved
search.

    where, params = search.to_sql('author:lark -tag:poetry')   # a WHERE fragment on books
                                                                # as `b`, and its parameters
    search.register_functions(db)          # the `fold` SQL function the fragments call

to_sql() never raises: whatever the user types gives some search (an empty one gives '1',
every book).

The syntax (for the user guide):

- Words match the title, authors, series, tags and publisher, in any order: `harbour lark`
  finds books whose title, author, series, tags or publisher hold both words. Case and
  accents do not matter, and a word matches inside a word (`harb` finds "Harbour").
- `"a quiet harbour"` matches the words together, as a phrase.
- `-word` leaves out the books that match: `lark -poetry`, `-tag:poetry`, `-"night train"`.
- `or` between two searches finds books matching either: `lark or ross`. Searches side by
  side must all match (`and` may be written but changes nothing). Parentheses group:
  `(lark or ross) -tag:poetry`.
- A field narrows a word to one part of the book. Put a phrase in quotes (`author:"ada lark"`)
  and start the value with `=` to match it whole (`tag:=fantasy` finds the tag "Fantasy",
  not "Urban Fantasy"):
  - `title:`, `author:`, `series:`, `tag:`, `publisher:`, `shelf:` (a shelf's name)
  - `language:en` (the language code, as on the book: `en` also matches `en-GB`)
  - `format:epub` (books that have a file in that format: epub, kepub, pdf, mobi, azw3, cbz…)
  - `isbn:9780000000000` (dashes are ignored; part of a number matches)
  - `status:unread`, `status:reading`, `status:finished` (also `status:read`)
  - `rating:4` (exactly four stars), `rating:>=4`, `rating:<3`, `rating:4.5`; `rating:0` for
    books without a rating
  - `added:<30d` (added less than 30 days ago), `added:>1y` (more than a year ago), with `d`
    days, `w` weeks, `m` months, `y` years; or a date: `added:>=2026-01-01`, `added:2025`
  - `read:<7d` (opened in the last week), the same forms as `added:`
  - `published:>=2000`, `published:<1900-06`, `published:1999`
  - `has:cover`, `has:series`, `has:rating`, `has:tags`, `has:description`,
    `has:annotations`; `has:missing` for books whose files cannot be found
- A word with a colon that is not one of these fields is searched as a word (`re:zero`).
- A field's value it cannot understand (`rating:many`) matches no book.
"""

import datetime
import re
import time

from bookcase.titles import fold

TEXT_FIELDS = {'title', 'author', 'series', 'tag', 'publisher', 'shelf'}
ALIASES = {'authors': 'author', 'by': 'author', 'tags': 'tag', 'subject': 'tag',
           'lang': 'language', 'year': 'published', 'date': 'published', 'stars': 'rating',
           'is': 'status', 'shelves': 'shelf'}
FIELDS = TEXT_FIELDS | {'language', 'format', 'isbn', 'status', 'rating', 'added', 'read',
                        'published', 'has'}
STATUSES = {'unread': 'unread', 'new': 'unread', 'reading': 'reading', 'finished': 'finished',
            'read': 'finished', 'done': 'finished'}
UNITS = {'d': 86400, 'w': 7 * 86400, 'm': 30 * 86400, 'y': 365 * 86400}
HAS = {
    'cover': 'b.has_cover != 0',
    'series': 'b.series_id IS NOT NULL',
    'rating': 'b.rating > 0',
    'tags': 'EXISTS (SELECT 1 FROM book_tags bt WHERE bt.book_id = b.id)',
    'description': "b.description != ''",
    'annotations': 'EXISTS (SELECT 1 FROM annotations n WHERE n.book_id = b.id)',
    'notes': "EXISTS (SELECT 1 FROM annotations n WHERE n.book_id = b.id AND n.note != '')",
    'missing': 'NOT EXISTS (SELECT 1 FROM files f WHERE f.book_id = b.id AND f.missing = 0)',
}
# The filter bar's format choices (pages/books.py): a name and the formats it takes in.
FORMAT_GROUPS = {
    'epub': ('epub', 'kepub'),
    'pdf': ('pdf',),
    'comic': ('cbz', 'cbr'),
    'kindle': ('mobi', 'azw3'),
    'fb2': ('fb2', 'fbz'),
    'txt': ('txt',),
}
NOTHING = '0'
_COMPARISON = re.compile(r'^(>=|<=|>|<|=)?\s*(.*)$', re.S)
_RELATIVE = re.compile(r'^(\d+(?:\.\d+)?)\s*([dwmy])$')
_DATE = re.compile(r'^(\d{4})(?:-(\d{1,2}))?(?:-(\d{1,2}))?$')


def register_functions(db):
    """Give a connection the `fold` SQL function (case- and accent-blind text)."""
    db.create_function('fold', 1, fold, deterministic=True)


def _tokens(query):
    """('(' | ')' | 'or' | 'not' | ('term', field, value, quoted)), from left to right."""
    tokens = []
    i, n = 0, len(query)
    while i < n:
        c = query[i]
        if c.isspace():
            i += 1
        elif c in '()':
            tokens.append(c)
            i += 1
        elif c == '-' and i + 1 < n and not query[i + 1].isspace():
            tokens.append('not')
            i += 1
        elif c == '"':
            end = query.find('"', i + 1)
            end = n if end < 0 else end
            tokens.append(('term', None, query[i + 1:end], True))
            i = end + 1
        else:
            j = i
            while j < n and not query[j].isspace() and query[j] not in '()"':
                j += 1
            word = query[i:j]
            field, colon, value = word.partition(':')
            field = ALIASES.get(field.lower(), field.lower())
            if colon and field in FIELDS:
                quoted = False
                if not value and j < n and query[j] == '"':
                    end = query.find('"', j + 1)
                    end = n if end < 0 else end
                    value, quoted, j = query[j + 1:end], True, end + 1
                elif value == '=' and j < n and query[j] == '"':
                    end = query.find('"', j + 1)
                    end = n if end < 0 else end
                    value, quoted, j = '=' + query[j + 1:end], True, end + 1
                tokens.append(('term', field, value, quoted))
            elif word.lower() in ('or', '|', '||'):
                tokens.append('or')
            elif word.lower() in ('and', '&', '&&'):
                pass
            else:
                tokens.append(('term', None, word, False))
            i = j
    return tokens


class _Parser:
    """A recursive descent over the tokens, building SQL as it goes: or < and < not < atom.
    Each method returns (sql, params), or None for an empty search."""

    def __init__(self, tokens, now):
        self.tokens = tokens
        self.at = 0
        self.depth = 0
        self.now = now

    def peek(self):
        return self.tokens[self.at] if self.at < len(self.tokens) else None

    def parse_or(self):
        parts = [self.parse_and()]
        while self.peek() == 'or':
            self.at += 1
            parts.append(self.parse_and())
        return _join(' OR ', [part for part in parts if part is not None])

    def parse_and(self):
        parts = []
        while True:
            token = self.peek()
            if token is None or token == 'or':
                break
            if token == ')':
                if self.depth:
                    break
                self.at += 1  # a stray one
                continue
            part = self.parse_not()
            if part is not None:
                parts.append(part)
        return _join(' AND ', parts)

    def parse_not(self):
        token = self.peek()
        if token == 'not':
            self.at += 1
            inner = self.parse_not()
            if inner is None:
                return None
            return f'NOT ({inner[0]})', inner[1]
        if token == '(':
            self.at += 1
            self.depth += 1
            inner = self.parse_or()
            self.depth -= 1
            if self.peek() == ')':
                self.at += 1
            return inner
        if token in (')', 'or', None):
            return None
        self.at += 1
        _kind, field, value, quoted = token
        return _term(field, value, quoted, self.now)


def _join(operator, parts):
    if not parts:
        return None
    if len(parts) == 1:
        return parts[0]
    sql = operator.join(f'({part[0]})' for part in parts)
    params = [param for part in parts for param in part[1]]
    return sql, params


def _like(text):
    """A LIKE pattern matching `text` (folded) anywhere, with ESCAPE '\\'."""
    escaped = fold(text).replace('\\', '\\\\').replace('%', '\\%').replace('_', '\\_')
    return f'%{escaped}%'


def _text_match(column, value):
    """SQL matching the folded `column` against a field's value: inside it, or whole with
    a leading '='."""
    if value.startswith('=') and len(value) > 1:
        return f'fold({column}) = ?', [fold(value[1:].strip())]
    return f"fold({column}) LIKE ? ESCAPE '\\'", [_like(value)]


def _term(field, value, quoted, now):
    value = value.strip()
    if field is None:
        if not value:
            return None
        return "b.search_text LIKE ? ESCAPE '\\'", [_like(value)]
    if not value or value == '=':
        return None
    if field == 'title':
        return _text_match('b.title', value)
    if field == 'author':
        sql, params = _text_match('a.name', value)
        return ('EXISTS (SELECT 1 FROM book_authors ba JOIN authors a ON a.id = ba.author_id '
                f'WHERE ba.book_id = b.id AND {sql})'), params
    if field == 'series':
        sql, params = _text_match('s.name', value)
        return f'EXISTS (SELECT 1 FROM series s WHERE s.id = b.series_id AND {sql})', params
    if field == 'tag':
        sql, params = _text_match('t.name', value)
        return ('EXISTS (SELECT 1 FROM book_tags bt JOIN tags t ON t.id = bt.tag_id '
                f'WHERE bt.book_id = b.id AND {sql})'), params
    if field == 'shelf':
        sql, params = _text_match('sh.name', value)
        return ('EXISTS (SELECT 1 FROM shelf_books sb JOIN shelves sh ON sh.id = sb.shelf_id '
                f'WHERE sb.book_id = b.id AND {sql})'), params
    if field == 'publisher':
        return _text_match('b.publisher', value)
    if field == 'language':
        code = value.lstrip('=').strip().lower()
        escaped = code.replace('\\', '\\\\').replace('%', '\\%').replace('_', '\\_')
        return ("lower(b.language) = ? OR lower(b.language) LIKE ? ESCAPE '\\'",
                [code, escaped + '-%'])
    if field == 'format':
        return ('EXISTS (SELECT 1 FROM files f WHERE f.book_id = b.id AND f.format = ?)',
                [value.lstrip('=').strip().lower().lstrip('.')])
    if field == 'isbn':
        digits = ''.join(c for c in value.upper() if c.isdigit() or c == 'X')
        if not digits:
            return NOTHING, []
        return ("EXISTS (SELECT 1 FROM identifiers i WHERE i.book_id = b.id AND "
                "i.type = 'isbn' AND upper(replace(replace(i.value, '-', ''), ' ', '')) "
                "LIKE ?)"), [f'%{digits}%']
    if field == 'status':
        status = STATUSES.get(fold(value.lstrip('=')))
        return ('b.status = ?', [status]) if status else (NOTHING, [])
    if field == 'has':
        return HAS.get(fold(value), NOTHING), []
    if field == 'rating':
        return _rating(value)
    if field in ('added', 'read'):
        return _time(field, value, now)
    if field == 'published':
        return _published(value)
    return NOTHING, []


def _split_comparison(value):
    match = _COMPARISON.match(value)
    return match.group(1) or '', match.group(2).strip()


def _rating(value):
    operator, number = _split_comparison(value)
    try:
        stars = float(number)
    except ValueError:
        return NOTHING, []
    if stars != stars or abs(stars) == float('inf'):
        return NOTHING, []
    stored = round(stars * 2)
    return f'b.rating {operator or "="} ?', [stored]


def _date_range(text):
    """The Unix times a 'YYYY', 'YYYY-MM' or 'YYYY-MM-DD' starts and ends (local time), or
    None."""
    match = _DATE.match(text)
    if not match:
        return None
    year, month, day = (int(part) if part else None for part in match.groups())
    try:
        if day is not None:
            start = datetime.datetime(year, month, day)
            end = start + datetime.timedelta(days=1)
        elif month is not None:
            start = datetime.datetime(year, month, 1)
            end = datetime.datetime(year + month // 12, month % 12 + 1, 1)
        else:
            start = datetime.datetime(year, 1, 1)
            end = datetime.datetime(year + 1, 1, 1)
        return start.timestamp(), end.timestamp()
    except (ValueError, OverflowError, OSError):
        return None


def _time(field, value, now):
    column = 'b.added' if field == 'added' else 'b.last_read'
    guard = '' if field == 'added' else 'b.last_read > 0 AND '
    operator, text = _split_comparison(value.lower())
    relative = _RELATIVE.match(text)
    if relative:
        # An age: '<30d' is younger than 30 days, so a time after now - 30 days.
        moment = now - float(relative.group(1)) * UNITS[relative.group(2)]
        flipped = {'': '>=', '=': '>=', '<': '>', '<=': '>=', '>': '<', '>=': '<='}[operator]
        return f'{guard}{column} {flipped} ?', [moment]
    if text == 'today':
        today = datetime.date.fromtimestamp(now)
        text = today.isoformat()
    span = _date_range(text)
    if span is None:
        return NOTHING, []
    start, end = span
    if operator == '<':
        return f'{guard}{column} < ?', [start]
    if operator == '<=':
        return f'{guard}{column} < ?', [end]
    if operator == '>':
        return f'{column} >= ?', [end]
    if operator == '>=':
        return f'{column} >= ?', [start]
    return f'{column} >= ? AND {column} < ?', [start, end]


def _published(value):
    operator, text = _split_comparison(value)
    if not _DATE.match(text):
        return NOTHING, []
    # Compare as many characters as the value has: '1999' takes in '1999-05-01'.
    parts = text.split('-')
    text = '-'.join([parts[0]] + [part.zfill(2) for part in parts[1:]])
    sql = f"b.published != '' AND substr(b.published, 1, {len(text)}) {operator or '='} ?"
    return sql, [text]


def to_sql(query, now=None):
    """A WHERE fragment over books (as `b`) and its parameters for a search; ('1', []) when
    the search is empty."""
    try:
        tokens = _tokens(query or '')
        parser = _Parser(tokens, time.time() if now is None else now)
        result = parser.parse_or()
        while parser.peek() is not None:  # what a stray ')' left behind
            parser.at += 1
            more = parser.parse_or()
            if more is not None:
                result = more if result is None else _join(' AND ', [result, more])
    except RecursionError:
        return NOTHING, []
    if result is None:
        return '1', []
    return result


def filter_query(format=None, status=None, rating=None, language=None):
    """The search the filter bar's choices make (pages/books.py), to put after what is typed:
    a FORMAT_GROUPS name, a status, the least stars (1-5), a language code. '' for none."""
    parts = []
    group = FORMAT_GROUPS.get(format or '')
    if group:
        terms = ' or '.join(f'format:{name}' for name in group)
        parts.append(f'({terms})' if len(group) > 1 else terms)
    if status in ('unread', 'reading', 'finished'):
        parts.append(f'status:{status}')
    if rating:
        parts.append(f'rating:>={int(rating)}')
    if language:
        code = ''.join(c for c in language if c.isalnum() or c == '-')
        if code:
            parts.append(f'language:{code}')
    return ' '.join(parts)


def words(query):
    """The plain words and phrases of a search (no fields, no negations), for highlighting
    what matched."""
    found = []
    negate = False
    for token in _tokens(query or ''):
        if token == 'not':
            negate = True
            continue
        if isinstance(token, tuple) and token[1] is None and not negate and token[2].strip():
            found.append(token[2].strip())
        negate = False
    return found
