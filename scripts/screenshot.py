#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-2.0-or-later
# SPDX-FileCopyrightText: 2026 Jack Tully

"""Render the app's window to a PNG, for checking UI changes without a human.

    scripts/headless.sh scripts/screenshot.py [out.png] [--light] [--size WxH]
                          [--page KEY] [--book TITLE] [--read TITLE] [--sidebar]
                          [--dialog edit|fetch|shelf|preferences|about|shortcuts|send|add]
                          [--search QUERY] [--scroll PX] [--device] [--wait MS]

Builds nothing itself: run meson install -C build (or scripts/run.sh) first, and run it
through scripts/headless.sh so the window opens on a private display. The demo library
(build/demo, generated when missing) is what it shows; settings go to a memory backend and
animations are off.

--page is a sidebar key: home, all, authors, series, tags, status:reading (unread,
finished), shelf:NAME (a shelf's name or id), device:NAME (a device's name or id; --device
plugs in the demo Kobo, build/demo-device, and --page device shows the first device).
--book pushes a book's details page (TITLE is matched as a search would). --read opens the
reader window for the book and shoots that window once its page has rendered (BookView's
'relocated' signal, or --wait milliseconds at most). --dialog opens a dialog over the page
(edit, fetch, send and add are for --book's book, or the first book; fetch searches Open
Library, so it needs the network; shelf is New Smart Shelf) and shoots it. --search
searches the library. In the narrow layout the shot shows the sidebar, or the page when
--page is given (--sidebar keeps the sidebar).
"""

import argparse
import os
import sys

import harness

parser = argparse.ArgumentParser()
parser.add_argument('out', nargs='?', default=os.path.join(harness.ROOT, 'build',
                                                            'screenshot.png'))
parser.add_argument('--light', action='store_true')
parser.add_argument('--size', default='1100x760')
parser.add_argument('--page', default='home')
parser.add_argument('--book', metavar='TITLE')
parser.add_argument('--read', metavar='TITLE')
parser.add_argument('--sidebar', action='store_true')
parser.add_argument('--dialog', choices=['edit', 'fetch', 'shelf', 'preferences', 'about',
                                         'shortcuts', 'send', 'add'])
parser.add_argument('--search', metavar='QUERY')
parser.add_argument('--scroll', metavar='PX', type=int, default=0)
parser.add_argument('--device', action='store_true')
parser.add_argument('--wait', metavar='MS', type=int, default=8000,
                    help='the longest --read waits for the book to render')
args = parser.parse_args()
width, height = (int(n) for n in args.size.split('x'))
if args.page.startswith('device'):
    args.device = True

app = harness.make_app('Screenshot', light=args.light, size=(width, height),
                       device=args.device)

from gi.repository import Adw, GLib, Graphene, Gtk  # noqa: E402  (after make_app)

steps = []
failed = False
target_window = None


def find_book(title):
    """The id of the book whose title is `title` (exactly, else the first a search finds)."""
    for book in app.library.books(query=f'title:"{title}"'):
        if book.title.casefold() == title.casefold():
            return book.id
    books = app.library.books(query=title, limit=1)
    if not books:
        sys.exit(f'screenshot: no book called {title}')
    return books[0].id


def resolve_page(window):
    key = args.page
    if key.startswith('shelf:') and not key[6:].isdigit():
        shelf = next((s for s in app.library.shelves() if s.name == key[6:]), None)
        if shelf is None:
            sys.exit(f'screenshot: no shelf called {key[6:]}')
        key = f'shelf:{shelf.id}'
    elif key.startswith('device'):
        devices = app.devices.devices()
        name = key[7:]
        device = next((d for d in devices if not name or name in (d.name, d.id)), None)
        if device is None:
            sys.exit('screenshot: no device plugged in')
        key = f'device:{device.id}'
    window.show_root(key)


def book_id():
    if args.book:
        return find_book(args.book)
    if args.read:
        return find_book(args.read)
    return app.library.books(limit=1)[0].id


def open_dialog(window):
    name = args.dialog
    if name in ('preferences', 'about', 'shortcuts'):
        app.activate_action(name)
    elif name == 'add':
        # Adding the book's own file again: the dialog's report of a duplicate.
        from bookcase.dialogs import add_books

        add_books.present(app, window, [app.library.files(book_id())[0].path])
    elif name in ('edit', 'fetch'):
        from bookcase.dialogs import edit_metadata

        dialog = edit_metadata.present(app, window, [book_id()])
        if name == 'fetch':  # Find Metadata… over it: this searches Open Library, online
            GLib.timeout_add(600, lambda: dialog.find_metadata() and GLib.SOURCE_REMOVE)
    elif name == 'shelf':
        from bookcase.dialogs import shelf

        shelf.present_new(app, window, smart=True)
    elif name == 'send':
        from bookcase.dialogs import send

        send.present(app, window, [book_id()])


