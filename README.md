# nfcink

Vibe-coded, reverse-engineered driver for a 4-colour e-ink NFC display badge
(240 × 416 px, black / white / red / yellow). The CLI reads the device
configuration, writes images, composes name/photo badges, and triggers the
e-ink refresh.

## Layout

```
nfcink/             -- Python package (DeviceCfg, NfcInkDevice, transports, …)
nfcink.py           -- CLI entry point
requirements.txt
```

## Quick start

Requires Python 3.10+ and a CCID-compliant contactless NFC reader
(see [NFC reader](#nfc-reader) below).

### Using `uv` (recommended)

```bash
uv venv
uv pip install -r requirements.txt
uv run nfcink.py read
```

### Using `pip`

```bash
python -m venv .venv
# Linux / macOS:
source .venv/bin/activate
# Windows (PowerShell):
.venv\Scripts\Activate.ps1

pip install -r requirements.txt
python nfcink.py read
```

## CLI

```bash
python nfcink.py [-v] [--reader N] <command> ...
```

Commands:

| Command | Description |
|---------|-------------|
| `read` | Print the parsed device configuration |
| `write <image>` | Quantise, dither and write an image, then refresh |
| `badge <photo> --name "Jane Doe"` | Compose and write a name badge |
| `refresh` | Trigger a screen refresh without rewriting image data |
| `clear` | Write a blank white screen |

Options:
- `--bw` (write / badge) — force 2-colour palette (black + white only)
- `--reader N` — PC/SC reader index (default: 0; PICC interface auto-preferred)
- `-v` — verbose APDU logging

## NFC reader

Uses [pyscard](https://pyscard.sourceforge.io/) to talk to any CCID-compliant
contactless NFC reader through the OS smart-card stack.  `SCardTransmit` has
no built-in timeout, so the 15-second e-ink waveform computation
(`F0D4850000`) completes without the reader dropping the session.

### Recommended hardware

Any ISO 14443-4 compliant CCID reader works.
The **ACS ACR1552U** has been tested and works well.

> **Note — ACR122U compatibility:** the ACR122U reader driven via nfcpy
> (direct USB / WinUSB) cannot sustain the ~15-second `F0D4850000` response
> wait.  The reader's firmware imposes a hard ~5-second NFC operation timeout
> that cannot be overridden in software, causing the session to drop before
> the device responds.  Use a CCID reader with pyscard instead.

### Reader setup

#### Windows (if needed)

The generic Windows CCID driver only exposes the SAM slot. If the PICC
interface does not appear (see verification below), install the ACS CCID
driver from [acs.com.hk](https://www.acs.com.hk/en/driver/3/acr1552-usb-type-c-nfc-reader/),
then unplug and re-plug the reader.

#### Linux

```bash
sudo apt install pcscd libpcsclite-dev swig
sudo systemctl enable --now pcscd
```

#### Verify

Re-plug the reader, then run:

```bash
python -c "from smartcard.System import readers; print(list(readers()))"
# Should show: ACS ACR1552 1S CL Reader PICC 0
```

## No reader? Alternative app

If you don't have a compatible NFC reader, the vendor's own Android app can
write to the badge directly from a phone:
[NetMePro](http://www.netmepro.com/app.html).

> **Use at your own risk.** This is a closed-source third-party application.
> Review its permissions before installing and do not use it with sensitive data.

---

## Protocol notes

- **Image data is LZO-compressed.** The device firmware expects `F0 D3`
  writes with LZO1x-1 compressed 2000-byte blocks split into ≤250-byte
  sub-chunks. Raw `F0 D2` writes cause the device to crash on the first
  refresh APDU.
- **Refresh sequence.** Send `F0D4050000`; on `68C6` resend once; on a
  second `68C6` send `F0D4850000` (blocks ~14.5 s while the e-ink waveform
  is computed); then poll once with `F0DE000001`. Any of `9000`, `009000`
  or `019000` counts as success — the device completes the waveform
  autonomously after the NFC session ends.

See `nfcink/runner.py` and `nfcink/protocol.py` for the full state machine.
