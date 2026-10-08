# SPDX-License-Identifier: GPL-2.0-or-later
# SPDX-FileCopyrightText: 2026 Jack Tully

"""EPUB 2 and 3 (and kepub, which is an EPUB): metadata and cover, read and written.

    read(path) -> BookInfo
    write(path, info, cover=None, dest=None)   a copy of path at dest with info's metadata
                                               (and cover's bytes as its cover)
    is_protected(path) -> bool                 DRM: such a file is read but never rewritten

The cover is looked for in the order EPUB readers use: the EPUB 3 'cover-image' item, the
EPUB 2 <meta name="cover">, a manifest image named like a cover, the image on the guide's
cover page, the first image in the manifest.

write() rewrites only the OPF (and the cover image's bytes): every other member is copied
as it is, 'mimetype' first and stored, and the package's unique identifier is kept (it keys
font obfuscation). For EPUB 3 it writes both Calibre's series metas and the standard
belongs-to-collection, and updates dcterms:modified. The source file is never touched.
"""

import datetime
import os
import posixpath
import tempfile
import uuid
import zipfile
from urllib.parse import quote, unquote

from lxml import etree

from . import (IMAGE_MEDIA_TYPES, IMAGE_SUFFIXES, BookInfo, FormatError, clean_isbn,
               image_type, isbn_valid)

NS = {
    'c': 'urn:oasis:names:tc:opendocument:xmlns:container',
    'opf': 'http://www.idpf.org/2007/opf',
    'dc': 'http://purl.org/dc/elements/1.1/',
}
OPF = '{http://www.idpf.org/2007/opf}'
DC = '{http://purl.org/dc/elements/1.1/}'
XLINK_HREF = '{http://www.w3.org/1999/xlink}href'
MIMETYPE = b'application/epub+zip'
# Identifier schemes recognised in 'scheme:value' identifiers (EPUB 3 has no opf:scheme).
SCHEMES = ('isbn', 'uuid', 'google', 'amazon', 'asin', 'goodreads', 'openlibrary', 'doi',
           'calibre', 'mobi-asin', 'issn')
MAX_OPF = 16 * 1024 * 1024


def _parser():
    return etree.XMLParser(resolve_entities=False, no_network=True, huge_tree=False)


def _html_parser():
    return etree.HTMLParser(no_network=True, recover=True)


def _open(path):
    try:
        archive = zipfile.ZipFile(path)
    except (zipfile.BadZipFile, ValueError) as error:
        raise FormatError(f'Not an EPUB (not a zip archive): {os.path.basename(path)}') \
            from error
    return archive


def opf_path(archive):
    """The package document's name inside the archive."""
    try:
        container = etree.fromstring(archive.read('META-INF/container.xml'), _parser())
    except KeyError:
        container = None
    if container is not None:
        paths = container.xpath('//c:rootfile/@full-path', namespaces=NS)
        if paths and paths[0] in archive.namelist():
            return paths[0]
    # Some broken files lack container.xml: take the first .opf there is.
    for name in archive.namelist():
        if name.lower().endswith('.opf'):
            return name
    raise FormatError('Not an EPUB: no package document')


def _read_opf(archive):
    name = opf_path(archive)
    if archive.getinfo(name).file_size > MAX_OPF:
        raise FormatError('The package document is too large')
    try:
        root = etree.fromstring(archive.read(name), _parser())
    except etree.XMLSyntaxError:
        # A broken OPF: lxml's recovering parser keeps what it can.
        root = etree.fromstring(archive.read(name), etree.XMLParser(
            resolve_entities=False, no_network=True, recover=True))
    if root is None or etree.QName(root).localname != 'package':
        raise FormatError('Not an EPUB: the package document is broken')
    return name, root


def _metadata(root):
    metadata = root.find('opf:metadata', NS)
    if metadata is None:
        # OPF 1 style <dc-metadata> wrappers, or a missing namespace.
        metadata = root.find('.//opf:metadata', NS)
    if metadata is None:
        metadata = etree.SubElement(root, OPF + 'metadata', nsmap={'dc': NS['dc']})
        root.insert(0, metadata)
    return metadata


