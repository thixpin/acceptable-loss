#!/usr/bin/env python3
"""Build "Acceptable Loss" as an A5 print PDF and a reflowable EPUB.

Usage:
    python3 build.py all        # pdf + epub + qa
    python3 build.py pdf
    python3 build.py epub
    python3 build.py qa

Sources: ../chapter-NN.md, ../Cover.png, book.json, css/, fonts/.
Outputs: ../dist/
"""
from __future__ import annotations

import argparse
import glob
import html
import json
import os
import re
import shutil
import sys
import unicodedata
import zipfile
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path

import markdown

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent
DIST = ROOT / "dist"

FONT_FILES = tuple(sorted(p.name for p in (HERE / "fonts").glob("*.ttf")))
FONTCONFIG_DEFAULTS = ("/opt/homebrew/etc/fonts/fonts.conf", "/usr/local/etc/fonts/fonts.conf", "/etc/fonts/fonts.conf")


def configure_fontconfig() -> Path:
    """Point fontconfig (used by WeasyPrint/Pango) at publish/fonts.

    WeasyPrint's own @font-face loading is unreliable on macOS, so the book fonts are
    registered through a private fonts.conf. The system copies of the Noto Myanmar fonts
    are rejected so the merged book fonts (which also carry Latin glyphs) always win.
    """
    if len(FONT_FILES) < 7:
        sys.exit("Book fonts missing in publish/fonts. Run: python3 publish/fonts.py")
    default = next((p for p in FONTCONFIG_DEFAULTS if Path(p).exists()), None)
    include = f'<include ignore_missing="yes">{default}</include>' if default else ""
    conf = (
        '<?xml version="1.0"?><!DOCTYPE fontconfig SYSTEM "fonts.dtd">\n'
        f'<fontconfig>{include}<dir>{HERE / "fonts"}</dir>'
        '<selectfont><rejectfont><glob>/System/Library/Fonts/NotoSerifMyanmar.ttc</glob>'
        '<glob>/System/Library/Fonts/NotoSansMyanmar.ttc</glob></rejectfont></selectfont>'
        '</fontconfig>\n'
    )
    conf_path = DIST / "src" / "fonts.conf"
    conf_path.parent.mkdir(parents=True, exist_ok=True)
    conf_path.write_text(conf, encoding="utf-8")
    os.environ["FONTCONFIG_FILE"] = str(conf_path)
    return conf_path

CHAPTER_HEAD_RE = re.compile(r"^#\s+(အခန်း\s*\([၀-၉0-9]+\))\s*-\s*(.+?)\s*$")


@dataclass
class Chapter:
    index: int            # 1-based position
    source: Path
    label: str            # e.g. "အခန်း (၁)"
    title: str            # e.g. "3 AM Standup"
    body_md: str          # markdown after the H1
    body_html: str = ""   # rendered body (without the chapter heading)
    plain_text: str = ""  # for QA statistics
    sections: list[str] = field(default_factory=list)

    @property
    def slug(self) -> str:
        return f"ch{self.index:02d}"

    @property
    def full_title(self) -> str:
        return f"{self.label} - {self.title}"


def load_meta() -> dict:
    with open(HERE / "book.json", encoding="utf-8") as f:
        meta = json.load(f)
    meta["_cover_path"] = (HERE / meta["cover"]).resolve()
    meta["_chapter_glob"] = str((HERE / meta["chapter_glob"]).resolve())
    return meta


def load_chapters(meta: dict) -> list[Chapter]:
    files = sorted(glob.glob(meta["_chapter_glob"]))
    if not files:
        sys.exit(f"No chapter files match {meta['_chapter_glob']}")
    chapters: list[Chapter] = []
    for i, path in enumerate(files, start=1):
        text = Path(path).read_text(encoding="utf-8")
        text = unicodedata.normalize("NFC", text).replace("\r\n", "\n")
        lines = text.split("\n")
        first = next((ln for ln in lines if ln.strip()), "")
        m = CHAPTER_HEAD_RE.match(first)
        if not m:
            sys.exit(f"{path}: first line is not a chapter heading: {first!r}")
        head_idx = lines.index(first)
        body = "\n".join(lines[head_idx + 1:]).strip("\n") + "\n"
        ch = Chapter(index=i, source=Path(path), label=m.group(1), title=m.group(2), body_md=body)
        ch.sections = re.findall(r"^##\s+(.+?)\s*$", body, flags=re.M)
        chapters.append(ch)
    return chapters


