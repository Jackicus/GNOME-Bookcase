# SPDX-License-Identifier: GPL-2.0-or-later
# SPDX-FileCopyrightText: 2026 Jack Tully

"""The reader's logic without GTK: the page's style from the settings, the progress label,
the reading-session clock."""

import re
import unittest

from tests import ROOT

from bookcase import reading

DEFAULTS = {
    'reader-theme': 'auto', 'reader-font': 'publisher', 'reader-custom-font': '',
    'reader-font-size': 18, 'reader-line-height': 1.5, 'reader-margin': 8,
    'reader-max-width': 720, 'reader-justify': True, 'reader-hyphenate': True,
    'reader-publisher-styles': True, 'reader-scrolled': False, 'reader-two-pages': True,
    'reader-animate': True,
    'reader-custom-theme': '{"bg": "#e8f0e3", "fg": "#26331f", "link": "#2c6a2e"}',
}


def settings(**changes):
    values = dict(DEFAULTS)
    values.update({key.replace('_', '-'): value for key, value in changes.items()})
    return values.get


class StyleTest(unittest.TestCase):

    def test_defaults(self):
        style = reading.build_style(settings(), dark=False, title='A Quiet Harbour')
        self.assertEqual(style['theme']['name'], 'light')
        self.assertEqual(style['font'], '')
        self.assertEqual(style['fontSize'], 18)
        self.assertEqual(style['lineHeight'], 1.5)
        self.assertEqual(style['flow'], 'paginated')
        self.assertEqual(style['maxColumns'], 2)
        self.assertEqual(style['maxWidth'], 720)
        self.assertEqual(style['margin'], 8)
        self.assertTrue(style['justify'] and style['hyphenate'] and style['publisherStyles'])
        self.assertEqual(style['title'], 'A Quiet Harbour')

    def test_auto_theme_follows_the_system(self):
        self.assertEqual(reading.build_style(settings(), True)['theme']['name'], 'dark')
        self.assertTrue(reading.build_style(settings(), True)['theme']['dark'])
        sepia = reading.build_style(settings(reader_theme='sepia'), True)['theme']
        self.assertEqual(sepia['name'], 'sepia')
        self.assertFalse(sepia['dark'])
        self.assertEqual(reading.theme_colors('nonsense', False)['name'], 'light')

    def test_auto_theme_under_high_contrast_is_black_on_white_or_white_on_black(self):
        light = reading.build_style(settings(), False, high_contrast=True)['theme']
        self.assertEqual((light['bg'], light['fg']), ('#ffffff', '#000000'))
        dark = reading.theme_colors('auto', True, high_contrast=True)
        self.assertEqual((dark['name'], dark['bg'], dark['fg']),
                         ('contrast-dark', '#000000', '#ffffff'))
        # A paper the user chose stays theirs.
        self.assertEqual(reading.theme_colors('sepia', True, high_contrast=True)['name'],
                         'sepia')

    def test_layout_and_fonts(self):
        style = reading.build_style(settings(reader_scrolled=True, reader_two_pages=False,
                                             reader_font='serif'), False)
        self.assertEqual(style['flow'], 'scrolled')
        self.assertEqual(style['maxColumns'], 1)
        self.assertIn('serif', style['font'])
        custom = reading.build_style(settings(reader_font='custom',
                                              reader_custom_font='Inven"ted; Sans'), False)
        self.assertEqual(custom['font'], '"Invented Sans", serif')
        self.assertEqual(reading.font_family('custom', ''), '')

    def test_every_style_key_is_a_reader_setting(self):
        schema = (ROOT / 'data' / 'io.github.jackicus.Bookcase.gschema.xml').read_text()
        for key in reading.STYLE_KEYS:
            self.assertIn(f'name="{key}"', schema)
            self.assertIn(key, DEFAULTS)

    def test_a_custom_theme(self):
        theme = reading.build_style(settings(
            reader_theme='custom',
            reader_custom_theme='{"bg": "#102030", "fg": "#E0E0D0", "link": "#80c0ff"}'),
            False)['theme']
        self.assertEqual(theme, {'name': 'custom', 'bg': '#102030', 'fg': '#e0e0d0',
                                 'link': '#80c0ff', 'dark': True})
        light = reading.theme_colors('custom', True, reading.custom_theme_json(
            '#fdf6e3', '#433422', '#0b6e99'))
        self.assertFalse(light['dark'])  # by its paper, not the system's style
        # what is broken falls back to the default colours
        self.assertEqual(reading.custom_theme('{"bg": "red", "fg": 3}')['bg'],
                         reading.CUSTOM_THEME['bg'])
        self.assertEqual(reading.custom_theme('not json')['fg'], reading.CUSTOM_THEME['fg'])
        self.assertEqual(reading.custom_theme('[1]')['link'], reading.CUSTOM_THEME['link'])
        self.assertTrue(reading.is_dark('#000000'))
        self.assertFalse(reading.is_dark('#ffffff'))
        self.assertFalse(reading.is_dark('nonsense'))

    def test_the_custom_theme_default_is_the_schema_default(self):
        schema = (ROOT / 'data' / 'io.github.jackicus.Bookcase.gschema.xml').read_text()
        match = re.search(r'name="reader-custom-theme".*?<default>\'(.*?)\'</default>', schema,
                          re.S)
        self.assertEqual(reading.custom_theme(match.group(1)),
                         {**reading.CUSTOM_THEME, 'dark': False})
        self.assertIn('nick="custom"', schema)

    def test_the_paper_themes_agree_with_the_style_sheet(self):
        css = (ROOT / 'src' / 'style.css').read_text()
        for name, colors in reading.THEMES.items():
            match = re.search(rf'\.reader-page\.theme-{name} \{{ --reader-bg: (#\w+); '
                              rf'--reader-fg: (#\w+); \}}', css)
            self.assertIsNotNone(match, name)
            self.assertEqual(match.groups(), (colors['bg'], colors['fg']))


