import sys

import pytest
from PIL import Image

from conftest import GOOD_STATUS, FakePrinter
from pt_p750w import printing

from pt_p750w.printing import (
    HEAD_PINS,
    MIN_LENGTH_DOTS,
    PRINT_AREA,
    Style,
    apply_case,
    build_job,
    check_ready,
    fitted_size_pt,
    make_job,
    raster_lines,
    render,
)
from pt_p750w.status import PrinterError


def test_print_area_is_centred_on_head():
    for left, area in PRINT_AREA.values():
        assert 2 * left + area == HEAD_PINS


def test_print_information_matches_spec_example():
    # Spec section 2.1: 100 mm on 24 mm tape at 180 dpi = 682 raster lines.
    job = build_job([bytes(16)] * 682, tape_mm=24, cut=True)
    assert bytes.fromhex("1b 69 7a 84 00 18 00 aa 02 00 00 00 00") in job


def test_job_layout():
    job = build_job([bytes(16)] * 2, tape_mm=24, cut=True)
    assert job.startswith(bytes(100) + b"\x1b@\x1bia\x01")
    for command in (b"\x1biM\x40", b"\x1biA\x01", b"\x1biK\x08", b"\x1bid\x0e\x00", b"M\x00"):
        assert command in job
    assert job.count(b"G\x10\x00") == 2
    assert job.endswith(b"\x1a")


def test_no_cut_clears_auto_cut_bit():
    assert b"\x1biM\x00" in build_job([bytes(16)], tape_mm=24, cut=False)


def one_pixel(row: int, height: int) -> Image.Image:
    image = Image.new("1", (1, height), 1)
    image.putpixel((0, row), 0)
    return image


def test_pin_zero_is_msb_of_first_byte():
    assert raster_lines(one_pixel(0, 128), left_pins=0, flip=False) == [b"\x80" + bytes(15)]


def test_left_margin_offsets_pins():
    # 12 mm tape: 29 margin pins, so row 0 lands on pin 29 = byte 3, bit 0x04.
    line = raster_lines(one_pixel(0, 70), left_pins=29, flip=False)[0]
    assert line[3] == 0x04 and sum(line) == 0x04


def test_flip_reverses_rows():
    line = raster_lines(one_pixel(0, 128), left_pins=0, flip=True)[0]
    assert line == bytes(15) + b"\x01"


@pytest.mark.parametrize("tape_mm", sorted(PRINT_AREA))
def test_render_fits_print_area(tape_mm):
    _, pins = PRINT_AREA[tape_mm]
    image = render("Hello", pins)
    assert image.height == pins
    assert image.width >= MIN_LENGTH_DOTS
    assert image.getextrema()[0] == 0


def test_short_text_meets_minimum_length():
    assert render("i", 24).width >= MIN_LENGTH_DOTS


def test_missing_font_falls_back_to_bundled_font():
    assert render("Hi", 128, font_path="/nonexistent.ttf").getextrema()[0] == 0


def test_check_ready_accepts_good_status():
    assert check_ready(GOOD_STATUS) == 24


def test_check_ready_rejects_error():
    replace_media = bytearray(GOOD_STATUS)
    replace_media[9] = 0x01
    with pytest.raises(PrinterError, match="replace media"):
        check_ready(bytes(replace_media))


def test_check_ready_rejects_unknown_tape_width():
    odd_tape = bytearray(GOOD_STATUS)
    odd_tape[10] = 7
    with pytest.raises(PrinterError, match="Unsupported tape width: 7 mm"):
        check_ready(bytes(odd_tape))


def test_check_ready_rejects_garbage():
    with pytest.raises(PrinterError, match="Bad status"):
        check_ready(b"")


def test_multiline_text_fits_print_area():
    one = render("Hello", 128)
    two = render("Hello\nWorld", 128)
    assert two.height == 128 and two.getextrema()[0] == 0
    # Two lines share the height, so each line is smaller and the label shorter.
    assert two.width < one.width


def test_send_waits_for_printing_completed(fake_printer, capsys):
    _, job = make_job("Hi", 24)
    phase = FakePrinter.reply(kind=0x06)
    fake_printer.stale = [phase]  # left over from an earlier job; drain() drops it
    fake_printer.script = [(job, [phase, phase, FakePrinter.reply(kind=0x01)])]
    printing.send("/dev/usb/lp0", job)
    assert fake_printer.received == job
    assert capsys.readouterr().out.splitlines() == ["printer: phase change", "printer: phase change", "printed"]


def test_send_raises_on_error_reply(fake_printer):
    _, job = make_job("Hi", 24)
    fake_printer.script = [(job, [FakePrinter.reply(kind=0x02, errors_2=0x10)])]
    with pytest.raises(PrinterError, match="Print failed: cover open"):
        printing.send("/dev/usb/lp0", job)


