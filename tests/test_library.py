# SPDX-License-Identifier: GPL-2.0-or-later
# SPDX-FileCopyrightText: 2026 Jack Tully

"""The library: adding, reading, filtering, sorting and editing books, undo, shelves,
annotations, folders, workers, and the test helpers that make books."""

import sqlite3
import threading
import time
import unittest
import zipfile

from tests import ROOT  # noqa: F401
from bookcase import schema
from bookcase.formats import BookInfo
from bookcase.library import Book, Library, LibraryError, format_of
from tests.support import add_book, make_epub, make_png, snapshot, temporary_library


class Recorder:
    """Collects the kinds a library's `changed` signal gives."""

    def __init__(self, library):
        self.kinds = []
        library.connect('changed', lambda _library, kind: self.kinds.append(kind))


class SupportTest(unittest.TestCase):

    def test_make_png_is_a_png(self):
        data = make_png(3, 2, (10, 20, 30))
        self.assertTrue(data.startswith(b'\x89PNG\r\n\x1a\n'))
        self.assertEqual(data[16:24], (3).to_bytes(4, 'big') + (2).to_bytes(4, 'big'))

    def test_make_epub_is_a_valid_container(self):
        with temporary_library() as library:
            path = make_epub(library.path.parent / 'a.epub', title='The Hobbit & Co',
                             authors=('Ada Lark', 'Ben Ross'), series='Harbour',
                             series_index=2, isbn='9780000000002', cover=make_png(4, 4),
                             tags=('Sea',), description='<p>Waves</p>')
            with zipfile.ZipFile(path) as archive:
                first = archive.infolist()[0]
                self.assertEqual(first.filename, 'mimetype')
                self.assertEqual(first.compress_type, zipfile.ZIP_STORED)
                self.assertEqual(archive.read('mimetype'), b'application/epub+zip')
                names = archive.namelist()
                self.assertIn('META-INF/container.xml', names)
                self.assertIn('OEBPS/nav.xhtml', names)
                self.assertIn('OEBPS/cover.png', names)
                self.assertEqual(sum(name.startswith('OEBPS/chapter') for name in names), 3)
                opf = archive.read('OEBPS/content.opf').decode()
                self.assertIn('The Hobbit &amp; Co', opf)
                self.assertIn('calibre:series', opf)
                self.assertIn('9780000000002', opf)

    def test_make_epub2(self):
        with temporary_library() as library:
            path = make_epub(library.path.parent / 'b.epub', epub3=False, chapters=1)
            with zipfile.ZipFile(path) as archive:
                self.assertNotIn('OEBPS/nav.xhtml', archive.namelist())
                self.assertIn('version="2.0"', archive.read('OEBPS/content.opf').decode())


class BookTest(unittest.TestCase):

    def test_author_label(self):
        book = Book(id=1, uuid='u', title='T', sort_title='T')
        self.assertEqual(book.author, 'Unknown Author')
        self.assertEqual(Book(1, 'u', 'T', 'T', authors=('Ada Lark',)).author, 'Ada Lark')
        self.assertEqual(Book(1, 'u', 'T', 'T', authors=('Ada Lark', 'Ben Ross')).author,
                         'Ada Lark and Ben Ross')
        self.assertEqual(Book(1, 'u', 'T', 'T', authors=('A', 'B', 'C')).author,
                         'A and 2 others')

    def test_format_of(self):
        self.assertEqual(format_of('/x/Book.EPUB'), 'epub')
        self.assertEqual(format_of('/x/Book.kepub.epub'), 'kepub')
        self.assertEqual(format_of('/x/Book.pdf'), 'pdf')


