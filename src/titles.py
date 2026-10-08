# SPDX-License-Identifier: GPL-2.0-or-later
# SPDX-FileCopyrightText: 2026 Jack Tully

"""Sort forms and comparison keys of titles and names: small pure functions.

    title_sort('The Hobbit')                 # 'Hobbit, The'
    title_sort('Der Prozess', 'de')          # 'Prozess, Der' (articles of a few languages)
    author_sort('Ada Lark')                  # 'Lark, Ada'
    author_sort('Martin Luther King Jr.')    # 'King, Martin Luther, Jr.'
    author_sort('Ursula K. Le Guin')         # 'Le Guin, Ursula K.' (a capitalised particle
                                             #  stays with the surname)
    author_sort('Ludwig van Beethoven')      # 'Beethoven, Ludwig van' (a lower-case one moves)
    authors_sort(['Ada Lark', 'Ben Ross'])   # 'Lark, Ada & Ross, Ben' (Calibre's form)
    fold('Émile Zola')                       # 'emile zola': case and accents dropped
    sort_key('Book 10')                      # fold() with numbers padded: 'Book 2' < 'Book 10'
    title_key('The Hobbit: There and Back')  # 'hobbit': for finding the same book twice
    name_tokens('Lark, Ada')                 # {'ada', 'lark'}: for matching names loosely
"""

import re
import unicodedata

ARTICLES = {
    'en': ('the', 'a', 'an'),
    'de': ('der', 'die', 'das', 'ein', 'eine'),
    'fr': ('le', 'la', 'les', "l'", 'un', 'une'),
    'es': ('el', 'la', 'los', 'las', 'un', 'una'),
    'it': ('il', 'lo', 'la', 'i', 'gli', 'le', "l'", 'un', 'una', 'uno'),
    'nl': ('de', 'het', 'een'),
    'pt': ('o', 'a', 'os', 'as', 'um', 'uma'),
}
SUFFIXES = {'jr', 'jr.', 'sr', 'sr.', 'ii', 'iii', 'iv', 'v', 'vi', 'vii', 'viii', 'phd',
            'ph.d.', 'md', 'm.d.'}
PARTICLES = {'van', 'von', 'de', 'der', 'den', 'del', 'della', 'di', 'da', 'du', 'des', 'la',
             'le', 'ten', 'ter', 'zu', 'dos', 'das', 'do'}
_NUMBER = re.compile(r'\d+')
_NOT_WORD = re.compile(r'[^\w]+')
_SUBTITLE = re.compile(r'\s*(?:[:;]|\s[-–—]\s).*$')
_BRACKETS = re.compile(r'\s*[(\[][^)\]]*[)\]]')


def fold(text):
    """Lower-case, accent-free text, for comparing and searching."""
    if not text:
        return ''
    if text.isascii():
        return text.lower()
    decomposed = unicodedata.normalize('NFKD', text)
    return ''.join(c for c in decomposed if not unicodedata.combining(c)).casefold()


def sort_key(text):
    """A key that sorts `text` case- and accent-blind, numbers by value."""
    return _NUMBER.sub(lambda match: match.group().zfill(8), fold(text))


def title_sort(title, language=''):
    """'The Hobbit' -> 'Hobbit, The'. English articles, or the language's when it has its
    own list (the language as an ISO 639 code)."""
    title = ' '.join((title or '').split())
    articles = ARTICLES.get((language or 'en')[:2].lower(), ARTICLES['en'])
    lowered = title.lower()
    for article in articles:
        if article.endswith("'"):
            if lowered.startswith(article) and len(title) > len(article):
                return f'{title[len(article):]}, {title[:len(article)]}'
        elif lowered.startswith(article + ' ') and len(title) > len(article) + 1:
            return f'{title[len(article) + 1:]}, {title[:len(article)]}'
    return title


def author_sort(name):
    """'Ada Lark' -> 'Lark, Ada'; a name with a comma already, or of one word, is kept."""
    name = ' '.join((name or '').split())
    if not name or ',' in name:
        return name
    words = name.split(' ')
    suffix = []
    while len(words) > 1 and words[-1].lower() in SUFFIXES:
        suffix.insert(0, words.pop())
    if len(words) < 2 or any(c.isdigit() for c in words[-1]):
        return ' '.join(words + suffix)
    surname = [words.pop()]
    run = []  # the particles before the surname, keeping at least one given name
    while len(words) > 1 and words[-1].lower() in PARTICLES:
        run.insert(0, words.pop())
    # A capitalised run starts the surname ('Le Guin', 'De la Mare'); a lower-case one
    # follows the given names ('Ludwig van Beethoven' -> 'Beethoven, Ludwig van').
    moved = []
    if run and run[0][0].isupper():
        surname[0:0] = run
    else:
        moved = run
    given = ' '.join(words + moved)
    result = f'{" ".join(surname)}, {given}'
    if suffix:
        result += ', ' + ' '.join(suffix)
    return result


def authors_sort(names):
    """The author sort of a list of names: Calibre's 'Lark, Ada & Ross, Ben'."""
    return ' & '.join(author_sort(name) for name in names if name)


def title_key(title):
    """A title reduced to what tells books apart: folded, without a leading article, a
    subtitle, bracketed notes or punctuation."""
    title = _BRACKETS.sub('', title or '')
    short = _SUBTITLE.sub('', title)
    if short.strip():
        title = short
    words = _NOT_WORD.sub(' ', fold(title)).split()
    if len(words) > 1 and words[0] in ('the', 'a', 'an'):
        words = words[1:]
    return ' '.join(words)


def name_tokens(name):
    """The words of a name, folded, without initials or punctuation: 'Lark, Ada' and
    'Ada Lark' give the same set."""
    words = _NOT_WORD.sub(' ', fold(name or '')).split()
    return {word for word in words if len(word) > 1}
