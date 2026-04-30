"""
nfcink -- driver for 4-colour e-ink NFC display badges.

Public API:
    Constants : SCREEN_WIDTH, SCREEN_HEIGHT
                COLOR_BLACK, COLOR_WHITE, COLOR_RED, COLOR_YELLOW, PALETTE
                SW_OK, SW_COLOR_SCREEN, SW_68CA, SW_6986, SW_698A
    Config    : DeviceCfg
    Image     : quantise_image, pixels_to_bytes, image_to_device_bytes
    Compose   : compose_badge
    Protocol  : NfcInkDevice
    Transport : PcscTransport, TagDropError, open_pcsc
    Runner    : run_on_tag
    Logging   : set_verbose
"""

__version__ = "0.1.0"

from .constants import (
    SCREEN_WIDTH, SCREEN_HEIGHT, CHUNK_SIZE,
    COLOR_BLACK, COLOR_WHITE, COLOR_RED, COLOR_YELLOW, PALETTE,
    APDU_READ_CONFIG, APDU_READ_IMAGE_INFO, APDU_DEVICE_CHECK,
    APDU_REFRESH_INIT, APDU_REFRESH_ALT, APDU_POLL_REFRESH,
    SW_OK, SW_NO_PIN, SW_COLOR_SCREEN, SW_68CA, SW_6986, SW_698A,
    set_verbose, vlog, hex_str,
)
from .config import DeviceCfg
from .image import quantise_image, pixels_to_bytes, image_to_device_bytes
from .compose import compose_badge
from .protocol import NfcInkDevice
from .transport import PcscTransport, TagDropError, open_pcsc
from .runner import run_on_tag

__all__ = [
    "__version__",
    # screen geometry
    "SCREEN_WIDTH", "SCREEN_HEIGHT",
    # colour palette
    "COLOR_BLACK", "COLOR_WHITE", "COLOR_RED", "COLOR_YELLOW", "PALETTE",
    # NFC status words
    "SW_OK", "SW_COLOR_SCREEN", "SW_68CA", "SW_6986", "SW_698A",
    # classes
    "DeviceCfg", "NfcInkDevice",
    "PcscTransport", "TagDropError", "open_pcsc",
    # functions
    "quantise_image", "pixels_to_bytes", "image_to_device_bytes",
    "compose_badge", "run_on_tag",
    # logging
    "set_verbose",
]
