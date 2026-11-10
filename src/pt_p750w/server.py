"""HTTP API and web UI host for the PT-P750W.

GET  /api/status            live status, re-read from the printer on each call
GET  /api/preview?text=...  PNG of the label as it would print on the loaded tape.
                            Optional tape=<mm> and style=<JSON, see printing.Style>.
                            Several labels show side by side: dashed line = half cut, solid = full cut.
                            upright=1 turns the strip clockwise so its start is at the top (vertical text).
GET  /api/fit?text=...      font size the renderer picks, as {"size_pt": 51.2}. Same query as preview.
POST /api/print             {"text": "...", "copies": 1, "style": {...}}
Cut marks in text split it into labels (see printing.split_labels): horizontal
text uses | (half cut) or || (full cut), vertical text a line of --- or ------.
Anything else is served from the built web UI directory.
"""
import argparse
import io
import json
import sys
import threading
from datetime import UTC, datetime
from functools import partial
from http import HTTPStatus
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlparse

from PIL import Image

from pt_p750w.printing import (
    PRINT_AREA,
    Style,
    check_job_options,
    fitted_size_pt,
    label_preview,
    print_text,
    split_labels,
)
from pt_p750w.status import PrinterError, decode, find_device, read_status

MAX_TEXT = 1000  # room for a set of labels, e.g. 99 short ones
MAX_BODY = 8192
DEFAULT_TAPE_MM = 24


class Printer:
    """Serialises access to the one USB device and caches the last status."""

    def __init__(self) -> None:
        self.lock = threading.Lock()
        self.last = {"connected": False, "device": None, "error": "not checked yet", "status": None}

    def status(self) -> dict:
        # A print job holds the lock for several seconds. A status request in
        # the middle would steal the printer's "printing completed" reply, so
        # report the cached state instead of touching the device.
        if not self.lock.acquire(blocking=False):
            return {**self.last, "busy": True}
        try:
            self.last = self._read()
            return {**self.last, "busy": False}
        finally:
            self.lock.release()

    def _read(self) -> dict:
        checked_at = datetime.now(UTC).isoformat()
        try:
            node = find_device()
            info = decode(read_status(node))
        except PrinterError as e:
            return {"connected": False, "device": None, "error": str(e), "status": None, "checked_at": checked_at}
        return {"connected": True, "device": node, "error": None, "status": info, "checked_at": checked_at}

    def tape_mm(self) -> int:
        status = self.last.get("status") or {}
        return status.get("tape_mm") if status.get("tape_mm") in PRINT_AREA else DEFAULT_TAPE_MM

    def print(self, text: str, style: Style, copies: int) -> int:
        if not self.lock.acquire(timeout=30):
            raise PrinterError("Printer is busy with another job")
        try:
            return print_text(text, style, copies)
        finally:
            self.lock.release()


