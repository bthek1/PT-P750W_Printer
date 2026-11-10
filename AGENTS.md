# AGENTS.md

Agent rules for this repo. CLAUDE.md only imports this file.

## Purpose

Drive a Brother PT-P750W label printer from Linux.

## Hardware facts

- PT-P750W USB IDs: vendor `04f9`. Product `2062` in normal (raster) mode.
  Product `2065` when the "Editor Lite" LED is on; it then shows up as USB mass
  storage, not as a printer. Hold the Editor Lite button until the LED is off.
- In Editor Lite mode the drive holds the Windows/macOS Editor Lite app.
  `Ptlite10/PRTINFO.INI` lists the model capabilities (180 dpi, 128-pin head,
  tape sizes, auto/half cut). `PRTSTATE.STS` is a 512-byte placeholder
  ("1234567890..."), not live status. No device settings are exposed there.
- Network: raw printing on TCP 9100, SNMP status on UDP 161.
  Wi-Fi Direct mode puts the printer on its own access point (`DIRECT-*_PT-P750W`).
- The printer is plugged into the Proxmox host. The dev machine is a VM on that
  host. The VM config passes the host USB **port** through (`usbN: host=<bus>-<port>`),
  not the vendor/product ID. Keep it port-based: the product ID changes when
  Editor Lite mode is toggled, and an ID-based passthrough would drop the printer.
- Check on the host: `sudo qm config <vmid> | grep ^usb` and
  `echo "info usb" | sudo qm monitor <vmid>`.
- Find the printer's host port: `lsusb -d 04f9:` on the host, then match
  `/sys/bus/usb/devices/*/idVendor` to get `<bus>-<port>`.
- Attach (or re-attach after removal): `sudo qm set <vmid> -usb0 host=<bus>-<port>`.
  This hotplugs into the running VM. No restart needed. Confirm in the VM with
  `lsusb -d 04f9:`.
- If the printer is off when the VM checks, it is simply absent. Switch it on
  and re-run `lsusb -d 04f9:`.
- The LAN has a different Brother printer (HL-L2305 laser) on port 9100 and in
  CUPS. Do not confuse it with the PT-P750W.

## How to find the printer

1. USB: `lsusb -d 04f9:` and `ls /dev/usb/lp*`.
2. LAN: `nmap -n -Pn -p 9100 --open <subnet>`, then check each hit with
   `ipptool` or SNMP. Only the PT-P750W model counts.

## Code

- Python env: `uv` project (`pyproject.toml`, `uv.lock`, `.venv/`). Run
  scripts with `uv run`. Only dependency: Pillow (text rendering).
- Spec: Brother "Raster Command Reference" for PT-E550W/P750W/P710BT,
  `https://download.brother.com/welcome/docp100064/cv_pte550wp750wp710bt_eng_raster_102.pdf`.
  Not stored in the repo. Extract with `pdftotext -layout`. Use it, not memory.
- `pt_print.py`: renders text with Pillow (DejaVu Sans Bold) to a 1-bit image,
  one raster line per column, and sends: invalidate, `ESC @`, `ESC i a 01`,
  `ESC i z`, `ESC i M 40` (auto cut), `ESC i A 01`, `ESC i K 08` (no chain),
  `ESC i d 0E 00` (2 mm feed), `M 00` (no compression), `G 10 00` + 16 bytes per
  line, `1A`. Checks status first and refuses to print on any error. Then waits
  for the unsolicited "printing completed" reply. `--dry-run PREFIX` writes a
  PNG preview and the raw job. `--flip` reverses pin order.
- `pt_status.py`: stdlib-only. Finds `/dev/usb/lpN` for `04f9:2062` through
  sysfs, sends invalidate + `ESC @` + `ESC i S`, decodes the 32-byte status
  reply. Run with `just status`.
- `udev/99-pt-p750w.rules`: gives group `plugdev` access to the printer (USB and
  usblp nodes). The user is in `plugdev`, not `lp`. Install with
  `just install-udev` (needs the user's sudo password; agents cannot run it).
- `just install-udev` must trigger both the USB device (`--attr-match=idVendor=04f9`)
  and the `usbmisc` subsystem. The `lp` node has no `idVendor` attribute, so
  the first trigger alone leaves it as `root:lp`. Debug with
  `udevadm test /sys/class/usbmisc/lpN 2>&1 | grep 99-pt`.
- CUPS is active on the dev VM and may briefly re-bind usblp on hotplug.

### Status reply (32 bytes, verified offsets)

| Byte | Meaning | Seen |
|---|---|---|
| 0 | Print head mark, always `0x80` | `0x80` |
| 4 | Model code (`0x68` = PT-P750W) | `0x68` |
| 8, 9 | Error bit fields 1 and 2 | `0x00 0x00` |
| 10 | Tape width in mm | `0x18` (24) |
| 11 | Media type | `0x01` laminated |
| 18 | Status type | `0x00` reply |
| 24 | Tape colour | `0x01` white |
| 25 | Text colour | `0x08` black |

All codes in `pt_status.py` are checked against the spec (section 4,
status tables). Pin map from the spec (section 2.3.5), as
(left margin, print area) pins on the 128-pin head: 3.5 mm (52, 24),
6 mm (48, 32), 9 mm (39, 50), 12 mm (29, 70), 18 mm (8, 112), 24 mm (0, 128).
3.5 mm tape reports width 4. Minimum label length 31 dots (4.4 mm).

## Docs

README.md holds the human setup steps. Keep it in sync with this file.
Do not put IPs, VM IDs, serial numbers or home paths in tracked files.

## Status

As of 2026-11-10 the printer is visible in the VM in printer mode
(`04f9:2062`, `/dev/usb/lpN`). The udev rule is installed. `just status` is
verified on hardware: model code `0x68`, 24 mm laminated tape, black on white.
Known-good raw reply:
`80 20 42 30 68 30 04 00 00 00 18 01 00 00 00 00 00 00 00 00 00 00 00 00 01 08 00 00 00 00 00 00`.
`pt_print.py` is written and a dry run matches the spec's example header.
It has not printed yet: after the dry run the printer reported
"replace media" (error info 2, bit 0) with 24 mm tape still detected. Cause
unknown. Unverified on hardware: pin orientation (whether `--flip` is needed)
and the completion reply handling.
