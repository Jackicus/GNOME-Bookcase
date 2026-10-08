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
        renderer.setAttribute('zoom', 'fit-page')
    }
    if (style.animated) renderer.setAttribute('animated', '')
    else renderer.removeAttribute('animated')
    // The highlights' colours follow the theme.
    for (const annotation of annotations.values()) view.addAnnotation(annotationValue(annotation))
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

const onRelocate = ({ detail }) => {
    lastDetail = detail
    if (selectionShown) clearSelection()
    hideFootnote()
    const renderer = view.renderer
    post('relocated', {
        fraction: detail.fraction ?? 0,
        cfi: detail.cfi ?? '',
        reason: detail.reason ?? '',
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
        book.transformTarget?.addEventListener('data', ({ detail }) => {
            detail.data = Promise.resolve(detail.data).catch(e => {
                console.error(new Error(`Failed to load ${detail.name}`, { cause: e }))
                return ''
            })
        })
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
    scroll: ({ direction }) => isPaginated()
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
    getTOC: () => toJSONTOC(view?.book?.toc),
    getSectionFractions: () => view?.getSectionFractions() ?? [],
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
