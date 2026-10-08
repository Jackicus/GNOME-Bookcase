# SPDX-License-Identifier: GPL-2.0-or-later
# SPDX-FileCopyrightText: 2026 Jack Tully

"""A PDF, drawn by Poppler: the reader window's view for PDF books, with BookView's methods
and signals, so the window drives either without caring which it has.

    view = PdfView()                        # PdfView.available(): False without Poppler
    view.open(path, fmt, location=None, fraction=None, annotations=(), bookmarks=(),
              style=None)                   # locations are pdf_location strings ('page:12')
    view.set_style(style)                   # theme (a recolouring), flow, maxColumns
    view.next() / prev() / go_left() / go_right() / scroll(direction)
    view.start() / end() / next_section() / prev_section() / back() / forward()
    view.go_to(location) / go_to_fraction(f) / select(location)
    view.clear_selection() / selected_text()
    view.search(text) / clear_search()
    view.set_annotations([{'cfi', 'color'}]) / add_annotation() / remove_annotation()
    view.set_bookmarks(locations, callback=None)
    view.zoom_in() / zoom_out() / set_fit('auto' | 'width' | 'page') / zoom_percent / fit
    view.show_progress(visible) / get_toc(callback) / close()

Signals as BookView's ('loaded', 'toc-ready', 'relocated', 'selection',
'annotation-activated', 'search-result', 'search-done', 'history', 'error',
'toggle-chrome'), the messages shaped the same (.claude/rules/reader.md), with locations in
place of CFIs; and 'zoom-changed' when the zoom or the fit changes.

Layout: continuous vertical scrolling ('flow' scrolled), or a page at a time (paginated),
two side by side ('maxColumns' 2) when the view is wider than tall, the first page alone (a
cover). Zoom: automatic (the default: the width up to AUTO_MAX when scrolling, the whole
page when paginated), fit width, fit page, or a percentage (100% = 96 px per inch);
Ctrl+scroll and the window's bigger/smaller keys go through the window, a pinch through
the view's Gtk.GestureZoom, each keeping the point under it in place.

Rendering: pages are drawn by a thread of its own, with its own Poppler.Document (Poppler's
documents are not thread-safe), at the zoom times the monitor's scale, into textures kept
in a least-recently-used cache of CACHE_BYTES; the main thread's document gives sizes, text,
links, the outline and search. A page not drawn yet shows as blank paper, or as its last
texture scaled while the new one is drawn. The paper themes recolour the page with a GSK
colour matrix: light is the page as it is, sepia maps white to the paper and black to its
ink, dark and black invert the lightness (keeping hues) onto their paper and ink.

Selecting: drag across text (one page at a time), double-click a word; the selection goes
to the window as 'selection' with the rects in the location ('page:3#x0,y0,x1,y1;…'), and
highlights are drawn from the locations the window sends. Links inside the PDF go there
(a jump: 'jumpedFrom'); links out open in the browser (http, https, mailto). A click on
the middle of the page toggles the window's bars; in paginated mode the left and right
thirds turn pages. An encrypted PDF asks for its password.
"""

import bisect
import collections
import logging
import re
import sys
import threading
import urllib.parse
from gettext import gettext as _

import gi

gi.require_version('Gtk', '4.0')
gi.require_version('Adw', '1')
gi.require_version('Gsk', '4.0')
gi.require_version('Graphene', '1.0')

from gi.repository import Adw, Gdk, Gio, GLib, GObject, Graphene, Gsk, Gtk  # noqa: E402

from .. import pdf_location  # noqa: E402

log = logging.getLogger(__name__)

try:
    gi.require_version('Poppler', '0.18')
    from gi.repository import Poppler
except (ImportError, ValueError):
    Poppler = None
try:
    import cairo
except ImportError:
    cairo = None

POINT = 96 / 72  # pixels per PDF point at 100%
ZOOM_STEPS = (0.25, 0.33, 0.5, 0.67, 0.8, 0.9, 1.0, 1.1, 1.25, 1.5, 1.75, 2.0, 2.5, 3.0, 4.0,
              5.0, 6.0)
AUTO_MAX = 1.25  # the automatic zoom fits the width up to 125% (the page, paginated)
MARGIN = 16  # around the pages, in pixels
GAP = 12  # between pages
CACHE_BYTES = 192 * 1024 * 1024
MAX_PIXELS = 36_000_000  # a page's texture at most; beyond, the last one is scaled
PRERENDER = 2  # pages drawn ahead of the reader, and behind
SEARCH_PAGES = 6  # pages searched per idle step
CLICK_DELAY_MS = 220
REPORT_DELAY_MS = 100  # relocated is sent when scrolling stops this long
CONTEXT = 40  # characters of a search result's context on each side
EXTERNAL_SCHEMES = ('http', 'https', 'mailto')
HIGHLIGHTS = {  # as reader.js's HIGHLIGHTS: [light paper, dark paper]
    'yellow': ('#f6d32d', '#c8a600'),
    'green': ('#57e389', '#26a269'),
    'blue': ('#62a0ea', '#3584e4'),
    'pink': ('#f66151', '#e01b24'),
    'purple': ('#c061cb', '#9141ac'),
}
# Hue rotation by 180 degrees (the CSS filter's matrix): with an inversion, it turns black
# text white and keeps a red figure red.
HUE_180 = ((-0.574, 1.430, 0.144), (0.426, 0.430, 0.144), (0.426, 1.430, -0.856))

if sys.byteorder == 'little':
    TEXTURE_FORMAT = Gdk.MemoryFormat.B8G8R8A8_PREMULTIPLIED
else:
    TEXTURE_FORMAT = Gdk.MemoryFormat.A8R8G8B8_PREMULTIPLIED


def available():
    """Whether PDFs can be shown: Poppler's typelib and pycairo imported."""
    return Poppler is not None and cairo is not None


def _rgba(color, alpha=None):
    rgba = Gdk.RGBA()
    if not rgba.parse(color):
        rgba.parse('#ffffff')
    if alpha is not None:
        rgba.alpha = alpha
    return rgba


def _rect(x, y, width, height):
    rect = Graphene.Rect()
    rect.init(x, y, width, height)
    return rect


def _channels(color):
    rgba = _rgba(color)
    return rgba.red, rgba.green, rgba.blue


def theme_matrix(theme):
    """(matrix rows, offset) of the recolouring for a theme dict ({'name', 'bg', 'fg',
    'dark'}): out = rows · in + offset per channel, or None for the page as it is."""
    if not theme or theme.get('name') in (None, 'light'):
        return None
    bg, fg = _channels(theme['bg']), _channels(theme['fg'])
    if not theme.get('dark'):
        # white -> paper, black -> ink
        rows = [[(bg[j] - fg[j]) if i == j else 0.0 for i in range(3)] for j in range(3)]
        return rows, list(fg)
    # out = bg + D·H·(1 - in), D = diag(fg - bg): white -> paper, black -> ink
    scale = [fg[j] - bg[j] for j in range(3)]
    rows = [[-scale[j] * HUE_180[j][i] for i in range(3)] for j in range(3)]
    offset = [bg[j] + scale[j] * sum(HUE_180[j]) for j in range(3)]
    return rows, offset


def _graphene_matrix(rows, offset):
    # GSK multiplies the colour as a row vector: values[4 * input + output].
    values = [0.0] * 16
    for out in range(3):
        for inp in range(3):
            values[4 * inp + out] = rows[out][inp]
    values[15] = 1.0
    matrix = Graphene.Matrix()
    matrix.init_from_float(values)
    vector = Graphene.Vec4()
    vector.init(offset[0], offset[1], offset[2], 0.0)
    return matrix, vector


def open_document(path, password=None):
    """A Poppler.Document; raises GLib.Error (Poppler.Error.ENCRYPTED for a password)."""
    return Poppler.Document.new_from_gfile(Gio.File.new_for_path(str(path)), password, None)


