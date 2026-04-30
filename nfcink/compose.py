"""
nfcink.compose — badge layout composer.

Produces a 240×416 RGB PIL Image from a portrait photo, person name,
and an optional company name or logo.  The result is passed directly
to quantise_image() / pixels_to_bytes() for NFC transfer.

Layout (top → bottom):
  [0   … 269]  photo — 240×270, scale-fitted on white
  [270 … 335]  name band — white background, bold all-caps name (≤54 px, 6 px padding each side)
  [336 … 341]  separator — 6 px yellow bar or Code 128B barcode (black on white, 12 px inset)
  [342 … 347]  gap — 6 px
  [348 … 415]  company area — logo or company name on white (≤62 px, 6 px bottom pad)
"""

from __future__ import annotations

import os

from PIL import Image, ImageDraw, ImageFont

from .constants import (
    SCREEN_WIDTH, SCREEN_HEIGHT, PHOTO_W, PHOTO_H,
    PALETTE, COLOR_YELLOW,
)

NAME_H    = 66                                        # name band height


# ── Font helpers ───────────────────────────────────────────────────────────────

def _find_font_path(bold: bool = False) -> str | None:
    candidates = (
        [
            "C:/Windows/Fonts/arialbd.ttf",
            "C:/Windows/Fonts/verdanab.ttf",
            "/usr/share/fonts/truetype/liberation/LiberationSans-Bold.ttf",
            "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf",
            "/System/Library/Fonts/Supplemental/Arial Bold.ttf",
        ] if bold else [
            "C:/Windows/Fonts/arial.ttf",
            "C:/Windows/Fonts/verdana.ttf",
            "/usr/share/fonts/truetype/liberation/LiberationSans-Regular.ttf",
            "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
            "/System/Library/Fonts/Supplemental/Arial.ttf",
        ]
    )
    return next((p for p in candidates if os.path.exists(p)), None)


_LINE_GAP = 4   # pixels between wrapped lines


def _fit_font_wrapped(
    draw: ImageDraw.ImageDraw,
    text: str,
    max_w: int,
    max_h: int,
    font_path: str | None,
    start: int = 72,
) -> tuple[ImageFont.FreeTypeFont | ImageFont.ImageFont, list[str]]:
    """
    Find the largest font size for text that fits in max_w × max_h.
    Tries single-line and all 2-line word-boundary splits; returns (font, lines).
    For a given size, the most balanced 2-line split is preferred.
    """
    words = text.split()
    candidates: list[list[str]] = [[text]]
    for i in range(1, len(words)):
        candidates.append([" ".join(words[:i]), " ".join(words[i:])])
    # Sort 2-line splits by balance (closest char-length halves first)
    candidates[1:] = sorted(candidates[1:], key=lambda s: abs(len(s[0]) - len(s[1])))

    for size in range(start, 7, -1):
        if font_path:
            try:
                font: ImageFont.FreeTypeFont | ImageFont.ImageFont = ImageFont.truetype(font_path, size)
            except OSError:
                font = ImageFont.load_default()
        else:
            font = ImageFont.load_default()

        for lines in candidates:
            bboxes = [draw.textbbox((0, 0), ln, font=font) for ln in lines]
            w = max(b[2] - b[0] for b in bboxes)
            h = sum(b[3] - b[1] for b in bboxes) + _LINE_GAP * (len(lines) - 1)
            if w <= max_w and h <= max_h:
                return font, lines

        if not font_path:
            break   # default font has no size parameter

    return ImageFont.load_default(), [text]   # best-effort fallback


def _lines_height(
    draw: ImageDraw.ImageDraw,
    lines: list[str],
    font: ImageFont.FreeTypeFont | ImageFont.ImageFont,
) -> int:
    """Total pixel height of stacked lines including gaps."""
    bboxes = [draw.textbbox((0, 0), ln, font=font) for ln in lines]
    return sum(b[3] - b[1] for b in bboxes) + _LINE_GAP * (len(lines) - 1)


