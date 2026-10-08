# SPDX-License-Identifier: GPL-2.0-or-later
# SPDX-FileCopyrightText: 2026 Jack Tully

"""The book: a WebKit.WebView running the reader page (src/reader/reader.html, our bridge
over the vendored foliate-js) on the bookcase:// scheme.

    view = BookView()                       # BookView.available(): False without WebKit
    view.open(path, fmt, location=None, fraction=None, annotations=(), bookmarks=(),
              style=None)                   # annotations: [{'cfi', 'color'}]; bookmarks: [cfi]
    view.set_style(style)                   # reading.build_style()'s dict, applied live
    view.next() / prev() / go_left() / go_right() / scroll(direction)
    view.start() / end() / next_section() / prev_section() / back() / forward()
    view.go_to(target) / go_to_fraction(f)  # target: a CFI or an href
    view.select(cfi)                        # go to a search result and select it
    view.clear_selection()
    view.search(text) / clear_search()      # results come as 'search-result' signals
    view.set_annotations(list) / add_annotation(cfi, color) / remove_annotation(cfi)
    view.set_bookmarks(cfis)
    view.show_progress(visible)             # the percentage at the page's foot
    view.get_toc(callback)                  # callback(toc)
    view.tts_start(callback) / tts_next(callback) / tts_stop()   # reading aloud, a sentence
                                            # at a time (callback(text), None at the end)
    view.location / view.fraction           # the last relocated place

Signals (each carries the page's message as a dict, .claude/rules/reader.md):
'loaded' (title, dir, fixedLayout, sectionFractions, toc), 'toc-ready' (the toc: [{label,
href, subitems}]), 'relocated' (fraction, cfi, chapter, page, section, location, time,
atStart, atEnd, bookmark, jumpedFrom, canGoBack, canGoForward), 'selection' (cfi, text,
rect, fraction; None when cleared), 'annotation-activated' (cfi, rect), 'search-result'
(label, items: [{cfi, pre, match, post}]), 'search-done' (query, count), 'history'
(canGoBack, canGoForward), 'error' (a message), 'toggle-chrome'.

The scheme serves the reader's files from the gresource (/io/github/jackicus/Bookcase/
reader/…) and the open book from its path, as bookcase://reader/book/<token>.<ext>: the same
origin as the page (the CSP allows only 'self'), and the real extension, by which foliate-js
tells FB2, FBZ and CBZ apart. A book over LARGE_FILE bytes is handed over as a File instead
(the page clicks a hidden file input; run-file-chooser answers with the path), read from disk
as needed rather than fetched whole. Nothing else loads: navigation is denied (a link out
opens in the browser, after the user's click), no local storage, an ephemeral network
session. The view takes no focus: the reader window's key controller gets every key. When
the web process dies, the page is loaded again at the last place.
"""

import json
import logging
import os
import secrets
import time
import urllib.parse

import gi

gi.require_version('Gtk', '4.0')
gi.require_version('Adw', '1')

from gi.repository import Adw, Gdk, Gio, GLib, GObject, Gtk  # noqa: E402

log = logging.getLogger(__name__)

WebKit = None
if not os.environ.get('BOOKCASE_NO_WEBKIT'):
    try:
        gi.require_version('WebKit', '6.0')
        from gi.repository import WebKit
    except (ImportError, ValueError):
        log.info('WebKitGTK 6.0 is not available: books cannot be read in Bookcase')

SCHEME = 'bookcase'
HOST = 'reader'
PAGE_URI = f'{SCHEME}://{HOST}/reader.html'
RESOURCE_BASE = '/io/github/jackicus/Bookcase/reader'
LARGE_FILE = 64 * 1024 * 1024
DEBUG = bool(os.environ.get('BOOKCASE_DEBUG_READER'))
TYPES = {'.html': 'text/html', '.js': 'text/javascript', '.css': 'text/css',
         '.json': 'application/json'}
EXTERNAL_SCHEMES = ('http', 'https', 'mailto')
# The page's file name for a format: the extension foliate-js goes by.
EXTENSIONS = {'kepub': 'epub', 'azw': 'azw3', 'prc': 'mobi'}

_books = {}  # token -> path, the books the scheme serves
_contexts = []  # the web contexts the scheme is registered on
_session = []  # the ephemeral network session, made once


def available():
    """Whether books can be shown: WebKitGTK 6.0 imported (and BOOKCASE_NO_WEBKIT unset)."""
    return WebKit is not None


def _not_found(request, what):
    log.warning('bookcase:// has no %s', what)
    request.finish_error(GLib.Error.new_literal(
        Gio.io_error_quark(), f'Not found: {what}', Gio.IOErrorEnum.NOT_FOUND))


