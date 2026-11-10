import os
import sys

import pytest

from conftest import GOOD_STATUS, FakePrinter
from pt_p750w import status
from pt_p750w.status import INITIALIZE, INVALIDATE, STATUS_REQUEST, PrinterError

REQUEST = INVALIDATE + INITIALIZE + STATUS_REQUEST


def test_decode_known_good_reply():
    assert status.decode(GOOD_STATUS) == {
        "model_code": 0x68,
        "status_type": "reply to status request",
        "errors": [],
        "tape_mm": 24,
        "media": "laminated",
        "tape_colour": "white",
        "text_colour": "black",
        "raw": GOOD_STATUS.hex(" "),
    }


def test_decode_12mm_flex_id_reply_from_hardware():
    # Seen on 2026-11-11 with 12 mm tape: tape colour 0x90 is "White (Flex. ID)" in spec table (8).
    reply = bytes.fromhex("80 20 42 30 68 30 04 00 00 00 0c 01 00 00 00 00 00 00 00 00 00 00 00 00 90 08 00 00 00 00 00 00")
    info = status.decode(reply)
    assert (info["tape_mm"], info["tape_colour"], info["text_colour"]) == (12, "white (flex. ID)", "black")


def test_tape_and_text_colours_use_separate_tables():
    # 0x09 is a tape colour only; 0x0A (gold) is a text colour only.
    assert status.named(status.TAPE_COLOURS, 0x09) == "clear (white text)"
    assert status.named(status.TEXT_COLOURS, 0x09) == "unknown (0x09)"
    assert status.named(status.TEXT_COLOURS, 0x0A) == "gold"
    assert status.named(status.TAPE_COLOURS, 0x0A) == "unknown (0x0a)"


def test_decode_lists_errors_from_both_fields():
    info = status.decode(FakePrinter.reply(kind=0x02, errors_1=0x01, errors_2=0x10))
    assert info["errors"] == ["no media", "cover open"]
    assert info["status_type"] == "error"


@pytest.mark.parametrize("reply", [b"", GOOD_STATUS[:31], b"\x00" + GOOD_STATUS[1:]])
def test_decode_rejects_malformed_reply(reply):
    with pytest.raises(PrinterError, match="Bad status reply"):
        status.decode(reply)


def test_unknown_codes_are_named_not_dropped():
    assert status.named(status.MEDIA_TYPES, 0x42) == "unknown (0x42)"
    assert status.bits(0x82, status.ERRORS_1) == ["unknown bits 0x82"]


def fake_usb_device(tmp_path, name: str, vendor: str, product: str | None) -> None:
    """Mimic /dev/usb/<name> and its sysfs link to the USB interface."""
    (tmp_path / "dev" / name).touch()
    usb_dev = tmp_path / "devices" / name
    (usb_dev / "interface").mkdir(parents=True)
    (usb_dev / "idVendor").write_text(vendor + "\n")
    if product:
        (usb_dev / "idProduct").write_text(product + "\n")
    (tmp_path / "usbmisc" / name).mkdir(parents=True)
    (tmp_path / "usbmisc" / name / "device").symlink_to(usb_dev / "interface")


@pytest.fixture
def sysfs(tmp_path, monkeypatch):
    (tmp_path / "dev").mkdir()
    monkeypatch.setattr(status, "DEV_GLOB", str(tmp_path / "dev" / "lp*"))
    monkeypatch.setattr(status, "USBMISC", tmp_path / "usbmisc")
    return tmp_path


def test_find_device_picks_the_pt_p750w(sysfs):
    fake_usb_device(sysfs, "lp0", "04f9", "2042")  # the HL-L2305 laser
    fake_usb_device(sysfs, "lp1", "04f9", None)  # half-gone device mid-unplug
    fake_usb_device(sysfs, "lp2", "04f9", "2062")
    assert status.find_device() == str(sysfs / "dev" / "lp2")


def test_find_device_ignores_editor_lite_mode(sysfs):
    fake_usb_device(sysfs, "lp0", "04f9", "2065")
    with pytest.raises(PrinterError, match="Editor Lite"):
        status.find_device()


def test_open_printer_reports_missing_permission(tmp_path):
    node = tmp_path / "lp0"
    node.touch(mode=0)
    if os.access(node, os.R_OK):
        pytest.skip("running as root")
    with pytest.raises(PrinterError, match="install-udev"):
        status.open_printer(str(node))


def test_open_printer_reports_vanished_device(tmp_path):
    with pytest.raises(PrinterError, match="disappeared"):
        status.open_printer(str(tmp_path / "lp0"))


def test_read_status_discards_stale_replies(fake_printer):
    # A queued "printing completed" must not be mistaken for the answer.
    fake_printer.stale = [FakePrinter.reply(kind=0x01)]
    fake_printer.script = [(REQUEST, [GOOD_STATUS])]
    assert status.read_status("/dev/usb/lp0") == GOOD_STATUS
    assert fake_printer.received == REQUEST


def test_read_status_times_out_with_partial_reply(fake_printer):
    fake_printer.script = [(REQUEST, [GOOD_STATUS[:10]])]
    assert status.read_status("/dev/usb/lp0", timeout=0.02) == GOOD_STATUS[:10]


def test_main_prints_decoded_status(monkeypatch, capsys):
    monkeypatch.setattr(sys, "argv", ["pt-status", "/dev/usb/lp7"])
    monkeypatch.setattr(status, "read_status", lambda node: GOOD_STATUS)
    status.main()
    out = capsys.readouterr().out
    assert "/dev/usb/lp7" in out
    assert "tape width   24 mm" in out
    assert "errors       none" in out


def test_main_exits_with_message_when_printer_missing(monkeypatch):
    def missing():
        raise PrinterError("No PT-P750W (04f9:2062) found.")

    monkeypatch.setattr(sys, "argv", ["pt-status"])
    monkeypatch.setattr(status, "find_device", missing)
    with pytest.raises(SystemExit, match="No PT-P750W"):
        status.main()


def test_read_reply_stops_when_nothing_arrives(monkeypatch):
    monkeypatch.setattr(status.select, "select", lambda r, w, x, t: ([], [], []))
    monkeypatch.setattr(status.os, "read", lambda fd, n: pytest.fail("read without data"))
    assert status.read_reply(0, timeout=5) == b""


def test_read_reply_retries_after_empty_read(monkeypatch):
    # usblp can report readable with nothing queued.
    chunks = [b"", GOOD_STATUS]
    sleeps = []
    monkeypatch.setattr(status.select, "select", lambda r, w, x, t: (r, [], []))
    monkeypatch.setattr(status.os, "read", lambda fd, n: chunks.pop(0))
    monkeypatch.setattr(status.time, "sleep", sleeps.append)
    assert status.read_reply(0, timeout=5) == GOOD_STATUS
    assert sleeps == [0.05]


def test_read_reply_with_spent_timeout_does_not_wait(monkeypatch):
    monkeypatch.setattr(status.select, "select", lambda *args: pytest.fail("waited after the deadline"))
    assert status.read_reply(0, timeout=0) == b""
