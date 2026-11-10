"""Read live status from a Brother PT-P750W over /dev/usb/lpN.

Protocol: Brother "Raster Command Reference" for PT-E550W/P750W/P710BT.
"""
import glob
import os
import select
import sys
import time
from pathlib import Path

VENDOR, PRODUCT = "04f9", "2062"
DEV_GLOB = "/dev/usb/lp*"
USBMISC = Path("/sys/class/usbmisc")
DRAIN_TIMEOUT = 0.2

INVALIDATE = b"\x00" * 100
INITIALIZE = b"\x1b@"
STATUS_REQUEST = b"\x1biS"

ERRORS_1 = {0: "no media", 2: "cutter jam", 3: "weak batteries", 6: "high-voltage adapter"}
ERRORS_2 = {0: "replace media", 4: "cover open", 5: "overheating"}
MEDIA_TYPES = {
    0x00: "none",
    0x01: "laminated",
    0x03: "non-laminated",
    0x11: "heat-shrink tube 2:1",
    0x17: "heat-shrink tube 3:1",
    0xFF: "incompatible",
}
STATUS_TYPES = {
    0x00: "reply to status request",
    0x01: "printing completed",
    0x02: "error",
    0x04: "turned off",
    0x05: "notification",
    0x06: "phase change",
}
COLOURS = {
    0x01: "white", 0x02: "other", 0x03: "clear", 0x04: "red", 0x05: "blue",
    0x06: "yellow", 0x07: "green", 0x08: "black", 0x0A: "gold",
}


class PrinterError(Exception):
    """The printer is missing, unreachable or not ready."""


def find_device() -> str:
    for node in sorted(glob.glob(DEV_GLOB)):
        usb_dev = (USBMISC / Path(node).name / "device").resolve().parent
        try:
            ids = ((usb_dev / "idVendor").read_text().strip(), (usb_dev / "idProduct").read_text().strip())
        except FileNotFoundError:
            continue
        if ids == (VENDOR, PRODUCT):
            return node
    raise PrinterError(f"No PT-P750W ({VENDOR}:{PRODUCT}) found. Is Editor Lite off and the USB port passed through?")


def open_printer(node: str) -> int:
    try:
        return os.open(node, os.O_RDWR)
    except PermissionError:
        raise PrinterError(f"No access to {node}. Run `just install-udev` once.")
    except FileNotFoundError:
        raise PrinterError(f"{node} disappeared. Is the printer still on?")


def read_reply(fd: int, timeout: float = 3.0) -> bytes:
    """Read one 32-byte status reply, or fewer bytes if the timeout expires."""
    deadline = time.monotonic() + timeout
    data = b""
    while len(data) < 32:
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            break
        ready, _, _ = select.select([fd], [], [], remaining)
        if not ready:
            break
        chunk = os.read(fd, 32 - len(data))
        if not chunk:
            # usblp can report readable with nothing queued; avoid a hot loop.
            time.sleep(0.05)
            continue
        data += chunk
    return data


def drain(fd: int) -> None:
    """Discard queued unsolicited replies so the next read answers our request."""
    while read_reply(fd, timeout=DRAIN_TIMEOUT):
        pass


def read_status(node: str, timeout: float = 3.0) -> bytes:
    fd = open_printer(node)
    try:
        drain(fd)
        os.write(fd, INVALIDATE + INITIALIZE + STATUS_REQUEST)
        return read_reply(fd, timeout)
    finally:
        os.close(fd)


def bits(value: int, names: dict[int, str]) -> list[str]:
    return [name for bit, name in names.items() if value & (1 << bit)] + (
        [f"unknown bits 0x{value:02x}"] if value & ~sum(1 << b for b in names) else []
    )


def named(table: dict[int, str], value: int) -> str:
    return table.get(value, f"unknown (0x{value:02x})")


def decode(status: bytes) -> dict:
    """Decode a 32-byte status reply. Raises PrinterError on a malformed reply."""
    if len(status) != 32 or status[0] != 0x80:
        raise PrinterError(f"Bad status reply: {status.hex(' ') or 'nothing'}")
    return {
        "model_code": status[4],
        "status_type": named(STATUS_TYPES, status[18]),
        "errors": bits(status[8], ERRORS_1) + bits(status[9], ERRORS_2),
        "tape_mm": status[10],
        "media": named(MEDIA_TYPES, status[11]),
        "tape_colour": named(COLOURS, status[24]),
        "text_colour": named(COLOURS, status[25]),
        "raw": status.hex(" "),
    }


def main() -> None:
    try:
        node = sys.argv[1] if len(sys.argv) > 1 else find_device()
        info = decode(read_status(node))
    except PrinterError as e:
        sys.exit(str(e))
    print(f"device       {node}")
    print(f"model code   0x{info['model_code']:02x}")
    print(f"status       {info['status_type']}")
    print(f"errors       {', '.join(info['errors']) or 'none'}")
    print(f"tape width   {info['tape_mm']} mm")
    print(f"media type   {info['media']}")
    print(f"tape colour  {info['tape_colour']}")
    print(f"text colour  {info['text_colour']}")
    print(f"raw          {info['raw']}")


if __name__ == "__main__":
    main()
