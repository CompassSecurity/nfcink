"""
nfcink.config -- DeviceCfg: parses the device configuration TLV.

The parser walks fixed tags in a fixed order (no generic TLV loop) and
consumes the concatenated response of TWO APDUs:

    00D1000000             -- TLV: A0 A1 B1 B2 B3 C0 C1 D1, then SW=9000
    F0D8000005000000000E   -- PIN check / 4-color marker: data + (9000 | 6985)

The trailer encodes both the PIN flag (via the SW of the second APDU) and a
literal "4_color Screen" ASCII marker that triggers the 4-color override
(forces a 4-color palette and halves the reported screen height).
"""

from .constants import (
    SCREEN_WIDTH, SCREEN_HEIGHT,
    COLOR_BLACK, COLOR_WHITE, COLOR_RED, COLOR_YELLOW,
)


# ---- Lookup tables ----------------------------------------------------------

# Manufacturer codes (A0 byte 0).
_MANUFACTURERS: dict[int, str] = {
    0x00: "Jiaxian",
    0x10: "Yuantai",
    0x20: "Aoyi",
    0x30: "Weifeng",
    0x40: "Pdi",
    0x60: "Jdf",
    0x70: "Dke",
    0x80: "Weixinnuo",
    0xF0: "Other",
}

# Color code (A0 byte 2) -> (color_count, has_red, has_yellow). 4-color devices
# also report one of these; the 4-color override is detected via the trailer.
_COLOR_CODE_INFO: dict[int, tuple[int, bool, bool]] = {
    0x20: (2, False, False),  # 2-color B/W
    0x30: (3, True,  False),  # 3-color B/W/R
    0x31: (3, False, True),   # 3-color B/W/Y
}

# Canonical 4-color wire encoding -- forced by the "4_color Screen" trailer.
_PALETTE_4COLOR: dict[int, str] = {
    COLOR_BLACK:  "00",
    COLOR_WHITE:  "01",
    COLOR_RED:    "11",
    COLOR_YELLOW: "10",
}

_FOUR_COLOR_MARKER = b"4_color Screen"


# ---- DeviceCfg --------------------------------------------------------------

