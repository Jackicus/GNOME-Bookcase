# E-book manager + reader: technical research

Researched 2026-10-08 on this machine (CachyOS). Status of each claim:

- **[tested]**: run here and it worked.
- **[source]**: read in upstream source code.
- **[docs]**: taken from documentation or memory, and not run.

## 0. Environment (verified on this machine)

| Component | Version | Notes |
|---|---|---|
| python | 3.14.7 | `sqlite3.sqlite_version` 3.53.4, FTS5 available |
| python-gobject | 3.56.3 | |
| gtk4 / libadwaita | 4.22.5 / 1.9.4 | |
| webkitgtk-6.0 | 2.52.6 | typelibs `WebKit-6.0`, `JavaScriptCore-6.0`, `WebKitWebProcessExtension-6.0` |
| poppler-glib | 26.08.0 | typelib `Poppler-0.18` |
| python-cairo | 1.29.1 | needed for Poppler page rendering |
| python-lxml | 6.1.3 | |
| libarchive / unrar / 7zip | 3.8.9 / 7.3.1 / 26.03 | relevant for CBR, which has no pure-Python path |

You do not need any new system packages. You will vendor foliate-js, a pure-JS bundle, as app data.

---

## 1. EPUB metadata: read and write (zipfile + lxml)

### Structure
1. `META-INF/container.xml` contains `<rootfile full-path="OEBPS/content.opf" media-type="application/oebps-package+xml"/>`. Use the first rootfile. Its namespace is `urn:oasis:names:tc:opendocument:xmlns:container`.
2. The OPF is `<package xmlns="http://www.idpf.org/2007/opf" version="2.0|3.0" unique-identifier="uid">` and contains:
   - `<metadata xmlns:dc="http://purl.org/dc/elements/1.1/">`
   - `<manifest>` with `<item id href media-type properties?>`
   - `<spine toc="ncx">` with `<itemref idref linear?>`
   - `<guide>` (EPUB2 only)
3. hrefs in the OPF are **relative to the OPF's directory** and **URL-encoded**. Resolve them with `posixpath.normpath(posixpath.join(dirname(opf), unquote(href)))`.

### Dublin Core fields
`dc:title`, `dc:creator`, `dc:contributor`, `dc:language` (required), `dc:identifier` (required), `dc:publisher`, `dc:date`, `dc:description` (often HTML), `dc:subject` (tags), `dc:rights`, `dc:source`, `dc:type`.

### EPUB2 vs EPUB3

| Concept | EPUB2 (OPF 2.0) | EPUB3 |
|---|---|---|
| Author role | `<dc:creator opf:role="aut" opf:file-as="Leckie, Ann">` | `<dc:creator id="c1">Ann Leckie</dc:creator>` + `<meta refines="#c1" property="role" scheme="marc:relators">aut</meta>` + `<meta refines="#c1" property="file-as">Leckie, Ann</meta>` |
| Generic meta | `<meta name="x" content="y"/>` | `<meta property="x">y</meta>`. Readers still honour `name/content` metas. |
| Identifier type | `<dc:identifier opf:scheme="ISBN">978…</dc:identifier>` | `<dc:identifier id="isbn">urn:isbn:978…</dc:identifier>`, optionally `<meta refines="#isbn" property="identifier-type" scheme="onix:codelist5">15</meta>` (15 = ISBN-13) |
| Series | calibre convention: `<meta name="calibre:series" content="Imperial Radch"/>` + `<meta name="calibre:series_index" content="2"/>` | standard: `<meta property="belongs-to-collection" id="s1">Imperial Radch</meta>` + `<meta refines="#s1" property="collection-type">series</meta>` + `<meta refines="#s1" property="group-position">2</meta>`. calibre writes **both** for EPUB3. |
| Cover | `<meta name="cover" content="<manifest-id>"/>`; `<guide><reference type="cover" href="cover.xhtml"/>` | `<item properties="cover-image" .../>` in the manifest |
| Modified date | n/a | `<meta property="dcterms:modified">2026-10-08T00:00:00Z</meta>` (required) |
| TOC | NCX (`spine/@toc`) | nav doc (`item properties="nav"`), often NCX as well |

**Identifiers.** The `unique-identifier` attribute points at the identifier the book uses as its key, often a `urn:uuid:`. Never delete or change it: it is the IDPF font-obfuscation key. Look for an ISBN in this order:
1. a `urn:isbn:` prefix
2. `opf:scheme="ISBN"`
3. `identifier-type` 15
4. a bare 10- or 13-digit value that passes the checksum

calibre also writes `<dc:identifier opf:scheme="calibre">uuid</dc:identifier>` and `opf:scheme="MOBI-ASIN"` / `"AMAZON"`.

### Cover lookup order **[tested]**
1. EPUB3: a manifest item whose `properties` tokens include `cover-image`.
2. EPUB2: `meta[name=cover]/@content` gives a manifest id. Check that the item is `image/*`. Some files put an href in `content` instead, so try a match on href too.
3. A manifest image whose `id` or `href` contains "cover".
4. `guide/reference[@type="cover"]` gives an XHTML page. Parse it and take the first `<img src>` or SVG `<image xlink:href>`. Resolve that path relative to the **XHTML file**, not the OPF.
5. Fallback: the first manifest `image/*`, or the first image referenced by the first spine item.

### Rewriting safely
- `zipfile` cannot replace a member in place. **Copy the archive to a temp file in the same directory**, then `os.replace()` it over the original so the swap is atomic.
- `mimetype` must be the **first** entry. It must be **STORED** (not compressed), with no extra field and content exactly `application/epub+zip`. This makes the bytes at offset 30 equal `mimetype` and offset 38 begin `application/epub+zip`. `ZipFile.writestr(ZipInfo(...))` with `compress_type=ZIP_STORED` produces exactly this **[tested]**.
- Keep every other member byte-identical except the OPF. Do not touch `META-INF/encryption.xml`. Never touch DRM'd files: check for `META-INF/rights.xml`, or an `encryption.xml` that lists non-font resources.
- Parse with `XMLParser(resolve_entities=False, no_network=True)` to block XXE. Serialize with `xml_declaration=True, encoding='utf-8'`. lxml keeps namespace prefixes and comments.
- Update `dcterms:modified` on EPUB3 when you write. The tested code below omits it for brevity.

### Tested implementation **[tested]**
The script was run against a synthetic EPUB3. It read the book, wrote title, authors, series, ISBN and cover, and read them back correctly. After the write, `mimetype` was entry 0 with `compress_type=0` and an empty extra field.