def is_encrypted_error(error):
    return (Poppler is not None and error.domain == GLib.quark_to_string(Poppler.error_quark())
            and error.code == int(Poppler.Error.ENCRYPTED))


def render_texture(page, scale):
    """A page drawn on white at `scale` pixels per point, as a Gdk.Texture."""
    width, height = page.get_size()
    pixels_w = max(1, round(width * scale))
    pixels_h = max(1, round(height * scale))
    surface = cairo.ImageSurface(cairo.FORMAT_ARGB32, pixels_w, pixels_h)
    context = cairo.Context(surface)
    context.set_source_rgb(1, 1, 1)
    context.paint()
    context.scale(pixels_w / width, pixels_h / height)
    page.render(context)
    surface.flush()
    data = GLib.Bytes.new(bytes(surface.get_data()))
    return Gdk.MemoryTexture.new(pixels_w, pixels_h, TEXTURE_FORMAT, data,
                                 surface.get_stride())


class _Renderer:
    """A thread drawing pages with its own document. want([(index, scale)]) replaces what
    it should draw next, in order; deliver(index, scale, texture) runs in the main loop."""

    def __init__(self, path, password, deliver):
        self._path = path
        self._password = password
        self._deliver = deliver
        self._condition = threading.Condition()
        self._wanted = []
        self._busy = None
        self._stopped = False
        self._thread = threading.Thread(target=self._run, name='bookcase-pdf', daemon=True)
        self._thread.start()

    def want(self, jobs):
        with self._condition:
            self._wanted = [job for job in jobs if job != self._busy]
            self._condition.notify()

    def stop(self):
        with self._condition:
            self._stopped = True
            self._wanted = []
            self._condition.notify()

    def _run(self):
        try:
            document = open_document(self._path, self._password)
        except GLib.Error as error:
            log.warning('the PDF renderer cannot open %s: %s', self._path, error.message)
            return
        while True:
            with self._condition:
                while not self._wanted and not self._stopped:
                    self._condition.wait()
                if self._stopped:
                    return
                job = self._busy = self._wanted.pop(0)
            index, scale = job
            try:
                texture = render_texture(document.get_page(index), scale)
            except Exception:  # noqa: BLE001  (a page that cannot be drawn stays blank)
                log.warning('drawing page %d', index + 1, exc_info=True)
                texture = None
            with self._condition:
                self._busy = None
                if self._stopped:
                    return
            GLib.idle_add(self._deliver, index, scale, texture)


class _Pages(Gtk.Widget, Gtk.Scrollable):
    """The pages: a scrollable widget that asks its PdfView to lay out and draw."""

    __gtype_name__ = 'BookcasePdfPages'

    hadjustment = GObject.Property(type=Gtk.Adjustment)
    vadjustment = GObject.Property(type=Gtk.Adjustment)
    hscroll_policy = GObject.Property(type=Gtk.ScrollablePolicy,
                                      default=Gtk.ScrollablePolicy.MINIMUM)
    vscroll_policy = GObject.Property(type=Gtk.ScrollablePolicy,
                                      default=Gtk.ScrollablePolicy.MINIMUM)

    def __init__(self, owner):
        super().__init__(hexpand=True, vexpand=True, focusable=False, overflow=Gtk.Overflow.HIDDEN)
        self._owner = owner.weak_ref()
        self._handlers = {}
        self._ref = self.weak_ref()
        self.connect('notify::hadjustment', _Pages._on_adjustment_set)
        self.connect('notify::vadjustment', _Pages._on_adjustment_set)

    def owner(self):
        return self._owner()

    def _on_adjustment_set(self, pspec):
        name = pspec.name
        old = self._handlers.pop(name, None)
        if old is not None:
            old[0].disconnect(old[1])
        adjustment = self.get_property(name)
        if adjustment is not None:
            handler = adjustment.connect('value-changed', _Pages._on_value_changed, self._ref)
            self._handlers[name] = (adjustment, handler)

    @staticmethod
    def _on_value_changed(_adjustment, ref):
        pages = ref()
        if pages is None:
            return
        pages.queue_draw()
        owner = pages.owner()
        if owner is not None:
            owner._on_scrolled()

    def do_measure(self, orientation, _for_size):
        return 0, 0, -1, -1

    def do_size_allocate(self, width, height, _baseline):
        owner = self.owner()
        if owner is not None:
            owner._on_allocate(width, height)

    def do_snapshot(self, snapshot):
        owner = self.owner()
        if owner is not None:
            owner._draw(snapshot, self.get_width(), self.get_height())