class Handler(SimpleHTTPRequestHandler):
    printer: Printer

    def do_GET(self) -> None:
        url = urlparse(self.path)
        if url.path == "/api/status":
            self.send_json(HTTPStatus.OK, self.printer.status())
        elif url.path == "/api/preview":
            self.preview(parse_qs(url.query))
        elif url.path == "/api/fit":
            self.fit(parse_qs(url.query))
        elif url.path.startswith("/api/"):
            self.send_json(HTTPStatus.NOT_FOUND, {"error": "unknown endpoint"})
        else:
            super().do_GET()

    def end_headers(self) -> None:
        # Without this the browser may keep an old index.html after `just
        # web-build` and load the previous bundle. Hashed assets revalidate cheaply.
        if not self.path.startswith("/api/"):
            self.send_header("Cache-Control", "no-cache")
        super().end_headers()

    def do_POST(self) -> None:
        if urlparse(self.path).path != "/api/print":
            self.send_json(HTTPStatus.NOT_FOUND, {"error": "unknown endpoint"})
            return
        try:
            body = self.read_json()
            style = Style.from_dict(body.get("style"))
            text = clean_text(body.get("text"), style.direction)
            copies = body.get("copies", 1)
            labels = len(split_labels(text, style.direction))
            check_job_options(copies, labels)
        except ValueError as e:
            self.send_json(HTTPStatus.BAD_REQUEST, {"error": str(e)})
            return
        try:
            lines = self.printer.print(text, style, copies)
        except PrinterError as e:
            self.send_json(HTTPStatus.CONFLICT, {"error": str(e)})
            return
        self.send_json(HTTPStatus.OK, {"ok": True, "labels": labels * copies, "raster_lines": lines,
                                       "length_mm": round(lines / 180 * 25.4, 1)})

    def label_query(self, query: dict) -> tuple[str, int, Style] | None:
        """Parse text, tape and style for preview and fit. Sends a 400 and returns None if invalid."""
        try:
            style = Style.from_dict(json.loads(query.get("style", ["{}"])[0]))
            text = clean_text(query.get("text", [""])[0], style.direction)
            tape = int(query.get("tape", [self.printer.tape_mm()])[0])
            if tape not in PRINT_AREA:
                raise ValueError(f"tape must be one of {sorted(PRINT_AREA)}")
            return text, tape, style
        except ValueError as e:
            self.send_json(HTTPStatus.BAD_REQUEST, {"error": str(e)})
            return None

    def fit(self, query: dict) -> None:
        if parsed := self.label_query(query):
            self.send_json(HTTPStatus.OK, {"size_pt": fitted_size_pt(*parsed)})

    def preview(self, query: dict) -> None:
        if not (parsed := self.label_query(query)):
            return
        image = label_preview(*parsed)
        if query.get("upright") == ["1"]:
            image = image.transpose(Image.Transpose.ROTATE_270)
        png = io.BytesIO()
        image.save(png, "PNG")
        self.send_bytes(HTTPStatus.OK, "image/png", png.getvalue())

    def read_json(self) -> dict:
        length = int(self.headers.get("Content-Length") or 0)
        if not 0 < length <= MAX_BODY:
            raise ValueError("body missing or too large")
        try:
            body = json.loads(self.rfile.read(length))
        except json.JSONDecodeError:
            raise ValueError("body is not JSON")
        if not isinstance(body, dict):
            raise ValueError("body must be a JSON object")
        return body

    def send_json(self, status: HTTPStatus, data: dict) -> None:
        self.send_bytes(status, "application/json", json.dumps(data).encode())

    def send_bytes(self, status: HTTPStatus, content_type: str, data: bytes) -> None:
        self.send_response(status)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(data)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(data)

    def log_message(self, format: str, *args) -> None:
        # The UI polls status every 2 s; logging each poll buries real events.
        if not self.path.startswith("/api/status"):
            super().log_message(format, *args)


def clean_text(text: object, direction: str) -> str:
    if not isinstance(text, str) or not split_labels(text, direction):
        raise ValueError("text is empty")
    if len(text) > MAX_TEXT:
        raise ValueError(f"text is longer than {MAX_TEXT} characters")
    return text


def make_server(host: str, port: int, web_dir: Path, printer: Printer | None = None) -> ThreadingHTTPServer:
    handler = partial(type("BoundHandler", (Handler,), {"printer": printer or Printer()}), directory=str(web_dir))
    return ThreadingHTTPServer((host, port), handler)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--host", default="127.0.0.1", help="use 0.0.0.0 to allow other machines (no auth)")
    parser.add_argument("--port", type=int, default=8750)
    parser.add_argument("--web", type=Path, default=Path("web/dist"), help="built web UI directory")
    args = parser.parse_args()
    if not (args.web / "index.html").is_file():
        print(f"warning: no web UI in {args.web}; only /api works. Build it with `just web-build`.")
    try:
        server = make_server(args.host, args.port, args.web)
    except OSError as e:
        sys.exit(f"Cannot listen on {args.host}:{args.port}: {e.strerror}. "
                 f"Another pt-server may be running (`ss -ltnp | grep {args.port}`), or use --port.")
    print(f"serving on http://{args.host}:{args.port}")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass


if __name__ == "__main__":
    main()
