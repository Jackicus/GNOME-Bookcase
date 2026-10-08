#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-2.0-or-later
# SPDX-FileCopyrightText: 2026 Jack Tully

"""Open an invented book in a reader window and save it as a PNG: the reader on its own,
without the library window, for checking the reader's look.

    meson compile -C build
    scripts/headless.sh scripts/reader_demo.py build/reader.png [--light] [--size WxH]
        [--theme auto|light|sepia|dark|black] [--one-page] [--scrolled] [--font serif|sans]
        [--sidebar contents|annotations|search] [--search QUERY] [--select] [--popover]
        [--fullscreen] [--hide-chrome] [--at FRACTION] [--missing] [--pdf]

It runs the source tree (src/ as the `bookcase` package, as the tests do) with the build's
gresource, settings in memory and a library in a temporary directory: an EPUB of invented
chapters (with a footnote and two highlights) written there. --select selects a sentence and
shows the selection popover, --popover opens the Text and Layout popover, --missing shows the
missing-file page, --pdf the page a PDF gets.
"""

import argparse
import faulthandler
import os
import pathlib
import sys
import tempfile
import zipfile

ROOT = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

parser = argparse.ArgumentParser()
parser.add_argument('out', nargs='?', default=str(ROOT / 'build' / 'reader.png'))
parser.add_argument('--light', action='store_true')
parser.add_argument('--size', default='1100x760')
parser.add_argument('--theme', default='auto')
parser.add_argument('--one-page', action='store_true')
parser.add_argument('--scrolled', action='store_true')
parser.add_argument('--font', default='publisher')
parser.add_argument('--sidebar', choices=['contents', 'annotations', 'search'])
parser.add_argument('--search', metavar='QUERY')
parser.add_argument('--select', action='store_true')
parser.add_argument('--popover', action='store_true')
parser.add_argument('--fullscreen', action='store_true')
parser.add_argument('--hide-chrome', action='store_true')
parser.add_argument('--at', type=float, default=None)
parser.add_argument('--missing', action='store_true')
parser.add_argument('--pdf', action='store_true')
args = parser.parse_args()
faulthandler.dump_traceback_later(60, exit=True)  # a hang is reported, not waited out
width, height = (int(n) for n in args.size.split('x'))

import tests  # noqa: E402,F401  (src/ as `bookcase`, settings in memory, the schema)
from tests.gtk import gtk_unavailable, wait_for  # noqa: E402

reason = gtk_unavailable()
if reason:
    sys.exit(f'reader_demo: {reason}')

from gi.repository import Adw, Gio, GLib, Graphene, Gtk  # noqa: E402

from bookcase import reader_window  # noqa: E402
from bookcase.formats import BookInfo  # noqa: E402
from bookcase.library import Library  # noqa: E402

TITLES = ('The Harbour at Dusk', 'Rope and Tar', 'A Letter from Inland', 'The Lighthouse Keeper',
          'Low Water', 'What the Gulls Knew')
SENTENCES = (
    'The lamps along the harbour wall were lit one by one as the tide came in.',
    'Nobody on the quay said much; the gulls said enough for everyone.',
    'A cart of crates rolled past the chandlery, and the smell of tar and rope followed it '
    'up the hill.',
    'Mira kept the ledger open on the counter, though no one had come in since noon.',
    'Somewhere beyond the breakwater a bell buoy rang, patient and unhurried.',
    'Her uncle had always said that a town by the sea is never quite finished: the water '
    'takes a little back every winter, and every spring someone builds it up again.',
    'She thought about the letter in her coat pocket, and about the inland road, and about '
    'how long a week could be.',
    'The ferry came in late, its deck lights smeared by the drizzle.',
)


def chapter_html(number, title):
    paragraphs = []
    for index in range(14):
        words = [SENTENCES[(number * 3 + index + k) % len(SENTENCES)] for k in range(3 + index % 3)]
        text = ' '.join(words)
        if number == 1 and index == 1:
            text += (' <a epub:type="noteref" href="#note1" id="ref1">1</a>')
        paragraphs.append(f'<p>{text}</p>')
    note = ''
    if number == 1:
        note = ('<aside epub:type="footnote" id="note1"><p>The chandlery closed in the '
                'winter of the great storm, and never opened again.</p></aside>')
    return ('<?xml version="1.0" encoding="UTF-8"?>\n<html xmlns="http://www.w3.org/1999/xhtml"'
            ' xmlns:epub="http://www.idpf.org/2007/ops"><head>'
            f'<title>{title}</title></head><body><h1>{title}</h1>'
            f'{"".join(paragraphs)}{note}</body></html>\n')


