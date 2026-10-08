// SPDX-License-Identifier: GPL-2.0-or-later
// SPDX-FileCopyrightText: 2026 Jack Tully

// The bridge between Python (widgets/book_view.py) and foliate-js (./foliate/, vendored).
//
// Python calls the methods of globalThis.reader through call_async_javascript_function,
// each with one JSON argument, and gets back what they resolve to. The page tells Python
// what happens with post(type, detail): one JSON message to the `bookcase` script message
// handler. The messages, and the methods, are listed in .claude/rules/reader.md.
//
// Keys never reach this page (the web view takes no focus: the reader window's key
// controller handles them). Clicks do: on a link (inside the book: followed, or a footnote
// shown; outside: Python opens it in the browser), on a highlight (Python shows its
// popover), else by thirds of the page in paginated mode (back, the chrome, forward).

import './foliate/view.js'
import { Overlayer } from './foliate/overlayer.js'
import { FootnoteHandler } from './foliate/footnotes.js'
import * as CFI from './foliate/epubcfi.js'

const post = (type, detail = {}) => {
    try {
        globalThis.webkit.messageHandlers.bookcase.postMessage(JSON.stringify({ type, ...detail }))
    } catch (e) {
        console.error(e)
    }
}

const debounce = (f, wait) => {
    let timeout
    return (...args) => {
        clearTimeout(timeout)
        timeout = setTimeout(() => f(...args), wait)
    }
}

// The highlight colours (library.COLORS): one colour for light pages and dark ones, the
// overlayer blending them (multiply on light paper, screen on dark).
const HIGHLIGHTS = {
    yellow: ['#f6d32d', '#c8a600'],
    green: ['#57e389', '#26a269'],
    blue: ['#62a0ea', '#3584e4'],
    pink: ['#f66151', '#e01b24'],
    purple: ['#c061cb', '#9141ac'],
}
const highlightColor = name =>
    (HIGHLIGHTS[name] ?? HIGHLIGHTS.yellow)[style?.theme?.dark ? 1 : 0]

// The style Python sends (reading.build_style()); the defaults until it does.
let style = {
    theme: { name: 'light', bg: '#ffffff', fg: '#1d1d1f', link: '#1c71d8', dark: false },
    font: '', fontSize: 18, lineHeight: 1.5, justify: true, hyphenate: true,
    publisherStyles: true, flow: 'paginated', maxColumns: 2, maxWidth: 720, margin: 8,
    animated: true,
}

// The CSS put into every section of the book.
const bookCSS = s => {
    const important = s.publisherStyles ? '' : ' !important'
    // Light paper keeps the book's colours; the other themes override them, or a book that
    // sets black text would be unreadable on dark paper.
    const force = s.theme.name !== 'light'
    return `
    @namespace epub "http://www.idpf.org/2007/ops";
    html {
        font-family: ${s.font || s.serif};
        color-scheme: ${s.theme.dark ? 'dark' : 'light'};
        font-size: ${s.fontSize}px !important;
        ${force ? `color: ${s.theme.fg} !important;` : `color: ${s.theme.fg};`}
        ${force ? 'background: transparent !important;' : ''}
    }
    ${force ? `
    body { background: transparent !important; color: inherit !important; }
    body *:not(a):not(img):not(svg):not(svg *):not(video) {
        color: inherit !important;
        background-color: transparent !important;
        border-color: color-mix(in srgb, currentColor 40%, transparent) !important;
    }
    a:link, a:visited { color: ${s.theme.link} !important; }
    ` : `
    a:link, a:visited { color: ${s.theme.link}; }
    `}
    ${s.font ? `
    body${s.publisherStyles ? '' : ' *'} { font-family: ${s.font}${important}; }
    code, pre, kbd, samp, tt { font-family: monospace !important; }
    ` : ''}
    p, li, blockquote, dd, div {
        line-height: ${s.lineHeight}${important};
    }
    ${s.publisherStyles ? '' : `
    body { margin: 0 !important; padding: 0 !important; }
    p, li, blockquote, dd {
        font-size: 1rem !important;
        letter-spacing: normal !important;
        word-spacing: normal !important;
    }
    p { margin-block: 0 !important; text-indent: 1.5em !important; }
    p + p { margin-block-start: 0 !important; }
    h1 + p, h2 + p, h3 + p, h4 + p, hr + p { text-indent: 0 !important; }
    `}
    p, li, blockquote, dd {
        text-align: ${s.justify ? 'justify' : 'start'}${important};
        -webkit-hyphens: ${s.hyphenate ? 'auto' : 'manual'};
        hyphens: ${s.hyphenate ? 'auto' : 'manual'};
        -webkit-hyphenate-limit-before: 3;
        -webkit-hyphenate-limit-after: 2;
        -webkit-hyphenate-limit-lines: 2;
        hanging-punctuation: allow-end last;
        widows: 2;
        orphans: 2;
    }
    [align="left"] { text-align: left; }
    [align="right"] { text-align: right; }
    [align="center"] { text-align: center; }
    [align="justify"] { text-align: justify; }
    pre { white-space: pre-wrap !important; }
    img, svg, video { max-width: 100%; }
    aside[epub|type~="endnote"],
    aside[epub|type~="footnote"],
    aside[epub|type~="note"],
    aside[epub|type~="rearnote"] {
        display: none;
    }
    ::selection { background: color-mix(in srgb, ${s.theme.link} 35%, transparent); }
    `
}