```python
import os, posixpath, shutil, tempfile, zipfile
from urllib.parse import unquote
from lxml import etree

NS = {'c': 'urn:oasis:names:tc:opendocument:xmlns:container',
      'opf': 'http://www.idpf.org/2007/opf',
      'dc': 'http://purl.org/dc/elements/1.1/'}
OPF = '{%s}' % NS['opf']; DC = '{%s}' % NS['dc']
PARSER = etree.XMLParser(resolve_entities=False, no_network=True)

def opf_path(z):
    root = etree.fromstring(z.read('META-INF/container.xml'), PARSER)
    return root.xpath('//c:rootfile/@full-path', namespaces=NS)[0]

def read(path):
    with zipfile.ZipFile(path) as z:
        op = opf_path(z)
        root = etree.fromstring(z.read(op), PARSER)
    md = root.find('opf:metadata', NS)
    refines = {}
    for m in md.findall('opf:meta[@refines]', NS):
        refines.setdefault(m.get('refines').lstrip('#'), {})[m.get('property')] = (m.text or '').strip()
    dc = lambda tag: md.findall('dc:' + tag, NS)
    authors = [(e.text or '').strip() for e in dc('creator')
               if (e.get(OPF + 'role') or refines.get(e.get('id'), {}).get('role')) in (None, 'aut')]
    out = {'version': root.get('version', '2.0'),
           'title': next((e.text for e in dc('title')), None), 'authors': authors,
           'language': [e.text for e in dc('language')],
           'publisher': next((e.text for e in dc('publisher')), None),
           'date': next((e.text for e in dc('date')), None),
           'description': next((e.text for e in dc('description')), None),
           'subjects': [e.text for e in dc('subject')], 'identifiers': {}}
    for e in dc('identifier'):
        v = (e.text or '').strip(); scheme = (e.get(OPF + 'scheme') or '').lower(); low = v.lower()
        if low.startswith('urn:isbn:') or scheme == 'isbn' \
           or refines.get(e.get('id'), {}).get('identifier-type') == '15':
            out['identifiers']['isbn'] = v.split(':')[-1]
        elif low.startswith('urn:uuid:') or scheme == 'uuid':
            out['identifiers']['uuid'] = v.split(':')[-1]
        elif scheme:
            out['identifiers'][scheme] = v
    s = md.find('opf:meta[@name="calibre:series"]', NS)
    if s is not None:
        out['series'] = s.get('content')
        i = md.find('opf:meta[@name="calibre:series_index"]', NS)
        out['series_index'] = float(i.get('content')) if i is not None else None
    else:
        for m in md.findall('opf:meta[@property="belongs-to-collection"]', NS):
            r = refines.get(m.get('id'), {})
            if r.get('collection-type', 'series') == 'series':
                out['series'] = (m.text or '').strip()
                out['series_index'] = float(r['group-position']) if r.get('group-position') else None
                break
    out['cover'] = find_cover(root, op)
    return out

def find_cover(root, op):
    base = posixpath.dirname(op)
    href = lambda h: posixpath.normpath(posixpath.join(base, unquote(h)))
    items = root.findall('opf:manifest/opf:item', NS)
    is_img = lambda it: (it.get('media-type') or '').startswith('image/')
    for it in items:                                            # EPUB3
        if 'cover-image' in (it.get('properties') or '').split(): return href(it.get('href'))
    m = root.find('opf:metadata/opf:meta[@name="cover"]', NS)    # EPUB2
    if m is not None:
        for it in items:
            if it.get('id') == m.get('content') and is_img(it): return href(it.get('href'))
    for it in items:
        if 'cover' in (it.get('id') or '').lower() and is_img(it): return href(it.get('href'))
    g = root.find('opf:guide/opf:reference[@type="cover"]', NS)   # XHTML page -> parse first img
    if g is not None: return ('page', href(g.get('href').split('#')[0]))
    for it in items:
        if is_img(it): return href(it.get('href'))
    return None

def write(path, title=None, authors=None, series=None, series_index=None, isbn=None):
    with zipfile.ZipFile(path) as z:
        op = opf_path(z)
        root = etree.fromstring(z.read(op), PARSER)
    md = root.find('opf:metadata', NS); v3 = root.get('version', '2.0').startswith('3')
    if title is not None:
        t = md.find('dc:title', NS)
        if t is None: t = etree.SubElement(md, DC + 'title')
        t.text = title
    if authors is not None:
        old = md.findall('dc:creator', NS); ids = {e.get('id') for e in old if e.get('id')}
        for e in old: md.remove(e)
        for m in md.findall('opf:meta[@refines]', NS):
            if m.get('refines').lstrip('#') in ids: md.remove(m)
        for n, a in enumerate(authors):
            e = etree.SubElement(md, DC + 'creator'); e.text = a
            if v3:
                e.set('id', f'creator{n}')
                etree.SubElement(md, OPF + 'meta', refines=f'#creator{n}', property='role',
                                 scheme='marc:relators').text = 'aut'
            else:
                e.set(OPF + 'role', 'aut')
    if series is not None:
        for m in md.findall('opf:meta[@name="calibre:series"]', NS) + \
                 md.findall('opf:meta[@name="calibre:series_index"]', NS):
            md.remove(m)
        etree.SubElement(md, OPF + 'meta', name='calibre:series', content=series)
        if series_index is not None:
            etree.SubElement(md, OPF + 'meta', name='calibre:series_index', content=f'{series_index:g}')
        if v3:
            for m in md.findall('opf:meta[@property="belongs-to-collection"]', NS):
                for r in md.findall(f'opf:meta[@refines="#{m.get("id")}"]', NS): md.remove(r)
                md.remove(m)
            etree.SubElement(md, OPF + 'meta', property='belongs-to-collection', id='series-1').text = series
            etree.SubElement(md, OPF + 'meta', refines='#series-1', property='collection-type').text = 'series'
            if series_index is not None:
                etree.SubElement(md, OPF + 'meta', refines='#series-1',
                                 property='group-position').text = f'{series_index:g}'
    if isbn is not None:
        uid = root.get('unique-identifier')
        for e in md.findall('dc:identifier', NS):
            if e.get('id') != uid and ('isbn' in (e.text or '').lower()
                                       or (e.get(OPF + 'scheme') or '').lower() == 'isbn'):
                md.remove(e)
        e = etree.SubElement(md, DC + 'identifier')
        if v3: e.text = f'urn:isbn:{isbn}'
        else: e.set(OPF + 'scheme', 'ISBN'); e.text = isbn
    replace_entry(path, op, etree.tostring(root, xml_declaration=True, encoding='utf-8'))

def replace_entry(path, name, data):
    fd, tmp = tempfile.mkstemp(suffix='.epub.tmp', dir=os.path.dirname(os.path.abspath(path))); os.close(fd)
    try:
        with zipfile.ZipFile(path) as src, zipfile.ZipFile(tmp, 'w') as dst:
            if 'mimetype' in src.namelist():
                zi = zipfile.ZipInfo('mimetype', date_time=src.getinfo('mimetype').date_time)
                zi.compress_type = zipfile.ZIP_STORED
                dst.writestr(zi, src.read('mimetype').strip())
            for info in src.infolist():
                if info.filename == 'mimetype': continue
                ni = zipfile.ZipInfo(info.filename, date_time=info.date_time)
                ni.compress_type = zipfile.ZIP_DEFLATED; ni.external_attr = info.external_attr
                dst.writestr(ni, data if info.filename == name else src.read(info.filename))
        shutil.copystat(path, tmp)
        os.replace(tmp, path)
    except BaseException:
        os.unlink(tmp); raise
```