def serve(request):
    """The bookcase:// scheme's handler: the reader's files and the open books."""
    uri = urllib.parse.urlsplit(request.get_uri())
    path = urllib.parse.unquote(uri.path)
    if uri.netloc != HOST or '..' in path.split('/'):
        _not_found(request, request.get_uri())
        return
    if path.startswith('/book/'):
        token = path[len('/book/'):].split('.', 1)[0]
        book = _books.get(token)
        if book is None:
            _not_found(request, path)
            return
        try:
            gfile = Gio.File.new_for_path(book)
            size = gfile.query_info(Gio.FILE_ATTRIBUTE_STANDARD_SIZE,
                                    Gio.FileQueryInfoFlags.NONE, None).get_size()
            request.finish(gfile.read(None), size, 'application/octet-stream')
        except GLib.Error as error:
            log.warning('reading %s: %s', book, error.message)
            request.finish_error(error)
        return
    try:
        stream = Gio.resources_open_stream(RESOURCE_BASE + path,
                                           Gio.ResourceLookupFlags.NONE)
    except GLib.Error:
        _not_found(request, path)
        return
    extension = os.path.splitext(path)[1]
    request.finish(stream, -1, TYPES.get(extension, 'application/octet-stream'))


def _register(context):
    if any(known == context for known in _contexts):
        return
    context.register_uri_scheme(SCHEME, serve)
    security = context.get_security_manager()
    security.register_uri_scheme_as_secure(SCHEME)
    security.register_uri_scheme_as_cors_enabled(SCHEME)
    _contexts.append(context)


def _network_session():
    if not _session:
        _session.append(WebKit.NetworkSession.new_ephemeral())
    return _session[0]


def _settings():
    settings = WebKit.Settings()
    values = {
        'enable-javascript': True,
        'enable-developer-extras': DEBUG,
        'enable-write-console-messages-to-stdout': DEBUG,
        'enable-html5-local-storage': False,
        'enable-html5-database': False,
        'enable-back-forward-navigation-gestures': False,
        'enable-page-cache': False,
        'enable-webgl': False,
        'enable-webaudio': False,
        'enable-media-stream': False,
        'enable-encrypted-media': False,
        'enable-webrtc': False,
        'javascript-can-open-windows-automatically': False,
        'javascript-can-access-clipboard': False,
        'allow-file-access-from-file-urls': False,
        'allow-universal-access-from-file-urls': False,
        'default-charset': 'utf-8',
    }
    for name, value in values.items():
        if settings.find_property(name) is not None:
            settings.set_property(name, value)
    return settings


def _rgba(color):
    rgba = Gdk.RGBA()
    if not rgba.parse(color):
        rgba.parse('#ffffff')
    return rgba