class AddAndReadTest(unittest.TestCase):

    def test_add_book_keeps_its_metadata(self):
        with temporary_library() as library:
            info = BookInfo(title='The Quiet Harbour', authors=['Ada Lark', 'Ben Ross'],
                            series='Harbour Tales', series_index=2.5, tags=['Sea', 'Mystery'],
                            publisher='Gull Press', published='2024-05', language='en',
                            description='<p>Waves.</p>',
                            identifiers={'isbn': '9780000000002',
                                         'uuid': '0b6d1b7e-6a9c-4b8f-9b7e-1c2d3e4f5a6b'},
                            format='epub')
            book_id = library.add_book(info, '/books/harbour.epub', hash='h1', size=1234)
            book = library.book(book_id)
            self.assertEqual(book.title, 'The Quiet Harbour')
            self.assertEqual(book.sort_title, 'Quiet Harbour, The')
            self.assertEqual(book.authors, ('Ada Lark', 'Ben Ross'))
            self.assertEqual(book.author_sort, 'Lark, Ada & Ross, Ben')
            self.assertEqual(book.series, 'Harbour Tales')
            self.assertEqual(book.series_index, 2.5)
            self.assertEqual(book.tags, ('Mystery', 'Sea'))
            self.assertEqual(book.identifiers, {'isbn': '9780000000002'})
            self.assertEqual(book.uuid, '0b6d1b7e-6a9c-4b8f-9b7e-1c2d3e4f5a6b')
            self.assertEqual(book.formats, ('epub',))
            self.assertEqual(book.status, 'unread')
            self.assertFalse(book.missing)
            self.assertEqual(library.find_by_hash('h1'), book_id)
            self.assertIsNone(library.find_by_hash('nope'))
            self.assertEqual(library.find_file('/books/harbour.epub').size, 1234)
            self.assertIsNone(library.book(9999))

    def test_a_second_copy_gets_a_new_uuid(self):
        with temporary_library() as library:
            info = BookInfo(title='T', identifiers={'uuid': 'urn:uuid:0b6d1b7e-6a9c-4b8f-'
                                                           '9b7e-1c2d3e4f5a6b'})
            first = library.add_book(info, '/a.epub', hash='a', size=1)
            second = library.add_book(info, '/b.epub', hash='b', size=1)
            self.assertNotEqual(library.book(first).uuid, library.book(second).uuid)

    def test_title_falls_back_on_the_file_name(self):
        with temporary_library() as library:
            book_id = library.add_book(BookInfo(), '/x/Some Story.txt', hash='', size=1)
            book = library.book(book_id)
            self.assertEqual(book.title, 'Some Story')
            self.assertEqual(book.formats, ('txt',))

    def test_files_in_reading_order(self):
        with temporary_library() as library:
            book_id = add_book(library, 'T', path='/x/t.pdf', fmt='pdf')
            library.add_file(book_id, '/x/t.epub', hash='e', size=5)
            library.add_file(book_id, '/x/t.kepub.epub', hash='k', size=5)
            self.assertEqual([file.format for file in library.files(book_id)],
                             ['epub', 'kepub', 'pdf'])
            self.assertEqual(library.book(book_id).formats, ('epub', 'kepub', 'pdf'))
            epub = library.files(book_id)[0]
            library.set_missing([epub.id])
            self.assertEqual(library.reading_file(book_id).format, 'kepub')
            self.assertFalse(library.book(book_id).missing)
            library.set_missing([file.id for file in library.files(book_id)])
            self.assertTrue(library.book(book_id).missing)
            self.assertIsNone(library.reading_file(book_id))
            library.set_file_path(epub.id, '/y/t.epub')
            self.assertEqual(library.reading_file(book_id).path, '/y/t.epub')
            with self.assertRaises(LibraryError):
                library.add_file(book_id, '/x/t.pdf', hash='p', size=1)

    def test_find_similar(self):
        with temporary_library() as library:
            harbour = add_book(library, 'The Quiet Harbour', ('Ada Lark',))
            add_book(library, 'The Quiet Harbour', ('Ben Ross',))
            add_book(library, 'Quiet Waters', ('Ada Lark',))
            self.assertEqual(library.find_similar('Quiet Harbour: A Novel', ['Lark, Ada']),
                             [harbour])
            self.assertEqual(len(library.find_similar('quiet harbour', [])), 2)
            self.assertEqual(library.find_similar('Elsewhere', ['Ada Lark']), [])

    def test_source_keys(self):
        with temporary_library() as library:
            book_id = library.add_book(BookInfo(title='T'), '/c/t.epub', hash='', size=1,
                                       source='calibre', source_key=42,
                                       source_modified='2026-01-01 10:00:00+00:00')
            book = library.book(book_id)
            self.assertEqual((book.source, book.source_key), ('calibre', '42'))
            self.assertEqual(book.source_modified, '2026-01-01 10:00:00+00:00')
            self.assertEqual(library.find_by_source_key('calibre', '42'), book_id)
            self.assertIsNone(library.find_by_source_key('calibre', '43'))
            library.update_book(book_id, source_modified='2026-02-01')
            self.assertEqual(library.book(book_id).source_modified, '2026-02-01')

    def test_groups(self):
        with temporary_library() as library:
            add_book(library, 'A', ('Ada Lark',), tags=['Sea'], series='Tides',
                     publisher='Gull Press', language='en')
            add_book(library, 'B', ('Ada Lark', 'Ben Ross'), tags=['sea', 'Fog'],
                     publisher='Ash House', language='fr')
            authors = library.authors()
            self.assertEqual([(a.name, a.sort, a.count) for a in authors],
                             [('Ada Lark', 'Lark, Ada', 2), ('Ben Ross', 'Ross, Ben', 1)])
            self.assertEqual([(t.name, t.count) for t in library.tags()],
                             [('Fog', 1), ('Sea', 2)])
            self.assertEqual([(s.name, s.count) for s in library.series()], [('Tides', 1)])
            self.assertEqual(library.publishers(), ['Ash House', 'Gull Press'])
            self.assertEqual(library.languages(), ['en', 'fr'])


