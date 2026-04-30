"""
nfcink.transport -- APDU transport layer.

Defines the transport contract (TagDropError + the transceive/close interface)
and the built-in PC/SC implementation.  Additional transports (e.g. an Android
ADB bridge) can be added by implementing the same interface:

    class MyTransport:
        def transceive(self, apdu: bytes) -> bytes: ...
        def close(self) -> None: ...
        def __str__(self) -> str: ...
"""

import time


class TagDropError(Exception):
    """Raised by any transport when the NFC tag or smart card is unreachable."""


# ── PC/SC transport ───────────────────────────────────────────────────────────

class PcscTransport:
    """Wraps a pyscard CardConnection as a generic APDU transport.

    pyscard's transmit() calls SCardTransmit with no timeout, so operations
    that take ~15 s (e.g. the F0D4850000 waveform refresh) complete normally.
    """

    def __init__(self, connection) -> None:
        self._conn = connection

    def __str__(self) -> str:
        try:
            return f"PCSC({self._conn.component.reader})"
        except AttributeError:
            return "PCSC(reader)"

    def transceive(self, apdu: bytes, timeout: float | None = None) -> bytes:  # noqa: ARG002
        try:
            data, sw1, sw2 = self._conn.transmit(list(apdu))
            return bytes(data) + bytes([sw1, sw2])
        except Exception as exc:
            raise TagDropError(str(exc)) from exc

    def close(self) -> None:
        try:
            self._conn.disconnect()
        except Exception:
            pass


def open_pcsc(reader_index: int = 0, timeout: float = 120.0) -> PcscTransport:
    """Wait for a card on a PC/SC reader and return a connected PcscTransport.

    Prefers the PICC (contactless NFC) interface when the reader exposes
    multiple interfaces (e.g. ACS ACR1552 shows both PICC 0 and SAM 0).
    Falls back to reader_index if no PICC interface is found.
    """
    try:
        from smartcard.System import readers as pcsc_readers
    except ImportError:
        raise RuntimeError("pyscard not installed.  Run: pip install pyscard")

    rs = pcsc_readers()
    if not rs:
        raise RuntimeError("No PC/SC readers found")

    reader = next(
        (r for r in rs if "PICC" in str(r).upper()),
        rs[min(reader_index, len(rs) - 1)],
    )

    deadline  = time.monotonic() + timeout
    last_exc: Exception | None = None

    while time.monotonic() < deadline:
        try:
            conn = reader.createConnection()
            conn.connect()
            return PcscTransport(conn)
        except Exception as exc:
            last_exc = exc
            time.sleep(0.05)

    raise TagDropError(
        f"Timed out waiting for badge on {reader} (last error: {last_exc})"
    )
