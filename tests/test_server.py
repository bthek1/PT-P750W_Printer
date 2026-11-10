import json
import threading
import urllib.error
import urllib.request

import pytest

from pt_p750w import server
from pt_p750w.status import PrinterError

GOOD_STATUS = bytes.fromhex(
    "80 20 42 30 68 30 04 00 00 00 18 01 00 00 00 00"
    "00 00 00 00 00 00 00 00 01 08 00 00 00 00 00 00"
)


@pytest.fixture
def api(tmp_path, monkeypatch):
    """Start the server on a free port with the USB device stubbed out."""
    printed = []
    monkeypatch.setattr(server, "find_device", lambda: "/dev/usb/lp9")
    monkeypatch.setattr(server, "read_status", lambda node: GOOD_STATUS)
    monkeypatch.setattr(server, "print_text", lambda text, cut: printed.append((text, cut)) or 386)
    (tmp_path / "index.html").write_text("<h1>ui</h1>")
    httpd = server.make_server("127.0.0.1", 0, tmp_path)
    # A short poll interval keeps shutdown() fast; the default 0.5 s adds up per test.
    threading.Thread(target=httpd.serve_forever, args=(0.01,), daemon=True).start()
    base = f"http://127.0.0.1:{httpd.server_address[1]}"
    yield base, printed
    httpd.shutdown()
    httpd.server_close()


def call(url, body=None):
    data = json.dumps(body).encode() if body is not None else None
    request = urllib.request.Request(url, data=data, headers={"Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(request, timeout=5) as response:
            return response.status, response.headers["Content-Type"], response.read()
    except urllib.error.HTTPError as e:
        return e.code, e.headers["Content-Type"], e.read()


def test_status_decodes_printer_reply(api):
    base, _ = api
    code, _, body = call(f"{base}/api/status")
    data = json.loads(body)
    assert code == 200
    assert data["connected"] and not data["busy"]
    assert data["status"]["tape_mm"] == 24
    assert data["status"]["errors"] == []
    assert data["status"]["media"] == "laminated"


def test_status_reports_missing_printer(api, monkeypatch):
    base, _ = api

    def missing():
        raise PrinterError("No PT-P750W found")

    monkeypatch.setattr(server, "find_device", missing)
    data = json.loads(call(f"{base}/api/status")[2])
    assert not data["connected"]
    assert "No PT-P750W" in data["error"]


def test_print_sends_text(api):
    base, printed = api
    code, _, body = call(f"{base}/api/print", {"text": "Hello\nWorld", "cut": False})
    assert code == 200
    assert json.loads(body)["raster_lines"] == 386
    assert printed == [("Hello\nWorld", False)]


@pytest.mark.parametrize("body", [{"text": ""}, {"text": "   "}, {"text": "x" * 201}, {"text": 5}, ["x"]])
def test_print_rejects_bad_text(api, body):
    base, printed = api
    assert call(f"{base}/api/print", body)[0] == 400
    assert printed == []


def test_print_reports_printer_error(api, monkeypatch):
    base, _ = api

    def fail(text, cut):
        raise PrinterError("Printer not ready: cover open")

    monkeypatch.setattr(server, "print_text", fail)
    code, _, body = call(f"{base}/api/print", {"text": "Hi"})
    assert code == 409
    assert "cover open" in json.loads(body)["error"]


def test_preview_is_png(api):
    base, _ = api
    code, content_type, body = call(f"{base}/api/preview?text=Hi&tape=12")
    assert code == 200 and content_type == "image/png"
    assert body.startswith(b"\x89PNG")


def test_preview_rejects_unknown_tape(api):
    base, _ = api
    assert call(f"{base}/api/preview?text=Hi&tape=7")[0] == 400


def test_serves_web_ui(api):
    base, _ = api
    code, _, body = call(f"{base}/")
    assert code == 200 and b"<h1>ui</h1>" in body


def test_status_during_print_uses_cache(monkeypatch):
    # Reading status mid-job would steal the printer's completion reply.
    printer = server.Printer()
    monkeypatch.setattr(server, "find_device", lambda: pytest.fail("touched the device during a print"))
    with printer.lock:
        assert printer.status()["busy"] is True