// A footnote's CSS: the book's text in the popup, smaller margins.
const footnoteCSS = s => bookCSS({ ...s, publisherStyles: true }) + `
    html, body { margin: 0 !important; }
    aside[epub|type~="endnote"], aside[epub|type~="footnote"],
    aside[epub|type~="note"], aside[epub|type~="rearnote"] { display: block; }
`

const $footnote = document.getElementById('footnote')
let view = null
let annotations = new Map() // cfi -> { cfi, color }
let bookmarks = []          // [cfi]
let lastDetail = null       // the last relocate's detail
let jumpedFrom = null       // the CFI before a jump (a link, goTo…), for the next relocate
let selectionShown = false
let annotationClicked = false
let searchToken = 0
let clickTimeout = null
let footerProgress = false  // the percentage at the foot: only while the bars are hidden

const toJSONTOC = items => (items ?? []).map(({ label, href, subitems }) => ({
    label: (label ?? '').trim(), href: href ?? '', subitems: toJSONTOC(subitems),
}))

// A range's (or an element's) rectangle in the page's coordinates, from inside a section's
// frame.
const pageRect = (doc, rect) => {
    const frame = doc?.defaultView?.frameElement
    const offset = frame ? frame.getBoundingClientRect() : { left: 0, top: 0 }
    const x = Math.max(0, Math.min(rect.left + offset.left, innerWidth))
    const y = Math.max(0, Math.min(rect.top + offset.top, innerHeight))
    return { x, y, width: Math.min(rect.width, innerWidth - x),
        height: Math.min(rect.height, innerHeight - y) }
}

// The rectangle of a selection or a highlight: its first line's, where a popover points.
const rangeRect = (doc, range) => {
    const rects = Array.from(range.getClientRects()).filter(r => r.width && r.height)
    const visible = rects.filter(r => {
        const p = pageRect(doc, r)
        return p.width > 0 && p.height > 0
    })
    const rect = visible[0] ?? rects[0] ?? range.getBoundingClientRect()
    return pageRect(doc, rect)
}

const isPaginated = () => style.flow !== 'scrolled' || view?.isFixedLayout

const applyStyle = () => {
    const root = document.documentElement
    root.classList.toggle('dark', !!style.theme.dark)
    root.style.setProperty('--bg', style.theme.bg)
    root.style.setProperty('--fg', style.theme.fg)
    root.style.setProperty('--link', style.theme.link)
    const renderer = view?.renderer
    if (!renderer) return
    if (!view.isFixedLayout) {
        renderer.setAttribute('flow', style.flow === 'scrolled' ? 'scrolled' : 'paginated')
        renderer.setAttribute('gap', `${style.margin}%`)
        renderer.setAttribute('margin', innerWidth < 500 ? '36px' : '48px')
        renderer.setAttribute('max-inline-size', `${style.maxWidth}px`)
        renderer.setAttribute('max-block-size', '1440px')
        renderer.setAttribute('max-column-count', style.maxColumns === 2 ? 2 : 1)
        renderer.setStyles?.(bookCSS(style))
    } else {
        renderer.setAttribute('zoom', String(fxlZoom))
        // A page zoomed wider or taller than the view scrolls from its edge, not its middle.
        renderer.style.justifyContent = 'safe center'
        renderer.style.alignItems = 'safe center'
        applySpread()
    }
    if (style.animated) renderer.setAttribute('animated', '')
    else renderer.removeAttribute('animated')
    // The highlights' colours follow the theme.
    for (const annotation of annotations.values()) view.addAnnotation(annotationValue(annotation))
}

// Fixed layouts (comics, picture books): the zoom ('fit-page', 'fit-width' or a scale, 1 the
// page's own size), panned by the wheel, by dragging and by the arrow keys when zoomed in;
// two pages side by side (the book's spreads, when the view is wider than tall) unless
// style.maxColumns is 1.
const FXL_MAX = 8 // times the fit-page scale
const FXL_STEP = 1.25
let fxlZoom = 'fit-page'
let fxlSpread = null // the spread the renderer was opened with
let fxlBookSpread       // the book's own
let fxlDrag = null
let fxlDragged = false

const fxlFrames = () => (view?.renderer?.getContents?.() ?? [])
    .map(({ doc }) => doc?.defaultView?.frameElement)
    .filter(frame => frame && frame.parentElement?.style.display !== 'none')

// The scale the pages are drawn at, and the scales fitting them to the page and the width.
const fxlScales = () => {
    const frames = fxlFrames()
    const { width, height } = view.renderer.getBoundingClientRect()
    let natural = 0
    let tallest = 0
    let scale = 1
    for (const frame of frames) {
        const w = parseFloat(frame.style.width) || 0
        const h = parseFloat(frame.style.height) || 0
        natural += w
        tallest = Math.max(tallest, h)
        if (w) scale = frame.getBoundingClientRect().width / w
    }
    if (!natural || !tallest) return { scale: 1, page: 1, width: 1 }
    return { scale, page: Math.min(width / natural, height / tallest), width: width / natural }
}

const fxlZoomed = () => view?.isFixedLayout && fxlZoom !== 'fit-page'

const fxlState = () => {
    const { scale, page } = view?.isFixedLayout ? fxlScales() : { scale: 1, page: 1 }
    return {
        fit: fxlZoom === 'fit-page' ? 'page' : fxlZoom === 'fit-width' ? 'width' : null,
        percent: Math.round(scale / (page || 1) * 100),
    }
}

const setZoom = zoom => {
    fxlZoom = zoom
    view.renderer.setAttribute('zoom', String(zoom))
    view.renderer.scrollTo?.(0, 0)
}