To embed a cover image:
1. Add the image file to the zip.
2. Add a manifest item with `properties="cover-image"`. For EPUB2 instead, add `<meta name="cover" content="id">`.
3. Optionally add a cover XHTML page and put it first in the spine.

calibre writes all of these.

---

## 2. Other formats

### MOBI / AZW / AZW3 (PalmDB) **[source + tested]**
All integers are **big-endian**. The layout below was confirmed against `foliate-js/mobi.js` (`PDB_HEADER`, `PALMDOC_HEADER`, `MOBI_HEADER`, `EXTH_RECORD_TYPE`). A parser was tested against a synthetic file.

**PalmDB header (78 bytes, file offset 0)**

| off | size | field |
|---|---|---|
| 0 | 32 | name (NUL-padded, short title) |
| 32 | 2+2 | attributes, version |
| 36 | 4×3 | ctime, mtime, backup time |
| 60 | 4 | type: `BOOK` (or `TEXt` for PalmDOC) |
| 64 | 4 | creator: `MOBI` (or `REAd`) |
| 76 | 2 | numRecords |
| 78 | 8×n | record list: `uint32 offset`, `uint8 attrs`, `uint24 uid` |

Then there are 2 padding bytes. Record *i* spans `offset[i]` to `offset[i+1]`, and the last record runs to EOF.

**Record 0 = PalmDOC header (16 bytes) + MOBI header + EXTH + full title**

| off in rec0 | size | field |
|---|---|---|
| 0 | 2 | compression (1 none, 2 PalmDOC LZ77, 17480 = HUFF/CDIC) |
| 4 | 4 | text length |
| 8 | 2 | number of text records |
| 10 | 2 | record size (4096) |
| 12 | 2 | encryption (0 none; 1/2 = DRM, so show "DRM-protected") |
| 16 | 4 | `MOBI` magic |
| 20 | 4 | MOBI header length (EXTH starts at `16 + this`) |
| 24 | 4 | mobi type (2 = book, 3 = PalmDoc, 257 = news…) |
| 28 | 4 | text encoding (1252 = cp1252, 65001 = UTF-8) |
| 32 | 4 | unique id |
| 36 | 4 | file version (6 = MOBI6, 8 = KF8/AZW3) |
| 84 | 4 | full-name offset (relative to rec0) |
| 88 | 4 | full-name length |
| 92 | 4 | locale (low byte = language, next = region) |
| 108 | 4 | **first image record index** ("resourceStart") |
| 112/116 | 4/4 | first HUFF record / count |
| 128 | 4 | EXTH flags; **bit 0x40 means an EXTH block is present** |
| 192/196 | 4/4 | (KF8) FDST index / count |
| 240 | 4 | extra record data flags (trailing entries) |
| 244 | 4 | INDX (NCX) record |

**EXTH block** at `16 + mobi_header_length`:
- `'EXTH'`, `uint32 header_length`, `uint32 record_count`
- then the records, each `uint32 type`, `uint32 length (includes these 8 bytes)`, `data[length-8]`

String records use the MOBI text encoding.

| EXTH type | meaning |
|---|---|
| 100 | author (repeatable) |
| 101 | publisher |
| 103 | description (HTML) |
| 104 | ISBN |
| 105 | subject (repeatable) |
| 106 | publishing date |
| 108 | contributor |
| 109 | rights |
| 112 | source (repeatable) |
| 113 | ASIN |
| 121 | KF8 boundary record index (combo MOBI+KF8 file; 0xFFFFFFFF = none) |
| 125 | number of resources |
| 129 | KF8 cover URI |
| 201 | **cover offset** (uint32, relative to first image record) |
| 202 | thumbnail offset |
| 501 | cdetype (`EBOK`, `PDOC`) |
| 503 | **updated title** (prefer it over the full-name) |
| 524 | language |
| 527 | page-progression-direction |

**Cover bytes** = record `first_image + exth[201]`. If 201 is missing or equals 0xFFFFFFFF, use 202. The bytes are raw JPEG, GIF or PNG; sniff the magic bytes.

**Combo files.** If version < 8 and EXTH 121 is set, record `boundary` holds a second record-0 for the KF8 half. foliate-js prefers that one for reading. For metadata, the MOBI6 record 0 is enough.

```python
import struct
EXTH = {100:'author',101:'publisher',103:'description',104:'isbn',105:'subject',106:'date',
        108:'contributor',109:'rights',113:'asin',501:'cdetype',503:'title',524:'language'}
MULTI = {'author','subject','contributor','language'}

def read_mobi(path):
    data = open(path, 'rb').read()          # or mmap
    if data[60:68] not in (b'BOOKMOBI', b'TEXtREAd'): raise ValueError('not MOBI')
    n = struct.unpack_from('>H', data, 76)[0]
    offs = [struct.unpack_from('>I', data, 78 + 8*i)[0] for i in range(n)] + [len(data)]
    rec = lambda i: data[offs[i]:offs[i+1]]
    r0 = rec(0); meta = {}
    if r0[16:20] != b'MOBI': return meta
    hlen, mtype, enc, uid, ver = struct.unpack_from('>IIIII', r0, 20)
    codec = 'utf-8' if enc == 65001 else 'cp1252'
    toff, tlen = struct.unpack_from('>II', r0, 84)
    first_img = struct.unpack_from('>I', r0, 108)[0]
    meta.update(title=r0[toff:toff+tlen].decode(codec, 'replace'),
                encrypted=struct.unpack_from('>H', r0, 12)[0] != 0, version=ver)
    cover = thumb = None
    if struct.unpack_from('>I', r0, 128)[0] & 0x40:
        p = 16 + hlen; count = struct.unpack_from('>I', r0, p + 8)[0]; p += 12
        for _ in range(count):
            typ, ln = struct.unpack_from('>II', r0, p); val = r0[p+8:p+ln]; p += ln
            if typ == 201: cover = struct.unpack('>I', val)[0]
            elif typ == 202: thumb = struct.unpack('>I', val)[0]
            elif typ in EXTH:
                k, s = EXTH[typ], val.decode(codec, 'replace').strip()
                meta.setdefault(k, []).append(s) if k in MULTI else meta.__setitem__(k, s)
    idx = cover if cover not in (None, 0xFFFFFFFF) else thumb
    if idx not in (None, 0xFFFFFFFF) and first_img not in (0, 0xFFFFFFFF) and first_img + idx < n:
        meta['cover'] = rec(first_img + idx)
    return meta
```

Writing MOBI metadata (patching EXTH in place) is possible but fiddly. It is **not recommended**; treat MOBI and AZW3 as read-only. KFX (`.kfx`, an Amazon Ion container) is out of scope.

