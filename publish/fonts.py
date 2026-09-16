#!/usr/bin/env python3
"""Fetch the official Noto releases and build the book's font files.

The Noto Myanmar fonts are script-only (no Latin letters or digits), and the Noto
Latin fonts have no Myanmar glyphs. Each book face therefore merges one Myanmar font
with one Latin font into a single file, so every family covers both scripts:

  Body + headings  "Noto Sans Myanmar"   Regular / Bold / Italic / Bold Italic / SemiBold
                   = Noto Sans Myanmar + Noto Sans (Latin scaled to harmonise)
                   (body Regular, section headings SemiBold, chapter titles Bold)
  Code (mono)      "Noto Sans Mono"      Regular / Bold
                   = Noto Sans Mono + Noto Sans Myanmar (for Burmese inside terminal blocks)

Latin glyphs in the body and heading faces are scaled down slightly (LATIN_SCALE) so
English words inside Burmese prose do not look oversized. Myanmar glyphs are never
slanted by the font vendor (no Myanmar font ships an italic), so the italic faces use
Noto Sans's true Latin italics plus Myanmar glyphs obliqued by ITALIC_ANGLE at the
outline level, with GPOS mark anchors moved to match so vowel signs stay attached.

All fonts are SIL OFL 1.1; the license is copied next to the fonts.
Run:  python3 fonts.py           (needs network access once)
"""
from __future__ import annotations

import io
import json
import sys
import urllib.request
import zipfile
from pathlib import Path

import math

from fontTools.merge import Merger
from fontTools.pens.recordingPen import DecomposingRecordingPen
from fontTools.pens.transformPen import TransformPen
from fontTools.pens.ttGlyphPen import TTGlyphPen
from fontTools.ttLib import TTFont
from fontTools.ttLib.scaleUpem import scale_upem

HERE = Path(__file__).resolve().parent
OUT = HERE / "fonts"

LATIN_SCALE = 0.93   # Latin glyph size relative to Myanmar glyphs in serif/sans faces
ITALIC_ANGLE = 12.0  # degrees; Myanmar glyphs in the italic faces are obliqued by this much

RELEASE_API = {
    "myanmar": "https://api.github.com/repos/notofonts/myanmar/releases?per_page=60",
    "lgc": "https://api.github.com/repos/notofonts/latin-greek-cyrillic/releases?per_page=60",
}
FAMILIES = {  # family -> release repo
    "NotoSansMyanmar": "myanmar",
    "NotoSans": "lgc", "NotoSansMono": "lgc",
}

# output file, family name, primary (family, face), secondary (family, face), scale secondary, weight, italic
FACES = [
    ("NotoSansMyanmar-Regular.ttf",     "Noto Sans Myanmar",  ("NotoSansMyanmar", "Regular"),   ("NotoSans", "Regular"),     LATIN_SCALE, 400, False),
    ("NotoSansMyanmar-Bold.ttf",        "Noto Sans Myanmar",  ("NotoSansMyanmar", "Bold"),      ("NotoSans", "Bold"),        LATIN_SCALE, 700, False),
    ("NotoSansMyanmar-Italic.ttf",      "Noto Sans Myanmar",  ("NotoSansMyanmar", "Regular"),   ("NotoSans", "Italic"),      LATIN_SCALE, 400, True),
    ("NotoSansMyanmar-BoldItalic.ttf",  "Noto Sans Myanmar",  ("NotoSansMyanmar", "Bold"),      ("NotoSans", "BoldItalic"),  LATIN_SCALE, 700, True),
    ("NotoSansMyanmar-SemiBold.ttf",    "Noto Sans Myanmar",  ("NotoSansMyanmar", "SemiBold"),  ("NotoSans", "SemiBold"),    LATIN_SCALE, 600, False),
    ("NotoSansMono-Regular.ttf",        "Noto Sans Mono",     ("NotoSansMono", "Regular"),      ("NotoSansMyanmar", "Regular"), 1.0,      400, False),
    ("NotoSansMono-Bold.ttf",           "Noto Sans Mono",     ("NotoSansMono", "Bold"),         ("NotoSansMyanmar", "Bold"),    1.0,      700, False),
]
SUBFAMILY = {(400, False): "Regular", (700, False): "Bold", (400, True): "Italic",
             (700, True): "Bold Italic", (600, False): "SemiBold"}


def release_assets() -> dict[str, str]:
    urls: dict[str, str] = {}
    for repo, api in RELEASE_API.items():
        with urllib.request.urlopen(api, timeout=60) as r:
            releases = json.load(r)
        for rel in releases:
            for asset in rel.get("assets", []):
                fam = asset["name"].split("-v")[0]
                if FAMILIES.get(fam) == repo and fam not in urls and asset["name"].endswith(".zip"):
                    urls[fam] = asset["browser_download_url"]
    missing = [f for f in FAMILIES if f not in urls]
    if missing:
        sys.exit(f"No release asset found for {missing}")
    return urls


def fetch_zip(url: str) -> zipfile.ZipFile:
    print(f"Downloading {url}")
    with urllib.request.urlopen(url, timeout=300) as r:
        return zipfile.ZipFile(io.BytesIO(r.read()))


def extract(zf: zipfile.ZipFile, family: str, face: str) -> bytes:
    want = f"{family}/unhinted/ttf/{family}-{face}.ttf"
    for name in zf.namelist():
        if name.endswith(want):
            return zf.read(name)
    sys.exit(f"{want} not found in archive")