def _draw_lines_centered(
    draw: ImageDraw.ImageDraw,
    lines: list[str],
    font: ImageFont.FreeTypeFont | ImageFont.ImageFont,
    cx: int,
    y_top: int,
    color: tuple[int, int, int],
) -> None:
    """Draw lines horizontally centered around cx, stacked downward from y_top."""
    y = y_top
    for line in lines:
        bbox = draw.textbbox((0, 0), line, font=font)
        lw = bbox[2] - bbox[0]
        draw.text((cx - lw // 2 - bbox[0], y - bbox[1]), line, fill=color, font=font)
        y += (bbox[3] - bbox[1]) + _LINE_GAP


# ── Image helpers ──────────────────────────────────────────────────────────────

def _scale_fit(img: Image.Image, w: int, h: int) -> Image.Image:
    """Scale img to fit within w×h (letterbox), centered on white."""
    img   = img.convert("RGB")
    ratio = min(w / img.width, h / img.height)
    new_w = max(round(img.width  * ratio), 1)
    new_h = max(round(img.height * ratio), 1)
    img   = img.resize((new_w, new_h), Image.LANCZOS)
    out   = Image.new("RGB", (w, h), (255, 255, 255))
    out.paste(img, ((w - new_w) // 2, (h - new_h) // 2))
    return out


def _paste_layer(canvas: Image.Image, layer: Image.Image,
                 pos: tuple[int, int]) -> None:
    """Paste layer onto canvas, handling RGBA transparency."""
    if layer.mode == "RGBA":
        canvas.paste(layer, pos, mask=layer.split()[3])
    else:
        canvas.paste(layer.convert("RGB"), pos)



# ── Code 128B barcode helpers ───────────────────────────────────────────────────
#
# The 216-pixel-wide separator band (8 px tall) renders a standard Code 128
# type-B barcode.  Black bars on white; module width auto-scales to fill the
# available width.  Supports ASCII 32–126; up to 14 characters fit at 1 px/module.
#
# Symbol layout (modules):
#   10 quiet | 11 Start-B | 11×N data | 11 check | 13 Stop | 10 quiet
#   = 55 + 11×N total.  Max chars at 1 px/module in 216 px: (216-55)//11 = 14.
#
# Reference: ISO/IEC 15417.

# Binary patterns for Code 128 symbols 0–106 (1=bar, 0=space, left to right).
# Regular symbols (0–105): 11 modules each.  Stop (106): 13 modules.
_C128_BITS: tuple[str, ...] = (
    "11011001100", "11001101100", "11001100110", "10010011000",  #  0- 3
    "10010001100", "10001001100", "10011001000", "10011000100",  #  4- 7
    "10001100100", "11001001000", "11001000100", "11000100100",  #  8-11
    "10110011100", "10011011100", "10011001110", "10111001100",  # 12-15
    "10011101100", "10011100110", "11001110010", "11001011100",  # 16-19
    "11001001110", "11011100100", "11001110100", "11101101110",  # 20-23
    "11101001100", "11100101100", "11100100110", "11101100100",  # 24-27
    "11100110100", "11100110010", "11011011000", "11011000110",  # 28-31
    "11000110110", "10100011000", "10001011000", "10001000110",  # 32-35
    "10110001000", "10001101000", "10001100010", "11010001000",  # 36-39
    "11000101000", "11000100010", "10110111000", "10110001110",  # 40-43
    "10001101110", "10111011000", "10111000110", "10001110110",  # 44-47
    "11101110110", "11010001110", "11000101110", "11011101000",  # 48-51
    "11011100010", "11011101110", "11101011000", "11101000110",  # 52-55
    "11100010110", "11101101000", "11101100010", "11100011010",  # 56-59
    "11101111010", "11001000010", "11110001010", "10100110000",  # 60-63
    "10100001100", "10010110000", "10010000110", "10000101100",  # 64-67
    "10000100110", "10110010000", "10110000100", "10011010000",  # 68-71
    "10011000010", "10000110100", "10000110010", "11000010010",  # 72-75
    "11001010000", "11110111010", "11000010100", "10001111010",  # 76-79
    "10100111100", "10010111100", "10010011110", "10111100100",  # 80-83
    "10011110100", "10011110010", "11110100100", "11110010100",  # 84-87
    "11110010010", "11011011110", "11011110110", "11110110110",  # 88-91
    "10101111000", "10100011110", "10001011110", "10111101000",  # 92-95
    "10111100010", "11110101000", "11110100010", "10111011110",  # 96-99
    "10111101110", "11101011110", "11110101110",                 # 100-102
    "11010000100", "11010010000", "11010011110",                 # 103=Start A, 104=Start B, 105=Start C
    "1100011101011",                                             # 106=Stop (13 modules)
)


def _draw_code128b(
    canvas: Image.Image,
    text: str,
    x0: int, y0: int, x1: int, y1: int,
    bar_color: tuple[int, int, int] = (0, 0, 0),
    fill_color: tuple[int, int, int] = (255, 255, 255),
    pad_char: str = "*",
) -> None:
    """
    Draw a Code 128 type-B barcode into the band [x0..x1] × [y0..y1].

    Quiet zones are always white.  The data area (between quiet zones) is
    filled with fill_color; bars are drawn in bar_color.  Module width is
    maximised to fill the available pixel width; the symbol is centered.
    Supports ASCII 32–126.  Raises ValueError for unsupported characters or
    text that is too long to fit at minimum (1 px) module width.
    """
    band_w = x1 - x0 + 1
    max_chars = (band_w - 55) // 11   # 55 fixed modules + 11 per data char
    if len(text) > max_chars:
        raise ValueError(
            f"Barcode text too long: {len(text)} chars (max {max_chars} in {band_w} px)"
        )
    for ch in text:
        if not (32 <= ord(ch) <= 126):
            raise ValueError(f"Code 128B: unsupported character {ch!r} (ord {ord(ch)})")

    # Pad symmetrically with pad_char to use all available modules.
    pad_total = max_chars - len(text)
    pad_l = pad_char * (pad_total // 2)
    pad_r = pad_char * (pad_total - len(pad_l))
    text = pad_l + text + pad_r

    vals = [ord(ch) - 32 for ch in text]
    check = (104 + sum(v * i for i, v in enumerate(vals, 1))) % 103

    bits = (
        "0" * 10
        + _C128_BITS[104]
        + "".join(_C128_BITS[v] for v in vals)
        + _C128_BITS[check]
        + _C128_BITS[106]
        + "0" * 10
    )

    mod_px  = max(1, band_w // len(bits))
    x_start = x0 + (band_w - len(bits) * mod_px) // 2

    draw = ImageDraw.Draw(canvas)
    # White for entire band (quiet zones)
    draw.rectangle([(x0, y0), (x1, y1)], fill=(255, 255, 255))
    # fill_color for the data area between quiet zones
    data_x0 = x_start + 10 * mod_px
    data_x1 = x_start + (len(bits) - 10) * mod_px - 1
    if data_x0 <= data_x1:
        draw.rectangle([(data_x0, y0), (data_x1, y1)], fill=fill_color)
    # bar_color bars
    for i, bit in enumerate(bits):
        if bit == "1":
            bx = x_start + i * mod_px
            draw.rectangle([(bx, y0), (bx + mod_px - 1, y1)], fill=bar_color)


# ── Public API ─────────────────────────────────────────────────────────────────

def compose_badge(
    photo_path:   str,
    person_name:  str,
    company_name: str | None = None,
    logo_path:    str | None = None,
    barcode:      str | None = None,
) -> Image.Image:
    """
    Compose a 240×416 RGB badge image.

    photo_path   — portrait photo; scale-fitted to 240×270 on white
    person_name  — displayed bold all-caps in the name band (black on white)
    company_name — regular text in the company area (ignored if logo_path given)
    logo_path    — path to a logo image; scaled to fit the company area
    barcode      — ASCII 32–126 text (up to 14 chars) rendered as a Code 128B
                   barcode in the 8-px separator line (red bars on yellow fill)

    Returns a PIL Image (RGB, 240×416).
    """
    bold_fp = _find_font_path(bold=True)
    reg_fp  = _find_font_path(bold=False)

    canvas = Image.new("RGB", (SCREEN_WIDTH, SCREEN_HEIGHT), (255, 255, 255))
    draw   = ImageDraw.Draw(canvas)

    # ── Photo (scale-fit to PHOTO_W × PHOTO_H) ───────────────────────────────
    photo = _scale_fit(Image.open(photo_path), PHOTO_W, PHOTO_H)
    canvas.paste(photo, (0, 0))

    # ── Name band — white background, black text, colored separator line ─────
    band_y0 = PHOTO_H
    band_y1 = band_y0 + NAME_H - 1            # 319
    draw.rectangle([(0, band_y0), (SCREEN_WIDTH - 1, band_y1)], fill=(255, 255, 255))

    name_text              = person_name.upper()
    name_font, name_lines  = _fit_font_wrapped(draw, name_text,
                                               SCREEN_WIDTH - 20, NAME_H - 12,
                                               bold_fp, start=68)
    name_h = _lines_height(draw, name_lines, name_font)
    _draw_lines_centered(draw, name_lines, name_font,
                         SCREEN_WIDTH // 2, band_y0 + (NAME_H - name_h) // 2, (0, 0, 0))

    # ── Separator line: name/company, 12px inset, 4px thick ─────────────────
    co_y0 = band_y0 + NAME_H
    sep_x1 = SCREEN_WIDTH - 1 - 12
    sep_y1 = co_y0 + 5                        # 6 px tall separator
    if barcode is not None:
        _draw_code128b(canvas, barcode, 12, co_y0, sep_x1, sep_y1,
                       bar_color=(0, 0, 0), fill_color=(255, 255, 255))
    else:
        draw.rectangle([(12, co_y0), (sep_x1, sep_y1)], fill=PALETTE[COLOR_YELLOW])

    # ── Company / logo — 6 px gap after separator, 6 px bottom pad ──────────────
    co_h = SCREEN_HEIGHT - co_y0

    if logo_path and os.path.exists(logo_path):
        logo = Image.open(logo_path).copy()
        logo.thumbnail((SCREEN_WIDTH - 20, co_h - 18), Image.LANCZOS)
        lx = (SCREEN_WIDTH - logo.width) // 2
        ly = co_y0 + 12
        _paste_layer(canvas, logo, (lx, ly))

    elif company_name:
        co_font, co_lines = _fit_font_wrapped(draw, company_name,
                                              SCREEN_WIDTH - 20, co_h - 18,
                                              reg_fp, start=40)
        _draw_lines_centered(draw, co_lines, co_font,
                             SCREEN_WIDTH // 2, co_y0 + 12, (0, 0, 0))

    return canvas