### PDF via Poppler GI **[tested]**
```python
gi.require_version('Poppler', '0.18'); from gi.repository import Poppler, Gio
import cairo
doc = Poppler.Document.new_from_gfile(Gio.File.new_for_path(p), None, None)  # (file, password, cancellable)
doc.props.title, doc.props.author, doc.props.subject, doc.props.keywords, doc.props.creation_datetime  # GLib.DateTime
doc.props.metadata   # XMP string (may contain dc:creator etc.)
doc.get_n_pages()
page = doc.get_page(0); w, h = page.get_size()              # points
scale = 600 / h
surf = cairo.ImageSurface(cairo.FORMAT_RGB24, int(w*scale), int(h*scale))
cr = cairo.Context(surf); cr.set_source_rgb(1, 1, 1); cr.paint()   # white bg, PDFs are transparent
cr.scale(scale, scale); page.render(cr); surf.write_to_png(out)    # or Gdk.Texture via memory
```
Poppler may raise `GLib.Error` for encrypted PDFs; catch it. Run cover rendering in a thread or subprocess: Poppler is thread-safe per document, but crashes on malformed PDFs are possible. A worker subprocess for imports is the safest design.

### CBZ / CBR **[docs]**
- **CBZ**: open with `zipfile`. Sort image entries (`.jpg .jpeg .png .webp .gif .avif`) naturally (`re.split(r'(\d+)')`), skip `__MACOSX/` and dotfiles, and take the first as the cover.
- **`ComicInfo.xml`** (Anansi schema) sits at the archive root. Fields: `Title, Series, Number, Volume, Summary, Year, Month, Day, Writer, Penciller, Publisher, Genre, Tags, LanguageISO, PageCount, Web`, plus `<Pages><Page Image="0" Type="FrontCover"/>`. Prefer the `FrontCover` page if present. Writing is easy: replace the zip entry the same way as in §1, with no mimetype constraint.
- **CBR** (RAR) has no stdlib support. Options:
  - shell out to `bsdtar -xOf book.cbr <entry>` (from libarchive, already installed) or `unrar p -inul`
  - use `7z`
  - convert to CBZ on import

  foliate-js cannot render CBR either, since it only handles zip. Recommendation: on import, offer to convert CBR to CBZ with `bsdtar`. Flatpak would need bsdtar bundled.
- **CB7**: same approach as CBR, via `7z`.

### FB2 / FBZ **[docs]**
- Format: XML with namespace `http://www.gribuser.ru/xml/fictionbook/2.0`. The metadata lives at `FictionBook/description/title-info`:
  - `book-title`
  - `author/{first-name,middle-name,last-name,nickname}`
  - `genre*`
  - `lang`
  - `annotation`
  - `date`
  - `sequence/@name` + `@number` (the series)
  - `coverpage/image/@l:href="#cover.jpg"`, where `xmlns:l` is XLink. Read it as `{http://www.w3.org/1999/xlink}href`.
- `publish-info/{publisher,year,isbn}`.
- Cover: find `binary[@id="cover.jpg"]` (strip the `#`) and `base64.b64decode(text)`. `@content-type` gives the MIME type.
- `.fbz` / `.fb2.zip` is a zip containing one `.fb2`.
- Parse with lxml, `huge_tree=True` (files with embedded images can be big), `resolve_entities=False`. Watch for `windows-1251` encoding declarations; lxml handles them when it gets bytes.

### TXT
Use the filename as the title. Detect encoding by trying UTF-8 (with BOM), then fall back to cp1252/latin-1. A TXT file has no cover, so generate a placeholder. foliate-js has no TXT loader. Either render it yourself (wrap into a minimal single-section book object, or convert to a one-chapter in-memory EPUB on open), or implement the foliate "book interface" in JS: `sections: [{load: () => blobURL, size}]`.

---

## 3. WebKitGTK 6 from Python **[tested, live, on WebKitGTK 2.52.6]**

A test script built a Gtk 4 window containing a `WebKit.WebView` and performed each of the following:
- served HTML and ES modules from a custom scheme
- imported a relative module
- `fetch()`ed a binary "book" from the same scheme
- checked `isSecureContext` (true) and `crypto.subtle.digest('SHA-1')`, which worked
- posted a message to Python
- ran `evaluate_javascript`
- ran `call_async_javascript_function` with arguments

### Signatures (introspected here)
```
WebContext.register_uri_scheme(scheme, callback(req), user_data=None)
URISchemeRequest.finish(stream: Gio.InputStream, stream_length: int, content_type: str=None)
URISchemeRequest.finish_with_response(WebKit.URISchemeResponse)      # status, headers
URISchemeRequest.finish_error(GLib.Error)
URISchemeRequest.get_uri() / get_path() / get_scheme() / get_http_method() / get_http_headers()
URISchemeResponse.new(stream, length); .set_content_type(); .set_status(code, reason); .set_http_headers(Soup.MessageHeaders)
UserContentManager.register_script_message_handler(name, world_name=None) -> bool
UserContentManager.register_script_message_handler_with_reply(name, world_name=None) -> bool
   signal 'script-message-received::<name>' (manager, JavaScriptCore.Value)
WebView.evaluate_javascript(script, length, world_name, source_uri, cancellable, callback)
WebView.evaluate_javascript_finish(res) -> JavaScriptCore.Value   (.to_string(), .to_json(indent), .to_int32() ...)
WebView.call_async_javascript_function(body, length, arguments: GLib.Variant a{sv}, world_name, source_uri, cancellable, callback)
WebView.call_async_javascript_function_finish(res) -> JavaScriptCore.Value   # awaits returned Promise
WebContext.get_security_manager().register_uri_scheme_as_{secure,cors_enabled,local,no_access,display_isolated,empty_document}
```

### Working pattern
```python
gi.require_version('WebKit', '6.0')
from gi.repository import WebKit, Gio, GLib

def on_request(req):
    path = req.get_path()                       # app://reader/foo.js -> '/foo.js'
    if path.startswith('/book/'):               # SAME HOST as the page (see CSP pitfall)
        data = library.read_book_bytes(path)    # or Gio.File.read() for streaming
        req.finish(Gio.MemoryInputStream.new_from_bytes(GLib.Bytes.new(data)), len(data),
                   'application/epub+zip'); return
    f = resolve_inside(READER_DIR, path)        # reject '..' escapes!
    if not f:
        req.finish_error(GLib.Error.new_literal(Gio.io_error_quark(), 'not found',
                                                Gio.IOErrorEnum.NOT_FOUND)); return
    gfile = Gio.File.new_for_path(f)
    req.finish(gfile.read(None), -1, MIME[ext])  # JS MUST be text/javascript for modules

ctx = WebKit.WebContext.get_default()
ctx.register_uri_scheme('app', on_request)        # register BEFORE first WebView loads
sm = ctx.get_security_manager()
sm.register_uri_scheme_as_secure('app')           # harmless; see note
sm.register_uri_scheme_as_cors_enabled('app')

ucm = WebKit.UserContentManager()
ucm.connect('script-message-received::bridge', lambda m, v: handle(json.loads(v.to_string())))
ucm.register_script_message_handler('bridge', None)  # JS: webkit.messageHandlers.bridge.postMessage(str)
view = WebKit.WebView(user_content_manager=ucm, settings=WebKit.Settings(
    enable_developer_extras=DEBUG, enable_write_console_messages_to_stdout=DEBUG,
    enable_html5_local_storage=False, enable_html5_database=False,
    enable_back_forward_navigation_gestures=False, enable_hyperlink_auditing=False))
view.load_uri('app://reader/reader.html')

# Python -> JS: fire and forget
view.evaluate_javascript('reader.view.next()', -1, None, None, None, None)
# Python -> JS with awaited result (cleaner than Foliate's token/promise dance):
view.call_async_javascript_function('return await reader.view.goTo(target)', -1,
    GLib.Variant('a{sv}', {'target': GLib.Variant('s', cfi)}), None, None, None,
    lambda v, r: v.call_async_javascript_function_finish(r))
```
For object or array arguments, pass the JSON as an `'s'` Variant and `JSON.parse` it in the function body. A raw `a{sv}` maps to JS objects, which works for simple types.

