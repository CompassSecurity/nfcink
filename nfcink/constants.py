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
APDU_GET_IMAGE_SN    = "00D5000000"            # read currently-displayed image slot index
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
# Bytes follow GDEM037F51 datasheet section 10.2 ("OTP Operation Reference
# Program Code") -- with one corrected byte, see note at the end. The
# datasheet diagrams a host-side procedure flow:
#
#       1. Reset the EPD driver IC          (TLV: A4 RST low/high + A5 delays)
#       2. Enter FITI Command (SPI burst)   (TLV: A1 commands -- the long block)
#       3. Power on  SPI(0x04)              (TLV: A1 01 04)
#       4. Check BUSY pin                    (TLV: A4 01 03 wait BUSY high)
#       5. Data start transmission SPI(0x10) (TLV: A3 01 10 declares RAM write cmd)
#       6. Transport B/W/R/Y data            (chip handles via F0D2/F0D3)
#       7. Display refresh SPI(0x12)         (TLV: A2 02 12 00)
#       8. Check BUSY pin                    (TLV: A4 01 03)
#       9. Power off SPI(0x02)               (TLV: A2 02 02 00)
#      10. Deep sleep SPI(0x07, 0xA5)        (TLV: A2 02 07 A5)
#
# `factory_reset` stores this TLV in the FMSC chip's "user configuration
# area" via the `F0DB` APDU (see FMSC ESL User Development Manual section
# 7.10 "Set Driver Flow"). Nothing actually runs on the panel at that
# point. Each later `F0D4` (display refresh) tells the chip to execute
# the entire stored TLV against the panel: reset -> FITI Command -> PON
# -> image transfer -> DRF -> POF -> DSLP. The "init" portion isn't
# one-shot; it runs every refresh, because each refresh begins with the
# panel in deep sleep (left there by the previous refresh's POF+DSLP
# tail). The image data is uploaded separately via `F0D2`/`F0D3` between
# refreshes and consumed at step 6.
# Each `A1 <len> <cmd> [<params...>]` below maps to one SPI(...) call in
# GDEM037F51 datasheet section 10.2.
#
# DEVIATION FROM DATASHEET: section 10.2 prints `R61_TRES, 0x00, 0xF0, 0x0A,
# 0xA0` (third byte 0x0A). The IST7163 datasheet's R61H field definition
# decomposes the 3rd parameter as VRES[9..8] in bits D1..D0 and explicitly
# constrains VRES[9]=0. The value 0x0A (= D1=1, D0=0) violates that
# constraint, and produces VRES=160 (or undefined behavior under bit
# masking) rather than the panel's actual 416. The §10.2 snippet is a
# datasheet typo; we use the IST7163-correct 0x01 and produce
# VRES=0x1A0=416 -- the panel's real vertical resolution. Empirically
# confirmed: real badges refresh correctly with 0x01.

_FACTORY_PAYLOAD_4C_37 = (
    # --- Step 1: reset the EPD driver IC ---
    "A0060720034000F0"                       # A0: panel descriptor (size 0x07, 240x416)
    "A40108"                                 # RST low
    "A5020028"                               # delay 40ms
    "A4010C"                                 # RST high
    "A5020028"                               # delay 40ms
    "A40103"                                 # wait BUSY high                                                 panel ready after reset
    # --- Step 2: Enter FITI Command (the long SPI burst from section 10.2) ---
    "A103000729"                             # SPI(R00_PSR, 0x07, 0x29)                                       Panel Setting -- panel size / colour / scan dir
    "A10701070022780A22"                     # SPI(R01_PWR, 0x07, 0x00, 0x22, 0x78, 0x0A, 0x22)               Power Setting -- VGH/VGL/VDH/VDL/VDHR rail tuning
    "A10406400080"                           # SPI(R06_BTST, 0x40, 0x00, 0x80)                                Booster Soft Start -- BT_PHA / BT_PHB / BT_PHC ramp-up timings
    "A1056100F001A0"                         # SPI(R61_TRES, 0x00, 0xF0, 0x01, 0xA0)                          Resolution Setting -- HRES=240, VRES=416
    "A1023002"                               # SPI(R30_PLL, 0x02)                                             PLL Control -- frame rate (bit3=0, FR[2:0]=010)
    "A1025037"                               # SPI(R50_CDI, 0x37)                                             VCOM and DATA Interval -- border voltage + clock spacing
    "A102E73C"                               # SPI(0xE7, 0x3C)                                                vendor-magic, undocumented in IST7163
    "A102FFA5"                               # SPI(RFF_TEST, 0xA5)                                            test-register space UNLOCK
    "A107EF0A0A080A0D0A"                     # SPI(REF_TEST_PWR_PWM, 0x0A, 0x0A, 0x08, 0x0A, 0x0D, 0x0A)      PWM converter tuning
    "A102DC01"                               # SPI(RDC_CPCK_EN, 0x01)                                         clamp enable=ON, use RDD/RDE custom timings
    "A102DD06"                               # SPI(RDD_CPCK_PWH, 0x06)                                        VDH/VDL clamp high-pulse = 6 * 125ns = 750ns
    "A102DE3C"                               # SPI(RDE_CPCK_PWL, 0x3C)                                        VDH/VDL clamp low-pulse  = 60 * 125ns = 7500ns
    "A102DA00"                               # SPI(RDA_DRV_SEL, 0x00)                                         VDHR/VDH/VDL all weak driving (default=strong)
    "A102E802"                               # SPI(0xE8, 0x02)                                                vendor-magic, undocumented in IST7163
    "A102FFE3"                               # SPI(RFF_TEST, 0xE3)                                            test-register space LOCK
    "A102E901"                               # SPI(0xE9, 0x01)                                                vendor-magic, undocumented in IST7163
    # --- Steps 3-4: power on, wait for BUSY ---
    "A10104"                                 # SPI(R04_PON)                                                   Power ON
    "A40103"                                 # wait BUSY high                                                 Check BUSY pin (step 4)
    # --- Step 5: declare the write-RAM SPI command for the image transfer ---
    "A30110"                                 # SPI(R10_DTM)                                                   Data Start Transmission opcode declaration
    # --- Steps 7-8: display refresh, wait for BUSY ---
    "A2021200"                               # SPI(R12_DRF, 0x00)                                             Display Refresh
    "A40103"                                 # wait BUSY high                                                 Check BUSY pin (step 8)
    # --- Steps 9-10: power off, deep sleep ---
    "A2020200"                               # SPI(R02_POF, 0x00)                                             Power Off
    "A40103"                                 # wait BUSY high                                                 ensure POF complete before DSLP (R07H DSLP needs BUSY=1)
    "A20207A5"                               # SPI(R07_DSLP, 0xA5)                                            Deep Sleep
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
