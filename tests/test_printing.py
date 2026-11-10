import sys

import pytest
from PIL import Image

from conftest import GOOD_STATUS, FakePrinter
from pt_p750w import printing

from pt_p750w.printing import (
    HEAD_PINS,
    INITIALIZE,
    INVALIDATE,
    MIN_LENGTH_DOTS,
    Label,
    PRINT_AREA,
    Style,
    apply_case,
    build_job,
    check_ready,
    fitted_size_pt,
    make_job,
    preview_strip,
    raster_lines,
    render,
    render_labels,
    split_labels,
)
from pt_p750w.status import PrinterError


def test_print_area_is_centred_on_head():
    for left, area in PRINT_AREA.values():
        assert 2 * left + area == HEAD_PINS


def test_print_information_matches_spec_example():
    # Spec section 2.1: 100 mm on 24 mm tape at 180 dpi = 682 raster lines.
    job = build_job([[bytes(16)] * 682], tape_mm=24)
    assert bytes.fromhex("1b 69 7a 84 00 18 00 aa 02 00 00 00 00") in job


def test_job_layout():
    job = build_job([[bytes(16)] * 2], tape_mm=24)
    assert job.startswith(bytes(100) + b"\x1b@\x1bia\x01")
    for command in (b"\x1biM\x40", b"\x1biA\x01", b"\x1biK\x08", b"\x1bid\x0e\x00", b"M\x00"):
        assert command in job
    assert job.count(b"G\x10\x00") == 2
    assert job.endswith(b"\x1a") and b"\x0c" not in job


def page_commands(job: bytes) -> list[tuple[int, int, int]]:
    """(ESC i M, ESC i A, ESC i K) parameter of each page."""
    pages = job[len(INVALIDATE + INITIALIZE):].split(b"\x1bia\x01")[1:]
    return [tuple(page[page.index(name) + 3] for name in (b"\x1biM", b"\x1biA", b"\x1biK")) for page in pages]


def test_cut_marks_set_mode_commands_per_run():
    # half, full, half, half, (full: the last label): runs of 2 and 3 labels.
    job = build_job([[bytes(16)]] * 5, tape_mm=24, full_cuts=[False, True, False, False, False])
    # Auto cut on; full cut every <run> labels; half cut bit on inside a run.
    assert page_commands(job) == [(0x40, 2, 0x0C)] * 2 + [(0x40, 3, 0x0C)] * 3


def test_full_cut_after_every_label_by_default():
    job = build_job([[bytes(16)]] * 2, tape_mm=24, copies=2)
    assert page_commands(job) == [(0x40, 1, 0x08)] * 4


def test_copies_of_a_half_cut_set_each_end_in_a_full_cut():
    job = build_job([[bytes(16)]] * 2, tape_mm=24, full_cuts=[False, False], copies=2)
    assert page_commands(job) == [(0x40, 2, 0x0C)] * 4


def test_run_lengths():
    assert printing.run_lengths([False, True, False, False, True, True]) == [2, 2, 3, 3, 3, 1]


def test_copies_repeat_the_page():
    # Spec 2.1: control codes and raster data per page, FF between pages,
    # 1A after the last; print information n9 is 0 on the first page only.
    line = b"\xff" * 16
    job = build_job([[line] * 2], tape_mm=24, copies=3)
    assert job.startswith(bytes(100) + b"\x1b@\x1bia\x01")
    assert job.count(INITIALIZE) == 1
    assert job.count(b"G\x10\x00" + line) == 6
    pages = job[len(INVALIDATE + INITIALIZE):].split(b"\x1bia\x01")[1:]
    assert [page[-1:] for page in pages] == [b"\x0c", b"\x0c", b"\x1a"]
    assert [page[11] for page in pages] == [0, 1, 1]


@pytest.mark.parametrize("copies", [0, 100, 2.0])
def test_build_job_rejects_bad_copies(copies):
    with pytest.raises(ValueError, match="copies must be"):
        build_job([[bytes(16)]], tape_mm=24, copies=copies)


def test_label_set_is_one_page_per_label_and_copies_repeat_the_set():
    a, b = [b"\x01" * 16], [b"\x02" * 16] * 2
    job = build_job([a, b], tape_mm=24, full_cuts=[False, True], copies=2)
    pages = job[len(INVALIDATE + INITIALIZE):].split(b"\x1bia\x01")[1:]
    # Raster line count in ESC i z, then the label's own data: a, b, a, b.
    assert [page[7] for page in pages] == [1, 2, 1, 2]
    assert [page.count(b"G\x10\x00\x02") for page in pages] == [0, 2, 0, 2]
    assert [page[-1:] for page in pages] == [b"\x0c", b"\x0c", b"\x0c", b"\x1a"]