def search(window):
    window.search(args.search)


def scroll(window):
    page = window.navigation_view.get_visible_page()
    for scrolled in harness.descendants(page, Gtk.ScrolledWindow):
        if scrolled.props.vscrollbar_policy != Gtk.PolicyType.NEVER:
            adjustment = scrolled.get_vadjustment()
            adjustment.set_value(min(args.scroll,
                                     adjustment.get_upper() - adjustment.get_page_size()))
            return


def read(window):
    """Open the reader and shoot it when the book has drawn its first page."""
    global target_window
    before = set(Gtk.Window.list_toplevels())
    app.open_book(find_book(args.read))
    reader = next((w for w in Gtk.Window.list_toplevels()
                   if w not in before and isinstance(w, Adw.ApplicationWindow)), None)
    if reader is None:
        raise RuntimeError('the reader window did not open')
    target_window = reader
    from bookcase.widgets.book_view import BookView

    views = list(harness.descendants(reader, BookView))
    if not views or not reader.get_visible():
        raise RuntimeError('the reader window did not open the book')
    done = []

    def ready(*_args):
        if not done:
            done.append(True)
            # One more frame or two for the page's fonts and images to settle.
            GLib.timeout_add(900, shoot)
        return GLib.SOURCE_REMOVE

    def error(_view, message):
        print(f'screenshot: the book did not open: {message}', file=sys.stderr)

    views[0].connect('relocated', ready)
    views[0].connect('error', error)
    GLib.timeout_add(args.wait, ready)
    return 'wait'


def dialog_window(window):
    """The dialog's own window when libadwaita gave it one, else the window."""
    for toplevel in Gtk.Window.list_toplevels():
        if toplevel is not window and toplevel.get_visible() and toplevel.get_mapped():
            if any(True for _ in harness.descendants(toplevel, Adw.Dialog)):
                return toplevel
    return window


def draw_popovers(window, snapshot):
    window_x, window_y = window.get_surface_transform()
    for popover in harness.popovers(window):
        if not popover.get_mapped():
            continue
        surface = popover.get_surface()
        popover_x, popover_y = popover.get_surface_transform()
        point = Graphene.Point()
        point.x = surface.get_position_x() + popover_x - window_x
        point.y = surface.get_position_y() + popover_y - window_y
        snapshot.save()
        snapshot.translate(point)
        Gtk.WidgetPaintable(widget=popover).snapshot(
            snapshot, popover.get_width(), popover.get_height())
        snapshot.restore()


def shoot():
    global failed
    try:
        return _shoot()
    except Exception:
        import traceback

        traceback.print_exc()
        failed = True
        app.quit()
        return GLib.SOURCE_REMOVE


def _shoot():
    window = app.window() if hasattr(app, 'window') else app.get_active_window()
    if steps:
        step, delay = steps.pop(0)
        if step(window) == 'wait':
            return GLib.SOURCE_REMOVE  # the step calls shoot() itself
        GLib.timeout_add(delay, shoot)
        return GLib.SOURCE_REMOVE
    target = target_window or dialog_window(window)
    paintable = Gtk.WidgetPaintable(widget=target)
    snapshot = Gtk.Snapshot()
    paintable.snapshot(snapshot, target.get_width(), target.get_height())
    draw_popovers(target, snapshot)
    texture = target.get_renderer().render_texture(snapshot.to_node(), None)
    texture.save_to_png(args.out)
    print(args.out)
    app.quit()
    return GLib.SOURCE_REMOVE


def on_activate(_app):
    GLib.idle_add(plan)  # after do_activate has made the window


def plan():
    window = app.get_active_window()
    steps.append((resolve_page, 600))
    if window.split_view.get_collapsed() and args.sidebar:
        steps.append((lambda w: w.split_view.set_show_content(False), 300))
    if args.search:
        steps.append((search, 1200))
    if args.book:
        steps.append((lambda w: w.show_book(find_book(args.book)), 1000))
    if args.scroll:
        steps.append((scroll, 600))
    if args.dialog:
        steps.append((open_dialog, 1200))
    if args.read:
        steps.append((read, 0))
    GLib.timeout_add(1000, shoot)
    return GLib.SOURCE_REMOVE


app.connect('activate', on_activate)
harness.run_app(app)
if failed:
    sys.exit(1)