### Pitfalls (some found by testing)
1. **CSP and origin. [tested]** A custom scheme URL's origin includes its host. `app://reader/...` and `app://book/...` are **different origins**, so `connect-src 'self'` blocked `fetch('app://book/42')`. Fix: serve the book under the same host (`/book/<id>.epub`), or list `app:` in the CSP.
2. **Module scripts** loaded from the same custom-scheme origin worked **even without** the SecurityManager calls on 2.52. `isSecureContext` was already `true`, so `crypto.subtle` (which foliate-js needs for IDPF font deobfuscation SHA-1) works. Still register `secure` and `cors_enabled`: older WebKit needed them for cross-origin module and fetch requests, and they cost nothing.
3. **Serve JS as `text/javascript`.** A wrong MIME type makes module loads fail silently.
4. **Book MIME type.** Setting it on a `URISchemeResponse` was not reflected in `Blob.type` (`''`). foliate-js `makeBook()` detects format by magic bytes (zip, PDF, MOBI) and then by **filename extension from the URL pathname** (`.cbz`, `.fb2`, `.fbz`). **So the book URL must end with the real extension**, e.g. `app://reader/book/123.cbz`.
5. **`fetch()` → `res.blob()` loads the whole book into memory**, which is fine for most books but costly for 500 MB comics or PDFs. Foliate avoids this with a hidden `<input type=file>`: it calls `input.click()` from JS, handles `WebView::run-file-chooser` in GJS with `req.select_files([path])`, and gets a real disk-backed `File` that zip.js reads with random access. PyGObject can do the same: connect `run-file-chooser`, call `request.select_files([uri_or_path])`, return `True`. Use this for large files.
6. `register_uri_scheme` must happen once per `WebContext`, before any load. A `WebKit.NetworkSession` is separate, so set the cache policy or ephemeral mode there if needed.
7. EPUB JavaScript must stay blocked. foliate-js renders sections in iframes from `blob:` URLs on the same origin, so **use Foliate's CSP**:
   ```
   default-src 'self' blob:; script-src 'self'; style-src 'self' blob: 'unsafe-inline';
   img-src 'self' blob: data:; connect-src 'self' blob: data:; frame-src blob: data:;
   object-src blob: data:; form-action 'none';
   ```
8. External links: handle `decide-policy` (`NAVIGATION_ACTION` / `NEW_WINDOW_ACTION`) and open them with `Gtk.UriLauncher`. foliate-js also emits an `external-link` event you can forward.
9. Handle the `web-process-terminated` signal: show an error and reload.

---

## 4. foliate-js

- **Repo:** https://github.com/johnfactotum/foliate-js
- **License:** **MIT** (Copyright (c) 2022 John Factotum).
- **Pinned commit:** `78914aef4466eb960965702401634c2cb348e9b1` (2026-05-01, "Use original hrefs for external links and add isExternal in fb2.js (#129)"). `git ls-remote` confirmed it is HEAD as of today.
- The README says "not stable, API may change; include as a git submodule". So **vendor at the pinned hash** and copy the files as-is (there is no build step).
- Foliate (the GJS app) is at commit `67b6676d3f936c5edea91d4d903385ef39dd25c0` (2026-04-08). It is GPL-3.0, so copy *patterns* from it, not code, unless the app is GPL too.

### Files to vendor
| File | Needed for |
|---|---|
| `view.js` | entry point; `<foliate-view>`, `makeBook()`; imports `epubcfi.js`, `progress.js`, `overlayer.js`, `text-walker.js` statically |
| `epub.js`, `epubcfi.js` | EPUB |
| `mobi.js` + `vendor/fflate.js` (4 KB, MIT) | MOBI/AZW3 (fflate for KF8 zlib fonts) |
| `fb2.js` | FB2/FBZ |
| `comic-book.js` | CBZ |
| `vendor/zip.js` (36 KB, zip.js, BSD-3-Clause) | all zip-based formats |
| `paginator.js` | reflowable renderer |
| `fixed-layout.js` | pre-paginated EPUB, CBZ, PDF |
| `overlayer.js`, `progress.js`, `text-walker.js` | static deps of view.js |
| `search.js`, `tts.js` | dynamically imported by `view.search()` / `view.initTTS()` |
| `footnotes.js` | optional; Foliate's reader uses it for footnote popups |
| `pdf.js` + `vendor/pdfjs/` (**13 MB**, PDF.js 5.x, Apache-2.0) | optional, "experimental". **Recommendation:** skip it and render PDFs natively with Poppler, or vendor PDF.js later. |
| `ui/tree.js`, `ui/menu.js`, `reader.html/js` | demo only; don't ship |
| `dict.js`, `opds.js`, `uri-template.js`, `quote-image.js` | optional extras |

All formats are dynamically imported from `makeBook()`, so missing optional files only break their own format.

