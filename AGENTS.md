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
  scripts with `uv run`. No third-party dependencies yet.
- `pt_status.py`: stdlib-only. Finds `/dev/usb/lpN` for `04f9:2062` through
  sysfs, sends invalidate + `ESC @` + `ESC i S`, decodes the 32-byte status
  reply. Run with `just status`.
- `udev/99-pt-p750w.rules`: gives group `plugdev` access to the printer (USB and
  usblp nodes). The user is in `plugdev`, not `lp`. Install with
  `just install-udev` (needs the user's sudo password; agents cannot run it).
- CUPS is active on the dev VM and may briefly re-bind usblp on hotplug.

## Docs

README.md holds the human setup steps. Keep it in sync with this file.
Do not put IPs, VM IDs, serial numbers or home paths in tracked files.

## Status

As of 2026-11-10 the printer is visible in the VM in printer mode
(`04f9:2062`, `/dev/usb/lpN`). The udev rule is installed. `just status` is
verified on hardware: model code `0x68`, 24 mm laminated tape, black on white.
Known-good raw reply:
`80 20 42 30 68 30 04 00 00 00 18 01 00 00 00 00 00 00 00 00 00 00 00 00 01 08 00 00 00 00 00 00`.
No printing code yet.