class FilterAndSortTest(unittest.TestCase):

    def setUp(self):
        self.context = temporary_library()
        self.library = self.context.__enter__()
        library = self.library
        self.harbour = add_book(library, 'The Quiet Harbour', ('Ada Lark',), tags=['Sea'],
                                series='Tides', series_index=2, published='2001')
        self.fog = add_book(library, 'Fog Over Ashby', ('Ben Ross',), tags=['Mystery'],
                            series='Tides', series_index=1, published='1999-03')
        self.zeta = add_book(library, 'Émile and the Gulls', ('Cara Moss',),
                             published='2010')
        self.ten = add_book(library, 'Book 10', ())
        self.two = add_book(library, 'Book 2', ())
        for offset, book_id in enumerate((self.harbour, self.fog, self.zeta, self.ten,
                                          self.two)):
            library.db.execute('UPDATE books SET added = ? WHERE id = ?',
                               (1000 + offset, book_id))

    def tearDown(self):
        self.context.__exit__(None, None, None)

    def ids(self, **kwargs):
        return [book.id for book in self.library.books(**kwargs)]

    def test_sorts(self):
        self.assertEqual(self.ids(), [self.two, self.ten, self.zeta, self.fog, self.harbour])
        self.assertEqual(self.ids(sort='added', descending=False)[0], self.harbour)
        self.assertEqual(self.ids(sort='title'),
                         [self.two, self.ten, self.zeta, self.fog, self.harbour])
        self.assertEqual(self.ids(sort='author')[:3], [self.harbour, self.zeta, self.fog])
        self.assertEqual(set(self.ids(sort='author')[3:]), {self.ten, self.two})
        self.assertEqual(self.ids(sort='series')[:2], [self.fog, self.harbour])
        self.assertEqual(self.ids(sort='series', descending=True)[:2], [self.harbour, self.fog])
        self.assertEqual(self.ids(sort='published')[:3], [self.zeta, self.harbour, self.fog])
        self.assertEqual(self.ids(sort='title', limit=2, offset=1), [self.ten, self.zeta])
        self.assertEqual(self.ids(sort='added', limit=2, offset=1), [self.ten, self.zeta])
        self.assertEqual(self.library.book_ids(sort='title'), self.ids(sort='title'))
        with self.assertRaises(ValueError):
            self.library.books(sort='colour')

    def test_rating_and_last_read(self):
        library = self.library
        library.update_book(self.fog, rating=8)
        library.update_book(self.zeta, rating=10)
        self.assertEqual(self.ids(sort='rating')[:2], [self.zeta, self.fog])
        library.set_progress(self.fog, 0.2, 'cfi-a')
        time.sleep(0.01)
        library.set_progress(self.harbour, 0.4, 'cfi-b')
        self.assertEqual(self.ids(sort='last-read')[:2], [self.harbour, self.fog])
        self.assertEqual([book.id for book in library.continue_reading()],
                         [self.harbour, self.fog])
        self.assertEqual(library.recently_added(limit=1)[0].id, self.two)

    def test_filters(self):
        library = self.library
        lark = next(a for a in library.authors() if a.name == 'Ada Lark')
        tides = library.series()[0]
        sea = next(t for t in library.tags() if t.name == 'Sea')
        self.assertEqual(self.ids(author=lark.id), [self.harbour])
        self.assertEqual(self.ids(author=lark), [self.harbour])
        self.assertEqual(set(self.ids(series=tides.id)), {self.harbour, self.fog})
        self.assertEqual(self.ids(tag=sea.id), [self.harbour])
        self.assertEqual(self.ids(query='emile'), [self.zeta])
        self.assertEqual(self.ids(query='tides -mystery'), [self.harbour])
        self.assertEqual(library.count(query='book'), 2)
        self.assertEqual(library.count(), 5)
        library.set_status([self.fog], 'finished')
        self.assertEqual(self.ids(status='finished'), [self.fog])
        self.assertEqual(library.count(status='unread'), 4)

    def test_shelves_filter(self):
        library = self.library
        manual = library.add_shelf('Holiday')
        smart = library.add_shelf('Tides', query='series:tides')
        library.add_to_shelf(manual, [self.zeta, self.two, self.zeta])
        self.assertEqual(set(self.ids(shelf=manual)), {self.zeta, self.two})
        self.assertEqual(set(self.ids(shelf=smart)), {self.harbour, self.fog})
        self.assertEqual([(s.name, s.count, s.query) for s in library.shelves()],
                         [('Holiday', 2, None), ('Tides', 2, 'series:tides')])
        self.assertEqual([s.id for s in library.book_shelves(self.zeta)], [manual])
        library.remove_from_shelf(manual, [self.two])
        self.assertEqual(self.ids(shelf=manual), [self.zeta])
        self.assertEqual(self.ids(shelf=999), [])
        library.move_shelf(smart, 0)
        self.assertEqual([s.id for s in library.shelves()], [smart, manual])
        library.update_shelf(smart, name='Tide Books', query='series:tides tag:sea')
        self.assertEqual(library.shelf(smart).name, 'Tide Books')
        self.assertEqual(self.ids(shelf=smart), [self.harbour])
        library.remove_shelf(manual)
        self.assertEqual([s.id for s in library.shelves()], [smart])
        self.assertEqual(library.undo(), 'Remove Shelf')
        self.assertEqual(self.ids(shelf=manual), [self.zeta])


