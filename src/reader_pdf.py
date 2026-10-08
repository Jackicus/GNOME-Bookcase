# SPDX-License-Identifier: GPL-2.0-or-later
# SPDX-FileCopyrightText: 2026 Jack Tully

"""The reader window's side of a PDF (widgets/pdf_view.PdfView): what it keeps per book, the
popover's spread switches, Print… and Read Aloud.

    pdf = ReaderPdf(window)             # once the window has swapped in its PdfView, before
                                        # the view opens the book
    pdf.scrolled()                      # the book's Pages or Scrolled (True: scrolled)
    pdf.set_scrolled(scrolled)          # the window's Pages/Scrolled toggle, for this book
    pdf.save()                          # keep the zoom and layout now (also on closing)

The zoom (Fit Width, Fit Page, automatic or a percentage), pages or scrolling, right to left
and the cover alone are kept per book in the library (Library.book_state(id)['pdf'],
checked by pdf_location.layout_state()), half a second after they change and on closing;
a PDF opened for the first time scrolls or not as the reader-pdf-scrolled setting says
(which every change of Pages or Scrolled also sets, for the PDFs opened next), and follows
the PDF's own viewer preferences for its spreads. Another window changing the setting
leaves an open PDF as it is.

The Zoom and Layout popover gets Right to Left (spreads and page turns from the right) and
Cover Page Alone (the first page by itself in spreads) under Two Pages; a change shows at
once. The main menu gets Print… (win.print, Ctrl+P: shortcuts.READER 'print'), the print
dialog over the window. Read Aloud's bar reads from the PdfView (its tts_* calls) in place
of the window's BookView.
"""

import logging
import weakref
from gettext import gettext as _

from gi.repository import Adw, Gio, GLib

from . import pdf_location
from .widgets import read_aloud

log = logging.getLogger(__name__)

SAVE_DELAY_MS = 500
STATE_KEY = 'pdf'


