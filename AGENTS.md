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

- Python env: `uv` package, src layout (`src/pt_p750w/`), hatchling build.
  Console scripts `pt-print`, `pt-status` and `pt-server` (see
  `[project.scripts]`). Runtime dependency: Pillow. Dev dependency: pytest.
- Library functions raise `PrinterError` (in `status.py`). Only the CLI
  `main()` functions turn it into `sys.exit`, so the server can reuse them.
- Tests: `just test`. Hermetic (no printer): 129 tests in about 0.6 s. They
  check the job against the spec's example bytes and the known-good status
  reply. `tests/conftest.py` has `fake_printer`: a socketpair that plays the
  printer, so the real `read_reply`/`drain`/`send` code runs without USB. Its
  script matches on the full request, not a suffix like `1A`, because raster
  data can contain any byte. `find_device` is tested against a fake sysfs
  tree via `status.DEV_GLOB` and `status.USBMISC`.
- Coverage: `just coverage` runs pytest-cov and Vitest v8 coverage. Both fail
  under 90%. As of 2026-11-11: Python 100% lines and branches (129 tests),
  web 100% statements, lines and branches (51 tests). pytest-cov needs
  `-p pytest_cov` because plugin autoload is off.
  `pyproject.toml` sets `addopts = ["--disable-plugin-autoload"]` (pytest 8.4+):
  the user's ROS 2 `PYTHONPATH` injects `launch_testing` plugins that fail to
  import. Keep it there, not in an env var, so VS Code test discovery works too.
- To test the installed package: `uv build --wheel -o <dir>`, then
  `env -u PYTHONPATH uv run --isolated --no-project --with <wheel> pt-status`.
- Spec: Brother "Raster Command Reference" for PT-E550W/P750W/P710BT,
  `https://download.brother.com/welcome/docp100064/cv_pte550wp750wp710bt_eng_raster_102.pdf`.
  Not stored in the repo. Extract with `pdftotext -layout`. Use it, not memory.
- `src/pt_p750w/printing.py` (`pt-print`): renders text with Pillow to a 1-bit image,
  one raster line per column, and sends: invalidate, `ESC @`, `ESC i a 01`,
  `ESC i z`, `ESC i M 40` (auto cut), `ESC i A 01`, `ESC i K 08` (no chain),
  `ESC i d 0E 00` (2 mm feed), `M 00` (no compression), `G 10 00` + 16 bytes per
  line, `1A`. Checks status first and refuses to print on any error. Then waits
  for the unsolicited "printing completed" reply. `--dry-run PREFIX` writes a
  PNG preview and the raw job. `--flip` reverses pin order. Not needed: the
  default (image row 0 at pin 0) prints readable text, confirmed by eye.
  Several words are joined with spaces. If the DejaVu font is missing it falls
  back to Pillow's bundled font.
- `src/pt_p750w/status.py` (`pt-status`): stdlib-only. Finds `/dev/usb/lpN` for `04f9:2062` through
  sysfs, sends invalidate + `ESC @` + `ESC i S`, decodes the 32-byte status
  reply. Run with `just status`.
- `src/pt_p750w/server.py` (`pt-server`): stdlib `ThreadingHTTPServer` on
  `127.0.0.1:8750`. `/api/status`, `/api/preview`, `/api/print`, and static
  files from `web/dist`. One lock guards the USB device. A status request
  during a print returns the cached state with `busy: true`, because reading
  mid-job would steal the "printing completed" reply. Each status poll takes
  about 0.35 s (mostly the 0.2 s drain).