### API (confirmed from view.js / paginator.js / README at the pinned commit)
```js
import './foliate-js/view.js'                  // defines <foliate-view>
const view = document.createElement('foliate-view'); document.body.append(view)
await view.open(fileOrBlobOrUrl)               // string URL -> fetch -> File(name = URL pathname)
// or: const book = await makeBook(file); await view.open(book)
view.book.metadata   // {title, author, language, publisher, published, identifier, subject, description, series?...}
view.book.toc        // [{label, href, subitems}]
await view.book.getCover?.()                    // Blob
await view.init({ lastLocation: savedCfi, showTextStart: true })   // restore position

view.renderer.setStyles(cssString)              // CSS injected into each section doc
view.renderer.setAttribute('flow', 'paginated' | 'scrolled')
view.renderer.setAttribute('max-column-count', 2)     // also 'gap' ('6%'), 'margin' ('48px'),
view.renderer.setAttribute('max-inline-size', '720px')// 'max-block-size', 'animated' (boolean attr)
// NO JS property API for these — setAttribute only.
CSS: foliate-view::part(filter) { filter: invert(1) hue-rotate(180deg) }  ::part(head) ::part(foot)

await view.goTo(href | sectionIndex | cfi)      // returns resolved {index, anchor}
await view.goToFraction(0.42)
await view.next(); await view.prev(); view.goLeft(); view.goRight()   // RTL-aware
view.renderer.nextSection() / prevSection() / firstSection() / lastSection()
view.history.back() / forward(); canGoBack / canGoForward; 'index-change' event
view.getSectionFractions()                      // tick marks for a progress slider
view.select(cfi); view.deselect()

// events on view
'relocate' -> detail: { fraction, section:{current,total}, location:{current,next,total},
                         time:{section,total}, tocItem:{label,href,...}, pageItem, cfi, range, reason }
'load'     -> { doc, index }   // attach keydown / selection listeners to section docs
'external-link', 'link' (cancelable)
'create-overlay' -> { index }  // add stored annotations for this section now
'draw-annotation' -> { draw, annotation, doc, range }  // call draw(Overlayer.highlight, {color})
'show-annotation' -> { value, index, range }           // user clicked an annotation

// annotations: an annotation is { value: cfi, color?, note? } — value is the key
await view.addAnnotation({ value: cfi, color: 'yellow' })  // -> {index, label}
await view.deleteAnnotation({ value: cfi })
await view.showAnnotation({ value: cfi })
// Overlayer draw functions: highlight, underline, strikethrough, squiggly, outline, copyImage
// CFI from current selection: view.getCFI(index, range)

// search: async generator
for await (const r of view.search({ query, matchCase:false, matchDiacritics:false, matchWholeWords:false /*, index*/ })) {
  if (r === 'done') break
  if (r.progress != null) updateProgress(r.progress)
  else if (r.subitems) showResults(r.label, r.subitems)   // [{cfi, excerpt:{pre, match, post}}]
}
view.clearSearch()

// TTS: await view.initTTS('word'|'sentence'); view.tts.start() / next() / prev() / resume() / setMark(mark)
//   returns SSML strings; you speak them yourself (e.g. Speech Dispatcher/spd-say from Python) and call setMark.
```
**EPUB font deobfuscation:** `new EPUB({loadText, loadBlob, getSize, sha1})` defaults to `crypto.subtle`. This is fine because the custom scheme is a secure context **[tested]**.

### How Foliate wires it (src/webview.js, src/book-viewer.js, src/reader/reader.js) **[source]**
- **Scheme.** `WebKit.WebContext.get_default().register_uri_scheme('foliate', …)` serves only the `/reader/` and `/foliate-js/` paths from GResource via `req.finish(file.read(null), -1, mime)`. Foliate does not call `SecurityManager`. Errors go to `req.finish_error(GLib.Error(Gio.IOErrorEnum, NOT_FOUND))`.
- **Page load.** `book-viewer.open(file)` stores the path and calls `webView.loadURI('foliate:///reader/reader.html')`, which has the CSP shown above.
- **Startup handshake.** On startup, reader.js posts `{type:'ready'}` via `webkit.messageHandlers.viewer`. GJS answers with `exec('init', {uiText})`. In the page, `init` sets `#file-input.onchange` and calls `loadFile()`, which is `input.click()`. GJS's `run-file-chooser` handler calls `req.select_files([path])`, so the book arrives as a **real `File`** with no copy. Then `makeBook(file)` → `new Reader(book)` → `emit({type:'book-ready', ...})`.
- **JS → GJS.** One handler, `viewer`, receives `JSON.stringify({type, ...detail})`. GJS switches on `payload.type`: `relocate`, `create-overlay`, `show-annotation`, `selection`, `external-link`, `history-index-change`, `book-error`, `dialog-open`/`dialog-close`, `pinch-zoom`, `show-image`.
- **GJS → JS.** `exec(funcName, params)` wraps the call as `(async()=>await f(JSON.parse(...)))().then(p => handler.postMessage({token, ok, payload}))`. A `PromiseStore` keyed by random token resolves it. `iter()` wraps async generators (used for search) by storing the generator under `globalThis[handlerName][token]` and calling `.next` repeatedly. **In Python, prefer `call_async_javascript_function`**, which returns the awaited value directly **[tested]**. Use the message handler only for push events (relocate and so on).
- **Settings.**
  - GJS sets the font families and size through `WebKit.Settings` (`serif_font_family`, `default_font_size`, `minimum_font_size`, …).
  - It calls `reader.setAppearance({layout:{gap,maxInlineSize,maxBlockSize,maxColumnCount,flow,animated}, style:{lineHeight,justify,hyphenate,invert,theme,...}})`. That sets renderer attributes and `renderer.setStyles(getCSS(style))`, plus theme CSS variables.
  - Defaults: `gap 0.06`, `max-inline-size 720`, `max-block-size 1440`, `max-column-count 2`, `line-height 1.5`, justify and hyphenate on.
- **Annotations.** They are stored in GJS, keyed by book identifier, and contain `{value: cfi, color, note, text, created}`. On `create-overlay {index}`, GJS sends `addAnnotation` for each stored annotation in that section. The `draw-annotation` handler in the page picks `Overlayer.highlight` or an underline/squiggly/strikethrough variant.
- **Running heads/feet.** These are filled in the `relocate` handler from `tocItem.label` and `location`.
- **Covers for the library.** Foliate imports books in a separate hidden WebView (`initImport`) that runs `book.getCover()` and returns base64. **Our app should do metadata and covers in Python instead** (§1–2), which is faster and needs no WebView.

---

## 5. Devices

### Detection with Gio **[docs]**
```python
mon = Gio.VolumeMonitor.get()       # keep a reference!
mon.connect('mount-added', on_mount); mon.connect('mount-removed', on_unmount)
for m in mon.get_mounts(): on_mount(mon, m)
def on_mount(_, mount):
    root = mount.get_root()            # Gio.File
    path = root.get_path()             # None for MTP/gphoto2 (gvfs) mounts -> use URIs
    def has(rel): return root.resolve_relative_path(rel).query_exists(None)
    if has('.kobo/KoboReader.sqlite') or has('.kobo/version'):   kind = 'kobo'
    elif has('documents') and has('system'):                      kind = 'kindle'
    # Eject: mount.eject_with_operation(Gio.MountUnmountFlags.NONE, Gtk.MountOperation(), None, cb)
```
- **Kobo** is a USB mass-storage device (vendor 0x2237).
  - Markers: `.kobo/` and `.kobo/KoboReader.sqlite`.
  - `.kobo/version` is a CSV: `serial,?,firmware,?,?,model-id`.
  - Books can go anywhere on the root; Kobo scans recursively. Use a folder such as `/Books/<Author>/<Title>.kepub.epub`. Sideloaded EPUBs show up in the `content` table after the device re-scans on eject.
  - **Do not write to `KoboReader.sqlite` while it is mounted** unless you know the schema; collections live in the `Shelf` and `ShelfContent` tables. Leave this for v2.
- **Kindle** (vendor 0x1949).
  - Older and most current models are USB mass storage, with `documents/` and `system/` at the root (`system/version.txt` on some). Copy books to `documents/`. With calibre's convention you can use `documents/<Author>/`, and the Kindle indexes them.
  - Sideloaded formats: AZW3, MOBI, PDF, TXT. **EPUB is not readable via USB** on most firmware, so convert or use Send-to-Kindle.
  - **2024+ Kindles (e.g. Paperwhite 12th gen, Colorsoft) reportedly use MTP.** These appear as gvfs `mtp://` mounts with `get_path()` returning None, and the internal folder is `Internal Storage/documents`. `Gio.File.copy()` to the mtp URI works through gvfs, so detect by URI and walk children.