class ProgressTextTest(unittest.TestCase):

    def test_percent(self):
        self.assertEqual(reading.progress_text('percent', {'fraction': 0.426}), '42%')
        self.assertEqual(reading.progress_text('percent', {'fraction': 1.0}), '100%')
        self.assertEqual(reading.progress_text('percent', None), '')

    def test_page_or_location(self):
        self.assertEqual(reading.progress_text('page', {'fraction': 0.1, 'page': 'xii'}),
                         'Page xii')
        place = {'fraction': 0.1, 'location': {'current': 4, 'next': 5, 'total': 90}}
        self.assertEqual(reading.progress_text('page', place), 'Location 5 of 90')
        self.assertEqual(reading.progress_text('page', {'fraction': 0.1}), '10%')

    def test_times(self):
        place = {'fraction': 0.5, 'time': {'section': 3.2, 'total': 125}}
        self.assertEqual(reading.progress_text('chapter-time', place), '3 min left in chapter')
        self.assertEqual(reading.progress_text('book-time', place), '2 h 5 min left in book')
        self.assertEqual(reading.progress_text('book-time', place, book_minutes=60),
                         '1 h left in book')
        self.assertEqual(reading.progress_text('chapter-time', {'fraction': 0.5}), '50%')
        self.assertEqual(reading.format_minutes(0.2), 'less than a minute')

    def test_the_label_cycles(self):
        kinds = ['percent']
        for _ in range(4):
            kinds.append(reading.next_label(kinds[-1]))
        self.assertEqual(kinds, ['percent', 'page', 'chapter-time', 'book-time', 'percent'])
        self.assertEqual(reading.next_label('unknown'), 'percent')


class SessionClockTest(unittest.TestCase):

    def test_a_session_from_open_to_close(self):
        clock = reading.SessionClock(0.1, now=1000)
        self.assertIsNone(clock.activity(1060, 0.12))
        self.assertIsNone(clock.activity(1200, 0.15))
        session = clock.finish(1230)
        self.assertEqual((session.started, session.seconds), (1000, 230))
        self.assertEqual((session.start_fraction, session.end_fraction), (0.1, 0.15))

    def test_idle_time_splits_sessions_and_is_not_counted(self):
        clock = reading.SessionClock(0.1, now=0)
        clock.activity(100, 0.2)
        ended = clock.activity(100 + reading.IDLE_SECONDS + 1, 0.25)
        self.assertEqual((ended.started, ended.seconds, ended.end_fraction), (0, 100, 0.2))
        session = clock.finish(100 + reading.IDLE_SECONDS + 61)
        self.assertEqual(session.seconds, 60)
        self.assertEqual((session.start_fraction, session.end_fraction), (0.2, 0.25))

    def test_closing_long_after_the_last_page_counts_at_most_the_idle_time(self):
        clock = reading.SessionClock(0.0, now=0)
        clock.activity(50, 0.1)
        self.assertEqual(clock.finish(10_000).seconds, 50 + reading.IDLE_SECONDS)

    def test_short_sessions_are_not_logged(self):
        clock = reading.SessionClock(0.0, now=0)
        self.assertIsNone(clock.finish(reading.MIN_SESSION_SECONDS - 1))

    def test_readable_formats(self):
        for fmt in ('epub', 'EPUB', 'kepub', 'azw3', 'mobi', 'fb2', 'fbz', 'cbz'):
            self.assertTrue(reading.readable(fmt), fmt)
        for fmt in ('pdf', 'txt', 'cbr', '', None):
            self.assertFalse(reading.readable(fmt), fmt)


if __name__ == '__main__':
    unittest.main()
