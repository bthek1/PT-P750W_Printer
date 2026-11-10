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
from typing import NamedTuple

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
DIRECTIONS = ("horizontal", "vertical")
SIZE_PT_RANGE = (4, 72)
DOTS_PER_PT = 180 / 72

PI_WIDTH, PI_RECOVER = 0x04, 0x80
AUTO_CUT = 0x40
HALF_CUT = 0x04
NO_CHAIN_PRINTING = 0x08
MAX_PAGES = 99  # the limit of ESC i A, which counts the labels between full cuts
MAX_COPIES = MAX_PAGES
# Cut marks between labels. Vertical text: a line of dashes, 3 to 5 for a
# half cut, 6 or more for a full cut. The newlines of the dash line go with it;
# any other blank line is part of a label. Horizontal text: | (half) or ||
# (full) inline, with the spaces around it.
DASH_MARK = re.compile(r"\n?^[ \t]*(-{3,})[ \t]*$\n?", re.MULTILINE)
FULL_CUT_DASHES = 6
PIPE_MARK = re.compile(r"[ \t]*(\|+)[ \t]*")
CUT_GAP_DOTS = 2 * FEED_MARGIN_DOTS  # preview only: feed margin after one label and before the next


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
    direction: str = "horizontal"  # vertical turns the text 90 degrees, lines run across the tape

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
        choices = {"font": tuple(FONTS), "align": ALIGNS, "case": CASES, "line_spacing": LINE_SPACINGS,
                   "direction": DIRECTIONS}
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
            # A blank line has no ink; give it the font's full line height so
            # leading and trailing blank lines add the same length.
            ascent, descent = font.getmetrics()
            left, top, right, bottom = 0, y - ascent, 0, y + descent
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


