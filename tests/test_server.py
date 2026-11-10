import io
import json
import sys
import threading
import urllib.error
import urllib.parse
import urllib.request

import pytest
from PIL import Image

from conftest import GOOD_STATUS
from pt_p750w import server
from pt_p750w.printing import PRINT_AREA, Style
from pt_p750w.status import PrinterError


@pytest.fixture
def api(tmp_path, monkeypatch):
    """Start the server on a free port with the USB device stubbed out."""
    printed = []
    monkeypatch.setattr(server, "find_device", lambda: "/dev/usb/lp9")
    monkeypatch.setattr(server, "read_status", lambda node: GOOD_STATUS)
    monkeypatch.setattr(server, "print_text", lambda text, cut, style: printed.append((text, cut, style)) or 386)
    (tmp_path / "index.html").write_text("<h1>ui</h1>")
    httpd = server.make_server("127.0.0.1", 0, tmp_path)
    # A short poll interval keeps shutdown() fast; the default 0.5 s adds up per test.
    threading.Thread(target=httpd.serve_forever, args=(0.001,), daemon=True).start()
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
    assert printed == [("Hello\nWorld", False, Style())]


@pytest.mark.parametrize("body", [{"text": ""}, {"text": "   "}, {"text": "x" * 201}, {"text": 5}, ["x"]])
def test_print_rejects_bad_text(api, body):
    base, printed = api
    assert call(f"{base}/api/print", body)[0] == 400
    assert printed == []


def test_print_reports_printer_error(api, monkeypatch):
    base, _ = api

    def fail(text, cut, style):
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
    with urllib.request.urlopen(f"{base}/", timeout=5) as response:
        assert response.status == 200 and b"<h1>ui</h1>" in response.read()
        # A rebuilt UI must replace the cached one on a normal reload.
        assert response.headers["Cache-Control"] == "no-cache"


def test_status_during_print_uses_cache(monkeypatch):
    # Reading status mid-job would steal the printer's completion reply.
    printer = server.Printer()
    monkeypatch.setattr(server, "find_device", lambda: pytest.fail("touched the device during a print"))
    with printer.lock:
        assert printer.status()["busy"] is True


def call_raw(url, data: bytes):
    request = urllib.request.Request(url, data=data, method="POST")
    try:
        with urllib.request.urlopen(request, timeout=5) as response:
            return response.status, response.read()
    except urllib.error.HTTPError as e:
        return e.code, e.read()


@pytest.mark.parametrize("data, message", [(b"", b"body missing"), (b"{nope", b"not JSON"), (b"x" * 5000, b"too large")])
def test_print_rejects_bad_body(api, data, message):
    base, printed = api
    code, body = call_raw(f"{base}/api/print", data)
    assert code == 400 and message in body
    assert printed == []


def test_unknown_api_get_returns_404(api):
    base, _ = api
    assert call(f"{base}/api/nope")[0] == 404


@pytest.mark.parametrize("path", ["/api/nope", "/"])
def test_post_outside_print_returns_404(api, path):
    base, printed = api
    assert call_raw(f"{base}{path}", b"{}")[0] == 404
    assert printed == []


def test_preview_defaults_to_last_seen_tape(api, monkeypatch):
    base, _ = api
    twelve_mm = bytearray(GOOD_STATUS)
    twelve_mm[10] = 12
    monkeypatch.setattr(server, "read_status", lambda node: bytes(twelve_mm))
    call(f"{base}/api/status")
    body = call(f"{base}/api/preview?text=Hi")[2]
    assert Image.open(io.BytesIO(body)).height == PRINT_AREA[12][1]


def test_preview_defaults_to_24mm_before_first_status(api):
    base, _ = api
    body = call(f"{base}/api/preview?text=Hi")[2]
    assert Image.open(io.BytesIO(body)).height == PRINT_AREA[24][1]


def test_main_reports_busy_port(monkeypatch, tmp_path):
    def busy(*args):
        raise OSError(98, "Address already in use")

    monkeypatch.setattr(server, "make_server", busy)
    monkeypatch.setattr(sys, "argv", ["pt-server", "--port", "8750", "--web", str(tmp_path)])
    with pytest.raises(SystemExit, match="Cannot listen on 127.0.0.1:8750: Address already in use"):
        server.main()