def _refines(metadata):
    refines = {}
    for meta in metadata.iter(OPF + 'meta'):
        target = meta.get('refines')
        if target and meta.get('property'):
            refines.setdefault(target.lstrip('#'), {})[meta.get('property')] = \
                (meta.text or '').strip()
    return refines


def _text(element):
    return ''.join(element.itertext()).strip() if element is not None else ''


def _href(base, href):
    return posixpath.normpath(posixpath.join(base, unquote(href.split('#')[0])))


def read(path):
    with _open(path) as archive:
        try:
            name, root = _read_opf(archive)
            info = _info(root)
            cover = find_cover(archive, root, name)
            if cover:
                try:
                    data = archive.read(cover)
                except KeyError:
                    data = None
                if image_type(data):
                    info.cover = data
        except (KeyError, zipfile.BadZipFile, etree.XMLSyntaxError, EOFError) as error:
            raise FormatError(f'Broken EPUB: {error}') from error
    return info


def _info(root):
    metadata = _metadata(root)
    refines = _refines(metadata)
    info = BookInfo()

    def dc(tag):
        return metadata.iter(DC + tag)

    info.title = next((_text(e) for e in dc('title') if _text(e)), '')
    for creator in dc('creator'):
        role = creator.get(OPF + 'role') or refines.get(creator.get('id'), {}).get('role')
        if role in (None, '', 'aut') and _text(creator):
            info.authors.append(_text(creator))
    info.language = next((_text(e) for e in dc('language') if _text(e)), '')
    info.publisher = next((_text(e) for e in dc('publisher') if _text(e)), '')
    dates = list(dc('date'))
    # EPUB 2 may carry several dates with opf:event; publication is the one wanted.
    for date in dates:
        if (date.get(OPF + 'event') or 'publication') == 'publication' and _text(date):
            info.published = _text(date)
            break
    else:
        info.published = next((_text(e) for e in dates if _text(e)), '')
    info.description = next((_text(e) for e in dc('description') if _text(e)), '')
    info.tags = [_text(e) for e in dc('subject') if _text(e)]
    unique = root.get('unique-identifier')
    for element in dc('identifier'):
        scheme, value = _identifier(element, refines)
        if not value:
            continue
        if scheme and scheme not in info.identifiers:
            info.identifiers[scheme] = value
        elif element.get('id') == unique and 'uuid' not in info.identifiers and not scheme:
            info.identifiers['uuid'] = value
    _read_series(metadata, refines, info)
    return info


def _identifier(element, refines):
    """(scheme, value) of a dc:identifier, scheme '' when unknown."""
    value = _text(element)
    scheme = (element.get(OPF + 'scheme') or '').lower()
    low = value.lower()
    kind = refines.get(element.get('id'), {}).get('identifier-type')
    if low.startswith('urn:isbn:') or scheme == 'isbn' or kind in ('02', '15'):
        return 'isbn', clean_isbn(value)
    if low.startswith('urn:uuid:'):
        return 'uuid', value[9:]
    if scheme:
        return scheme, value
    prefix, _colon, rest = value.partition(':')
    if rest and prefix.lower() in SCHEMES:
        if prefix.lower() == 'isbn':
            return 'isbn', clean_isbn(rest)
        return prefix.lower(), rest.strip()
    if isbn_valid(value):
        return 'isbn', clean_isbn(value)
    return '', value


def _float(text):
    try:
        value = float((text or '').strip())
    except ValueError:
        return 0.0
    return value if value == value and abs(value) < 1e9 else 0.0


def _read_series(metadata, refines, info):
    for meta in metadata.iter(OPF + 'meta'):
        if meta.get('name') == 'calibre:series' and (meta.get('content') or '').strip():
            info.series = meta.get('content').strip()
            for index in metadata.iter(OPF + 'meta'):
                if index.get('name') == 'calibre:series_index':
                    info.series_index = _float(index.get('content'))
            return
    for meta in metadata.iter(OPF + 'meta'):
        if meta.get('property') == 'belongs-to-collection' and _text(meta):
            refined = refines.get(meta.get('id'), {})
            if refined.get('collection-type', 'series') == 'series':
                info.series = _text(meta)
                info.series_index = _float(refined.get('group-position'))
                return


