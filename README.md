# PT-P750W Printer

Print labels on a Brother PT-P750W from a Linux VM.

## Setup

The printer connects by USB to the Proxmox host. The host forwards the USB port
to the VM.

1. On the Proxmox host, find the printer's port:

   ```sh
   lsusb -d 04f9:
   grep -l 04f9 /sys/bus/usb/devices/*/idVendor
   ```

   The directory name (for example `5-3`) is `<bus>-<port>`.

2. Forward that port to the VM:

   ```sh
   sudo qm set <vmid> -usb0 host=<bus>-<port>
   ```

   This works while the VM runs.

3. In the VM, confirm the printer is visible:

   ```sh
   lsusb -d 04f9:
   ```

Forward by port, not by device ID. The product ID changes with the printer
mode, and an ID-based forward loses the printer when it does.

## Printer modes

| LED | Product ID | VM sees it as |
|---|---|---|
| Editor Lite on | `04f9:2065` | USB drive (4 MiB) |
| Editor Lite off | `04f9:2062` | Printer |

Hold the Editor Lite button until the LED goes off before printing.

## Python environment

The project uses `uv`. Create the virtual environment once:

```sh
uv sync
```

`just` recipes run the scripts through `uv run`.

## Permissions

The printer device belongs to group `lp` by default. Install the udev rule once
so members of `plugdev` can use it without root:

```sh
just install-udev
```

## Usage

```sh
just status   # tape width, media type, colours, errors
```

## Files

| Path | Purpose |
|---|---|
| `pt_status.py` | Reads and decodes live printer status over USB |
| `udev/99-pt-p750w.rules` | Lets the `plugdev` group use the printer |
| `justfile` | `status` and `install-udev` recipes |
| `AGENTS.md` | Notes and rules for AI agents |

## Troubleshooting

| Symptom | Cause | Fix |
|---|---|---|
| `No PT-P750W (04f9:2062) found` | Printer off, Editor Lite on, or port not forwarded | Switch it on, turn Editor Lite off, check `lsusb -d 04f9:` |
| `No access to /dev/usb/lpN` | udev rule missing or not applied | `just install-udev`, or switch the printer off and on |
| `lsusb` shows `04f9:2065` | Editor Lite mode | Hold the Editor Lite button until the LED goes off |

## Status

USB forwarding and live status work (verified on 24 mm laminated tape).
No printing code yet. Next: print a test label.