_PRE_RE = re.compile(r"<pre>(<code[^>]*>)(.*?)</code></pre>", re.S)


def _tag_wide_pre(m: re.Match) -> str:
    longest = max((len(html.unescape(ln)) for ln in m.group(2).split("\n")), default=0)
    cls = ' class="xwide"' if longest > 72 else (' class="wide"' if longest > 56 else "")
    return f"<pre{cls}>{m.group(1)}{m.group(2)}</code></pre>"


def render_markdown(md_text: str) -> str:
    md = markdown.Markdown(extensions=["fenced_code"], output_format="xhtml")
    out = md.convert(md_text)
    # Terminal blocks with long lines get a class so print CSS can set them smaller.
    # python-markdown emits <hr /> for '---'; it is kept as a scene break.
    return _PRE_RE.sub(_tag_wide_pre, out)


def html_to_text(fragment: str) -> str:
    text = re.sub(r"<[^>]+>", " ", fragment)
    text = html.unescape(text)
    return re.sub(r"[ \t]+", " ", text)


def render_chapters(chapters: list[Chapter]) -> None:
    for ch in chapters:
        ch.body_html = render_markdown(ch.body_md)
        ch.plain_text = html_to_text(ch.body_html)


def chapter_head_html(ch: Chapter) -> str:
    return (
        '<header class="chapter-head">'
        f'<p class="chapter-number">{html.escape(ch.label)}</p>'
        f'<h1>{html.escape(ch.title)}</h1>'
        '</header>'
    )


def front_matter_html(meta: dict) -> tuple[str, str]:
    """Return (title_page, copyright_page) inner HTML shared by both formats."""
    title = html.escape(meta["title"])
    subtitle = html.escape(meta.get("subtitle") or "")
    author = html.escape(meta["author"])
    publisher = html.escape(meta["publisher"])
    year = html.escape(meta["year"])
    isbn = html.escape(meta["isbn"])
    title_page = (
        '<div class="title-page">'
        f'<p class="book-title">{title}</p>'
        + (f'<p class="book-subtitle">{subtitle}</p>' if subtitle else "")
        + f'<p class="book-author">{author}</p>'
        + (f'<p class="book-publisher">{publisher}</p>' if publisher else "")
        + '</div>'
    )
    copyright_page = (
        '<div class="copyright-page">'
        f'<p>{title}</p>'
        f'<p>Copyright &#169; {year} {author}</p>'
        '<p>This work is licensed under the Creative Commons Attribution-NonCommercial-NoDerivatives 4.0 International License (CC BY-NC-ND 4.0). https://creativecommons.org/licenses/by-nc-nd/4.0/</p>'
        + (f'<p>Publisher: {publisher}</p>' if publisher else "")
        + (f'<p>ISBN: {isbn}</p>' if isbn else "") +
        '<p>Typeface: Noto Sans Myanmar</p>'
        '</div>'
    )
    return title_page, copyright_page


# ---------------------------------------------------------------- PDF

# Pango breaks Burmese only at spaces, which leaves large gaps in justified text.
# For the print layout, a zero-width space is inserted before every syllable-initial
# consonant (the "sylbreak" rule: a consonant not preceded by virama and not followed
# by asat or virama), giving the line breaker the same opportunities a Burmese
# typesetter would use. Syllables themselves are never split. Code blocks are left alone.
_SYLLABLE_BREAK_RE = re.compile("(?<=[\u1000-\u109f])(?<!\u1039)(?=[\u1000-\u1021](?![\u103a\u1039]))")
_PRE_OR_TAG_RE = re.compile(r"(<pre\b.*?</pre>|<code\b.*?</code>|<[^>]+>)", re.S)


# Print measure: A5 148 mm minus 22 mm inside and 17 mm outside margins, minus pre padding.
_PRE_USABLE_MM = 104.0
_PRE_MAX_PT = 8.3
_PRE_MIN_PT = 6.0    # below this, long lines wrap instead
_MONO_ADVANCE_EM = 0.6   # Noto Sans Mono advance width