def test_send_raises_when_printer_goes_quiet(fake_printer):
    _, job = make_job("Hi", 24)
    fake_printer.script = [(job, [])]
    with pytest.raises(PrinterError, match="No completion reply"):
        printing.send("/dev/usb/lp0", job, timeout=0.02)


def test_send_gives_up_after_too_many_replies(fake_printer):
    _, job = make_job("Hi", 24)
    fake_printer.script = [(job, [FakePrinter.reply(kind=0x06)] * 6)]
    with pytest.raises(PrinterError, match="Gave up"):
        printing.send("/dev/usb/lp0", job)


@pytest.fixture
def stub_device(monkeypatch):
    """Printer present with GOOD_STATUS; records the jobs passed to send()."""
    sent = []
    monkeypatch.setattr(printing, "find_device", lambda: "/dev/usb/lp0")
    monkeypatch.setattr(printing, "read_status", lambda node: GOOD_STATUS)
    monkeypatch.setattr(printing, "send", lambda node, job: sent.append(job))
    return sent


def test_print_text_uses_loaded_tape_width(stub_device):
    lines = printing.print_text("Hello", cut=False)
    assert lines > MIN_LENGTH_DOTS
    assert b"\x1biM\x00" in stub_device[0]  # no cut
    assert b"\x1biz\x84\x00\x18\x00" in stub_device[0]  # 24 mm tape


def test_main_dry_run_writes_preview_without_printer(tmp_path, monkeypatch, capsys):
    prefix = tmp_path / "label"
    monkeypatch.setattr(sys, "argv", ["pt-print", "Hello", "world", "--tape", "12", "--dry-run", str(prefix)])
    monkeypatch.setattr(printing, "find_device", lambda: pytest.fail("dry run touched the printer"))
    printing.main()
    assert Image.open(f"{prefix}.png").height == PRINT_AREA[12][1]
    assert open(f"{prefix}.bin", "rb").read().endswith(b"\x1a")
    assert "tape 12 mm" in capsys.readouterr().out


def test_main_prints_on_printer(stub_device, monkeypatch):
    monkeypatch.setattr(sys, "argv", ["pt-print", "Hi", "--no-cut"])
    printing.main()
    assert len(stub_device) == 1 and b"\x1biM\x00" in stub_device[0]


def test_main_refuses_when_printer_reports_error(stub_device, monkeypatch):
    monkeypatch.setattr(printing, "read_status", lambda node: FakePrinter.reply(errors_2=0x10))
    monkeypatch.setattr(sys, "argv", ["pt-print", "Hi"])
    with pytest.raises(SystemExit, match="cover open"):
        printing.main()
    assert stub_device == []


def test_main_exits_with_message_when_print_fails(stub_device, monkeypatch):
    def jam(node, job):
        raise PrinterError("Print failed: cutter jam")

    monkeypatch.setattr(printing, "send", jam)
    monkeypatch.setattr(sys, "argv", ["pt-print", "Hi"])
    with pytest.raises(SystemExit, match="cutter jam"):
        printing.main()


def ink_rows(image):
    """Indices of rows that contain at least one black pixel."""
    return [y for y in range(image.height) if any(image.getpixel((x, y)) == 0 for x in range(image.width))]


def test_default_style_is_bold_sans_centred():
    assert Style().font_file().endswith("/DejaVuSans-Bold.ttf")
    assert Style(font="serif", bold=False, italic=True).font_file().endswith("/DejaVuSerif-Italic.ttf")


@pytest.mark.parametrize("data, message", [
    ("x", "JSON object"),
    ({"shadow": True}, "unknown style option: shadow"),
    ({"italic": 1}, "italic must be true or false"),
    ({"font": "comic"}, "font must be one of"),
    ({"align": "justify"}, "align must be one of"),
    ({"case": "toggle"}, "case must be one of"),
    ({"line_spacing": 3}, "line_spacing must be one of"),
    ({"direction": "diagonal"}, "direction must be one of"),
    ({"size_pt": 2}, "size_pt must be"),
    ({"size_pt": 12.5}, "size_pt must be"),
    ({"size_pt": True}, "size_pt must be"),
])
def test_style_rejects_bad_input(data, message):
    with pytest.raises(ValueError, match=message):
        Style.from_dict(data)


def test_style_accepts_full_input():
    data = {"font": "mono", "bold": False, "italic": True, "underline": True, "strike": True, "outline": True,
            "invert": True, "border": True, "align": "right", "case": "upper", "size_pt": 12, "line_spacing": 1.5,
            "direction": "vertical"}
    assert Style.from_dict(data) == Style(**data)
    assert Style.from_dict(None) == Style()


@pytest.mark.parametrize("case, expected", [
    ("none", "don't stop\nme"), ("upper", "DON'T STOP\nME"), ("lower", "don't stop\nme"),
    ("title", "Don't Stop\nMe"),
])
def test_apply_case(case, expected):
    assert apply_case("don't stop\nme", case) == expected


