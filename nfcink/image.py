"""
nfcink.image -- image quantisation and device-byte packing.
"""

from PIL import Image

from .constants import PALETTE, COLOR_BLACK, COLOR_WHITE, COLOR_RED, COLOR_YELLOW, vlog
from .config import DeviceCfg


def quantise_image(img: Image.Image, cfg: DeviceCfg, dither: str = "floyd",
                   force_bw: bool = False) -> list[int]:
    """Convert a PIL image to a flat list of colour indices (one per pixel).

    Steps:
      1. Resize to device screen dimensions (LANCZOS).
      2. Unconditional horizontal flip.
      3. Optional vertical flip (cfg.flip_vertical).
      4. Floyd-Steinberg dithering or nearest-colour assignment.

    dither:   "floyd" -- error diffusion (default).
              "none"  -- nearest colour only.
    force_bw: True restricts the palette to black + white only (2-colour
              waveform); useful on devices with insufficient RF power budget
              for the 4-colour waveform.

    Returns COLOR_* integers, row-major left-to-right top-to-bottom.
    """
    img = img.convert("RGB")
    img.thumbnail((cfg.screen_width, cfg.screen_height), Image.LANCZOS)
    if img.size != (cfg.screen_width, cfg.screen_height):
        canvas = Image.new("RGB", (cfg.screen_width, cfg.screen_height), (255, 255, 255))
        x = (cfg.screen_width  - img.width)  // 2
        y = (cfg.screen_height - img.height) // 2
        canvas.paste(img, (x, y))
        img = canvas

    img = img.transpose(Image.FLIP_LEFT_RIGHT)
    if cfg.flip_vertical:
        img = img.transpose(Image.FLIP_TOP_BOTTOM)

    if force_bw:
        candidates = [
            (COLOR_BLACK, PALETTE[COLOR_BLACK]),
            (COLOR_WHITE, PALETTE[COLOR_WHITE]),
        ]
    else:
        candidates = [
            (COLOR_BLACK,  PALETTE[COLOR_BLACK]),
            (COLOR_WHITE,  PALETTE[COLOR_WHITE]),
            (COLOR_YELLOW, PALETTE[COLOR_YELLOW]),
        ]
        if cfg.supports_red():
            candidates.append((COLOR_RED, PALETTE[COLOR_RED]))

    w, h = cfg.screen_width, cfg.screen_height

    if dither == "floyd":
        return _floyd_steinberg(img, w, h, candidates)
    else:
        return _nearest_colour(img, w, h, candidates)


def _dist_sq(r1: float, g1: float, b1: float, r2: int, g2: int, b2: int) -> float:
    return (r1 - r2) ** 2 + (g1 - g2) ** 2 + (b1 - b2) ** 2


def _floyd_steinberg(
    img: Image.Image,
    w: int, h: int,
    candidates: list[tuple[int, tuple[int, int, int]]],
) -> list[int]:
    r_ch, g_ch, b_ch = img.split()
    buf_r = [float(v) for v in r_ch.getdata()]
    buf_g = [float(v) for v in g_ch.getdata()]
    buf_b = [float(v) for v in b_ch.getdata()]

    result: list[int] = []
    for y in range(h):
        for x in range(w):
            i = y * w + x
            r = max(0.0, min(255.0, buf_r[i]))
            g = max(0.0, min(255.0, buf_g[i]))
            b = max(0.0, min(255.0, buf_b[i]))

            best_idx, best_dist = COLOR_BLACK, float("inf")
            for cidx, (pr, pg, pb) in candidates:
                d = _dist_sq(r, g, b, pr, pg, pb)
                if d < best_dist:
                    best_dist, best_idx = d, cidx
            result.append(best_idx)

            qr, qg, qb = PALETTE[best_idx]
            er, eg, eb = r - qr, g - qg, b - qb
            for dx, dy, w16 in ((1, 0, 7), (-1, 1, 3), (0, 1, 5), (1, 1, 1)):
                nx, ny = x + dx, y + dy
                if 0 <= nx < w and 0 <= ny < h:
                    ni = ny * w + nx
                    buf_r[ni] += er * w16 / 16.0
                    buf_g[ni] += eg * w16 / 16.0
                    buf_b[ni] += eb * w16 / 16.0

    vlog(f"Floyd-Steinberg dithering applied ({w}x{h} pixels)")
    return result


def _nearest_colour(
    img: Image.Image,
    w: int, h: int,
    candidates: list[tuple[int, tuple[int, int, int]]],
) -> list[int]:
    result: list[int] = []
    for y in range(h):
        for x in range(w):
            r, g, b = img.getpixel((x, y))
            best_idx, best_dist = COLOR_BLACK, float("inf")
            for cidx, (pr, pg, pb) in candidates:
                d = _dist_sq(float(r), float(g), float(b), pr, pg, pb)
                if d < best_dist:
                    best_dist, best_idx = d, cidx
            result.append(best_idx)
    return result


def pixels_to_bytes(pixels: list[int], cfg: DeviceCfg) -> bytes:
    """
    Pack colour indices into device bytes.

    Encoding:
      black=00, white=01, yellow=10, red=11 (2 bits each)
      4 pixels per byte, MSB first: byte = p0<<6 | p1<<4 | p2<<2 | p3

    240x416 pixels / 4 = 24 960 bytes total.
    """
    color_map = cfg.color_dic
    result = bytearray()
    for i in range(0, len(pixels), 4):
        group = pixels[i:i+4]
        while len(group) < 4:
            group.append(COLOR_BLACK)
        bin_str = "".join(color_map.get(c, "00") for c in group)
        result.append(int(bin_str, 2))
    return bytes(result)


def image_to_device_bytes(image_src: "str | Image.Image", cfg: DeviceCfg,
                          dither: str = "floyd", force_bw: bool = False) -> bytes:
    """Full pipeline: image file path or PIL Image → quantised pixels → packed device bytes."""
    img = Image.open(image_src) if isinstance(image_src, str) else image_src
    pixels = quantise_image(img, cfg, dither=dither, force_bw=force_bw)
    return pixels_to_bytes(pixels, cfg)
