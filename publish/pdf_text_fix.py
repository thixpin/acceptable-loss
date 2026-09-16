"""Correct the PDF ToUnicode maps for complex-script fonts (Myanmar).

WeasyPrint maps every glyph to the text between its cluster start and the next
glyph's cluster start. A Myanmar syllable is one cluster made of several glyphs,
so all but the last glyph get an empty string and PDF readers show the glyph id
as a stray Latin letter when text is copied or searched. The same glyph also
ends up mapped to whatever syllable it happened to end first, so extracted text
is scrambled.

This hook rewrites each used glyph's entry from the font itself: a glyph in the
cmap maps to its code point; a shaped variant or ligature is decoded from its
Noto glyph name (e.g. ``ka.sub`` is virama + ka, ``medial_ra_wa.w2`` is the two
medials). Text comes out in visual glyph order, which is the normal result for
Myanmar PDFs, but with no invented characters. Rendering is not affected.
"""
from __future__ import annotations

import io
import re
import unicodedata

from fontTools.ttLib import TTFont

# Glyph-name pieces with no cmap entry of their own.
EXTRA = {
    "kinzi": "င်္",   # nga + asat + virama
    "sub": "္",                 # subjoined (stacked) consonant prefix
}
# Purely presentational name pieces.
MODIFIERS = {"tt", "bt", "bt2", "bt3", "w2", "w3", "mon", "shn", "skt", "dup", "spacing",
             "tall", "short", "narrow", "wide", "alt", "sub2", "sub3", "small", "low", "high"}

_cache: dict[int, dict[int, str]] = {}


def _glyph_map(font) -> dict[int, str]:
    """gid -> Unicode string for every glyph of the (unsubsetted) font."""
    key = font.hash
    if key in _cache:
        return _cache[key]
    tt = TTFont(io.BytesIO(font.file_content), fontNumber=font.index)
    names = tt.getGlyphOrder()
    by_name: dict[str, str] = {}
    for cp, gname in tt.getBestCmap().items():
        by_name.setdefault(gname, chr(cp))
    by_name.update(EXTRA)
    hmtx = tt["hmtx"]
    result = {}
    for gid, name in enumerate(names):
        text = _decode(name, by_name)
        if not text and (hmtx[name][0] == 0 or re.fullmatch(r"sp\d+", name)):
            # Spacer glyphs (Noto Myanmar's sp1..sp7, or any zero-width glyph) carry no
            # text of their own; HarfBuzz draws them for the syllable-break U+200B that
            # build.py inserts, so they map back to that character.
            text = "\u200b"
        # Latin ligature glyphs (fi, ffl ...) become their letters; Myanmar is unchanged.
        result[gid] = unicodedata.normalize("NFKC", text) if text else text
    _cache[key] = result
    return result


def _decode(name: str, by_name: dict[str, str]) -> str:
    if name in by_name:
        return by_name[name]
    if name.startswith("uni") and re.fullmatch(r"uni[0-9A-Fa-f]{4,}", name):
        hexes = name[3:]
        return "".join(chr(int(hexes[i:i + 4], 16)) for i in range(0, len(hexes), 4))
    if name.startswith("u") and re.fullmatch(r"u[0-9A-Fa-f]{4,6}", name):
        return chr(int(name[1:], 16))
    base, _, suffix = name.partition(".")
    tokens = [t for t in base.split("_") if t]
    if suffix:
        tokens += [t for t in suffix.split("_") if t]
    out = []
    i = 0
    while i < len(tokens):
        matched = False
        for j in range(len(tokens), i, -1):          # longest component first
            piece = "_".join(tokens[i:j])
            if piece in by_name:
                if piece == "sub":                    # virama goes before the consonant
                    out.insert(len(out) - 1 if out else 0, by_name[piece])
                else:
                    out.append(by_name[piece])
                i = j
                matched = True
                break
        if not matched:
            i += 1                                    # modifier or unknown piece
    return "".join(out)


def _fix_font(font) -> None:
    if font.bitmap or not font.to_unicode:
        return
    try:
        gmap = _glyph_map(font)
    except Exception:  # unknown font format: leave WeasyPrint's map alone
        return
    for gid in list(font.to_unicode):
        text = gmap.get(gid)
        if text:
            font.to_unicode[gid] = text
        elif not font.to_unicode[gid]:
            font.to_unicode[gid] = "�"   # never leave an empty target


def install() -> None:
    """Patch WeasyPrint so the fix runs right before font dictionaries are built."""
    import weasyprint.pdf as pdf_pkg
    import weasyprint.pdf.fonts as fonts_mod

    original = fonts_mod.build_fonts_dictionary
    if getattr(original, "_myanmar_fix", False):
        return

    def patched(pdf, fonts, *args, **kwargs):
        for font in fonts.values():
            _fix_font(font)
        return original(pdf, fonts, *args, **kwargs)

    patched._myanmar_fix = True
    fonts_mod.build_fonts_dictionary = patched
    for mod in (pdf_pkg,):
        if getattr(mod, "build_fonts_dictionary", None) is original:
            mod.build_fonts_dictionary = patched
