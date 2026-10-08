# SPDX-License-Identifier: GPL-2.0-or-later
# SPDX-FileCopyrightText: 2026 Jack Tully

"""Look Up in the reader: a word's definition and an encyclopedia summary, in the selection
popover (a wide window) or a bottom sheet (a narrow one).

    panel = LookupPanel(on_search=callback, service=None)   # a Gtk.Box
    panel.show_text(text, language)     # a word: Dictionary and Wikipedia; more: Wikipedia
    panel.cancel()                      # forget the lookup running
    present_sheet(window, text, language, on_search)        # the panel in a bottom sheet
    inline(window)                      # whether the window is wide enough for the popover

The definitions come from lookup.service() (offline StarDict dictionaries first, else
Wiktionary), the summaries from Wikipedia: fetched in a thread, cached, never blocking.
Offline, the panel says so. Open in Browser opens the page the answer came from (Wiktionary
for an offline dictionary's word); Search in Book calls on_search(text). The panel is the
same for any view that emits the reader's 'selection' signal (an EPUB's, a PDF's).
"""

from gettext import gettext as _

from gi.repository import Adw, Gtk, Pango

from .. import lookup
from .util import connect_weak

INLINE_WIDTH = 500  # narrower windows show the panel in a bottom sheet
PANEL_WIDTH = 320
POPOVER_HEIGHT = 240  # the answer scrolls beyond this, in the popover
SHEET_HEIGHT = 360
SHEET_MIN_HEIGHT = 220  # a bottom sheet measures the answer too short to read without it


def inline(window):
    return window.get_width() >= INLINE_WIDTH


def _label(text, *classes, wrap=True, selectable=False):
    label = Gtk.Label(label=text, xalign=0, wrap=wrap, wrap_mode=Pango.WrapMode.WORD_CHAR,
                      max_width_chars=36, width_chars=1, selectable=selectable)
    for name in classes:
        label.add_css_class(name)
    return label