class PdfView(Adw.Bin):
    __gtype_name__ = 'BookcasePdfView'

    __gsignals__ = {
        'loaded': (GObject.SignalFlags.RUN_FIRST, None, (object,)),
        'toc-ready': (GObject.SignalFlags.RUN_FIRST, None, (object,)),
        'relocated': (GObject.SignalFlags.RUN_FIRST, None, (object,)),
        'selection': (GObject.SignalFlags.RUN_FIRST, None, (object,)),
        'annotation-activated': (GObject.SignalFlags.RUN_FIRST, None, (object,)),
        'search-result': (GObject.SignalFlags.RUN_FIRST, None, (object,)),
        'search-done': (GObject.SignalFlags.RUN_FIRST, None, (object,)),
        'history': (GObject.SignalFlags.RUN_FIRST, None, (object,)),
        'error': (GObject.SignalFlags.RUN_FIRST, None, (str,)),
        'toggle-chrome': (GObject.SignalFlags.RUN_FIRST, None, ()),
        'zoom-changed': (GObject.SignalFlags.RUN_FIRST, None, ()),
    }

    def __init__(self, **kwargs):
        super().__init__(vexpand=True, hexpand=True, **kwargs)
        self.add_css_class('reader-pdf')
        self._pages = _Pages(self)
        self._scrolled = Gtk.ScrolledWindow(child=self._pages, hexpand=True, vexpand=True)
        self._progress = Gtk.Label(halign=Gtk.Align.CENTER, valign=Gtk.Align.END,
                                   margin_bottom=10, visible=False, can_target=False)
        self._progress.add_css_class('reader-pdf-progress')
        self._progress.add_css_class('caption')
        self._progress.add_css_class('numeric')
        overlay = Gtk.Overlay(child=self._scrolled)
        overlay.add_overlay(self._progress)
        self.set_child(overlay)

        self._document = None
        self._path = None
        self._password = None
        self._renderer = None
        self._sizes = []  # (width, height) in points, per page
        self._count = 0
        self._style = None
        self._theme = None
        self._matrix = None
        self._continuous = True
        self._two_pages = False
        self._fit = 'auto'  # 'auto', 'width', 'page' or None (a percentage: self._scale)
        self._scale = POINT
        self._viewport = (0, 0)
        self._boxes = []  # (x, y, w, h) in content pixels per page laid out
        self._laid_out = []  # the page indexes in _boxes, in order
        self._content = (0, 0)
        self._spreads = []  # [[index]] in paginated mode
        self._spread = 0
        self._pending = None  # (page, offset) to show once laid out
        self._cache = collections.OrderedDict()  # index -> (scale, texture, bytes)
        self._cache_bytes = 0
        self._links = {}  # index -> [(rect, action)]
        self._layouts = collections.OrderedDict()  # index -> [char rects], a few pages
        self._annotations = {}  # index -> [(location, rects, color)]
        self._bookmarks = []
        self._hits = {}  # index -> [rects], the search's matches
        self._current_hit = None  # (index, rects)
        self._selection = None  # {'index', 'rects', 'text', 'start', 'end', 'style'}
        self._toc = []
        self._flat_toc = []  # [(page, offset, item)] in the outline's order
        self._sections = []  # the first page (1-based) of each top-level section
        self._history = []
        self._future = []
        self._jumped_from = None
        self._reason = 'scroll'
        self._report_source = 0
        self._search_source = 0
        self._search = None
        self._click_source = 0
        self._pointer = None
        self._zoom_start = None
        self._wheel = 0.0
        self._start = (None, None)
        self._annotation_list = []
        self._dragged = False
        self._drag_start = None
        self._zoom_anchor = None
        self.location = ''
        self.fraction = 0.0
        self.place = None
        self.toc = []
        self._build_controllers()

    # -- opening -------------------------------------------------------------------------

    def open(self, path, fmt='pdf', location=None, fraction=None, annotations=(),
             bookmarks=(), style=None, password=None):
        """Open the PDF at `path` at a location ('page:N…'), else a fraction, else page 1."""
        self.close()
        self._path = str(path)
        if style is not None:
            self.set_style(style)
        self._start = (location, fraction)
        self._annotation_list = list(annotations)
        self._bookmarks = list(bookmarks)
        if not available():
            self.emit('error', 'Poppler is not available')
            return
        try:
            document = open_document(self._path, password)
        except GLib.Error as error:
            if is_encrypted_error(error):
                GLib.idle_add(self._ask_password, password is not None)
                return
            log.warning('opening %s: %s', self._path, error.message)
            self.emit('error', error.message)
            return
        self._loaded(document, password)

    def _loaded(self, document, password):
        self._document = document
        self._password = password
        self._count = document.get_n_pages()
        if self._count == 0:
            self.emit('error', 'The PDF has no pages')
            return
        self._sizes = []
        for index in range(self._count):
            width, height = document.get_page(index).get_size()
            self._sizes.append((max(1.0, width), max(1.0, height)))
        self._renderer = _Renderer(self._path, password, _weak_method(self, PdfView._deliver))
        self.set_annotations(self._annotation_list)
        self._toc = self._read_outline()
        self.toc = self._toc
        location, fraction = self._start
        place = pdf_location.parse(location or '')
        if place is not None:
            self._pending = (min(place.page, self._count), place.offset)
        elif fraction:
            self._pending = pdf_location.from_fraction(fraction, self._count)
        else:
            self._pending = (1, 0.0)
        self._relayout()
        title = (document.props.title or '').strip()
        self.emit('loaded', {'title': title, 'dir': 'ltr', 'fixedLayout': True,
                             'sectionFractions': self._section_fractions(),
                             'toc': self._toc, 'pages': self._count})
        self.emit('toc-ready', self._toc)

    def _ask_password(self, retry):
        root = self.get_root()
        if root is None:
            return GLib.SOURCE_CONTINUE  # not in a window yet
        dialog = Adw.AlertDialog(
            heading=_('Password Needed'),
            body=_('The password was not right. Try again.') if retry
            else _('This PDF is locked with a password'))
        entry = Gtk.PasswordEntry(show_peek_icon=True, activates_default=True)
        entry.update_property([Gtk.AccessibleProperty.LABEL], [_('Password')])
        dialog.set_extra_child(entry)
        dialog.add_response('cancel', _('Cancel'))
        dialog.add_response('unlock', _('Unlock'))
        dialog.set_response_appearance('unlock', Adw.ResponseAppearance.SUGGESTED)
        dialog.set_default_response('unlock')
        dialog.set_close_response('cancel')
        ref = self.weak_ref()
        window = root if hasattr(root, 'set_dialog_open') else None

        def response(_dialog, answer):
            if window is not None:
                window.set_dialog_open(False)
            view = ref()
            if view is None:
                return
            if answer != 'unlock':
                view.emit('error', _('This PDF is locked with a password'))
                return
            view.open(view._path, 'pdf', *view._start, annotations=view._annotation_list,
                      bookmarks=view._bookmarks, password=entry.get_text())

        dialog.connect('response', response)
        if window is not None:
            window.set_dialog_open(True)
        dialog.present(self)
        entry.grab_focus()
        return GLib.SOURCE_REMOVE

    def close(self):
        """Stop drawing and forget the document."""
        if self._renderer is not None:
            self._renderer.stop()
            self._renderer = None
        for name in ('_report_source', '_search_source', '_click_source'):
            source = getattr(self, name)
            if source:
                GLib.source_remove(source)
                setattr(self, name, 0)
        self._document = None
        self._cache.clear()
        self._cache_bytes = 0
        self._links.clear()
        self._layouts.clear()
        self._hits.clear()

    def _read_outline(self):
        self._flat_toc = []
        try:
            iterator = Poppler.IndexIter.new(self._document)
        except (TypeError, GLib.Error):
            iterator = None
        toc = self._outline(iterator, 0) if iterator is not None else []
        self._sections = sorted({page for page, _offset, item in self._flat_toc
                                 if item.get('depth') == 0})
        for _page, _offset, item in self._flat_toc:
            item.pop('depth', None)
        return toc

    def _outline(self, iterator, depth):
        items = []
        while True:
            action = iterator.get_action()
            target = self._action_target(action)
            title = ''
            if action is not None and action.type == Poppler.ActionType.GOTO_DEST:
                title = action.goto_dest.title or ''
            if target is not None:
                page, offset = target
                item = {'label': ' '.join(title.split()) or _('Page {}').format(page),
                        'href': pdf_location.location(page, offset), 'subitems': [],
                        'depth': depth}
                self._flat_toc.append((page, offset, item))
                child = iterator.get_child()
                if child is not None:
                    item['subitems'] = self._outline(child, depth + 1)
                items.append(item)
            if not iterator.next():
                break
        return items

    def _action_target(self, action):
        """(page, offset) an action goes to, or None."""
        if action is None or action.type != Poppler.ActionType.GOTO_DEST:
            return None
        dest = action.goto_dest.dest
        if dest is None:
            return None
        if dest.type == Poppler.DestType.NAMED:
            dest = self._document.find_dest(dest.named_dest)
            if dest is None:
                return None
        page = dest.page_num
        if page < 1 or page > self._count:
            return None
        offset = 0.0
        if dest.change_top and dest.type in (Poppler.DestType.XYZ, Poppler.DestType.FITH,
                                             Poppler.DestType.FITBH):
            height = self._sizes[page - 1][1]
            offset = max(0.0, min(1.0, 1 - dest.top / height))
            if offset < 0.03:
                offset = 0.0
        return page, offset

    def _section_fractions(self):
        starts = [0.0] + [(page - 1) / self._count for page in self._sections if page > 1]
        return starts if len(starts) > 1 else []

    # -- the style -----------------------------------------------------------------------

    def set_style(self, style):
        self._style = style
        theme = style.get('theme') or {}
        if theme != self._theme:
            self._theme = theme
            matrix = theme_matrix(theme)
            self._matrix = _graphene_matrix(*matrix) if matrix else None
        continuous = style.get('flow', 'scrolled') == 'scrolled'
        two_pages = (style.get('maxColumns') or 1) > 1
        if (continuous, two_pages) != (self._continuous, self._two_pages):
            place = self._top_place()
            self._continuous, self._two_pages = continuous, two_pages
            if self._document is not None:
                self._pending = place
                self._relayout()
        self._pages.queue_draw()

    def _surround(self):
        """The colour around the pages."""
        theme = self._theme or {}
        bg = _rgba(theme.get('bg') or '#ffffff')
        if theme.get('name') == 'black':
            return _rgba('#000000')
        if theme.get('dark'):
            factor = 0.62
            return Gdk.RGBA(red=bg.red * factor, green=bg.green * factor,
                            blue=bg.blue * factor, alpha=1)
        factor = 0.9
        return Gdk.RGBA(red=bg.red * factor, green=bg.green * factor, blue=bg.blue * factor,
                        alpha=1)

    # -- zoom ----------------------------------------------------------------------------

    @property
    def fit(self):
        return self._fit

    @property
    def zoom_percent(self):
        return round(self._scale / POINT * 100)

    def _zoom_changed(self):
        self.emit('zoom-changed')
        return GLib.SOURCE_REMOVE

    def set_fit(self, fit):
        """'auto', 'width' or 'page'."""
        if self._fit == fit:
            return
        self._fit = fit
        self._relayout(keep=True)
        self.emit('zoom-changed')

    def zoom_in(self, anchor=None):
        self._step_zoom(1, anchor)

    def zoom_out(self, anchor=None):
        self._step_zoom(-1, anchor)

    def _step_zoom(self, direction, anchor):
        current = self._scale / POINT
        if direction > 0:
            level = next((z for z in ZOOM_STEPS if z > current * 1.01), ZOOM_STEPS[-1])
        else:
            level = next((z for z in reversed(ZOOM_STEPS) if z < current * 0.99),
                         ZOOM_STEPS[0])
        self.set_zoom(level * POINT, anchor if anchor is not None else self._pointer)

    def set_zoom(self, scale, anchor=None):
        """Zoom to `scale` pixels per point, keeping the point at `anchor` (view pixels; the
        middle of the view by default) where it is."""
        scale = max(ZOOM_STEPS[0] * POINT, min(ZOOM_STEPS[-1] * POINT, scale))
        if self._document is None:
            return
        width, height = self._viewport
        if anchor is None:
            anchor = (width / 2, height / 2)
        point = self._point_at(*anchor)
        self._fit = None
        self._scale = scale
        self._layout()
        if point is not None:
            index, fx, fy = point
            if index in self._laid_out:
                x, y, w, h = self._boxes[self._laid_out.index(index)]
                self._set_values(x + fx * w - anchor[0], y + fy * h - anchor[1])
        self._pages.queue_draw()
        self.emit('zoom-changed')

    # -- layout --------------------------------------------------------------------------

    def _on_allocate(self, width, height):
        if (width, height) == self._viewport:
            self._configure()
            return
        place = self._pending or self._top_place()
        self._viewport = (width, height)
        if self._document is None:
            return
        self._pending = place
        self._relayout()

    def _relayout(self, keep=False):
        """Lay the pages out again, keeping the place (or going to the pending one)."""
        if keep and self._pending is None:
            self._pending = self._top_place()
        width, height = self._viewport
        if self._document is None or width <= 0 or height <= 0:
            return
        place = self._pending or self._top_place()
        self._pending = None
        self._layout()
        if place is not None:
            self._show_place(*place)
        self._pages.queue_draw()
        self._schedule_report()

    def _fit_scale(self, pages):
        width, height = self._viewport
        max_w = max(sum(self._sizes[i][0] for i in group) + GAP * (len(group) - 1) / POINT
                    for group in pages)
        max_h = max(max(self._sizes[i][1] for i in group) for group in pages)
        fit_w = (width - 2 * MARGIN) / max_w
        fit_h = (height - 2 * MARGIN) / max_h
        if self._fit == 'page' or (self._fit == 'auto' and not self._continuous):
            return min(fit_w, fit_h)
        if self._fit == 'auto':
            return min(fit_w, AUTO_MAX * POINT)
        return fit_w

    def _wants_two(self):
        width, height = self._viewport
        return self._two_pages and not self._continuous and width > height * 1.05

    def _layout(self):
        """Place the pages (continuous: all of them; paginated: the spread shown), in
        content pixels, and size the content."""
        count = self._count
        if self._continuous:
            self._spreads = []
            groups = [[i] for i in range(count)]
        else:
            first = self._spreads[self._spread][0] if self._spreads else 0
            if self._wants_two():
                self._spreads = [[0]] + [list(range(i, min(i + 2, count)))
                                         for i in range(1, count, 2)]
            else:
                self._spreads = [[i] for i in range(count)]
            self._spread = next((n for n, group in enumerate(self._spreads) if first in group), 0)
            groups = self._spreads
        if self._fit is not None:
            # Fit on the widest pages of the book (continuous) or the spreads (paginated).
            sample = groups if len(groups) <= 50 else [groups[i] for i in range(0, len(groups),
                                                                               len(groups) // 50)]
            scale = max(ZOOM_STEPS[0] * POINT,
                        min(ZOOM_STEPS[-1] * POINT, self._fit_scale(sample)))
            if abs(scale - self._scale) > 1e-6:
                self._scale = scale
                GLib.idle_add(_weak_method(self, PdfView._zoom_changed))
        scale = self._scale
        width, height = self._viewport
        boxes, laid_out = [], []
        if self._continuous:
            content_w = max(w for w, _h in self._sizes) * scale + 2 * MARGIN
            content_w = max(content_w, width)
            y = MARGIN
            for index, (w, h) in enumerate(self._sizes):
                pw, ph = w * scale, h * scale
                boxes.append(((content_w - pw) / 2, y, pw, ph))
                laid_out.append(index)
                y += ph + GAP
            content_h = y - GAP + MARGIN
        else:
            group = self._spreads[self._spread]
            pw = [self._sizes[i][0] * scale for i in group]
            ph = [self._sizes[i][1] * scale for i in group]
            total = sum(pw) + GAP * (len(group) - 1)
            content_w = max(total + 2 * MARGIN, width)
            content_h = max(max(ph) + 2 * MARGIN, height)
            x = (content_w - total) / 2
            for index, w, h in zip(group, pw, ph, strict=True):
                boxes.append((x, (content_h - h) / 2, w, h))
                laid_out.append(index)
                x += w + GAP
        self._boxes, self._laid_out = boxes, laid_out
        self._content = (content_w, content_h)
        self._configure()

    def _configure(self):
        width, height = self._viewport
        content_w, content_h = self._content
        for adjustment, size, content in ((self._pages.props.hadjustment, width, content_w),
                                          (self._pages.props.vadjustment, height, content_h)):
            if adjustment is None:
                continue
            upper = max(size, content)
            value = max(0, min(adjustment.get_value(), upper - size))
            adjustment.configure(value, 0, upper, size * 0.05 + 24, size * 0.9, size)

    def _set_values(self, x, y):
        hadjustment = self._pages.props.hadjustment
        vadjustment = self._pages.props.vadjustment
        if hadjustment is not None and x is not None:
            hadjustment.set_value(max(0, min(x, hadjustment.get_upper()
                                             - hadjustment.get_page_size())))
        if vadjustment is not None and y is not None:
            vadjustment.set_value(max(0, min(y, vadjustment.get_upper()
                                             - vadjustment.get_page_size())))

    def _values(self):
        hadjustment = self._pages.props.hadjustment
        vadjustment = self._pages.props.vadjustment
        return (hadjustment.get_value() if hadjustment else 0,
                vadjustment.get_value() if vadjustment else 0)

    def _show_place(self, page, offset):
        """Scroll (or turn) to page `page` (1-based), `offset` down it."""
        index = max(0, min(self._count - 1, page - 1))
        if not self._continuous:
            spread = next((n for n, group in enumerate(self._spreads) if index in group), 0)
            if spread != self._spread:
                self._spread = spread
                self._layout()
            box = self._boxes[self._laid_out.index(index)]
            self._set_values(None, box[1] + offset * box[3] - MARGIN if offset else 0)
            self._pages.queue_draw()
            return
        x, y, w, h = self._boxes[index]
        top = y + offset * h if offset else y - MARGIN / 2
        content_w = self._content[0]
        self._set_values((content_w - self._viewport[0]) / 2, top)

    def _point_at(self, x, y):
        """(index, fx, fy): the page under a point of the view and where on it (0-1), or
        None between pages."""
        hvalue, vvalue = self._values()
        cx, cy = x + hvalue, y + vvalue
        for index, (bx, by, bw, bh) in zip(self._laid_out, self._boxes, strict=True):
            if bx <= cx <= bx + bw and by <= cy <= by + bh:
                return index, (cx - bx) / bw, (cy - by) / bh
        return None

    def _visible(self):
        """The laid-out pages on screen: [(index, (x, y, w, h) in view pixels)]."""
        hvalue, vvalue = self._values()
        width, height = self._viewport
        shown = []
        if self._continuous and self._boxes:
            tops = [box[1] for box in self._boxes]
            start = max(0, bisect.bisect_right(tops, vvalue) - 1)
            for index in range(start, self._count):
                x, y, w, h = self._boxes[index]
                if y > vvalue + height:
                    break
                if y + h >= vvalue:
                    shown.append((index, (x - hvalue, y - vvalue, w, h)))
            return shown
        for index, (x, y, w, h) in zip(self._laid_out, self._boxes, strict=True):
            shown.append((index, (x - hvalue, y - vvalue, w, h)))
        return shown

    def _top_place(self):
        """(page, offset) at the top of the view, or None before the first layout."""
        if not self._boxes:
            return self._pending
        if not self._continuous:
            group = self._spreads[self._spread] if self._spreads else [0]
            return group[0] + 1, 0.0
        _hvalue, vvalue = self._values()
        tops = [box[1] for box in self._boxes]
        index = max(0, bisect.bisect_right(tops, vvalue) - 1)
        _x, y, _w, h = self._boxes[index]
        if vvalue >= y + h and index + 1 < self._count:
            return index + 2, 0.0  # in the gap: the next page
        offset = (vvalue - y) / h
        return index + 1, (0.0 if offset < 0.002 else min(1.0, offset))

    def _current_page(self):
        """The page the reader is on (1-based): the one a third of the way down the view."""
        if not self._boxes:
            return (self._pending or (1, 0.0))[0]
        if not self._continuous:
            group = self._spreads[self._spread] if self._spreads else [0]
            return group[0] + 1
        _hvalue, vvalue = self._values()
        tops = [box[1] for box in self._boxes]
        index = bisect.bisect_right(tops, vvalue + self._viewport[1] / 3) - 1
        return max(0, min(self._count - 1, index)) + 1

    # -- moving --------------------------------------------------------------------------

    def _at_end(self):
        vadjustment = self._pages.props.vadjustment
        bottom = vadjustment is None or (vadjustment.get_value() + vadjustment.get_page_size()
                                         >= vadjustment.get_upper() - 1)
        if self._continuous:
            return bottom
        return bottom and self._spread >= len(self._spreads) - 1

    def _at_start(self):
        vadjustment = self._pages.props.vadjustment
        top = vadjustment is None or vadjustment.get_value() <= 0.5
        return top and (self._continuous or self._spread == 0)

    def _turn(self, step):
        """Paginated: the next or previous spread."""
        spread = max(0, min(len(self._spreads) - 1, self._spread + step))
        if spread == self._spread:
            return False
        self._spread = spread
        self._reason = 'page'
        self._layout()
        self._set_values(None, 0 if step > 0 else self._content[1])
        self._pages.queue_draw()
        self._schedule_report()
        return True

    def _scroll_by(self, pixels):
        vadjustment = self._pages.props.vadjustment
        if vadjustment is None:
            return False
        before = vadjustment.get_value()
        self._set_values(None, before + pixels)
        return vadjustment.get_value() != before

    def next(self):
        if self._document is None:
            return
        height = self._viewport[1]
        self._reason = 'page'
        if not self._scroll_by(height - 48) and not self._continuous:
            self._turn(1)

    def prev(self):
        if self._document is None:
            return
        height = self._viewport[1]
        self._reason = 'page'
        if not self._scroll_by(-(height - 48)) and not self._continuous:
            self._turn(-1)

    def go_right(self):
        if self._document is None:
            return
        if not self._continuous:
            self._turn(1)
            return
        page = self._current_page()
        if page < self._count:
            self._reason = 'page'
            self._show_place(page + 1, 0.0)

    def go_left(self):
        if self._document is None:
            return
        if not self._continuous:
            self._turn(-1)
            return
        page, offset = self._top_place() or (1, 0.0)
        target = page if offset > 0.02 else page - 1
        self._reason = 'page'
        self._show_place(max(1, target), 0.0)

    def scroll(self, direction):
        if self._document is None:
            return
        self._reason = 'scroll'
        if not self._scroll_by(direction * 60) and not self._continuous:
            self._turn(1 if direction > 0 else -1)

    def start(self):
        self._jump(1, 0.0)

    def end(self):
        if self._document is None:
            return
        self._jump_to(lambda: (self._show_place(self._count, 0.0),
                               self._set_values(None, self._content[1])))

    def next_section(self):
        page = self._current_page()
        starts = sorted({p for p, _o, _i in self._flat_toc})
        target = next((p for p in starts if p > page), None)
        if target is None:
            target = min(self._count, page + 1) if not starts else None
        if target is not None:
            self._jump(target, 0.0)

    def prev_section(self):
        page = self._current_page()
        starts = sorted({p for p, _o, _i in self._flat_toc})
        top = self._top_place() or (page, 0.0)
        target = next((p for p in reversed(starts)
                       if p < page or (p == page and top[1] > 0.05)), None)
        if target is None and not starts:
            target = max(1, page - 1)
        if target is not None:
            self._jump(target, 0.0)

    def go_to(self, target, callback=None):
        """Go to a location ('page:N', with an offset or rects): a jump."""
        place = pdf_location.parse(target or '')
        if place is None or self._document is None:
            if callback is not None:
                callback(None)
            return
        if place.rects:
            self._jump_to(lambda: self._reveal(place.page - 1, place.rects))
        else:
            self._jump(place.page, place.offset)
        if callback is not None:
            callback(True)

    def go_to_fraction(self, fraction):
        if self._document is None:
            return
        self._jump(*pdf_location.from_fraction(fraction, self._count))

    def _jump(self, page, offset):
        self._jump_to(lambda: self._show_place(min(page, self._count), offset))

    def _jump_to(self, move):
        if self._document is None:
            return
        here = self._location()
        move()
        if here and here != self._location():
            self._history.append(here)
            self._future = []
            self._jumped_from = here
            self._emit_history()
        self._reason = 'jump'
        self._schedule_report()

    def _reveal(self, index, rects):
        """Show rects of a page (a search match, a highlight) a third of the way down."""
        page = index + 1
        if not self._continuous:
            self._show_place(page, 0.0)
        if index not in self._laid_out:
            return
        x, y, w, h = self._boxes[self._laid_out.index(index)]
        scale = w / self._sizes[index][0]
        top = min(r[1] for r in rects) * scale + y
        left = min(r[0] for r in rects) * scale + x
        width, height = self._viewport
        hvalue, vvalue = self._values()
        new_y = None if vvalue + 24 <= top <= vvalue + height - 48 else top - height / 3
        new_x = None if hvalue <= left <= hvalue + width - 48 else left - width / 3
        self._set_values(new_x, new_y)
        self._pages.queue_draw()

    def back(self):
        if not self._history:
            return
        here = self._location()
        target = self._history.pop()
        if here:
            self._future.append(here)
        place = pdf_location.parse(target)
        if place is not None:
            self._show_place(place.page, place.offset)
        self._reason = 'jump'
        self._emit_history()
        self._schedule_report()

    def forward(self):
        if not self._future:
            return
        here = self._location()
        target = self._future.pop()
        if here:
            self._history.append(here)
        place = pdf_location.parse(target)
        if place is not None:
            self._show_place(place.page, place.offset)
        self._reason = 'jump'
        self._emit_history()
        self._schedule_report()

    def _emit_history(self):
        self.emit('history', {'canGoBack': bool(self._history),
                              'canGoForward': bool(self._future)})

    # -- where the reader is -------------------------------------------------------------

    def _location(self):
        place = self._top_place()
        return pdf_location.location(*place) if place else ''

    def _on_scrolled(self):
        self._schedule_report()

    def _schedule_report(self):
        """Say where the reader is once the view stops moving."""
        if self._document is None:
            return
        if self._report_source:
            GLib.source_remove(self._report_source)
        self._report_source = GLib.timeout_add(REPORT_DELAY_MS,
                                               _weak_method(self, PdfView._report))

    def _chapter(self, page):
        chapter = None
        for start, _offset, item in self._flat_toc:
            if start <= page and (chapter is None or start >= chapter[0]):
                chapter = (start, item)
        return chapter[1] if chapter else None

    def _report(self):
        self._report_source = 0
        if self._document is None or not self._boxes:
            return GLib.SOURCE_REMOVE
        place = self._top_place()
        page = self._current_page()
        at_end = self._at_end()
        fraction = 1.0 if at_end and page >= self._count - 1 else \
            pdf_location.fraction(place[0], place[1], self._count)
        location = pdf_location.location(*place)
        chapter = self._chapter(page)
        section = bisect.bisect_right(self._sections, page) - 1 if self._sections else None
        if section is not None and self._sections and self._sections[0] > 1:
            section += 1  # the pages before the first section are section 0
        bookmark = next((b for b in self._bookmarks
                         if (p := pdf_location.parse(b)) is not None and p.page == page), None)
        message = {
            'fraction': fraction, 'cfi': location, 'start': pdf_location.location(page),
            'reason': self._reason,
            'chapter': {'label': chapter['label'], 'href': chapter['href']} if chapter else None,
            'page': page, 'pages': self._count,
            'section': {'current': max(0, section), 'total': len(self._sections)}
            if section is not None else None,
            'location': {'current': page - 1, 'total': self._count},
            'atStart': self._at_start(), 'atEnd': at_end, 'bookmark': bookmark,
            'jumpedFrom': self._jumped_from,
            'canGoBack': bool(self._history), 'canGoForward': bool(self._future),
        }
        self._jumped_from = None
        self._reason = 'scroll'
        if self.place is not None and all(self.place.get(k) == message.get(k) for k in (
                'cfi', 'page', 'atEnd', 'bookmark')) and not message['jumpedFrom']:
            return GLib.SOURCE_REMOVE
        self.place = message
        self.location = location
        self.fraction = fraction
        self._progress.set_label(_('{page} of {total}').format(page=page, total=self._count))
        self.emit('relocated', message)
        return GLib.SOURCE_REMOVE

    def show_progress(self, visible):
        self._progress.set_visible(bool(visible) and self._document is not None)

    def get_toc(self, callback):
        callback(self._toc)

    # -- drawing -------------------------------------------------------------------------

    def _render_scale(self):
        native = self._pages.get_native()
        factor = self._pages.get_scale_factor()
        surface = native.get_surface() if native is not None else None
        if surface is not None and hasattr(surface, 'get_scale'):
            factor = surface.get_scale()
        return self._scale * max(1.0, factor)

    def _draw(self, snapshot, width, height):
        snapshot.append_color(self._surround(), _rect(0, 0, width, height))
        if self._document is None:
            return
        target = self._render_scale()
        shown = self._visible()
        dark = bool((self._theme or {}).get('dark'))
        paper = _rgba('#ffffff')
        shadow = _rgba('#000000', 0.5 if dark else 0.18)
        for index, (x, y, w, h) in shown:
            rect = _rect(x, y, w, h)
            outline = Gsk.RoundedRect()
            outline.init_from_rect(rect, 0)
            snapshot.append_outset_shadow(outline, shadow, 0, 1, 0, 4)
            # The marks blend with the page as the EPUB's highlights do (multiply on light
            # paper, screen on dark), so the text under them keeps its colour.
            marked = self._has_marks(index)
            if marked:
                snapshot.push_blend(Gsk.BlendMode.SCREEN if dark else Gsk.BlendMode.MULTIPLY)
            if self._matrix is not None:
                snapshot.push_color_matrix(*self._matrix)
            cached = self._cache.get(index)
            if cached is not None:
                self._cache.move_to_end(index)
                snapshot.append_color(paper, rect)
                snapshot.append_scaled_texture(cached[1], Gsk.ScalingFilter.LINEAR, rect)
            else:
                snapshot.append_color(paper, rect)
            if self._matrix is not None:
                snapshot.pop()
            if marked:
                snapshot.pop()  # the bottom: the page; the top: its marks
                self._draw_marks(snapshot, index, x, y, w / self._sizes[index][0], dark)
                snapshot.pop()
        self._request(shown, target)

    def _has_marks(self, index):
        selection = self._selection
        return bool(self._annotations.get(index) or self._hits.get(index)
                    or (self._current_hit is not None and self._current_hit[0] == index)
                    or (selection is not None and selection['index'] == index
                        and selection['rects']))

    def _draw_marks(self, snapshot, index, x, y, scale, dark):
        def boxes(rects, color):
            for x0, y0, x1, y1 in rects:
                snapshot.append_color(color, _rect(x + x0 * scale, y + y0 * scale,
                                                   (x1 - x0) * scale, (y1 - y0) * scale))

        for _location, rects, color in self._annotations.get(index, ()):
            shades = HIGHLIGHTS.get(color, HIGHLIGHTS['yellow'])
            boxes(rects, _rgba(shades[1 if dark else 0], 0.45 if dark else 0.4))
        hits = self._hits.get(index)
        if hits:
            boxes(hits, _rgba('#f6d32d' if not dark else '#c8a600', 0.35))
        if self._current_hit is not None and self._current_hit[0] == index:
            boxes(self._current_hit[1], _rgba('#ff7800', 0.5))
        selection = self._selection
        if selection is not None and selection['index'] == index and selection['rects']:
            accent = Adw.StyleManager.get_default().get_accent_color_rgba()
            accent.alpha = 0.35
            boxes(selection['rects'], accent)

    def _request(self, shown, target):
        """Ask the renderer for the pages on screen (then those around them) at `target`
        pixels per point, unless their texture already is."""
        if self._renderer is None or self._zoom_start is not None:
            return
        indexes = [index for index, _box in shown]
        if indexes:
            ahead = range(indexes[-1] + 1, min(self._count, indexes[-1] + 1 + PRERENDER))
            behind = range(max(0, indexes[0] - PRERENDER), indexes[0])
            if not self._continuous and self._spreads:
                ahead = [i for n in range(self._spread + 1,
                                          min(len(self._spreads), self._spread + 2))
                         for i in self._spreads[n]]
                behind = [i for n in range(max(0, self._spread - 1), self._spread)
                          for i in self._spreads[n]]
            indexes += list(ahead) + list(behind)
        jobs = []
        for index in indexes:
            scale = self._clamped(index, target)
            cached = self._cache.get(index)
            if cached is None or abs(cached[0] - scale) > 1e-3:
                jobs.append((index, scale))
        self._renderer.want(jobs)

    def _clamped(self, index, scale):
        width, height = self._sizes[index]
        if width * height * scale * scale > MAX_PIXELS:
            scale = (MAX_PIXELS / (width * height)) ** 0.5
        return round(scale, 3)

    def _deliver(self, index, scale, texture):
        if self._document is None or texture is None:
            return GLib.SOURCE_REMOVE
        size = texture.get_width() * texture.get_height() * 4
        old = self._cache.pop(index, None)
        if old is not None:
            self._cache_bytes -= old[2]
        self._cache[index] = (scale, texture, size)
        self._cache_bytes += size
        shown = {i for i, _box in self._visible()}
        for key in list(self._cache):
            if self._cache_bytes <= CACHE_BYTES:
                break
            if key in shown or key == index:
                continue
            self._cache_bytes -= self._cache.pop(key)[2]
        if index in shown:
            self._pages.queue_draw()
        return GLib.SOURCE_REMOVE

    # -- the pointer ---------------------------------------------------------------------

    def _build_controllers(self):
        pages = self._pages
        click = Gtk.GestureClick(button=Gdk.BUTTON_PRIMARY)
        click.connect('pressed', _weak_method(self, PdfView._on_pressed))
        click.connect('released', _weak_method(self, PdfView._on_released))
        pages.add_controller(click)
        drag = Gtk.GestureDrag(button=Gdk.BUTTON_PRIMARY)
        drag.connect('drag-begin', _weak_method(self, PdfView._on_drag_begin))
        drag.connect('drag-update', _weak_method(self, PdfView._on_drag_update))
        drag.connect('drag-end', _weak_method(self, PdfView._on_drag_end))
        pages.add_controller(drag)
        motion = Gtk.EventControllerMotion()
        motion.connect('motion', _weak_method(self, PdfView._on_motion))
        motion.connect('leave', _weak_method(self, PdfView._on_leave))
        pages.add_controller(motion)
        zoom = Gtk.GestureZoom()
        zoom.connect('begin', _weak_method(self, PdfView._on_zoom_begin))
        zoom.connect('scale-changed', _weak_method(self, PdfView._on_zoom_scale))
        zoom.connect('end', _weak_method(self, PdfView._on_zoom_end))
        pages.add_controller(zoom)
        wheel = Gtk.EventControllerScroll(flags=Gtk.EventControllerScrollFlags.VERTICAL)
        wheel.connect('scroll', _weak_method(self, PdfView._on_wheel))
        pages.add_controller(wheel)

    def _page_point(self, x, y, clamp_index=None):
        """(index, px, py) in PDF points for a point of the view; with clamp_index, the
        point is held to that page's edges."""
        if clamp_index is not None:
            if clamp_index not in self._laid_out:
                return None
            hvalue, vvalue = self._values()
            bx, by, bw, bh = self._boxes[self._laid_out.index(clamp_index)]
            fx = max(0.0, min(1.0, (x + hvalue - bx) / bw))
            fy = max(0.0, min(1.0, (y + vvalue - by) / bh))
            index = clamp_index
        else:
            point = self._point_at(x, y)
            if point is None:
                return None
            index, fx, fy = point
        width, height = self._sizes[index]
        return index, fx * width, fy * height

    def _view_rect(self, index, rects):
        """A Gdk.Rectangle-like dict around rects of a page, in the view's pixels."""
        if index not in self._laid_out or not rects:
            return None
        hvalue, vvalue = self._values()
        x, y, w, _h = self._boxes[self._laid_out.index(index)]
        scale = w / self._sizes[index][0]
        x0 = min(r[0] for r in rects) * scale + x - hvalue
        y0 = min(r[1] for r in rects) * scale + y - vvalue
        x1 = max(r[2] for r in rects) * scale + x - hvalue
        y1 = max(r[3] for r in rects) * scale + y - vvalue
        return {'x': x0, 'y': y0, 'width': x1 - x0, 'height': y1 - y0}

    def _on_pressed(self, gesture, n_press, x, y):
        self._press = (x, y)
        self._dragged = False
        if n_press == 2:
            self._cancel_click()
            point = self._page_point(x, y)
            if point is not None:
                self._select(point[0], (point[1], point[2]), (point[1], point[2]),
                             Poppler.SelectionStyle.WORD)
                self._selection_done()
                self._dragged = True

    def _on_released(self, gesture, n_press, x, y):
        if self._dragged or n_press != 1:
            return
        point = self._page_point(x, y)
        if point is not None:
            link = self._link_at(*point)
            if link is not None:
                self._follow(link)
                return
            annotation = self._annotation_at(*point)
            if annotation is not None:
                location, rects = annotation
                self.emit('annotation-activated',
                          {'cfi': location, 'rect': self._view_rect(point[0], rects)})
                return
        if self._selection is not None:
            self.clear_selection()
            self.emit('selection', None)
            return
        self._cancel_click()
        self._click_source = GLib.timeout_add(CLICK_DELAY_MS,
                                              _weak_method(self, PdfView._on_click, x))

    def _cancel_click(self):
        if self._click_source:
            GLib.source_remove(self._click_source)
            self._click_source = 0

    def _on_click(self, x):
        self._click_source = 0
        width = self._viewport[0]
        if not self._continuous and x < width / 3:
            self._turn(-1)
        elif not self._continuous and x > width * 2 / 3:
            self._turn(1)
        else:
            self.emit('toggle-chrome')
        return GLib.SOURCE_REMOVE

    def _on_drag_begin(self, gesture, x, y):
        point = self._page_point(x, y)
        self._drag_start = point
        self._dragged = False

    def _on_drag_update(self, gesture, dx, dy):
        start = self._drag_start
        if start is None:
            return
        if not self._dragged and dx * dx + dy * dy < 16:
            return
        self._dragged = True
        self._cancel_click()
        ok, x, y = gesture.get_start_point()
        end = self._page_point(x + dx, y + dy, clamp_index=start[0])
        if end is None:
            return
        self._select(start[0], (start[1], start[2]), (end[1], end[2]),
                     Poppler.SelectionStyle.GLYPH)

    def _on_drag_end(self, gesture, dx, dy):
        if self._dragged and self._drag_start is not None:
            self._selection_done()
        self._drag_start = None

    def _select(self, index, start, end, style):
        page = self._document.get_page(index)
        area = Poppler.Rectangle()
        area.x1, area.y1 = start
        area.x2, area.y2 = end
        precision = 8.0
        region = page.get_selected_region(precision, style, area)
        rects = []
        if region is not None:
            for n in range(region.num_rectangles()):
                r = region.get_rectangle(n)
                rects.append((r.x / precision, r.y / precision, (r.x + r.width) / precision,
                              (r.y + r.height) / precision))
        text = page.get_selected_text(style, area) or '' if rects else ''
        self._selection = {'index': index, 'rects': _merge_lines(rects), 'text': text}
        self._pages.queue_draw()

    def _selection_done(self):
        selection = self._selection
        if selection is None or not selection['rects'] or not selection['text'].strip():
            self._selection = None
            self._pages.queue_draw()
            return
        index = selection['index']
        rects = selection['rects']
        height = self._sizes[index][1]
        fraction = pdf_location.fraction(index + 1, min(r[1] for r in rects) / height,
                                         self._count)
        self.emit('selection', {
            'cfi': pdf_location.location(index + 1, rects=rects),
            'text': ' '.join(selection['text'].split()),
            'rect': self._view_rect(index, rects), 'fraction': fraction})

    def selected_text(self):
        return ' '.join(self._selection['text'].split()) if self._selection else ''

    def clear_selection(self):
        if self._selection is not None:
            self._selection = None
            self._pages.queue_draw()

    def _links_of(self, index):
        links = self._links.get(index)
        if links is None:
            height = self._sizes[index][1]
            links = []
            for mapping in self._document.get_page(index).get_link_mapping():
                area = mapping.area
                action = mapping.action
                kind = action.type
                if kind == Poppler.ActionType.URI:
                    target = ('uri', action.uri.uri or '')
                elif kind == Poppler.ActionType.GOTO_DEST:
                    place = self._action_target(action)
                    target = ('page', place) if place else None
                else:
                    target = None
                if target is not None:
                    links.append(((min(area.x1, area.x2), height - max(area.y1, area.y2),
                                   max(area.x1, area.x2), height - min(area.y1, area.y2)),
                                  target))
            self._links[index] = links
        return links

    def _link_at(self, index, px, py):
        for (x0, y0, x1, y1), target in self._links_of(index):
            if x0 <= px <= x1 and y0 <= py <= y1:
                return target
        return None

    def _follow(self, link):
        kind, target = link
        if kind == 'page':
            self._jump(*target)
            return
        scheme = urllib.parse.urlsplit(target).scheme.lower()
        if scheme not in EXTERNAL_SCHEMES:
            log.info('not opening a link to %s', target)
            return
        root = self.get_root()
        Gtk.UriLauncher.new(target).launch(root if isinstance(root, Gtk.Window) else None,
                                           None, None, None)

    def _annotation_at(self, index, px, py):
        for location, rects, _color in self._annotations.get(index, ()):
            for x0, y0, x1, y1 in rects:
                if x0 - 1 <= px <= x1 + 1 and y0 - 1 <= py <= y1 + 1:
                    return location, rects
        return None

    def _text_at(self, index, px, py):
        layout = self._layouts.get(index)
        if layout is None:
            ok, rects = self._document.get_page(index).get_text_layout()
            layout = [(r.x1, r.y1, r.x2, r.y2) for r in rects] if ok and rects else []
            self._layouts[index] = layout
            while len(self._layouts) > 6:
                self._layouts.popitem(last=False)
        return any(x0 <= px <= x1 and y0 <= py <= y1 for x0, y0, x1, y1 in layout)

    def _on_motion(self, _controller, x, y):
        self._pointer = (x, y)
        if self._document is None:
            return
        point = self._page_point(x, y)
        cursor = None
        if point is not None:
            if self._link_at(*point) is not None or self._annotation_at(*point) is not None:
                cursor = 'pointer'
            elif self._text_at(*point):
                cursor = 'text'
        self._pages.set_cursor_from_name(cursor)

    def _on_leave(self, _controller):
        self._pointer = None

    def _on_zoom_begin(self, gesture, _sequence):
        self._zoom_start = self._scale
        ok, x, y = gesture.get_bounding_box_center()
        self._zoom_anchor = (x, y) if ok else None

    def _on_zoom_scale(self, gesture, scale):
        if self._zoom_start is not None:
            self.set_zoom(self._zoom_start * scale, self._zoom_anchor)

    def _on_zoom_end(self, gesture, _sequence):
        self._zoom_start = None
        self._pages.queue_draw()

    def _on_wheel(self, controller, _dx, dy):
        """Paginated: the wheel turns the page when the spread does not scroll."""
        if self._continuous or self._document is None:
            return False
        state = controller.get_current_event_state()
        if state & Gdk.ModifierType.CONTROL_MASK:
            return False
        vadjustment = self._pages.props.vadjustment
        scrollable = vadjustment.get_upper() > vadjustment.get_page_size() + 1
        if scrollable:
            value = vadjustment.get_value()
            if (dy < 0 and value > 0) or (dy > 0 and value + vadjustment.get_page_size()
                                          < vadjustment.get_upper() - 1):
                return False
        if controller.get_unit() == Gdk.ScrollUnit.WHEEL:
            self._turn(1 if dy > 0 else -1)
            return True
        self._wheel += dy
        if abs(self._wheel) > 60:
            self._turn(1 if self._wheel > 0 else -1)
            self._wheel = 0.0
        return True

    # -- search --------------------------------------------------------------------------

    def search(self, text):
        self.clear_search()
        query = (text or '').strip()
        if not query or self._document is None:
            return
        words = [re.escape(word) for word in query.split()]
        pattern = re.compile(r'[^\S\n]+'.join(words), re.I)
        self._search = {'query': query, 'pattern': pattern, 'index': 0, 'count': 0}
        self._search_source = GLib.idle_add(_weak_method(self, PdfView._search_step))

    def _search_step(self):
        search = self._search
        if search is None or self._document is None:
            self._search_source = 0
            return GLib.SOURCE_REMOVE
        stop = min(self._count, search['index'] + SEARCH_PAGES)
        for index in range(search['index'], stop):
            self._search_page(index, search)
        search['index'] = stop
        if stop < self._count:
            return GLib.SOURCE_CONTINUE
        self._search_source = 0
        self.emit('search-done', {'query': search['query'], 'count': search['count']})
        return GLib.SOURCE_REMOVE

    def _search_page(self, index, search):
        page = self._document.get_page(index)
        found = page.find_text_with_options(search['query'], Poppler.FindFlags.DEFAULT)
        if not found:
            return
        height = self._sizes[index][1]
        rects = [(min(r.x1, r.x2), height - max(r.y1, r.y2), max(r.x1, r.x2),
                  height - min(r.y1, r.y2)) for r in found]
        self._hits[index] = rects
        text = page.get_text() or ''
        matches = list(search['pattern'].finditer(text))
        items = []
        for number, rect in enumerate(rects):
            if number < len(matches):
                match = matches[number]
                pre = ' '.join(text[max(0, match.start() - CONTEXT):match.start()].split())
                post = ' '.join(text[match.end():match.end() + CONTEXT].split())
                word = ' '.join(match.group(0).split())
                pre = ('…' + pre) if match.start() > CONTEXT else pre
                post = (post + '…') if match.end() + CONTEXT < len(text) else post
                items.append({'cfi': pdf_location.location(index + 1, rects=[rect]),
                              'pre': pre + (' ' if pre and text[match.start() - 1].isspace()
                                            else ''),
                              'match': word,
                              'post': (' ' if post and match.end() < len(text)
                                       and text[match.end()].isspace() else '') + post})
            else:
                items.append({'cfi': pdf_location.location(index + 1, rects=[rect]),
                              'pre': '', 'match': search['query'], 'post': ''})
        search['count'] += len(items)
        chapter = self._chapter(index + 1)
        label = _('Page {}').format(index + 1)
        if chapter is not None:
            label = f'{chapter["label"]} · {label}'
        self.emit('search-result', {'label': label, 'items': items})
        if self._visible_index(index):
            self._pages.queue_draw()

    def _visible_index(self, index):
        return any(i == index for i, _box in self._visible())

    def select(self, location):
        """Go to a search match (or any location with rects) and mark it."""
        place = pdf_location.parse(location or '')
        if place is None:
            return
        self._current_hit = (place.page - 1, place.rects) if place.rects else None
        self.go_to(location)

    def clear_search(self):
        if self._search_source:
            GLib.source_remove(self._search_source)
            self._search_source = 0
        self._search = None
        self._hits = {}
        self._current_hit = None
        self._pages.queue_draw()

    # -- highlights and bookmarks --------------------------------------------------------

    def set_annotations(self, annotations):
        self._annotation_list = list(annotations)
        marks = {}
        for annotation in self._annotation_list:
            place = pdf_location.parse(annotation.get('cfi') or '')
            if place is None or not place.rects:
                continue
            marks.setdefault(place.page - 1, []).append(
                (annotation['cfi'], place.rects, annotation.get('color') or 'yellow'))
        self._annotations = marks
        self._pages.queue_draw()

    def add_annotation(self, cfi, color='yellow'):
        self.set_annotations(self._annotation_list + [{'cfi': cfi, 'color': color}])

    def remove_annotation(self, cfi):
        self.set_annotations([a for a in self._annotation_list if a.get('cfi') != cfi])

    def set_bookmarks(self, locations, callback=None):
        self._bookmarks = list(locations)
        page = self._current_page() if self._boxes else None
        on_page = next((b for b in self._bookmarks
                        if (p := pdf_location.parse(b)) is not None and p.page == page), None)
        if self.place is not None:
            self.place['bookmark'] = on_page
        if callback is not None:
            callback(on_page)


def _merge_lines(rects):
    """A selection's glyph boxes joined into one box per line."""
    lines = []
    for x0, y0, x1, y1 in sorted(rects, key=lambda r: (round(r[1], 0), r[0])):
        if lines:
            lx0, ly0, lx1, ly1 = lines[-1]
            overlap = min(y1, ly1) - max(y0, ly0)
            if overlap > 0.5 * min(y1 - y0, ly1 - ly0) and x0 <= lx1 + 4:
                lines[-1] = (min(lx0, x0), min(ly0, y0), max(lx1, x1), max(ly1, y1))
                continue
        lines.append((x0, y0, x1, y1))
    return [tuple(round(v, 1) for v in line) for line in lines]


def _weak_method(obj, function):
    """A callback calling function(obj, *args) while obj lives, without keeping it alive."""
    ref = obj.weak_ref()

    def call(*args):
        instance = ref()
        if instance is None:
            return False
        return function(instance, *args)

    return call
