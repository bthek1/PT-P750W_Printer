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

The project is a `uv` package with two commands, `pt-print` and `pt-status`.
Create the virtual environment once:

```sh
uv sync
```

Run the commands with `uv run`, or through the `just` recipes:

```sh
uv run pt-print Hello world
uv run pt-status
```

To put the commands on your `PATH` for use from any directory:

```sh
just install    # uv tool install
```

## Permissions

The printer device belongs to group `lp` by default. Install the udev rule once
so members of `plugdev` can use it without root:

```sh
just install-udev
```

## Usage

```sh
just status                  # tape width, media type, colours, errors
just print "Hello"           # print a text label on the loaded tape
just print "Hello" --flip    # reverse pin order (not needed normally)
just print "Hello" --no-cut
just test                    # hermetic tests, no printer needed
```

Preview a label without printing (writes `label.png` and `label.bin`):

```sh
uv run pt-print Hello --tape 24 --dry-run label
```

`print` reads the status first and refuses to print if the printer reports an
error.

## Files

| Path | Purpose |
|---|---|
| `src/pt_p750w/status.py` | `pt-status`: reads and decodes live printer status over USB |
| `src/pt_p750w/printing.py` | `pt-print`: renders text and prints it as a label |
| `tests/` | Hermetic tests for the print job and status decoding |
| `udev/99-pt-p750w.rules` | Lets the `plugdev` group use the printer |
| `justfile` | `status`, `print`, `test`, `install` and `install-udev` recipes |
| `AGENTS.md` | Notes and rules for AI agents |

## Troubleshooting

| Symptom | Cause | Fix |
|---|---|---|
| `No PT-P750W (04f9:2062) found` | Printer off, Editor Lite on, or port not forwarded | Switch it on, turn Editor Lite off, check `lsusb -d 04f9:` |
| `No access to /dev/usb/lpN` | udev rule missing or not applied | `just install-udev`, or switch the printer off and on |
| `lsusb` shows `04f9:2065` | Editor Lite mode | Hold the Editor Lite button until the LED goes off |
| `Printer not ready: replace media` | Cassette not seated, or a latched error | Reseat the tape cassette, close the cover, switch the printer off and on |

## Status

USB forwarding and live status work (verified on 24 mm laminated tape).
Status and printing work. Verified on 24 mm laminated tape: the label prints,
cuts and reads correctly.
