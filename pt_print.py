#!/usr/bin/env python3
"""Print a text label on a Brother PT-P750W over /dev/usb/lpN.

Protocol: Brother "Raster Command Reference" for PT-E550W/P750W/P710BT,
sections 2.1 (job layout), 2.3.5 (pin table) and 4 (command details).
"""
import argparse
import os
import sys
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont

from pt_status import (
    ERRORS_1,
    ERRORS_2,
    INITIALIZE,
    INVALIDATE,
    STATUS_TYPES,
    bits,
    drain,
    find_device,
    named,
    open_printer,
    read_reply,
    read_status,
)

HEAD_PINS = 128
LINE_BYTES = HEAD_PINS // 8
# Tape width (mm, as the status reply reports it) -> (left margin pins, print area pins).
# 3.5 mm tape reports as 4.
PRINT_AREA = {4: (52, 24), 6: (48, 32), 9: (39, 50), 12: (29, 70), 18: (8, 112), 24: (0, 128)}
MIN_LENGTH_DOTS = 31  # 4.4 mm at 180 dpi
FEED_MARGIN_DOTS = 14  # 2 mm, the spec's example value
DEFAULT_FONT = "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf"

PI_WIDTH, PI_RECOVER = 0x04, 0x80
AUTO_CUT = 0x40
NO_CHAIN_PRINTING = 0x08


def render(text: str, pins: int, font_path: str) -> Image.Image:
    """Render text as a 1-bit image: height = print area pins, width = label length."""
    target = int(pins * 0.85)
    size = pins
    while size > 4:
        font = ImageFont.truetype(font_path, size)
        left, top, right, bottom = font.getbbox(text)
        if bottom - top <= target:
            break
        size -= 1
    pad = pins // 8
    width = max(right - left + 2 * pad, MIN_LENGTH_DOTS)
    image = Image.new("1", (width, pins), 1)
    y = (pins - (bottom - top)) // 2 - top
    ImageDraw.Draw(image).text((pad - left, y), text, font=font, fill=0)
    return image


def raster_lines(image: Image.Image, left_pins: int, flip: bool) -> list[bytes]:
    """One raster line per image column. Pin 0 is the MSB of the first byte."""
    width, height = image.size
    pixels = image.load()
    lines = []
    for x in range(width):
        line = bytearray(LINE_BYTES)
        for y in range(height):
            if pixels[x, y] == 0:
                pin = left_pins + (height - 1 - y if flip else y)
                line[pin // 8] |= 0x80 >> (pin % 8)
        lines.append(bytes(line))
    return lines


def build_job(lines: list[bytes], tape_mm: int, cut: bool) -> bytes:
    job = bytearray(INVALIDATE + INITIALIZE)
    job += b"\x1bia\x01"  # raster mode
    job += b"\x1biz" + bytes([PI_WIDTH | PI_RECOVER, 0x00, tape_mm, 0x00])
    job += len(lines).to_bytes(4, "little") + b"\x00\x00"
    job += b"\x1biM" + bytes([AUTO_CUT if cut else 0])
    job += b"\x1biA\x01"  # cut after every label
    job += b"\x1biK" + bytes([NO_CHAIN_PRINTING])  # feed and cut after the last label
    job += b"\x1bid" + FEED_MARGIN_DOTS.to_bytes(2, "little")
    job += b"M\x00"  # no compression
    for line in lines:
        job += b"G" + LINE_BYTES.to_bytes(2, "little") + line
    job += b"\x1a"  # print with feeding (last page)
    return bytes(job)


def check_ready(status: bytes) -> int:
    if len(status) != 32 or status[0] != 0x80:
        sys.exit(f"Bad status reply: {status.hex(' ') or 'nothing'}")
    errors = bits(status[8], ERRORS_1) + bits(status[9], ERRORS_2)
    if errors:
        sys.exit(f"Printer not ready: {', '.join(errors)}")
    tape_mm = status[10]
    if tape_mm not in PRINT_AREA:
        sys.exit(f"Unsupported tape width: {tape_mm} mm")
    return tape_mm


def send(node: str, job: bytes) -> None:
    fd = open_printer(node)
    try:
        drain(fd)
        os.write(fd, job)
        # The printer sends unsolicited status replies (phase change, then
        # printing completed or error).
        for _ in range(6):
            reply = read_reply(fd, timeout=15)
            if len(reply) != 32:
                sys.exit(f"No completion reply from printer (got {reply.hex(' ') or 'nothing'})")
            kind = reply[18]
            if kind == 0x01:
                print("printed")
                return
            if kind == 0x02:
                errors = bits(reply[8], ERRORS_1) + bits(reply[9], ERRORS_2)
                sys.exit(f"Print failed: {', '.join(errors) or 'unknown error'}")
            print(f"printer: {named(STATUS_TYPES, kind)}")
        sys.exit("Gave up waiting for printing completed")
    finally:
        os.close(fd)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("text")
    parser.add_argument("--font", default=DEFAULT_FONT)
    parser.add_argument("--tape", type=int, help="tape width in mm; skips the printer query (dry run only)")
    parser.add_argument("--no-cut", action="store_true")
    parser.add_argument("--flip", action="store_true", help="reverse pin order if text prints mirrored")
    parser.add_argument("--dry-run", metavar="PREFIX", help="write PREFIX.png and PREFIX.bin instead of printing")
    args = parser.parse_args()

    if args.dry_run and args.tape:
        node, tape_mm = None, args.tape
    else:
        node = find_device()
        tape_mm = check_ready(read_status(node))

    left_pins, pins = PRINT_AREA[tape_mm]
    image = render(args.text, pins, args.font)
    job = build_job(raster_lines(image, left_pins, args.flip), tape_mm, not args.no_cut)
    length_mm = image.width / 180 * 25.4
    print(f"tape {tape_mm} mm, {image.width} raster lines ({length_mm:.1f} mm + margins), job {len(job)} bytes")

    if args.dry_run:
        image.save(Path(f"{args.dry_run}.png"))
        Path(f"{args.dry_run}.bin").write_bytes(job)
        print(f"wrote {args.dry_run}.png and {args.dry_run}.bin")
        return
    send(node, job)


if __name__ == "__main__":
    main()
