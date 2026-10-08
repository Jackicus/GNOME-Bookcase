# SPDX-License-Identifier: GPL-2.0-or-later
# SPDX-FileCopyrightText: 2026 Jack Tully

"""The translatable strings: what xgettext and a translator need of them.

Every `_()`, `ngettext()` and `pgettext()` in src/ takes literal strings (an f-string or a
variable never reaches the catalogue); placeholders are named (`{title}`, `{n}`), never `{}`
or `%s`, so a translator can reorder them; a count goes through ngettext, never `_('{n}
books')`, nor an untranslated f-string; a string with no word of its own ("{percent}%",
"{title}\\n{author}") has a translator comment. And po/POTFILES.in, run through xgettext as
`meson compile -C build bookcase-pot` runs it, gives strings from the Python, the Blueprint,
the desktop file, the metainfo and the settings schema.
"""

import ast
import re
import shutil
import string
import subprocess
import tempfile
import unittest

from tests import ROOT

SRC = ROOT / 'src'
# The literal arguments of each gettext function.
FUNCTIONS = {'_': 1, 'gettext': 1, 'N_': 1, 'ngettext': 2, 'pgettext': 2, 'npgettext': 3}
# A count's placeholder followed by a plural noun, in a string that is not ngettext's.
COUNT = re.compile(r'\{(?:n|count|number|total)\}\s+[a-z]+s\b')
# An untranslated count in an f-string: f'{n} books'.
F_COUNT = re.compile(r"""f['"]\{[^}]+\} (?:books?|files?|items?|pages?|results?|words?|"""
                     r"""highlights?|minutes?|hours?|days?)\b""")
# meson's i18n.gettext(preset: 'glib') arguments (po/meson.build).
XGETTEXT_ARGS = ['--from-code=UTF-8', '--add-comments', '--keyword=_', '--keyword=N_',
                 '--keyword=C_:1c,2', '--keyword=NC_:1c,2', '--keyword=g_dcgettext:2',
                 '--keyword=g_dngettext:2,3', '--keyword=g_dpgettext2:2c,3']


def calls():
    """(where, function name, [argument nodes], lines before the call) of every gettext call
    in src/."""
    for path in sorted(SRC.rglob('*.py')):
        if 'foliate' in path.parts:
            continue
        text = path.read_text(encoding='utf-8')
        lines = text.splitlines()
        for node in ast.walk(ast.parse(text)):
            if isinstance(node, ast.Call) and isinstance(node.func, ast.Name) \
                    and node.func.id in FUNCTIONS:
                where = f'{path.relative_to(ROOT)}:{node.lineno}'
                before = '\n'.join(lines[max(0, node.lineno - 4):node.lineno])
                yield where, node.func.id, node.args[:FUNCTIONS[node.func.id]], before


def fields(text):
    return [field for _literal, field, _spec, _conversion in string.Formatter().parse(text)
            if field is not None]


class StringsTest(unittest.TestCase):
    def test_gettext_takes_literal_strings(self):
        found = [f'{where}: {ast.unparse(argument)[:60]}'
                 for where, _name, arguments, _before in calls() for argument in arguments
                 if not (isinstance(argument, ast.Constant) and isinstance(argument.value, str))]
        self.assertEqual(found, [])

    def test_placeholders_are_named(self):
        found = []
        for where, _name, arguments, _before in calls():
            for argument in arguments:
                text = getattr(argument, 'value', '')
                if not isinstance(text, str):
                    continue
                try:
                    names = fields(text)
                except ValueError:
                    found.append(f'{where}: {text!r} is no format string')
                    continue
                if any(name == '' or name.isdigit() for name in names) \
                        or re.search(r'%[sdif]', text):
                    found.append(f'{where}: {text!r}')
        self.assertEqual(found, [])

    def test_counts_go_through_ngettext(self):
        found = [f'{where}: {arguments[0].value!r}'
                 for where, name, arguments, _before in calls()
                 if name in ('_', 'gettext') and isinstance(arguments[0], ast.Constant)
                 and COUNT.search(str(arguments[0].value))]
        for path in sorted(SRC.rglob('*.py')):
            for number, line in enumerate(path.read_text(encoding='utf-8').splitlines(), 1):
                if F_COUNT.search(line) and 'log.' not in line:
                    found.append(f'{path.relative_to(ROOT)}:{number}: {line.strip()}')
        self.assertEqual(found, [])

    def test_strings_without_words_have_a_translator_comment(self):
        found = []
        for where, _name, arguments, before in calls():
            text = getattr(arguments[0], 'value', '') if arguments else ''
            if not isinstance(text, str) or not fields(text):
                continue
            words = re.sub(r'\{[^}]*\}', '', text)
            if not re.search(r'[^\W\d_]{2,}', words) and 'Translators' not in before:
                found.append(f'{where}: {text!r}')
        self.assertEqual(found, [])


@unittest.skipUnless(shutil.which('xgettext'), 'xgettext is not installed')
class CatalogueTest(unittest.TestCase):
    def test_the_template_has_every_kind_of_source(self):
        with tempfile.TemporaryDirectory() as directory:
            pot = f'{directory}/bookcase.pot'
            done = subprocess.run(
                ['xgettext', *XGETTEXT_ARGS, f'--directory={ROOT}',
                 f'--files-from={ROOT / "po" / "POTFILES.in"}', f'--output={pot}'],
                capture_output=True, text=True)
            self.assertEqual(done.returncode, 0, done.stderr)
            with open(pot, encoding='utf-8') as file:
                text = file.read()
        sources = set(re.findall(r'^#: .*', text, re.MULTILINE))
        references = ' '.join(sources)
        for suffix in ('.py:', '.blp:', '.desktop.in:', '.metainfo.xml.in:', '.gschema.xml:'):
            self.assertIn(suffix, references)
        self.assertIn('msgid "Reading Progress"', text)  # Blueprint and Python both
        self.assertIn('msgid_plural', text)
        self.assertIn('#. Translators:', text)


if __name__ == '__main__':
    unittest.main()
