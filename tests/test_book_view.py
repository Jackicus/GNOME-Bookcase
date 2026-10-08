# SPDX-License-Identifier: GPL-2.0-or-later
# SPDX-FileCopyrightText: 2026 Jack Tully

"""The book view: the bookcase:// scheme (without WebKit), and the reader page opening a
generated EPUB in a real WebKit view (skipped without WebKit, with BOOKCASE_NO_WEBKIT=1, or
when its web process cannot start). The WebKit tests share one view, opened once: a page
takes a second or two to load, more than a widget test's usual budget."""

import pathlib
import shutil
import tempfile
import unittest
import zipfile
from unittest import mock

from tests import ROOT
from tests.gtk import RESOURCES, requires_gtk, wait_for
from tests.support import make_epub

from bookcase import reading

STYLE_SETTINGS = {
    'reader-theme': 'light', 'reader-font': 'publisher', 'reader-custom-font': '',
    'reader-font-size': 18, 'reader-line-height': 1.5, 'reader-margin': 8,
    'reader-max-width': 720, 'reader-justify': True, 'reader-hyphenate': True,
    'reader-publisher-styles': True, 'reader-scrolled': False, 'reader-two-pages': False,
    'reader-animate': False,
}
STYLE = reading.build_style(STYLE_SETTINGS.get, False, 'A Quiet Harbour')
WAIT = 15  # seconds for the page to load (a cold WebKit start in CI is slow)


class FakeRequest:
    """What serve() is given: a URISchemeRequest's get_uri, finish and finish_error."""

    def __init__(self, uri):
        self.uri = uri
        self.data = None
        self.length = None
        self.type = None
        self.error = None

    def get_uri(self):
        return self.uri

    def finish(self, stream, length, content_type):
        chunks = []
        while True:
            chunk = stream.read_bytes(65536, None).get_data()
            if not chunk:
                break
            chunks.append(chunk)
        self.data = b''.join(chunks)
        self.length = length
        self.type = content_type

    def finish_error(self, error):
        self.error = error


def _load_resource():
    from gi.repository import Gio

    for path in RESOURCES:
        if path.exists():
            try:
                Gio.resources_lookup_data('/io/github/jackicus/Bookcase/reader/reader.html',
                                          Gio.ResourceLookupFlags.NONE)
            except Exception:  # noqa: BLE001  (not registered yet)
                Gio.Resource.load(str(path))._register()
            return True
    return False


class SchemeTest(unittest.TestCase):

    @classmethod
    def setUpClass(cls):
        if not _load_resource():
            raise unittest.SkipTest('build/src/bookcase.gresource is missing (meson compile)')
        from bookcase.widgets import book_view

        cls.book_view = book_view

    def serve(self, uri):
        request = FakeRequest(uri)
        self.book_view.serve(request)
        return request

    def test_serves_the_reader_page_and_foliate(self):
        page = self.serve('bookcase://reader/reader.html')
        self.assertIsNone(page.error)
        self.assertEqual(page.type, 'text/html')
        self.assertIn(b'Content-Security-Policy', page.data)
        self.assertEqual(page.data, (ROOT / 'src' / 'reader' / 'reader.html').read_bytes())
        script = self.serve('bookcase://reader/foliate/view.js')
        self.assertEqual(script.type, 'text/javascript')
        self.assertIn(b'foliate-view', script.data)
        self.assertEqual(self.serve('bookcase://reader/reader.css').type, 'text/css')
        self.assertIsNotNone(self.serve('bookcase://reader/foliate/vendor/zip.js').data)

    def test_serves_an_open_book_by_its_token(self):
        directory = pathlib.Path(tempfile.mkdtemp(prefix='bookcase-test-'))
        self.addCleanup(shutil.rmtree, directory, ignore_errors=True)
        path = make_epub(directory / 'book.epub')
        self.book_view._books['0123abcd'] = str(path)
        self.addCleanup(self.book_view._books.pop, '0123abcd', None)
        book = self.serve('bookcase://reader/book/0123abcd.epub')
        self.assertEqual(book.data, path.read_bytes())
        self.assertEqual(book.length, path.stat().st_size)
        with self.assertLogs('bookcase.widgets.book_view', 'WARNING'):
            self.assertIsNotNone(self.serve('bookcase://reader/book/ffff0000.epub').error)

    def test_serves_nothing_else(self):
        for uri in ('bookcase://other/reader.html', 'bookcase://reader/../library.py',
                    'bookcase://reader/missing.js', 'bookcase://reader/%2E%2E/main.py'):
            with self.assertLogs('bookcase.widgets.book_view', 'WARNING'):
                request = self.serve(uri)
            self.assertIsNotNone(request.error, uri)
            self.assertIsNone(request.data, uri)