class EditTest(unittest.TestCase):

    def test_update_book_and_undo(self):
        with temporary_library() as library:
            book_id = add_book(library, 'The Quiet Harbour', ('Ada Lark',), tags=['Sea'],
                               series='Tides', series_index=1,
                               identifiers={'isbn': '9780000000002'})
            recorder = Recorder(library)
            library.update_book(book_id, title='A Loud Harbour', authors=['Ben Ross', 'Ada Lark'],
                                tags=['Fog'], series='', rating=7, publisher=' Gull ',
                                identifiers={'google': 'abc'})
            self.assertEqual(recorder.kinds, ['books'])
            book = library.book(book_id)
            self.assertEqual(book.title, 'A Loud Harbour')
            self.assertEqual(book.sort_title, 'Loud Harbour, A')
            self.assertEqual(book.authors, ('Ben Ross', 'Ada Lark'))
            self.assertEqual(book.author_sort, 'Ross, Ben & Lark, Ada')
            self.assertEqual(book.tags, ('Fog',))
            self.assertEqual(book.series, '')
            self.assertEqual(book.rating, 7)
            self.assertEqual(book.publisher, 'Gull')
            self.assertEqual(book.identifiers, {'google': 'abc'})
            self.assertEqual(library.count(query='ross'), 1)
            self.assertEqual(library.count(query='quiet'), 0)

            self.assertTrue(library.can_undo())
            self.assertEqual(library.undo_label, 'Edit Book')
            self.assertEqual(library.undo(), 'Edit Book')
            book = library.book(book_id)
            self.assertEqual(book.title, 'The Quiet Harbour')
            self.assertEqual(book.sort_title, 'Quiet Harbour, The')
            self.assertEqual(book.authors, ('Ada Lark',))
            self.assertEqual(book.tags, ('Sea',))
            self.assertEqual(book.series, 'Tides')
            self.assertEqual(book.identifiers, {'isbn': '9780000000002'})
            self.assertEqual(library.count(query='quiet'), 1)
            self.assertEqual(library.count(query='ross'), 0)
            self.assertEqual(library.undo_label, 'Add Book')
            library.clear_undo()
            self.assertFalse(library.can_undo())
            self.assertIsNone(library.undo())

    def test_undo_keeps_later_progress(self):
        with temporary_library() as library:
            book_id = add_book(library, 'T')
            library.update_book(book_id, title='U')
            library.set_progress(book_id, 0.5, 'cfi')
            library.undo()
            book = library.book(book_id)
            self.assertEqual((book.title, book.progress, book.location), ('T', 0.5, 'cfi'))

    def test_explicit_sorts_win(self):
        with temporary_library() as library:
            book_id = add_book(library, 'T')
            library.update_book(book_id, title='The X', sort_title='Mine',
                                authors=['Ada Lark'], author_sort='Own')
            book = library.book(book_id)
            self.assertEqual((book.sort_title, book.author_sort), ('Mine', 'Own'))

    def test_cover_version(self):
        with temporary_library() as library:
            book_id = add_book(library, 'T')
            library.update_book(book_id, has_cover=True)
            library.update_book(book_id, has_cover=True)
            book = library.book(book_id)
            self.assertEqual((book.has_cover, book.cover_version), (True, 2))
            library.undo()
            self.assertEqual(library.book(book_id).cover_version, 1)
            library.undo()
            book = library.book(book_id)
            self.assertEqual((book.has_cover, book.cover_version), (False, 0))

    def test_bad_fields(self):
        with temporary_library() as library:
            book_id = add_book(library, 'T')
            with self.assertRaises(ValueError):
                library.update_book(book_id, colour='red')
            with self.assertRaises(ValueError):
                library.update_book(book_id, status='lost')
            library.update_book(book_id, rating=42)
            self.assertEqual(library.book(book_id).rating, 10)

    def test_bulk_tags(self):
        with temporary_library() as library:
            first = add_book(library, 'A', tags=['Sea'])
            second = add_book(library, 'B', tags=['Fog', 'Sea'])
            library.update_books([first, second], add_tags=['Night', 'sea'], remove_tags=['Fog'])
            self.assertEqual(library.book(first).tags, ('Night', 'Sea'))
            self.assertEqual(library.book(second).tags, ('Night', 'Sea'))
            self.assertEqual(library.undo(), 'Edit Books')
            self.assertEqual(library.book(second).tags, ('Fog', 'Sea'))
            self.assertEqual(library.count(query='tag:night'), 0)

    def test_remove_books_and_undo(self):
        with temporary_library() as library:
            book_id = add_book(library, 'T', ('Ada Lark',), tags=['Sea'], series='Tides',
                               identifiers={'isbn': '1'})
            other = add_book(library, 'U', ())
            shelf = library.add_shelf('S')
            library.add_to_shelf(shelf, [book_id])
            library.add_annotation(book_id, 'highlight', 'cfi', text='words')
            library.log_session(book_id, 100.0, 60, 0.0, 0.1)
            before = library.book(book_id)
            recorder = Recorder(library)
            library.remove_books([book_id])
            self.assertIsNone(library.book(book_id))
            self.assertEqual(library.count(), 1)
            self.assertEqual(library.authors(), [])
            self.assertIn('books', recorder.kinds)
            self.assertEqual(library.undo(), 'Remove Book')
            self.assertEqual(library.book(book_id), before)
            self.assertEqual(library.count(shelf=shelf), 1)
            self.assertEqual(len(library.annotations(book_id)), 1)
            self.assertEqual(len(library.sessions(book_id)), 1)
            self.assertEqual(library.find_file(library.files(book_id)[0].path).book_id, book_id)
            self.assertEqual(library.book(other).title, 'U')

    def test_add_book_undo(self):
        with temporary_library() as library:
            book_id = add_book(library, 'T', ('Ada Lark',))
            self.assertEqual(library.undo(), 'Add Book')
            self.assertIsNone(library.book(book_id))
            self.assertEqual(library.files(book_id), [])
            # An id is never given twice, so undo can put a row back under its own.
            self.assertGreater(add_book(library, 'U'), book_id)

    def test_nested_blocks_are_one_step(self):
        with temporary_library() as library:
            recorder = Recorder(library)
            with library.undoable('Import'):
                first = add_book(library, 'A')
                add_book(library, 'B')
                library.update_book(first, title='C')
                self.assertEqual(recorder.kinds, [])
            self.assertEqual(sorted(recorder.kinds), ['books', 'files'])
            self.assertEqual(library.undo(), 'Import')
            self.assertEqual(library.count(), 0)

    def test_an_exception_rolls_back(self):
        with temporary_library() as library:
            with self.assertRaises(RuntimeError):
                with library.undoable('Broken'):
                    add_book(library, 'A')
                    raise RuntimeError
            self.assertEqual(library.count(), 0)
            self.assertFalse(library.can_undo())

    def test_set_status(self):
        with temporary_library() as library:
            book_id = add_book(library, 'T')
            library.clear_undo()
            recorder = Recorder(library)
            library.set_progress(book_id, 0.3, 'cfi')
            self.assertEqual(library.book(book_id).status, 'reading')
            self.assertEqual(sorted(recorder.kinds), ['books', 'progress'])
            library.set_progress(book_id, 0.4, 'cfi2')
            self.assertEqual(recorder.kinds[-1], 'progress')
            self.assertFalse(library.can_undo())
            library.set_status([book_id], 'finished')
            self.assertEqual(library.undo(), 'Mark as Finished')
            self.assertEqual(library.book(book_id).status, 'reading')
            with self.assertRaises(ValueError):
                library.set_status([book_id], 'gone')


