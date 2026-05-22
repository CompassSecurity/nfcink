"""
nfcink.runner -- backend-agnostic tag callback and refresh state machine.

run_on_tag is the entry point invoked by each transport backend when a tag
or card is detected. It dispatches based on args.command (read / write /
badge / refresh).

Returns True  -- session complete; backend stops scanning.
Returns False -- session dropped mid-refresh; backend should reconnect and
                 call run_on_tag again to re-write and retry the refresh.

Refresh protocol:

  1. Send F0D4050000 (P1=0x05).
  2. On 68C6 -> resend F0D4050000 once (z=true).
  3. On 68C6 again -> send F0D4850000 (z=z2=true). This APDU BLOCKS ~14-15 s
     while the device computes the e-ink waveform, then returns 9000 / 009000
     / 019000.
  4. On 019000 -> success; device draws autonomously, no poll needed.
  5. On 9000 / 009000 -> single F0DE000001 poll.
  6. Poll accepts 009000 OR 019000 as success. 68C6 -> recurse from step 1.
  7. 6986 -> retry from start, up to 5x.
  8. 68CA -> device busy; retransmit current APDU once, accept bare 9000.
  9. 698A -> hardware error, fail.

There is no long polling loop: a single poll is sufficient.
"""

import time
from typing import TypedDict

from .constants import (
    SW_OK, SW_COLOR_SCREEN, SW_6986, SW_698A, SW_68CA,
    APDU_REFRESH_INIT, APDU_REFRESH_ALT, APDU_POLL_REFRESH,
    SCREEN_WIDTH, SCREEN_HEIGHT,
    hex_str, vlog,
)
from .protocol import NfcInkDevice, strip_sw
from .transport import TagDropError
from .image import image_to_device_bytes, quantise_image, pixels_to_bytes
from .compose import compose_badge


class _State(TypedDict, total=False):
    result: bool | None       # True=success, False=failure, None=in progress
    _drop_attempts: int       # tag-drop counter during refresh


# ---- Refresh state machine --------------------------------------------------

_MAX_6986_RETRIES = 5