def _epub_with(path, addition):
    """make_epub()'s book with `addition` after its first chapter's heading."""
    plain = make_epub(path.with_name('plain-' + path.name), chapters=2)
    with zipfile.ZipFile(plain) as source, zipfile.ZipFile(path, 'w') as target:
        for info in source.infolist():
            data = source.read(info.filename)
            if info.filename.endswith('chapter1.xhtml'):
                data = data.replace(b'<html ', b'<html xmlns:epub="http://www.idpf.org/2007/ops" ')
                data = data.replace(b'</h1>', b'</h1>' + addition.encode())
            target.writestr(info, data)
    return path


def _scripted_epub(path):
    """A book with a script in its first chapter, which must never run."""
    return _epub_with(path, '<script>try { window.parent.webkit.messageHandlers.bookcase.'
                      'postMessage(JSON.stringify({type: "error", message: "the book ran a '
                      'script"})) } catch (e) {}</script>')


def _linked_epub(path):
    """A book whose first chapter has a footnote and a link out."""
    return _epub_with(path, '<p>A note<a epub:type="noteref" href="#n1" id="r1">1</a> and '
                      '<a id="out" href="https://example.invalid/harbour">a link out</a>.</p>'
                      '<aside epub:type="footnote" id="n1"><p>The invented note.</p></aside>')


class _Opened:
    """A window with a BookView showing a book, and the signals it has sent."""

    def __init__(self, path, fmt='epub'):
        from gi.repository import Gtk

        from bookcase.widgets.book_view import BookView

        self.window = Gtk.Window(default_width=800, default_height=600)
        self.view = BookView()
        self.window.set_child(self.view)
        self.window.present()
        self.events = []
        for name in ('loaded', 'toc-ready', 'relocated', 'search-result', 'search-done',
                     'error', 'selection'):
            self.view.connect(name, lambda _view, *args, name=name:
                              self.events.append((name, args[0] if args else None)))
        self.view.open(str(path), fmt, style=STYLE)

    def of(self, name):
        return [value for kind, value in self.events if kind == name]

    def wait_for_page(self):
        """Whether the page loaded (False: WebKit could not start here)."""
        return wait_for(lambda: self.view._ready, WAIT)

    def close(self):
        self.view.close()
        self.window.destroy()


