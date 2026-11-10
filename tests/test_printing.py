import pytest
from PIL import Image

from pt_p750w.printing import (
    HEAD_PINS,
    MIN_LENGTH_DOTS,
    PRINT_AREA,
    build_job,
    check_ready,
    raster_lines,
    render,
)

# Status reply captured from the real printer: 24 mm laminated, black on white.
GOOD_STATUS = bytes.fromhex(
    "80 20 42 30 68 30 04 00 00 00 18 01 00 00 00 00"
    "00 00 00 00 00 00 00 00 01 08 00 00 00 00 00 00"
)


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
    with pytest.raises(SystemExit, match="replace media"):
        check_ready(bytes(replace_media))


def test_check_ready_rejects_garbage():
    with pytest.raises(SystemExit, match="Bad status"):
        check_ready(b"")
