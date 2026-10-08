#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-2.0-or-later
# SPDX-FileCopyrightText: 2026 Jack Tully

"""Build the invented demo library, for screenshots and `scripts/run.sh --demo`.

    scripts/demo_library.py [--data-dir build/demo] [--force] [--count N] [--files-only]
    scripts/demo_library.py --device build/demo-device   # a pretend Kobo with books on it

Forty books that do not exist (invented titles, authors, series and publishers, and five
public-domain classics under their real authors), each a real file in DATA_DIR/Books: EPUBs
of generated prose (the classics open with a few lines of their own text), a PDF and a CBZ
comic of drawn pages, every cover drawn with Cairo. They are added through the app's own
Importer, as a user would add them, then given what a library collects over time: ratings,
reading progress, finished books, reading sessions, highlights and notes, a shelf and a smart
shelf. The data directory gets the app's layout (library.sqlite, covers/, thumbnails/) and is
what BOOKCASE_DATA_DIR points at; the books folder is DATA_DIR/Books.

Everything is seeded: the same files, byte for byte, on every run (only the times are taken
from the clock, so "read yesterday" stays yesterday). An existing library is kept unless
--force; --count builds the first N books only (the tests' quick run); --files-only writes
the files and covers without a library (it needs no app code but formats/).

--device DIR lays out a Kobo in DIR (what BOOKCASE_TEST_DEVICE takes; screenshot.py's
--device does this): copies of a few of the library's books as kepubs, and one book the
library does not have.
"""

import argparse
import datetime
import io
import math
import os
import pathlib
import random
import shutil
import sys
import time
import uuid
import zipfile
from xml.sax.saxutils import escape

import cairo
import gi

gi.require_version('Pango', '1.0')
gi.require_version('PangoCairo', '1.0')
gi.require_version('GdkPixbuf', '2.0')
gi.require_foreign('cairo')
from gi.repository import GdkPixbuf, Pango, PangoCairo  # noqa: E402

ROOT = pathlib.Path(__file__).resolve().parent.parent
DEFAULT_DIR = ROOT / 'build' / 'demo'
COVER_SIZE = (600, 900)
ZIP_TIME = (2026, 1, 1, 0, 0, 0)
DAY = 86400

# ---------------------------------------------------------------------------------------
# The books. rating is Calibre's 0-10 (half stars); state is (status, progress, days since
# last read); style and palette choose the cover.

SALTMARSH = 'The Saltmarsh Chronicles'
CALDER = 'Inspector Calder'
HALCYON = 'Halcyon'
MOTH = "The Lamplighter's Moth"

BOOKS = [
    dict(title='The Glass Estuary', authors=['Imogen Vale'], series=SALTMARSH, index=3,
         tags=['Fantasy'], publisher='Lantern House', published='2022-04-07', rating=10,
         state=('reading', 0.42, 0), style='waves', palette=2, setting='coast',
         cast=('Wren', 'Corran', 'the Tidewright'),
         blurb='The estuary has frozen in high summer, and only Wren knows why. The third '
               'book of the Saltmarsh Chronicles follows her upriver to the city of glass, '
               'where the tides are sold by the hour.'),
    dict(title="The Tidewright's Daughter", authors=['Imogen Vale'], series=SALTMARSH,
         index=1, tags=['Fantasy'], publisher='Lantern House', published='2019-03-14',
         rating=9, state=('finished', 1.0, 70), style='waves', palette=0, setting='coast',
         cast=('Wren', 'Old Mag', 'the Tidewright'),
         blurb='Wren has spent her whole life on the marsh, mending nets and counting tides. '
               'When her father fails to come home with the evening flood, she learns that '
               'the sea keeps its own accounts.'),
    dict(title='A Lantern for the Drowned', authors=['Imogen Vale'], series=SALTMARSH,
         index=2, tags=['Fantasy'], publisher='Lantern House', published='2020-09-03',
         rating=8, state=('finished', 1.0, 40), style='waves', palette=1, setting='coast',
         cast=('Wren', 'Corran', 'Hester'),
         blurb='The lights along the sea wall are going out, one a night, and the people of '
               'Saltmarsh are disappearing with them.'),
    dict(title='The Heron King', authors=['Imogen Vale'], series=SALTMARSH, index=4,
         tags=['Fantasy'], publisher='Lantern House', published='2024-10-17',
         style='waves', palette=3, setting='coast', cast=('Wren', 'Corran', 'the Heron King'),
         blurb='The final book of the Saltmarsh Chronicles.'),
    dict(title='Death at Fennick Lock', authors=['Tobias Wren'], series=CALDER, index=1,
         tags=['Mystery', 'Crime'], publisher='Harrow & Finch', published='2015-05-21',
         rating=8, state=('finished', 1.0, 200), style='window', palette=0, setting='town',
         cast=('Calder', 'Sergeant Pryce', 'Mrs Arbery'),
         blurb='A lock-keeper is found in his own lock on the first frost of the year. '
               'Detective Inspector Ruth Calder has a week before the canal reopens.'),
    dict(title='The Orchard Murders', authors=['Tobias Wren'], series=CALDER, index=2,
         tags=['Mystery', 'Crime'], publisher='Harrow & Finch', published='2017-08-10',
         rating=6, state=('finished', 1.0, 150), style='window', palette=1, setting='town',
         cast=('Calder', 'Sergeant Pryce', 'Elias Thorne'),
         blurb='Three deaths in three orchards, and the only witness is a scarecrow.'),
    dict(title='A Quiet Word at Midnight', authors=['Tobias Wren'], series=CALDER, index=3,
         tags=['Mystery', 'Crime'], publisher='Harrow & Finch', published='2021-11-04',
         state=('reading', 0.18, 2), style='window', palette=2, setting='town',
         cast=('Calder', 'Sergeant Pryce', 'Dr Okafor'),
         blurb='Calder is on leave, which is when the telephone at the cottage begins to '
               'ring at midnight.'),
    dict(title='Halcyon Drift', authors=['Priya Anand-Holt'], series=HALCYON, index=1,
         tags=['Science Fiction'], publisher='Northlight Books', published='2018-02-01',
         rating=10, state=('finished', 1.0, 95), style='stars', palette=0, setting='space',
         cast=('Ilse', 'Tomas', 'MERIDIAN'),
         blurb='The generation ship Halcyon has been drifting for three hundred years. '
               'Its navigator has just noticed that it is slowing down.'),
    dict(title='The Long Burn', authors=['Priya Anand-Holt'], series=HALCYON, index=2,
         tags=['Science Fiction'], publisher='Northlight Books', published='2020-06-18',
         state=('reading', 0.67, 1), style='stars', palette=1, setting='space',
         cast=('Ilse', 'Tomas', 'MERIDIAN'),
         blurb='Eleven months of deceleration, and a crew that no longer agrees on where '
               'they are going.'),
    dict(title="Signal from Kepler's Reach", added_days=6, authors=['Priya Anand-Holt'],
         series=HALCYON, index=3, tags=['Science Fiction'], publisher='Northlight Books',
         published='2023-09-28', style='stars', palette=2, setting='space',
         cast=('Ilse', 'Tomas', 'MERIDIAN'),
         blurb="Someone at Kepler's Reach has been expecting them."),
    dict(title="The Lamplighter's Moth, Volume One", authors=['Ottilie Brandt', 'Sam Okafor'],
         series=MOTH, index=1, tags=['Comics', 'Fantasy'], publisher='Greywater Press',
         published='2022-12-01', rating=8, state=('finished', 1.0, 12), format='cbz',
         style='comic', palette=0,
         blurb='Every night a moth the size of a cat follows the lamplighter on her round.'),
    dict(title="The Cartographer's Apprentice", authors=['Elena Marsh'],
         tags=['Historical Fiction'], publisher='Harrow & Finch', published='2016-03-17',
         rating=8, state=('finished', 1.0, 255), style='compass', palette=0,
         setting='country', cast=('Anne', 'Master Hollis', 'Jem'),
         blurb='Lisbon, 1755. A girl who draws maps for a living is asked to draw one of a '
               'city that no longer exists.'),
    dict(title='Winter at Aldmoor House', authors=['Hugh Castellane'],
         tags=['Literary Fiction', 'Gothic'], publisher='Greywater Press',
         published='2012-10-25', style='hills', palette=3, setting='country',
         cast=('Margaret', 'Mr Fenwick', 'the housekeeper'),
         blurb='A governess, a house on the moor, and a winter that will not end.'),
    dict(title='The Small Hours Hotel', authors=['Mira Solberg'], tags=['Literary Fiction'],
         publisher='Meridian Editions', published='2021-01-28', rating=0,
         state=('reading', 0.83, 4), style='deco', palette=0, setting='city',
         cast=('Ines', 'the night porter', 'Mr Abernathy'),
         blurb='The guests of a hotel that only opens between two and five in the morning.'),
    dict(title='The Beekeeper of Lindenhall', authors=['Clara Dunmore'],
         tags=['Historical Fiction', 'Romance'], publisher='Small Orchard Press',
         published='2019-05-09', rating=6, state=('finished', 1.0, 230), style='botanical',
         palette=1, setting='country', cast=('Hanna', 'Felix', 'Aunt Dorothea'),
         blurb='A summer of swarms, letters and a walled garden in 1912.'),
    dict(title='Paper Boats on the Ouse', authors=['Jonah Ferreira'],
         tags=['Literary Fiction'], publisher='Meridian Editions', published='2023-07-13',
         style='sun', palette=1, setting='town', he=True, cast=('Danny', 'his grandmother', 'Lou'),
         blurb='Three summers, two brothers, one river.'),
    dict(title='The Clockwork Orchard', authors=['Bartholomew Quill'],
         tags=['Fantasy', 'Steampunk'], publisher='Lantern House', published='2014-11-20',
         style='bauhaus', palette=2, setting='country', he=True,
         cast=('Pip', 'Professor Hale', 'Ada'),
         blurb='Apples of brass and a gardener who winds them every morning.'),
    dict(title='Seven Letters to Hollin Street', authors=['Agnes Merriweather'],
         tags=['Romance'], publisher='Small Orchard Press', published='2018-02-08',
         rating=8, state=('finished', 1.0, 160), style='type', palette=4, setting='city',
         cast=('Rose', 'Arthur', 'Mrs Pell'),
         blurb='A love story told in letters that arrive forty years late.'),
    dict(title='Under the Tamarisk Moon', added_days=9, authors=['Nadia Haddad'],
         tags=['Literary Fiction'], publisher='Meridian Editions', published='2022-06-16',
         style='sun', palette=3, setting='coast', cast=('Leila', 'Yusuf', 'Grandfather'),
         blurb='A family returns to the house by the sea that none of them has forgiven.'),
    dict(title='The Last Ferry to Inchcolm', authors=['Eilidh Ross'],
         tags=['Mystery', 'Thriller'], publisher='Harrow & Finch', published='2020-10-01',
         style='hills', palette=1, setting='coast', cast=('Kirsty', 'Callum', 'the ferryman'),
         blurb='Twelve passengers board the last ferry of the season. Eleven get off.'),
    dict(title='Starlings over Wexcombe', authors=['Theo Lindgren'],
         tags=['Literary Fiction'], publisher='Greywater Press', published='2017-04-06',
         style='flock', palette=0, setting='country', cast=('Nell', 'Oliver', 'the vicar'),
         blurb='A village, a winter, and the evening the starlings did not come.'),
    dict(title="The Bellfounder's Wager", authors=['Rowan Achterberg'], tags=['Fantasy'],
         publisher='Lantern House', published='2025-02-20', added_days=3, style='deco',
         palette=2, setting='town', cast=('Marta', 'the Bellfounder', 'Brother Anselm'),
         blurb='A bell that rings once in a hundred years, and a wager on who will hear it.'),
    dict(title='Ashes of the Copper Sun', authors=['Dmitri Valen'],
         tags=['Science Fiction'], publisher='Northlight Books', published='2015-08-27',
         style='planet', palette=0, setting='space', he=True,
         cast=('Sava', 'Commander Ruiz', 'ECHO'),
         blurb='The sun of Tessaly is going out, and the colony has one ship.'),
    dict(title="The Lighthouse Keeper's Almanac", authors=['Morwenna Tregarthen'],
         tags=['Short Stories'], publisher='Greywater Press', published='2024-05-30',
         added_days=0, style='hills', palette=2, setting='coast',
         cast=('Tamsin', 'the keeper', 'Jory'),
         blurb='Twelve stories, one for each month, from a lighthouse on the Lizard.'),
    dict(title='A Field Guide to Ordinary Clouds', authors=['Rosalind Pike'],
         tags=['Science', 'Nature'], publisher='Northlight Books', published='2020-04-23',
         state=('reading', 0.3, 6), style='clouds', palette=0, kind='nonfiction',
         topics=('cumulus', 'the dew point', 'a cold front', 'cirrus', 'the troposphere',
                 'a mackerel sky', 'convection', 'fog'),
         chapters=('Looking Up', 'The Heap', "Mares' Tails", 'Fronts', 'Fog and Mist',
                   'Weather Lore', 'Keeping a Cloud Diary'),
         blurb='How to read the sky from your back step, with nothing but patience.'),
    dict(title="The Patient Gardener's Year", authors=['Wilfred Ashby'],
         tags=['Gardening'], publisher='Small Orchard Press', published='2018-01-11',
         rating=8, state=('finished', 1.0, 120), style='botanical', palette=0,
         kind='nonfiction',
         topics=('compost', 'a cold frame', 'the first frost', 'seedlings', 'mulch',
                 'pruning', 'the soil', 'a hedge'),
         chapters=('January', 'March', 'May', 'July', 'September', 'November'),
         blurb='A year in a small garden, month by month, mostly waiting.'),
    dict(title='Bread, Salt and Time', authors=['Lucia Ferrante'], tags=['Cooking'],
         publisher='Meridian Editions', published='2021-09-30', style='type', palette=1,
         kind='nonfiction',
         topics=('a sourdough starter', 'salt', 'the oven', 'flour', 'proving', 'the crust',
                 'olive oil', 'rye'),
         chapters=('Flour', 'Water', 'Salt', 'Time', 'Fire', 'The Table'),
         blurb='Thirty breads from a kitchen in Puglia, and why they take so long.'),
    dict(title='Small Engines of the Universe', authors=['Kwame Asante-Reed'],
         tags=['Science', 'Physics'], publisher='Northlight Books', published='2019-10-10',
         rating=10, state=('finished', 1.0, 60), style='orbits', palette=0,
         kind='nonfiction',
         topics=('entropy', 'a photon', 'the electron', 'gravity', 'a star', 'heat',
                 'a quantum of light', 'the nucleus'),
         chapters=('Heat', 'Light', 'Charge', 'Mass', 'Stars', 'Endings'),
         blurb='The machinery behind everyday things, from kettles to stars.'),
    dict(title='How Rivers Remember', added_days=12, authors=['Hanna Lindqvist'],
         tags=['Nature', 'Essays'], publisher='Greywater Press', published='2022-03-03',
         style='river', palette=0, kind='nonfiction',
         topics=('the floodplain', 'a meander', 'silt', 'the watershed', 'an oxbow lake',
                 'the spring melt', 'a weir', 'the delta'),
         chapters=('Source', 'Meander', 'Flood', 'Weir', 'Delta'),
         blurb='Essays on rivers, and on what they keep of the places they pass.'),
    dict(title='Notes on Letterpress', authors=['Gideon Marlow'],
         tags=['Design', 'Printing'], publisher='Greywater Press', published='2013-06-06',
         format='pdf', style='type', palette=3, kind='nonfiction',
         topics=('the composing stick', 'leading', 'a forme', 'the platen', 'ink', 'a quoin',
                 'kerning', 'the chase'),
         chapters=('Type', 'Setting', 'Locking Up', 'Printing'),
         blurb='A short guide to setting and printing by hand.'),
    dict(title='Walking the Old Drove Roads', authors=['Fergus McAllister'],
         tags=['Travel', 'History'], publisher='Harrow & Finch', published='2016-05-26',
         style='hills', palette=0, kind='nonfiction',
         topics=('the drove road', 'a cattle stance', 'the ford', 'a bothy', 'the pass',
                 'a dry-stone dyke', 'the drovers', 'a market town'),
         chapters=('Setting Out', 'The High Pass', 'Fords', 'Stances', 'Falkirk Tryst'),
         blurb='Four hundred miles on foot along the roads cattle once walked to market.'),
    dict(title='The Quiet Arithmetic of Habits', authors=['Selma Okonkwo-Hart'],
         tags=['Psychology'], publisher='Meridian Editions', published='2023-01-19',
         added_days=1, style='bauhaus', palette=0, kind='nonfiction',
         topics=('a habit', 'attention', 'a routine', 'the cue', 'a small change',
                 'the reward', 'memory', 'the evening'),
         chapters=('Cues', 'Routines', 'Rewards', 'Small Numbers', 'Evenings'),
         blurb='Why small things done often outweigh large things done once.'),
    dict(title='Le Jardin des heures perdues', authors=['Margaux Delacroix-Vian'],
         tags=['Roman'], publisher='Éditions du Pli', published='2019-08-22', language='fr',
         style='botanical', palette=2, setting='fr',
         blurb="Dans un jardin de Provence, une horloge retarde d'une heure chaque été."),
    dict(title='Die Uhrmacherin von Lindau', authors=['Friederike Albrecht'],
         tags=['Roman', 'Historisch'], publisher='Brückenverlag', published='2020-02-13',
         language='de', state=('reading', 0.12, 9), style='compass', palette=1,
         setting='de',
         blurb='Lindau am Bodensee, 1890: eine junge Uhrmacherin und ein Auftrag, der nicht '
               'zu erfüllen ist.'),
    dict(title='La casa de los vientos azules', authors=['Tomás Ibarra Lucena'],
         tags=['Novela'], publisher='Ediciones Albur', published='2017-11-09', language='es',
         style='sun', palette=0, setting='es',
         blurb='Una casa en la costa donde el viento cambia de color cada tarde.'),
    dict(title='Pride and Prejudice', authors=['Jane Austen'], tags=['Classics', 'Romance'],
         publisher='Penhallow Classics', published='1813', rating=10,
         state=('finished', 1.0, 275), style='cloth', palette=0, setting='country',
         cast=('Elizabeth', 'Mr Darcy', 'Jane'), classic='austen',
         blurb="The Bennet sisters, their mother's ambitions, and Mr Darcy."),
    dict(title='Moby-Dick', added_days=15, authors=['Herman Melville'],
         tags=['Classics', 'Adventure'], publisher='Penhallow Classics', published='1851',
         style='cloth', palette=1,
         setting='coast', he=True, cast=('Ishmael', 'Queequeg', 'Ahab'), classic='melville',
         blurb='Ishmael goes to sea on the Pequod, whose captain is hunting a white whale.'),
    dict(title='Frankenstein', authors=['Mary Shelley'], tags=['Classics', 'Gothic'],
         publisher='Penhallow Classics', published='1818', rating=8,
         state=('finished', 1.0, 180), style='cloth', palette=2, setting='country',
         he=True, cast=('Victor', 'Elizabeth', 'Clerval'), classic='shelley',
         blurb='A young scientist makes a creature, and abandons it.'),
    dict(title='The Adventures of Sherlock Holmes', authors=['Arthur Conan Doyle'],
         tags=['Classics', 'Mystery'], publisher='Penhallow Classics', published='1892',
         state=('reading', 0.55, 3), style='cloth', palette=3, setting='city',
         he=True, cast=('Holmes', 'Watson', 'Mrs Hudson'), classic='doyle',
         blurb='Twelve cases from Baker Street.'),
    dict(title='Middlemarch', authors=['George Eliot'], tags=['Classics'],
         publisher='Penhallow Classics', published='1871', style='cloth', palette=4,
         setting='country', cast=('Dorothea', 'Mr Casaubon', 'Lydgate'), classic='eliot',
         blurb='A provincial town, and the lives that cross in it.'),
]

