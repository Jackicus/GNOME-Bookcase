# foliate-js (vendored)

The e-book renderer of the reader page (`../reader.js` is our bridge over it).

- Upstream: https://github.com/johnfactotum/foliate-js
- Commit: `78914aef4466eb960965702401634c2cb348e9b1` (2026-05-01, "Use original hrefs for
  external links and add isExternal in fb2.js (#129)")
- Licence: MIT (`LICENSE`, © 2022 John Factotum). `vendor/zip.js` is zip.js (BSD-3-Clause),
  `vendor/fflate.js` is fflate (MIT).

The files are copied unchanged, each keeping its own header (tests/test_spdx.py skips this
folder). Only what the reader uses is here: view.js and its static imports (epubcfi.js,
progress.js, overlayer.js, text-walker.js), the formats (epub.js, mobi.js, fb2.js,
comic-book.js), the renderers (paginator.js, fixed-layout.js), search.js, tts.js,
footnotes.js and vendor/zip.js and vendor/fflate.js. pdf.js and PDF.js are left out: PDF
books open in the system's document viewer.

To update: check out the new commit, copy the same files over, update the commit above, and
read `git diff` of view.js and paginator.js for API changes against `../reader.js`.