class BookView(Adw.Bin):
    __gtype_name__ = 'BookcaseBookView'

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
    }

    def __init__(self, **kwargs):
        super().__init__(vexpand=True, hexpand=True, **kwargs)
        self._web = None
        self._book = None  # what open() was given
        self._ready = False  # the page has said 'ready' and been given the book
        self._pending = []  # calls made before that
        self._file_for_input = None  # the path the page's file input asks for
        self._crashes = []  # times the web process died
        self._style = None
        self.location = ''
        self.fraction = 0.0
        self.place = None  # the last relocated message
        self.toc = []
        if WebKit is not None:
            self._web = self._make_web_view()
            self.set_child(self._web)

    def _make_web_view(self):
        _register(WebKit.WebContext.get_default())
        self._manager = WebKit.UserContentManager()
        web = WebKit.WebView(user_content_manager=self._manager, settings=_settings(),
                             network_session=_network_session(), vexpand=True, hexpand=True)
        web.set_can_focus(False)
        web.set_focusable(False)
        web.set_background_color(_rgba('#ffffff'))
        ref = self.weak_ref()

        def weak(method):
            def call(*args):
                view = ref()
                return method(view, *args) if view is not None else False
            return call

        self._manager.connect('script-message-received::bookcase',
                              weak(BookView._on_message))
        self._manager.register_script_message_handler('bookcase', None)
        web.connect('decide-policy', weak(BookView._on_decide_policy))
        web.connect('run-file-chooser', weak(BookView._on_run_file_chooser))
        web.connect('web-process-terminated', weak(BookView._on_terminated))
        web.connect('load-failed', weak(BookView._on_load_failed))
        web.connect('context-menu', lambda *_args: True)
        web.connect('create', lambda *_args: None)
        return web

    # -- the page -----------------------------------------------------------------------------

    @property
    def web_view(self):
        return self._web

    def open(self, path, fmt, location=None, fraction=None, annotations=(), bookmarks=(),
             style=None):
        """Load the page and open the book at `path` (format `fmt`), at `location` (a CFI),
        else `fraction`, else its start."""
        if self._web is None:
            self.emit('error', 'WebKitGTK is not available')
            return
        token = secrets.token_hex(12)
        _books[token] = str(path)
        extension = EXTENSIONS.get(fmt, fmt)
        try:
            size = os.path.getsize(path)
        except OSError:
            size = 0
        if style is not None:
            self._style = style
        self._book = {
            'token': token,
            'path': str(path),
            'url': f'{SCHEME}://{HOST}/book/{token}.{extension}',
            'fileInput': size > LARGE_FILE,
            'location': location or None,
            'fraction': fraction or None,
            'annotations': list(annotations),
            'bookmarks': list(bookmarks),
        }
        self.location = location or ''
        self._load()

    def _load(self):
        self._ready = False
        self._pending = []
        if self._style is not None:
            self._web.set_background_color(_rgba(self._style['theme']['bg']))
        self._web.load_uri(PAGE_URI)

    def _send_open(self):
        book = self._book
        self._file_for_input = book['path'] if book['fileInput'] else None
        args = {key: book[key] for key in ('url', 'location', 'fraction', 'annotations',
                                           'bookmarks', 'fileInput')}
        args['style'] = self._style
        self._ready = True
        self._call('open', args)
        pending, self._pending = self._pending, []
        for name, call_args, callback in pending:
            self._call(name, call_args, callback)

    def close(self):
        """Forget the open book (the scheme stops serving it)."""
        if self._book is not None:
            _books.pop(self._book['token'], None)
        self._book = None
        if self._web is not None:
            self._web.try_close()

    # -- Python -> JS ------------------------------------------------------------------------

    def _call(self, name, args=None, callback=None):
        """reader.<name>(args) in the page; callback(result) with what it resolves to
        (None when it fails)."""
        if self._web is None:
            return
        if not self._ready:
            self._pending.append((name, args, callback))
            return
        body = f'return await globalThis.reader.{name}(JSON.parse(args))'
        variant = GLib.Variant('a{sv}', {'args': GLib.Variant('s', json.dumps(args or {}))})
        self._web.call_async_javascript_function(body, -1, variant, None, None, None,
                                                 _on_called, (name, callback))

    def set_style(self, style):
        self._style = style
        if self._web is not None:
            self._web.set_background_color(_rgba(style['theme']['bg']))
        if self._ready:
            self._call('setStyle', style)

    def next(self):
        self._call('next')

    def prev(self):
        self._call('prev')

    def go_left(self):
        self._call('goLeft')

    def go_right(self):
        self._call('goRight')

    def scroll(self, direction):
        self._call('scroll', {'direction': direction})

    def start(self):
        self._call('start')

    def end(self):
        self._call('end')

    def next_section(self):
        self._call('nextSection')

    def prev_section(self):
        self._call('prevSection')

    def back(self):
        self._call('back')

    def forward(self):
        self._call('forward')

    def go_to(self, target, callback=None):
        self._call('goTo', {'target': target}, callback)

    def go_to_fraction(self, fraction):
        self._call('goToFraction', {'fraction': float(fraction)})

    def select(self, cfi):
        self._call('select', {'cfi': cfi})

    def clear_selection(self):
        self._call('clearSelection')

    def search(self, text):
        self._call('search', {'query': text})

    def clear_search(self):
        self._call('clearSearch')

    def find_texts(self, items, callback):
        """Where highlights known only by their text are: items [{'id', 'text'}];
        callback([{'id', 'cfi', 'fraction'}] for those found, or None)."""
        self._call('findTexts', {'items': list(items)}, callback)

    def set_annotations(self, annotations):
        """The highlights drawn: [{'cfi', 'color'}], replacing those drawn before."""
        annotations = list(annotations)
        if self._book is not None:
            self._book['annotations'] = annotations
        self._call('setAnnotations', {'annotations': annotations})

    def add_annotation(self, cfi, color='yellow'):
        if self._book is not None:
            self._book['annotations'].append({'cfi': cfi, 'color': color})
        self._call('addAnnotation', {'annotation': {'cfi': cfi, 'color': color}})

    def remove_annotation(self, cfi):
        if self._book is not None:
            self._book['annotations'] = [a for a in self._book['annotations']
                                         if a['cfi'] != cfi]
        self._call('removeAnnotation', {'cfi': cfi})

    def set_bookmarks(self, cfis, callback=None):
        """The bookmarks' CFIs; callback(cfi or None): the one on the page."""
        cfis = list(cfis)
        if self._book is not None:
            self._book['bookmarks'] = cfis
        self._call('setBookmarks', {'bookmarks': cfis}, callback)

    def show_progress(self, visible):
        """Whether the page shows the percentage at its foot (when the window's bars are
        hidden)."""
        self._call('showProgress', {'visible': bool(visible)})

    def get_toc(self, callback):
        self._call('getTOC', None, callback)

    # Reading aloud (widgets/read_aloud.py): the page highlights each sentence it gives and
    # turns to it.

    def tts_start(self, callback):
        """Read from the page shown; callback(True), or callback(False/None) when the book
        cannot be read aloud (a fixed layout)."""
        self._call('ttsStart', None, callback)

    def tts_next(self, callback):
        """callback(text) with the next sentence, or None at the end of the book."""
        self._call('ttsNext', None, callback)

    def tts_stop(self):
        self._call('ttsStop')

    def evaluate(self, script, callback=None):
        """Run a script in the page (tests and the demo script); callback(result)."""
        body = f'return await (async () => {{ {script} }})()'
        self._web.call_async_javascript_function(body, -1, None, None, None, None,
                                                 _on_called, ('evaluate', callback))

    # -- JS -> Python ------------------------------------------------------------------------

    def _on_message(self, _manager, value):
        try:
            message = json.loads(value.to_string())
        except (TypeError, ValueError):
            log.warning('the reader page sent a message that is not JSON')
            return
        kind = message.pop('type', '')
        if self._book is None and kind != 'ready':
            return  # closed: what the page still says is about nothing open
        if kind == 'ready':
            if self._book is not None:
                self._send_open()
        elif kind == 'loaded':
            self.toc = message.get('toc') or []
            self.emit('loaded', message)
            self.emit('toc-ready', self.toc)
        elif kind == 'relocated':
            self.place = message
            self.location = message.get('cfi') or self.location
            self.fraction = message.get('fraction') or 0.0
            if self._book is not None:
                self._book['location'] = self.location
            self.emit('relocated', message)
        elif kind == 'selection':
            self.emit('selection', message if message.get('cfi') else None)
        elif kind == 'annotation':
            self.emit('annotation-activated', message)
        elif kind == 'search-result':
            self.emit('search-result', message)
        elif kind == 'search-done':
            self.emit('search-done', message)
        elif kind == 'history':
            self.emit('history', message)
        elif kind == 'toggle-chrome':
            self.emit('toggle-chrome')
        elif kind == 'external-link':
            self._open_external(message.get('href') or '')
        elif kind == 'error':
            log.warning('the reader page: %s', message.get('message'))
            self.emit('error', message.get('message') or '')
        elif kind != 'search-progress':
            log.debug('the reader page sent %r', kind)

    def _open_external(self, uri):
        scheme = urllib.parse.urlsplit(uri).scheme.lower()
        if scheme not in EXTERNAL_SCHEMES:
            log.info('not opening a link to %s', uri)
            return
        root = self.get_root()
        Gtk.UriLauncher.new(uri).launch(root if isinstance(root, Gtk.Window) else None,
                                        None, None, None)

    def _on_decide_policy(self, _web, decision, kind):
        if kind == WebKit.PolicyDecisionType.RESPONSE:
            decision.use()
            return True
        action = decision.get_navigation_action()
        uri = action.get_request().get_uri() or ''
        scheme = urllib.parse.urlsplit(uri).scheme.lower()
        clicked = action.get_navigation_type() == WebKit.NavigationType.LINK_CLICKED
        if kind == WebKit.PolicyDecisionType.NAVIGATION_ACTION and (
                uri == PAGE_URI or scheme in ('blob', 'about', 'data')):
            decision.use()  # the page itself and foliate-js's section frames
            return True
        decision.ignore()
        if clicked and action.is_user_gesture():
            self._open_external(uri)
        else:
            log.info('the reader page may not load %s', uri)
        return True

    def _on_run_file_chooser(self, _web, request):
        path = self._file_for_input
        self._file_for_input = None
        if path:
            request.select_files([path])
        else:
            request.cancel()
        return True

    def _on_terminated(self, _web, reason):
        log.warning("the reader page's web process ended (%s)", reason)
        now = time.monotonic()
        self._crashes = [t for t in self._crashes if now - t < 60] + [now]
        if self._book is None:
            return
        if len(self._crashes) > 3:
            self.emit('error', 'The page stopped working')
            return
        self._ready = False
        GLib.idle_add(self._reload)

    def _reload(self):
        if self._book is not None and self._web is not None:
            self._load()  # at the last place: _book['location'] follows relocated
        return GLib.SOURCE_REMOVE

    def _on_load_failed(self, _web, _event, uri, error):
        log.warning('loading %s: %s', uri, error.message)
        return False


def _on_called(web, result, data):
    name, callback = data
    try:
        value = web.call_async_javascript_function_finish(result)
    except GLib.Error as error:
        log.warning('reader.%s(): %s', name, error.message)
        value = None
    result = None
    if value is not None and not value.is_undefined() and not value.is_null():
        try:
            result = json.loads(value.to_json(0))
        except (TypeError, ValueError):
            result = None
    if callback is not None:
        callback(result)