@pytest.mark.parametrize("style", [
    Style(underline=True), Style(strike=True), Style(outline=True), Style(border=True), Style(invert=True),
    Style(italic=True, font="serif"), Style(line_spacing=2.0), Style(align="left"), Style(align="right"),
    Style(direction="vertical"), Style(direction="vertical", border=True, underline=True),
])
@pytest.mark.parametrize("tape_mm", [4, 24])
def test_every_style_fits_print_area(style, tape_mm):
    _, pins = PRINT_AREA[tape_mm]
    image = render("Hello\nWorld", pins, style)
    assert image.height == pins and image.width >= MIN_LENGTH_DOTS
    assert image.getextrema() == (0, 1)


def test_styles_change_the_image():
    plain = render("Hello", 128).tobytes()
    for style in (Style(underline=True), Style(strike=True), Style(outline=True), Style(border=True),
                  Style(bold=False), Style(font="mono")):
        assert render("Hello", 128, style).tobytes() != plain, style


def test_invert_swaps_background():
    assert render("Hi", 128).getpixel((0, 0)) == 1
    assert render("Hi", 128, Style(invert=True)).getpixel((0, 0)) == 0


def test_border_draws_frame_inside_edge():
    image = render("Hi", 128, Style(border=True))
    frame = 128 // 24
    assert image.getpixel((0, 0)) == 1
    assert image.getpixel((frame, 64)) == 0 and image.getpixel((image.width - 1 - frame, 64)) == 0


def test_size_pt_caps_text_height():
    small = render("Hello", 128, Style(size_pt=8))
    rows = ink_rows(small)
    assert rows[-1] - rows[0] < 8 * 2.5
    # A size larger than the tape still shrinks to fit.
    assert render("Hello", 128, Style(size_pt=72)).tobytes() == render("Hello", 128).tobytes()


def test_alignment_moves_short_line():
    def first_ink_column(image, rows):
        return min(x for x in range(image.width) for y in rows if image.getpixel((x, y)) == 0)

    bottom_half = range(70, 128)
    left = first_ink_column(render("Wide line\nab", 128, Style(align="left")), bottom_half)
    centre = first_ink_column(render("Wide line\nab", 128), bottom_half)
    right = first_ink_column(render("Wide line\nab", 128, Style(align="right")), bottom_half)
    assert left < centre < right


def test_blank_line_keeps_its_space():
    # Three lines must share the height, so the text is smaller than with two.
    assert render("A\n\nB", 128).width < render("A\nB", 128).width


def test_main_applies_style_json(tmp_path, monkeypatch):
    prefix = tmp_path / "label"
    style = '{"invert": true}'
    monkeypatch.setattr(sys, "argv", ["pt-print", "Hi", "--tape", "12", "--style", style, "--dry-run", str(prefix)])
    printing.main()
    assert Image.open(f"{prefix}.png").getpixel((0, 0)) == 0


def test_main_rejects_bad_style_json(monkeypatch):
    monkeypatch.setattr(sys, "argv", ["pt-print", "Hi", "--style", '{"align": 1}'])
    with pytest.raises(SystemExit, match="Bad --style: align must be one of"):
        printing.main()


def test_fitted_size_matches_tape_and_style():
    # Narrower tape and a smaller maximum both give a smaller font. Size is in points (2.5 dots each).
    wide, narrow = fitted_size_pt("Hello", 24), fitted_size_pt("Hello", 12)
    assert narrow < wide <= 128 / 2.5
    assert fitted_size_pt("Hello", 24, Style(size_pt=10)) == 10


def test_vertical_text_runs_across_the_tape():
    # Long text: horizontal gets a long label, vertical shrinks the font to fit
    # the line across the tape, so the label stays short.
    text = "PATCH PANEL 12"
    horizontal, vertical = render(text, 128), render(text, 128, Style(direction="vertical"))
    assert vertical.height == 128
    assert vertical.width < horizontal.width
    assert fitted_size_pt(text, 24, Style(direction="vertical")) < fitted_size_pt(text, 24)


def test_vertical_lines_add_label_length():
    style = Style(direction="vertical")
    assert render("A\nB\nC", 128, style).width > render("A", 128, style).width


def test_vertical_text_reads_top_to_bottom():
    # Turned clockwise: the line's first letter is at the top of the tape.
    image = render("L      .", 128, Style(direction="vertical", align="left"))
    top, bottom = ink_rows(image)[0], ink_rows(image)[-1]
    def ink_in(rows):
        return sum(image.getpixel((x, y)) == 0 for x in range(image.width) for y in rows)
    assert ink_in(range(top, top + 20)) > ink_in(range(bottom - 20, bottom + 1))
