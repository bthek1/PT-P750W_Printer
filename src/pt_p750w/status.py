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


def find_device() -> str:
    for node in sorted(glob.glob("/dev/usb/lp*")):
        usb_dev = Path("/sys/class/usbmisc", Path(node).name, "device").resolve().parent
        try:
            ids = ((usb_dev / "idVendor").read_text().strip(), (usb_dev / "idProduct").read_text().strip())
        except FileNotFoundError:
            continue
        if ids == (VENDOR, PRODUCT):
            return node
    sys.exit(f"No PT-P750W ({VENDOR}:{PRODUCT}) found. Is Editor Lite off and the USB port passed through?")


def open_printer(node: str) -> int:
    try:
        return os.open(node, os.O_RDWR)
    except PermissionError:
        sys.exit(f"No access to {node}. Run `just install-udev` once.")


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
    while read_reply(fd, timeout=0.2):
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


def main() -> None:
    node = sys.argv[1] if len(sys.argv) > 1 else find_device()
    status = read_status(node)
    if len(status) != 32 or status[0] != 0x80:
        sys.exit(f"Bad reply from {node}: {status.hex(' ') or 'nothing'}")
    errors = bits(status[8], ERRORS_1) + bits(status[9], ERRORS_2)
    print(f"device       {node}")
    print(f"model code   0x{status[4]:02x}")
    print(f"status       {named(STATUS_TYPES, status[18])}")
    print(f"errors       {', '.join(errors) or 'none'}")
    print(f"tape width   {status[10]} mm")
    print(f"media type   {named(MEDIA_TYPES, status[11])}")
    print(f"tape colour  {named(COLOURS, status[24])}")
    print(f"text colour  {named(COLOURS, status[25])}")
    print(f"raw          {status.hex(' ')}")


if __name__ == "__main__":
    main()