// Pans a zoomed page; false at the edge (nothing moved).
const fxlPan = (dx, dy) => {
    const r = view.renderer
    const left = r.scrollLeft
    const top = r.scrollTop
    r.scrollBy(dx, dy)
    return r.scrollLeft !== left || r.scrollTop !== top
}

const applySpread = async () => {
    if (!view?.isFixedLayout || !view.book) return
    const want = style.maxColumns === 1 ? 'none' : fxlBookSpread
    if (want === fxlSpread) return
    const renderer = view.renderer
    let section = null
    let before = -1
    try {
        section = view.book.sections[renderer.index] ?? null
        before = section ? renderer.getSpreadOf(section)?.index ?? -1 : -1
    } catch {
        section = null // nothing shown yet
    }
    fxlSpread = want
    view.book.rendition = { ...(view.book.rendition ?? {}), spread: want }
    renderer.open(view.book)
    if (!section) return
    const target = renderer.getSpreadOf(section)
    if (!target) return
    // The renderer keeps the spread it shows by its number: another first, then this one.
    if (target.index === before)
        await renderer.goToSpread(target.index ? target.index - 1 : target.index + 1)
    await renderer.goToSpread(target.index, target.side, 'navigation')
}

addEventListener('resize', debounce(() => {
    if (view?.renderer && !view.isFixedLayout)
        view.renderer.setAttribute('margin', innerWidth < 500 ? '36px' : '48px')
    hideFootnote()
}, 100))

const annotationValue = ({ cfi, color }) => ({ value: cfi, color })

const sectionOf = cfi => {
    try {
        return view.resolveCFI(cfi)?.index
    } catch {
        return undefined
    }
}

// The bookmark shown on the page, if any: one whose CFI lies in the visible range.
const visibleBookmark = cfi => {
    if (!cfi || !bookmarks.length) return null
    try {
        const start = CFI.collapse(cfi)
        const end = CFI.collapse(cfi, true)
        return bookmarks.find(b => CFI.compare(start, b) <= 0 && CFI.compare(b, end) <= 0)
            ?? null
    } catch {
        return null
    }
}

const startOf = cfi => {
    try {
        return cfi ? CFI.collapse(cfi) : null
    } catch {
        return null
    }
}

const fillMarginals = detail => {
    const renderer = view.renderer
    const heads = renderer.heads ?? []
    const feet = renderer.feet ?? []
    for (const el of [...heads, ...feet]) el.textContent = ''
    if (!style.marginals) return
    if (heads.length) heads[heads.length - 1].textContent = detail.tocItem?.label ?? ''
    if (heads.length > 1) heads[0].textContent = style.title ?? ''
    if (feet.length && footerProgress) {
        const percent = Math.floor((detail.fraction ?? 0) * 100 + 1e-9)
        feet[feet.length - 1].textContent = `${percent}%`
    }
}

// The renderer's reason for a relocation ('page', 'scroll', 'snap', 'navigation',
// 'selection', 'anchor'), which foliate-js's view leaves out of its own relocate event: read
// from the renderer's, in the capture phase (before the view's listener).
let lastReason = ''
const MOVES = new Set(['page', 'scroll', 'snap', 'navigation', 'selection'])

// Whether the sentence read aloud is on the page shown (its start in the visible range).
const ttsShown = visible => {
    if (!ttsRange || !visible) return false
    try {
        return visible.startContainer.ownerDocument === ttsRange.startContainer.ownerDocument
            && visible.comparePoint(ttsRange.startContainer, ttsRange.startOffset) === 0
    } catch {
        return false
    }
}

const onRelocate = ({ detail }) => {
    lastDetail = detail
    // Reading aloud, the reader moved (not the page turning to the sentence read, nor a
    // layout's re-anchoring): Python goes on from the new page.
    const reason = lastReason
    lastReason = ''
    const moved = ttsActive && !ttsTurningNow() && MOVES.has(reason) && !ttsShown(detail.range)
    if (moved) ttsMoved = true
    if (fxlZoomed() && reason === 'page') view.renderer.scrollTo?.(0, 0)
    if (selectionShown) clearSelection()
    hideFootnote()
    const renderer = view.renderer
    post('relocated', {
        fraction: detail.fraction ?? 0,
        cfi: detail.cfi ?? '',
        reason,
        chapter: detail.tocItem
            ? { label: (detail.tocItem.label ?? '').trim(), href: detail.tocItem.href ?? '' }
            : null,
        page: detail.pageItem?.label ?? null,
        section: detail.section ?? null,
        location: detail.location ?? null,
        time: detail.time ?? null,
        atStart: !!renderer.atStart,
        atEnd: !!renderer.atEnd,
        bookmark: visibleBookmark(detail.cfi),
        start: startOf(detail.cfi),
        jumpedFrom,
        canGoBack: view.history.canGoBack,
        canGoForward: view.history.canGoForward,
        ttsMoved: moved,
    })
    jumpedFrom = null
    fillMarginals(detail)
}

// A jump (a link, a contents entry, a search result, the scrubber): the place before it
// goes with the next relocate, for the window's "return" button.
const jump = async f => {
    jumpedFrom = lastDetail?.cfi ?? null
    try {
        return await f()
    } catch (e) {
        console.error(e)
        jumpedFrom = null
    }
}

const clearSelection = () => {
    for (const { doc } of view?.renderer?.getContents?.() ?? [])
        doc?.defaultView?.getSelection()?.removeAllRanges()
    if (selectionShown) {
        selectionShown = false
        post('selection', { cfi: null })
    }
}

