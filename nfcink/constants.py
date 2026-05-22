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

# ---- Factory-reset driver flow ----------------------------------------------
#
# Restores the badge's screen driver flow. The chip's stored driver-flow
# TLV can be wiped by an interrupted `F0DB02` (clear) APDU;
# if that happens, the screen no longer refreshes (chip still answers NFC
# normally). `factory_reset()` clears whatever's loaded and re-uploads this
# canonical sequence.
#
# Values come from `GDEM037F51.pdf` section 10.2 ("OTP Operation Reference
# Program Code") -- the datasheet-authoritative tuning for this panel.

_FACTORY_PAYLOAD_4C_37 = (
    "A0060720034000F0"                       # A0: size code 0x07 (panel 240x416)
    "A40108A5020028A4010CA5020028A40103"     # RST sequence + waits
    "A103000729"                             # R00H PSR   = 07 29
    "A10701070022780A22"                     # R01H PWR   = 07 00 22 78 0A 22
    "A10406400080"                           # R06H BTST  = 40 00 80
    "A1056100F001A0"                         # R61H TRES  = 240 x 416
    "A1023002"                               # R30H PLL   = 02
    "A1025037"                               # R50H CDI   = 37
    "A102E73C"                               # 0xE7       = 3C
    "A102FFA5"                               # RFFH TEST  = A5 (unlock)
    "A107EF0A0A080A0D0A"                     # REFH PWM   = 0A 0A 08 0A 0D 0A
    "A102DC01"                               # RDCH CPCK_EN  = 01
    "A102DD06"                               # RDDH CPCK_PWH = 06
    "A102DE3C"                               # RDEH CPCK_PWL = 3C
    "A102DA00"                               # RDAH DRV_SEL  = 00
    "A102E802"                               # 0xE8          = 02
    "A102FFE3"                               # RFFH TEST  = E3 (lock)
    "A102E901"                               # 0xE9       = 01
    "A10104A40103"                           # R04H PON + wait
    "A30110"                                 # write-RAM cmd = 0x10
    "A2021200A40103"                         # R12H DRF + wait
    "A2020200A40103A20207A5"                 # R02H POF + wait + R07H DSLP=A5
)
APDU_DRIVER_CLEAR        = "F0DB020000"
APDU_DRIVER_FLOW_FACTORY = (
    f"F0DB0000{len(bytes.fromhex(_FACTORY_PAYLOAD_4C_37)):02X}{_FACTORY_PAYLOAD_4C_37}"
)
APDU_SET_SCREEN_FACTORY  = "F0DA000003F00720"

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