### Kepub conversion **[docs]**
kepubify (Go, MIT, by pgaskin) does the following:
1. Wraps text in each content document in `<span class="koboSpan" id="kobo.P.S">`. `P` increments per paragraph (block element) and `S` per sentence or text segment. It splits text nodes at sentence boundaries and skips `script`, `style`, `pre`-like and SVG content.
2. Wraps the body's content in `<div id="book-columns"><div id="book-inner">…</div></div>`.
3. Adds a small style block (`kobostylehacks`).
4. Applies optional cleanups: removes Adobe `page-map` and `adept` metas, fixes the cover `properties`, smartens punctuation, and so on.
5. Writes the output as `name.kepub.epub`.

**A minimal pure-Python version is feasible**, at about 150 lines with lxml. For each spine XHTML file:
- parse as XML, falling back to `lxml.html` when it fails to parse
- walk block elements (`p, h1-h6, li, blockquote, td, dt, dd, div` with direct text) and increment `P` for each
- split each direct text node and `tail` with a sentence regex `(?<=[.!?…]["”’)]?)\s+` and wrap each piece in a koboSpan with id `kobo.{P}.{S}`
- add the two wrapper divs
- write the file back with the §1 zip rewrite

The Kobo reader only needs the spans for page and position tracking, highlights and stats. Exact parity with kepubify is not required. Plain EPUB also works on a Kobo, using the older Adobe RMSDK renderer.

### Send-to-Kindle by email **[docs; Amazon's official page not re-checked]**
- Before sending:
  - Send from an address on the user's **Approved Personal Document E-mail List**.
  - Send to the user's `name@kindle.com` address.
  - Leaving the subject empty is fine; subject `convert` applies only to PDF.
- **Accepted formats:** EPUB (since 2022, must be DRM-free), PDF, DOC/DOCX, TXT, RTF, HTM/HTML, JPG/PNG/GIF/BMP. **Do not send MOBI/AZW**: Amazon deprecated them for Send-to-Kindle in 2022–23. One 2026 blog claims legacy MOBI still works, but it is unconfirmed, so treat MOBI as unsupported. Convert AZW3 to EPUB, or send EPUB only.
- **Size limits:** about 50 MB per email (total attachments); the web and desktop apps allow about 200 MB.
- **Python (stdlib):**
  ```python
  msg = email.message.EmailMessage(); msg['From']=user; msg['To']=kindle_addr; msg['Subject']=title
  msg.set_content('Sent from <App>')
  msg.add_attachment(data, maintype='application', subtype='epub+zip', filename=safe_ascii_name)
  with smtplib.SMTP_SSL('smtp.gmail.com', 465, context=ssl.create_default_context()) as s:
      s.login(user, app_password); s.send_message(msg)
  ```
  - Gmail and Outlook need an **app password** (2FA on). OAuth is out of scope.
  - Store the password in **libsecret** (`gi.require_version('Secret','1')`) if the typelib is available; otherwise warn.
  - Use STARTTLS (port 587) via `SMTP(...).starttls()` for other providers.
  - Keep the filename ASCII; Amazon mangles some non-ASCII names.

---

## 6. Writing to a calibre library (metadata.db) **[source + tested]**

The schema is saved verbatim as `research/calibre_metadata_sqlite.sql`, from calibre `master`, `resources/metadata_sqlite.sql`. It has `PRAGMA user_version=28` and `application_id=0x63616c69` ("cali"). Real libraries carry whatever version they were upgraded to; calibre's `schema_upgrades.py` migrates them. **Never create or upgrade the schema yourself for an existing library; just read `user_version`.**

### Custom functions calibre registers
From `src/calibre/db/backend.py`, class `Connection.__init__` (it uses apsw):
```python
self.createcollation('PYNOCASE', partial(pynocase, encoding=encoding))
self.createscalarfunction('title_sort', title_sort)
self.createscalarfunction('author_to_author_sort', _author_to_author_sort, 1)
self.createscalarfunction('uuid4', lambda *a: str(uuid.uuid4()), 0)
self.createscalarfunction('books_list_filter', lambda x: 1, 1)   # "dummy, for dynamically created filters"
self.createcollation('icucollate', icu_collator)
# "Legacy aggregators (never used) but present for backwards compat"
self.createaggregatefunction('sortconcat', SortedConcatenate, 2)
self.createaggregatefunction('sortconcat_bar', partial(SortedConcatenate, sep='|'), 2)
self.createaggregatefunction('sortconcat_amper', partial(SortedConcatenate, sep='&'), 2)
self.createaggregatefunction('identifiers_concat', IdentifiersConcat, 2)
self.createaggregatefunction('concat', Concatenate, 1)
self.createaggregatefunction('aum_sortconcat', AumSortedConcatenate, 4)
```
calibre also loads its C `sqlite_extension`, which provides a custom FTS tokenizer. That tokenizer is only used by the separate `full-text-search.db`. The `annotations_fts` tables in metadata.db use the built-in `unicode61` and `porter` tokenizers, which stdlib sqlite supports **[tested]**.

### Where the schema actually calls them
- **Triggers:**
  - `books_insert_trg`: `UPDATE books SET sort=title_sort(NEW.title), uuid=uuid4() WHERE id=NEW.id`
  - `books_update_trg`: `title_sort(NEW.title)` when the title changes
  - `series_insert_trg` / `series_update_trg`: `title_sort(NEW.name)`
- **Views:** `meta` uses `sortconcat(bal.id, name)` and `concat(name)` / `concat(format)`. `tag_browser_*` views use `title_sort(name)` and `books_list_filter(...)` (10 uses).
- `author_to_author_sort` is not used in triggers. calibre computes `authors.sort` in Python on insert, so **you must set `authors.sort` and `books.author_sort` yourself**.