const checkSelection = (doc, index) => {
    const selection = doc.getSelection()
    if (!selection || selection.isCollapsed || !selection.rangeCount) return
    const range = selection.getRangeAt(0)
    const text = selection.toString().trim()
    if (!text) return
    let cfi
    try {
        cfi = view.getCFI(index, range)
    } catch (e) {
        console.error(e)
        return
    }
    selectionShown = true
    post('selection', { cfi, text, rect: rangeRect(doc, range),
        fraction: lastDetail?.fraction ?? 0 })
}

// A click: by thirds of the page in paginated mode (back, the chrome, forward), the chrome
// in scrolled mode. Waits a moment, so a double click selecting a word turns nothing.
const onClick = (event, doc) => {
    if (event.button !== 0 || event.defaultPrevented) return
    if (!$footnote.hidden) {
        hideFootnote()
        return
    }
    if (event.target?.closest?.('a[href]')) return
    if (fxlDragged) {
        fxlDragged = false
        return
    }
    clearTimeout(clickTimeout)
    if (event.detail > 1) return
    const frame = doc?.defaultView?.frameElement
    const x = event.clientX + (frame ? frame.getBoundingClientRect().left : 0)
    clickTimeout = setTimeout(() => {
        if (annotationClicked) {
            annotationClicked = false
            return
        }
        const selection = (doc ?? document).getSelection?.()
        if (selection && !selection.isCollapsed) return
        if (selectionShown) {
            clearSelection()
            return
        }
        const third = innerWidth / 3
        if (!isPaginated() || (x >= third && x <= 2 * third)) post('toggle-chrome')
        else if (x < third) view.goLeft()
        else view.goRight()
    }, 220)
}

// The wheel turns pages in paginated mode: one page per notch, or per swipe of a touchpad.
let wheelDelta = 0
let wheelLockedUntil = 0
const resetWheel = debounce(() => wheelDelta = 0, 200)
const onWheel = event => {
    if (event.ctrlKey || !view || !isPaginated()) return
    event.preventDefault()
    if (fxlZoomed()) {
        fxlPan(event.deltaX, event.deltaY)
        return
    }
    const now = performance.now()
    const delta = Math.abs(event.deltaY) >= Math.abs(event.deltaX) ? event.deltaY : event.deltaX
    resetWheel()
    if (now < wheelLockedUntil) return
    wheelDelta += delta
    if (Math.abs(wheelDelta) < 24) return
    if (wheelDelta > 0) view.next()
    else view.prev()
    wheelDelta = 0
    wheelLockedUntil = now + 300
}

const onLoad = ({ detail: { doc, index } }) => {
    doc.addEventListener('pointerup', () => setTimeout(() => checkSelection(doc, index), 10))
    doc.addEventListener('selectionchange', debounce(() => {
        const selection = doc.getSelection()
        if (selectionShown && (!selection || selection.isCollapsed)) {
            selectionShown = false
            post('selection', { cfi: null })
        }
    }, 150))
    doc.addEventListener('click', event => onClick(event, doc))
    doc.addEventListener('wheel', onWheel, { passive: false })
    doc.addEventListener('contextmenu', event => event.preventDefault())
    doc.addEventListener('dragstart', event => event.preventDefault())
    if (view.isFixedLayout) {
        doc.addEventListener('pointerdown', event => {
            fxlDragged = false
            fxlDrag = fxlZoomed() && event.button === 0
                ? { x: event.screenX, y: event.screenY } : null
        })
        doc.addEventListener('pointermove', event => {
            if (!fxlDrag || !(event.buttons & 1)) return
            const dx = event.screenX - fxlDrag.x
            const dy = event.screenY - fxlDrag.y
            if (!fxlDragged && Math.hypot(dx, dy) < 6) return
            fxlDragged = true
            fxlPan(-dx, -dy)
            fxlDrag = { x: event.screenX, y: event.screenY }
            doc.getSelection()?.removeAllRanges()
        })
        doc.addEventListener('pointerup', () => fxlDrag = null)
    }
}

const footnotes = new FootnoteHandler()
// The footnote's view must be in the document to load: it goes into the popup, unseen until
// it has rendered.
footnotes.addEventListener('before-render', ({ detail: { view: noteView } }) => {
    $footnote.style.visibility = 'hidden'
    $footnote.replaceChildren(noteView)
    $footnote.hidden = false
    noteView.renderer.setAttribute('flow', 'scrolled')
    noteView.renderer.setAttribute('margin', '0px')
    noteView.renderer.setAttribute('gap', '6%')
    noteView.renderer.setStyles?.(footnoteCSS(style))
})
footnotes.addEventListener('render', ({ detail: { view: noteView } }) => {
    noteView.addEventListener('link', e => {
        e.preventDefault()
        hideFootnote()
        jump(() => view.goTo(e.detail.href))
    })
    noteView.addEventListener('external-link', e => {
        e.preventDefault()
        post('external-link', { href: e.detail.href_ ?? e.detail.href })
    })
    if (footnoteAnchor) showFootnoteAt(footnoteAnchor)
    $footnote.style.visibility = ''
})
let footnoteAnchor = null

const showFootnoteAt = rect => {
    const width = $footnote.offsetWidth || 440
    const height = $footnote.offsetHeight || 260
    const left = Math.max(12, Math.min(rect.x + rect.width / 2 - width / 2,
        innerWidth - width - 12))
    const below = rect.y + rect.height + 8
    const top = below + height < innerHeight - 8 ? below : Math.max(8, rect.y - height - 8)
    $footnote.style.left = `${left}px`
    $footnote.style.top = `${top}px`
}

