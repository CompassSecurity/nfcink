"""
nfcink.constants -- screen geometry, color codes, NFC status words, logging.
"""

import binascii

# ---- Screen geometry --------------------------------------------------------

SCREEN_WIDTH  = 240
SCREEN_HEIGHT = 416

# Max payload bytes per F0 D2 (raw) write APDU.
CHUNK_SIZE    = 250

# ---- Badge layout (top photo slot) ------------------------------------------
# AI backends should produce this aspect ratio; compose_badge scale-fits the
# result into the rectangle.

PHOTO_W = SCREEN_WIDTH   # 240
PHOTO_H = 270            # leaves 146 px for name band + company area

# ---- 2-bit color indices -------------------------------------------------------

COLOR_BLACK  = 0b00
COLOR_WHITE  = 0b01
COLOR_YELLOW = 0b10
COLOR_RED    = 0b11

# RGB palette used for quantisation and preview rendering.
PALETTE: dict[int, tuple[int, int, int]] = {
    COLOR_BLACK:  (0,   0,   0),
    COLOR_WHITE:  (255, 255, 255),
    COLOR_RED:    (255, 0,   0),
    COLOR_YELLOW: (255, 255, 0),
}

# ---- NFC APDU constants -----------------------------------------------------

APDU_READ_CONFIG     = "00D1000000"            # read device config TLV
APDU_READ_IMAGE_INFO = "00EB000002"            # read 2-byte image flip flags
APDU_DEVICE_CHECK    = "F0D8000005000000000E"  # device / PIN status check
APDU_REFRESH_INIT    = "F0D4050000"            # initial refresh (P1=0x05)
APDU_REFRESH_ALT     = "F0D4850000"            # alternate refresh (P1=0x85)
APDU_POLL_REFRESH    = "F0DE000001"            # poll e-ink refresh status

# ---- NFC status words -------------------------------------------------------

SW_OK           = b'\x90\x00'   # success
SW_NO_PIN       = b'\x69\x85'   # conditions not satisfied / no PIN required
SW_COLOR_SCREEN = b'\x68\xC6'   # caller should escalate refresh stage
SW_68CA         = b'\x68\xCA'   # device busy
SW_6986         = b'\x69\x86'   # command not allowed; retry from start
SW_698A         = b'\x69\x8A'   # device hardware error (terminal)

# ---- Verbose logging --------------------------------------------------------

_verbose = False


def set_verbose(flag: bool) -> None:
    """Enable or disable verbose APDU logging globally."""
    global _verbose
    _verbose = flag


def vlog(msg: str) -> None:
    """Print msg only when verbose mode is active."""
    if _verbose:
        print(f"  [v] {msg}")


def hex_str(data: bytes) -> str:
    """Return uppercase hex string for a bytes object."""
    return binascii.hexlify(data).upper().decode()
