# SPDX-License-Identifier: GPL-2.0-or-later
# SPDX-FileCopyrightText: 2026 Jack Tully

"""screenshot.py's Discover scenes, over the invented catalogue of demo_catalog.py (served
from a thread on 127.0.0.1: no internet).

    catalog_shots.setup(app)                 # the demo catalogue served and listed
    catalog_shots.open_catalog(window)       # its New Arrivals page pushed
    catalog_shots.open_entry(window, title)  # a book's sheet over it
"""

import demo_catalog

_server = None


def setup(app):
    global _server
    from bookcase import opds

    if _server is None:
        _server = demo_catalog.serve()
    title, url, description = _server.catalog()
    catalogs = opds.builtin_catalogs()
    catalogs.insert(0, opds.Catalog('demo', title, url, description=description))
    app.settings.set_string('catalogs', opds.dump_catalogs(catalogs))


def open_catalog(window):
    window.show_root('discover')
    page = window.navigation_view.get_visible_page().open_catalog('demo')
    url = _server.url('/opds/new')
    page.url = url
    page.load()
    return page


def open_entry(window, title):
    from bookcase import opds
    from bookcase.dialogs import catalog_entry
    from bookcase.pages.catalog import CatalogPage

    page = window.navigation_view.get_visible_page()
    if not isinstance(page, CatalogPage) or page.feed is None:
        raise RuntimeError('open the catalogue first (--catalog)')
    entry = next((tile.entry for tile in page._tiles
                  if tile.entry.title.casefold() == title.casefold()), None)
    if entry is None:
        raise RuntimeError(f'no book called {title} in the catalogue')
    return catalog_entry.present(window.get_application(), window, page.catalog, entry,
                                 page.client or opds.Client())