const hideFootnote = () => {
    if ($footnote.hidden) return
    $footnote.hidden = true
    $footnote.replaceChildren()
}

const onLink = event => {
    const { a } = event.detail
    const doc = a?.ownerDocument
    footnoteAnchor = doc ? pageRect(doc, a.getBoundingClientRect()) : null
    const shown = footnotes.handle(view.book, event)
    if (shown) {
        // the handler took it: a footnote, shown where the reference is (on 'render')
        shown.catch(e => {
                console.error(e)
                hideFootnote()
                jump(() => view.goTo(event.detail.href))
            })
        return
    }
    event.preventDefault()
    jump(() => view.goTo(event.detail.href))
}

// Opening the book: by URL (fetched whole), or, for a large file, as a File from a hidden
// file input that Python answers through the view's run-file-chooser signal (the file read
// from disk as it is needed, never copied).
const pickFile = () => new Promise((resolve, reject) => {
    const input = document.createElement('input')
    input.type = 'file'
    input.hidden = true
    document.body.append(input)
    input.addEventListener('change', () => {
        const file = input.files?.[0]
        input.remove()
        if (file) resolve(file)
        else reject(new Error('No file was given'))
    })
    input.addEventListener('cancel', () => {
        input.remove()
        reject(new Error('No file was given'))
    })
    input.click()
})

// What Python calls before the book is open waits for it.
let opening = Promise.resolve(false)
const whenOpen = f => async (...args) => {
    await opening
    return f(...args)
}

// Reading aloud (widgets/read_aloud.py speaks, a sentence at a time): foliate-js's TTS
// gives a block's SSML with a mark before each sentence; ttsNext() returns the next
// sentence's text, highlighted and turned to, and goes on into the following sections;
// ttsPrev() the one before; ttsWord() underlines a word of the sentence being read. The
// highlights' keys start with foliate-js's search prefix, so a click on one is no
// annotation's. When the reader moves (a page turned, a jump) while reading, the relocated
// message says ttsMoved, and the next sentence asked for is the new page's first.
const TTS_KEY = 'foliate-search:bookcase-tts'
const TTS_WORD_KEY = 'foliate-search:bookcase-tts-word'
let ttsSentences = [] // [{ mark, text }]: the block being read
let ttsIndex = -1     // the sentence given last, in ttsSentences
let ttsTruncated = false // ttsSentences starts mid-block (read from the page shown)
let ttsActive = false
let ttsMoved = false  // the reader moved since the last sentence was given
let ttsTurning = 0    // the page is going to a section to read: not the reader moving
let ttsTurnedAt = -Infinity // when the page last turned to a sentence
const TTS_TURN_MS = 300
const ttsTurningNow = () => ttsTurning > 0 || performance.now() - ttsTurnedAt < TTS_TURN_MS
let ttsRange = null   // the sentence being read

const ssmlSentences = ssml => {
    if (!ssml) return []
    const doc = new DOMParser().parseFromString(ssml, 'application/xml')
    const sentences = []
    let current = { mark: null, text: '' }
    const walk = node => {
        for (const child of node.childNodes) {
            if (child.nodeType === Node.ELEMENT_NODE && child.localName === 'mark') {
                sentences.push(current)
                current = { mark: child.getAttribute('name'), text: '' }
            } else if (child.nodeType === Node.TEXT_NODE
                || child.nodeType === Node.CDATA_SECTION_NODE) current.text += child.nodeValue
            else if (child.nodeType === Node.ELEMENT_NODE) {
                if (child.localName === 'break') current.text += ' '
                walk(child)
            }
        }
    }
    walk(doc.documentElement)
    sentences.push(current)
    return sentences.map(({ mark, text }) => ({ mark, text: text.replace(/\s+/g, ' ').trim() }))
        .filter(({ text }) => text)
}

const ttsClearWord = () => {
    for (const { overlayer } of view?.renderer?.getContents?.() ?? [])
        overlayer?.remove(TTS_WORD_KEY)
}

const ttsClear = () => {
    ttsClearWord()
    for (const { overlayer } of view?.renderer?.getContents?.() ?? []) overlayer?.remove(TTS_KEY)
    ttsRange = null
}

const overlayerOf = range => {
    const contents = view.renderer.getContents?.() ?? []
    return (contents.find(c => c.doc === range.startContainer?.ownerDocument)
        ?? contents[0] ?? {}).overlayer
}

const ttsHighlight = range => {
    ttsClear()
    ttsRange = range
    overlayerOf(range)?.add(TTS_KEY, range, Overlayer.highlight, { color: style.theme.link })
    // (its promise does not always settle: the turn is the relocations of the next moment)
    ttsTurnedAt = performance.now()
    Promise.resolve(view.renderer.scrollToAnchor?.(range)).catch(e => console.error(e))
}

// The text nodes of a range, with the normalized text the sentence was given as (runs of
// white space as one space, trimmed): [normalized offset -> (node, offset)].
const textMap = range => {
    const root = range.commonAncestorContainer
    const doc = root.ownerDocument ?? root
    const walker = doc.createTreeWalker(root.nodeType === Node.TEXT_NODE ? root.parentNode
        : root, NodeFilter.SHOW_TEXT)
    const map = []
    let space = true // leading white space is trimmed
    for (let node = walker.nextNode(); node; node = walker.nextNode()) {
        if (!range.intersectsNode(node)) continue
        const start = node === range.startContainer ? range.startOffset : 0
        const end = node === range.endContainer ? range.endOffset : node.nodeValue.length
        for (let i = start; i < end; i++) {
            const white = /\s/.test(node.nodeValue[i])
            if (white && space) continue
            map.push({ node, offset: i, white })
            space = white
        }
    }
    return map
}