@requires_gtk
class BookViewTest(unittest.TestCase):

    @classmethod
    def setUpClass(cls):
        from bookcase.widgets import book_view

        if not book_view.available():
            raise unittest.SkipTest('WebKitGTK 6.0 is not available (or BOOKCASE_NO_WEBKIT)')
        cls.directory = pathlib.Path(tempfile.mkdtemp(prefix='bookcase-test-'))
        cls.book = cls.directory / 'A Quiet Harbour.epub'
        make_epub(cls.book, chapters=4)
        cls.opened = _Opened(cls.book)
        if not cls.opened.wait_for_page():
            cls.opened.close()
            raise unittest.SkipTest('the WebKit web process did not start')
        if not wait_for(lambda: cls.opened.of('relocated'), WAIT):
            raise AssertionError(f'the book never showed: {cls.opened.events}')

    @classmethod
    def tearDownClass(cls):
        cls.opened.close()
        shutil.rmtree(cls.directory, ignore_errors=True)

    def wait_for_chapter(self, label):
        opened = self.opened
        ok = wait_for(lambda: any((place.get('chapter') or {}).get('label') == label
                                  for place in opened.of('relocated')[-1:]), WAIT)
        self.assertTrue(ok, f'never reached {label}: {opened.of("relocated")[-1:]}')

    def test_01_opens_at_the_start(self):
        loaded = self.opened.of('loaded')[0]
        self.assertEqual(loaded['title'], 'A Quiet Harbour')
        self.assertEqual([item['label'] for item in loaded['toc']],
                         ['Chapter 1', 'Chapter 2', 'Chapter 3', 'Chapter 4'])
        self.assertEqual(self.opened.of('toc-ready')[0], loaded['toc'])
        self.assertEqual(len(loaded['sectionFractions']), 5)
        self.wait_for_chapter('Chapter 1')
        place = self.opened.of('relocated')[-1]
        self.assertTrue(place['cfi'].startswith('epubcfi('))
        self.assertTrue(place['start'].startswith('epubcfi('))
        self.assertGreaterEqual(place['fraction'] or 0, 0)
        self.assertEqual(self.opened.view.location, place['cfi'])
        self.assertEqual(self.opened.of('error'), [])

    def navigate(self, call, label):
        """call(), then the relocations it brings, ending in chapter `label`."""
        before = len(self.opened.of('relocated'))
        call()

        def arrived():
            moves = self.opened.of('relocated')[before:]
            return moves and (moves[-1].get('chapter') or {}).get('label') == label
        self.assertTrue(wait_for(arrived, WAIT), f'never reached {label}')
        return self.opened.of('relocated')[before:]

    def test_02_goes_to_chapters_and_fractions(self):
        view = self.opened.view
        self.navigate(view.next_section, 'Chapter 2')
        moves = self.navigate(lambda: view.go_to_fraction(0.9), 'Chapter 4')
        self.assertTrue(any(place['jumpedFrom'] for place in moves))
        self.navigate(lambda: view.go_to('OEBPS/chapter3.xhtml'), 'Chapter 3')
        self.navigate(view.start, 'Chapter 1')

    def test_03_bookmarks_are_found_on_the_page(self):
        place = self.opened.of('relocated')[-1]
        found = []
        self.opened.view.set_bookmarks([place['start'], 'epubcfi(/6/8!/4/2)'],
                                       found.append)
        self.assertTrue(wait_for(lambda: found, 5))
        self.assertEqual(found[0], place['start'])

    def test_04_highlights_are_drawn(self):
        place = self.opened.of('relocated')[-1]
        done = []
        self.opened.view.set_annotations([{'cfi': place['cfi'], 'color': 'green'}])
        self.opened.view.evaluate(
            'const { overlayer } = globalThis.reader._contents();'
            'return overlayer?.element?.querySelectorAll("g").length ?? -1', done.append)
        self.assertTrue(wait_for(lambda: done, 5))
        self.assertGreaterEqual(done[0], 1)

    def test_05_searches(self):
        self.opened.view.search('harbour')
        self.assertTrue(wait_for(lambda: self.opened.of('search-done'), WAIT))
        done = self.opened.of('search-done')[-1]
        results = self.opened.of('search-result')
        self.assertEqual(done['query'], 'harbour')
        self.assertEqual(done['count'], sum(len(r['items']) for r in results))
        self.assertGreater(done['count'], 0)
        item = results[0]['items'][0]
        self.assertTrue(item['cfi'].startswith('epubcfi('))
        self.assertEqual(item['match'].lower(), 'harbour')
        self.opened.view.clear_search()

    def test_05_finds_imported_highlights_by_text(self):
        found = []
        text = ('Nobody on the quay said   much; the gulls said enough for everyone.\n'
                'A cart of crates rolled past the chandlery')
        self.opened.view.find_texts([{'id': 7, 'text': text},
                                     {'id': 8, 'text': 'Words never written in it'}],
                                    found.append)
        self.assertTrue(wait_for(lambda: found, WAIT))
        self.assertEqual([item['id'] for item in found[0]], [7])
        self.assertTrue(found[0][0]['cfi'].startswith('epubcfi('))
        self.assertIn(',', found[0][0]['cfi'])  # a range, from the first words to the last

    def test_06_a_selection_is_reported(self):
        self.opened.view.evaluate(
            'const { doc } = globalThis.reader._contents();'
            'const p = doc.querySelector("p"); const range = doc.createRange();'
            'range.setStart(p.firstChild, 4); range.setEnd(p.firstChild, 9);'
            'doc.getSelection().removeAllRanges(); doc.getSelection().addRange(range);'
            'doc.dispatchEvent(new PointerEvent("pointerup")); return true')
        self.assertTrue(wait_for(lambda: [s for s in self.opened.of('selection') if s], 5))
        selection = [s for s in self.opened.of('selection') if s][-1]
        self.assertEqual(selection['text'], 'lamps')
        self.assertTrue(selection['cfi'].startswith('epubcfi('))
        self.assertGreaterEqual(selection['rect']['width'], 1)

    def test_07_the_book_s_scripts_never_run(self):
        path = _scripted_epub(self.directory / 'scripted.epub')
        opened = _Opened(path)
        self.addCleanup(opened.close)
        self.assertTrue(opened.wait_for_page())
        self.assertTrue(wait_for(lambda: opened.of('relocated'), WAIT))
        counts = []

        def count():
            opened.view.evaluate(
                'const { doc } = globalThis.reader._contents();'
                'return [doc?.querySelectorAll("p").length ?? 0,'
                ' doc?.querySelectorAll("script").length ?? 0]', counts.append)
            return wait_for(lambda: counts, 5) and counts.pop()[0] > 0 or counts.clear()

        self.assertTrue(wait_for(count, 5))
        opened.view.evaluate('const { doc } = globalThis.reader._contents();'
                             'return doc.querySelectorAll("script").length', counts.append)
        self.assertTrue(wait_for(lambda: counts, 5))
        self.assertEqual(counts[0], 1)  # the script is in the section, and blocked
        wait_for(lambda: False, 0.5)
        self.assertEqual(opened.of('error'), [])

    def run_js(self, opened, script):
        result = []
        opened.view.evaluate(script, result.append)
        self.assertTrue(wait_for(lambda: result, 5))
        return result[0]

    def click(self, opened, where):
        """A click at a fraction of the page's width, in the section."""
        self.run_js(opened, 'const { doc } = globalThis.reader._contents();'
                    f'const x = innerWidth * {where};'
                    'doc.body.dispatchEvent(new MouseEvent("click", { bubbles: true, '
                    'clientX: x - doc.defaultView.frameElement.getBoundingClientRect().left, '
                    'clientY: 200, button: 0, detail: 1 })); return true')

    def test_09_clicks_and_the_wheel_turn_pages(self):
        opened = self.opened
        chrome = []
        opened.view.connect('toggle-chrome', lambda *_args: chrome.append(True))
        opened.view.clear_selection()  # a click would dismiss it, turning nothing
        opened.view.set_annotations([])  # a click on a highlight opens it instead
        before = len(opened.of('relocated'))
        opened.view.start()
        self.assertTrue(wait_for(lambda: len(opened.of('relocated')) > before, 5))
        wait_for(lambda: False, 0.3)  # the section's document settled
        start = opened.of('relocated')[-1]['fraction']
        self.click(opened, 0.9)
        self.assertTrue(wait_for(lambda: opened.of('relocated')[-1]['fraction'] > start, 5))
        self.click(opened, 0.5)
        self.assertTrue(wait_for(lambda: chrome, 5))
        self.run_js(opened, 'const { doc } = globalThis.reader._contents();'
                    'doc.body.dispatchEvent(new WheelEvent("wheel", { bubbles: true, '
                    'cancelable: true, deltaY: -120 })); return true')
        self.assertTrue(wait_for(lambda: opened.of('relocated')[-1]['fraction'] == start, 5))

    def test_10_footnotes_and_links_out(self):
        from bookcase.widgets.book_view import BookView

        opened = _Opened(_linked_epub(self.directory / 'linked.epub'))
        self.addCleanup(opened.close)
        self.assertTrue(opened.wait_for_page())
        self.assertTrue(wait_for(lambda: opened.of('relocated'), WAIT))
        self.assertTrue(wait_for(lambda: self.run_js(
            opened, 'return !!globalThis.reader._contents().doc?.getElementById("r1")'), 5))
        self.run_js(opened, 'globalThis.reader._contents().doc.getElementById("r1").click();'
                    'return true')
        self.assertTrue(wait_for(lambda: self.run_js(
            opened, 'return !document.getElementById("footnote").hidden'), 5))
        with mock.patch.object(BookView, '_open_external') as open_external:
            self.run_js(opened, 'globalThis.reader._contents().doc.getElementById("out")'
                        '.click(); return true')
            self.assertTrue(wait_for(lambda: open_external.called, 5))
        open_external.assert_called_with('https://example.invalid/harbour')

    def test_99_the_page_comes_back_after_a_crash(self):
        opened = self.opened
        place = opened.of('relocated')[-1]
        before = len(opened.events)
        opened.view.web_view.terminate_web_process()
        self.assertTrue(wait_for(lambda: any(
            kind == 'relocated' and value['fraction'] is not None
            for kind, value in opened.events[before:]), WAIT))
        wait_for(lambda: False, 0.3)
        again = opened.of('relocated')[-1]
        self.assertEqual((again['chapter'], again['fraction']),
                         (place['chapter'], place['fraction']))
        self.navigate(opened.view.next_section, 'Chapter 2')

    def test_08_a_large_book_is_handed_over_as_a_file(self):
        from bookcase.widgets import book_view

        limit = book_view.LARGE_FILE
        book_view.LARGE_FILE = 0
        self.addCleanup(setattr, book_view, 'LARGE_FILE', limit)
        opened = _Opened(self.book)
        self.addCleanup(opened.close)
        self.assertTrue(opened.view._book['fileInput'])
        self.assertTrue(opened.wait_for_page())
        self.assertTrue(wait_for(lambda: opened.of('relocated'), WAIT), opened.events)


if __name__ == '__main__':
    unittest.main()