def fit_pre_blocks(fragment: str) -> str:
    """Give each terminal block an explicit size so its longest line fits the measure."""
    def repl(m: re.Match) -> str:
        longest = max((len(html.unescape(ln)) for ln in m.group(2).split("\n")), default=1)
        pt = max(_PRE_MIN_PT, min(_PRE_MAX_PT, _PRE_USABLE_MM / (longest * _MONO_ADVANCE_EM * 25.4 / 72)))
        return f'<pre{m.group(1)} style="font-size: {pt:.2f}pt">{m.group(2)}</pre>'
    return re.sub(r"<pre([^>]*)>(.*?)</pre>", repl, fragment, flags=re.S)


def add_syllable_breaks(fragment: str) -> str:
    out = []
    for piece in _PRE_OR_TAG_RE.split(fragment):
        if not piece or piece.startswith("<"):
            out.append(piece)
        else:
            out.append(_SYLLABLE_BREAK_RE.sub("\u200b", piece))
    return "".join(out)

def build_pdf(meta: dict, chapters: list[Chapter], out_path: Path) -> Path:
    configure_fontconfig()
    from weasyprint import HTML, CSS
    import pdf_text_fix
    pdf_text_fix.install()

    title_page, copyright_page = front_matter_html(meta)
    cover_uri = meta["_cover_path"].as_uri()
    recto = "recto" if meta.get("recto_chapter_start") else ""

    toc_items = "".join(
        f'<li><a href="#{ch.slug}">{html.escape(ch.full_title)}</a></li>' for ch in chapters
    )
    parts = [
        '<!DOCTYPE html><html lang="my"><head><meta charset="utf-8"/>',
        f'<title>{html.escape(meta["title"])}</title></head>',
        f'<body data-title="{html.escape(meta["title"])}">',
        f'<div class="cover-page"><img src="{cover_uri}" alt="Cover"/></div>',
        f'<section class="front">{title_page}</section>',
        f'<section class="front">{copyright_page}</section>',
        f'<section class="front toc-page"><h1>မာတိကာ</h1><ol>{toc_items}</ol></section>',
    ]
    for ch in chapters:
        group = "group-a" if ch.index % 2 else "group-b"
        parts.append(
            f'<section class="chapter {group} {recto}" id="{ch.slug}">'
            + chapter_head_html(ch)
            + fit_pre_blocks(add_syllable_breaks(ch.body_html))
            + '</section>'
        )
    parts.append('</body></html>')
    doc_html = "\n".join(parts)

    css_files = [HERE / "css" / "common.css", HERE / "css" / "print.css"]
    if not meta.get("running_headers", True):
        css_files.append(CSS(string="@page :left{@top-left{content:none}} @page :right{@top-right{content:none}}"))

    src_dir = DIST / "src"
    src_dir.mkdir(parents=True, exist_ok=True)
    (src_dir / "book-print.html").write_text(doc_html, encoding="utf-8")

    out_path.parent.mkdir(parents=True, exist_ok=True)
    stylesheets = [CSS(filename=str(c)) if isinstance(c, Path) else c for c in css_files]
    HTML(string=doc_html, base_url=str(HERE / "css" / "print.css")).write_pdf(
        str(out_path), stylesheets=stylesheets
    )
    print(f"PDF written: {out_path}")
    return out_path


# ---------------------------------------------------------------- EPUB

XHTML_HEAD = (
    '<?xml version="1.0" encoding="utf-8"?>\n'
    '<!DOCTYPE html>\n'
    '<html xmlns="http://www.w3.org/1999/xhtml" xmlns:epub="http://www.idpf.org/2007/ops" '
    'xml:lang="{lang}" lang="{lang}">\n'
    '<head><meta charset="utf-8"/><title>{title}</title>'
    '<link rel="stylesheet" type="text/css" href="../css/common.css"/>'
    '<link rel="stylesheet" type="text/css" href="../css/epub.css"/></head>\n'
    '<body{body_attrs}>\n'
)
XHTML_TAIL = '\n</body>\n</html>\n'


def xhtml_doc(meta: dict, title: str, body: str, body_class: str = "", epub_type: str = "") -> str:
    attrs = ""
    if body_class:
        attrs += f' class="{body_class}"'
    if epub_type:
        attrs += f' epub:type="{epub_type}"'
    return XHTML_HEAD.format(lang=meta["language"], title=html.escape(title), body_attrs=attrs) + body + XHTML_TAIL


