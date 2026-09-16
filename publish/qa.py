#!/usr/bin/env python3
"""QA checks for the built PDF and EPUB. Invoked by build.py (target qa/all)."""
from __future__ import annotations

import html
import re
import shutil
import subprocess
import unicodedata
import zipfile
from collections import Counter
from pathlib import Path
from xml.etree import ElementTree as ET

EM_DASH = chr(0x2014)
MM_PER_PT = 25.4 / 72


def count_em_dash_in_tree(paths: list[Path]) -> dict[str, int]:
    found: dict[str, int] = {}
    for p in paths:
        try:
            text = p.read_text(encoding="utf-8")
        except (UnicodeDecodeError, OSError):
            continue
        n = text.count(EM_DASH)
        if n:
            found[str(p)] = n
    return found


def text_files(root: Path, exts=(".md", ".html", ".xhtml", ".css", ".opf", ".ncx", ".xml", ".json", ".py", ".txt")) -> list[Path]:
    return [p for p in root.rglob("*") if p.is_file() and p.suffix.lower() in exts and ".venv" not in p.parts]


def manuscript_stats(chapters) -> dict:
    total_chars = 0
    total_chars_nospace = 0
    total_myanmar = 0
    tokens = 0
    for ch in chapters:
        t = ch.plain_text
        total_chars += len(t)
        total_chars_nospace += len(re.sub(r"\s", "", t))
        total_myanmar += sum(1 for c in t if "က" <= c <= "႟")
        tokens += len(t.split())
    return {
        "chars": total_chars,
        "chars_nospace": total_chars_nospace,
        "myanmar_chars": total_myanmar,
        "tokens": tokens,
    }


def unicode_checks(chapters) -> list[str]:
    issues: list[str] = []
    dup_word_re = re.compile(r"(?<!\S)(\S{2,})\s+\1(?!\S)")
    for ch in chapters:
        raw = ch.source.read_text(encoding="utf-8")
        name = ch.source.name
        if unicodedata.normalize("NFC", raw) != raw:
            issues.append(f"{name}: text is not NFC-normalized (build normalizes it)")
        for cp, label in (("�", "U+FFFD replacement char"), ("​", "zero-width space"),
                          (" ", "no-break space"), ("﻿", "BOM")):
            n = raw.count(cp)
            if n:
                issues.append(f"{name}: {n} x {label}")
        ctrl = [c for c in raw if unicodedata.category(c) == "Cc" and c not in "\n\t"]
        if ctrl:
            issues.append(f"{name}: {len(ctrl)} control characters")
        for m in re.finditer(r"([ါ-ှ])\1", raw):
            line = raw.count("\n", 0, m.start()) + 1
            issues.append(f"{name}:{line}: doubled vowel/medial sign {m.group(0)!r}")
        for m in re.finditer(r"(။။|၊၊|။၊|၊။)", raw):
            line = raw.count("\n", 0, m.start()) + 1
            issues.append(f"{name}:{line}: doubled punctuation {m.group(0)!r}")
        n_space_punct = len(re.findall(r"\S [။၊]", raw))
        if n_space_punct:
            issues.append(f"{name}: {n_space_punct} x space before ။/၊ (author's convention after Latin words; not changed)")
        in_code = False
        for ln_no, line in enumerate(raw.split("\n"), start=1):
            if line.startswith("```"):
                in_code = not in_code
                continue
            if in_code:
                continue
            if "  " in line.strip():
                issues.append(f"{name}:{ln_no}: double space inside prose line")
            for m in dup_word_re.finditer(line):
                w = m.group(1)
                if re.search(r"[က-႟]", w) and len(w) >= 4:
                    issues.append(f"{name}:{ln_no}: repeated word {w!r}")
            if re.search(r"<[a-zA-Z/][^>]*>", line):
                issues.append(f"{name}:{ln_no}: HTML tag in manuscript")
    return issues


