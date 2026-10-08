# SPDX-License-Identifier: GPL-2.0-or-later
# SPDX-FileCopyrightText: 2026 Jack Tully

"""Convert… (converting.convert_book and dialogs/convert.py) with a fake ebook-convert put on
PATH in a temporary directory: a script that prints Calibre's 'NN% what' lines, writes the
output, fails or dawdles when asked (FAKE_CONVERT_MODE). EPUB to kepub needs no program."""

import hashlib
import os
import pathlib
import shutil
import sys
import tempfile
import textwrap
import time
import unittest
from unittest import mock

from tests import ROOT  # noqa: F401
from tests.gtk import requires_gtk, wait_for
from tests.support import add_book, make_epub, temporary_library
from tests.test_formats import make_mobi
from bookcase import converting, kepub
from bookcase.covers import CoverStore

FAKE = textwrap.dedent("""\
    #!{python}
    import os, shutil, sys, time
    src, dest = sys.argv[1:3]
    mode = os.environ.get('FAKE_CONVERT_MODE', 'ok')
    for number in (1, 34, 67, 100):
        print(f'{{number}}% Step {{number}}', flush=True)
        if mode == 'slow':
            time.sleep(0.5)
    if mode == 'fail':
        print('Conversion error: the input is broken', flush=True)
        sys.exit(1)
    if dest.endswith('.epub'):
        shutil.copyfile(os.environ.get('FAKE_EPUB') or src, dest)
    else:
        with open(dest, 'wb') as file:
            file.write(b'FAKE ' + dest.rsplit('.', 1)[1].encode())
""")


def digest(path):
    return hashlib.sha256(pathlib.Path(path).read_bytes()).hexdigest()


class ConvertTestCase(unittest.TestCase):

    def setUp(self):
        self.directory = pathlib.Path(tempfile.mkdtemp(prefix='bookcase-convert-'))
        bin_dir = self.directory / 'bin'
        bin_dir.mkdir()
        self.program = bin_dir / 'ebook-convert'
        self.program.write_text(FAKE.format(python=sys.executable))
        self.program.chmod(0o755)
        self.environ = mock.patch.dict(os.environ, {
            'PATH': f'{bin_dir}{os.pathsep}{os.environ.get("PATH", "")}',
            'FAKE_CONVERT_MODE': 'ok'})
        self.environ.start()
        self.books = self.directory / 'Books'
        (self.directory / 'source').mkdir()
        self.epub = make_epub(self.directory / 'source' / 'Harbour.epub', title='A Harbour')

    def tearDown(self):
        self.environ.stop()
        shutil.rmtree(self.directory, ignore_errors=True)


class TestConverting(ConvertTestCase):

    def test_ebook_convert_is_found_on_path(self):
        self.assertEqual(converting.ebook_convert(), str(self.program))

    def test_targets(self):
        offers = dict(converting.targets(('epub',), None))
        self.assertIsNone(offers['kepub'])  # Bookcase's own
        self.assertEqual(offers['epub'], 'Already in the library')
        self.assertEqual(offers['azw3'], 'Needs Calibre’s ebook-convert')
        offers = dict(converting.targets(('epub',), 'ebook-convert'))
        self.assertIsNone(offers['azw3'])
        self.assertIsNone(offers['pdf'])
        offers = dict(converting.targets(('pdf',), None))
        self.assertEqual(offers['kepub'], 'Needs Calibre’s ebook-convert')
        self.assertEqual(converting.source_format(('pdf', 'mobi', 'epub'), 'azw3'), 'epub')
        self.assertEqual(converting.source_format(('pdf', 'mobi'), 'epub'), 'mobi')

    def test_progress(self):
        seen = []
        dest = self.directory / 'out.azw3'
        converting.run_ebook_convert(str(self.epub), str(dest),
                                     progress=lambda f, text: seen.append((f, text)))
        self.assertEqual(seen, [(0.01, 'Step 1'), (0.34, 'Step 34'), (0.67, 'Step 67'),
                                (1.0, 'Step 100')])
        self.assertEqual(dest.read_bytes(), b'FAKE azw3')

    def test_failure_says_why(self):
        os.environ['FAKE_CONVERT_MODE'] = 'fail'
        with self.assertRaises(converting.ConversionError) as caught:
            converting.run_ebook_convert(str(self.epub), str(self.directory / 'out.mobi'))
        self.assertIn('the input is broken', str(caught.exception))

    def test_cancel_stops_it(self):
        os.environ['FAKE_CONVERT_MODE'] = 'slow'
        seen = []
        started = time.monotonic()
        with self.assertRaises(converting.ConversionCancelled):
            converting.run_ebook_convert(str(self.epub), str(self.directory / 'out.pdf'),
                                         progress=lambda f, text: seen.append(f),
                                         cancelled=lambda: bool(seen))
        self.assertLess(time.monotonic() - started, 1.9)  # not the whole 2 s
        self.assertFalse((self.directory / 'out.pdf').exists())

    def test_a_name_like_an_option_is_never_one(self):
        # ebook-convert is given absolute paths: '-x.epub' would be read as an option.
        record = self.directory / 'argv'
        self.program.write_text(FAKE.format(python=sys.executable).replace(
            'src, dest = sys.argv[1:3]',
            f'src, dest = sys.argv[1:3]\nopen({str(record)!r}, "w").write(repr(sys.argv[1:]))'))
        shutil.copyfile(self.epub, self.directory / '-x.epub')
        cwd = os.getcwd()
        os.chdir(self.directory)
        try:
            converting.run_ebook_convert('-x.epub', '--out.azw3')
        finally:
            os.chdir(cwd)
        argv = eval(record.read_text())  # noqa: S307 - our own repr()
        self.assertEqual(argv, [str(self.directory / '-x.epub'),
                                str(self.directory / '--out.azw3')])

    def test_no_program(self):
        with self.assertRaises(converting.ConversionError):
            converting.run_ebook_convert(str(self.epub), str(self.directory / 'x.pdf'),
                                         program=str(self.directory / 'missing'))