const ttsWordRange = offset => {
    if (!ttsRange) return null
    const map = textMap(ttsRange)
    if (offset < 0 || offset >= map.length || map[offset].white) return null
    let end = offset
    while (end + 1 < map.length && !map[end + 1].white) end++
    const range = ttsRange.startContainer.ownerDocument.createRange()
    range.setStart(map[offset].node, map[offset].offset)
    range.setEnd(map[end].node, map[end].offset + 1)
    return range
}

const ttsShow = sentence => {
    if (sentence.mark != null) view.tts.setMark(sentence.mark)
    ttsMoved = false
    return sentence.text
}

// Reading from the page shown (its first sentence) in the section shown.
const ttsInit = async fromPage => {
    ttsSentences = []
    ttsIndex = -1
    ttsMoved = false
    await view.initTTS('sentence', ttsHighlight)
    let ssml
    try {
        ssml = fromPage && lastDetail?.range ? view.tts.from(lastDetail.range) : view.tts.start()
        ttsTruncated = !!(fromPage && lastDetail?.range)
    } catch (e) {
        console.error(e)
        ssml = view.tts.start()
        ttsTruncated = false
    }
    ttsSentences = ssmlSentences(ssml)
}

// The block being read, whole (it was read from a mark on the page shown).
const ttsWholeBlock = () => {
    const back = view.tts.prev()
    return ssmlSentences(back ? view.tts.next() : view.tts.start())
}

const prevLinearSection = index => {
    const sections = view.book.sections ?? []
    for (let i = index - 1; i >= 0; i--)
        if (sections[i].linear !== 'no') return i
    return null
}

const nextLinearSection = index => {
    const sections = view.book.sections ?? []
    for (let i = index + 1; i < sections.length; i++)
        if (sections[i].linear !== 'no') return i
    return null
}