class AnnotationTest(unittest.TestCase):

    def test_annotations(self):
        with temporary_library() as library:
            book_id = add_book(library, 'T')
            recorder = Recorder(library)
            late = library.add_annotation(book_id, 'highlight', 'cfi-b', text='later',
                                          position=0.8, color='blue')
            early = library.add_annotation(book_id, 'highlight', 'cfi-a', text='first',
                                           note='mine', position=0.2, color='orange')
            mark = library.add_annotation(book_id, 'bookmark', 'cfi-c', position=0.5)
            self.assertEqual(recorder.kinds, ['annotations'] * 3)
            self.assertEqual([a.id for a in library.annotations(book_id)], [early, mark, late])
            self.assertEqual([a.id for a in library.annotations(book_id, 'bookmark')], [mark])
            self.assertEqual(library.annotation(early).color, 'yellow')
            library.update_annotation(early, note='changed', color='pink')
            self.assertEqual(library.annotation(early).note, 'changed')
            self.assertEqual(library.undo(), 'Edit Note')
            self.assertEqual(library.annotation(early).note, 'mine')
            library.remove_annotation(late)
            self.assertIsNone(library.annotation(late))
            self.assertEqual(library.undo(), 'Remove Highlight')
            self.assertEqual(library.annotation(late).text, 'later')
            with self.assertRaises(ValueError):
                library.add_annotation(book_id, 'scribble', '')
            with self.assertRaises(ValueError):
                library.update_annotation(early, color='orange')


class FolderTest(unittest.TestCase):

    def test_files_belong_to_the_deepest_folder(self):
        with temporary_library() as library:
            outer_book = add_book(library, 'A', path='/books/a.epub')
            inner_book = add_book(library, 'B', path='/books/watched/b.epub')
            outer = library.add_folder('/books', 'library')
            inner = library.add_folder('/books/watched/', 'watched')
            self.assertEqual(library.add_folder('/books', 'library'), outer)
            self.assertEqual([f.path for f in library.folder_files(outer)], ['/books/a.epub'])
            self.assertEqual([f.path for f in library.folder_files(inner)],
                             ['/books/watched/b.epub'])
            later = add_book(library, 'C', path='/books/watched/c.epub')
            self.assertEqual(len(library.folder_files(inner)), 2)
            self.assertEqual([(f.path, f.kind) for f in library.folders()],
                             [('/books', 'library'), ('/books/watched', 'watched')])
            library.add_file(outer_book, '/books/watched/a.pdf', hash='', size=1)
            library.remove_folder(inner, remove_books=True)
            self.assertIsNone(library.book(inner_book))
            self.assertIsNone(library.book(later))
            self.assertEqual(library.book(outer_book).formats, ('epub',))
            self.assertEqual([f.id for f in library.folders()], [outer])
            self.assertEqual(library.undo(), 'Remove Folder')
            self.assertEqual(library.book(inner_book).title, 'B')
            self.assertEqual(library.book(outer_book).formats, ('epub', 'pdf'))
            library.remove_folder(inner)
            self.assertEqual(library.book(inner_book).title, 'B')
            self.assertEqual(library.folder_files(inner), [])
            with self.assertRaises(ValueError):
                library.add_folder('/x', 'cloud')


class WorkerTest(unittest.TestCase):

    def test_a_worker_writes_and_the_main_library_sees_it(self):
        with temporary_library() as library:
            recorder = Recorder(library)
            worker = library.open_worker()
            done = []

            def work():
                for number in range(20):
                    add_book(worker, f'Book {number}')
                worker.close()
                done.append(True)

            thread = threading.Thread(target=work)
            thread.start()
            while thread.is_alive():
                library.count()  # reading while the worker writes
            thread.join()
            self.assertEqual(done, [True])
            self.assertEqual(library.count(), 20)
            self.assertFalse(library.can_undo())
            self.assertIs(library.notify_changed('books', 'files'), False)
            self.assertEqual(recorder.kinds, ['books', 'files'])

    def test_reopening_keeps_everything_and_drops_unused_names(self):
        with temporary_library() as library:
            book_id = add_book(library, 'T', ('Ada Lark',), tags=['Sea'])
            library.update_book(book_id, authors=['Ben Ross'], tags=[])
            again = Library(library.path)
            names = [row[0] for row in again.db.execute('SELECT name FROM authors')]
            self.assertEqual(names, ['Ben Ross'])
            self.assertEqual(again.db.execute('SELECT COUNT(*) FROM tags').fetchone()[0], 0)
            self.assertEqual(again.book(book_id).authors, ('Ben Ross',))
            again.close()

    def test_a_newer_schema_is_refused(self):
        with temporary_library() as library:
            path = library.path.parent / 'newer.sqlite'
            db = sqlite3.connect(path)
            db.execute(f'PRAGMA user_version = {schema.VERSION + 1}')
            db.close()
            with self.assertRaises(schema.SchemaError):
                Library(path)