SHELVES = [
    ('Holiday Reading', None, ['The Glass Estuary', 'The Last Ferry to Inchcolm',
                               'Under the Tamarisk Moon', 'The Small Hours Hotel',
                               "The Bellfounder's Wager"]),
    ('Five Stars', 'rating:5', []),
]

# Highlights: (book, chapter, paragraph, colour, note); the text is the paragraph's first
# sentence.
HIGHLIGHTS = [
    ('The Glass Estuary', 1, 3, 'yellow', ''),
    ('The Glass Estuary', 2, 5, 'green', 'The tide tables again: compare book one.'),
    ('The Glass Estuary', 3, 2, 'blue', ''),
    ('Halcyon Drift', 2, 4, 'yellow', 'This is where it turns.'),
    ('Small Engines of the Universe', 1, 2, 'pink', ''),
    ('Pride and Prejudice', 1, 1, 'yellow', ''),
]
BOOKMARKS = [('The Glass Estuary', 4), ('The Long Burn', 6)]

# ---------------------------------------------------------------------------------------
# Prose: sentences from templates over a setting's words, seeded per book.

SETTINGS = {
    'coast': dict(
        places=['harbour wall', 'quay', 'salt marsh', 'lighthouse', 'boathouse', 'estuary',
                'fish market', 'sea wall', 'shingle beach', 'net loft'],
        things=['lantern', 'coil of rope', 'oilskin coat', 'tide table', 'brass compass',
                'tin of matches', 'letter', 'oar', 'cork float', 'bottle of ink'],
        sounds=['the gulls', 'halyards knocking against the masts', 'the tide on the shingle',
                'a bell buoy far out', 'the wind in the reeds'],
        weather=['a sea fret', 'rain off the estuary', 'a north-easterly', 'a low grey sky',
                 'the evening flood']),
    'town': dict(
        places=['canal towpath', 'police house', 'churchyard', 'high street', 'orchard',
                'back room of the Swan', 'railway bridge', 'lock cottage', 'market square',
                'vicarage garden'],
        things=['notebook', 'pocket watch', 'telegram', 'muddy boot', 'ledger', 'door key',
                'bicycle', 'pair of gloves', 'photograph', 'cup of tea'],
        sounds=['the church clock', 'a dog barking two gardens away', 'the last train',
                'rain in the gutters', 'someone whistling'],
        weather=['the first frost', 'a fine drizzle', 'a still, cold evening', 'low cloud',
                 'a thin autumn sun']),
    'space': dict(
        places=['observation deck', 'cargo bay', 'galley', 'airlock', 'bridge',
                'hydroponics ring', 'maintenance shaft', 'medical bay', 'long corridor',
                'navigation room'],
        things=['data slate', 'pressure suit', 'ration tin', 'star chart', 'comm unit',
                'mug of reconstituted coffee', 'access card', 'torch', 'logbook', 'helmet'],
        sounds=['the hum of the drive', 'the recyclers ticking over', 'a distant alarm',
                'the hull settling', 'the soft chime of the hour'],
        weather=['the dark between the stars', 'the slow turn of the planet below',
                 'a scatter of cold light', 'the glare of the far sun', "the ship's night"]),
    'country': dict(
        places=['kitchen garden', 'library', 'orchard', 'attic', 'stable yard', 'lane',
                'mill pond', 'drawing room', 'walled garden', 'top meadow'],
        things=['letter', 'teacup', 'candle', 'key', 'pressed flower', 'shawl', 'basket',
                'book of sermons', 'pair of boots', 'pocket knife'],
        sounds=['the rooks in the elms', 'a cart on the lane', 'the clock in the hall',
                'rain against the glass', 'the kettle'],
        weather=['a heavy summer heat', 'a hard frost', 'rain from the west',
                 'a pale morning', 'the long light of evening']),
    'city': dict(
        places=['hotel lobby', 'night bus', 'rooftop', 'all-night café', 'station concourse',
                'stairwell', 'corner shop', 'bridge', 'back office', 'tram stop'],
        things=['room key', 'paper cup', 'umbrella', 'timetable', 'photograph',
                'newspaper', 'coat', 'envelope', 'cigarette case', 'ticket'],
        sounds=['the traffic', 'a siren a long way off', 'the lift', 'rain on the awning',
                'a radio in another room'],
        weather=['a warm drizzle', 'the orange dark', 'a cold, bright morning',
                 'the last of the light', 'a fog off the river']),
}

NARRATIVE = [
    '{A} stood at the edge of the {place} and watched {weather} come in.',
    'For a long time nobody spoke, and the only sound was {sound}.',
    '{A} turned the {thing} over in {his} hands, as if it might explain itself.',
    'It was later than {he} had thought; {sound} had stopped some time ago.',
    'The {place} was empty, which was not the same as being deserted.',
    '{B} had left the {thing} on the table, where anyone could see it, and that was the '
    'first thing that seemed wrong.',
    'There are places that wait for you, and the {place} was one of them.',
    '{A} counted to ten, then to twenty, and then gave up counting.',
    'Somewhere beyond the {place2}, {sound} carried on as though nothing had happened.',
    'By the time they reached the {place}, {weather} had found its way into everything.',
    '{He} remembered the {thing}, and the evening {he} had found it, and wished {he} had not.',
    'Nobody in the {place} looked up when the door opened.',
    '{A} walked the length of the {place} twice before {he} was sure.',
    'The light changed, as it always did at that hour, and the {place} became somewhere '
    'else.',
    'It would have been easy to go back; it usually is.',
    '{B} was waiting by the {place2}, collar up, with the look of someone who had been '
    'there a while.',
    'There was a smell of {smell} and, under it, something older.',
    '{A} wrote it all down that night, in the back of the {thing2} book, and read it again '
    'in the morning.',
    'What {he} wanted, more than anything, was for somebody else to decide.',
    'Down by the {place2}, a single light was burning.',
    'They went on in silence, which suited them both.',
    'Afterwards {A} could not have said how long {he} stood there.',
    'The {thing} was exactly where {C} had said it would be, and that was somehow worse.',
    '{He} had promised {B} {he} would be careful, and {he} meant to be.',
    'The day went on without them.',
    'Even {sound} seemed to have something to say about it.',
    '{A} had not slept, and it showed.',
    'The {thing} was heavier than {he} remembered.',
    '{C} would have known what to do; {C} always did.',
    'A gust came across the {place} and was gone.',
    'It was the kind of evening that makes promises it does not keep.',
    '{A} let the door close behind {him} and stood for a moment in the dark.',
    'From the {place2} came {sound}, and then nothing at all.',
    'Every so often {B} glanced at the {thing}, then away again.',
    'There was no hurry. There had never been any hurry.',
    '{A} thought of the summer before, and of everything {he} had not said.',
    'The map was wrong, or the {place} had moved.',
    'Under {weather}, the {place} looked smaller than it had in the morning.',
    'Later, much later, {he} would remember this as the moment it began.',
    '{B} laughed, though nothing was funny.',
    'In the end it was {B} who spoke first.',
]