def test_label_set_over_page_limit_is_rejected():
    with pytest.raises(ValueError, match="50 labels x 2 copies"):
        build_job([[bytes(16)]] * 50, tape_mm=24, copies=2)


def texts(labels: list[Label]) -> list[str]:
    return [label.text for label in labels]


def test_split_labels_on_dash_lines():
    text = "---\n01a\n\n\n---\n02a\nline 2\n  -----  \n\n---\n"
    assert texts(split_labels(text, "vertical")) == ["01a\n\n", "02a\nline 2"]
    assert texts(split_labels("01a\n---\n\n01", "vertical")) == ["01a", "\n01"]
    assert texts(split_labels("a --- b\n--", "vertical")) == ["a --- b\n--"]
    assert split_labels("---\n \n---", "vertical") == []


def test_dash_count_picks_the_cut():
    text = "01a\n---\n02a\n-----\n03a\n------\n04a\n----------\n05a"
    assert split_labels(text, "vertical") == [
        Label("01a", False), Label("02a", False), Label("03a", True), Label("04a", True), Label("05a", True),
    ]


def test_pipes_split_horizontal_text_inline():
    labels = split_labels("01a | 02a||03a |\t04a\nline 2")
    assert labels == [Label("01a", False), Label("02a", True), Label("03a", False), Label("04a\nline 2", True)]
    # Strict per direction: dashes are text in horizontal mode, pipes in vertical.
    assert texts(split_labels("a\n---\nb")) == ["a\n---\nb"]
    assert texts(split_labels("a | b", "vertical")) == ["a | b"]


def test_blank_label_keeps_the_stronger_cut():
    assert split_labels("a || | b") == [Label("a", True), Label("b", True)]
    assert split_labels("a | | b") == [Label("a", False), Label("b", True)]


def test_leading_and_trailing_blank_lines_add_the_same_length():
    style = Style(direction="vertical", size_pt=10)
    plain, leading, trailing = (render(t, 128, style).width for t in ("01", "\n01", "01\n"))
    assert leading == trailing > plain
    # Not the same picture: the blank line is on the other end.
    assert render("\n01", 128, style).tobytes() != render("01\n", 128, style).tobytes()


def test_label_set_shares_one_font_size():
    style = Style(direction="vertical")
    wide, narrow = render_labels(split_labels("WWWW\n---\nii", "vertical"), 128, style)
    alone = render("ii", 128, style)
    # "ii" alone fits a larger font, so it would be longer than in the set.
    assert narrow.height == wide.height == 128
    assert narrow.width < alone.width
    assert fitted_size_pt("WWWW\n---\nii", 24, style) == fitted_size_pt("WWWW", 24, style)


def test_render_labels_rejects_empty_set():
    with pytest.raises(ValueError, match="empty"):
        render_labels(split_labels("||"), 128)


def test_preview_strip_draws_cut_lines_between_labels():
    images = [Image.new("1", (40, 70), 1), Image.new("1", (50, 70), 0), Image.new("1", (30, 70), 1)]
    labels = [Label("a", False), Label("b", True), Label("c", True)]
    strip = preview_strip(labels, images)
    assert strip.size == (40 + 28 + 50 + 28 + 30, 70) and strip.mode == "L"
    half, full = 40 + 14, 40 + 28 + 50 + 14
    assert [strip.getpixel((half, y)) for y in (0, 5)] == [128, 255]  # dashed
    assert [strip.getpixel((full, y)) for y in (0, 5)] == [64, 64]  # solid
    assert strip.getpixel((40, 0)) == 255 and strip.getpixel((40 + 28, 0)) == 0


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


def test_send_waits_for_every_copy(fake_printer, capsys):
    _, job = make_job("Hi", 24, copies=2)
    done = FakePrinter.reply(kind=0x01)
    fake_printer.script = [(job, [done, FakePrinter.reply(kind=0x06), done])]
    printing.send("/dev/usb/lp0", job, pages=2)
    assert capsys.readouterr().out.splitlines() == ["printed 1/2", "printer: phase change", "printed 2/2"]


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
    monkeypatch.setattr(printing, "send", lambda node, job, pages=1: sent.append((job, pages)))
    return sent


def test_print_text_uses_loaded_tape_width(stub_device):
    lines = printing.print_text("Hello", copies=2)
    assert lines > MIN_LENGTH_DOTS
    job, pages = stub_device[0]
    assert pages == 2
    assert b"\x1biz\x84\x00\x18\x00" in job  # 24 mm tape