class ScaleTest(unittest.TestCase):

    def test_two_thousand_books(self):
        with temporary_library() as library:
            with library.undoable('Import'):
                for number in range(2000):
                    add_book(library, f'Title {number:04}', (f'Author {number % 50}', 'Ada Lark'),
                             tags=['Sea', f'T{number % 7}'], series=f'S{number % 10}',
                             series_index=number % 9)
            started = time.perf_counter()
            books = library.books(sort='title')
            elapsed = time.perf_counter() - started
            self.assertEqual(len(books), 2000)
            self.assertEqual(books[0].title, 'Title 0000')
            self.assertEqual(books[-1].title, 'Title 1999')
            self.assertEqual(books[0].authors, ('Author 0', 'Ada Lark'))
            self.assertEqual(books[0].tags, ('Sea', 'T0'))
            self.assertLess(elapsed, 0.5)
            self.assertEqual(library.count(query='author:="author 3"'), 40)
            self.assertEqual(library.count(query='author:"author 3"'), 440)  # 3, 30-39
            self.assertEqual(library.count(tag=next(t for t in library.tags()
                                                    if t.name == 'T3')), 286)
            self.assertEqual(len(library.book_ids(sort='series')), 2000)
            counts = {author.name: author.count for author in library.authors()}
            self.assertEqual((counts['Ada Lark'], counts['Author 7']), (2000, 40))


class MergeTest(unittest.TestCase):
    """Duplicates and merging them, and books opened without adding."""

    def test_duplicates_share_a_title_and_an_author(self):
        with temporary_library() as library:
            one = add_book(library, 'A Quiet Harbour', ('Ada Lark',))
            two = add_book(library, 'A quiet  harbour', ('Lark, Ada',), fmt='pdf')
            add_book(library, 'A Quiet Harbour', ('Ben Ross',))  # another author's
            three = add_book(library, 'Salt Roads', ())
            four = add_book(library, 'Salt Roads', ('Cy Moor',))
            add_book(library, 'Lantern Hill')
            self.assertEqual(library.duplicates(), [[one, two], [three, four]])
            library.merge_books(one, [two])
            self.assertEqual(library.duplicates(), [[three, four]])

    def test_merge_keeps_everything_and_undoes(self):
        with temporary_library() as library:
            keep = add_book(library, 'A Quiet Harbour', ('Ada Lark',), tags=['Sea'],
                            identifiers={'isbn': '9780000000002'})
            other = add_book(library, 'A Quiet Harbour', ('Ada Lark',), fmt='pdf',
                             tags=['Coast'], description='<p>Tides.</p>', series='Saltmarsh',
                             series_index=2.0, identifiers={'google': 'abc', 'isbn': '1'},
                             publisher='Lantern House')
            library.update_book(other, rating=8)
            library.set_progress(other, 0.4, 'epubcfi(/6/4)')
            note = library.add_annotation(other, 'highlight', 'epubcfi(/6/4!/2)', text='tide')
            library.log_session(other, 1000.0, 60, 0.3, 0.4)
            shelf = library.add_shelf('Holiday')
            library.add_to_shelf(shelf, [other])
            before = library.book(keep)
            self.assertEqual(library.richest([keep, other]), other)

            self.assertEqual(library.merge_books(keep, [other]), 1)
            book = library.book(keep)
            self.assertIsNone(library.book(other))
            self.assertEqual(book.formats, ('epub', 'pdf'))
            self.assertEqual(book.tags, ('Coast', 'Sea'))
            self.assertEqual(book.identifiers, {'isbn': '9780000000002', 'google': 'abc'})
            self.assertEqual((book.series, book.series_index), ('Saltmarsh', 2.0))
            self.assertEqual((book.description, book.publisher, book.rating),
                             ('<p>Tides.</p>', 'Lantern House', 8))
            self.assertEqual((book.status, book.progress, book.location),
                             ('reading', 0.4, 'epubcfi(/6/4)'))
            self.assertEqual(book.added, before.added)
            self.assertEqual([a.id for a in library.annotations(keep)], [note])
            self.assertEqual(len(library.sessions(keep)), 1)
            self.assertEqual([s.id for s in library.book_shelves(keep)], [shelf])
            self.assertEqual(library.count(), 1)
            self.assertEqual(library.count(query='saltmarsh'), 1)

            self.assertEqual(library.undo(), 'Merge Books')
            self.assertEqual(library.book(keep), before)
            self.assertEqual(library.book(other).formats, ('pdf',))
            self.assertEqual([a.id for a in library.annotations(other)], [note])
            self.assertEqual(library.annotations(keep), [])
            self.assertEqual([s.id for s in library.book_shelves(other)], [shelf])
            self.assertEqual(library.book_shelves(keep), [])
            self.assertEqual(library.count(), 2)

    def test_merge_of_nothing(self):
        with temporary_library() as library:
            keep = add_book(library, 'A Quiet Harbour')
            self.assertEqual(library.merge_books(keep, [keep, 999]), 0)
            self.assertFalse(library.can_undo() and library.undo_label == 'Merge Books')

    def test_opened_books_are_kept_out_of_sight(self):
        with temporary_library() as library:
            add_book(library, 'Lantern Hill', ('Ben Ross',), tags=['Sea'])
            undo_before = library.undo_label
            info = BookInfo(title='A Quiet Harbour', authors=['Ada Lark'], format='epub',
                            tags=['Coast'], series='Saltmarsh', language='en')
            opened = library.add_opened(info, '/invented/harbour.epub', hash='h1', size=10)
            self.assertEqual(library.undo_label, undo_before)  # no undo step
            self.assertEqual(library.book(opened).source, 'opened')
            self.assertEqual(library.count(), 1)
            self.assertEqual(library.book_ids(), [opened - 1])
            self.assertEqual([a.name for a in library.authors()], ['Ben Ross'])
            self.assertEqual(library.series(), [])
            self.assertEqual([t.name for t in library.tags()], ['Sea'])
            self.assertEqual(library.languages(), [])
            self.assertEqual(library.find_similar('A Quiet Harbour', ['Ada Lark']), [])
            self.assertEqual(library.find_by_hash('h1'), opened)  # opened again: the same
            library.set_progress(opened, 0.5, 'epubcfi(/6/2)')
            self.assertEqual(library.continue_reading(), [])

            self.assertTrue(library.keep_book(opened, path='/invented/Books/harbour.epub'))
            self.assertEqual(library.undo_label, 'Add to Library')
            book = library.book(opened)
            self.assertEqual((book.source, book.progress), ('library', 0.5))
            self.assertEqual(library.files(opened)[0].path, '/invented/Books/harbour.epub')
            self.assertEqual(library.count(), 2)
            self.assertFalse(library.keep_book(opened))  # already kept
            library.undo()
            self.assertEqual(library.book(opened).source, 'opened')
            self.assertEqual(library.files(opened)[0].path, '/invented/harbour.epub')
            self.assertEqual(library.count(), 1)


