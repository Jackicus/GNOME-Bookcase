# SPDX-License-Identifier: GPL-2.0-or-later
# SPDX-FileCopyrightText: 2026 Jack Tully

"""Comic archives: CBZ (zip) and CBR (RAR, through libarchive's bsdtar when installed).

    read(path) -> BookInfo
    images(names) -> [name]          an archive's page images in reading (natural) order
    cbr_to_cbz(path, dest) -> dest   a CBZ with a CBR's files; FormatError without bsdtar

The cover is the page ComicInfo.xml marks FrontCover, else the first image by natural sort
('page2' before 'page10'); ComicInfo.xml gives Title, Series, Number (series index),
Writer (authors), Summary, Year/Month/Day, Publisher, Genre and Tags, LanguageISO. A '.cbr'
that is really a zip (it happens) is read as a CBZ.
"""

import os
import re
import shutil
import subprocess
import tempfile
import zipfile

from lxml import etree

from . import BookInfo, FormatError, image_type

IMAGE_SUFFIXES = ('.jpg', '.jpeg', '.png', '.webp', '.gif', '.avif', '.bmp')
MAX_IMAGE = 64 * 1024 * 1024
TIMEOUT = 60


def _natural(name):
    return [int(part) if part.isdigit() else part.lower()
            for part in re.split(r'(\d+)', name)]


def images(names):
    """The page images among an archive's member names, in natural order."""
    pages = []
    for name in names:
        parts = name.split('/')
        if any(part.startswith('.') or part == '__MACOSX' for part in parts):
            continue
        if name.lower().endswith(IMAGE_SUFFIXES):
            pages.append(name)
    return sorted(pages, key=_natural)


def read(path):
    with open(path, 'rb') as file:
        head = file.read(8)
    if head.startswith(b'PK'):
        return _read_cbz(path)
    if head.startswith(b'Rar!\x1a\x07'):
        return _read_cbr(path)
    raise FormatError('Not a comic archive')


def _read_cbz(path):
    try:
        with zipfile.ZipFile(path) as archive:
            names = [member.filename for member in archive.infolist() if not member.is_dir()]
            pages = images(names)
            if not pages:
                raise FormatError('A comic archive without images')
            info = BookInfo()
            front = None
            comic_info = _find(names, 'comicinfo.xml')
            if comic_info:
                front = _comic_info(archive.read(comic_info), info)
            cover = pages[front] if front is not None and front < len(pages) else pages[0]
            if archive.getinfo(cover).file_size <= MAX_IMAGE:
                data = archive.read(cover)
                info.cover = data if image_type(data) else None
            return info
    except (zipfile.BadZipFile, EOFError) as error:
        raise FormatError(f'Broken comic archive: {error}') from error


def _find(names, wanted):
    for name in names:
        if name.lower() == wanted or name.lower().endswith('/' + wanted):
            return name
    return None


def _bsdtar():
    return shutil.which('bsdtar')


def _read_cbr(path):
    info = BookInfo()
    tool = _bsdtar()
    if not tool:
        return info  # known by its name: no way to look inside without libarchive
    try:
        listing = subprocess.run([tool, '-tf', path], capture_output=True, timeout=TIMEOUT,
                                 check=True).stdout.decode('utf-8', 'replace')
    except (subprocess.SubprocessError, OSError) as error:
        raise FormatError(f'Broken comic archive: {error}') from error
    names = [name for name in listing.splitlines() if name and not name.endswith('/')]
    pages = images(names)
    if not pages:
        raise FormatError('A comic archive without images')
    front = None
    comic_info = _find(names, 'comicinfo.xml')
    if comic_info:
        data = _extract(tool, path, comic_info)
        if data:
            front = _comic_info(data, info)
    cover = pages[front] if front is not None and front < len(pages) else pages[0]
    data = _extract(tool, path, cover)
    info.cover = data if image_type(data) else None
    return info


def _extract(tool, path, name):
    # bsdtar takes member names as patterns: escape the pattern characters.
    pattern = re.sub(r'([\\*?\[])', r'\\\1', name)
    try:
        result = subprocess.run([tool, '-xOf', path, pattern], capture_output=True,
                                timeout=TIMEOUT)
    except (subprocess.SubprocessError, OSError):
        return None
    return result.stdout if result.returncode == 0 else None


def _comic_info(data, info):
    """Fill info from ComicInfo.xml; return the FrontCover page's index, if it says."""
    try:
        root = etree.fromstring(data, etree.XMLParser(resolve_entities=False,
                                                      no_network=True, recover=True))
    except etree.XMLSyntaxError:
        return None
    if root is None:
        return None
    fields = {}
    for element in root:
        if isinstance(element.tag, str):
            fields[etree.QName(element).localname] = (element.text or '').strip()
    info.title = fields.get('Title', '')
    info.series = fields.get('Series', '')
    if info.series:
        try:
            info.series_index = float(fields.get('Number', '') or 0)
        except ValueError:
            info.series_index = 0.0
        if not info.title and fields.get('Number'):
            info.title = f'{info.series} {fields["Number"]}'
    info.authors = [name.strip() for name in fields.get('Writer', '').split(',')
                    if name.strip()]
    info.description = fields.get('Summary', '')
    info.publisher = fields.get('Publisher', '')
    info.language = fields.get('LanguageISO', '')
    year, month, day = (fields.get(k, '') for k in ('Year', 'Month', 'Day'))
    if re.fullmatch(r'\d{4}', year):
        info.published = year
        if month.isdigit() and 1 <= int(month) <= 12:
            info.published += f'-{int(month):02d}'
            if day.isdigit() and 1 <= int(day) <= 31:
                info.published += f'-{int(day):02d}'
    for key in ('Genre', 'Tags'):
        info.tags += [tag.strip() for tag in fields.get(key, '').split(',') if tag.strip()]
    if fields.get('Web', '').startswith('http'):
        info.identifiers['url'] = fields['Web']
    pages = root.find('Pages')
    if pages is not None:
        for page in pages:
            if page.get('Type') == 'FrontCover' and (page.get('Image') or '').isdigit():
                return int(page.get('Image'))
    return None


def cbr_to_cbz(path, dest):
    """Write a CBZ at dest holding a CBR's files (the reader opens zips only)."""
    tool = _bsdtar()
    if not tool:
        raise FormatError('Converting a CBR needs bsdtar (libarchive)')
    folder = os.path.dirname(os.path.abspath(dest))
    with tempfile.TemporaryDirectory(dir=folder, prefix='.cbr-') as temporary:
        try:
            subprocess.run([tool, '-xf', path, '-C', temporary], capture_output=True,
                           timeout=TIMEOUT * 5, check=True)
        except (subprocess.SubprocessError, OSError) as error:
            raise FormatError(f'Cannot unpack the comic: {error}') from error
        part = dest + '.part'
        try:
            with zipfile.ZipFile(part, 'w') as archive:
                for directory, folders, files in os.walk(temporary):
                    folders.sort()
                    for name in sorted(files):
                        full = os.path.join(directory, name)
                        if os.path.islink(full):
                            continue
                        relative = os.path.relpath(full, temporary)
                        archive.write(full, relative, compress_type=zipfile.ZIP_STORED)
            os.replace(part, dest)
        except BaseException:
            if os.path.exists(part):
                os.unlink(part)
            raise
    return dest