class DeviceCfg:
    """Device configuration parsed from concat(resp(00D1000000), resp(F0D8...))."""

    def __init__(self) -> None:
        # A0 -- manufacturer block + screen geometry
        self.manufacturer_code = 0
        self.manufacturer_name = ""
        self.color_code        = 0          # A0 byte 2: 0x20 / 0x30 / 0x31
        self.screen_width      = SCREEN_WIDTH
        self.screen_height     = SCREEN_HEIGHT

        # A1 -- scan info + colour palette
        self.refresh_scan      = 0          # 0 = vertical, 1 = horizontal
        self.size              = 0          # high nibble of A1 byte 1
        self.color_count       = 4          # low nibble of A1 byte 1
        # color_dic: COLOR_xxx -> 2-bit binary string (the wire encoding).
        self.color_dic: dict[int, str] = dict(_PALETTE_4COLOR)

        # B1, B2, B3 -- single-byte fields
        self.picture_capacity  = 0
        self.user_data         = 0
        self.is_battery        = False

        # C0, C1 -- 4-byte hex strings
        self.app_id            = ""
        self.uuid              = ""

        # D1 -- compress flag + COS version
        self.is_compress       = False
        self.cos_version       = 0

        # Trailer (from the second APDU's SW + payload)
        self.color_desc        = ""         # "4_color Screen" or ""
        self.is_pin            = False      # True if SW=6985

        # Derived: 0=B/W, 1=has red, 2=has yellow, 3=4-color
        self.device_type       = 0

        # Image-orientation hint queried via APDU 00EB000002 (read_image_info).
        # Not part of the TLV; populated by callers that read the flip flags.
        self.flip_vertical     = False

    # ---- Parsing --------------------------------------------------------

    @classmethod
    def from_responses(cls, resp1: bytes, resp2: bytes = b"") -> "DeviceCfg":
        """Parse the concatenated bytes of both APDU responses.

        resp1 -- response of 00D1000000 (TLV + SW).
        resp2 -- response of F0D8000005000000000E (PIN check + SW).
                 Optional; if empty, PIN/4-color detection degrades gracefully.
        """
        return cls._parse(resp1 + resp2)

    @classmethod
    def from_response(cls, raw_hex: str) -> "DeviceCfg":
        """Parse a single hex string (the concatenation of both APDU responses).

        Provided for callers that already have the concatenated hex; new code
        should prefer from_responses(resp1, resp2).
        """
        try:
            data = bytes.fromhex(raw_hex)
        except ValueError as exc:
            raise ValueError(f"Invalid hex in config response: {exc}") from exc
        return cls._parse(data)

    @classmethod
    def _parse(cls, data: bytes) -> "DeviceCfg":
        cfg = cls()
        if len(data) < 2 or data[0] != 0xA0:
            # Empty or malformed response: keep defaults.
            return cfg

        # Walk fixed tags positionally.
        pos = cfg._consume_tag(data, 0,   0xA0, cfg._parse_a0)
        pos = cfg._consume_tag(data, pos, 0xA1, cfg._parse_a1)
        pos = cfg._consume_tag(data, pos, 0xB1, cfg._parse_b1)
        pos = cfg._consume_tag(data, pos, 0xB2, cfg._parse_b2)
        pos = cfg._consume_tag(data, pos, 0xB3, cfg._parse_b3)
        pos = cfg._consume_tag(data, pos, 0xC0, cfg._parse_c0)
        pos = cfg._consume_tag(data, pos, 0xC1, cfg._parse_c1)
        pos = cfg._consume_tag(data, pos, 0xD1, cfg._parse_d1)
        cfg._parse_trailer(data)
        return cfg

    @staticmethod
    def _consume_tag(data: bytes, pos: int, tag: int, parser) -> int:
        """If data[pos] == tag, run parser on the value bytes and return the
        offset of the next tag. Otherwise return pos unchanged."""
        if pos + 1 >= len(data) or data[pos] != tag:
            return pos
        length  = data[pos + 1]
        val_end = pos + 2 + length
        if val_end > len(data):
            return pos
        parser(data[pos + 2 : val_end])
        return val_end

    def _parse_a0(self, value: bytes) -> None:
        """A0: manufacturer code, color code, height (BE), width (BE)."""
        if len(value) >= 1:
            self.manufacturer_code = value[0]
            self.manufacturer_name = _MANUFACTURERS.get(
                value[0], f"unknown(0x{value[0]:02X})"
            )
        if len(value) >= 3:
            self.color_code = value[2]
            info = _COLOR_CODE_INFO.get(value[2])
            if info is not None:
                self.color_count, has_red, has_yellow = info
                if has_red:
                    self.device_type = 1
                elif has_yellow:
                    self.device_type = 2
        if len(value) >= 5:
            self.screen_height = (value[3] << 8) | value[4]
        if len(value) >= 7:
            self.screen_width = (value[5] << 8) | value[6]

    def _parse_a1(self, value: bytes) -> None:
        """A1: refresh scan, size + color count, per-color wire encoding records.

        Each per-color record byte: top 3 bits = colour ID
        (0=black 1=white 2=red 3=yellow), middle bits = 2-bit (or 1-bit for
        2-colour devices) wire encoding.
        """
        if len(value) >= 1:
            self.refresh_scan = value[0]
        if len(value) >= 2:
            self.size        = (value[1] >> 4) & 0x0F
            self.color_count = value[1] & 0x0F

        # Decode per-color records.
        n_bits = 1 if self.color_count == 2 else 2
        shift  = (3 - self.color_count) + 3
        mask   = (1 << n_bits) - 1
        palette: dict[int, str] = {}

        for i in range(self.color_count):
            if 2 + i >= len(value):
                break
            byte     = value[2 + i]
            color_id = (byte >> 5) & 0x07
            encoding = format((byte >> shift) & mask, f"0{n_bits}b")
            if color_id == 0:
                palette[COLOR_BLACK]  = encoding
            elif color_id == 1:
                palette[COLOR_WHITE]  = encoding
            elif color_id == 2:
                palette[COLOR_RED]    = encoding
                self.device_type = 1
            elif color_id == 3:
                palette[COLOR_YELLOW] = encoding
                self.device_type = 2

        if palette:
            self.color_dic = palette

    def _parse_b1(self, value: bytes) -> None:
        if len(value) >= 1:
            self.picture_capacity = value[0]

    def _parse_b2(self, value: bytes) -> None:
        if len(value) >= 1:
            self.user_data = value[0]

    def _parse_b3(self, value: bytes) -> None:
        if len(value) >= 1:
            self.is_battery = value[0] != 0

    def _parse_c0(self, value: bytes) -> None:
        if len(value) >= 4:
            self.app_id = value[:4].hex().upper()

    def _parse_c1(self, value: bytes) -> None:
        if len(value) >= 4:
            self.uuid = value[:4].hex().upper()

    def _parse_d1(self, value: bytes) -> None:
        if len(value) >= 1:
            self.is_compress = value[0] != 0
        if len(value) >= 2 and value[1] == 0x20:
            self.cos_version = 2

    def _parse_trailer(self, data: bytes) -> None:
        """Last 16 bytes encode the PIN flag and the optional 4-color marker.

        SW=9000 -> PIN not required. If the 14 bytes preceding the SW equal
                   the ASCII string "4_color Screen", apply the 4-color
                   override (halve height, force the canonical palette).
        SW=6985 -> PIN required.
        """
        if len(data) < 2:
            return
        sw = data[-2:]
        if sw == b"\x90\x00":
            self.is_pin = False
            if len(data) >= 16 and data[-16:-2] == _FOUR_COLOR_MARKER:
                self.color_desc    = _FOUR_COLOR_MARKER.decode("ascii")
                self.device_type   = 3
                self.color_count   = 4
                self.screen_height = self.screen_height // 2
                self.color_dic     = dict(_PALETTE_4COLOR)
        elif sw == b"\x69\x85":
            self.is_pin = True

    # ---- Queries --------------------------------------------------------

    def supports_red(self) -> bool:
        """True if the device's palette includes red."""
        return COLOR_RED in self.color_dic

    def supports_yellow(self) -> bool:
        """True if the device's palette includes yellow."""
        return COLOR_YELLOW in self.color_dic

    def __repr__(self) -> str:
        return (
            f"DeviceCfg(manufacturer={self.manufacturer_name!r}, "
            f"size={self.screen_width}x{self.screen_height}, "
            f"colors={self.color_count}, colorCode=0x{self.color_code:02X}, "
            f"deviceType={self.device_type}, pictureCapacity={self.picture_capacity}, "
            f"battery={self.is_battery}, compress={self.is_compress}, "
            f"cosVersion={self.cos_version}, pin={self.is_pin}, "
            f"colorDesc={self.color_desc!r}, appID={self.app_id!r}, "
            f"uid={self.uuid!r})"
        )