class UndoExactTest(unittest.TestCase):
    """Each undoable change of the second round, undone, leaves every row as it was."""

    def _undone(self, library, change, label):
        before = snapshot(library)
        change()
        self.assertNotEqual(snapshot(library), before)
        self.assertEqual(library.undo(), label)
        self.assertEqual(snapshot(library), before)

    def _books(self, library):
        keep = add_book(library, 'A Quiet Harbour', ('Ada Lark',), tags=['Sea'],
                        identifiers={'isbn': '9780000000002'})
        other = add_book(library, 'A Quiet Harbour', ('Ada Lark', 'Ben Ross'), fmt='pdf',
                         tags=['Coast', 'Sea'], description='<p>Tides.</p>',
                         series='Saltmarsh', series_index=2.0, language='en',
                         identifiers={'google': 'abc'}, publisher='Lantern House')
        third = add_book(library, 'A quiet harbour', (), fmt='mobi', published='2019')
        library.update_book(other, rating=8)
        library.set_progress(other, 0.4, 'epubcfi(/6/4)')
        library.set_status([third], 'finished')
        library.add_annotation(other, 'highlight', 'epubcfi(/6/4!/2)', text='tide')
        library.add_annotation(third, 'bookmark', 'epubcfi(/6/8)')
        library.log_session(other, 1000.0, 60, 0.3, 0.4)
        shelf = library.add_shelf('Holiday')
        library.add_to_shelf(shelf, [other, third, keep])
        return keep, other, third, shelf

    def test_merge(self):
        with temporary_library() as library:
            keep, other, third, _shelf = self._books(library)
            self._undone(library, lambda: library.merge_books(keep, [other, third]),
                         'Merge Books')

    def test_add_to_library_of_an_opened_book(self):
        with temporary_library() as library:
            add_book(library, 'Lantern Hill')
            info = BookInfo(title='A Quiet Harbour', authors=['Ada Lark'], format='epub')
            opened = library.add_opened(info, '/invented/harbour.epub', hash='h1', size=10)
            library.set_progress(opened, 0.5, 'epubcfi(/6/2)')
            library.add_annotation(opened, 'highlight', 'epubcfi(/6/2!/4)', text='lamps')
            self._undone(library, lambda: library.keep_book(
                opened, path='/invented/Books/harbour.epub'), 'Add to Library')
            self._undone(library, lambda: library.keep_book(opened, source='watched'),
                         'Add to Library')

    def test_shelves(self):
        with temporary_library() as library:
            keep, other, third, shelf = self._books(library)
            smart = library.add_shelf('Sea', 'tag:sea')
            self._undone(library, lambda: library.add_shelf('New'), 'Add Shelf')
            self._undone(library, lambda: library.update_shelf(smart, name='Coast',
                                                               query='tag:coast'),
                         'Edit Shelf')
            self._undone(library, lambda: library.move_shelf(smart, 0), 'Move Shelf')
            second = library.add_shelf('Later')
            self._undone(library, lambda: library.add_to_shelf(second, [keep, third]),
                         'Add to Shelf')
            self._undone(library, lambda: library.remove_from_shelf(shelf, [other, third]),
                         'Remove from Shelf')
            self._undone(library, lambda: library.remove_shelf(shelf), 'Remove Shelf')
            self._undone(library, lambda: library.remove_books([other]), 'Remove Book')

    def test_setting_an_annotation_location_is_no_undo_step(self):
        with temporary_library() as library:
            book = add_book(library, 'A Quiet Harbour')
            note = library.add_annotation(book, 'highlight', '', text='lamps', position=0.3)
            library.update_annotation(note, note='later')
            label = library.undo_label
            library.set_annotation_location(note, 'epubcfi(/6/4!/2)', 0.35)
            self.assertEqual(library.undo_label, label)
            self.assertEqual(library.undo(), 'Edit Note')  # the note goes, the place stays
            found = library.annotation(note)
            self.assertEqual((found.note, found.location, found.position),
                             ('', 'epubcfi(/6/4!/2)', 0.35))

    def test_undo_keeps_a_name_a_worker_took_up_since(self):
        # An edit makes the author 'Cy Moor' and the tag 'Night'; a worker's import then
        # gives them to a new book. Undoing the edit must not take them from that book.
        with temporary_library() as library:
            book = add_book(library, 'A Quiet Harbour')
            library.update_book(book, authors=['Cy Moor'], tags=['Night'], series='Dusk')
            worker = library.open_worker()
            try:
                imported = add_book(worker, 'Lantern Hill', ('Cy Moor',), tags=['Night'],
                                    series='Dusk')
            finally:
                worker.close()
            self.assertEqual(library.undo(), 'Edit Book')
            self.assertEqual(library.book(book).authors, ('Ada Lark',))
            found = library.book(imported)
            self.assertEqual((found.authors, found.tags, found.series),
                             (('Cy Moor',), ('Night',), 'Dusk'))
            self.assertEqual(library.count(query='author:moor'), 1)
            self.assertEqual([group.name for group in library.authors()],
                             ['Ada Lark', 'Cy Moor'])

    def test_undo_still_drops_the_names_it_made(self):
        with temporary_library() as library:
            book = add_book(library, 'A Quiet Harbour')
            before = snapshot(library)
            library.update_book(book, authors=['Cy Moor'], tags=['Night'], series='Dusk')
            library.undo()
            self.assertEqual(snapshot(library), before)



