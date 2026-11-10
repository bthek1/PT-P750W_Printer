import sys

import pytest
from PIL import Image

from conftest import GOOD_STATUS, FakePrinter
from pt_p750w import printing

from pt_p750w.printing import (
    HEAD_PINS,
    MIN_LENGTH_DOTS,
    PRINT_AREA,
    build_job,
    check_ready,
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