class ReaderPdf:
    def __init__(self, window):
        self._window = window.weak_ref()
        self.view = window.view
        self.library = window.library
        self.book_id = window.book_id
        self._save_source = 0
        self._closed = False
        state = pdf_location.layout_state(self.library.book_state(self.book_id).get(STATE_KEY))
        self._flow = state['flow']
        if self._flow is None:  # never set for this book: the setting, as it is now
            self._flow = 'scrolled' if window.settings.get_boolean('reader-pdf-scrolled') \
                else 'paginated'
        self._saved = self._normal(state)
        self.view.restore_layout(state)

        _connect(self, self.view, 'zoom-changed', self._on_zoom_changed)
        _connect(self, self.view, 'loaded', self._on_loaded)
        _connect(self, window, 'close-request', self._on_close_request)
        self._build_rows(window)
        self._build_print(window)
        bar = getattr(window, '_read_aloud', None)
        if bar is not None:  # made for the BookView: it reads the PdfView instead
            bar.source = bar.player.source = read_aloud.ViewSource(self.view)

    def window(self):
        return self._window()

    # -- what is kept ----------------------------------------------------------------------

    def scrolled(self):
        return self._flow == 'scrolled'

    def set_scrolled(self, scrolled):
        flow = 'scrolled' if scrolled else 'paginated'
        window = self.window()
        if flow == self._flow or window is None:
            return
        self._flow = flow
        self.save()
        # the PDFs opened next scroll or not as this one now does
        if window.settings.get_boolean('reader-pdf-scrolled') != scrolled:
            window.settings.set_boolean('reader-pdf-scrolled', scrolled)
        window.view.set_style(window._style())
        window._update_layout_group()
        self._update_rows()

    def _state(self):
        return dict(self.view.layout_state(), flow=self._flow)

    @staticmethod
    def _normal(state):
        """A state as compared with the one kept: the automatic zoom is no zoom kept."""
        state = pdf_location.layout_state(state)
        if state['fit'] == 'auto':
            state['fit'] = None
        return state

    def save(self):
        if self._save_source:
            GLib.source_remove(self._save_source)
            self._save_source = 0
        if self.library.closed:
            return GLib.SOURCE_REMOVE
        state = self._normal(self._state())
        if state != self._saved:
            self._saved = state
            self.library.set_book_state(self.book_id, STATE_KEY,
                                        {k: v for k, v in state.items() if v is not None})
        return GLib.SOURCE_REMOVE

    def _schedule_save(self):
        if self._closed:
            return
        if self._save_source:
            GLib.source_remove(self._save_source)
        ref = weakref.ref(self)
        self._save_source = GLib.timeout_add(
            SAVE_DELAY_MS, lambda: ref().save() if ref() is not None else GLib.SOURCE_REMOVE)

    def _on_zoom_changed(self, _view):
        self._schedule_save()

    def _on_close_request(self, _window):
        if not self._closed:
            self.save()
            self._closed = True
        return False

    # -- the popover's rows ----------------------------------------------------------------

    def _build_rows(self, window):
        two_pages = window.two_pages_row
        two_pages.set_subtitle(_('Side by side when the window is wide'))
        self.rtl_row = Adw.SwitchRow(title=_('Right to Left'),
                                     subtitle=_('Pages turn and pair from the right'))
        self.cover_row = Adw.SwitchRow(title=_('Cover Page Alone'),
                                       subtitle=_('The first page by itself, beside no other'))
        box = two_pages.get_parent()
        position = two_pages.get_index() + 1
        box.insert(self.rtl_row, position)
        box.insert(self.cover_row, position + 1)
        self._update_rows()
        _connect(self, self.rtl_row, 'notify::active', self._on_rtl_toggled)
        _connect(self, self.cover_row, 'notify::active', self._on_cover_toggled)
        _connect(self, two_pages, 'notify::active', self._on_two_pages)

    def _update_rows(self):
        paginated = self._flow == 'paginated'
        window = self.window()
        two = window is not None and window.two_pages_row.get_active()
        self.rtl_row.set_sensitive(paginated)
        self.cover_row.set_sensitive(paginated and two)
        if self.rtl_row.get_active() != self.view.rtl:
            self.rtl_row.set_active(self.view.rtl)
        if self.cover_row.get_active() != self.view.cover:
            self.cover_row.set_active(self.view.cover)

    def _on_two_pages(self, _row, _pspec):
        self._update_rows()

    def _on_loaded(self, _view, _loaded):
        self._update_rows()  # the PDF's own preferences are known now

    def _on_rtl_toggled(self, row, _pspec):
        if row.get_active() == self.view.rtl:
            return
        self.view.set_rtl(row.get_active())
        self._show_direction()
        self.save()

    def _on_cover_toggled(self, row, _pspec):
        if row.get_active() == self.view.cover:
            return
        self.view.set_cover(row.get_active())
        self.save()

    def _show_direction(self):
        """The scrubber and the arrows' tooltips as the pages turn (the window does the
        same on 'loaded')."""
        window = self.window()
        if window is None:
            return
        rtl = self.view.rtl
        window.progress_scale.set_inverted(rtl)
        window.prev_button.set_tooltip_text(_('Next Page') if rtl else _('Previous Page'))
        window.next_button.set_tooltip_text(_('Previous Page') if rtl else _('Next Page'))

    # -- printing --------------------------------------------------------------------------

    def _build_print(self, window):
        action = Gio.SimpleAction.new('print', None)
        _connect(self, action, 'activate', self._on_print)
        window.add_action(action)
        menu = window.menu_button.get_menu_model()
        section = menu.get_item_link(0, Gio.MENU_LINK_SECTION) if menu is not None else None
        if isinstance(section, Gio.Menu):
            section.append(_('Print…'), 'win.print')

    def _on_print(self, _action, _parameter):
        self.print()

    def print(self):
        window = self.window()
        if window is None or window.content_stack.get_visible_child_name() != 'book':
            return False
        try:
            self.view.print_document(window)
        except GLib.Error as error:
            log.warning('printing: %s', error.message)
            window.toast(_('The PDF cannot be printed'))
        return True


def _connect(owner, obj, signal, method):
    """obj.connect(signal, method) holding `owner` weakly (the window holds it), so a
    signal of the window or the view never keeps it alive."""
    ref = weakref.ref(owner)
    function = method.__func__

    def call(*args):
        instance = ref()
        return function(instance, *args) if instance is not None else None

    return obj.connect(signal, call)