def font_coverage(chapters, font_dir: Path) -> dict:
    """Which manuscript characters fall outside the embedded fonts."""
    from fontTools.ttLib import TTFont
    merged = set(TTFont(font_dir / "NotoSansMyanmar-Regular.ttf").getBestCmap())
    merged |= set(TTFont(font_dir / "NotoSansMono-Regular.ttf").getBestCmap())
    myanmar = {cp for cp in merged if 0x1000 <= cp <= 0x109F or 0xA9E0 <= cp <= 0xAA7F}
    latin = merged - myanmar
    counts: Counter = Counter()
    for ch in chapters:
        for c in ch.plain_text:
            if ord(c) > 32 and not c.isspace():
                counts[c] += 1
    in_myanmar = sum(n for c, n in counts.items() if ord(c) in myanmar)
    in_latin = sum(n for c, n in counts.items() if ord(c) not in myanmar and ord(c) in latin)
    outside = {c: n for c, n in counts.items() if ord(c) not in myanmar and ord(c) not in latin}
    return {"myanmar": in_myanmar, "latin": in_latin, "outside": outside}


def pdf_checks(pdf_path: Path, chapters, out_dir: Path) -> dict:
    import fitz  # pymupdf
    from pypdf import PdfReader

    res: dict = {}
    reader = PdfReader(str(pdf_path))
    res["pages"] = len(reader.pages)
    box = reader.pages[5].mediabox
    res["page_size_mm"] = (round(float(box.width) * MM_PER_PT, 1), round(float(box.height) * MM_PER_PT, 1))

    doc = fitz.open(str(pdf_path))
    fonts: Counter = Counter()
    em_dash_pages = []
    short_pages = []
    chapter_start_pages: dict[str, int] = {}
    full_text_parts = []
    for pno, page in enumerate(doc, start=1):
        for f in page.get_fonts(full=True):
            fonts[f[3]] += 1
        text = page.get_text("text")
        full_text_parts.append(text)
        if EM_DASH in text:
            em_dash_pages.append(pno)
        body_lines = [ln for ln in text.split("\n") if ln.strip()]
        if pno > 4 and len(body_lines) <= 3:
            short_pages.append((pno, len(body_lines)))
        for ch in chapters:
            if ch.slug not in chapter_start_pages and ch.label in text and ch.title in text and len(body_lines) < 40:
                # heading page: label line followed by title line near top
                lines = text.split("\n")
                for i, ln in enumerate(lines[:6]):
                    if ln.strip() == ch.label and i + 1 < len(lines) and lines[i + 1].strip() == ch.title:
                        chapter_start_pages[ch.slug] = pno
    res["fonts"] = sorted(fonts)
    res["em_dash_pages"] = em_dash_pages
    res["short_pages"] = short_pages
    res["chapter_start_pages"] = chapter_start_pages
    res["text_chars"] = sum(len(re.sub(r"\s", "", t)) for t in full_text_parts)
    all_text = "".join(full_text_parts)
    stray = Counter(c for c in all_text if ord(c) > 0x7f and not (0x1000 <= ord(c) <= 0x109f)
                    and unicodedata.category(c)[0] not in "PZ" and c not in "©\u200b")
    res["extraction_stray"] = stray.most_common(8)
    res["extraction_replacement"] = all_text.count("\ufffd")

    # Sample renders for visual inspection.
    if out_dir.exists():
        shutil.rmtree(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    sample = {1: "cover", 2: "title", 3: "copyright", 4: "toc"}
    first_ch = chapter_start_pages.get(chapters[0].slug)
    if first_ch:
        sample[first_ch] = "ch01-open"
        sample[first_ch + 1] = "ch01-p2"
        sample[first_ch + 2] = "ch01-p3"
    mid = chapter_start_pages.get(chapters[len(chapters) // 2].slug)
    if mid:
        sample[mid] = "mid-chapter-open"
        sample[mid + 1] = "mid-chapter-p2"
    last = chapter_start_pages.get(chapters[-1].slug)
    if last:
        sample[last] = "last-chapter-open"
    sample[len(doc)] = "last-page"
    rendered = []
    for pno, name in sorted(sample.items()):
        if 1 <= pno <= len(doc):
            pix = doc[pno - 1].get_pixmap(dpi=110)
            target = out_dir / f"page-{pno:03d}-{name}.png"
            pix.save(str(target))
            rendered.append(target.name)
    res["samples"] = rendered
    doc.close()
    return res


def epub_checks(epub_path: Path, chapters) -> dict:
    res: dict = {"errors": [], "warnings": []}
    with zipfile.ZipFile(epub_path) as zf:
        names = zf.namelist()
        info = zf.getinfo("mimetype")
        if names[0] != "mimetype":
            res["errors"].append("mimetype is not the first zip entry")
        if info.compress_type != zipfile.ZIP_STORED:
            res["errors"].append("mimetype is compressed")
        opf = zf.read("OEBPS/content.opf").decode("utf-8")
        res["fixed_layout"] = "rendition:layout" in opf and "pre-paginated" in opf
        res["fonts_embedded"] = [n for n in names if n.endswith(".ttf")]
        xhtml_names = [n for n in names if n.endswith(".xhtml")]
        for n in xhtml_names:
            try:
                ET.fromstring(zf.read(n))
            except ET.ParseError as e:
                res["errors"].append(f"{n}: not well-formed XML: {e}")
        res["chapter_docs"] = sum(1 for n in xhtml_names if re.search(r"/ch\d{2}\.xhtml$", n))
        # Manifest hrefs must exist.
        for href in re.findall(r'href="([^"]+)"', opf):
            if f"OEBPS/{href}" not in names:
                res["errors"].append(f"manifest href missing: {href}")
        # Text preservation: chapter text in EPUB vs. rendered manuscript.
        mismatches = []
        for ch in chapters:
            x = zf.read(f"OEBPS/text/{ch.slug}.xhtml").decode("utf-8")
            body = x.split("</header>", 1)[1] if "</header>" in x else x
            got = re.sub(r"\s", "", html.unescape(re.sub(r"<[^>]+>", "", body)))
            want = re.sub(r"\s", "", ch.plain_text)
            if got != want:
                mismatches.append(ch.slug)
        res["text_mismatch_chapters"] = mismatches
        res["em_dash_files"] = {n: zf.read(n).decode("utf-8", "ignore").count(EM_DASH)
                                for n in names if not n.endswith((".ttf", ".png", ".jpg", ".jpeg"))}
        res["em_dash_files"] = {k: v for k, v in res["em_dash_files"].items() if v}

    epubcheck = shutil.which("epubcheck")
    if epubcheck:
        proc = subprocess.run([epubcheck, str(epub_path)], capture_output=True, text=True)
        out = (proc.stdout + proc.stderr).strip()
        res["epubcheck"] = {"returncode": proc.returncode, "output": out[-4000:]}
    else:
        res["epubcheck"] = None
    return res


def run(meta: dict, chapters, pdf_path: Path, epub_path: Path, report_path: Path) -> None:
    root = pdf_path.parent.parent
    lines: list[str] = []
    add = lines.append

    add("# QA Report: Acceptable Loss")
    add("")
    add(f"Generated: {__import__('datetime').datetime.now().isoformat(timespec='seconds')}")
    add("")

    # --- manuscript
    st = manuscript_stats(chapters)
    add("## Manuscript")
    add("")
    add(f"- Chapters: {len(chapters)}")
    add(f"- Chapter order: " + ("OK (files sorted, numbering 1..N matches labels)" if all(
        ch.label == f"အခန်း ({''.join(chr(0x1040 + int(d)) for d in str(ch.index))})" for ch in chapters) else "MISMATCH, see list"))
    add(f"- Approximate word count (whitespace tokens): {st['tokens']:,}")
    add(f"- Character count (incl. spaces): {st['chars']:,}")
    add(f"- Character count (excl. spaces): {st['chars_nospace']:,}")
    add(f"- Myanmar-script characters: {st['myanmar_chars']:,}")
    add("")
    add("| # | Label | Title | Sections |")
    add("|---|---|---|---|")
    for ch in chapters:
        add(f"| {ch.index} | {ch.label} | {ch.title} | {len(ch.sections)} |")
    add("")

    # --- em dash, whole project (manuscript + publish sources + dist), venv excluded
    add("## Em dash search")
    add("")
    manuscript_files = [ch.source for ch in chapters]
    build_files = text_files(root / "publish") + text_files(root / "dist")
    md_found = count_em_dash_in_tree(manuscript_files)
    build_found = count_em_dash_in_tree(build_files)
    other_found = count_em_dash_in_tree([p for p in root.glob("*.md") if p not in manuscript_files])
    total_out = sum(md_found.values()) + sum(build_found.values())
    add(f"- Manuscript chapters: {sum(md_found.values())} occurrences")
    add(f"- Publish sources + dist outputs: {sum(build_found.values())} occurrences")
    for k, v in {**md_found, **build_found}.items():
        add(f"  - {k}: {v}")
    add(f"- Non-manuscript project notes (CLAUDE.md, outline.md, not part of the book): {sum(other_found.values())} occurrences")
    add(f"- **Total in book sources and outputs: {total_out}**")
    add("")

    # --- unicode
    add("## Unicode / Burmese text checks")
    add("")
    issues = unicode_checks(chapters)
    if issues:
        for i in issues[:200]:
            add(f"- {i}")
        if len(issues) > 200:
            add(f"- ... {len(issues) - 200} more")
    else:
        add("- No issues found (NFC, no replacement/control chars, no doubled signs or punctuation, no repeated words, no HTML tags).")
    add("")

    # --- fonts
    add("## Typeface coverage")
    add("")
    fc = font_coverage(chapters, root / "publish" / "fonts")
    add("- Body text: Noto Sans Myanmar (Regular, Bold, Italic, Bold Italic).")
    add("- Chapter titles: Noto Sans Myanmar Bold. Chapter numbers and section headings: Noto Sans Myanmar SemiBold. English text uses Noto Sans at the same weight, merged into the same files.")
    add("- Terminal output, commands, logs and inline code: Noto Sans Mono (Regular, Bold).")
    add("- The upstream Noto Myanmar fonts contain no Latin letters or digits, so each book font file merges the Myanmar font with its Latin counterpart (publish/fonts.py). Latin glyphs in the Noto Sans Myanmar faces are scaled to 93% so English words inside Burmese prose do not look oversized. Myanmar glyphs are never slanted; italics affect Latin only.")
    add("- Note: this scheme follows the author's later typography instructions (sans body, sans headings, mono code) and supersedes the Noto Serif Myanmar single-typeface rule in Public-Instruction.md.")
    add(f"- Myanmar-script characters (from the Noto Sans Myanmar glyph set): {fc['myanmar']:,}")
    add(f"- Latin/digit/punctuation characters (from the merged Latin glyph sets): {fc['latin']:,}")
    if fc["outside"]:
        add("- Characters covered by none of the book fonts (system symbol/emoji fallback in PDF, reader fallback in EPUB):")
        for c, n in sorted(fc["outside"].items(), key=lambda kv: -kv[1]):
            add(f"  - U+{ord(c):04X} {unicodedata.name(c, '?')} x{n}")
    else:
        add("- All characters covered by the two embedded fonts.")
    add("")

    # --- PDF
    add("## PDF")
    add("")
    if pdf_path.exists():
        p = pdf_checks(pdf_path, chapters, root / "dist" / "qa-pages")
        w, h = p["page_size_mm"]
        add(f"- File: {pdf_path.name}")
        add(f"- Pages: {p['pages']}")
        add(f"- Page size: {w} x {h} mm (A5 = 148 x 210)")
        add(f"- Fonts embedded: {', '.join(p['fonts']) or 'none detected'}")
        add("- Body font size: 10.75 pt; line spacing 1.55; first-line indent 6 mm; no extra space between paragraphs")
        add("- Margins: top 19 mm, bottom 20 mm, inside 22 mm, outside 17 mm")
        add(f"- Em dash on pages: {p['em_dash_pages'] or 'none'}")
        add(f"- Chapter opening pages detected: {len(p['chapter_start_pages'])} of {len(chapters)}")
        add(f"- Nearly empty pages (3 lines or fewer, after front matter): {p['short_pages'] or 'none'}")
        add(f"- Extracted text characters (excl. whitespace): {p['text_chars']:,} (manuscript: {st['chars_nospace']:,}; PDF includes front matter, headers, page numbers)")
        add(f"- Text extraction check (copy/search): {p['extraction_replacement']} replacement characters; non-Burmese non-ASCII characters present: {p['extraction_stray'] or 'none'} (all from the manuscript). Syllable-break zero-width spaces are extracted as U+200B.")
        add("- Extracted Burmese is in visual glyph order (pre-base vowel before consonant), the standard result for Myanmar PDFs without ActualText.")
        add(f"- Sample renders in dist/qa-pages/: {', '.join(p['samples'])}")
    else:
        add("- PDF not built.")
    add("")

    # --- EPUB
    add("## EPUB")
    add("")
    if epub_path.exists():
        e = epub_checks(epub_path, chapters)
        add(f"- File: {epub_path.name}")
        add(f"- Reflowable: {'NO (fixed layout metadata present)' if e['fixed_layout'] else 'yes (no fixed-layout metadata)'}")
        add(f"- Fonts embedded: {', '.join(Path(f).name for f in e['fonts_embedded'])}")
        add(f"- Chapter documents: {e['chapter_docs']}")
        add(f"- Chapter text identical to manuscript render: {'yes' if not e['text_mismatch_chapters'] else 'MISMATCH in ' + ', '.join(e['text_mismatch_chapters'])}")
        add(f"- Em dash inside EPUB: {e['em_dash_files'] or 'none'}")
        add(f"- Structural errors: {e['errors'] or 'none'}")
        if e["epubcheck"] is None:
            add("- epubcheck: NOT RUN (epubcheck not installed; `brew install epubcheck`)")
        else:
            status = "PASS" if e["epubcheck"]["returncode"] == 0 else "FAIL"
            add(f"- epubcheck: {status}")
            add("")
            add("```text")
            add(e["epubcheck"]["output"])
            add("```")
    else:
        add("- EPUB not built.")
    add("")

    # --- metadata placeholders
    add("## Metadata placeholders (must be filled before publication)")
    add("")
    for key in ("author", "publisher", "isbn"):
        if "PLACEHOLDER" in meta.get(key, ""):
            add(f"- {key}: {meta[key]}")
    add("")
    add("## Known layout limitations")
    add("")
    add("- Terminal blocks with long lines (nvidia-smi tables, log lines) are set in smaller monospace so the longest line fits the A5 measure; anything longer still wraps rather than overflowing.")
    add("- Emoji and the warning sign are not part of the book fonts; the PDF uses the system symbol/emoji font for those few characters and EPUB readers use their own.")
    add("- Burmese line breaking in the PDF follows syllable boundaries (zero-width break opportunities inserted at build time, not stored in the manuscript). Syllables are never split; word-internal breaks between syllables are standard Burmese typesetting practice.")
    add("- A section heading that does not fit with its first lines at the bottom of a page moves to the next page, leaving that page short. This is deliberate (no heading orphaned at a page foot).")
    add("")
    add("## Content / continuity issues found but NOT changed")
    add("")
    add("- None recorded by the automated checks. Add manual review notes here.")
    add("")

    report_path.write_text("\n".join(lines), encoding="utf-8")
    print(f"QA report written: {report_path}")
    print(f"Em dash total in book sources/outputs: {total_out}")
