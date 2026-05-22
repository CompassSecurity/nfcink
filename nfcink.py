#!/usr/bin/env python3
"""
nfcink.py — CLI for the 4-colour e-ink NFC display badge.

Requires a CCID-compliant contactless reader with the ACS PC/SC driver
installed so the "PICC 0" interface is visible.  See README for setup.

Usage:
    python nfcink.py read
    python nfcink.py write <image.png>
    python nfcink.py badge <photo.jpg> --name "Jane Doe" [--company "Acme" | --logo logo.png]
    python nfcink.py refresh
    python nfcink.py clear
    python nfcink.py write <image.png> -v
"""

import signal
import sys
import threading
import argparse

from nfcink import set_verbose, run_on_tag
from nfcink.transport import PcscTransport, TagDropError, open_pcsc

_interrupted = threading.Event()


def _setup_sigint() -> None:
    signal.signal(signal.SIGINT, lambda *_: _interrupted.set())


def _run(args: argparse.Namespace, state: dict) -> None:
    try:
        from smartcard.System import readers as pcsc_readers
        rs = pcsc_readers()
        reader = next((r for r in rs if "PICC" in str(r).upper()),
                      rs[0] if rs else None)
        if reader:
            print(f"[*] PC/SC reader: {reader}")
    except ImportError:
        print("[!] pyscard not installed.  Run: pip install pyscard")
        sys.exit(1)
    except Exception:
        pass

    print("[*] Place badge flat on the reader and hold it still...")

    while state["result"] is None and not _interrupted.is_set():
        try:
            transport = open_pcsc(timeout=120.0)
        except RuntimeError as exc:
            print(f"[!] {exc}")
            sys.exit(1)
        except TagDropError as exc:
            print(f"[!] {exc}")
            state["result"] = False
            return

        print(f"[+] Badge detected: {transport}")

        try:
            done = run_on_tag(transport, args, state)
        except KeyboardInterrupt:
            _interrupted.set()
            done = True
        except Exception as exc:
            print(f"[!] Unexpected error: {exc}")
            state["result"] = False
            done = True
        finally:
            transport.close()

        if done or state["result"] is not None:
            break

        print("[*] Session dropped — place badge on reader again...")
        _interrupted.wait(timeout=1.0)


def main() -> None:
    _setup_sigint()
    parser = argparse.ArgumentParser(
        description="4-colour e-ink NFC display badge"
    )
    parser.add_argument("-v", "--verbose", action="store_true",
                        help="Print raw APDU bytes for debugging")
    parser.add_argument("--reader", default="0",
                        help="PC/SC reader index (default: 0)")
    sub = parser.add_subparsers(dest="command", required=True)

    # Shared defaults — every subcommand receives all keys so run_on_tag
    # can access them without hasattr guards.
    parser.set_defaults(image=None, section=0, photo=None, name=None,
                        company=None, logo=None, barcode=None, bw=False)

    sub.add_parser("read", help="Read and display device configuration")

    p_write = sub.add_parser("write", help="Write an image to the display")
    p_write.add_argument("image", help="Path to image file (PNG/JPEG/...)")
    p_write.add_argument("--section", type=int, default=0,
                         help="Image slot index (default 0)")
    p_write.add_argument("--bw", action="store_true",
                         help="Force black+white only (2-colour waveform)")

    p_badge = sub.add_parser("badge", help="Compose and write a badge")
    p_badge.add_argument("photo", help="Path to portrait photo")
    p_badge.add_argument("--name", required=True, help="Person name (bold all-caps)")
    p_badge.add_argument("--company", default=None, help="Company name")
    p_badge.add_argument("--logo", default=None, help="Path to logo image")
    p_badge.add_argument("--barcode", default=None,
                         help="Text to render as Code 128B barcode (up to 14 chars)")
    p_badge.add_argument("--bw", action="store_true",
                         help="Force black+white only (2-colour waveform)")

    p_refresh = sub.add_parser("refresh", help="Trigger a screen refresh")
    p_refresh.add_argument("--section", type=int, default=0,
                           help="Image slot index to display (default 0)")
    sub.add_parser("clear",   help="Write a blank white screen")
    sub.add_parser("factory-reset",
                   help="Re-upload the canonical driver flow (recovers a badge whose screen no longer refreshes)")

    args = parser.parse_args()

    if args.verbose:
        set_verbose(True)

    state: dict = {"result": None}  # _State
    _run(args, state)

    if state["result"] is not None:
        sys.exit(0 if state["result"] else 1)


if __name__ == "__main__":
    main()
