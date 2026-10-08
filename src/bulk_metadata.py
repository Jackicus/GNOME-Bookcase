# SPDX-License-Identifier: GPL-2.0-or-later
# SPDX-FileCopyrightText: 2026 Jack Tully

"""Find Metadata for many books at once: a queue of online lookups, then what each would
change, applied as one undo step.

    lookups = [Lookup.of(book) for book in books]
    queue = Queue(lookups, on_update, on_done, fetch=None, google_key='')
    queue.start()                   # one book after another in a thread; on the main loop
                                    # on_update(lookup) as each starts and ends,
                                    # on_done(cancelled) at the end
    queue.cancel()                  # stops after the request in flight
    run_lookup(lookup, fetch=None, google_key='')   # one book, in the calling thread
    choose(lookup, index, fetch=None)   # take lookup.choices[index] instead (completed and
                                    # its cover fetched, blocking; remembered)
    complete_choice(lookup, index, fetch=None)   # what choose() fetches, for a thread;
                                    # lookup.completed[index] = it, then choose() is quick
    changes(book, lookup, replace=False)   # {group: [Change]} it would make
    apply(library, covers, lookups, selection, replace=False)   # one undo step; books changed

A lookup asks online.search() (by ISBN first when the book has one, else title and first
author), judges the best candidate (classify: 'found', 'ambiguous' or 'not-found'),
keeps up to CHOICES plausible candidates (lookup.choices, the best first: the review offers
them in a drop-down), fetches what the search leaves out (online.complete) and the
candidate's cover (Open
Library's covers by id, which are not rate limited). online's default fetch keeps Open
Library's API requests a second apart, so a queue of a hundred books takes a few minutes and
never floods it (the reason r/Calibre begs users not to bulk-download, calibre.md §2).

Changes come in four groups the review can switch per book: 'details' (publisher,
published, language, series and number, page count, identifiers: the ISBN and the source's
ids),
'description', 'cover' and 'tags'. Title and authors are never changed in bulk. By default
only empty fields are filled (no publisher, no description, no cover, no tags); `replace`
also overwrites what is there (tags are only ever added). apply() writes the selected groups
({book_id: {group, …}}) through Library.update_books and CoverStore.save inside one
Library.undoable('Find Metadata').
"""

import dataclasses
import logging
import threading
from gettext import gettext as _

from . import online

log = logging.getLogger(__name__)

GROUPS = ('details', 'description', 'cover', 'tags')
STATUSES = ('waiting', 'searching', 'found', 'ambiguous', 'not-found', 'error')
FOUND_TITLE = 0.9  # title similarity for a sure match
FOUND_AUTHOR = 0.8
MAYBE_TITLE = 0.6  # below this: not found
DETAIL_FIELDS = ('publisher', 'published', 'language', 'series', 'pages')
CHOICES = 3  # candidates offered for a book


@dataclasses.dataclass
class Lookup:
    book_id: int
    book: object  # the library.Book looked up (a frozen snapshot)
    status: str = 'waiting'
    candidate: object = None  # online.Candidate, completed
    cover: bytes = None  # the candidate's cover, fetched
    error: str = ''
    choices: list = dataclasses.field(default_factory=list)  # [online.Candidate]
    chosen: int = 0  # the index in choices of `candidate`
    completed: dict = dataclasses.field(default_factory=dict)  # index -> (candidate, cover)

    @classmethod
    def of(cls, book):
        return cls(book.id, book)


@dataclasses.dataclass(frozen=True)
class Change:
    field: str
    label: str
    old: str  # as shown
    new: str


def labels():
    return {'publisher': _('Publisher'), 'published': _('Published'),
            'language': _('Language'), 'series': _('Series'), 'pages': _('Pages'),
            'isbn': _('ISBN'),
            'description': _('Description'), 'cover': _('Cover'), 'tags': _('Tags')}


def group_names():
    return {'details': _('Details'), 'description': _('Description'), 'cover': _('Cover'),
            'tags': _('Tags')}


# -- looking up --------------------------------------------------------------------------------

