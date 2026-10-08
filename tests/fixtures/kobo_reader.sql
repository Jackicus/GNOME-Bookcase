-- SPDX-License-Identifier: GPL-2.0-or-later
-- SPDX-FileCopyrightText: 2026 Jack Tully

-- The parts of a Kobo's .kobo/KoboReader.sqlite that Bookcase reads and writes (kobo.py), for
-- tests/test_kobo.py's synthetic databases. Not dumped from a device: put together from the
-- queries of Calibre's KoboTouch driver (src/calibre/devices/kobo/driver.py: Shelf and
-- ShelfContent inserts, content's ReadStatus, ___PercentRead, DateLastRead, dbversion) and a
-- MobileRead thread's dump of the Shelf table (CreationDate, Id, InternalName, LastModified,
-- Name, Type, _IsDeleted, _IsVisible, _IsSynced). Firmware 4.x has many more content columns
-- (chapters, store data, Kobo Plus…); those here are the ones Calibre's driver names. Booleans
-- are the strings 'true' and 'false', as the Kobo writes them. The rows a test needs are
-- inserted by the test, with invented titles.

CREATE TABLE dbversion (version INTEGER);
INSERT INTO dbversion VALUES (174);

CREATE TABLE content (
    ContentID TEXT NOT NULL,
    ContentType TEXT NOT NULL,
    MimeType TEXT NOT NULL,
    BookID TEXT,
    BookTitle TEXT,
    ImageId TEXT,
    Title TEXT COLLATE NOCASE,
    Attribution TEXT COLLATE NOCASE,
    Description TEXT,
    DateCreated TEXT,
    ShortCoverKey TEXT,
    adobe_location TEXT,
    Publisher TEXT,
    IsEncrypted BOOL,
    DateLastRead TEXT,
    FirstTimeReading BOOL,
    ChapterIDBookmarked TEXT,
    ParagraphBookmarked INTEGER,
    BookmarkWordOffset INTEGER,
    NumShortcovers INTEGER,
    VolumeIndex INTEGER,
    ___NumPages INTEGER,
    ReadStatus INTEGER,
    ___SyncTime TEXT,
    ___UserID TEXT NOT NULL,
    PublicationId TEXT,
    ___FileOffset INTEGER,
    ___FileSize INTEGER,
    ___PercentRead INTEGER,
    ___ExpirationStatus INTEGER,
    FavouritesIndex NUMERIC NOT NULL DEFAULT -1,
    Accessibility INTEGER DEFAULT 1,
    ContentURL TEXT,
    Language TEXT,
    BookshelfTags TEXT,
    IsDownloaded BIT NOT NULL DEFAULT 1,
    ISBN TEXT,
    Series TEXT,
    SeriesNumber TEXT,
    SeriesNumberFloat REAL,
    Subtitle TEXT,
    ExternalId TEXT,
    TimeSpentReading INTEGER,
    LastTimeStartedReading TEXT,
    LastTimeFinishedReading TEXT,
    PRIMARY KEY (ContentID)
);

CREATE TABLE Shelf (
    CreationDate TEXT,
    Id TEXT,
    InternalName TEXT,
    LastModified TEXT,
    Name TEXT,
    Type TEXT,
    _IsDeleted BOOL,
    _IsVisible BOOL,
    _IsSynced BOOL,
    PRIMARY KEY (Id)
);

CREATE TABLE ShelfContent (
    ShelfName TEXT,
    ContentId TEXT,
    DateModified TEXT,
    _IsDeleted BOOL,
    _IsSynced BOOL,
    PRIMARY KEY (ShelfName, ContentId)
);
