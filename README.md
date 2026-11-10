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

The project is a `uv` package with three commands: `pt-print`, `pt-status`
and `pt-server` (web UI and API).
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
just web-test                # web UI tests, no server needed
just coverage                # coverage for both; fails under 90%
```

Preview a label without printing (writes `label.png` and `label.bin`):

```sh
uv run pt-print Hello --tape 24 --dry-run label
```

Format the label with `--style` (JSON, same fields as the web UI and API):

```sh
uv run pt-print Hello --style '{"font": "serif", "underline": true, "border": true}'
```

`print` reads the status first and refuses to print if the printer reports an
error.

### Web UI

A React page (Vite, shadcn/ui, Vitest) shows live printer status and prints
the text you type. It
needs Node (via `nvm`) for the build.

```sh
just serve      # build the UI, then serve it on http://127.0.0.1:8750
```

The page polls the status every 2 s and reconnects by itself when the server
or printer comes back. It shows a preview of the label at the loaded tape
width. A new line in the text prints as a second line on the tape. Ctrl+Enter
prints.

The toolbar formats the whole label (the printer prints one colour, so
per-character options such as colour, highlight or subscript do not apply):

| Option | Values | Shortcut |
|---|---|---|
| Font (`font`) | `sans`, `condensed`, `serif`, `mono` (DejaVu) | |
| Size (`size_pt`) | `null` = fit the tape, or 4 to 72 pt as a maximum | |
| `bold`, `italic`, `underline`, `strike` | true / false | Ctrl+B, Ctrl+I, Ctrl+U |
| Case (`case`) | `none`, `upper`, `lower`, `title` | |
| Alignment (`align`) | `left`, `center`, `right` | |
| `line_spacing` | 1.0, 1.5, 2.0 | |
| `outline`, `invert` (white on black), `border` | true / false | |

Text always shrinks to fit the tape, so a fixed size is an upper limit.
"Clear formatting" restores the defaults (bold sans, centred, auto size).

`pt-server` binds to `127.0.0.1` by default. `just serve --host 0.0.0.0`
opens it to the LAN. There is no authentication, so anyone who can reach the
port can print.

For UI development, run `uv run pt-server` and `just web-dev` side by side.
Vite (port 5173) forwards `/api` to the server.

| Endpoint | Purpose |
|---|---|
| `GET /api/status` | Live status as JSON (`connected`, `busy`, `error`, `status`) |
| `GET /api/preview?text=...&tape=24&style={...}` | PNG of the label; `tape` and `style` are optional |
| `POST /api/print` | `{"text": "...", "cut": true, "style": {...}}`; returns 400 for a bad style, 409 with `error` if the printer is not ready |

Do not run `pt-print` while `pt-server` is printing.

### Printing through CUPS (browsers, other apps)

A CUPS queue named `PT-P750W` uses the PT-P710BT driver from
`printer-driver-ptouch`. It shows up in browser print dialogs. Pages are
portrait: width is the tape width, length runs down the page. For a
24 x 60 mm label, make a 24 x 60 mm page and rotate the content 90 degrees:

```sh
lp -d PT-P750W -o PageSize=Custom.68x170 label.pdf   # sizes in points
```

Verified: a 24 x 60 mm test PDF printed correctly this way.

A web page can print the same way: lay out the label at a fixed size, set
`@page { size: 24mm 60mm; margin: 0; }`, call `window.print()` and pick
`PT-P750W` in the dialog.

Do not run a CUPS job and `pt-print` at the same time.

## Files

| Path | Purpose |
|---|---|
| `src/pt_p750w/status.py` | `pt-status`: reads and decodes live printer status over USB |
| `src/pt_p750w/printing.py` | `pt-print`: renders text and prints it as a label |
| `src/pt_p750w/server.py` | `pt-server`: HTTP API and host for the web UI |
| `web/` | React + TypeScript + Vite + Tailwind + shadcn/ui web UI, Vitest tests |
| `tests/` | Hermetic tests for the print job, USB I/O (fake printer), status decoding, API and CLI |
| `udev/99-pt-p750w.rules` | Lets the `plugdev` group use the printer |
| `justfile` | `status`, `print`, `test`, `coverage`, `serve`, `web-*`, `install` and `install-udev` recipes |
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
cuts and reads correctly, both with `pt-print` and through the CUPS queue.