DIALOGUE = [
    'We should have left an hour ago',
    "I don't think it was an accident",
    "You're not going to like this",
    'Tell me again, slowly',
    "It's been like that since the spring",
    'Nobody comes down here any more',
    'I said I would be back, and here I am',
    'Did you see who it was?',
    "There's no sense in waiting",
    'That was never the plan',
    "Then we'll do it the long way",
    'I kept it, all these years',
    "Ask {C}, if you don't believe me",
    "It's colder than it looks",
    'Leave the light on',
    "You knew, didn't you?",
    'Not yet. Soon',
    'Have you eaten anything today?',
]

SPEECH = ['said', 'said quietly', 'asked', 'answered', 'said at last']

ACTION = [
    '{A} did not answer at once.',
    '{B} looked away, towards the {place2}.',
    'Neither of them moved.',
    '{He} put the {thing} down carefully.',
    'Outside, {sound} went on.',
]

SMELLS = ['tar and rope', 'woodsmoke', 'wet stone', 'coffee', 'old paper', 'rain',
          'apples', 'engine oil', 'lavender']

EXPOSITORY = [
    'Most of us pass {t1} every day without giving it a second thought.',
    'It is tempting to think of {t1} as simple, but {t2} complicates the picture.',
    'In the nineteenth century, careful observers recorded {t1} with nothing more than a '
    'notebook, a pencil and a great deal of patience.',
    'Consider {t1} for a moment, and what it asks of you.',
    'The answer, as so often, lies in {t2}.',
    'None of this is new; what is new is how easily we can now see it for ourselves.',
    'A good rule of thumb is to begin with {t1} and to leave {t2} until later.',
    'There is a moment, familiar to anyone who has spent time with {t1}, when everything '
    'suddenly makes sense.',
    'That, at least, is the theory.',
    'In practice, {t1} rarely behaves as the textbooks say it should.',
    'If you take only one thing from this chapter, let it be this: {t1} rewards attention.',
    'We will come back to {t2} in a later chapter.',
    'The best way to understand {t1} is to watch it closely for a week.',
    'Each of these has a name, but the names matter less than the habit of noticing.',
    'It helps to keep a record, however brief.',
    'Some of the oldest accounts of {t1} are still among the best.',
    'The connection between {t1} and {t2} is closer than it first appears.',
    'Nothing here needs special equipment.',
]

FOREIGN = {
    'fr': [
        'Le jardin dormait encore lorsque Camille ouvrit la grille.',
        'Elle avait toujours aimé cette heure-là, entre la nuit et le matin.',
        "L'horloge de la maison retardait, comme chaque été.",
        'Personne ne savait vraiment pourquoi, et personne ne voulait la réparer.',
        'Il y avait une odeur de lavande et de pierre chaude.',
        'Sa grand-mère disait que les heures perdues finissaient toujours par revenir.',
        'Au fond du jardin, le vieux figuier penchait vers le mur.',
        "Elle s'assit sur le banc et attendit que le soleil touche les toits.",
        "Les cigales commencèrent, d'abord une, puis toutes à la fois.",
        "Il faudrait écrire à Lucien, mais pas aujourd'hui.",
        "Le facteur passa sans s'arrêter.",
        "Dans la cuisine, quelqu'un avait laissé une tasse encore tiède.",
        "Elle se souvint de l'été où tout avait changé.",
        "Rien ne pressait, et c'était bien là le problème.",
    ],
    'de': [
        'Die Werkstatt lag im zweiten Stock, über der Bäckerei am Hafen.',
        'Johanna stieg jeden Morgen um sechs Uhr die schmale Treppe hinauf.',
        'Auf dem Werktisch lagen die Zahnräder in ordentlichen Reihen.',
        'Ihr Vater hatte immer gesagt, eine Uhr sei ein Versprechen.',
        'Draußen läuteten die Glocken der Stadtkirche.',
        'Der Nebel hing tief über dem See und verschluckte die Berge.',
        'Niemand in Lindau glaubte, dass eine Frau diesen Auftrag erfüllen könne.',
        'Sie öffnete das Gehäuse vorsichtig und hielt den Atem an.',
        'Es roch nach Öl, nach Messing und nach frischem Brot.',
        'Der Brief aus Konstanz lag noch ungeöffnet neben der Lampe.',
        'Am Abend kam der Wind vom Wasser herauf.',
        'Sie arbeitete, bis das Licht nicht mehr reichte.',
        'Vielleicht, dachte sie, war das Unmögliche nur eine Frage der Geduld.',
    ],
    'es': [
        'La casa estaba al final del camino, donde la arena se mezcla con la hierba.',
        'Cada tarde, a las seis, el viento cambiaba de color.',
        'Mi abuela decía que era azul los martes y verde los domingos.',
        'Nadie en el pueblo se atrevía a contradecirla.',
        'Desde la ventana se veía el faro y, más allá, el mar.',
        'Aquel verano llegaron cartas que nadie había escrito.',
        'Mi hermano las guardaba en una caja de galletas, debajo de la cama.',
        'Olía a sal, a jazmín y a pan recién hecho.',
        'Por las noches oíamos el mar golpear contra las rocas.',
        'Mi padre no hablaba de la casa, ni de por qué la habíamos dejado.',
        'El viento entraba por todas las rendijas.',
        'Fue entonces cuando encontré la llave.',
    ],
}

CHAPTER_NAMES = [
    'The Lamps on the Quay', 'A Change in the Weather', 'What the Tide Left', 'Visitors',
    'The Long Night', 'Small Hours', 'An Errand', 'The Letter', 'Crossing', 'The Old Road',
    'Low Water', 'A Light Left On', 'Morning', 'The Reckoning', 'Home',
]

CLASSIC_OPENINGS = {
    'austen': [
        'It is a truth universally acknowledged, that a single man in possession of a good '
        'fortune, must be in want of a wife.',
        'However little known the feelings or views of such a man may be on his first '
        'entering a neighbourhood, this truth is so well fixed in the minds of the '
        'surrounding families, that he is considered the rightful property of some one or '
        'other of their daughters.',
        '“My dear Mr. Bennet,” said his lady to him one day, “have you heard '
        'that Netherfield Park is let at last?”',
        'Mr. Bennet replied that he had not.',
        '“But it is,” returned she; “for Mrs. Long has just been here, and '
        'she told me all about it.”',
        'Mr. Bennet made no answer.',
    ],
    'melville': [
        'Call me Ishmael. Some years ago—never mind how long precisely—having '
        'little or no money in my purse, and nothing particular to interest me on shore, I '
        'thought I would sail about a little and see the watery part of the world. It is a '
        'way I have of driving off the spleen and regulating the circulation.',
    ],
    'shelley': [
        'You will rejoice to hear that no disaster has accompanied the commencement of an '
        'enterprise which you have regarded with such evil forebodings. I arrived here '
        'yesterday, and my first task is to assure my dear sister of my welfare and '
        'increasing confidence in the success of my undertaking.',
    ],
    'doyle': [
        'To Sherlock Holmes she is always the woman. I have seldom heard him mention her '
        'under any other name. In his eyes she eclipses and predominates the whole of her '
        'sex.',
    ],
    'eliot': [
        'Who that cares much to know the history of man, and how the mysterious mixture '
        'behaves under the varying experiments of Time, has not dwelt, at least briefly, on '
        'the life of Saint Theresa?',
    ],
}

def seed_of(text):
    return int.from_bytes(text.encode('utf-8'), 'little') % (2 ** 61)


def capitalize(sentence):
    return sentence[:1].upper() + sentence[1:]


class Deck:
    """Draws from a list without repeating one until all have been drawn."""

    def __init__(self, rng, items):
        self.rng = rng
        self.items = list(items)
        self.pile = []

    def draw(self):
        if not self.pile:
            self.pile = self.items[:]
            self.rng.shuffle(self.pile)
        return self.pile.pop()

    def deal(self, count):
        return [self.draw() for _ in range(count)]


def fiction_sentence(rng, words, cast, template, she=True):
    a, b, c = cast
    return capitalize(template.format(
        A=a, B=b, C=c, he='she' if she else 'he', He='She' if she else 'He',
        his='her' if she else 'his', him='her' if she else 'him',
        place=rng.choice(words['places']),
        place2=rng.choice(words['places']), thing=rng.choice(words['things']),
        thing2=rng.choice(['red', 'blue', 'old', 'little']), sound=rng.choice(words['sounds']),
        weather=rng.choice(words['weather']), smell=rng.choice(SMELLS)))


def fiction_paragraph(rng, words, cast, decks, she=True):
    if rng.random() < 0.3:
        line = decks['dialogue'].draw().format(C=cast[2])
        mark = '?' if line.endswith('?') else ','
        speaker = rng.choice(cast[:2])
        verb = 'asked' if mark == '?' else rng.choice([s for s in SPEECH if s != 'asked'])
        text = f'“{line.rstrip("?")}{mark}” {speaker} {verb}.'
        if rng.random() < 0.6:
            text += ' ' + fiction_sentence(rng, words, cast, rng.choice(ACTION), she)
        return text
    return ' '.join(fiction_sentence(rng, words, cast, template, she)
                    for template in decks['narrative'].deal(rng.randint(3, 6)))


def expository_paragraph(rng, topics, decks):
    sentences = []
    for template in decks['expository'].deal(rng.randint(3, 6)):
        t1, t2 = rng.sample(topics, 2)
        sentences.append(capitalize(template.format(t1=t1, t2=t2)))
    return ' '.join(sentences)


def foreign_paragraph(rng, decks):
    return ' '.join(decks['foreign'].deal(rng.randint(3, 5)))


def chapters_of(book, rng):
    """[(title, [paragraph])] for an EPUB or PDF book."""
    kind = book.get('kind', 'fiction')
    if kind == 'nonfiction':
        titles = list(book['chapters'])
    elif book.get('language', 'en') != 'en':
        word = {'fr': 'Chapitre', 'de': 'Kapitel', 'es': 'Capítulo'}[book['language']]
        titles = [f'{word} {n}' for n in range(1, 9)]
    else:
        names = rng.sample(CHAPTER_NAMES, 10)
        titles = names
    decks = {'narrative': Deck(rng, NARRATIVE), 'dialogue': Deck(rng, DIALOGUE),
             'expository': Deck(rng, EXPOSITORY),
             'foreign': Deck(rng, FOREIGN.get(book.get('language'), []))}
    she = not book.get('he')
    chapters = []
    for number, title in enumerate(titles):
        paragraphs = []
        if number == 0 and book.get('classic'):
            paragraphs.extend(CLASSIC_OPENINGS[book['classic']])
        for _ in range(rng.randint(14, 22)):
            if kind == 'nonfiction':
                paragraphs.append(expository_paragraph(rng, book['topics'], decks))
            elif book.get('language', 'en') != 'en':
                paragraphs.append(foreign_paragraph(rng, decks))
            else:
                paragraphs.append(fiction_paragraph(rng, SETTINGS[book['setting']],
                                                    book['cast'], decks, she))
        chapters.append((title, paragraphs))
    return chapters


# ---------------------------------------------------------------------------------------
# Covers, drawn with Cairo and set with Pango.

PALETTES = {
    # (background top, background bottom, ink, accent)
    'waves': [('#0b3c5d', '#328cc1', '#f6f5f4', '#f6d32d'),
              ('#1d3b2a', '#4f7c5a', '#f6f5f4', '#ffbe6f'),
              ('#2b1d3d', '#6b4e8e', '#f6f5f4', '#8ff0a4'),
              ('#3d2b1c', '#9a6b3c', '#fdf6e3', '#99c1f1')],
    'window': [('#141e30', '#243b55', '#f6f5f4', '#f9c440'),
               ('#2d1b1b', '#5c2a2a', '#f6f5f4', '#ffd38a'),
               ('#1b2d24', '#2f5141', '#f6f5f4', '#f6d32d')],
    'stars': [('#05070f', '#1a2240', '#e6ecff', '#ff7800'),
              ('#0f0518', '#3a1458', '#f5e6ff', '#33d17a'),
              ('#02141a', '#0b4050', '#e6fbff', '#f66151')],
    'compass': [('#efe3c8', '#d9c49a', '#3d2b1c', '#a51d2d'),
                ('#d7e3e8', '#a9c2cc', '#1c2b33', '#8a5a00')],
    'hills': [('#f9d29d', '#e66465', '#2b1d1d', '#fdf6e3'),
              ('#9fb8c7', '#3d5a6c', '#0d1a22', '#f6f5f4'),
              ('#c9d6a3', '#6a8d4e', '#1d2b14', '#fdf6e3'),
              ('#d6dbe4', '#56607a', '#141820', '#f6f5f4')],
    'deco': [('#101820', '#1f3a4a', '#e8c872', '#e8c872'),
             ('#2a0f1e', '#4d1a36', '#f0d58c', '#f0d58c'),
             ('#0e2620', '#1b4b3e', '#e9d9a6', '#e9d9a6')],
    'botanical': [('#f4efe1', '#e8dfc4', '#2f4a2c', '#b5835a'),
                  ('#fbf3d5', '#f1e2a8', '#4a3a12', '#c88a12'),
                  ('#f3e9ee', '#e5d2dc', '#4a2238', '#6a8d4e')],
    'sun': [('#ffd6a5', '#ff8c61', '#2b1b3d', '#fff3e0'),
            ('#e0f0ea', '#95c8b5', '#1d3b34', '#f28f3b'),
            ('#2b2d42', '#8d99ae', '#edf2f4', '#ef233c'),
            ('#fde2c4', '#f4a259', '#3b2416', '#5b8e7d')],
    'bauhaus': [('#f2ede3', '#f2ede3', '#1d1d1d', '#e01b24'),
                ('#f2ede3', '#f2ede3', '#1d1d1d', '#1c71d8'),
                ('#1d1d1d', '#1d1d1d', '#f2ede3', '#f6d32d')],
    'type': [('#e8e2d4', '#e8e2d4', '#1d1d1d', '#c01c28'),
             ('#f3d9b1', '#f3d9b1', '#3b2416', '#7a3e13'),
             ('#1a1a1a', '#1a1a1a', '#f6f5f4', '#e5a50a'),
             ('#e6eef0', '#e6eef0', '#123040', '#e66100'),
             ('#5c1a2b', '#5c1a2b', '#f9ede0', '#f0c987')],
    'cloth': [('#1f3b5a', '#1a3350', '#d8b45a', '#d8b45a'),
              ('#2f3b2a', '#283322', '#d8b45a', '#d8b45a'),
              ('#4a1c1c', '#3e1717', '#d8b45a', '#d8b45a'),
              ('#2b2b2b', '#232323', '#d8b45a', '#d8b45a'),
              ('#5a4630', '#4d3b28', '#e9d5a1', '#e9d5a1')],
    'flock': [('#f6c89f', '#8f7aa8', '#1d1530', '#1d1530')],
    'planet': [('#120c06', '#3d1e0a', '#fbe7c6', '#ff7800')],
    'clouds': [('#6fa8dc', '#cfe2f3', '#0b2a45', '#ffffff')],
    'orbits': [('#0d0d12', '#1d1d2b', '#f6f5f4', '#f6d32d')],
    'river': [('#e9f1ec', '#cfe3d8', '#16352a', '#3a7d9c')],
    'comic': [('#1d2b53', '#7e2553', '#fff1e8', '#ffec27')],
}