def make_book(path):
    names = [f'chapter{n}.xhtml' for n in range(1, len(TITLES) + 1)]
    manifest = ''.join(f'<item id="c{n}" href="{name}" media-type="application/xhtml+xml"/>'
                       for n, name in enumerate(names, 1))
    spine = ''.join(f'<itemref idref="c{n}"/>' for n in range(1, len(names) + 1))
    opf = ('<?xml version="1.0" encoding="UTF-8"?>\n<package xmlns="http://www.idpf.org/2007/opf"'
           ' version="3.0" unique-identifier="uid"><metadata '
           'xmlns:dc="http://purl.org/dc/elements/1.1/">'
           '<dc:identifier id="uid">urn:uuid:5b0f3c2e-0d6a-4c5e-9a43-2f0d6c1e7a11</dc:identifier>'
           '<dc:title>A Quiet Harbour</dc:title><dc:creator>Ada Lark</dc:creator>'
           '<dc:language>en</dc:language>'
           '<meta property="dcterms:modified">2026-01-01T00:00:00Z</meta></metadata>'
           '<manifest><item id="nav" href="nav.xhtml" media-type="application/xhtml+xml" '
           f'properties="nav"/>{manifest}</manifest><spine>{spine}</spine></package>\n')
    items = ''.join(f'<li><a href="{name}">{title}</a></li>'
                    for name, title in zip(names, TITLES, strict=True))
    nav = ('<?xml version="1.0" encoding="UTF-8"?>\n<html xmlns="http://www.w3.org/1999/xhtml" '
           'xmlns:epub="http://www.idpf.org/2007/ops"><head><title>Contents</title></head><body>'
           f'<nav epub:type="toc"><ol>{items}</ol></nav></body></html>\n')
    with zipfile.ZipFile(path, 'w') as archive:
        archive.writestr(zipfile.ZipInfo('mimetype'), 'application/epub+zip')
        archive.writestr('META-INF/container.xml',
                         '<?xml version="1.0"?><container version="1.0" xmlns="urn:oasis:names:'
                         'tc:opendocument:xmlns:container"><rootfiles><rootfile full-path='
                         '"OEBPS/content.opf" media-type="application/oebps-package+xml"/>'
                         '</rootfiles></container>', zipfile.ZIP_DEFLATED)
        archive.writestr('OEBPS/content.opf', opf, zipfile.ZIP_DEFLATED)
        archive.writestr('OEBPS/nav.xhtml', nav, zipfile.ZIP_DEFLATED)
        for number, (name, title) in enumerate(zip(names, TITLES, strict=True), 1):
            archive.writestr(f'OEBPS/{name}', chapter_html(number, title), zipfile.ZIP_DEFLATED)
    return path


class DemoApp(Adw.Application):
    def __init__(self, library):
        super().__init__(application_id='io.github.jackicus.Bookcase.ReaderDemo',
                         flags=Gio.ApplicationFlags.NON_UNIQUE,
                         resource_base_path='/io/github/jackicus/Bookcase')
        self.library = library
        self.settings = Gio.Settings.new('io.github.jackicus.Bookcase')

    def undo(self):
        return self.library.undo()

    def toast(self, text, undo=False):
        pass


directory = pathlib.Path(tempfile.mkdtemp(prefix='bookcase-reader-demo-'))
library = Library(directory / 'library.sqlite')
book_path = make_book(directory / ('missing.epub' if args.missing else 'A Quiet Harbour.epub'))
if args.pdf:
    book_path = directory / 'A Quiet Harbour.pdf'
    book_path.write_bytes(b'%PDF-1.4\n%invented\n')
info = BookInfo(title='A Quiet Harbour', authors=['Ada Lark'], format=book_path.suffix[1:],
                language='en')
book_id = library.add_book(info, str(book_path), hash='demo', size=book_path.stat().st_size)
if args.missing:
    os.remove(book_path)

app = DemoApp(library)
settings = app.settings
settings.set_string('reader-theme', args.theme)
settings.set_boolean('reader-two-pages', not args.one_page)
settings.set_boolean('reader-scrolled', args.scrolled)
settings.set_string('reader-font', args.font)
settings.set_boolean('reader-animate', False)
settings.set_int('reader-width', width)
settings.set_int('reader-height', height)
failed = []