def find_cover(archive, root, name):
    """The archive member holding the cover image, or None."""
    base = posixpath.dirname(name)
    items = root.findall('opf:manifest/opf:item', NS)
    items = [item for item in items if item.get('href')]

    def is_image(item):
        return (item.get('media-type') or '').startswith('image/')

    item = cover_item(root)
    if item is not None:
        return _href(base, item.get('href'))
    guide = root.find('opf:guide/opf:reference[@type="cover"]', NS)
    if guide is not None and guide.get('href'):
        page = _href(base, guide.get('href'))
        image = _image_on_page(archive, page)
        if image:
            return image
    for item in items:
        if is_image(item):
            return _href(base, item.get('href'))
    return None


def cover_item(root):
    """The manifest <item> of the cover image, by the first three lookups, or None."""
    items = [item for item in root.findall('opf:manifest/opf:item', NS) if item.get('href')]

    def is_image(item):
        return (item.get('media-type') or '').startswith('image/')

    for item in items:
        if 'cover-image' in (item.get('properties') or '').split():
            return item
    for meta in root.iter(OPF + 'meta'):
        if meta.get('name') == 'cover' and meta.get('content'):
            content = meta.get('content')
            for item in items:
                if (item.get('id') == content or item.get('href') == content) \
                        and is_image(item):
                    return item
    for item in items:
        if is_image(item) and ('cover' in (item.get('id') or '').lower()
                               or 'cover' in posixpath.basename(item.get('href')).lower()):
            return item
    return None


def _image_on_page(archive, page):
    try:
        data = archive.read(page)
    except KeyError:
        return None
    try:
        document = etree.fromstring(data, _parser())
    except etree.XMLSyntaxError:
        document = etree.fromstring(data, _html_parser())
    if document is None:
        return None
    for element in document.iter():
        if not isinstance(element.tag, str):
            continue
        tag = etree.QName(element).localname.lower()
        source = None
        if tag == 'img':
            source = element.get('src')
        elif tag == 'image':
            source = element.get(XLINK_HREF) or element.get('href')
        if source and not source.startswith('data:'):
            return _href(posixpath.dirname(page), source)
    return None


def is_protected(path):
    """Whether an EPUB carries DRM: rights.xml, or encryption.xml listing anything but
    obfuscated fonts."""
    with _open(path) as archive:
        return _protected(archive)


_FONT_ALGORITHMS = ('http://www.idpf.org/2008/embedding',
                    'http://ns.adobe.com/pdf/enc#RC')


def _protected(archive):
    names = set(archive.namelist())
    if 'META-INF/rights.xml' in names:
        return True
    if 'META-INF/encryption.xml' not in names:
        return False
    try:
        root = etree.fromstring(archive.read('META-INF/encryption.xml'), _parser())
    except etree.XMLSyntaxError:
        return True
    for method in root.iter('{http://www.w3.org/2001/04/xmlenc#}EncryptionMethod'):
        if method.get('Algorithm') not in _FONT_ALGORITHMS:
            return True
    return False


def write(path, info, cover=None, dest=None):
    """Write a copy of the EPUB at path to dest with info's metadata (title, authors,
    series, tags, publisher, published, language, description, identifiers) and, when
    cover is given, those image bytes as its cover. Returns dest. Raises FormatError for a
    broken or DRM-protected file (the caller copies it as it is instead)."""
    if dest is None or os.path.abspath(dest) == os.path.abspath(path):
        raise ValueError('epub.write() writes a copy: dest must be another file')
    cover_type = image_type(cover) if cover else None
    if cover and not cover_type:
        raise ValueError('The cover is not an image')
    with _open(path) as source:
        try:
            if _protected(source):
                raise FormatError('The book is protected by DRM')
            name, root = _read_opf(source)
            replacements = {}
            added = {}
            _write_metadata(root, info)
            if cover:
                _write_cover(source, root, name, cover, cover_type, replacements, added)
            opf = etree.tostring(root, xml_declaration=True, encoding='utf-8')
            replacements[name] = opf
            _copy_archive(source, dest, replacements, added)
        except (KeyError, zipfile.BadZipFile, EOFError, etree.XMLSyntaxError) as error:
            raise FormatError(f'Broken EPUB: {error}') from error
    return dest