def rgb(color):
    color = color.lstrip('#')
    return tuple(int(color[i:i + 2], 16) / 255 for i in (0, 2, 4))


def set_color(ctx, color, alpha=1.0):
    ctx.set_source_rgba(*rgb(color), alpha)


def vertical_gradient(ctx, top, bottom, y0=0, y1=None):
    y1 = COVER_SIZE[1] if y1 is None else y1
    gradient = cairo.LinearGradient(0, y0, 0, y1)
    gradient.add_color_stop_rgb(0, *rgb(top))
    gradient.add_color_stop_rgb(1, *rgb(bottom))
    return gradient


def layout_text(ctx, text, font, size, width, align='center', spacing=0.0, line_spacing=0):
    layout = PangoCairo.create_layout(ctx)
    layout.set_font_description(Pango.FontDescription.from_string(f'{font} {size}px'))
    layout.set_width(int(width * Pango.SCALE))
    layout.set_wrap(Pango.WrapMode.WORD)
    layout.set_alignment({'center': Pango.Alignment.CENTER, 'left': Pango.Alignment.LEFT,
                          'right': Pango.Alignment.RIGHT}[align])
    if line_spacing:
        layout.set_line_spacing(line_spacing)
    if spacing:
        attributes = Pango.AttrList()
        attributes.insert(Pango.attr_letter_spacing_new(int(spacing * Pango.SCALE)))
        layout.set_attributes(attributes)
    layout.set_text(text, -1)
    return layout


def fitted(ctx, text, font, size, width, max_lines=4, align='center', spacing=0.0,
           line_spacing=0.9):
    """A layout of `text` at the largest size up to `size` whose lines fit `width` without
    breaking a word and number at most max_lines."""
    while True:
        layout = layout_text(ctx, text, font, size, width, align, spacing, line_spacing)
        widest = max(layout_text(ctx, word, font, size, 10000, align, spacing)
                     .get_pixel_size()[0] for word in text.split())
        if (widest <= width and layout.get_line_count() <= max_lines) or size <= 18:
            return layout
        size -= 2


def draw_text(ctx, layout, x, y, color, alpha=1.0):
    """Show a layout with its top at y, centred on the cover's width when x is None."""
    width, height = layout.get_pixel_size()
    if x is None:
        x = (COVER_SIZE[0] - layout.get_width() / Pango.SCALE) / 2
    set_color(ctx, color, alpha)
    ctx.move_to(x, y)
    PangoCairo.show_layout(ctx, layout)
    return height


def series_line(book):
    if not book.get('series'):
        return ''
    number = int(book['index'])
    words = ['', 'One', 'Two', 'Three', 'Four', 'Five', 'Six']
    return f'{book["series"]} · Book {words[number]}'


def standard_text(ctx, book, ink, title_y=90, title_font='Noto Serif Display Bold',
                  title_size=74, author_y=None, upper=False, width=480, accent=None,
                  author_font='Noto Sans Medium', author=True):
    """The title (and the series line under it) from title_y down, then the author at
    author_y: the foot of the cover when None, just under the title when 'below'. Returns
    the y the text ends at."""
    title = book['title'].upper() if upper else book['title']
    layout = fitted(ctx, title, title_font, title_size, width, spacing=2 if upper else 0)
    bottom = title_y + draw_text(ctx, layout, None, title_y, ink)
    line = series_line(book)
    if line:
        small = layout_text(ctx, line.upper(), 'Noto Sans SemiBold', 16, width, spacing=2.5)
        bottom += 22 + draw_text(ctx, small, None, bottom + 22, accent or ink, 0.9)
    if not author:
        return bottom
    author_layout = layout_text(ctx, ' & '.join(book['authors']).upper(), author_font, 24,
                                520, spacing=4)
    if author_y is None:
        author_y = COVER_SIZE[1] - 110
    elif author_y == 'below':
        author_y = bottom + 28
    bottom = max(bottom, author_y + draw_text(ctx, author_layout, None, author_y, ink))
    return bottom


def cover_waves(ctx, book, colors, rng):
    top, bottom, ink, accent = colors
    width, height = COVER_SIZE
    ctx.set_source(vertical_gradient(ctx, top, bottom))
    ctx.paint()
    # A low moon, then rolling bands of waves, lighter towards the front.
    set_color(ctx, accent, 0.9)
    ctx.arc(width * 0.72, 500, 50, 0, 2 * math.pi)
    ctx.fill()
    for band in range(7):
        base = 520 + band * 56
        amplitude = 14 + band * 3
        phase = rng.uniform(0, math.pi)
        ctx.move_to(0, height)
        for x in range(0, width + 11, 10):
            y = base + amplitude * math.sin(x / (52 + band * 6) + phase)
            ctx.line_to(x, y)
        ctx.line_to(width, height)
        ctx.close_path()
        shade = band / 7
        ctx.set_source_rgba(*(c * (0.55 - 0.35 * shade) for c in rgb(top)), 0.55 + 0.06 * band)
        ctx.fill_preserve()
        set_color(ctx, ink, 0.25)
        ctx.set_line_width(2)
        ctx.stroke()
    standard_text(ctx, book, ink, title_y=80, accent=accent)


def cover_window(ctx, book, colors, rng):
    top, bottom, ink, accent = colors
    width, height = COVER_SIZE
    ctx.set_source(vertical_gradient(ctx, top, bottom))
    ctx.paint()
    # Rooftops against the sky, one window lit.
    set_color(ctx, '#000000', 0.55)
    x = -20
    roofs = []
    while x < width:
        w = rng.randint(70, 140)
        h = rng.randint(250, 420)
        roofs.append((x, w, h))
        ctx.move_to(x, height)
        ctx.line_to(x, height - h)
        ctx.line_to(x + w / 2, height - h - rng.randint(30, 70))
        ctx.line_to(x + w, height - h)
        ctx.line_to(x + w, height)
        ctx.close_path()
        ctx.fill()
        x += w - 4
    inside = [n for n, (x, w, h) in enumerate(roofs) if x > 20 and x + w < width - 20]
    lit = rng.choice(inside)
    for index, (x, w, h) in enumerate(roofs):
        for row in range(2):
            wy = height - h + 40 + row * 70
            if index == lit and row == 0:
                set_color(ctx, accent)
            else:
                set_color(ctx, '#ffffff', 0.06)
            ctx.rectangle(x + w / 2 - 14, wy, 28, 40)
            ctx.fill()
    set_color(ctx, '#f6f5f4', 0.85)
    ctx.arc(110, 120, 30, 0, 2 * math.pi)
    ctx.fill()
    standard_text(ctx, book, ink, title_y=190, title_font='Noto Serif Bold', title_size=64,
                  accent=accent, author_y=60)


def cover_stars(ctx, book, colors, rng):
    top, bottom, ink, accent = colors
    width, height = COVER_SIZE
    ctx.set_source(vertical_gradient(ctx, top, bottom))
    ctx.paint()
    for _ in range(260):
        set_color(ctx, '#ffffff', rng.uniform(0.2, 0.9))
        ctx.arc(rng.uniform(0, width), rng.uniform(0, height), rng.uniform(0.6, 2.0), 0,
                2 * math.pi)
        ctx.fill()
    # A ship's long trail across a planet's limb.
    planet = cairo.RadialGradient(300, 1250, 200, 300, 1250, 640)
    planet.add_color_stop_rgba(0, *rgb(accent), 0.9)
    planet.add_color_stop_rgba(1, *rgb(bottom), 1)
    ctx.set_source(planet)
    ctx.arc(300, 1250, 640, 0, 2 * math.pi)
    ctx.fill()
    trail = cairo.LinearGradient(80, 560, 470, 380)
    trail.add_color_stop_rgba(0, *rgb(ink), 0)
    trail.add_color_stop_rgba(1, *rgb(ink), 0.95)
    ctx.set_source(trail)
    ctx.set_line_width(3)
    ctx.move_to(80, 560)
    ctx.curve_to(220, 470, 340, 420, 470, 380)
    ctx.stroke()
    set_color(ctx, ink)
    ctx.arc(470, 380, 5, 0, 2 * math.pi)
    ctx.fill()
    title = fitted(ctx, book['title'].upper(), 'Noto Sans Light', 64, 500, spacing=8)
    draw_text(ctx, title, None, 110, ink)
    line = series_line(book)
    if line:
        draw_text(ctx, layout_text(ctx, line.upper(), 'Noto Sans SemiBold', 15, 500,
                                   spacing=3), None, 70, accent)
    author = layout_text(ctx, ' & '.join(book['authors']).upper(), 'Noto Sans Medium', 22,
                         500, spacing=6)
    draw_text(ctx, author, None, height - 90, ink)


def cover_compass(ctx, book, colors, rng):
    top, bottom, ink, accent = colors
    width, height = COVER_SIZE
    ctx.set_source(vertical_gradient(ctx, top, bottom))
    ctx.paint()
    # A faint map grid and coastline, a compass rose over it.
    set_color(ctx, ink, 0.12)
    ctx.set_line_width(1)
    for x in range(0, width, 50):
        ctx.move_to(x, 0)
        ctx.line_to(x, height)
    for y in range(0, height, 50):
        ctx.move_to(0, y)
        ctx.line_to(width, y)
    ctx.stroke()
    set_color(ctx, ink, 0.35)
    ctx.set_line_width(2.5)
    ctx.move_to(0, 600)
    x, y = 0, 600
    while x < width:
        x += rng.uniform(20, 45)
        y += rng.uniform(-30, 30)
        ctx.line_to(x, y)
    ctx.stroke()
    cx, cy, r = width / 2, 520, 150
    set_color(ctx, ink, 0.8)
    ctx.set_line_width(2)
    ctx.arc(cx, cy, r, 0, 2 * math.pi)
    ctx.stroke()
    ctx.arc(cx, cy, r - 12, 0, 2 * math.pi)
    ctx.stroke()
    for point in range(16):
        angle = point * math.pi / 8
        length = r - 4 if point % 4 == 0 else (r * 0.6 if point % 2 == 0 else r * 0.4)
        set_color(ctx, accent if point % 4 == 0 else ink, 0.9)
        ctx.move_to(cx, cy)
        ctx.line_to(cx + math.cos(angle - 0.09) * length * 0.25,
                    cy + math.sin(angle - 0.09) * length * 0.25)
        ctx.line_to(cx + math.cos(angle) * length, cy + math.sin(angle) * length)
        ctx.line_to(cx + math.cos(angle + 0.09) * length * 0.25,
                    cy + math.sin(angle + 0.09) * length * 0.25)
        ctx.close_path()
        ctx.fill()
    standard_text(ctx, book, ink, title_y=70, title_font='Noto Serif Display SemiBold Italic',
                  title_size=66, accent=accent)


def hill_layers(ctx, colors, rng, count=5, start=430):
    top, bottom, ink, accent = colors
    width, height = COVER_SIZE
    for layer in range(count):
        base = start + layer * 85
        ctx.move_to(0, height)
        ctx.line_to(0, base)
        x = 0
        points = []
        while x <= width + 120:
            points.append((x, base + rng.uniform(-60, 30)))
            x += rng.uniform(140, 230)
        ctx.line_to(*points[0])
        for (x0, y0), (x1, y1) in zip(points, points[1:], strict=False):
            mid = (x0 + x1) / 2
            ctx.curve_to(mid, y0, mid, y1, x1, y1)
        ctx.line_to(width, height)
        ctx.close_path()
        mix = (layer + 1) / count
        r0, g0, b0 = rgb(bottom)
        r1, g1, b1 = rgb(ink)
        ctx.set_source_rgb(r0 + (r1 - r0) * mix, g0 + (g1 - g0) * mix, b0 + (b1 - b0) * mix)
        ctx.fill()