### Python `sqlite3` registrations (tested: schema creates, trigger fires, `meta` view works)
```python
import sqlite3, uuid, re
db = sqlite3.connect(lib / 'metadata.db', timeout=10, isolation_level=None)   # manage txns explicitly
db.execute('PRAGMA foreign_keys=ON')

_ART = re.compile(r'^(A\s+|The\s+|An\s+)', re.I)        # calibre tweak per_language_title_sort_articles['eng']
def title_sort(t, *_):
    t = (t or '').strip()
    if t[:1] in '"\'“‘«': t = t[1:]                       # calibre strips one leading quote pair
    m = _ART.search(t)
    return f'{t[len(m.group(1)):]}, {m.group(1).strip()}' if m else t
def author_sort(a):                                      # simplified author_to_author_sort('comma' method)
    if not a or ',' in a: return a or ''
    toks = re.sub(r'\(.*?\)|\[.*?\]', '', a).split()
    if len(toks) < 2: return a
    suffixes = {'jr','sr','inc','ph.d','phd','md','m.d','i','ii','iii','iv','junior','senior'}
    sfx = []
    while len(toks) > 2 and toks[-1].lower().rstrip('.') in suffixes: sfx.insert(0, toks.pop())
    return ' '.join([toks[-1] + ','] + toks[:-1] + sfx)

class _Concat:
    def __init__(self): self.v = []
    def step(self, x):
        if x is not None: self.v.append(x)
    def finalize(self): return ','.join(self.v) if self.v else None
def _sortconcat(sep):
    class C:
        def __init__(self): self.v = {}
        def step(self, ndx, x):
            if x is not None: self.v[ndx] = x
        def finalize(self): return sep.join(self.v[k] for k in sorted(self.v)) if self.v else None
    return C
class _IdConcat:
    def __init__(self): self.v = []
    def step(self, k, v): self.v.append(f'{k}:{v}')
    def finalize(self): return ','.join(self.v) if self.v else None
class _AumSortConcat:     # (ndx, author, sort, link) -> 'author:::sort:::link' joined by ':#:'
    def __init__(self): self.v = {}
    def step(self, ndx, author, sort, link):
        if author is not None: self.v[ndx] = ':::'.join((author, sort or '', link or ''))
    def finalize(self): return ':#:'.join(self.v[k] for k in sorted(self.v)) if self.v else None

db.create_function('title_sort', 1, title_sort, deterministic=True)
db.create_function('author_to_author_sort', 1, lambda a: author_sort((a or '').replace('|', ',')), deterministic=True)
db.create_function('uuid4', 0, lambda: str(uuid.uuid4()))
db.create_function('books_list_filter', 1, lambda x: 1)
db.create_aggregate('concat', 1, _Concat)
db.create_aggregate('sortconcat', 2, _sortconcat(','))
db.create_aggregate('sortconcat_bar', 2, _sortconcat('|'))
db.create_aggregate('sortconcat_amper', 2, _sortconcat('&'))
db.create_aggregate('identifiers_concat', 2, _IdConcat)
db.create_aggregate('aum_sortconcat', 4, _AumSortConcat)
db.create_collation('PYNOCASE', lambda a, b: (a.lower() > b.lower()) - (a.lower() < b.lower()))
db.create_collation('icucollate', lambda a, b: (a.casefold() > b.casefold()) - (a.casefold() < b.casefold()))
```
The test inserted `'The Hobbit'`. The trigger set `sort='Hobbit, The'` and a fresh uuid, and created the `books_pages_link` row; the `series` trigger also fired.

### Adding a book the way calibre does
In one transaction:
1. Insert into `books(title, series_index, author_sort, timestamp, pubdate, last_modified, path)`.
2. Upsert `authors(name, sort, link='')`, then `books_authors_link`.
3. Set `path = "Author Name/Title (id)"`. The directory and file names use calibre's sanitising rules: ASCII-ish, max lengths, `:` replaced, and so on.
4. Copy the file to `<lib>/<path>/<Title - Author>.epub`.
5. Insert `data(book, format='EPUB', uncompressed_size, name='Title - Author')`.
6. Write `cover.jpg` into the book directory and set `has_cover=1`.
7. Optionally write `metadata.opf` into the book directory; calibre uses it for recovery.
8. Insert `identifiers(book, type='isbn', val)`, `comments(book, text)`, `books_tags_link`, `books_series_link`, `books_publishers_link`, `books_languages_link`.

Rules:
- `last_modified` must be updated on every change.
- **Refuse to write while calibre is running** on that library. Check for a `metadata.db-journal`/`-wal` file or calibre's lock, or just warn.
- **Back up `metadata.db` first.**
- Safest MVP: **read-only import of calibre libraries**, with writing as an opt-in feature.

---

## 7. Online metadata lookup (no API key)

### Open Library **[tested live]**
Send a `User-Agent: AppName/1.0 (contact)` header. The informal limit is about 1 request per second per IP; higher limits need a UA with contact info.

- **Search:** `https://openlibrary.org/search.json?q=the+hobbit+tolkien&fields=key,title,author_name,first_publish_year,isbn,cover_i,edition_key,publisher,language,subject,number_of_pages_median&limit=10`. You can also query `title=…&author=…` or `isbn=…`. The response contains `docs[]` with `key: "/works/OL27482W"`, `title`, `author_name[]`, `first_publish_year`, `isbn[]`, `cover_i: 14627509`, `edition_key[]`, `publisher[]`, `language[]` (ISO 639-2, e.g. `eng`) and `subject[]`.
- **By ISBN (edition):** `https://openlibrary.org/isbn/9780261103344.json` (**302 redirect**, so follow it). It returns `title, subtitle, publishers[], publish_date, number_of_pages, covers[], works[{key}], authors[{key}], isbn_10/13, description`. `description` can be a string **or** `{type:'/type/text', value}`. Fetch the work (`/works/OL…W.json`) for `description` and `subjects`, and fetch authors via `/authors/OL…A.json` → `name`.
- **One-shot, flattened:** `https://openlibrary.org/api/books?bibkeys=ISBN:9780261103344&format=json&jscmd=data` returns `{"ISBN:…": {title, subtitle, authors:[{name,url}], publishers:[{name}], publish_date, number_of_pages, identifiers:{isbn_10,isbn_13,goodreads,librarything,oclc,openlibrary}, subjects:[{name}], cover:{small,medium,large}}}`. This is the easiest endpoint for ISBN lookup.
- **Covers:** `https://covers.openlibrary.org/b/{isbn|olid|id}/{value}-{S|M|L}.jpg`, e.g. `/b/id/14627509-L.jpg`. Add `?default=false` to get a 404 instead of a blank 1×1 GIF. The response is a 302 to an archive.org host, so follow redirects. Covers by ISBN/OLID are rate-limited (about 100 per 5 minutes per IP); covers by `id` are not.

### Google Books **[tested live: keyless access currently fails]**
- **Endpoints:** `https://www.googleapis.com/books/v1/volumes?q=isbn:9780261103344`, or `?q=intitle:hobbit+inauthor:tolkien&maxResults=10&printType=books`. `…/books/v1/volumes/{id}` fetches one volume.
- **Today a keyless request returned HTTP 429 `RESOURCE_EXHAUSTED`**, `quota_limit_value: "0"` for the shared anonymous consumer. **Plan on Google Books needing a user-supplied API key** (free from Cloud Console, `&key=…`), and make Open Library the default.
- **Response fields:** `items[].volumeInfo` contains `{title, subtitle, authors[], publisher, publishedDate, description, industryIdentifiers:[{type:'ISBN_13', identifier}], pageCount, categories[], language, imageLinks:{smallThumbnail, thumbnail}, seriesInfo?}`. To get a bigger cover, change `&zoom=1` to `zoom=0`/`2`, swap `http:` for `https:`, and drop `&edge=curl`.

Use `urllib.request` with a timeout in a thread, or `Soup-3.0` (typelib present) for async GLib-integrated HTTP. **Soup is the better fit with GTK.**

---

Sources for Send-to-Kindle (none is an official Amazon page):
- [the-ebook-reader blog (Dec 2022)](https://blog.the-ebook-reader.com/2022/12/20/send-to-kindle-apps-for-pc-and-mac-now-support-epub/)
- [cloudwards how-to](https://www.cloudwards.net/how-to-send-to-kindle-email/)
- [Michelle Pillow](https://michellepillow.com/send-ebook-kindle/)
