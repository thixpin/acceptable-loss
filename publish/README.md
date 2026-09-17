# Publishing "Acceptable Loss"

Builds the A5 print PDF and the reflowable EPUB from `../chapters/chapter-NN.md` and `../Cover.png`. Chapters in `../drafts/` are not included.

## Setup (once)

```bash
publish/setup.sh
```

This installs pango and epubcheck via Homebrew, creates `.venv`, installs the Python
dependencies, and runs `publish/fonts.py`, which downloads the official Noto releases and
builds the seven book font files in `publish/fonts/` (Noto Sans Myanmar for body and headings, Noto Sans Mono for code; each file covers Myanmar and Latin).

## Build

Activate the environment first (`source .venv/bin/activate`), then:

```bash
python3 publish/build.py all    # PDF + EPUB + QA report
python3 publish/build.py pdf
python3 publish/build.py epub
python3 publish/build.py qa
```

Outputs land in `../dist/`:

- `Acceptable-Loss-A5.pdf`: A5 portrait, print layout
- `Acceptable-Loss.epub`: EPUB 3, reflowable, validated with epubcheck
- `QA-REPORT.md`: counts, page size, fonts, em dash search, epubcheck result
- `qa-pages/`: PNG renders of representative PDF pages
- `src/`: the generated HTML, fonts.conf and unpacked EPUB tree

## Metadata

Edit `book.json`. Publisher and ISBN may be left empty; they are then omitted from the copyright page and EPUB metadata.
`recto_chapter_start: true` makes every chapter open on a right-hand page (adds blank pages).

## Files

- `build.py`: manuscript parsing, PDF (WeasyPrint) and EPUB (zipfile) generation
- `qa.py`: automated checks and the QA report
- `fonts.py`: font download and Myanmar + Latin merge (Noto Sans Myanmar, Noto Sans Mono)
- `pdf_text_fix.py`: corrects the PDF ToUnicode maps so copied and searched Burmese text has no stray characters
- `css/common.css`: typography shared by both formats
- `css/print.css`: A5 page geometry, running headers, page numbers, chapter openings
- `css/epub.css`: reflowable defaults