def cover_hills(ctx, book, colors, rng):
    top, bottom, ink, accent = colors
    ctx.set_source(vertical_gradient(ctx, top, bottom, 0, 600))
    ctx.paint()
    # The moon and the hills sit below the title, however many lines it takes.
    title = fitted(ctx, book['title'], 'Noto Serif Display Bold', 74, 480)
    moon_y = max(450, 80 + title.get_pixel_size()[1] + 100)
    set_color(ctx, accent, 0.85)
    ctx.arc(430, moon_y, 40, 0, 2 * math.pi)
    ctx.fill()
    hill_layers(ctx, colors, rng, start=moon_y + 30)
    standard_text(ctx, book, ink, title_y=80, title_font='Noto Serif Display Bold',
                  accent=ink, author=False)
    # The author over the darkest hill, in the light colour.
    ctx.save()
    ctx.rectangle(0, COVER_SIZE[1] - 140, COVER_SIZE[0], 140)
    ctx.clip()
    set_color(ctx, ink)
    ctx.paint()
    ctx.restore()
    author = layout_text(ctx, ' & '.join(book['authors']).upper(), 'Noto Sans Medium', 24,
                         520, spacing=4)
    draw_text(ctx, author, None, COVER_SIZE[1] - 92, accent)


def cover_deco(ctx, book, colors, rng):
    top, bottom, ink, accent = colors
    width, height = COVER_SIZE
    ctx.set_source(vertical_gradient(ctx, top, bottom))
    ctx.paint()
    cx, cy = width / 2, height + 40
    set_color(ctx, accent, 0.35)
    ctx.set_line_width(2)
    for ray in range(25):
        angle = math.pi + ray * math.pi / 24
        ctx.move_to(cx, cy)
        ctx.line_to(cx + math.cos(angle) * 900, cy + math.sin(angle) * 900)
    ctx.stroke()
    for radius in (260, 300, 340):
        set_color(ctx, accent, 0.8 if radius == 260 else 0.5)
        ctx.arc(cx, cy, radius, math.pi, 2 * math.pi)
        ctx.stroke()
    set_color(ctx, top)
    ctx.arc(cx, cy, 256, math.pi, 2 * math.pi)
    ctx.fill()
    set_color(ctx, accent)
    ctx.set_line_width(3)
    ctx.rectangle(30, 30, width - 60, height - 60)
    ctx.stroke()
    ctx.set_line_width(1)
    ctx.rectangle(40, 40, width - 80, height - 80)
    ctx.stroke()
    standard_text(ctx, book, ink, title_y=120, title_font='Noto Serif Display Medium',
                  title_size=70, upper=True, accent=accent, author_y=height - 170)


def leaf(ctx, x, y, length, angle, color):
    ctx.save()
    ctx.translate(x, y)
    ctx.rotate(angle)
    ctx.move_to(0, 0)
    ctx.curve_to(length * 0.3, -length * 0.28, length * 0.75, -length * 0.22, length, 0)
    ctx.curve_to(length * 0.75, length * 0.22, length * 0.3, length * 0.28, 0, 0)
    set_color(ctx, color, 0.9)
    ctx.fill()
    ctx.move_to(0, 0)
    ctx.line_to(length * 0.9, 0)
    ctx.set_source_rgba(1, 1, 1, 0.35)
    ctx.set_line_width(1.5)
    ctx.stroke()
    ctx.restore()


def cover_botanical(ctx, book, colors, rng):
    top, bottom, ink, accent = colors
    width, height = COVER_SIZE
    ctx.set_source(vertical_gradient(ctx, top, bottom))
    ctx.paint()
    # Three stems rising from the foot, leaves in pairs.
    for stem in range(3):
        x0 = width / 2 + (stem - 1) * 110
        x1 = x0 + rng.uniform(-60, 60)
        top_y = rng.uniform(420, 480)
        set_color(ctx, ink, 0.85)
        ctx.set_line_width(3)
        ctx.move_to(x0, height + 10)
        ctx.curve_to(x0, 700, x1, 560, x1, top_y)
        ctx.stroke()
        for n in range(6):
            t = n / 6
            y = height - (height - top_y) * (t + 0.08)
            x = x0 + (x1 - x0) * t
            size = 70 - n * 6
            leaf(ctx, x, y, size, -0.6 - rng.uniform(0, 0.4), ink if n % 2 else accent)
            leaf(ctx, x, y, size, math.pi + 0.6 + rng.uniform(0, 0.4), accent if n % 2 else ink)
        set_color(ctx, accent)
        ctx.arc(x1, top_y - 6, 12, 0, 2 * math.pi)
        ctx.fill()
    standard_text(ctx, book, ink, title_y=70, title_font='Noto Serif Display Italic',
                  title_size=72, author_y='below', author_font='Noto Sans')


def cover_sun(ctx, book, colors, rng):
    top, bottom, ink, accent = colors
    width, height = COVER_SIZE
    ctx.set_source(vertical_gradient(ctx, top, bottom, 0, 560))
    ctx.paint()
    set_color(ctx, accent, 0.95)
    ctx.arc(width / 2, 560, 170, math.pi, 2 * math.pi)
    ctx.fill()
    # The sea: bands with the sun's reflection broken across them.
    set_color(ctx, ink)
    ctx.rectangle(0, 560, width, height - 560)
    ctx.fill()
    for band in range(14):
        y = 572 + band * 22
        w = 300 - band * 18
        if w <= 10:
            break
        set_color(ctx, accent, 0.8 - band * 0.05)
        ctx.rectangle(width / 2 - w / 2 + rng.uniform(-10, 10), y, w, 6)
        ctx.fill()
    standard_text(ctx, book, ink, title_y=80, title_font='Noto Serif Display Bold',
                  title_size=70, accent=ink, author=False)
    author = layout_text(ctx, ' & '.join(book['authors']).upper(), 'Noto Sans Medium', 24,
                         520, spacing=4)
    draw_text(ctx, author, None, height - 80, accent)


def cover_bauhaus(ctx, book, colors, rng):
    top, bottom, ink, accent = colors
    width, height = COVER_SIZE
    set_color(ctx, top)
    ctx.paint()
    cell = 150
    choices = [accent, ink, '#f6d32d', '#1c71d8', '#e01b24', top]
    for row in range(4):
        for column in range(4):
            x, y = column * cell, row * cell
            color = rng.choice(choices)
            shape = rng.randrange(4)
            ctx.save()
            ctx.rectangle(x, y, cell, cell)
            ctx.clip()
            set_color(ctx, color)
            if shape == 0:
                ctx.rectangle(x, y, cell, cell)
            elif shape == 1:
                ctx.arc(x + cell / 2, y + cell / 2, cell / 2 - 6, 0, 2 * math.pi)
            elif shape == 2:
                corner = rng.randrange(4)
                cx = x + (cell if corner in (1, 2) else 0)
                cy = y + (cell if corner in (2, 3) else 0)
                ctx.move_to(cx, cy)
                ctx.arc(cx, cy, cell, corner * math.pi / 2, corner * math.pi / 2 + math.pi / 2)
                ctx.close_path()
            else:
                ctx.rectangle(x, y + cell / 2 - 10, cell, 20)
            ctx.fill()
            ctx.restore()
    set_color(ctx, top)
    ctx.rectangle(0, 600, width, 300)
    ctx.fill()
    set_color(ctx, accent)
    ctx.rectangle(50, 630, 60, 8)
    ctx.fill()
    title = fitted(ctx, book['title'], 'Noto Sans Bold', 46, 500, max_lines=3, align='left',
                   line_spacing=0.95)
    bottom = 655 + draw_text(ctx, title, 50, 655, ink)
    author = layout_text(ctx, ' & '.join(book['authors']), 'Noto Sans', 24, 500, align='left')
    draw_text(ctx, author, 50, min(bottom + 18, height - 60), ink, 0.8)


def cover_type(ctx, book, colors, rng):
    top, bottom, ink, accent = colors
    width, height = COVER_SIZE
    set_color(ctx, top)
    ctx.paint()
    set_color(ctx, accent)
    ctx.rectangle(0, 0, width, 22)
    ctx.fill()
    ctx.rectangle(0, height - 22, width, 22)
    ctx.fill()
    # A big ampersand or initial, faint, behind the title.
    initial = layout_text(ctx, book['title'][0], 'Noto Serif Display Black', 620, 600)
    draw_text(ctx, initial, None, 40, accent, 0.12)
    title = fitted(ctx, book['title'], 'Noto Serif Display SemiBold', 80, 470, max_lines=5)
    _w, h = title.get_pixel_size()
    y = 400 - h / 2
    draw_text(ctx, title, None, y, ink)
    set_color(ctx, accent)
    ctx.rectangle(width / 2 - 40, y + h + 30, 80, 3)
    ctx.fill()
    author = layout_text(ctx, ' & '.join(book['authors']).upper(), 'Noto Sans Medium', 22,
                         500, spacing=5)
    draw_text(ctx, author, None, y + h + 60, ink)
    publisher = layout_text(ctx, book['publisher'].upper(), 'Noto Sans SemiBold', 14, 500,
                            spacing=4)
    draw_text(ctx, publisher, None, height - 80, ink, 0.6)


def cover_cloth(ctx, book, colors, rng):
    top, bottom, ink, accent = colors
    width, height = COVER_SIZE
    ctx.set_source(vertical_gradient(ctx, top, bottom))
    ctx.paint()
    # The weave of book cloth, then a gilt double frame with corner pieces.
    for y in range(0, height, 3):
        ctx.set_source_rgba(0, 0, 0, rng.uniform(0.02, 0.08))
        ctx.rectangle(0, y, width, 1)
        ctx.fill()
    for x in range(0, width, 3):
        ctx.set_source_rgba(1, 1, 1, rng.uniform(0.0, 0.03))
        ctx.rectangle(x, 0, 1, height)
        ctx.fill()
    set_color(ctx, accent)
    ctx.set_line_width(3)
    ctx.rectangle(36, 36, width - 72, height - 72)
    ctx.stroke()
    ctx.set_line_width(1.2)
    ctx.rectangle(48, 48, width - 96, height - 96)
    ctx.stroke()
    for cx, cy in ((48, 48), (width - 48, 48), (48, height - 48), (width - 48, height - 48)):
        ctx.save()
        ctx.translate(cx, cy)
        ctx.rotate(math.pi / 4)
        ctx.rectangle(-9, -9, 18, 18)
        ctx.restore()
        ctx.fill()
    title = fitted(ctx, book['title'].upper(), 'Noto Serif Display SemiBold', 58, 440,
                   spacing=3)
    _w, h = title.get_pixel_size()
    y = 330 - h / 2
    draw_text(ctx, title, None, y, accent)
    # A small fleuron of two leaves and a lozenge.
    oy = y + h + 50
    leaf(ctx, width / 2 - 8, oy, 60, math.pi, accent)
    leaf(ctx, width / 2 + 8, oy, 60, 0, accent)
    ctx.save()
    ctx.translate(width / 2, oy)
    ctx.rotate(math.pi / 4)
    ctx.rectangle(-7, -7, 14, 14)
    ctx.restore()
    ctx.fill()
    author = layout_text(ctx, ' & '.join(book['authors']).upper(), 'Noto Serif Medium', 24,
                         440, spacing=5)
    draw_text(ctx, author, None, oy + 50, accent)
    imprint = layout_text(ctx, book['publisher'].upper(), 'Noto Serif', 13, 440, spacing=4)
    draw_text(ctx, imprint, None, height - 100, accent, 0.8)


def cover_flock(ctx, book, colors, rng):
    top, bottom, ink, accent = colors
    width, height = COVER_SIZE
    ctx.set_source(vertical_gradient(ctx, top, bottom))
    ctx.paint()
    # A murmuration: small birds dense along a curve, thinning out at its ends.
    for _ in range(900):
        t = rng.random()
        spread = 60 * math.sin(t * math.pi) + 10
        x = 60 + t * 480 + rng.gauss(0, spread * 0.6)
        y = 520 + 100 * math.sin(t * 2 * math.pi) + rng.gauss(0, spread)
        size = rng.uniform(2, 4.5)
        set_color(ctx, ink, rng.uniform(0.5, 0.95))
        ctx.move_to(x - size, y - size * 0.4)
        ctx.line_to(x, y)
        ctx.line_to(x + size, y - size * 0.4)
        ctx.set_line_width(1.4)
        ctx.stroke()
    hill_layers(ctx, (top, bottom, ink, accent), rng, count=2, start=760)
    standard_text(ctx, book, ink, title_y=80, title_font='Noto Serif Display Italic',
                  title_size=70, author_y='below')


def cover_planet(ctx, book, colors, rng):
    top, bottom, ink, accent = colors
    width, height = COVER_SIZE
    ctx.set_source(vertical_gradient(ctx, top, bottom))
    ctx.paint()
    sun = cairo.RadialGradient(300, 470, 20, 300, 470, 260)
    sun.add_color_stop_rgba(0, *rgb('#fff3c4'), 1)
    sun.add_color_stop_rgba(0.35, *rgb(accent), 0.95)
    sun.add_color_stop_rgba(1, *rgb(accent), 0)
    ctx.set_source(sun)
    ctx.paint()
    set_color(ctx, '#120c06')
    ctx.arc(300, 470, 120, 0, 2 * math.pi)
    ctx.fill()
    for _ in range(140):
        set_color(ctx, ink, rng.uniform(0.2, 0.7))
        ctx.arc(rng.uniform(0, width), rng.uniform(0, height), rng.uniform(0.5, 1.6), 0,
                2 * math.pi)
        ctx.fill()
    title = fitted(ctx, book['title'].upper(), 'Noto Sans Bold', 56, 520, spacing=6)
    draw_text(ctx, title, None, 680, ink)
    author = layout_text(ctx, ' & '.join(book['authors']).upper(), 'Noto Sans', 22, 500,
                         spacing=6)
    draw_text(ctx, author, None, 90, ink, 0.85)


