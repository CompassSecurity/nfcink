"""
nfcink.protocol -- low-level APDU transport and NfcInkDevice command layer.

NfcInkDevice wraps a transport and exposes
one method per known APDU. Multi-step business logic (refresh escalation,
polling) lives in nfcink.runner so this module stays free of backend-specific
loop logic.
"""

import minilzo

from .constants import (
    SW_OK, SW_6986,
    APDU_READ_CONFIG, APDU_READ_IMAGE_INFO, APDU_GET_IMAGE_SN,
    APDU_DEVICE_CHECK,
    APDU_REFRESH_INIT, APDU_REFRESH_ALT, APDU_POLL_REFRESH,
    APDU_DRIVER_CLEAR, APDU_DRIVER_FLOW_FACTORY, APDU_SET_SCREEN_FACTORY,
    vlog, hex_str,
)
from .config import DeviceCfg


# ---- LZO compressed write parameters ----------------------------------------
# Image is split into 2000-byte blocks; each block is LZO1x-1 compressed
# and split into 250-byte sub-chunks.

_BLOCK_SIZE     = 2000
_SUB_CHUNK_SIZE = 250


# ---- APDU helpers -----------------------------------------------------------

def send_apdu(transport, apdu: bytes, timeout: float | None = None) -> bytes:
    """Send a raw APDU and return the full response (data + SW)."""
    vlog(f"APDU TX: {hex_str(apdu)}")
    resp = transport.transceive(apdu, timeout=timeout)
    vlog(f"APDU RX: {hex_str(resp)}")
    return resp


def check_sw(response: bytes, expected: bytes = SW_OK) -> bool:
    """True if the last 2 bytes of response equal expected."""
    return len(response) >= 2 and response[-2:] == expected


def strip_sw(response: bytes) -> bytes:
    """Return the data payload of a response (everything except the 2 SW bytes)."""
    return response[:-2]


# ---- Device command layer ---------------------------------------------------