def scaled(path: Path, scale: float) -> Path:
    """Shrink every glyph of a font by `scale` while keeping unitsPerEm at 1000."""
    if scale == 1.0:
        return path
    font = TTFont(str(path))
    upem = font["head"].unitsPerEm
    scale_upem(font, int(round(upem * scale)))
    font["head"].unitsPerEm = upem
    out = path.with_name(path.stem + "-scaled.ttf")
    font.save(str(out))
    return out


def slanted(path: Path, angle: float) -> Path:
    """Oblique every glyph of a TrueType font (composites decomposed) and shift GPOS anchors."""
    font = TTFont(str(path))
    skew = math.tan(math.radians(angle))
    glyf, hmtx = font["glyf"], font["hmtx"]
    glyph_set = font.getGlyphSet()
    new_glyphs = {}
    for name in font.getGlyphOrder():
        rec = DecomposingRecordingPen(glyph_set)
        glyph_set[name].draw(rec)
        pen = TTGlyphPen(None)
        rec.replay(TransformPen(pen, (1, 0, skew, 1, 0, 0)))
        new_glyphs[name] = pen.glyph()
    for name, glyph in new_glyphs.items():
        glyph.recalcBounds(glyf)
        glyf[name] = glyph
        adv, _ = hmtx[name]
        hmtx[name] = (adv, getattr(glyph, "xMin", 0))

    def shift(anchor):
        if anchor is not None:
            anchor.XCoordinate = int(round(anchor.XCoordinate + skew * anchor.YCoordinate))

    if "GPOS" in font:
        for lookup in font["GPOS"].table.LookupList.Lookup:
            for sub in lookup.SubTable:
                if lookup.LookupType == 9:
                    sub = sub.ExtSubTable
                lt = getattr(sub, "LookupType", lookup.LookupType)
                if lt == 4:      # mark-to-base
                    for m in sub.MarkArray.MarkRecord:
                        shift(m.MarkAnchor)
                    for r in sub.BaseArray.BaseRecord:
                        for a in r.BaseAnchor:
                            shift(a)
                elif lt == 5:    # mark-to-ligature
                    for m in sub.MarkArray.MarkRecord:
                        shift(m.MarkAnchor)
                    for lig in sub.LigatureArray.LigatureAttach:
                        for comp in lig.ComponentRecord:
                            for a in comp.LigatureAnchor:
                                shift(a)
                elif lt == 6:    # mark-to-mark
                    for m in sub.Mark1Array.MarkRecord:
                        shift(m.MarkAnchor)
                    for r in sub.Mark2Array.Mark2Record:
                        for a in r.Mark2Anchor:
                            shift(a)
    out = path.with_name(path.stem + "-oblique.ttf")
    font.save(str(out))
    return out


def set_style(font: TTFont, family: str, ps_base: str, weight: int, italic: bool) -> None:
    subfamily = SUBFAMILY[(weight, italic)]
    bold = weight >= 700
    name = font["name"]
    full = family if subfamily == "Regular" else f"{family} {subfamily}"
    ps = f"{ps_base}-{subfamily.replace(' ', '')}"
    for rec in list(name.names):
        if rec.nameID in (16, 17):
            name.names.remove(rec)
    for nid, value in ((1, family), (2, subfamily if subfamily in ("Regular", "Bold", "Italic", "Bold Italic") else "Regular"),
                       (3, f"{ps};merged"), (4, full), (6, ps), (16, family), (17, subfamily)):
        name.setName(value, nid, 3, 1, 0x409)
        name.setName(value, nid, 1, 0, 0)
    os2 = font["OS/2"]
    fs = (1 if italic else 0) | (1 << 5 if bold else 0)
    if not italic and not bold:
        fs |= 1 << 6
    os2.fsSelection = (os2.fsSelection & ~0b1100001) | fs
    os2.usWeightClass = weight
    font["head"].macStyle = (1 if bold else 0) | (2 if italic else 0)
    font["post"].italicAngle = -ITALIC_ANGLE if italic else 0.0


def main() -> int:
    OUT.mkdir(exist_ok=True)
    urls = release_assets()
    zips = {fam: fetch_zip(url) for fam, url in urls.items()}

    tmp = OUT / "_src"
    tmp.mkdir(exist_ok=True)
    for out_name, family, (fam1, face1), (fam2, face2), scale2, weight, italic in FACES:
        p1 = tmp / f"{fam1}-{face1}.ttf"
        p2 = tmp / f"{fam2}-{face2}.ttf"
        p1.write_bytes(extract(zips[fam1], fam1, face1))
        p2.write_bytes(extract(zips[fam2], fam2, face2))
        if italic:
            p1 = slanted(p1, ITALIC_ANGLE)
        merged = Merger().merge([str(p1), str(scaled(p2, scale2))])
        set_style(merged, family, out_name.split("-")[0], weight, italic)
        merged.save(str(OUT / out_name))
        cmap = TTFont(str(OUT / out_name)).getBestCmap()
        print(f"{out_name}: {len(cmap)} codepoints, Myanmar {0x1000 in cmap}, Latin {0x61 in cmap}")
    for p in tmp.iterdir():
        p.unlink()
    tmp.rmdir()

    for name in zips["NotoSansMyanmar"].namelist():
        if name.endswith("OFL.txt"):
            (OUT / "LICENSE-OFL.txt").write_bytes(zips["NotoSansMyanmar"].read(name))
            break
    print(f"Fonts written to {OUT}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