def cover_clouds(ctx, book, colors, rng):
    top, bottom, ink, accent = colors
    width, height = COVER_SIZE
    ctx.set_source(vertical_gradient(ctx, top, bottom))
    ctx.paint()
    for _ in range(5):
        cx, cy = rng.uniform(40, width - 40), rng.uniform(380, 820)
        for _ in range(9):
            set_color(ctx, accent, 0.6)
            ctx.arc(cx + rng.uniform(-90, 90), cy + rng.uniform(-20, 20),
                    rng.uniform(30, 60), 0, 2 * math.pi)
            ctx.fill()
    title = fitted(ctx, book['title'], 'Noto Serif Display Bold', 70, 500)
    draw_text(ctx, title, None, 70, ink)
    author = layout_text(ctx, ' & '.join(book['authors']).upper(), 'Noto Sans Medium', 24,
                         500, spacing=4)
    draw_text(ctx, author, None, height - 90, ink)


def cover_orbits(ctx, book, colors, rng):
    top, bottom, ink, accent = colors
    width, height = COVER_SIZE
    ctx.set_source(vertical_gradient(ctx, top, bottom))
    ctx.paint()
    cx, cy = width / 2, 520
    set_color(ctx, ink, 0.5)
    ctx.set_line_width(1.5)
    for n in range(6):
        ctx.save()
        ctx.translate(cx, cy)
        ctx.rotate(n * math.pi / 6)
        ctx.scale(1, 0.35)
        ctx.arc(0, 0, 210, 0, 2 * math.pi)
        ctx.restore()
        ctx.stroke()
    for n in range(6):
        angle = rng.uniform(0, 2 * math.pi)
        ctx.save()
        ctx.translate(cx, cy)
        ctx.rotate(n * math.pi / 6)
        x, y = 210 * math.cos(angle), 210 * 0.35 * math.sin(angle)
        ctx.restore()
        rx = cx + x * math.cos(n * math.pi / 6) - y * math.sin(n * math.pi / 6)
        ry = cy + x * math.sin(n * math.pi / 6) + y * math.cos(n * math.pi / 6)
        set_color(ctx, accent)
        ctx.arc(rx, ry, 7, 0, 2 * math.pi)
        ctx.fill()
    set_color(ctx, accent)
    ctx.arc(cx, cy, 34, 0, 2 * math.pi)
    ctx.fill()
    title = fitted(ctx, book['title'].upper(), 'Noto Sans Bold', 50, 500, spacing=4)
    draw_text(ctx, title, None, 80, ink)
    author = layout_text(ctx, ' & '.join(book['authors']).upper(), 'Noto Sans', 22, 500,
                         spacing=6)
    draw_text(ctx, author, None, height - 90, accent)


def cover_river(ctx, book, colors, rng):
    top, bottom, ink, accent = colors
    width, height = COVER_SIZE
    ctx.set_source(vertical_gradient(ctx, top, bottom))
    ctx.paint()
    # A meandering river seen from above, drawn as a few parallel strokes.
    for offset, alpha, line in ((0, 1, 34), (-30, 0.35, 3), (30, 0.35, 3)):
        set_color(ctx, accent, alpha)
        ctx.set_line_width(line)
        ctx.move_to(120 + offset, 360)
        ctx.curve_to(520 + offset, 420, 80 + offset, 560, 420 + offset, 640)
        ctx.curve_to(620 + offset, 690, 640 + offset, 760, 640 + offset, 920)
        ctx.stroke()
    standard_text(ctx, book, ink, title_y=80, title_font='Noto Serif Display SemiBold',
                  title_size=74, author_y=height - 90)


COVER_STYLES = {
    'waves': cover_waves, 'window': cover_window, 'stars': cover_stars,
    'compass': cover_compass, 'hills': cover_hills, 'deco': cover_deco,
    'botanical': cover_botanical, 'sun': cover_sun, 'bauhaus': cover_bauhaus,
    'type': cover_type, 'cloth': cover_cloth, 'flock': cover_flock, 'planet': cover_planet,
    'clouds': cover_clouds, 'orbits': cover_orbits, 'river': cover_river,
}


def draw_cover(book):
    """The cover as a Cairo surface."""
    surface = cairo.ImageSurface(cairo.FORMAT_RGB24, *COVER_SIZE)
    ctx = cairo.Context(surface)
    rng = random.Random(seed_of('cover:' + book['title']))
    style = book['style']
    if style == 'comic':
        comic_cover(ctx, book, PALETTES['comic'][0], rng)
    else:
        palettes = PALETTES[style]
        COVER_STYLES[style](ctx, book, palettes[book.get('palette', 0) % len(palettes)], rng)
    # The light catching the spine side of the cover.
    spine = cairo.LinearGradient(0, 0, 26, 0)
    spine.add_color_stop_rgba(0, 0, 0, 0, 0.28)
    spine.add_color_stop_rgba(0.35, 1, 1, 1, 0.12)
    spine.add_color_stop_rgba(1, 1, 1, 1, 0)
    ctx.set_source(spine)
    ctx.rectangle(0, 0, 26, COVER_SIZE[1])
    ctx.fill()
    surface.flush()
    return surface


def jpeg_bytes(surface, quality=90):
    """A Cairo surface as JPEG bytes, through GdkPixbuf."""
    png = io.BytesIO()
    surface.write_to_png(png)
    loader = GdkPixbuf.PixbufLoader.new_with_type('png')
    loader.write(png.getvalue())
    loader.close()
    ok, data = loader.get_pixbuf().save_to_bufferv('jpeg', ['quality'], [str(quality)])
    return bytes(data)


# ---------------------------------------------------------------------------------------
# The comic: drawn panels, a lamplighter, a moth.

COMIC_SIZE = (800, 1200)
COMIC_LINES = [
    ['Every night, the same round.', 'Forty-two lamps.', 'And one moth.'],
    ['You again?', '...', 'Fine. Come on, then.'],
    ["The lamps on Tanner's Row have gone out.", 'All of them?', 'All of them.'],
    ['Something is eating the light.', 'Not something.', 'Someone.'],
    ['Stay close.', 'The dark is thicker here.', 'TO BE CONTINUED'],
]


def comic_scene(ctx, x, y, w, h, rng, colors, lamp=True, moth=True, lamp_x=None,
                moth_at=None):
    top, bottom, ink, accent = colors
    sky = cairo.LinearGradient(0, y, 0, y + h)
    sky.add_color_stop_rgb(0, *rgb(top))
    sky.add_color_stop_rgb(1, *rgb(bottom))
    ctx.set_source(sky)
    ctx.rectangle(x, y, w, h)
    ctx.fill()
    ctx.save()
    ctx.rectangle(x, y, w, h)
    ctx.clip()
    # Rooftops.
    set_color(ctx, '#0b0b18', 0.9)
    rx = x - 10
    while rx < x + w:
        rw = rng.uniform(50, 110)
        rh = rng.uniform(h * 0.25, h * 0.5)
        ctx.rectangle(rx, y + h - rh, rw, rh)
        ctx.move_to(rx, y + h - rh)
        ctx.line_to(rx + rw / 2, y + h - rh - rng.uniform(15, 40))
        ctx.line_to(rx + rw, y + h - rh)
        ctx.fill()
        rx += rw
    if lamp:
        lx = x + (lamp_x if lamp_x is not None else rng.uniform(0.2, 0.8)) * w
        glow = cairo.RadialGradient(lx, y + h * 0.35, 4, lx, y + h * 0.35, h * 0.45)
        glow.add_color_stop_rgba(0, *rgb(accent), 0.9)
        glow.add_color_stop_rgba(1, *rgb(accent), 0)
        ctx.set_source(glow)
        ctx.paint()
        set_color(ctx, '#0b0b18')
        ctx.rectangle(lx - 3, y + h * 0.38, 6, h)
        ctx.fill()
        set_color(ctx, accent)
        ctx.rectangle(lx - 10, y + h * 0.3, 20, 22)
        ctx.fill()
    if moth:
        mx, my = moth_at or (rng.uniform(0.2, 0.8), rng.uniform(0.15, 0.5))
        mx, my = x + mx * w, y + my * h
        size = min(w, h) * 0.09
        set_color(ctx, ink, 0.95)
        for side in (-1, 1):
            ctx.move_to(mx, my)
            ctx.curve_to(mx + side * size * 1.6, my - size * 1.4, mx + side * size * 2.2,
                         my + size * 0.2, mx, my + size * 0.4)
            ctx.fill()
        set_color(ctx, '#0b0b18')
        ctx.rectangle(mx - 2, my - size * 0.3, 4, size)
        ctx.fill()
    ctx.restore()
    set_color(ctx, '#000000')
    ctx.set_line_width(6)
    ctx.rectangle(x, y, w, h)
    ctx.stroke()


def balloon(ctx, text, x, y, width):
    layout = layout_text(ctx, text, 'Noto Sans Bold', 24, width - 30)
    tw, th = layout.get_pixel_size()
    w, h = width, th + 26
    ctx.save()
    ctx.translate(x + w / 2, y + h / 2)
    ctx.scale(w / 2, h / 2)
    ctx.arc(0, 0, 1, 0, 2 * math.pi)
    ctx.restore()
    set_color(ctx, '#ffffff')
    ctx.fill_preserve()
    set_color(ctx, '#000000')
    ctx.set_line_width(3)
    ctx.stroke()
    set_color(ctx, '#000000')
    ctx.move_to(x + 15, y + 13)
    PangoCairo.show_layout(ctx, layout)


def comic_page(number, colors):
    width, height = COMIC_SIZE
    surface = cairo.ImageSurface(cairo.FORMAT_RGB24, width, height)
    ctx = cairo.Context(surface)
    rng = random.Random(seed_of(f'comic page {number}'))
    set_color(ctx, '#fdf6e3')
    ctx.paint()
    lines = COMIC_LINES[number % len(COMIC_LINES)]
    margin, gutter = 40, 24
    panels = [(margin, margin, width - 2 * margin, 460),
              (margin, margin + 460 + gutter, (width - 2 * margin - gutter) / 2, 320),
              (margin + (width - 2 * margin + gutter) / 2, margin + 460 + gutter,
               (width - 2 * margin - gutter) / 2, 320),
              (margin, margin + 780 + 2 * gutter, width - 2 * margin, height - 2 * margin
               - 780 - 2 * gutter)]
    for index, (x, y, w, h) in enumerate(panels):
        comic_scene(ctx, x, y, w, h, rng, colors, lamp=index != 1, moth=index != 2)
        if index < len(lines):
            balloon(ctx, lines[index], x + 20, y + 20, min(w - 40, 300))
    surface.flush()
    return surface


def comic_cover(ctx, book, colors, rng):
    width, height = COVER_SIZE
    comic_scene(ctx, 0, 0, width, height, rng, colors, lamp_x=0.72, moth_at=(0.35, 0.52))
    top, bottom, ink, accent = colors
    shade = cairo.LinearGradient(0, 0, 0, 320)
    shade.add_color_stop_rgba(0, 0, 0, 0.1, 0.75)
    shade.add_color_stop_rgba(1, 0, 0, 0.1, 0)
    ctx.set_source(shade)
    ctx.rectangle(0, 0, width, 320)
    ctx.fill()
    title = fitted(ctx, book['title'].split(',')[0].upper(), 'Noto Sans Black', 72, 520,
                   spacing=2)
    set_color(ctx, '#000000', 0.6)
    ctx.move_to((width - 520) / 2 + 4, 64)
    PangoCairo.show_layout(ctx, title)
    draw_text(ctx, title, None, 60, accent)
    volume = layout_text(ctx, 'VOLUME ONE', 'Noto Sans Bold', 22, 520, spacing=6)
    draw_text(ctx, volume, None, 60 + title.get_pixel_size()[1] + 16, ink)
    author = layout_text(ctx, ' & '.join(book['authors']).upper(), 'Noto Sans Bold', 22, 520,
                         spacing=4)
    draw_text(ctx, author, None, height - 80, ink)


# ---------------------------------------------------------------------------------------
# Writing the files.

def book_uuid(book):
    return str(uuid.uuid5(uuid.NAMESPACE_URL, 'bookcase-demo:' + book['title']))


def zip_write(archive, name, data, stored=False):
    info = zipfile.ZipInfo(name, ZIP_TIME)
    info.compress_type = zipfile.ZIP_STORED if stored else zipfile.ZIP_DEFLATED
    info.external_attr = 0o644 << 16
    archive.writestr(info, data)


STYLESHEET = """body { font-family: serif; line-height: 1.5; margin: 0 5%; }
h1 { font-weight: normal; text-align: center; margin: 3em 0 2em; font-size: 1.6em; }
h1 .number { display: block; font-size: 0.6em; letter-spacing: 0.2em; text-transform: uppercase;
  margin-bottom: 0.6em; }
p { margin: 0; text-indent: 1.5em; text-align: justify; }
h1 + p { text-indent: 0; }
h1 + p::first-line { font-variant: small-caps; letter-spacing: 0.05em; }
.title-page { text-align: center; margin-top: 30%; }
.title-page h1 { margin: 0 0 1em; font-size: 2em; }
.title-page p { text-indent: 0; text-align: center; }
.cover { text-align: center; margin: 0; padding: 0; }
.cover img { max-width: 100%; max-height: 100vh; }
"""