def _remove(element):
    parent = element.getparent()
    if parent is not None:
        parent.remove(element)


def _remove_refines(metadata, element_id):
    if not element_id:
        return
    for meta in list(metadata.iter(OPF + 'meta')):
        if (meta.get('refines') or '').lstrip('#') == element_id:
            _remove(meta)


def _set_dc(metadata, tag, values):
    for element in list(metadata.iter(DC + tag)):
        _remove_refines(metadata, element.get('id'))
        _remove(element)
    for value in values:
        if value:
            etree.SubElement(metadata, DC + tag).text = value


def _write_metadata(root, info):
    metadata = _metadata(root)
    v3 = root.get('version', '2.0').startswith('3')

    _set_dc(metadata, 'title', [info.title])
    if v3:
        titles = list(metadata.iter(DC + 'title'))
        if titles:
            titles[0].set('id', 'title')

    old = list(metadata.iter(DC + 'creator'))
    for creator in old:
        refined = _refines(metadata).get(creator.get('id'), {})
        role = creator.get(OPF + 'role') or refined.get('role')
        if role in (None, '', 'aut'):
            _remove_refines(metadata, creator.get('id'))
            _remove(creator)
    for number, author in enumerate(info.authors, 1):
        creator = etree.SubElement(metadata, DC + 'creator')
        creator.text = author
        if v3:
            creator.set('id', f'creator{number:02d}')
            meta = etree.SubElement(metadata, OPF + 'meta', refines=f'#creator{number:02d}',
                                    property='role', scheme='marc:relators')
            meta.text = 'aut'
        else:
            creator.set(OPF + 'role', 'aut')

    _set_dc(metadata, 'subject', list(info.tags))
    _set_dc(metadata, 'publisher', [info.publisher])
    _set_dc(metadata, 'description', [info.description])
    _set_dc(metadata, 'language', [info.language or 'und'])
    if v3:
        _set_dc(metadata, 'date', [info.published])
    else:
        _set_dc(metadata, 'date', [])
        if info.published:
            date = etree.SubElement(metadata, DC + 'date')
            date.set(OPF + 'event', 'publication')
            date.text = info.published

    _write_identifiers(root, metadata, info, v3)
    _write_series(metadata, info, v3)
    if v3:
        for meta in list(metadata.iter(OPF + 'meta')):
            if meta.get('property') == 'dcterms:modified':
                _remove(meta)
        now = datetime.datetime.now(datetime.UTC).strftime('%Y-%m-%dT%H:%M:%SZ')
        etree.SubElement(metadata, OPF + 'meta', property='dcterms:modified').text = now


def _write_identifiers(root, metadata, info, v3):
    unique = root.get('unique-identifier')
    keep = None
    for element in list(metadata.iter(DC + 'identifier')):
        if unique and element.get('id') == unique:
            keep = element
            continue
        _remove_refines(metadata, element.get('id'))
        _remove(element)
    if keep is None:
        keep = etree.SubElement(metadata, DC + 'identifier', id=unique or 'bookid')
        keep.text = 'urn:uuid:' + (info.identifiers.get('uuid') or _new_uuid())
        root.set('unique-identifier', keep.get('id'))
    kept = _identifier(keep, _refines(metadata))
    for scheme, value in sorted(info.identifiers.items()):
        if not value or (scheme, str(value)) == kept or (scheme == 'uuid' and kept[1]):
            continue
        element = etree.SubElement(metadata, DC + 'identifier')
        if scheme == 'isbn':
            value = clean_isbn(value)
            if v3:
                element.text = f'urn:isbn:{value}'
            else:
                element.set(OPF + 'scheme', 'ISBN')
                element.text = value
        elif v3:
            element.text = f'{scheme}:{value}'
        else:
            element.set(OPF + 'scheme', scheme)
            element.text = str(value)


def _new_uuid():
    return str(uuid.uuid4())