def border_width(pins: int, style: Style) -> int:
    return max(2, pins // 24) if style.border else 0


class Label(NamedTuple):
    text: str
    full_cut: bool  # after this label; False means a half cut


def split_labels(text: str, direction: str = "horizontal") -> list[Label]:
    """Split text into labels at the cut marks for this direction.

    Blank lines in a label are kept. Blank labels are dropped; the stronger of
    the cuts around one wins. The last label always gets a full cut.
    """
    if direction == "vertical":
        pattern, full_length = DASH_MARK, FULL_CUT_DASHES
    else:
        pattern, full_length = PIPE_MARK, 2
    parts = pattern.split(text)  # label, mark, label, mark, ..., label
    labels: list[Label] = []
    for i in range(0, len(parts), 2):
        full = i + 1 == len(parts) or len(parts[i + 1]) >= full_length
        if parts[i].strip():
            labels.append(Label(parts[i], full))
        elif labels and full:
            labels[-1] = labels[-1]._replace(full_cut=True)
    if labels:
        labels[-1] = labels[-1]._replace(full_cut=True)
    return labels


def fit(lines: list[str], pins: int, style: Style, font_path: str | None = None, max_size: int | None = None):
    """Largest font size (in dots) up to the style's maximum that fits the print area.

    Horizontal text must fit the block height across the tape, vertical text
    the longest line. max_size caps it further. Returns (size, font, line boxes).
    """
    target = int((pins - 4 * border_width(pins, style)) * 0.85)
    size = pins if style.size_pt is None else min(pins, round(style.size_pt * DOTS_PER_PT))
    if max_size is not None:
        size = min(size, max_size)
    while True:
        font = load_font(font_path or style.font_file(), size)
        boxes = layout(lines, font, size, style)
        if style.direction == "vertical":
            across = max(b[2] for b in boxes) - min(b[0] for b in boxes)
        else:
            across = max(b[3] for b in boxes) - min(b[1] for b in boxes)
        if across <= target or size <= 4:
            return size, font, boxes
        size -= 1


def shared_size(labels: list[Label], pins: int, style: Style, font_path: str | None = None) -> int:
    """One font size (dots) for every label in a set: the size of the one that needs the smallest."""
    return min(fit(apply_case(label.text, style.case).split("\n"), pins, style, font_path)[0] for label in labels)


def fitted_size_pt(text: str, tape_mm: int, style: Style = Style()) -> float:
    """The font size, in points, that render_labels() uses for this text on this tape."""
    labels = split_labels(text, style.direction)
    return round(shared_size(labels, PRINT_AREA[tape_mm][1], style) / DOTS_PER_PT, 1)


def render_labels(labels: list[Label], pins: int, style: Style = Style(),
                  font_path: str | None = None) -> list[Image.Image]:
    """One image per label. All labels use the same font size, so a numbered set matches."""
    if not labels:
        raise ValueError("text is empty")
    size = shared_size(labels, pins, style, font_path)
    return [render(label.text, pins, style, font_path, size) for label in labels]


def preview_strip(labels: list[Label], images: list[Image.Image]) -> Image.Image:
    """The labels side by side, as greyscale. A dashed line marks a half cut, a solid line a full cut."""
    height = images[0].height
    width = sum(image.width for image in images) + CUT_GAP_DOTS * (len(images) - 1)
    strip = Image.new("L", (width, height), 255)
    draw = ImageDraw.Draw(strip)
    x = 0
    for i, image in enumerate(images):
        if i:
            cut = x + CUT_GAP_DOTS // 2
            if labels[i - 1].full_cut:
                draw.line((cut, 0, cut, height - 1), fill=64)
            else:
                for y in range(0, height, 8):
                    draw.line((cut, y, cut, min(y + 3, height - 1)), fill=128)
            x += CUT_GAP_DOTS
        strip.paste(image.convert("L"), (x, 0))
        x += image.width
    return strip


def label_preview(text: str, tape_mm: int, style: Style = Style()) -> Image.Image:
    labels = split_labels(text, style.direction)
    return preview_strip(labels, render_labels(labels, PRINT_AREA[tape_mm][1], style))


def render(text: str, pins: int, style: Style = Style(), font_path: str | None = None,
           max_size: int | None = None) -> Image.Image:
    """Render text as a 1-bit image: height = print area pins, width = label length.

    Newlines give several lines. Text shrinks until it fits. Vertical text is
    drawn on a page as wide as the tape, then turned counter-clockwise: the
    first line prints first, so with the label's start held at the top, the
    lines read top to bottom in the order typed.
    """
    lines = apply_case(text, style.case).split("\n")
    frame = border_width(pins, style)
    size, font, boxes = fit(lines, pins, style, font_path, max_size)
    top = min(b[1] for b in boxes)
    bottom = max(b[3] for b in boxes)

    block_left = min(b[0] for b in boxes)
    block_width = max(b[2] for b in boxes) - block_left
    pad = pins // 8 + 2 * frame
    vertical = style.direction == "vertical"
    if vertical:
        width, height = pins, max(bottom - top + 2 * pad, MIN_LENGTH_DOTS)
        x0 = (width - block_width) // 2
    else:
        width, height = max(block_width + 2 * pad, MIN_LENGTH_DOTS), pins
        x0 = pad
    ink, paper = (1, 0) if style.invert else (0, 1)
    image = Image.new("1", (width, height), paper)
    draw = ImageDraw.Draw(image)
    y0 = (height - (bottom - top)) // 2 - top
    pitch = round(sum(font.getmetrics()) * style.line_spacing)
    rule = rule_width(size)
    for i, (line, (left, _, right, _)) in enumerate(zip(lines, boxes)):
        spare = block_width - (right - left)
        shift = {"left": 0, "center": spare // 2, "right": spare}[style.align]
        x = x0 + shift - left
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
        draw.rectangle((frame, frame, width - 1 - frame, height - 1 - frame), outline=ink, width=frame)
    return image.transpose(Image.Transpose.ROTATE_90) if vertical else image


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


def check_job_options(copies: int, labels: int = 1) -> None:
    """Validate an untrusted copy count. Raises ValueError."""
    if type(copies) is not int or not 1 <= copies <= MAX_COPIES:
        raise ValueError(f"copies must be a whole number from 1 to {MAX_COPIES}")
    if labels * copies > MAX_PAGES:
        raise ValueError(f"{labels} labels x {copies} copies is more than {MAX_PAGES} labels per job")


def run_lengths(full_cuts: list[bool]) -> list[int]:
    """For each label, the size of its run: the labels joined by half cuts up to the next full cut."""
    lengths, run = [], 0
    for full in full_cuts:
        run += 1
        if full:
            lengths += [run] * run
            run = 0
    return lengths


def build_job(pages: list[list[bytes]], tape_mm: int, full_cuts: list[bool] | None = None, copies: int = 1) -> bytes:
    """One page per label (spec 2.1): control codes, raster data, then FF, or 1A after the last page.

    pages holds the raster lines of each label, full_cuts whether a full cut
    (True) or a half cut follows it; the last label always gets a full cut.
    The default is a full cut after every label. Copies repeat the whole set.
    Each page sets "cut each n labels" to the length of its run and turns half
    cut on inside runs. The spec sends these per page but does not say how the
    counter behaves when n changes within a job; not yet tested on hardware.
    """
    check_job_options(copies, len(pages))
    cuts = [True] * len(pages) if full_cuts is None else [*full_cuts[:-1], True]
    sequence = pages * copies
    runs = run_lengths(cuts * copies)
    job = bytearray(INVALIDATE + INITIALIZE)
    for page, (lines, run) in enumerate(zip(sequence, runs)):
        job += b"\x1bia\x01"  # raster mode
        job += b"\x1biz" + bytes([PI_WIDTH | PI_RECOVER, 0x00, tape_mm, 0x00])
        job += len(lines).to_bytes(4, "little") + bytes([page > 0, 0x00])  # 0 = starting page
        job += b"\x1biM" + bytes([AUTO_CUT])
        job += b"\x1biA" + bytes([run])  # full cut every n labels
        # No chain printing: feed and cut after the last label.
        job += b"\x1biK" + bytes([NO_CHAIN_PRINTING | (HALF_CUT if run > 1 else 0)])
        job += b"\x1bid" + FEED_MARGIN_DOTS.to_bytes(2, "little")
        job += b"M\x00"  # no compression
        for line in lines:
            job += b"G" + LINE_BYTES.to_bytes(2, "little") + line
        job += b"\x1a" if page == len(sequence) - 1 else b"\x0c"  # print with feeding / print
    return bytes(job)


def check_ready(status: bytes) -> int:
    info = decode(status)
    if info["errors"]:
        raise PrinterError(f"Printer not ready: {', '.join(info['errors'])}")
    if info["tape_mm"] not in PRINT_AREA:
        raise PrinterError(f"Unsupported tape width: {info['tape_mm']} mm")
    return info["tape_mm"]


def send(node: str, job: bytes, pages: int = 1, timeout: float = 15) -> None:
    fd = open_printer(node)
    try:
        drain(fd)
        os.write(fd, job)
        # The printer sends unsolicited status replies: phase changes, then
        # printing completed (one per page, spec 1.2 step 5) or error.
        done = 0
        for _ in range(6 * pages):
            reply = read_reply(fd, timeout)
            if len(reply) != 32:
                raise PrinterError(f"No completion reply from printer (got {reply.hex(' ') or 'nothing'})")
            kind = reply[18]
            if kind == 0x01:
                done += 1
                print("printed" if pages == 1 else f"printed {done}/{pages}")
                if done == pages:
                    return
                continue
            if kind == 0x02:
                errors = bits(reply[8], ERRORS_1) + bits(reply[9], ERRORS_2)
                raise PrinterError(f"Print failed: {', '.join(errors) or 'unknown error'}")
            print(f"printer: {named(STATUS_TYPES, kind)}")
        raise PrinterError("Gave up waiting for printing completed")
    finally:
        os.close(fd)


def make_job(text: str, tape_mm: int, flip: bool = False, font_path: str | None = None,
             style: Style = Style(), copies: int = 1) -> tuple[list[Image.Image], bytes]:
    """Images of the labels in text (split at the cut marks) and the print job for all of them."""
    left_pins, pins = PRINT_AREA[tape_mm]
    labels = split_labels(text, style.direction)
    images = render_labels(labels, pins, style, font_path)
    pages = [raster_lines(image, left_pins, flip) for image in images]
    return images, build_job(pages, tape_mm, [label.full_cut for label in labels], copies)


def print_text(text: str, style: Style = Style(), copies: int = 1) -> int:
    """Check the printer, then print the labels `copies` times. Returns the raster lines of one set."""
    check_job_options(copies, len(split_labels(text, style.direction)))
    node = find_device()
    images, job = make_job(text, check_ready(read_status(node)), style=style, copies=copies)
    send(node, job, len(images) * copies)
    return sum(image.width for image in images)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("text", nargs="+", help="label text; several words are joined with spaces. Cut marks "
                        "between labels: horizontal, | (half cut) or || (full cut); "
                        "vertical, a line of --- (half cut) or ------ (full cut)")
    parser.add_argument("--each", action="store_true", help="each argument is a label, joined by half cuts")
    parser.add_argument("--font", help="TTF file; overrides the style's font, bold and italic")
    parser.add_argument("--style", default="{}", help='JSON, e.g. \'{"align": "left", "underline": true}\'')
    parser.add_argument("--tape", type=int, help="tape width in mm; skips the printer query (dry run only)")
    parser.add_argument("--copies", type=int, default=1, help=f"1 to {MAX_COPIES}")
    parser.add_argument("--flip", action="store_true", help="reverse pin order if text prints mirrored")
    parser.add_argument("--dry-run", metavar="PREFIX", help="write PREFIX.png and PREFIX.bin instead of printing")
    args = parser.parse_args()

    try:
        style = Style.from_dict(json.loads(args.style))
    except ValueError as e:
        sys.exit(f"Bad --style: {e}")
    half_cut = "\n---\n" if style.direction == "vertical" else "|"
    text = half_cut.join(args.text) if args.each else " ".join(args.text)
    labels = split_labels(text, style.direction)
    try:
        if not labels:
            raise ValueError("text is empty")
        check_job_options(args.copies, len(labels))
    except ValueError as e:
        sys.exit(str(e))

    try:
        if args.dry_run and args.tape:
            node, tape_mm = None, args.tape
        else:
            node = find_device()
            tape_mm = check_ready(read_status(node))
    except PrinterError as e:
        sys.exit(str(e))

    images, job = make_job(text, tape_mm, args.flip, args.font, style, args.copies)
    width = sum(image.width for image in images)
    count = f"{len(images)} labels, " if len(images) > 1 else ""
    print(f"tape {tape_mm} mm, {count}{width} raster lines ({width / 180 * 25.4:.1f} mm + margins), "
          f"job {len(job)} bytes")

    if args.dry_run:
        preview_strip(labels, images).save(Path(f"{args.dry_run}.png"))
        Path(f"{args.dry_run}.bin").write_bytes(job)
        print(f"wrote {args.dry_run}.png and {args.dry_run}.bin")
        return
    try:
        send(node, job, len(images) * args.copies)
    except PrinterError as e:
        sys.exit(str(e))


if __name__ == "__main__":
    main()