- `web/`: React 19 + TypeScript + Vite + Tailwind v4 + shadcn/ui (radix
  base, Nova preset, Lucide icons, Geist font), Vitest + Testing Library.
  Add components with `npx shadcn@latest add <name>` in `web/`; they land in
  `src/components/ui/` and are generated code, so leave their style alone.
  `cn()` comes from the `cn` package (shadcn's replacement for clsx +
  tailwind-merge). Imports use the `@/` alias for `src/`. Dark mode follows the
  OS: `main.tsx` toggles the `.dark` class that shadcn's variant expects. `usePrinterStatus` polls every 2 s, pauses while the tab is hidden,
  and keeps retrying when the server is down. Vite proxies `/api` to
  `pt-server` in dev. Tests mock `fetch`: `just web-test`. Vitest runs
  without `globals`, so Testing Library does not unmount between tests: each
  test file must call `cleanup()` in `afterEach`, or old components keep
  polling the next test's mock. In VS Code the web tests need the Vitest
  extension (`vitest.explorer`, recommended in `.vscode/extensions.json`). It
  finds `web/vite.config.ts` on its own. Build: `just
  web-build`.
- `render()` accepts newlines (several lines). The CLI joins its
  arguments with spaces, so only the API produces multi-line labels.
- Formatting is one `printing.Style` per label (font family, bold, italic,
  underline, strike, outline, invert, border, align, case, max `size_pt`,
  line spacing). Defaults reproduce the original look: DejaVu Sans Bold,
  centred, fit to tape. `Style.from_dict` validates API and `--style` JSON
  and raises `ValueError` (400 in the server). The UI mirrors it as
  `LabelStyle` in `web/src/api.ts` and the toolbar in `FormatToolbar.tsx`;
  change both sides together. Per-character formatting (subscript, colour,
  highlight, small caps) is out of scope: one-colour printer, one image per
  label. Ctrl+L/E/R are not bound because browsers keep them.
- `udev/99-pt-p750w.rules`: gives group `plugdev` access to the printer (USB and
  usblp nodes). The user is in `plugdev`, not `lp`. Install with
  `just install-udev` (needs the user's sudo password; agents cannot run it).
- `just install-udev` must trigger both the USB device (`--attr-match=idVendor=04f9`)
  and the `usbmisc` subsystem. The `lp` node has no `idVendor` attribute, so
  the first trigger alone leaves it as `root:lp`. Debug with
  `udevadm test /sys/class/usbmisc/lpN 2>&1 | grep 99-pt`.
- CUPS is active on the dev VM and may briefly re-bind usblp on hotplug.
- A CUPS queue `PT-P750W` exists on the dev VM. Nobody added it by hand:
  `system-config-printer-udev` (`udev-configure-printer`) created it when the
  printer entered printer mode, using the closest installed driver, PT-P710BT
  from `printer-driver-ptouch` (Foomatic/ptouch-pt, default page `tz-24`).
  Browsers list it in their print dialogs. CUPS and `pt-print` share the USB
  device, so do not use both at once.
- Driver page geometry: portrait, width = tape width, label length runs down
  the page. `tz-24` is 68 x 284 pt (24 x 100 mm). Custom sizes are
  `-o PageSize=Custom.<W>x<H>` in points, min 36 pt each.
- 2026-11-10: a 24 x 60 mm PDF (68 x 170 pt, text rotated 90 degrees, 1 mm
  border) sent with `lp -d PT-P750W -o PageSize=Custom.68x170` completed in
  CUPS with no printer error and printed correctly, confirmed by eye. So
  browser printing (`@page { size: 24mm 60mm }` + `window.print()`, pick the
  `PT-P750W` queue) is a valid route for web apps, with no backend.

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

All codes in `status.py` are checked against the spec (section 4,
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
`just print "TEST"` printed on hardware (2026-11-10): 386 raster lines,
printer replied phase change, phase change, printing completed. The label
reads correctly, not mirrored, with the default pin order.

2026-11-10: `POST /api/print` with "UI TEST\nline 2" through `pt-server`
returned after "printing completed" (281 raster lines). Not yet checked by
eye. The browser UI was built and served, but not clicked through in a
browser by an agent.

The printer queues unsolicited replies (phase change etc.). Both scripts call
`drain()` before sending, or the first read returns a stale message. An earlier
"replace media" error was probably such a stale reply or a transient state; it
cleared by itself.