def test_print_text_rejects_bad_options_before_touching_printer(stub_device, monkeypatch):
    monkeypatch.setattr(printing, "find_device", lambda: pytest.fail("touched the printer"))
    with pytest.raises(ValueError, match="copies"):
        printing.print_text("Hello", copies=0)


def test_main_dry_run_writes_preview_without_printer(tmp_path, monkeypatch, capsys):
    prefix = tmp_path / "label"
    monkeypatch.setattr(sys, "argv", ["pt-print", "Hello", "world", "--tape", "12", "--dry-run", str(prefix)])
    monkeypatch.setattr(printing, "find_device", lambda: pytest.fail("dry run touched the printer"))
    printing.main()
    assert Image.open(f"{prefix}.png").height == PRINT_AREA[12][1]
    assert open(f"{prefix}.bin", "rb").read().endswith(b"\x1a")
    assert "tape 12 mm" in capsys.readouterr().out


def test_main_dry_run_previews_a_label_set(tmp_path, monkeypatch, capsys):
    prefix = tmp_path / "set"
    style = '{"direction": "vertical"}'
    monkeypatch.setattr(sys, "argv", ["pt-print", "01a\n---\n02a\n------\n03a", "--tape", "24", "--style", style,
                                      "--dry-run", str(prefix)])
    printing.main()
    strip = Image.open(f"{prefix}.png")
    assert {64, 128} <= {value for _, value in strip.getcolors()}  # one full and one half cut line
    assert "3 labels" in capsys.readouterr().out


def test_main_prints_on_printer(stub_device, monkeypatch):
    monkeypatch.setattr(sys, "argv", ["pt-print", "Hi | Ho", "--copies", "2"])
    printing.main()
    assert len(stub_device) == 1
    job, pages = stub_device[0]
    assert pages == 4 and page_commands(job) == [(0x40, 2, 0x0C)] * 4


def test_main_each_argument_is_a_label(stub_device, monkeypatch):
    monkeypatch.setattr(sys, "argv", ["pt-print", "01a", "02a", "03a", "--each", "--copies", "2"])
    printing.main()
    job, pages = stub_device[0]
    assert pages == 6 and page_commands(job) == [(0x40, 3, 0x0C)] * 6


def test_main_each_joins_vertical_labels_with_dash_lines(stub_device, monkeypatch):
    monkeypatch.setattr(sys, "argv", ["pt-print", "01a", "02a", "--each", "--style", '{"direction": "vertical"}'])
    printing.main()
    job, pages = stub_device[0]
    assert pages == 2 and page_commands(job) == [(0x40, 2, 0x0C)] * 2


def test_main_rejects_empty_label_set(stub_device, monkeypatch):
    monkeypatch.setattr(sys, "argv", ["pt-print", "||"])
    with pytest.raises(SystemExit, match="empty"):
        printing.main()
    assert stub_device == []


def test_main_rejects_bad_copies(stub_device, monkeypatch):
    monkeypatch.setattr(sys, "argv", ["pt-print", "Hi", "--copies", "0"])
    with pytest.raises(SystemExit, match="copies must be"):
        printing.main()
    assert stub_device == []


def test_main_refuses_when_printer_reports_error(stub_device, monkeypatch):
    monkeypatch.setattr(printing, "read_status", lambda node: FakePrinter.reply(errors_2=0x10))
    monkeypatch.setattr(sys, "argv", ["pt-print", "Hi"])
    with pytest.raises(SystemExit, match="cover open"):
        printing.main()
    assert stub_device == []


def test_main_exits_with_message_when_print_fails(stub_device, monkeypatch):
    def jam(node, job, pages):
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


def test_vertical_text_turns_counter_clockwise():
    # The line's first letter is at the far edge of the tape (high pins)...
    image = render("L      .", 128, Style(direction="vertical", align="left"))
    top, bottom = ink_rows(image)[0], ink_rows(image)[-1]
    def ink_in(rows):
        return sum(image.getpixel((x, y)) == 0 for x in range(image.width) for y in rows)
    assert ink_in(range(bottom - 20, bottom + 1)) > ink_in(range(top, top + 20))


def test_vertical_first_line_prints_first():
    # ...and the first line is in the first raster lines, so lines print in the order typed.
    image = render("WWW\n.", 128, Style(direction="vertical"))
    def ink_in(columns):
        return sum(image.getpixel((x, y)) == 0 for x in columns for y in range(image.height))
    half = image.width // 2
    assert ink_in(range(half)) > ink_in(range(half, image.width))