def build_epub(meta: dict, chapters: list[Chapter], out_path: Path) -> Path:
    title_page, copyright_page = front_matter_html(meta)
    cover_path = meta["_cover_path"]
    cover_ext = cover_path.suffix.lower().lstrip(".")
    cover_mime = {"png": "image/png", "jpg": "image/jpeg", "jpeg": "image/jpeg"}[cover_ext]
    cover_name = f"cover.{cover_ext}"
    modified = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    uid = meta["identifier"]

    # --- documents
    docs: list[tuple[str, str, str]] = []   # (id, href, xhtml)
    docs.append(("cover", "text/cover.xhtml", xhtml_doc(
        meta, meta["title"],
        f'<div class="cover"><img src="../images/{cover_name}" alt="Cover"/></div>',
        body_class="cover-body", epub_type="cover")))
    docs.append(("titlepage", "text/title.xhtml", xhtml_doc(meta, meta["title"], title_page, epub_type="titlepage")))
    docs.append(("copyright", "text/copyright.xhtml", xhtml_doc(meta, "Copyright", copyright_page, epub_type="copyright-page")))

    nav_items = "".join(
        f'<li><a href="{ch.slug}.xhtml">{html.escape(ch.full_title)}</a></li>' for ch in chapters
    )
    nav_xhtml = xhtml_doc(
        meta, "မာတိကာ",
        '<nav epub:type="toc" id="toc"><h1>မာတိကာ</h1><ol>' + nav_items + '</ol></nav>'
        '<nav epub:type="landmarks" hidden="hidden"><ol>'
        '<li><a epub:type="cover" href="cover.xhtml">Cover</a></li>'
        '<li><a epub:type="toc" href="nav.xhtml">Table of Contents</a></li>'
        f'<li><a epub:type="bodymatter" href="{chapters[0].slug}.xhtml">Start</a></li>'
        '</ol></nav>')
    docs.append(("nav", "text/nav.xhtml", nav_xhtml))

    for ch in chapters:
        body = f'<section epub:type="chapter" id="{ch.slug}">' + chapter_head_html(ch) + ch.body_html + '</section>'
        docs.append((ch.slug, f"text/{ch.slug}.xhtml", xhtml_doc(meta, ch.full_title, body, epub_type="bodymatter")))

    # --- NCX (EPUB 2 readers)
    nav_points = "".join(
        f'<navPoint id="np{ch.index}" playOrder="{ch.index}"><navLabel><text>{html.escape(ch.full_title)}</text></navLabel>'
        f'<content src="text/{ch.slug}.xhtml"/></navPoint>' for ch in chapters
    )
    ncx = (
        '<?xml version="1.0" encoding="utf-8"?>\n'
        '<ncx xmlns="http://www.daisy.org/z3986/2005/ncx/" version="2005-1">'
        f'<head><meta name="dtb:uid" content="{html.escape(uid)}"/><meta name="dtb:depth" content="1"/>'
        '<meta name="dtb:totalPageCount" content="0"/><meta name="dtb:maxPageNumber" content="0"/></head>'
        f'<docTitle><text>{html.escape(meta["title"])}</text></docTitle>'
        f'<navMap>{nav_points}</navMap></ncx>'
    )

    # --- OPF
    manifest = [
        f'<item id="cover-image" href="images/{cover_name}" media-type="{cover_mime}" properties="cover-image"/>',
        '<item id="css-common" href="css/common.css" media-type="text/css"/>',
        '<item id="css-epub" href="css/epub.css" media-type="text/css"/>',
        *(f'<item id="font-{i}" href="fonts/{fn}" media-type="font/ttf"/>' for i, fn in enumerate(FONT_FILES)),
        '<item id="ncx" href="toc.ncx" media-type="application/x-dtbncx+xml"/>',
    ]
    spine = []
    for doc_id, href, _ in docs:
        props = ' properties="nav"' if doc_id == "nav" else ""
        manifest.append(f'<item id="{doc_id}" href="{href}" media-type="application/xhtml+xml"{props}/>')
        linear = ' linear="no"' if doc_id == "cover" else ""
        spine.append(f'<itemref idref="{doc_id}"{linear}/>')

    opf = (
        '<?xml version="1.0" encoding="utf-8"?>\n'
        '<package xmlns="http://www.idpf.org/2007/opf" version="3.0" unique-identifier="bookid" xml:lang="{lang}">\n'
        '<metadata xmlns:dc="http://purl.org/dc/elements/1.1/">\n'
        '<dc:identifier id="bookid">{uid}</dc:identifier>\n'
        '<dc:title>{title}</dc:title>\n'
        '<dc:creator id="creator">{author}</dc:creator>\n'
        '<dc:language>{lang}</dc:language>\n'
        '{publisher_tag}'
        '<dc:date>{year}</dc:date>\n'
        '<meta property="dcterms:modified">{modified}</meta>\n'
        '<meta name="cover" content="cover-image"/>\n'
        '</metadata>\n'
        '<manifest>\n{manifest}\n</manifest>\n'
        '<spine toc="ncx">\n{spine}\n</spine>\n'
        '</package>\n'
    ).format(
        lang=meta["language"], uid=html.escape(uid), title=html.escape(meta["title"]),
        author=html.escape(meta["author"]),
        publisher_tag=(f'<dc:publisher>{html.escape(meta["publisher"])}</dc:publisher>\n' if meta.get("publisher") else ""),
        year=html.escape(meta["year"]), modified=modified,
        manifest="\n".join(manifest), spine="\n".join(spine),
    )

    container = (
        '<?xml version="1.0" encoding="utf-8"?>\n'
        '<container version="1.0" xmlns="urn:oasis:names:tc:opendocument:xmlns:container">'
        '<rootfiles><rootfile full-path="OEBPS/content.opf" media-type="application/oebps-package+xml"/></rootfiles>'
        '</container>\n'
    )

    # --- write unpacked source tree, then zip it
    src_dir = DIST / "src" / "epub"
    if src_dir.exists():
        shutil.rmtree(src_dir)
    (src_dir / "META-INF").mkdir(parents=True)
    for sub in ("text", "css", "fonts", "images"):
        (src_dir / "OEBPS" / sub).mkdir(parents=True)
    (src_dir / "mimetype").write_text("application/epub+zip", encoding="ascii")
    (src_dir / "META-INF" / "container.xml").write_text(container, encoding="utf-8")
    (src_dir / "OEBPS" / "content.opf").write_text(opf, encoding="utf-8")
    (src_dir / "OEBPS" / "toc.ncx").write_text(ncx, encoding="utf-8")
    for doc_id, href, content in docs:
        (src_dir / "OEBPS" / href).write_text(content, encoding="utf-8")
    shutil.copy(HERE / "css" / "common.css", src_dir / "OEBPS" / "css" / "common.css")
    shutil.copy(HERE / "css" / "epub.css", src_dir / "OEBPS" / "css" / "epub.css")
    for fn in FONT_FILES:
        shutil.copy(HERE / "fonts" / fn, src_dir / "OEBPS" / "fonts" / fn)
    shutil.copy(cover_path, src_dir / "OEBPS" / "images" / cover_name)

    out_path.parent.mkdir(parents=True, exist_ok=True)
    if out_path.exists():
        out_path.unlink()
    with zipfile.ZipFile(out_path, "w") as zf:
        zf.write(src_dir / "mimetype", "mimetype", compress_type=zipfile.ZIP_STORED)
        for path in sorted(src_dir.rglob("*")):
            if path.is_file() and path.name != "mimetype":
                zf.write(path, str(path.relative_to(src_dir)), compress_type=zipfile.ZIP_DEFLATED)
    print(f"EPUB written: {out_path}")
    return out_path


# ---------------------------------------------------------------- main

def main(argv: list[str]) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("target", choices=["all", "pdf", "epub", "qa"])
    args = ap.parse_args(argv)

    meta = load_meta()
    chapters = load_chapters(meta)
    render_chapters(chapters)
    print(f"Loaded {len(chapters)} chapters")

    pdf_path = DIST / "Acceptable-Loss-A5.pdf"
    epub_path = DIST / "Acceptable-Loss.epub"

    if args.target in ("all", "pdf"):
        build_pdf(meta, chapters, pdf_path)
    if args.target in ("all", "epub"):
        build_epub(meta, chapters, epub_path)
    if args.target in ("all", "qa"):
        import qa
        qa.run(meta, chapters, pdf_path, epub_path, DIST / "QA-REPORT.md")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