class LookupPanel(Gtk.Box):
    __gtype_name__ = 'BookcaseLookupPanel'

    def __init__(self, on_search=None, service=None, max_height=POPOVER_HEIGHT, min_height=-1,
                 **kwargs):
        super().__init__(orientation=Gtk.Orientation.VERTICAL, spacing=8, **kwargs)
        self.add_css_class('lookup-panel')
        self.set_size_request(PANEL_WIDTH, -1)
        self.on_search = on_search
        self.service = service
        self.text = ''
        self.language = 'en'
        self.answer = None  # the Article or Summary shown
        self._task = None
        self._loading = False

        self.toggles = Adw.ToggleGroup(halign=Gtk.Align.CENTER)
        self.toggles.add_css_class('round')
        for name, label in (('dictionary', _('Dictionary')), ('wikipedia', _('Wikipedia'))):
            self.toggles.add(Adw.Toggle(name=name, label=label))
        connect_weak(self.toggles, 'notify::active-name', self._on_toggled)
        self.append(self.toggles)

        self.stack = Gtk.Stack(vhomogeneous=False, hhomogeneous=True,
                               transition_type=Gtk.StackTransitionType.CROSSFADE)
        spinner = Adw.Spinner(width_request=32, height_request=32, halign=Gtk.Align.CENTER,
                              margin_top=24, margin_bottom=24)
        self.stack.add_named(spinner, 'loading')
        self.result = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=6)
        scrolled = Gtk.ScrolledWindow(hscrollbar_policy=Gtk.PolicyType.NEVER,
                                      propagate_natural_height=True,
                                      max_content_height=max_height,
                                      min_content_height=min_height, child=self.result)
        self.stack.add_named(scrolled, 'result')
        message = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=6, margin_top=12,
                          margin_bottom=12)
        self.message_icon = Gtk.Image(pixel_size=32, halign=Gtk.Align.CENTER,
                                      accessible_role=Gtk.AccessibleRole.PRESENTATION)
        self.message_icon.add_css_class('dimmed')
        message.append(self.message_icon)
        self.message_title = _label('', 'heading')
        self.message_title.set_xalign(0.5)
        self.message_title.set_justify(Gtk.Justification.CENTER)
        message.append(self.message_title)
        self.message_body = _label('', 'dimmed')
        self.message_body.set_xalign(0.5)
        self.message_body.set_justify(Gtk.Justification.CENTER)
        message.append(self.message_body)
        self.stack.add_named(message, 'message')
        self.append(self.stack)

        self.source_label = _label('', 'caption', 'dimmed')
        self.append(self.source_label)
        footer = Gtk.Box(spacing=6, homogeneous=True)
        self.browser_button = Gtk.Button(label=_('Open in Browser'))
        self.browser_button.add_css_class('flat')
        connect_weak(self.browser_button, 'clicked', self._on_browser)
        footer.append(self.browser_button)
        self.search_button = Gtk.Button(label=_('Search in Book'))
        self.search_button.add_css_class('flat')
        connect_weak(self.search_button, 'clicked', self._on_search)
        footer.append(self.search_button)
        self.append(footer)
        self.connect('unrealize', lambda panel: panel.cancel())

    # -- asking ------------------------------------------------------------------------------

    def show_text(self, text, language='en'):
        """Look up `text`: a word in the dictionary (Wikipedia a toggle away), more than a
        word in Wikipedia."""
        self.text = ' '.join((text or '').split())[:200]
        self.language = language or 'en'
        word = lookup.is_word(self.text)
        self.toggles.set_visible(word)
        name = 'dictionary' if word else 'wikipedia'
        if self.toggles.get_active_name() != name:
            self.toggles.set_active_name(name)  # loads, through notify::active-name
        else:
            self._load()

    def cancel(self):
        if self._task is not None:
            self._task.cancel()
            self._task = None
        self._loading = False

    def _service(self):
        return self.service or lookup.service()

    def _on_toggled(self, _group, _pspec):
        if self.text:
            self._load()

    def _load(self):
        self.cancel()
        self.answer = None
        self._clear_result()
        self.source_label.set_label('')
        kind = self.toggles.get_active_name() or 'dictionary'
        ref = self.weak_ref()
        text = self.text

        def done(answer, error):
            panel = ref()
            if panel is None or panel.text != text:
                return
            panel._task = None
            panel._loading = False
            if error is not None:
                panel._show_error(error)
            elif kind == 'dictionary':
                panel._show_article(answer)
            else:
                panel._show_summary(answer)

        self.stack.set_visible_child_name('loading')
        self._loading = True
        if kind == 'dictionary':
            self._task = self._service().define(lookup.clean_word(text), self.language, done)
        else:
            self._task = self._service().summarize(text, self.language, done)

    # -- showing -----------------------------------------------------------------------------

    def _clear_result(self):
        while (child := self.result.get_first_child()) is not None:
            self.result.remove(child)

    def _show_article(self, article):
        self.answer = article
        self._clear_result()
        self.result.append(_label(article.word, 'title-4'))
        for entry in article.entries:
            heading = entry.heading
            if entry.language:
                heading = _('{part_of_speech} · {language}').format(
                    part_of_speech=entry.heading, language=entry.language)
            title = _label(heading, 'heading')
            title.set_margin_top(4)
            self.result.append(title)
            numbered = len(entry.senses) > 1
            for number, sense in enumerate(entry.senses, 1):
                text = f'{number}. {sense.text}' if numbered else sense.text
                self.result.append(_label(text))
                for example in sense.examples:
                    example_label = _label(f'“{example}”', 'dimmed')
                    example_label.set_margin_start(16)
                    self.result.append(example_label)
        if article.source == 'wiktionary':
            self.source_label.set_label(_('From Wiktionary, CC BY-SA'))
        else:
            self.source_label.set_label(_('From {dictionary}').format(dictionary=article.source))
        self.stack.set_visible_child_name('result')

    def _show_summary(self, summary):
        self.answer = summary
        self._clear_result()
        self.result.append(_label(summary.title, 'title-4'))
        if summary.description:
            self.result.append(_label(summary.description, 'dimmed'))
        self.result.append(_label(summary.extract))
        self.source_label.set_label(_('From Wikipedia, CC BY-SA'))
        self.stack.set_visible_child_name('result')

    def _show_error(self, error):
        if getattr(error, 'offline', False):
            icon, title = 'network-offline-symbolic', _('No Connection')
            body = _('Looking up needs the internet, or a StarDict dictionary installed for '
                     'offline use')
        elif lookup.not_found(error):
            icon, title, body = 'edit-find-symbolic', _('Nothing Found'), str(error)
        else:
            icon, title, body = 'dialog-warning-symbolic', _('Lookup Failed'), str(error)
        self.message_icon.set_from_icon_name(icon)
        self.message_title.set_label(title)
        self.message_body.set_label(body)
        self.stack.set_visible_child_name('message')

    # -- the buttons -------------------------------------------------------------------------

    def page_url(self):
        """The page Open in Browser opens."""
        answer = self.answer
        if getattr(answer, 'url', ''):
            return answer.url
        if self.toggles.get_active_name() == 'dictionary' and lookup.is_word(self.text):
            return lookup.wiktionary_page(lookup.clean_word(self.text))
        return lookup.wikipedia_page(self.text, self.language)

    def _on_browser(self, _button):
        root = self.get_root()
        Gtk.UriLauncher.new(self.page_url()).launch(
            root if isinstance(root, Gtk.Window) else None, None, None, None)

    def _on_search(self, _button):
        if self.on_search is not None and self.text:
            self.on_search(self.text)


def present_sheet(window, text, language, on_search=None, service=None):
    """The panel in a bottom sheet over `window` (a narrow one); returns the dialog. A window
    with set_dialog_open() hands its keys over while it is open."""
    dialog = Adw.Dialog(title=_('Look Up'), content_width=360,
                        presentation_mode=Adw.DialogPresentationMode.BOTTOM_SHEET)
    toolbar = Adw.ToolbarView()
    toolbar.add_top_bar(Adw.HeaderBar())

    ref = dialog.weak_ref()

    def search(found):
        sheet = ref()
        if sheet is not None:
            sheet.close()
        if on_search is not None:
            on_search(found)

    panel = LookupPanel(on_search=search, service=service, max_height=SHEET_HEIGHT,
                        min_height=SHEET_MIN_HEIGHT,
                        margin_start=12, margin_end=12, margin_bottom=12)
    toolbar.set_content(panel)
    dialog.set_child(toolbar)
    dialog.panel = panel
    set_open = getattr(window, 'set_dialog_open', None)
    if set_open is not None:
        set_open(True)
        dialog.connect('closed', lambda _dialog: set_open(False))
    dialog.present(window)
    panel.show_text(text, language)
    return dialog
