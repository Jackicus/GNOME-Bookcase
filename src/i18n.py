# SPDX-License-Identifier: GPL-2.0-or-later
# SPDX-FileCopyrightText: 2026 Jack Tully

"""Translations: the one place the gettext domain is bound.

Two gettext implementations read the catalogues. C's libintl serves GtkBuilder, so the
Blueprint strings (`_("…")` in .blp files); Python's gettext module serves every `_()` and
`ngettext()` imported with `from gettext import gettext as _`, which looks up its own current
domain, not C's. setup() binds and selects the domain for both: the launcher calls it before
importing the app, and the developer scripts through scripts/harness.py. It also puts the
catalogue found then in place of gettext's lookups (see setup()).
"""

import gettext
import locale

DOMAIN = 'bookcase'


def setup(localedir, domain=DOMAIN):
    """Read `domain`'s catalogues from `localedir` (<localedir>/<lang>/LC_MESSAGES/<domain>.mo)
    for C libintl and for Python's gettext, and make it the default domain of both."""
    if hasattr(locale, 'bindtextdomain'):  # not on every platform's Python
        locale.bindtextdomain(domain, localedir)
        locale.textdomain(domain)
    gettext.bindtextdomain(domain, localedir)
    gettext.textdomain(domain)
    # gettext.gettext() looks for the catalogue on disk on every call (a dozen stats each,
    # 0.3 s of a large library's startup): the domain's translation, found once, answers
    # instead. The modules import gettext's functions after this runs.
    translation = gettext.translation(domain, localedir, fallback=True)
    gettext.gettext = translation.gettext
    gettext.ngettext = translation.ngettext
    gettext.pgettext = translation.pgettext
    gettext.npgettext = translation.npgettext