def _send_refresh(device: NfcInkDevice, state: _State,
                  retried_once: bool, use_alt: bool,
                  retries_6986: int, section: int = 0) -> bool:
    """Refresh state machine (escalating: F0D4050000 → F0D4050000 → F0D4850000).

    retried_once -- True once we have sent the initial APDU and received 68C6.
    use_alt      -- True once we have escalated to F0D4850000 (P1=0x85).
    section      -- image-slot index to display (0..2); becomes P2 of the F0D4.

    Returns True  -- operation finished; state['result'] is set.
    Returns False -- tag dropped; caller should reconnect and retry.
    """
    if use_alt:
        timeout  = 50.0
        apdu_hex = f"F0D485{section & 0x7F:02X}00"
    else:
        timeout  = 10.0
        apdu_hex = f"F0D405{section & 0x7F:02X}00"

    vlog(f"  refresh TX: {apdu_hex}  (retried_once={retried_once} use_alt={use_alt})")
    print(f"    Sending {apdu_hex} ...", flush=True)

    try:
        if use_alt:
            resp = device.cmd_refresh_alt(section=section, timeout=timeout)
        else:
            resp = device.cmd_refresh_init(section=section, timeout=timeout)
    except TagDropError:
        # Tag dropped during refresh.
        # TagLostException at F0D4850000 triggers reconnect + re-write + retry.
        # The device loses its write buffer on RF loss, so the full write must
        # be repeated before the next refresh attempt can succeed.
        # Leave state['result'] as None so the backend reconnects and
        # run_on_tag reruns the full write + refresh cycle.
        attempts = state.get('_drop_attempts', 0) + 1
        state['_drop_attempts'] = attempts
        if attempts >= 3:
            print(f"    [!] Tag dropped during refresh {attempts} times -- giving up.")
            state['result'] = False
            return True   # done, failed
        apdu_label = apdu_hex
        print(f"    Tag dropped during {apdu_label} (attempt {attempts}/3).")
        print("    Keep badge on reader -- reconnecting, re-writing, and retrying...")
        return False  # signal backends to reconnect and rerun run_on_tag

    sw   = resp[-2:] if len(resp) >= 2 else b''
    data = strip_sw(resp)
    vlog(f"  refresh RX: {hex_str(resp)}")

    if sw == SW_698A:
        print(f"    [!] Device hardware error  SW={hex_str(sw)}")
        state['result'] = False
        return True

    if sw == SW_6986:
        if retries_6986 > 0:
            vlog(f"  SW=6986: retry from start ({_MAX_6986_RETRIES - retries_6986 + 1}/{_MAX_6986_RETRIES})")
            return _send_refresh(device, state,
                                 retried_once=False, use_alt=False,
                                 retries_6986=retries_6986 - 1,
                                 section=section)
        print(f"    [!] Device rejected refresh after {_MAX_6986_RETRIES} retries (SW=6986)")
        state['result'] = False
        return True

    if sw == SW_COLOR_SCREEN:
        if not retried_once:
            # After the first 68C6 the badge briefly stops responding while
            # it transitions state; 250 ms delay observed in reference traces.
            time.sleep(0.25)
            return _send_refresh(device, state,
                                 retried_once=True, use_alt=False,
                                 retries_6986=retries_6986,
                                 section=section)
        if not use_alt:
            return _send_refresh(device, state,
                                 retried_once=True, use_alt=True,
                                 retries_6986=retries_6986,
                                 section=section)
        print("    [!] Refresh rejected after F0D4850000 (SW=68C6)")
        state['result'] = False
        return True

    # 019000 -- success, drawing continues autonomously.
    if sw == SW_OK and data and data[0] == 0x01:
        print("[+] Refresh accepted (device drawing autonomously).")
        state.pop('_drop_attempts', None)
        state['result'] = True
        return True

    # 9000 / 009000 -- poll once for final status.
    if sw == SW_OK:
        return _poll_once(device, state, retries_6986, section)

    # 68CA -- device busy. Retransmit the same APDU once more (same P2)
    # and accept bare 9000 as success.
    if sw == SW_68CA:
        vlog("  SW=68CA: device busy, retrying once")
        try:
            if use_alt:
                resp2 = device.cmd_refresh_alt(section=section, timeout=50.0)
            else:
                resp2 = device.cmd_refresh_init(section=section, timeout=10.0)
        except TagDropError:
            state['result'] = False
            return True
        if resp2[-2:] == SW_OK and (len(resp2) == 2):
            print("[+] Refresh accepted after 68CA retry.")
            state.pop('_drop_attempts', None)
            state['result'] = True
        else:
            print(f"    [!] Refresh failed after 68CA retry  SW={hex_str(resp2[-2:])}")
            state['result'] = False
        return True

    print(f"    [!] Unexpected refresh SW={hex_str(sw)}")
    state['result'] = False
    return True


def _poll_once(device: NfcInkDevice, state: _State, retries_6986: int,
               section: int = 0) -> bool:
    """Send one F0DE000001 poll and interpret the response.

    Accepts 009000 or 019000 as success. `section` is passed through so
    that a 68C6 response (which restarts the refresh) targets the same slot.
    """
    vlog(f"  poll TX: {APDU_POLL_REFRESH}")
    try:
        resp = device.cmd_poll_refresh(timeout=50.0)
    except TagDropError:
        # Drop after a successful refresh-init still means the device is
        # drawing -- treat as success.
        print("    Tag dropped during poll -- drawing autonomously.")
        state['result'] = True
        return True

    sw   = resp[-2:] if len(resp) >= 2 else b''
    data = strip_sw(resp)
    vlog(f"  poll RX: {hex_str(resp)}")
    print(f"    <- {hex_str(resp)}")

    if sw == SW_OK and (not data or data[0] in (0x00, 0x01)):
        print("[+] Refresh complete.")
        state.pop('_drop_attempts', None)
        state['result'] = True
        return True

    if sw == SW_COLOR_SCREEN:
        # 68C6 in the poll response -- escalate refresh from start.
        return _send_refresh(device, state,
                             retried_once=False, use_alt=False,
                             retries_6986=retries_6986,
                             section=section)

    print(f"    [!] Poll error  SW={hex_str(sw)}")
    state['result'] = False
    return True