class TestConvertBook(ConvertTestCase):

    def convert(self, target, program='default', **kwargs):
        if program == 'default':
            program = str(self.program)
        with temporary_library() as library:
            covers = CoverStore(pathlib.Path(library.path).parent, library)
            book_id = add_book(library, 'A Harbour', ('Ada Lark',), path=self.source,
                               fmt=self.fmt)
            library.update_book(book_id, title='The Harbour at Night')
            before = digest(self.source)
            try:
                path = converting.convert_book(library, covers, book_id, target, self.books,
                                               program=program, **kwargs)
                book = library.book(book_id)
                files = {file.format: file.path for file in library.files(book_id)}
            finally:
                covers.shutdown()
            self.assertEqual(digest(self.source), before)  # the book's own file: only read
            return path, book, files

    def test_epub_to_azw3(self):
        self.source, self.fmt = self.epub, 'epub'
        path, book, files = self.convert('azw3')
        self.assertEqual(path, str(self.books / 'Ada Lark' / 'The Harbour at Night.azw3'))
        self.assertEqual(set(book.formats), {'epub', 'azw3'})
        self.assertEqual(files['azw3'], path)
        self.assertEqual(pathlib.Path(path).read_bytes(), b'FAKE azw3')

    def test_epub_to_kepub_needs_no_program(self):
        self.source, self.fmt = self.epub, 'epub'
        path, book, _files = self.convert('kepub', program=None)
        self.assertTrue(path.endswith('The Harbour at Night.kepub.epub'))
        self.assertIn('kepub', book.formats)
        self.assertTrue(kepub.is_kepub(path))

    def test_mobi_to_kepub_goes_through_ebook_convert(self):
        self.source = make_mobi(self.directory / 'source' / 'Line.mobi')
        self.fmt = 'mobi'
        os.environ['FAKE_EPUB'] = str(self.epub)
        path, book, _files = self.convert('kepub')
        self.assertTrue(kepub.is_kepub(path))
        self.assertEqual(set(book.formats), {'mobi', 'kepub'})

    def test_a_format_the_book_has_is_refused(self):
        self.source, self.fmt = self.epub, 'epub'
        with self.assertRaises(converting.ConversionError):
            self.convert('epub')

    def test_a_file_that_turns_up_meanwhile_is_never_written_over(self):
        # Another conversion (or the user) saves 'The Harbour at Night.azw3' after the free
        # name was chosen: that file stays, and the new one takes the next name.
        self.source, self.fmt = self.epub, 'epub'
        from bookcase import importing

        real = importing.library_path
        theirs = self.books / 'Ada Lark' / 'The Harbour at Night.azw3'

        def library_path(folder, book, suffix):
            path = real(folder, book, suffix)
            if not theirs.exists():
                theirs.parent.mkdir(parents=True, exist_ok=True)
                theirs.write_bytes(b'theirs')
            return path

        with mock.patch.object(importing, 'library_path', library_path):
            path, _book, _files = self.convert('azw3')
        self.assertEqual(theirs.read_bytes(), b'theirs')
        self.assertEqual(path, str(self.books / 'Ada Lark' / 'The Harbour at Night (2).azw3'))
        self.assertEqual(pathlib.Path(path).read_bytes(), b'FAKE azw3')
        self.assertEqual(sorted(p.name for p in theirs.parent.iterdir()),
                         ['The Harbour at Night (2).azw3', 'The Harbour at Night.azw3'])

    def test_cancelled_adds_nothing(self):
        self.source, self.fmt = self.epub, 'epub'
        os.environ['FAKE_CONVERT_MODE'] = 'slow'
        with self.assertRaises(converting.ConversionCancelled):
            self.convert('pdf', cancelled=lambda: True)
        self.assertFalse(self.books.exists() and any(self.books.rglob('*.pdf')))


@requires_gtk
class TestConvertDialog(ConvertTestCase):

    def test_without_ebook_convert_only_kepub(self):
        from tests.dialog_support import fake_app
        from bookcase.dialogs import convert

        with fake_app() as app:
            book_id = add_book(app.library, 'A Harbour', path=self.epub)
            with mock.patch.object(converting, 'ebook_convert', return_value=None):
                dialog = convert.ConvertDialog(app, book_id)
            possible = [target for target in dialog._radios if dialog._possible(target)]
            self.assertEqual(possible, ['kepub'])
            self.assertEqual(dialog.selected(), 'kepub')

    def test_convert_adds_the_format_with_undo(self):
        from tests.dialog_support import fake_app
        from bookcase.dialogs import convert

        with fake_app() as app:
            app.library_folder = lambda: str(self.books)
            book_id = add_book(app.library, 'A Harbour', path=self.epub)
            dialog = convert.ConvertDialog(app, book_id, program=str(self.program))
            dialog._radios['azw3'].set_active(True)
            self.assertEqual(dialog.selected(), 'azw3')
            dialog.start('azw3')
            wait_for(lambda: app.toasts, timeout=10)
            self.assertIn('azw3', app.library.book(book_id).formats)
            self.assertEqual(app.toasts[-1][1], True)
            app.undo()
            self.assertNotIn('azw3', app.library.book(book_id).formats)


if __name__ == '__main__':
    unittest.main()