def classify(book, candidates):
    """('found' | 'ambiguous' | 'not-found', the best candidate or None)."""
    if not candidates:
        return 'not-found', None
    isbn = online.normalize_isbn(book.identifiers.get('isbn', ''))
    if isbn:
        for candidate in candidates:
            if candidate.identifiers.get('isbn') == isbn:
                return 'found', candidate
    best = candidates[0]
    main = best.title.split(':')[0]
    title = max(online.similarity(book.title, best.title), online.similarity(book.title, main))
    if book.authors and best.authors:
        author = max(online.similarity(book.authors[0], name) for name in best.authors)
    else:
        author = 1.0 if not book.authors else 0.0
    if title >= FOUND_TITLE and author >= FOUND_AUTHOR:
        return 'found', best
    if title >= MAYBE_TITLE:
        return 'ambiguous', best
    return 'not-found', None


def plausible(book, candidates, best=None):
    """Up to CHOICES candidates worth offering for the book: `best` (classify's) first,
    then the others whose title is close enough."""
    chosen = [best] if best is not None else []
    for candidate in candidates:
        if len(chosen) >= CHOICES:
            break
        if candidate is best:
            continue
        main = candidate.title.split(':')[0]
        title = max(online.similarity(book.title, candidate.title),
                    online.similarity(book.title, main))
        if title >= MAYBE_TITLE:
            chosen.append(candidate)
    return chosen


def complete_choice(lookup, index, fetch=None):
    """(lookup.choices[index] completed, its cover's bytes or None), blocking; the lookup
    is not changed (choose() takes it)."""
    return _complete(lookup, lookup.choices[index], fetch)


def _complete(lookup, candidate, fetch):
    """(the candidate completed, its cover's bytes or None)."""
    try:
        candidate = online.complete(candidate, fetch=fetch)
    except online.OnlineError as error:
        log.info('completing %s: %s', lookup.book.title, error)
    cover = None
    for url in (candidate.cover_url, candidate.thumbnail_url):
        if not url:
            continue
        try:
            cover = online.fetch_cover(url, fetch=fetch)
            break
        except online.OnlineError as error:
            log.info('cover of %s: %s', lookup.book.title, error)
    return candidate, cover


def choose(lookup, index, fetch=None):
    """Make lookup.choices[index] the lookup's candidate (blocking: completed and its
    cover fetched the first time)."""
    if not 0 <= index < len(lookup.choices):
        raise IndexError(index)
    if index not in lookup.completed:
        lookup.completed[index] = _complete(lookup, lookup.choices[index], fetch)
    lookup.candidate, lookup.cover = lookup.completed[index]
    lookup.chosen = index
    return lookup


def run_lookup(lookup, fetch=None, google_key=''):
    """Look one book up (blocking): sets lookup.status, candidate, cover and error."""
    book = lookup.book
    isbn = book.identifiers.get('isbn', '')
    try:
        candidates = online.search(title=book.title, authors=list(book.authors[:1]),
                                   isbn=isbn, google_key=google_key, fetch=fetch)
    except online.OnlineError as error:
        lookup.status, lookup.error = 'error', str(error)
        return lookup
    except ValueError:
        lookup.status = 'not-found'
        return lookup
    status, candidate = classify(book, candidates)
    lookup.status = status
    if candidate is None:
        return lookup
    lookup.choices = plausible(book, candidates, candidate)
    lookup.completed = {}
    return choose(lookup, 0, fetch)


class Queue:
    """Lookups one after another in a thread; see the module."""

    def __init__(self, lookups, on_update, on_done, fetch=None, google_key=''):
        self.lookups = list(lookups)
        self.on_update = on_update
        self.on_done = on_done
        self.fetch = fetch
        self.google_key = google_key
        self._cancelled = threading.Event()
        self.thread = None

    def start(self):
        self.thread = threading.Thread(target=self._run, name='bookcase-bulk-metadata',
                                       daemon=True)
        self.thread.start()

    def cancel(self):
        self._cancelled.set()

    @property
    def cancelled(self):
        return self._cancelled.is_set()

    def _deliver(self, callback, *args):
        from gi.repository import GLib

        def call():
            callback(*args)
            return GLib.SOURCE_REMOVE

        GLib.idle_add(call)

    def _run(self):
        for lookup in self.lookups:
            if self.cancelled:
                break
            lookup.status = 'searching'
            self._deliver(self.on_update, lookup)
            try:
                run_lookup(lookup, fetch=self.fetch, google_key=self.google_key)
            except Exception as error:  # a bug: that book fails, the rest go on
                log.exception('looking up %s', lookup.book.title)
                lookup.status, lookup.error = 'error', str(error)
            if self.cancelled and lookup.status == 'searching':
                lookup.status = 'waiting'
            self._deliver(self.on_update, lookup)
        self._deliver(self.on_done, self.cancelled)


