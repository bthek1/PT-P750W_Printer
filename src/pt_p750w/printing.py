"""Print a text label on a Brother PT-P750W over /dev/usb/lpN.

Protocol: Brother "Raster Command Reference" for PT-E550W/P750W/P710BT,
sections 2.1 (job layout), 2.3.5 (pin table) and 4 (command details).
"""
import argparse
import json
import os
import re
import sys
from dataclasses import dataclass, fields
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont

from pt_p750w.status import (
    ERRORS_1,
    ERRORS_2,
    INITIALIZE,
    INVALIDATE,
    STATUS_TYPES,
    PrinterError,
    bits,
    decode,
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
FONT_DIR = "/usr/share/fonts/truetype/dejavu"
# Family -> font files for (regular, bold, italic, bold italic).
FONTS = {
    "sans": ("DejaVuSans", "DejaVuSans-Bold", "DejaVuSans-Oblique", "DejaVuSans-BoldOblique"),
    "condensed": ("DejaVuSansCondensed", "DejaVuSansCondensed-Bold",
                  "DejaVuSansCondensed-Oblique", "DejaVuSansCondensed-BoldOblique"),
    "serif": ("DejaVuSerif", "DejaVuSerif-Bold", "DejaVuSerif-Italic", "DejaVuSerif-BoldItalic"),
    "mono": ("DejaVuSansMono", "DejaVuSansMono-Bold", "DejaVuSansMono-Oblique", "DejaVuSansMono-BoldOblique"),
}
ALIGNS = ("left", "center", "right")
CASES = ("none", "upper", "lower", "title")
LINE_SPACINGS = (1.0, 1.5, 2.0)
SIZE_PT_RANGE = (4, 72)
DOTS_PER_PT = 180 / 72

PI_WIDTH, PI_RECOVER = 0x04, 0x80
AUTO_CUT = 0x40
NO_CHAIN_PRINTING = 0x08


@dataclass(frozen=True)
class Style:
    """Whole-label formatting. The defaults reproduce the original look."""

    font: str = "sans"
    bold: bool = True
    italic: bool = False
    underline: bool = False
    strike: bool = False
    outline: bool = False
    invert: bool = False
    border: bool = False
    align: str = "center"
    case: str = "none"
    size_pt: int | None = None  # None fits the tape; a number is a maximum
    line_spacing: float = 1.0

    @classmethod
    def from_dict(cls, data: object) -> "Style":
        """Validate untrusted input (API body or CLI JSON). Raises ValueError."""
        if data is None:
            return cls()
        if not isinstance(data, dict):
            raise ValueError("style must be a JSON object")
        known = {f.name for f in fields(cls)}
        if unknown := set(data) - known:
            raise ValueError(f"unknown style option: {', '.join(sorted(unknown))}")
        for name in ("bold", "italic", "underline", "strike", "outline", "invert", "border"):
            if not isinstance(data.get(name, False), bool):
                raise ValueError(f"{name} must be true or false")
        choices = {"font": tuple(FONTS), "align": ALIGNS, "case": CASES, "line_spacing": LINE_SPACINGS}
        for name, allowed in choices.items():
            if name in data and data[name] not in allowed:
                raise ValueError(f"{name} must be one of {', '.join(map(str, allowed))}")
        size = data.get("size_pt")
        low, high = SIZE_PT_RANGE
        if size is not None and (type(size) is not int or not low <= size <= high):
            raise ValueError(f"size_pt must be null or a whole number from {low} to {high}")
        return cls(**data)

    def font_file(self) -> str:
        name = FONTS[self.font][self.bold + 2 * self.italic]
        return f"{FONT_DIR}/{name}.ttf"


def load_font(font_path: str, size: int) -> ImageFont.FreeTypeFont:
    if Path(font_path).is_file():
        return ImageFont.truetype(font_path, size)
    # Pillow's bundled font, so the package still works without DejaVu installed.
    return ImageFont.load_default(size)


def apply_case(text: str, case: str) -> str:
    if case == "upper":
        return text.upper()
    if case == "lower":
        return text.lower()
    if case == "title":
        # str.title() would turn "don't" into "Don'T".
        return re.sub(r"(^|\s)(\S)", lambda m: m[1] + m[2].upper(), text)
    return text


def layout(lines: list[str], font: ImageFont.FreeTypeFont, size: int, style: Style) -> list[tuple]:
    """Ink box (left, top, right, bottom) of each line, baselines at 0, pitch, 2*pitch."""
    draw = ImageDraw.Draw(Image.new("1", (1, 1)))
    pitch = round(sum(font.getmetrics()) * style.line_spacing)
    stroke = outline_width(size) if style.outline else 0
    boxes = []
    for i, line in enumerate(lines):
        y = i * pitch
        left, top, right, bottom = draw.textbbox((0, y), line, font=font, anchor="ls", stroke_width=stroke)
        if not line:
            left = right = 0
        if style.underline and line:
            bottom = max(bottom, y + underline_offset(size) + rule_width(size))
        boxes.append((left, top, right, bottom))
    return boxes


def outline_width(size: int) -> int:
    return max(1, size // 20)


def rule_width(size: int) -> int:
    return max(1, size // 14)


def underline_offset(size: int) -> int:
    return max(1, size // 10)


def render(text: str, pins: int, style: Style = Style(), font_path: str | None = None) -> Image.Image:
    """Render text as a 1-bit image: height = print area pins, width = label length.

    Newlines give several lines across the tape. Text shrinks until it fits.
    """
    lines = apply_case(text, style.case).split("\n")
    frame = max(2, pins // 24) if style.border else 0
    target = int((pins - 4 * frame) * 0.85)
    size = pins if style.size_pt is None else min(pins, round(style.size_pt * DOTS_PER_PT))
    while True:
        font = load_font(font_path or style.font_file(), size)
        boxes = layout(lines, font, size, style)
        top = min(b[1] for b in boxes)
        bottom = max(b[3] for b in boxes)
        if bottom - top <= target or size <= 4:
            break
        size -= 1

    block_left = min(b[0] for b in boxes)
    block_width = max(b[2] for b in boxes) - block_left
    pad = pins // 8 + 2 * frame
    width = max(block_width + 2 * pad, MIN_LENGTH_DOTS)
    ink, paper = (1, 0) if style.invert else (0, 1)
    image = Image.new("1", (width, pins), paper)
    draw = ImageDraw.Draw(image)
    y0 = (pins - (bottom - top)) // 2 - top
    pitch = round(sum(font.getmetrics()) * style.line_spacing)
    rule = rule_width(size)
    for i, (line, (left, _, right, _)) in enumerate(zip(lines, boxes)):
        spare = block_width - (right - left)
        shift = {"left": 0, "center": spare // 2, "right": spare}[style.align]
        x = pad + shift - left
        baseline = y0 + i * pitch
        if style.outline:
            draw.text((x, baseline), line, font=font, anchor="ls", fill=paper,
                      stroke_width=outline_width(size), stroke_fill=ink)
        else:
            draw.text((x, baseline), line, font=font, anchor="ls", fill=ink)
        if not line:
            continue
        ink_left, ink_right = x + left, x + right
        if style.underline:
            y = baseline + underline_offset(size)
            draw.rectangle((ink_left, y, ink_right, y + rule - 1), fill=ink)
        if style.strike:
            y = baseline - round(size * 0.3)
            draw.rectangle((ink_left, y, ink_right, y + rule - 1), fill=ink)
    if frame:
        draw.rectangle((frame, frame, width - 1 - frame, pins - 1 - frame), outline=ink, width=frame)
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
    info = decode(status)
    if info["errors"]:
        raise PrinterError(f"Printer not ready: {', '.join(info['errors'])}")
    if info["tape_mm"] not in PRINT_AREA:
        raise PrinterError(f"Unsupported tape width: {info['tape_mm']} mm")
    return info["tape_mm"]


def send(node: str, job: bytes, timeout: float = 15) -> None:
    fd = open_printer(node)
    try:
        drain(fd)
        os.write(fd, job)
        # The printer sends unsolicited status replies (phase change, then
        # printing completed or error).
        for _ in range(6):
            reply = read_reply(fd, timeout)
            if len(reply) != 32:
                raise PrinterError(f"No completion reply from printer (got {reply.hex(' ') or 'nothing'})")
            kind = reply[18]
            if kind == 0x01:
                print("printed")
                return
            if kind == 0x02:
                errors = bits(reply[8], ERRORS_1) + bits(reply[9], ERRORS_2)
                raise PrinterError(f"Print failed: {', '.join(errors) or 'unknown error'}")
            print(f"printer: {named(STATUS_TYPES, kind)}")
        raise PrinterError("Gave up waiting for printing completed")
    finally:
        os.close(fd)


def make_job(text: str, tape_mm: int, cut: bool = True, flip: bool = False,
             font_path: str | None = None, style: Style = Style()) -> tuple[Image.Image, bytes]:
    left_pins, pins = PRINT_AREA[tape_mm]
    image = render(text, pins, style, font_path)
    return image, build_job(raster_lines(image, left_pins, flip), tape_mm, cut)


def print_text(text: str, cut: bool = True, style: Style = Style()) -> int:
    """Check the printer, then print one label. Returns the raster line count."""
    node = find_device()
    image, job = make_job(text, check_ready(read_status(node)), cut, style=style)
    send(node, job)
    return image.width


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("text", nargs="+", help="label text; several words are joined with spaces")
    parser.add_argument("--font", help="TTF file; overrides the style's font, bold and italic")
    parser.add_argument("--style", default="{}", help='JSON, e.g. \'{"align": "left", "underline": true}\'')
    parser.add_argument("--tape", type=int, help="tape width in mm; skips the printer query (dry run only)")
    parser.add_argument("--no-cut", action="store_true")
    parser.add_argument("--flip", action="store_true", help="reverse pin order if text prints mirrored")
    parser.add_argument("--dry-run", metavar="PREFIX", help="write PREFIX.png and PREFIX.bin instead of printing")
    args = parser.parse_args()

    try:
        style = Style.from_dict(json.loads(args.style))
    except ValueError as e:
        sys.exit(f"Bad --style: {e}")

    try:
        if args.dry_run and args.tape:
            node, tape_mm = None, args.tape
        else:
            node = find_device()
            tape_mm = check_ready(read_status(node))
    except PrinterError as e:
        sys.exit(str(e))

    image, job = make_job(" ".join(args.text), tape_mm, not args.no_cut, args.flip, args.font, style)
    length_mm = image.width / 180 * 25.4
    print(f"tape {tape_mm} mm, {image.width} raster lines ({length_mm:.1f} mm + margins), job {len(job)} bytes")

    if args.dry_run:
        image.save(Path(f"{args.dry_run}.png"))
        Path(f"{args.dry_run}.bin").write_bytes(job)
        print(f"wrote {args.dry_run}.png and {args.dry_run}.bin")
        return
    try:
        send(node, job)
    except PrinterError as e:
        sys.exit(str(e))


if __name__ == "__main__":
    main()