def _write_series(metadata, info, v3):
    for meta in list(metadata.iter(OPF + 'meta')):
        if meta.get('name') in ('calibre:series', 'calibre:series_index'):
            _remove(meta)
        elif meta.get('property') == 'belongs-to-collection':
            _remove_refines(metadata, meta.get('id'))
            _remove(meta)
    if not info.series:
        return
    index = f'{info.series_index:g}'
    etree.SubElement(metadata, OPF + 'meta', name='calibre:series', content=info.series)
    etree.SubElement(metadata, OPF + 'meta', name='calibre:series_index', content=index)
    if v3:
        meta = etree.SubElement(metadata, OPF + 'meta', property='belongs-to-collection',
                                id='series')
        meta.text = info.series
        etree.SubElement(metadata, OPF + 'meta', refines='#series',
                         property='collection-type').text = 'series'
        etree.SubElement(metadata, OPF + 'meta', refines='#series',
                         property='group-position').text = index


def _write_cover(archive, root, name, data, kind, replacements, added):
    """Put the cover's bytes in: over the existing cover image (its media type updated) or
    as a new manifest item marked as the cover both ways."""
    base = posixpath.dirname(name)
    v3 = root.get('version', '2.0').startswith('3')
    metadata = _metadata(root)
    item = cover_item(root)
    if item is not None and _href(base, item.get('href')) in archive.namelist():
        replacements[_href(base, item.get('href'))] = data
        item.set('media-type', IMAGE_MEDIA_TYPES[kind])
    else:
        manifest = root.find('opf:manifest', NS)
        if manifest is None:
            manifest = etree.SubElement(root, OPF + 'manifest')
        names = set(archive.namelist())
        href = 'cover' + IMAGE_SUFFIXES[kind]
        number = 1
        while posixpath.normpath(posixpath.join(base, href)) in names:
            number += 1
            href = f'cover-{number}{IMAGE_SUFFIXES[kind]}'
        ids = {element.get('id') for element in root.iter() if element.get('id')}
        item_id = 'cover-image'
        while item_id in ids:
            item_id += '-1'
        item = etree.SubElement(manifest, OPF + 'item', id=item_id, href=quote(href),
                                **{'media-type': IMAGE_MEDIA_TYPES[kind]})
        added[posixpath.normpath(posixpath.join(base, href))] = data
    if v3:
        for other in root.findall('opf:manifest/opf:item', NS):
            properties = (other.get('properties') or '').split()
            if 'cover-image' in properties and other is not item:
                properties.remove('cover-image')
                if properties:
                    other.set('properties', ' '.join(properties))
                else:
                    del other.attrib['properties']
        properties = (item.get('properties') or '').split()
        if 'cover-image' not in properties:
            item.set('properties', ' '.join([*properties, 'cover-image']))
    for meta in list(metadata.iter(OPF + 'meta')):
        if meta.get('name') == 'cover':
            _remove(meta)
    etree.SubElement(metadata, OPF + 'meta', name='cover', content=item.get('id'))


def _copy_archive(source, dest, replacements, added):
    """Write source's members to dest (through a temporary file next to it): 'mimetype'
    first and stored, replaced members with their new bytes, then the added ones."""
    folder = os.path.dirname(os.path.abspath(dest))
    fd, temporary = tempfile.mkstemp(suffix='.epub.part', dir=folder)
    os.close(fd)
    try:
        with zipfile.ZipFile(temporary, 'w') as target:
            mimetype = zipfile.ZipInfo('mimetype', date_time=(2000, 1, 1, 0, 0, 0))
            mimetype.compress_type = zipfile.ZIP_STORED
            target.writestr(mimetype, MIMETYPE)
            seen = {'mimetype'}
            for member in source.infolist():
                if member.filename in seen or member.is_dir():
                    continue
                seen.add(member.filename)
                copy = zipfile.ZipInfo(member.filename, date_time=member.date_time)
                copy.external_attr = member.external_attr
                copy.compress_type = (zipfile.ZIP_STORED
                                      if member.compress_type == zipfile.ZIP_STORED
                                      else zipfile.ZIP_DEFLATED)
                data = replacements.get(member.filename)
                if data is None:
                    data = source.read(member.filename)
                target.writestr(copy, data)
            for name, data in added.items():
                member = zipfile.ZipInfo(name, date_time=_now_tuple())
                member.compress_type = zipfile.ZIP_STORED
                target.writestr(member, data)
        os.replace(temporary, dest)
    except BaseException:
        os.unlink(temporary)
        raise


def _now_tuple():
    return datetime.datetime.now().timetuple()[:6]