# -- what would change -------------------------------------------------------------------------

def _series_text(name, index):
    if not name:
        return ''
    return f'{name} #{index:g}' if index else name


def changes(book, lookup, replace=False, has_cover=None):
    """{group: [Change]} for the groups the lookup's candidate would change (see the
    module). has_cover defaults to book.has_cover."""
    candidate = lookup.candidate
    if candidate is None:
        return {}
    names = labels()
    found = {}
    details = []
    for field in DETAIL_FIELDS:
        if field == 'series':
            new = _series_text(candidate.series, candidate.series_index)
            old = _series_text(book.series, book.series_index)
        elif field == 'pages':
            new = str(candidate.pages) if candidate.pages > 0 else ''
            old = str(book.pages) if getattr(book, 'pages', 0) else ''
        else:
            new, old = getattr(candidate, field), getattr(book, field)
        if new and new != old and (replace or not old):
            details.append(Change(field, names[field], old, new))
    isbn = candidate.identifiers.get('isbn', '')
    old_isbn = book.identifiers.get('isbn', '')
    if isbn and online.normalize_isbn(old_isbn) != isbn and (replace or not old_isbn):
        details.append(Change('isbn', names['isbn'], old_isbn, isbn))
    if details:
        found['details'] = details
    description = online.html_to_plain(candidate.description)
    old_description = online.html_to_plain(book.description)
    if description and description != old_description and (replace or not old_description):
        found['description'] = [Change('description', names['description'], old_description,
                                       description)]
    has_cover = book.has_cover if has_cover is None else has_cover
    if lookup.cover and (replace or not has_cover):
        found['cover'] = [Change('cover', names['cover'], 'cover' if has_cover else '',
                                 'cover')]
    known = {tag.casefold() for tag in book.tags}
    extra = [tag for tag in candidate.tags if tag.casefold() not in known]
    if extra and (replace or not book.tags):
        found['tags'] = [Change('tags', names['tags'], ', '.join(book.tags), ', '.join(extra))]
    return found


def update_fields(book, lookup, groups, replace=False):
    """(fields for Library.update_books, tags to add, cover bytes or None) for the groups."""
    candidate = lookup.candidate
    found = changes(book, lookup, replace)
    fields, add_tags, cover = {}, [], None
    for group in groups:
        for change in found.get(group, ()):
            if change.field == 'series':
                fields['series'] = candidate.series
                fields['series_index'] = candidate.series_index
            elif change.field == 'pages':
                fields['pages'] = candidate.pages
            elif change.field == 'isbn':
                pass  # with the identifiers, below
            elif change.field == 'description':
                fields['description'] = candidate.description
            elif change.field == 'cover':
                cover = lookup.cover
            elif change.field == 'tags':
                known = {tag.casefold() for tag in book.tags}
                add_tags = [tag for tag in candidate.tags if tag.casefold() not in known]
            else:
                fields[change.field] = getattr(candidate, change.field)
    if 'details' in groups and 'details' in found:
        identifiers = {key: value for key, value in book.identifiers.items() if key != 'uuid'}
        for key, value in candidate.identifiers.items():
            if value and (key not in identifiers or (replace and key == 'isbn')):
                identifiers[key] = value
        if identifiers != {key: value for key, value in book.identifiers.items()
                           if key != 'uuid'}:
            fields['identifiers'] = identifiers
    return fields, add_tags, cover


def apply(library, covers, lookups, selection, replace=False):
    """Write the selected groups of each lookup ({book_id: groups}) in one undo step; the
    number of books changed. Books changed since their lookup are read again first."""
    changed = 0
    with library.undoable(_('Find Metadata')):
        for lookup in lookups:
            groups = selection.get(lookup.book_id)
            if not groups or lookup.candidate is None:
                continue
            book = library.book(lookup.book_id)
            if book is None:
                continue
            fields, add_tags, cover = update_fields(book, lookup, groups, replace)
            if fields or add_tags:
                library.update_books([book.id], add_tags=add_tags, **fields)
            if cover:
                try:
                    covers.save(book.id, cover)
                except ValueError as error:
                    log.info('cover of %s: %s', book.title, error)
                    cover = None
            if fields or add_tags or cover:
                changed += 1
    return changed