class UnreadStartsOverTest(unittest.TestCase):

    def test_marking_unread_forgets_the_place_and_undo_brings_it_back(self):
        with temporary_library() as library:
            book_id = library.add_book(BookInfo(title='Tides', authors=['Ada Lark']),
                                       '/invented/tides.epub', hash='h1', size=1)
            library.set_progress(book_id, 0.4, 'epubcfi(/6/8!/4/2)')
            library.set_status([book_id], 'unread')
            book = library.book(book_id)
            self.assertEqual((book.status, book.progress, book.location), ('unread', 0.0, ''))
            library.undo()
            book = library.book(book_id)
            self.assertEqual((book.progress, book.location), (0.4, 'epubcfi(/6/8!/4/2)'))


class OpenedBooksTest(unittest.TestCase):
    """Books opened without adding: listed apart, forgotten with Undo."""

    def test_recently_opened_and_forget(self):
        with temporary_library() as library:
            kept = add_book(library, 'In the Library')
            first = library.add_opened(BookInfo(title='Opened First', authors=['Ada Lark']),
                                       '/invented/first.epub', hash='o1', size=1)
            second = library.add_opened(BookInfo(title='Opened Second', authors=['Ben Ross']),
                                        '/invented/second.epub', hash='o2', size=1)
            library.set_progress(first, 0.3, 'epubcfi(/6/2)')
            library.add_annotation(first, 'highlight', 'epubcfi(/6/4)', text='Salt')
            library.log_session(first, 1000, 600, 0.0, 0.3)
            self.assertEqual([book.title for book in library.opened_books()],
                             ['Opened First', 'Opened Second'])
            self.assertEqual(library.count(), 1)
            before = snapshot(library)
            self.assertEqual(library.forget_books([first, kept]), 1)  # the library's stays
            self.assertEqual(library.undo_label, 'Forget Book')
            self.assertIsNone(library.book(first))
            self.assertIsNotNone(library.book(kept))
            self.assertEqual(library.annotations(first), [])
            self.assertEqual(library.sessions(first), [])
            self.assertEqual([book.id for book in library.opened_books()], [second])
            library.undo()
            self.assertEqual(snapshot(library), before)
            self.assertEqual(library.forget_books([kept]), 0)
            self.assertEqual(len(library.opened_books(limit=1)), 1)


class PagesTest(unittest.TestCase):

    def test_page_count_is_editable_and_undone(self):
        with temporary_library() as library:
            book_id = add_book(library, 'Tides')
            self.assertEqual(library.book(book_id).pages, 0)
            library.update_book(book_id, pages=312)
            self.assertEqual(library.book(book_id).pages, 312)
            self.assertEqual(library.page_counts(), {book_id: 312})
            library.update_book(book_id, pages=-4)
            self.assertEqual(library.book(book_id).pages, 0)
            library.undo()
            self.assertEqual(library.books()[0].pages, 312)


if __name__ == '__main__':
    unittest.main()