def xhtml(title, body, language):
    return ('<?xml version="1.0" encoding="UTF-8"?>\n<!DOCTYPE html>\n'
            f'<html xmlns="http://www.w3.org/1999/xhtml" xmlns:epub="http://www.idpf.org/2007/'
            f'ops" xml:lang="{language}" lang="{language}"><head><meta charset="UTF-8"/>'
            f'<title>{escape(title)}</title>'
            '<link rel="stylesheet" type="text/css" href="style.css"/></head>'
            f'<body>{body}</body></html>\n')


def chapter_body(number, title, paragraphs):
    """A chapter's body: the heading at body child 1 (/4/2 in a CFI), then the paragraphs
    (paragraph n at /4/{2 n + 2})."""
    heading = (f'<h1 id="chapter-{number}"><span class="number">{number}</span>'
               f'{escape(title)}</h1>')
    return heading + ''.join(f'<p>{escape(p)}</p>' for p in paragraphs)


def write_epub(path, book, cover, chapters):
    language = book.get('language', 'en')
    identifier = book_uuid(book)
    authors = book['authors']
    meta = [f'<dc:identifier id="uid">urn:uuid:{identifier}</dc:identifier>',
            f'<dc:title>{escape(book["title"])}</dc:title>',
            f'<dc:language>{language}</dc:language>',
            '<meta property="dcterms:modified">2026-01-01T00:00:00Z</meta>']
    for number, author in enumerate(authors):
        meta.append(f'<dc:creator id="creator{number}">{escape(author)}</dc:creator>')
        meta.append(f'<meta refines="#creator{number}" property="role" '
                    'scheme="marc:relators">aut</meta>')
        last = author.split()[-1]
        rest = ' '.join(author.split()[:-1])
        meta.append(f'<meta refines="#creator{number}" property="file-as">'
                    f'{escape(last)}, {escape(rest)}</meta>')
    meta.append(f'<dc:publisher>{escape(book["publisher"])}</dc:publisher>')
    meta.append(f'<dc:date>{book["published"]}</dc:date>')
    meta.append(f'<dc:description>{escape(book["blurb"])}</dc:description>')
    for tag in book['tags']:
        meta.append(f'<dc:subject>{escape(tag)}</dc:subject>')
    if book.get('series'):
        meta.append(f'<meta name="calibre:series" content="{escape(book["series"])}"/>')
        meta.append(f'<meta name="calibre:series_index" content="{book["index"]}"/>')
        meta.append(f'<meta property="belongs-to-collection" id="c1">'
                    f'{escape(book["series"])}</meta>')
        meta.append('<meta refines="#c1" property="collection-type">series</meta>')
        meta.append(f'<meta refines="#c1" property="group-position">{book["index"]}</meta>')
    meta.append('<meta name="cover" content="cover-image"/>')

    manifest = ['<item id="nav" href="nav.xhtml" media-type="application/xhtml+xml" '
                'properties="nav"/>',
                '<item id="ncx" href="toc.ncx" media-type="application/x-dtbncx+xml"/>',
                '<item id="css" href="style.css" media-type="text/css"/>',
                '<item id="cover-image" href="cover.jpg" media-type="image/jpeg" '
                'properties="cover-image"/>',
                '<item id="cover" href="cover.xhtml" media-type="application/xhtml+xml"/>',
                '<item id="title" href="title.xhtml" media-type="application/xhtml+xml"/>']
    spine = ['<itemref idref="cover" linear="no"/>', '<itemref idref="title"/>']
    for number in range(1, len(chapters) + 1):
        manifest.append(f'<item id="c{number}" href="chapter{number}.xhtml" '
                        'media-type="application/xhtml+xml"/>')
        spine.append(f'<itemref idref="c{number}"/>')
    opf = ('<?xml version="1.0" encoding="UTF-8"?>\n'
           '<package xmlns="http://www.idpf.org/2007/opf" version="3.0" '
           f'unique-identifier="uid" xml:lang="{language}">\n'
           '<metadata xmlns:dc="http://purl.org/dc/elements/1.1/">\n' + '\n'.join(meta)
           + '\n</metadata>\n<manifest>\n' + '\n'.join(manifest)
           + '\n</manifest>\n<spine toc="ncx">\n' + '\n'.join(spine)
           + '\n</spine>\n</package>\n')

    items = ''.join(f'<li><a href="chapter{n}.xhtml">{escape(title)}</a></li>'
                    for n, (title, _p) in enumerate(chapters, 1))
    nav = xhtml('Contents', f'<nav epub:type="toc" id="toc"><h1>Contents</h1><ol>{items}</ol>'
                '</nav>', language)
    points = ''.join(f'<navPoint id="p{n}" playOrder="{n}"><navLabel><text>{escape(title)}'
                     f'</text></navLabel><content src="chapter{n}.xhtml"/></navPoint>'
                     for n, (title, _p) in enumerate(chapters, 1))
    ncx = ('<?xml version="1.0" encoding="UTF-8"?>\n'
           '<ncx xmlns="http://www.daisy.org/z3986/2005/ncx/" version="2005-1"><head>'
           f'<meta name="dtb:uid" content="urn:uuid:{identifier}"/></head>'
           f'<docTitle><text>{escape(book["title"])}</text></docTitle>'
           f'<navMap>{points}</navMap></ncx>\n')
    title_page = xhtml(book['title'], '<div class="title-page">'
                       f'<h1>{escape(book["title"])}</h1>'
                       f'<p>{escape(" & ".join(authors))}</p>'
                       f'<p><small>{escape(book["publisher"])}</small></p></div>', language)
    cover_page = xhtml('Cover', '<div class="cover"><img src="cover.jpg" alt="Cover"/></div>',
                       language)

    with zipfile.ZipFile(path, 'w') as archive:
        zip_write(archive, 'mimetype', 'application/epub+zip', stored=True)
        zip_write(archive, 'META-INF/container.xml',
                  '<?xml version="1.0" encoding="UTF-8"?>\n<container version="1.0" '
                  'xmlns="urn:oasis:names:tc:opendocument:xmlns:container"><rootfiles>'
                  '<rootfile full-path="OEBPS/content.opf" '
                  'media-type="application/oebps-package+xml"/></rootfiles></container>\n')
        zip_write(archive, 'OEBPS/content.opf', opf)
        zip_write(archive, 'OEBPS/nav.xhtml', nav)
        zip_write(archive, 'OEBPS/toc.ncx', ncx)
        zip_write(archive, 'OEBPS/style.css', STYLESHEET)
        zip_write(archive, 'OEBPS/cover.jpg', cover)
        zip_write(archive, 'OEBPS/cover.xhtml', cover_page)
        zip_write(archive, 'OEBPS/title.xhtml', title_page)
        for number, (title, paragraphs) in enumerate(chapters, 1):
            zip_write(archive, f'OEBPS/chapter{number}.xhtml',
                      xhtml(title, chapter_body(number, title, paragraphs), language))


def write_pdf(path, book, cover_surface, chapters):
    """An A5 PDF set like a small printed book: the cover; a contents page whose entries
    link to the chapters; each chapter on a new page, its number and title, its paragraphs
    justified and indented, broken across pages line by line, a running head and a folio;
    a drawn figure with a caption in the first and third chapters; and an outline (the
    reader's contents)."""
    width, height = 420, 595
    margin, top, bottom = 52, 64, 58
    text_width = width - 2 * margin
    ink, grey, accent = '#1d1d1d', '#77767b', '#c64600'
    surface = cairo.PDFSurface(str(path), width, height)
    surface.set_metadata(cairo.PDFMetadata.TITLE, book['title'])
    surface.set_metadata(cairo.PDFMetadata.AUTHOR, ' & '.join(book['authors']))
    surface.set_metadata(cairo.PDFMetadata.SUBJECT, book['blurb'])
    surface.set_metadata(cairo.PDFMetadata.KEYWORDS, ', '.join(book['tags']))
    surface.set_metadata(cairo.PDFMetadata.CREATE_DATE, '2013-06-06T00:00:00')
    ctx = cairo.Context(surface)
    ctx.save()
    ctx.scale(width / COVER_SIZE[0], height / COVER_SIZE[1])
    ctx.set_source_surface(cover_surface)
    ctx.paint()
    ctx.restore()
    ctx.show_page()

    # Where each chapter starts: the cover, the contents, then four pages or so a chapter.
    # Laid out once without drawing to know the pages, then drawn.
    def set_chapters(draw):
        page = 3
        starts = []
        state = {'page': page, 'y': top}

        def new_page(running):
            if draw:
                folio(state['page'], running, opening=state['page'] == starts[-1])
                ctx.show_page()
            state['page'] += 1
            state['y'] = top

        for number, (title, paragraphs) in enumerate(chapters, 1):
            starts.append(state['page'])
            if draw:
                surface.add_outline(cairo.PDF_OUTLINE_ROOT, title, f'page={state["page"]}', 0)
            y = 128
            label = layout_text(ctx, f'CHAPTER {number}', 'Noto Sans', 8.5, text_width,
                                spacing=2.2)
            heading = layout_text(ctx, title, 'Noto Serif Display SemiBold', 26, text_width)
            if draw:
                set_color(ctx, accent)
                ctx.move_to(margin, y)
                PangoCairo.show_layout(ctx, label)
                set_color(ctx, ink)
                ctx.move_to(margin, y + 18)
                PangoCairo.show_layout(ctx, heading)
            state['y'] = y + 18 + heading.get_pixel_size()[1] + 34
            for index, paragraph in enumerate(paragraphs):
                if index == 3 and number in (1, 3):
                    figure(state, number, title, new_page, draw)
                layout = layout_text(ctx, paragraph, 'Noto Serif', 10.5, text_width,
                                     align='left', line_spacing=1.38)
                layout.set_justify(True)
                if index:
                    layout.set_indent(int(15 * Pango.SCALE))
                lines = layout.get_iter()
                while True:
                    ink_rect, logical = lines.get_line_extents()
                    line_height = logical.height / Pango.SCALE * 1.38
                    if state['y'] + line_height > height - bottom:
                        new_page(title)
                    if draw:
                        set_color(ctx, ink)
                        ctx.move_to(margin + logical.x / Pango.SCALE,
                                    state['y'] + lines.get_baseline() / Pango.SCALE
                                    - logical.y / Pango.SCALE)
                        PangoCairo.show_layout_line(ctx, lines.get_line_readonly())
                    state['y'] += line_height
                    if not lines.next_line():
                        break
                state['y'] += 2
            new_page(title)
        return starts

    def folio(page, running, opening=False):
        number = layout_text(ctx, str(page), 'Noto Serif', 9, text_width)
        set_color(ctx, grey)
        ctx.move_to(margin, height - 38)
        PangoCairo.show_layout(ctx, number)
        if opening:
            return  # no running head over a chapter's title
        head = layout_text(ctx, (book['title'] if page % 2 == 0 else running).upper(),
                           'Noto Sans', 7, text_width, spacing=1.6)
        set_color(ctx, grey)
        ctx.move_to(margin, 30)
        PangoCairo.show_layout(ctx, head)

    def figure(state, number, title, new_page, draw):
        box = 150
        if state['y'] + box + 40 > height - bottom:
            new_page(title)
        y = state['y'] + 6
        if draw:
            if number == 1:  # a line of type in the composing stick
                set_color(ctx, '#e8e2d6')
                ctx.rectangle(margin, y, text_width, box)
                ctx.fill()
                set_color(ctx, '#5e5c64')
                ctx.rectangle(margin + 18, y + 56, text_width - 36, 46)
                ctx.set_line_width(3)
                ctx.stroke()
                for n, letter in enumerate('LETTERPRESS'):
                    x = margin + 30 + n * ((text_width - 60) / 11)
                    set_color(ctx, '#3d3846' if n % 2 else '#241f31')
                    ctx.rectangle(x, y + 62, (text_width - 60) / 11 - 3, 34)
                    ctx.fill()
                    glyph = layout_text(ctx, letter, 'Noto Serif Display SemiBold', 18, 20)
                    set_color(ctx, '#f6f5f4')
                    ctx.move_to(x - 3, y + 66)
                    PangoCairo.show_layout(ctx, glyph)
                caption_text = 'Figure 1. A line of type set in the composing stick, read ' \
                               'upside down and backwards by the compositor.'
            else:  # the forme locked up in its chase
                set_color(ctx, '#e8e2d6')
                ctx.rectangle(margin, y, text_width, box)
                ctx.fill()
                set_color(ctx, '#5e5c64')
                ctx.set_line_width(6)
                ctx.rectangle(margin + 70, y + 14, text_width - 140, box - 28)
                ctx.stroke()
                for row in range(7):
                    set_color(ctx, '#3d3846', 0.85 if row % 3 else 0.55)
                    ctx.rectangle(margin + 88, y + 30 + row * 14, text_width - 176 - (
                        30 if row == 6 else 0), 8)
                    ctx.fill()
                set_color(ctx, accent)
                for x in (margin + 82, width - margin - 92):
                    ctx.rectangle(x, y + box - 34, 10, 14)
                    ctx.fill()
                caption_text = 'Figure 2. The forme locked up in its chase, the quoins (in ' \
                               'orange) tightened against the furniture.'
            caption = layout_text(ctx, caption_text, 'Noto Sans Italic', 8.5, text_width,
                                  align='left', line_spacing=1.2)
            set_color(ctx, grey)
            ctx.move_to(margin, y + box + 8)
            PangoCairo.show_layout(ctx, caption)
        state['y'] = y + box + 48

    starts = set_chapters(draw=False)
    # The contents, each entry a link to its chapter.
    heading = layout_text(ctx, 'Contents', 'Noto Serif Display SemiBold', 22, text_width)
    set_color(ctx, ink)
    ctx.move_to(margin, 128)
    PangoCairo.show_layout(ctx, heading)
    surface.add_outline(cairo.PDF_OUTLINE_ROOT, 'Contents', 'page=2', 0)
    y = 190
    for number, ((title, _paragraphs), page) in enumerate(zip(chapters, starts, strict=True), 1):
        entry = layout_text(ctx, f'{number}.  {title}', 'Noto Serif', 12, text_width - 40,
                            align='left')
        folio_layout = layout_text(ctx, str(page), 'Noto Serif', 12, 40, align='right')
        ctx.tag_begin(cairo.TAG_LINK, f'page={page}')
        set_color(ctx, ink)
        ctx.move_to(margin, y)
        PangoCairo.show_layout(ctx, entry)
        set_color(ctx, grey)
        ctx.move_to(width - margin - 40, y)
        PangoCairo.show_layout(ctx, folio_layout)
        ctx.tag_end(cairo.TAG_LINK)
        y += 30
    ctx.show_page()
    set_chapters(draw=True)
    surface.finish()


