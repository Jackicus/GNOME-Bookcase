# SPDX-License-Identifier: GPL-2.0-or-later
# SPDX-FileCopyrightText: 2026 Jack Tully

"""Covers of books in a catalogue (not in the library): fetched from the catalogue, kept in
the cache, drawn like the library's covers.

    cover = RemoteCover(width=150)
    cover.set_entry(entry, client)       # an opds.Entry, the opds.Client of its catalogue
    tile = CatalogTile(width=150)        # the cover, the title and the author, for a grid
    tile.set_entry(entry, client, in_library=False)

A RemoteCover is widgets/cover.py's Cover over a stand-in book (CoverBook): the entry's
thumbnail (or cover) is fetched into opds.ThumbnailCache by a small pool of threads (one
still waiting for a cover that has been scrolled past is skipped), decoded there, and kept
in Cover's cache; without one, or when the image fails or is tiny (a catalogue's generic
book icon), the drawn cover shows the title and author. A tile marks a book the library has
already, as a finished book's tile is marked.
"""

import concurrent.futures
import dataclasses
import logging
import threading
from gettext import gettext as _

from gi.repository import Gdk, GLib, Gtk, Pango

from .. import opds
from . import cover as covers

log = logging.getLogger(__name__)

FETCHERS = 4
MIN_WIDTH = 48  # pixels: a smaller image is an icon, not a cover

_pool = None
_thumbnails = None
_lock = threading.Lock()


@dataclasses.dataclass(frozen=True)
class CoverBook:
    """What Cover needs of a book, for an entry of a catalogue."""

    id: str
    title: str
    authors: tuple = ()
    has_cover: bool = False
    cover_version: int = 0


def thumbnails():
    global _thumbnails
    with _lock:
        if _thumbnails is None:
            _thumbnails = opds.ThumbnailCache()
        return _thumbnails


def _fetcher():
    global _pool
    with _lock:
        if _pool is None:
            _pool = concurrent.futures.ThreadPoolExecutor(
                max_workers=FETCHERS, thread_name_prefix='bookcase-catalog-cover')
        return _pool


def _fetch(url, client, wanted, show):
    """In a fetching thread: the image at `url` as a texture, unless no longer wanted."""
    if not wanted():
        return
    texture = None
    try:
        path = thumbnails().fetch(url, client)
        texture = Gdk.Texture.new_from_filename(path)
        if texture.get_width() < MIN_WIDTH:
            texture = None
    except opds.OpdsError as error:
        log.info('catalogue cover %s: %s', url, error)
    except GLib.Error as error:
        log.info('catalogue cover %s: %s', url, error.message)
    except Exception:
        log.exception('catalogue cover %s', url)
    GLib.idle_add(show, texture)


def entry_url(entry):
    return entry.thumbnail or entry.cover


class RemoteCover(covers.Cover):
    __gtype_name__ = 'BookcaseRemoteCover'

    def __init__(self, width=150, **kwargs):
        super().__init__(width=width, **kwargs)
        self._url = ''
        self._client = None
        self._wanted = [0]  # the token a fetching thread compares with

    def set_entry(self, entry, client=None, url=None):
        if entry is None:
            self._wanted[0] = -1
            self.set_book(None)
            return
        self._url = url if url is not None else entry_url(entry)
        self._client = client
        book = CoverBook(id='opds:' + (self._url or entry.key), title=entry.title,
                         authors=tuple(entry.authors), has_cover=bool(self._url))
        self.set_book(book)

    def _load(self):
        book = self._book
        self._token += 1
        token = self._token
        self._wanted[0] = token
        key = (book.id, 0, 0)
        cached = covers._cache.get(key)
        if cached is not None:
            covers._cache.move_to_end(key)
            self._texture = cached
            self.queue_draw()
            return
        if self._client is None:
            self._failed = True
            return
        ref = self.weak_ref()
        wanted_list = self._wanted

        def show(texture):
            if texture is not None:
                covers._remember(key, texture)
            cover = ref()
            if cover is not None and cover._token == token:
                cover._texture = texture
                cover._failed = texture is None
                cover.queue_draw()
            return GLib.SOURCE_REMOVE

        _fetcher().submit(_fetch, self._url, self._client, lambda: wanted_list[0] == token,
                          show)


class CatalogTile(Gtk.Box):
    """A catalogue's book in a grid: the cover (with a mark when the library has it), the
    title in two lines at most, and the author."""

    __gtype_name__ = 'BookcaseCatalogTile'

    def __init__(self, width=150):
        super().__init__(orientation=Gtk.Orientation.VERTICAL, spacing=4,
                         halign=Gtk.Align.CENTER, valign=Gtk.Align.START)
        self.add_css_class('book-tile')
        self.cover = RemoteCover(width=width)
        overlay = Gtk.Overlay(halign=Gtk.Align.CENTER)
        overlay.set_child(self.cover)
        self.mark = Gtk.Image(halign=Gtk.Align.END, valign=Gtk.Align.END, visible=False,
                              pixel_size=12, icon_name='object-select-symbolic')
        self.mark.add_css_class('cover-mark')
        self.mark.set_tooltip_text(_('In your library'))
        overlay.add_overlay(self.mark)
        self.append(overlay)
        self.title = Gtk.Label(xalign=0, yalign=0, margin_top=6, wrap=True,
                               wrap_mode=Pango.WrapMode.WORD_CHAR, lines=2,
                               ellipsize=Pango.EllipsizeMode.END, max_width_chars=1,
                               width_chars=1)
        self.title.add_css_class('book-tile-title')
        _width, two_lines = self.title.create_pango_layout('Ag\nAg').get_pixel_size()
        self.title.set_size_request(-1, two_lines)
        self.append(self.title)
        self.subtitle = Gtk.Label(xalign=0, ellipsize=Pango.EllipsizeMode.END,
                                  max_width_chars=1, width_chars=1)
        self.subtitle.add_css_class('caption')
        self.subtitle.add_css_class('dimmed')
        self.append(self.subtitle)

    def set_entry(self, entry, client=None, in_library=False):
        self.cover.set_entry(entry, client)
        if entry is None:
            return
        self.title.set_text(entry.title)
        self.subtitle.set_text(entry.author)
        self.subtitle.set_visible(bool(entry.authors))
        self.mark.set_visible(in_library)
        tooltip = entry.title
        if entry.authors:
            tooltip = _('{title}\n{author}').format(title=entry.title, author=entry.author)
        self.set_tooltip_text(tooltip)