def start_refresh(device: NfcInkDevice, state: _State,
                  section: int = 0) -> bool:
    """Drive the full refresh sequence.

    section -- image-slot index to display (0..pictureCapacity-1).

    Returns True  -- complete; state['result'] is set.
    Returns False -- tag dropped; caller should reconnect and retry.
    """
    print(f"[*] Refreshing screen (slot {section})...")
    return _send_refresh(device, state,
                         retried_once=False, use_alt=False,
                         retries_6986=_MAX_6986_RETRIES,
                         section=section)


# ---- Tag callback -----------------------------------------------------------

def run_on_tag(transport, args, state: _State) -> bool:
    """Tag/card callback invoked by every backend on tag detection.

    transport -- a PcscTransport or AdbTransport instance.
    args      -- argparse Namespace with command-specific attributes.
    state     -- shared dict; state['result'] is set to True/False on completion.

    Returns True  -- session complete; backend should stop scanning.
    Returns False -- session dropped mid-refresh; backend should reconnect and
                     call run_on_tag again to re-write and retry the refresh.
    """
    device = NfcInkDevice(transport)
    print(f"[+] Tag detected: {transport}")

    if args.command == "read":
        device.read_config()
        flip_h, flip_v = device.read_image_info()
        vlog(f"  flip_h={flip_h}  flip_v={flip_v}")
        state["result"] = True
        return True

    if args.command == "current-slot":
        resp = device.cmd_get_image_sn()
        sw = resp[-2:] if len(resp) >= 2 else b""
        data = resp[:-2]
        if sw == SW_OK and len(data) >= 1:
            print(f"[+] Currently displayed slot: {data[0]}")
            state["result"] = True
        else:
            print(f"[!] Unexpected response  SW={hex_str(sw)}  data={hex_str(data)}")
            state["result"] = False
        return True

    if args.command == "write":
        cfg = device.read_config()
        if not 0 <= args.section < cfg.picture_capacity:
            print(f"[!] --section {args.section} out of range "
                  f"(badge has {cfg.picture_capacity} slots, "
                  f"valid: 0..{cfg.picture_capacity - 1})")
            state['result'] = False
            return True
        force_bw   = getattr(args, 'bw', False)
        image_data = image_to_device_bytes(args.image, cfg, force_bw=force_bw)
        vlog(f"  image: {len(image_data)} bytes  (force_bw={force_bw}, compression={args.compression})")
        if args.compression == "none":
            ok = device.write_image_d2(image_data, section=args.section)
        else:
            ok = device.write_image_d3(image_data, section=args.section)
        if not ok:
            state['result'] = False
            return True
        return start_refresh(device, state, section=args.section)

    if args.command == "badge":
        cfg      = device.read_config()
        force_bw = getattr(args, 'bw', False)
        print("[*] Composing badge...")
        img = compose_badge(
            args.photo, args.name,
            company_name=args.company,
            logo_path=args.logo,
            barcode=args.barcode,
        )
        image_data = image_to_device_bytes(img, cfg, force_bw=force_bw)
        vlog(f"  image: {len(image_data)} bytes  (force_bw={force_bw})")
        if not device.write_image_d3(image_data):
            state['result'] = False
            return True
        return start_refresh(device, state)

    if args.command == "refresh":
        cfg = device.read_config()
        if not 0 <= args.section < cfg.picture_capacity:
            print(f"[!] --section {args.section} out of range "
                  f"(badge has {cfg.picture_capacity} slots, "
                  f"valid: 0..{cfg.picture_capacity - 1})")
            state['result'] = False
            return True
        return start_refresh(device, state, section=args.section)

    if args.command == "factory-reset":
        # Re-upload the canonical driver flow. Do NOT call read_config() first
        # -- this command must work even when D1 returns 6451 (no driver flow
        # loaded, the post-F0DB02 state).
        state['result'] = device.factory_reset()
        return True

    if args.command == "clear":
        from PIL import Image
        cfg        = device.read_config()
        white      = Image.new("RGB", (SCREEN_WIDTH, SCREEN_HEIGHT), (255, 255, 255))
        pixels     = quantise_image(white, cfg)
        image_data = pixels_to_bytes(pixels, cfg)
        if not device.write_image_d3(image_data):
            state['result'] = False
            return True
        return start_refresh(device, state)

    return True