def write_cbz(path, book, cover_surface):
    colors = PALETTES['comic'][0]
    with zipfile.ZipFile(path, 'w') as archive:
        zip_write(archive, '000.jpg', jpeg_bytes(cover_surface), stored=True)
        for number in range(1, 9):
            zip_write(archive, f'{number:03d}.jpg', jpeg_bytes(comic_page(number, colors)),
                      stored=True)
        info = ('<?xml version="1.0" encoding="utf-8"?>\n<ComicInfo>'
                f'<Title>{escape(book["title"])}</Title>'
                f'<Series>{escape(book["series"])}</Series><Number>{book["index"]}</Number>'
                f'<Writer>{escape(", ".join(book["authors"]))}</Writer>'
                f'<Publisher>{escape(book["publisher"])}</Publisher>'
                f'<Year>{book["published"][:4]}</Year><Genre>{escape(", ".join(book["tags"]))}'
                f'</Genre><Summary>{escape(book["blurb"])}</Summary>'
                '<LanguageISO>en</LanguageISO></ComicInfo>\n')
        zip_write(archive, 'ComicInfo.xml', info)


def file_name(book):
    fmt = book.get('format', 'epub')
    safe = ''.join(c for c in book['title'] if c not in '/\\:*?"<>|')
    return f'{book["authors"][0]} - {safe}.{fmt}'


def write_book(book, directory):
    """Write one book's file into `directory`; returns (path, chapters)."""
    rng = random.Random(seed_of('text:' + book['title']))
    surface = draw_cover(book)
    path = pathlib.Path(directory) / file_name(book)
    fmt = book.get('format', 'epub')
    chapters = []
    if fmt == 'cbz':
        write_cbz(path, book, surface)
    elif fmt == 'pdf':
        chapters = chapters_of(book, rng)
        write_pdf(path, book, surface, chapters)
    else:
        chapters = chapters_of(book, rng)
        write_epub(path, book, jpeg_bytes(surface), chapters)
    return path, chapters


# ---------------------------------------------------------------------------------------
# The library.

def text_cfi(chapter, paragraph, length):
    """A CFI range over the first `length` characters of a paragraph: the spine holds the
    cover page and the title page before chapter 1."""
    spine = 2 * (chapter + 2)
    step = 2 * paragraph + 2
    return f'epubcfi(/6/{spine}!/4/{step},/1:0,/1:{length})'


def point_cfi(chapter, paragraph=1):
    return f'epubcfi(/6/{2 * (chapter + 2)}!/4/{2 * paragraph + 2}/1:0)'


def first_sentence(paragraph):
    for end in ('. ', '? ', '! ', '.” ', '?” '):
        index = paragraph.find(end)
        if index > 0:
            return paragraph[:index + len(end) - 1]
    return paragraph


def import_app_modules(data_dir):
    """The app's modules: the installed build's (build/install), else the source tree's."""
    os.environ['BOOKCASE_DATA_DIR'] = str(data_dir)
    installed = ROOT / 'build' / 'install' / 'share' / 'bookcase'
    if (installed / 'bookcase' / 'library.py').exists():
        sys.path.insert(0, str(installed))
    else:
        sys.path.insert(0, str(ROOT))
        import tests  # noqa: F401  (registers src/ as the bookcase package)
    from bookcase import covers, importing, library

    return covers, importing, library


def pages_of(book):
    """A believable page count for a book (the generated files are short)."""
    if book.get('format') == 'cbz':
        return 32
    return random.Random(seed_of('pages:' + book['title'])).randrange(180, 520, 4)


def reading_day(timestamp):
    """The day a moment counts for, as stats.py's (the day starting at 4 am)."""
    return (datetime.datetime.fromtimestamp(timestamp) - datetime.timedelta(hours=4)).date()


def log_sessions(library, book_id, book, last, now):
    """Reading up to where the book is now: a stretch of days before `last` (two to four
    weeks for a finished book, less for one half read), read on most of them, in the
    evening on a weekday, at lunch or on the train now and then, in the afternoon at the
    weekend. The last five days are all read and the one before them not, so the streak is
    five days; the last session ends at `last` (or, in the small hours of a day before today,
    the evening before)."""
    rng = random.Random(seed_of('sessions:' + book['title']))
    status, progress, _days = book['state']
    span = rng.randint(14, 26) if status == 'finished' else max(2, round(progress * 22))
    today = reading_day(now)
    gap = today - datetime.timedelta(days=5)
    end = datetime.datetime.fromtimestamp(last)
    times = []
    for back in range(span, 0, -1):
        day = (end - datetime.timedelta(days=back)).date()
        recent = (today - day).days < 5
        if day == gap or (not recent and rng.random() > 0.7):
            continue
        for _n in range(2 if rng.random() < 0.15 else 1):
            if day.weekday() >= 5:
                hour = rng.uniform(13, 17) if rng.random() < 0.6 else rng.uniform(20, 23)
            else:
                roll = rng.random()
                hour = (rng.uniform(7, 8.5) if roll < 0.2 else rng.uniform(12, 13.5)
                        if roll < 0.3 else rng.uniform(19.5, 23))
            start = datetime.datetime.combine(day, datetime.time()) + datetime.timedelta(
                hours=hour)
            times.append((start.timestamp(), rng.uniform(12, 55) * 60))
    times.sort()
    seconds = rng.uniform(15, 50) * 60
    final = last - seconds
    if end.hour < 7 and reading_day(last) != today:  # not at 4 am: the evening before
        final = datetime.datetime.combine(reading_day(last), datetime.time(21)).timestamp()
    if reading_day(final) != gap:
        times.append((final, seconds))
    count = len(times)
    for n, (started, seconds) in enumerate(times):
        if started + seconds > now:
            continue
        library.log_session(book_id, started, seconds, progress * n / count,
                            progress * (n + 1) / count)


def build_library(data_dir, written, now):
    """Add the written files through the app's Importer, as a user adding them would, and
    give the library its history. Times the API does not take (when a book was added, when
    it was last read) are set in the database afterwards."""
    covers_module, importing, library_module = import_app_modules(data_dir)
    library = library_module.Library(data_dir / 'library.sqlite')
    covers = covers_module.CoverStore(data_dir, library)
    importer = importing.Importer(library, covers, data_dir / 'Books')
    hashes = {book['title']: importing.partial_md5(path) for book, path, _c in written}
    report = importer.add([str(path) for _book, path, _c in written], copy=True)
    if report.failed:
        sys.exit(f'demo_library: could not add {report.failed}')
    ids = {}
    for book, _path, chapters in written:
        book_id = library.find_by_hash(hashes[book['title']])
        if book_id is None:
            sys.exit(f'demo_library: {book["title"]} was not added')
        ids[book['title']] = (book_id, chapters)

    for order, (book, _path, chapters) in enumerate(written, 1):
        book_id, chapters = ids[book['title']]
        fields = {'rating': book.get('rating', 0)}
        if book.get('format') == 'pdf':  # what a PDF's metadata does not say
            fields.update(publisher=book['publisher'], published=book['published'],
                          language='en', description=f'<p>{escape(book["blurb"])}</p>')
        library.update_book(book_id, **fields)
        added = now - book.get('added_days', 40 + order * 4) * DAY - order * 600
        state = book.get('state')
        if state:  # added a while before it was last read
            added = min(added, now - (state[2] + 30 + order) * DAY)
        library.db.execute('UPDATE books SET added = ? WHERE id = ?', (added, book_id))
        if not state:
            continue
        status, progress, days = state
        last = now - days * DAY - 1800 * (order % 9 + 1)
        count = max(1, len(chapters))
        chapter = max(1, min(count, math.ceil(progress * count)))
        library.set_progress(book_id, progress, point_cfi(chapter, 3))
        library.set_status([book_id], status)
        library.db.execute('UPDATE books SET last_read = ?, finished = ?, pages = ? '
                           'WHERE id = ?', (last, last if status == 'finished' else 0,
                                            pages_of(book), book_id))
        log_sessions(library, book_id, book, last, now)


    for title, chapter, paragraph, color, note in HIGHLIGHTS:
        if title not in ids:
            continue
        book_id, chapters = ids[title]
        text = first_sentence(chapters[chapter - 1][1][paragraph - 1])
        library.add_annotation(book_id, 'highlight', text_cfi(chapter, paragraph, len(text)),
                               text=text, note=note, color=color,
                               position=(chapter - 1 + paragraph / 20) / len(chapters))
    for title, chapter in BOOKMARKS:
        if title not in ids:
            continue
        book_id, chapters = ids[title]
        library.add_annotation(book_id, 'bookmark', point_cfi(chapter),
                               text=chapters[chapter - 1][0],
                               position=(chapter - 1) / len(chapters))
    for name, query, titles in SHELVES:
        shelf_id = library.add_shelf(name, query=query)
        members = [ids[title][0] for title in titles if title in ids]
        if members:
            library.add_to_shelf(shelf_id, members)
    library.close()


DEVICE_BOOKS = ['The Glass Estuary', 'The Long Burn', 'Pride and Prejudice',
                'A Quiet Word at Midnight']
DEVICE_ONLY = dict(
    title='Night Trains of the North', authors=['Astrid Halvorsen'], tags=['Travel'],
    publisher='Northlight Books', published='2021-02-25', style='stars', palette=1,
    kind='nonfiction',
    topics=('the sleeper car', 'a border station', 'the timetable', 'snow', 'the dining car',
            'a ferry', 'the night', 'a junction'),
    chapters=('Departures', 'Sleepers', 'Borders', 'Arrivals'),
    blurb='Ten night trains from Oslo to Narvik and beyond.')


def make_device(device_dir, data_dir):
    """A Kobo in device_dir with kepub copies of DEVICE_BOOKS and a book of its own."""
    device_dir = pathlib.Path(device_dir)
    if device_dir.exists():
        shutil.rmtree(device_dir)
    (device_dir / '.kobo').mkdir(parents=True)
    (device_dir / '.kobo' / 'version').write_text(
        'N000000000000,4.1.15,4.38.21908,4.1.15,4.1.15,00000000-0000-0000-0000-000000000388\n',
        encoding='utf-8')
    target = device_dir / 'Bookcase'
    sources = {path.name: path for path in (pathlib.Path(data_dir) / 'Books').rglob('*.epub')}
    for title in DEVICE_BOOKS:
        book = next(b for b in BOOKS if b['title'] == title)
        folder = target / book['authors'][0]
        folder.mkdir(parents=True, exist_ok=True)
        source = next((p for name, p in sources.items() if name.startswith(title)), None)
        if source is None:  # not imported (yet): write the book afresh
            source, _chapters = write_book(book, folder)
        else:
            shutil.copyfile(source, folder / f'{title}.kepub.epub')
            continue
        source.rename(folder / f'{title}.kepub.epub')
    folder = target / DEVICE_ONLY['authors'][0]
    folder.mkdir(parents=True, exist_ok=True)
    path, _chapters = write_book(DEVICE_ONLY, folder)
    path.rename(folder / f'{DEVICE_ONLY["title"]}.kepub.epub')
    return device_dir


def build(data_dir, count=None, files_only=False, force=False):
    data_dir = pathlib.Path(data_dir)
    if (data_dir / 'library.sqlite').exists() and not force and not files_only:
        print(f'{data_dir} exists; --force rebuilds it')
        return
    if force and data_dir.exists():
        shutil.rmtree(data_dir)
    data_dir.mkdir(parents=True, exist_ok=True)
    books = BOOKS[:count] if count else BOOKS
    staging = data_dir / ('Books' if files_only else 'incoming')
    staging.mkdir(exist_ok=True)
    written = []
    for book in books:
        path, chapters = write_book(book, staging)
        written.append((book, path, chapters))
    if files_only:
        return [path for _book, path, _chapters in written]
    build_library(data_dir, written, time.time())
    shutil.rmtree(staging)
    return sorted((data_dir / 'Books').rglob('*.*'))


def main():
    parser = argparse.ArgumentParser(description=__doc__.split('\n\n')[0])
    parser.add_argument('--data-dir', default=str(DEFAULT_DIR))
    parser.add_argument('--force', action='store_true', help='rebuild an existing library')
    parser.add_argument('--count', type=int, help='only the first COUNT books')
    parser.add_argument('--files-only', action='store_true',
                        help='write the books and covers, no library')
    parser.add_argument('--device', metavar='DIR',
                        help='lay out a pretend Kobo in DIR instead')
    args = parser.parse_args()
    if args.device:
        make_device(args.device, args.data_dir)
        print(args.device)
        return
    files = build(args.data_dir, args.count, args.files_only, args.force)
    if files:
        print(f'{len(files)} books in {args.data_dir}')


if __name__ == '__main__':
    main()