def test_main_warns_without_web_ui_and_stops_on_ctrl_c(monkeypatch, tmp_path, capsys):
    class Interrupted:
        def serve_forever(self):
            raise KeyboardInterrupt

    monkeypatch.setattr(server, "make_server", lambda *args: Interrupted())
    monkeypatch.setattr(sys, "argv", ["pt-server", "--web", str(tmp_path)])
    server.main()
    out = capsys.readouterr().out
    assert "no web UI" in out and "serving on http://127.0.0.1:8750" in out


def test_print_passes_style(api):
    base, printed = api
    code, _, _ = call(f"{base}/api/print", {"text": "Hi", "style": {"align": "left", "underline": True}})
    assert code == 200
    assert printed[0][2] == Style(align="left", underline=True)


@pytest.mark.parametrize("style", [{"align": "middle"}, {"bold": "yes"}, {"colour": "red"}, ["bold"]])
def test_print_rejects_bad_style(api, style):
    base, printed = api
    code, _, body = call(f"{base}/api/print", {"text": "Hi", "style": style})
    assert code == 400 and (b"style" in body or b"must be" in body)
    assert printed == []


def test_preview_applies_style(api):
    base, _ = api
    query = urllib.parse.urlencode({"text": "Hi", "tape": 12, "style": json.dumps({"invert": True})})
    image = Image.open(io.BytesIO(call(f"{base}/api/preview?{query}")[2]))
    # Inverted: the background is black, so most pixels are dark.
    assert image.getpixel((0, 0)) == 0


@pytest.mark.parametrize("style", ["{nope", '{"size_pt": 500}'])
def test_preview_rejects_bad_style(api, style):
    base, _ = api
    query = urllib.parse.urlencode({"text": "Hi", "style": style})
    assert call(f"{base}/api/preview?{query}")[0] == 400


def test_api_responses_are_not_cached(api):
    base, _ = api
    with urllib.request.urlopen(f"{base}/api/status", timeout=5) as response:
        assert response.headers.get_all("Cache-Control") == ["no-store"]


def test_print_reports_busy_printer(monkeypatch):
    class HeldLock:
        def acquire(self, timeout):
            return False

    printer = server.Printer()
    printer.lock = HeldLock()
    monkeypatch.setattr(server, "print_text", lambda *args: pytest.fail("printed while busy"))
    with pytest.raises(PrinterError, match="busy with another job"):
        printer.print("Hi", True, Style())


def test_print_passes_style_to_the_job(monkeypatch):
    # Unlike the api fixture, this runs the real print_text and make_job.
    jobs = []
    monkeypatch.setattr(server, "print_text", server.print_text)
    monkeypatch.setattr("pt_p750w.printing.find_device", lambda: "/dev/usb/lp9")
    monkeypatch.setattr("pt_p750w.printing.read_status", lambda node: GOOD_STATUS)
    monkeypatch.setattr("pt_p750w.printing.send", lambda node, job: jobs.append(job))
    printer = server.Printer()
    printer.print("Hi", True, Style())
    printer.print("Hi", True, Style(invert=True))
    assert len(jobs) == 2 and jobs[0] != jobs[1]
    # Inverted: the first raster line is all black across the 24 mm print area.
    first_line = jobs[1].index(b"G\x10\x00") + 3
    assert jobs[1][first_line:first_line + 16] == b"\xff" * 16


def test_main_serves_built_ui_without_warning(monkeypatch, tmp_path, capsys):
    class Interrupted:
        def serve_forever(self):
            raise KeyboardInterrupt

    (tmp_path / "index.html").write_text("<h1>ui</h1>")
    monkeypatch.setattr(server, "make_server", lambda *args: Interrupted())
    monkeypatch.setattr(sys, "argv", ["pt-server", "--web", str(tmp_path)])
    server.main()
    assert "warning" not in capsys.readouterr().out


@pytest.mark.parametrize("tape", sorted(PRINT_AREA))
def test_preview_with_every_effect_fits_each_tape(api, tape):
    base, _ = api
    style = {"underline": True, "strike": True, "outline": True, "invert": True, "border": True,
             "align": "right", "case": "title", "line_spacing": 2.0, "font": "condensed", "italic": True}
    query = urllib.parse.urlencode({"text": "Shelf 2\nkitchen", "tape": tape, "style": json.dumps(style)})
    code, _, body = call(f"{base}/api/preview?{query}")
    assert code == 200
    assert Image.open(io.BytesIO(body)).height == PRINT_AREA[tape][1]