const reader = {
    open(args) {
        opening = reader._open(args)
        return opening
    },
    async _open({ url, location, fraction, style: newStyle, annotations: list, bookmarks: marks,
        fileInput }) {
        if (newStyle) style = newStyle
        applyStyle()
        annotations = new Map((list ?? []).map(a => [a.cfi, a]))
        bookmarks = marks ?? []
        view?.close()
        view?.remove()
        view = document.createElement('foliate-view')
        document.body.prepend(view)
        try {
            await view.open(fileInput ? await pickFile() : url)
        } catch (e) {
            console.error(e)
            post('error', { message: String(e?.message ?? e), kind: e?.constructor?.name ?? '' })
            return false
        }
        const { book } = view
        fxlZoom = 'fit-page'
        fxlBookSpread = book.rendition?.spread
        fxlSpread = fxlBookSpread
        book.transformTarget?.addEventListener('data', ({ detail }) => {
            detail.data = Promise.resolve(detail.data).catch(e => {
                console.error(new Error(`Failed to load ${detail.name}`, { cause: e }))
                return ''
            })
        })
        view.renderer.addEventListener('relocate', ({ detail }) => {
            lastReason = detail?.reason ?? ''
        }, { capture: true })
        view.addEventListener('relocate', onRelocate)
        view.addEventListener('load', onLoad)
        view.addEventListener('link', onLink)
        view.addEventListener('external-link', event => {
            event.preventDefault()
            post('external-link', { href: event.detail.href_ })
        })
        view.addEventListener('create-overlay', ({ detail: { index } }) => {
            for (const annotation of annotations.values())
                if (sectionOf(annotation.cfi) === index)
                    view.addAnnotation(annotationValue(annotation))
        })
        view.addEventListener('draw-annotation', ({ detail: { draw, annotation } }) => {
            draw(Overlayer.highlight, { color: highlightColor(annotation.color) })
        })
        view.addEventListener('show-annotation', ({ detail: { value, index, range } }) => {
            annotationClicked = true
            setTimeout(() => annotationClicked = false, 400)
            const doc = view.renderer.getContents().find(x => x.index === index)?.doc
            const rect = doc && range ? rangeRect(doc, range) : null
            post('annotation', { cfi: value, rect })
        })
        view.history.addEventListener('index-change', () => post('history', {
            canGoBack: view.history.canGoBack, canGoForward: view.history.canGoForward }))
        applyStyle()
        const metadata = book.metadata ?? {}
        const title = typeof metadata.title === 'string' ? metadata.title
            : Object.values(metadata.title ?? {})[0] ?? ''
        post('loaded', {
            title,
            dir: book.dir ?? 'ltr',
            fixedLayout: view.isFixedLayout,
            sectionFractions: view.getSectionFractions(),
            toc: toJSONTOC(book.toc),
            pageList: !!book.pageList?.length,
            pageItems: (book.pageList ?? []).slice(0, 20000).map(({ label, href }) => ({
                label: String(label ?? '').trim(), href: href ?? '' })),
            zoom: view.isFixedLayout ? { fit: 'page', percent: 100 } : null,
        })
        try {
            await view.init({ lastLocation: location || null, showTextStart: !location && !fraction })
            if (!location && fraction) await view.goToFraction(fraction)
        } catch (e) {
            console.error(e)
            await view.renderer.next?.()
        }
        return true
    },
    setStyle(newStyle) {
        style = newStyle
        applyStyle()
        if (lastDetail) fillMarginals(lastDetail)
        return true
    },
    next: () => view?.next(),
    prev: () => view?.prev(),
    goLeft: () => view?.goLeft(),
    goRight: () => view?.goRight(),
    // Up and Down: a little in scrolled mode, a page in paginated mode.
    scroll: ({ direction }) => fxlZoomed() && fxlPan(0, direction * innerHeight / 3) ? true
        : isPaginated()
        ? (direction > 0 ? view?.next() : view?.prev())
        : (direction > 0 ? view?.next(innerHeight / 8) : view?.prev(innerHeight / 8)),
    start: () => jump(() => view.renderer.firstSection()),
    end: () => jump(() => view.renderer.lastSection()),
    nextSection: () => view?.renderer.nextSection(),
    prevSection: () => view?.renderer.prevSection(),
    back: () => view?.history.back(),
    forward: () => view?.history.forward(),
    async goTo({ target }) {
        return !!(await jump(() => view.goTo(target)))
    },
    async goToFraction({ fraction }) {
        await jump(() => view.goToFraction(Math.max(0, Math.min(1, fraction))))
        return true
    },
    // Shows a search result: goes there and selects it.
    async select({ cfi }) {
        await jump(() => view.select(cfi))
        return true
    },
    clearSelection() {
        clearSelection()
        return true
    },
    // Streams the results: search-result messages, then search-done. A new search, or
    // clearSearch, abandons the one running.
    search({ query, matchCase = false, wholeWords = false }) {
        const token = ++searchToken
        ;(async () => {
            let count = 0
            const iterator = view.search({ query, matchCase, matchDiacritics: false,
                matchWholeWords: wholeWords,
                drawOptions: { color: style.theme.link, width: 2, radius: 3 } })
            for await (const result of iterator) {
                if (token !== searchToken) return
                if (result === 'done') break
                if (result.progress != null) post('search-progress', { progress: result.progress })
                else if (result.subitems) {
                    count += result.subitems.length
                    post('search-result', {
                        label: (result.label ?? '').trim(),
                        items: result.subitems.map(({ cfi, excerpt }) => ({
                            cfi, pre: excerpt?.pre ?? '', match: excerpt?.match ?? '',
                            post: excerpt?.post ?? '',
                        })),
                    })
                }
            }
            if (token === searchToken) post('search-done', { query, count })
        })().catch(e => {
            console.error(e)
            if (token === searchToken) post('search-done', { query, count: 0 })
        })
        return true
    },
    clearSearch() {
        searchToken++
        view?.clearSearch()
        return true
    },
    // The highlights on the page: setAnnotations replaces them all ([{cfi, color}]).
    async setAnnotations({ annotations: list }) {
        const next = new Map(list.map(a => [a.cfi, a]))
        for (const [cfi, old] of annotations)
            if (!next.has(cfi)) {
                annotations.delete(cfi)
                await view?.deleteAnnotation({ value: cfi })
            } else if (next.get(cfi).color === old.color) next.delete(cfi)
        for (const annotation of next.values()) {
            annotations.set(annotation.cfi, annotation)
            await view?.addAnnotation(annotationValue(annotation))
        }
        return true
    },
    async addAnnotation({ annotation }) {
        annotations.set(annotation.cfi, annotation)
        await view?.addAnnotation(annotationValue(annotation))
        return true
    },
    async removeAnnotation({ cfi }) {
        annotations.delete(cfi)
        await view?.deleteAnnotation({ value: cfi })
        return true
    },
    // The bookmarks' CFIs: the relocate message says which one is on the page.
    setBookmarks({ bookmarks: list }) {
        bookmarks = list ?? []
        return visibleBookmark(lastDetail?.cfi)
    },
    // Whether the page shows the percentage itself (the window's bars, which show it, hidden).
    showProgress({ visible }) {
        footerProgress = !!visible
        if (lastDetail) fillMarginals(lastDetail)
        return true
    },
    // Reading aloud: ttsStart() from the page shown, then ttsNext() for each sentence (its
    // text, or null at the end of the book), ttsPrev() for the one before, ttsWord() to
    // underline a word, ttsStop() to clear the highlight.
    async ttsStart() {
        if (!view?.renderer?.getContents?.()?.[0]?.doc || view.isFixedLayout) return false
        await ttsInit(true)
        ttsActive = true
        return true
    },
    async ttsNext() {
        ttsActive = true
        for (let guard = 0; guard < 10000; guard++) {
            const contents = view.renderer.getContents()?.[0]
            if (!contents?.doc) return null
            // The reader went to another section, or moved: read from the page shown.
            if (!view.tts || view.tts.doc !== contents.doc || ttsMoved) await ttsInit(true)
            if (ttsIndex + 1 < ttsSentences.length) return ttsShow(ttsSentences[++ttsIndex])
            const ssml = view.tts.next()
            if (ssml) {
                ttsSentences = ssmlSentences(ssml)
                ttsIndex = -1
                ttsTruncated = false
                continue
            }
            const next = nextLinearSection(contents.index)
            if (next == null) {
                ttsClear()
                return null
            }
            ttsTurning++
            try {
                await view.renderer.goTo({ index: next })
            } finally {
                ttsTurning--
            }
            await ttsInit(false)
        }
        return null
    },
    // The sentence before the one given last (the same one at the start of the book).
    async ttsPrev() {
        const contents = view.renderer.getContents()?.[0]
        if (!contents?.doc) return null
        if (!view.tts || view.tts.doc !== contents.doc || ttsMoved || ttsIndex < 0)
            return reader.ttsNext()
        ttsActive = true
        if (ttsIndex > 0) return ttsShow(ttsSentences[--ttsIndex])
        const mark = ttsSentences[ttsIndex]?.mark
        if (ttsTruncated) {
            const whole = ttsWholeBlock()
            const at = whole.findIndex(s => s.mark === mark)
            ttsSentences = whole
            ttsTruncated = false
            ttsIndex = Math.max(0, at)
            if (at > 0) return ttsShow(ttsSentences[--ttsIndex])
        }
        for (let guard = 0; guard < 10000; guard++) {
            const ssml = view.tts.prev()
            if (!ssml) break
            const sentences = ssmlSentences(ssml)
            if (!sentences.length) continue
            ttsSentences = sentences
            ttsIndex = sentences.length - 1
            return ttsShow(ttsSentences[ttsIndex])
        }
        // The section's first block: the last sentence of the section before.
        const prev = prevLinearSection(contents.index)
        if (prev == null) {
            ttsSentences = ssmlSentences(view.tts.start())
            ttsIndex = 0
            return ttsSentences[0] ? ttsShow(ttsSentences[0]) : null
        }
        ttsTurning++
        try {
            await view.renderer.goTo({ index: prev })
        } finally {
            ttsTurning--
        }
        await ttsInit(false)
        let last = ttsSentences
        for (let ssml = view.tts.next(); ssml; ssml = view.tts.next()) {
            const sentences = ssmlSentences(ssml)
            if (sentences.length) last = sentences
        }
        // the iterator is on the last block now; its marks are the last block's
        ttsSentences = last
        ttsIndex = last.length - 1
        return last.length ? ttsShow(last[ttsIndex]) : reader.ttsNext()
    },
    // Underlines the word at `offset` of the sentence being read (its text as given).
    ttsWord({ offset }) {
        ttsClearWord()
        const range = ttsWordRange(offset)
        if (!range) return false
        overlayerOf(range)?.add(TTS_WORD_KEY, range, Overlayer.underline,
            { color: style.theme.link, width: 3 })
        return true
    },
    ttsStop() {
        ttsSentences = []
        ttsIndex = -1
        ttsActive = false
        ttsMoved = false
        ttsClear()
        return true
    },
    // A fixed layout's zoom: action 'in', 'out', 'fit-page' or 'fit-width'; returns
    // { fit: 'page', 'width' or null, percent } (100 the page fitted), null for a book that
    // reflows.
    zoom({ action }) {
        if (!view?.isFixedLayout) return null
        if (action === 'fit-page' || action === 'fit-width') setZoom(action)
        else if (action === 'in' || action === 'out') {
            const { scale, page } = fxlScales()
            const next = action === 'in' ? scale * FXL_STEP : scale / FXL_STEP
            if (next <= page * 1.001) setZoom('fit-page')
            else setZoom(Math.min(next, page * FXL_MAX))
        }
        return fxlState()
    },
    getTOC: () => toJSONTOC(view?.book?.toc),
    getSectionFractions: () => view?.getSectionFractions() ?? [],
    // The places of highlights known only by their text (imported from a Kindle):
    // [{id, text}] -> [{id, cfi, fraction}] for those found, each the first match in the
    // book, from its first words to its last.
    async findTexts({ items }) {
        const sections = view?.book?.sections ?? []
        const { searchMatcher } = await import('./foliate/search.js')
        const { textWalker } = await import('./foliate/text-walker.js')
        const matcher = searchMatcher(textWalker, { defaultLocale: view.language,
            matchCase: false, matchDiacritics: true, matchWholeWords: false })
        const words = text => text.split(/\s+/).filter(Boolean)
        const left = new Map()
        for (const { id, text } of items) {
            const all = words(text ?? '')
            if (!all.length) continue
            const head = all.slice(0, 8).join(' ')
            const tail = all.length > 12 ? all.slice(-8).join(' ') : null
            left.set(id, { head, tail })
        }
        const fractions = view?.getSectionFractions() ?? []
        const found = []
        for (const [index, section] of sections.entries()) {
            if (!left.size) break
            if (!section.createDocument) continue
            const doc = await section.createDocument()
            for (const [id, { head, tail }] of left) {
                const first = matcher(doc, head).next()
                if (first.done) continue
                let range = first.value.range
                if (tail) for (const end of matcher(doc, tail)) {
                    if (end.range.compareBoundaryPoints(Range.START_TO_START, range) < 0)
                        continue
                    const whole = doc.createRange()
                    whole.setStart(range.startContainer, range.startOffset)
                    whole.setEnd(end.range.endContainer, end.range.endOffset)
                    range = whole
                    break
                }
                found.push({ id, cfi: view.getCFI(index, range), fraction: fractions[index] ?? 0 })
                left.delete(id)
            }
        }
        return found
    },
    // For scripts/reader_demo.py and the tests (through BookView.evaluate), not the window.
    _contents: () => view?.renderer?.getContents()?.[0] ?? {},
    _cfi: (index, range) => view.getCFI(index, range),
}

document.addEventListener('click', event => onClick(event, null))
document.addEventListener('wheel', onWheel, { passive: false })
document.addEventListener('contextmenu', event => event.preventDefault())

for (const [name, f] of Object.entries(reader))
    if (name !== 'open' && !name.startsWith('_')) reader[name] = whenOpen(f)

globalThis.reader = reader
post('ready')
