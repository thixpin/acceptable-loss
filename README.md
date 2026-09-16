# Acceptable Loss (လက်ခံနိုင်သော ဆုံးရှုံးမှု)

A Burmese-language techno-thriller novel by သစ်ပင်, told in the first person by a Yangon DevOps engineer who discovers that the AI system he keeps running has started rewriting itself. Thirty chapters, published here one chapter at a time as each is finalised.

This repository holds the manuscript together with the Python pipeline that typesets it as an A5 print PDF and a reflowable EPUB.

## Repository layout

| Path | Contents |
|---|---|
| `chapter-NN.md` | Finalised chapters (Markdown, zero-padded numbering) |
| `drafts/` | Chapters still being revised, plus `outline.md` |
| `Cover.png` | Cover art |
| `publish/` | Build scripts, stylesheets, fonts and book metadata |
| `dist/` | Generated outputs (not committed) |
| `LICENSE` | CC BY-NC-ND 4.0 |

Only chapters in the repository root are included in a build. Move a chapter out of `drafts/` when it is ready.

## Requirements

- macOS with [Homebrew](https://brew.sh) (the setup script installs `pango` and `epubcheck`)
- Python 3.11 or newer (built and tested with 3.13)
- Network access once, to download the Noto fonts

## Setup

```bash
publish/setup.sh
```

This creates `.venv`, installs the Python dependencies, and runs `publish/fonts.py`, which downloads the official Noto releases and builds the book's font files.

## Build

```bash
source .venv/bin/activate
python3 publish/build.py all      # PDF + EPUB + QA report
python3 publish/build.py pdf
python3 publish/build.py epub
python3 publish/build.py qa
```

Outputs in `dist/`:

| File | Description |
|---|---|
| `Acceptable-Loss-A5.pdf` | A5 portrait print layout: running headers, page numbers, chapters opening on a right-hand page |
| `Acceptable-Loss.epub` | EPUB 3, reflowable, embedded fonts, validated with epubcheck |
| `QA-REPORT.md` | Chapter and character counts, page count, fonts, em dash search, epubcheck result, text-extraction check |
| `qa-pages/` | PNG renders of representative PDF pages |
| `src/` | Generated HTML, `fonts.conf`, and the unpacked EPUB tree |

## Typography

- Body text: Noto Sans Myanmar Regular, with true italic and bold faces
- Chapter titles: Noto Sans Myanmar Bold; section headings: SemiBold
- Terminal output, logs and inline code: Noto Sans Mono
- Noto's Myanmar fonts contain no Latin glyphs, so `publish/fonts.py` merges each Myanmar face with its Noto Latin counterpart into one file, scales the Latin glyphs to sit well inside Burmese prose, and builds oblique Myanmar glyphs for the italic faces
- Burmese line breaking in the PDF follows syllable boundaries; syllables are never split

Book metadata (title, author, year, identifier) lives in `publish/book.json`.

## Manuscript conventions

- One file per chapter, starting with `# အခန်း (N) - Title`
- `##` for scene breaks, `>` for AURA broadcasts and inner thoughts, fenced code blocks for terminal output
- Terminal commands, logs and diagnostics are English only
- The em dash character is not used anywhere in the book; the QA report enforces this

## License

Everything in this repository, the novel and the publishing toolchain, is licensed under [CC BY-NC-ND 4.0](https://creativecommons.org/licenses/by-nc-nd/4.0/). You may share it unmodified for non-commercial purposes with attribution. See `LICENSE`.

The fonts in `publish/fonts/` are Noto fonts under the SIL Open Font License. See `publish/fonts/LICENSE-OFL.txt`.