class NfcInkDevice:
    """Command-level interface to the e-ink NFC display.

    Each public method corresponds to exactly one APDU exchange or one
    self-contained transfer (write_image_d3). Higher-level logic lives in
    nfcink.runner.
    """

    def __init__(self, transport):
        self.transport = transport

    def _tx(self, apdu_hex: str, timeout: float | None = None) -> bytes:
        return send_apdu(self.transport, bytes.fromhex(apdu_hex), timeout=timeout)

    def _txb(self, apdu: bytes, timeout: float | None = None) -> bytes:
        return send_apdu(self.transport, apdu, timeout=timeout)

    # ---- Config / info ------------------------------------------------------

    def cmd_read_config(self) -> bytes:
        """00 D1 00 00 00 -- read device config TLV."""
        return self._tx(APDU_READ_CONFIG)

    def cmd_read_image_info(self) -> bytes:
        """00 EB 00 00 02 -- read 2-byte image flip flags."""
        return self._tx(APDU_READ_IMAGE_INFO)

    def cmd_get_image_sn(self) -> bytes:
        """00 D5 00 00 00 -- return index of the currently displayed image slot.

        Response: 1 data byte = slot index (0..pictureCapacity-1), then SW=9000.
        Documented in FMSC ESL User Development Manual section 7.8.
        """
        return self._tx(APDU_GET_IMAGE_SN)

    def cmd_get_device_config(self, timeout: float | None = None) -> bytes:
        """F0 D8 00 00 05 00 00 00 00 0E -- extended device / PIN status check."""
        return self._tx(APDU_DEVICE_CHECK, timeout=timeout)

    # ---- Image write (D3 / LZO compressed) ----------------------------------

    def cmd_write_d3_subchunk(self, section: int, block_idx: int, sub_idx: int,
                              data: bytes, is_last: bool) -> bytes:
        """F0 D3 <section> <P2> <Lc> <block_idx> <sub_idx> <data...>

        Sends one sub-chunk (<=250 bytes) of a LZO-compressed image block.
        P2=1 marks the final sub-chunk of a block. Lc = len(data) + 2.
        """
        lc = len(data) + 2
        p2 = 1 if is_last else 0
        apdu = bytes([0xF0, 0xD3, section & 0xFF, p2, lc,
                      block_idx & 0xFF, sub_idx & 0xFF]) + data
        return self._txb(apdu)

    def cmd_load_image_raw(self, section: int, seq: int, data: bytes) -> bytes:
        """F0 D2 <section> <seq> <Lc> <data...> -- write raw image bytes.

        Uncompressed counterpart of `cmd_write_d3_subchunk`. Each APDU
        carries one 250-byte chunk (last chunk can be shorter).

        section: image slot index (0..pictureCapacity-1)
        seq:     packet sequence number (0..255)
        data:    1..250 bytes of raw image data
        """
        if not 0 <= section <= 0x7F:
            raise ValueError(f"section out of range: {section}")
        if not 0 <= seq <= 0xFF:
            raise ValueError(f"seq out of range: {seq}")
        if not 1 <= len(data) <= 250:
            raise ValueError(f"data length must be 1..250, got {len(data)}")
        apdu = bytes([0xF0, 0xD2, section, seq, len(data)]) + bytes(data)
        return self._txb(apdu)

    # ---- Screen refresh -----------------------------------------------------

    def cmd_refresh_init(self, section: int = 0, timeout: float = 10.0) -> bytes:
        """F0 D4 05 <section> 00 -- initial refresh.

        P1=0x05 (wait mode, no screen-detect bypass).
        P2 bits 6-0 = image-slot index to display (0..pictureCapacity-1).
        Expected responses: 9000 / 009000 / 019000 (success), 68C6
        (escalate), 6986 (retry up to 5x).
        """
        apdu = bytes([0xF0, 0xD4, 0x05, section & 0x7F, 0x00])
        return self._txb(apdu, timeout=timeout)

    def cmd_refresh_alt(self, section: int = 0, timeout: float = 50.0) -> bytes:
        """F0 D4 85 <section> 00 -- alternate refresh (P1=0x85, no detect).

        Sent after two 68C6 responses. BLOCKS ~14-15 s while the e-ink
        waveform is computed, then returns 9000. The long timeout here is
        required: short timeouts will drop the session. P2 bits 6-0 =
        image-slot index to display.
        """
        apdu = bytes([0xF0, 0xD4, 0x85, section & 0x7F, 0x00])
        return self._txb(apdu, timeout=timeout)

    def cmd_poll_refresh(self, timeout: float = 50.0) -> bytes:
        """F0 DE 00 00 01 -- poll refresh status.

        Response data byte: 0x00 = done, 0x01 = drawing in progress.
        Both are accepted as success.
        """
        return self._tx(APDU_POLL_REFRESH, timeout=timeout)

    # ---- Driver-flow management ---------------------------------------------
    #
    # The chip stores a "driver flow" (panel init TLV) in EEPROM. These three
    # APDUs are the primitives that load it. Used internally by factory_reset;
    # exposed as cmd_* methods for diagnostics but no CLI subcommand.

    def cmd_clear_driver_flow(self) -> bytes:
        """F0 DB 02 00 00 -- wipe any currently loaded screen driver flow.

        Returns 9000 when something was cleared, 6986 when nothing was loaded.
        After this APDU, the screen cannot refresh until a new driver flow is
        loaded via cmd_load_driver_flow.
        """
        return self._tx(APDU_DRIVER_CLEAR)

    def cmd_load_driver_flow(self, apdu_hex: str) -> bytes:
        """F0 DB 00 00 <Lc> <TLV blob> -- load a screen driver flow."""
        return self._tx(apdu_hex)

    def cmd_set_screen_type(self, apdu_hex: str) -> bytes:
        """F0 DA 00 00 <Lc> <screen-type bytes> -- switch to loaded driver."""
        return self._tx(apdu_hex)

    # ---- Higher-level helpers -----------------------------------------------

    def read_config(self) -> DeviceCfg:
        """Read and parse the device config.

        Sends both config APDUs:
        00D1000000 (TLV) and F0D8000005000000000E (PIN check / 4-color
        marker). The concatenated response feeds the TLV parser; the SW of
        the second APDU encodes the PIN flag, and the data preceding that
        SW may carry the literal "4_color Screen" marker that triggers the
        4-color override.
        """
        vlog("Reading device config...")
        resp_tlv = self.cmd_read_config()
        if len(resp_tlv) < 4:
            raise RuntimeError(f"Config read failed: {hex_str(resp_tlv)}")
        try:
            resp_pin = self.cmd_get_device_config(timeout=5.0)
        except Exception as exc:
            vlog(f"  device-check APDU failed ({exc}); proceeding with TLV only")
            resp_pin = b""
        vlog(f"  TLV : {hex_str(resp_tlv)}")
        vlog(f"  PIN : {hex_str(resp_pin)}")
        cfg = DeviceCfg.from_responses(resp_tlv, resp_pin)
        vlog(f"  got : {cfg}")
        return cfg

    def factory_reset(self) -> bool:
        """Re-upload the original driver flow.

        Use this when a badge's screen no longer refreshes despite the chip
        still answering NFC normally -- typically the result of an interrupted
        F0DB02 / F0DB00 sequence that left the chip without a driver flow,
        or a wrong driver flow having been loaded by another tool.

        Sends three APDUs in order:
          1. F0DB020000                clear the current driver flow
          2. F0DB0000<Lc><factory TLV> load the canonical OTP-reference flow
          3. F0DA000003F00720          switch the chip's screen type to it

        Returns True if all three APDUs returned 9000 (or 6986 from step 1,
        meaning no driver flow was loaded -- also acceptable, the load step
        will install one).
        """
        print("[*] Factory-resetting driver flow (datasheet OTP reference)...")

        resp = self.cmd_clear_driver_flow()
        sw = resp[-2:] if len(resp) >= 2 else b""
        if sw not in (b"\x90\x00", b"\x69\x86"):
            print(f"    [!] Clear failed  SW={hex_str(sw)}")
            return False
        vlog(f"    Clear: SW={hex_str(sw)}")

        resp = self.cmd_load_driver_flow(APDU_DRIVER_FLOW_FACTORY)
        if not check_sw(resp):
            print(f"    [!] Load driver flow failed  SW={hex_str(resp[-2:])}")
            return False
        vlog(f"    Load:  SW={hex_str(resp[-2:])}")

        resp = self.cmd_set_screen_type(APDU_SET_SCREEN_FACTORY)
        if not check_sw(resp):
            print(f"    [!] Set screen type failed  SW={hex_str(resp[-2:])}")
            return False
        vlog(f"    Type:  SW={hex_str(resp[-2:])}")

        print("[+] Factory reset complete. Try `write <image>` to verify.")
        return True

    def read_image_info(self) -> tuple[bool, bool]:
        """Read 2-byte image flip flags. Returns (flip_h, flip_v)."""
        resp = self.cmd_read_image_info()
        if len(resp) < 4:
            return False, False
        data = strip_sw(resp)
        return (len(data) >= 1 and data[0] == 0x01,
                len(data) >= 2 and data[1] == 0x01)

    def write_image_d2(self, image_data: bytes, section: int = 0) -> bool:
        """Write image_data using D2 (raw, uncompressed) APDUs.

        Each APDU carries up to 250 bytes; last packet can be shorter.
        Counterpart of `write_image_d3` -- same image bytes, no LZO
        compression.
        """
        total = len(image_data)
        if total == 0:
            raise ValueError("Empty image data")

        n_full    = total // _SUB_CHUNK_SIZE
        remainder = total %  _SUB_CHUNK_SIZE
        n_chunks  = n_full + (1 if remainder else 0)
        print("[*] Writing image (D2 / raw)...")
        vlog(f"  {total} bytes -> {n_chunks} raw chunks of {_SUB_CHUNK_SIZE} (section={section})")

        seq = 0
        for i in range(n_full):
            chunk = image_data[i * _SUB_CHUNK_SIZE : (i + 1) * _SUB_CHUNK_SIZE]
            resp  = self.cmd_load_image_raw(section, seq, chunk)
            if not check_sw(resp):
                print(f"    [!] D2 chunk {seq} failed  SW={hex_str(resp[-2:])}")
                return False
            seq += 1
            self._print_progress(seq, n_chunks, final=False)

        if remainder:
            chunk = image_data[n_full * _SUB_CHUNK_SIZE :]
            resp  = self.cmd_load_image_raw(section, seq, chunk)
            if not check_sw(resp):
                print(f"    [!] D2 last chunk failed  SW={hex_str(resp[-2:])}")
                return False
            seq += 1

        self._print_progress(n_chunks, n_chunks, final=True)
        print("[+] Write complete.")
        return True

    def write_image_d3(self, image_data: bytes, section: int = 0) -> bool:
        """Write image_data using D3 (LZO1x-1 compressed) APDUs.

        Image data is split into 2000-byte blocks, each compressed and
        sent as <=250-byte sub-chunks. Counterpart of `write_image_d2`;
        use this when you want the upload to be smaller over NFC.
        """
        total = len(image_data)
        if total == 0:
            raise ValueError("Empty image data")

        n_full    = total // _BLOCK_SIZE
        remainder = total %  _BLOCK_SIZE
        n_blocks  = n_full + (1 if remainder else 0)
        print("[*] Writing image (D3 / LZO)...")
        vlog(f"  {total} bytes -> {n_blocks} LZO blocks of {_BLOCK_SIZE} (section={section})")

        for block_idx in range(n_full):
            block      = image_data[block_idx * _BLOCK_SIZE : (block_idx + 1) * _BLOCK_SIZE]
            compressed = minilzo.compress(block)
            vlog(f"  block {block_idx}: {len(block)} -> {len(compressed)} bytes")
            if not self._write_d3_block(compressed, section, block_idx):
                return False
            self._print_progress(block_idx + 1, n_blocks, final=False)

        if remainder:
            last_idx   = n_full
            block      = image_data[n_full * _BLOCK_SIZE :]
            compressed = minilzo.compress(block)
            vlog(f"  block {last_idx} (partial): {len(block)} -> {len(compressed)} bytes")
            if not self._write_d3_block(compressed, section, last_idx):
                return False

        self._print_progress(n_blocks, n_blocks, final=True)
        print("[+] Write complete.")
        return True

    def _write_d3_block(self, compressed: bytes, section: int, block_idx: int) -> bool:
        """Send one LZO-compressed block as one or more D3 sub-chunk APDUs."""
        n = len(compressed)

        # Block fits in a single sub-chunk: P2=1, sub_idx=0, Lc=n+2.
        if n <= _SUB_CHUNK_SIZE:
            resp = self.cmd_write_d3_subchunk(section, block_idx, 0,
                                              compressed, is_last=True)
            if not check_sw(resp):
                print(f"    [!] D3 block {block_idx} failed  SW={hex_str(resp[-2:])}")
                return False
            return True

        n_full    = n // _SUB_CHUNK_SIZE
        remainder = n %  _SUB_CHUNK_SIZE

        for sub_idx in range(n_full):
            sub_data = compressed[sub_idx * _SUB_CHUNK_SIZE : (sub_idx + 1) * _SUB_CHUNK_SIZE]
            is_last  = (remainder == 0 and sub_idx == n_full - 1)
            resp = self.cmd_write_d3_subchunk(section, block_idx, sub_idx,
                                              sub_data, is_last=is_last)
            if not check_sw(resp):
                print(f"    [!] D3 block {block_idx} sub-chunk {sub_idx} failed  "
                      f"SW={hex_str(resp[-2:])}")
                return False

        if remainder:
            sub_data = compressed[n_full * _SUB_CHUNK_SIZE :]
            resp = self.cmd_write_d3_subchunk(section, block_idx, n_full,
                                              sub_data, is_last=True)
            if not check_sw(resp):
                print(f"    [!] D3 block {block_idx} last sub-chunk failed  "
                      f"SW={hex_str(resp[-2:])}")
                return False

        return True

    @staticmethod
    def _print_progress(done: int, total: int, final: bool) -> None:
        pct = done * 100 // total
        bar = "#" * (pct // 5) + "-" * (20 - pct // 5)
        end = "\n" if final else "\r"
        print(f"    [{bar}] {pct:3d}%", end=end, flush=True)