def shoot(window):
    target = window
    native = target.get_native()
    paintable = Gtk.WidgetPaintable.new(target)
    snapshot = Gtk.Snapshot()
    paintable.snapshot(snapshot, target.get_width(), target.get_height())
    node = snapshot.to_node()
    if node is None:
        failed.append('nothing drawn')
        return
    texture = native.get_renderer().render_texture(
        node, Graphene.Rect().init(0, 0, target.get_width(), target.get_height()))
    texture.save_to_png(args.out)
    print(args.out)


def run_js(window, script):
    done = []
    window.book_view.evaluate(script, lambda result: done.append(result))
    wait_for(lambda: done, 5)
    return done[0] if done else None


def activate(app):
    try:
        start(app)
    except Exception:
        app.quit()
        raise


def start(app):
    style = Adw.StyleManager.get_default()
    style.set_color_scheme(Adw.ColorScheme.FORCE_LIGHT if args.light
                           else Adw.ColorScheme.FORCE_DARK)
    Gtk.Settings.get_default().set_property('gtk-enable-animations', False)
    window = reader_window.open(app, book_id)
    window.set_resizable(False)
    if args.fullscreen:
        window.fullscreen()
    GLib.idle_add(lambda: (steps(window), GLib.SOURCE_REMOVE)[1])


def steps(window):
    if window.content_stack.get_visible_child_name() == 'book':
        if not wait_for(lambda: window._place is not None, 15):
            failed.append('the book never showed')
        # two highlights, saved as the selection popover saves them
        if args.at is not None:
            window.book_view.go_to_fraction(args.at)
            wait_for(lambda: False, 0.8)
        cfis = run_js(window, """
            const { doc, index } = globalThis.reader._contents()
            const ps = doc.querySelectorAll('p')
            const out = []
            for (const [i, start, end] of [[0, 0, 60], [2, 30, 120]]) {
                const range = doc.createRange()
                const text = ps[i].firstChild
                range.setStart(text, start); range.setEnd(text, Math.min(end, text.length))
                out.push([globalThis.reader._cfi(index, range), range.toString()])
            }
            return out""") or []
        for (cfi, text), color in zip(cfis, ('yellow', 'blue'), strict=False):
            library.add_annotation(book_id, 'highlight', cfi, text=text, color=color,
                                   position=0.05, note='The tide again.' if color == 'blue'
                                   else '')
        library.add_annotation(book_id, 'bookmark', 'epubcfi(/6/6!/4/2)', text=TITLES[1],
                               position=0.2)
        wait_for(lambda: False, 0.6)
        if args.hide_chrome:
            window._set_chrome(False)
        if args.sidebar:
            window.sidebar_stack.set_visible_child_name(args.sidebar)
            window.split_view.set_show_sidebar(True)
        if args.search:
            window.sidebar_stack.set_visible_child_name('search')
            window.split_view.set_show_sidebar(True)
            window.search_entry.set_text(args.search)
            wait_for(lambda: window.search_stack.get_visible_child_name() != 'start'
                     and 'so far' not in window.search_status.get_label(), 5)
            window._search_step(1)
            wait_for(lambda: False, 0.6)
        if args.select:
            run_js(window, """
                const { doc } = globalThis.reader._contents()
                const p = doc.querySelectorAll('p')[4]
                const range = doc.createRange()
                range.setStart(p.firstChild, 0); range.setEnd(p.firstChild, 66)
                const selection = doc.getSelection()
                selection.removeAllRanges(); selection.addRange(range)
                doc.dispatchEvent(new PointerEvent('pointerup'))
                return true""")
            wait_for(lambda: window._selection is not None, 3)
            wait_for(lambda: False, 0.5)
        if args.popover:
            window.typography_button.popup()
            wait_for(lambda: False, 0.5)
    wait_for(lambda: False, 1.0)
    shoot(window)
    popovers = []
    if args.select:
        popovers.append(window._selection_popover)
    if args.popover:
        popovers.append(window.typography_popover)
    for index, popover in enumerate(popovers):
        if popover.get_mapped():
            out = args.out.replace('.png', f'-popover{index}.png')
            paintable = Gtk.WidgetPaintable.new(popover)
            snapshot = Gtk.Snapshot()
            paintable.snapshot(snapshot, popover.get_width(), popover.get_height())
            node = snapshot.to_node()
            if node is not None:
                popover.get_native().get_renderer().render_texture(
                    node, Graphene.Rect().init(0, 0, popover.get_width(),
                                               popover.get_height())).save_to_png(out)
                print(out)
    window.close()
    app.quit()


app.connect('activate', activate)
app.run([])
library.close()
sys.exit('reader_demo: ' + '; '.join(failed) if failed else 0)
