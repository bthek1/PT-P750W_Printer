import socket
import threading

import pytest

from pt_p750w import printing, status

# Status reply captured from the real printer: 24 mm laminated, black on white.
GOOD_STATUS = bytes.fromhex(
    "80 20 42 30 68 30 04 00 00 00 18 01 00 00 00 00"
    "00 00 00 00 00 00 00 00 01 08 00 00 00 00 00 00"
)


class FakePrinter:
    """Plays the printer on the far end of a socketpair.

    select() and os.read/os.write behave the same on a socket fd as on
    /dev/usb/lpN, so the real I/O code runs unchanged.
    """

    def __init__(self) -> None:
        self.stale: list[bytes] = []  # unsolicited replies already queued when opened
        self.script: list[tuple[bytes, list[bytes]]] = []  # (request suffix, replies)
        self.received = bytearray()
        self.opened: list[str] = []

    @staticmethod
    def reply(kind: int = 0x00, errors_1: int = 0, errors_2: int = 0) -> bytes:
        data = bytearray(GOOD_STATUS)
        data[8], data[9], data[18] = errors_1, errors_2, kind
        return bytes(data)

    def open(self, node: str) -> int:
        self.opened.append(node)
        ours, peer = socket.socketpair()
        for message in self.stale:
            peer.sendall(message)
        threading.Thread(target=self._serve, args=(peer,), daemon=True).start()
        return ours.detach()

    def _serve(self, peer: socket.socket) -> None:
        script = list(self.script)
        pending = bytearray()
        with peer:
            while script:
                chunk = peer.recv(65536)
                if not chunk:
                    return
                pending += chunk
                self.received += chunk
                suffix, replies = script[0]
                if pending.endswith(suffix):
                    script.pop(0)
                    pending.clear()
                    for message in replies:
                        peer.sendall(message)
            # Keep the line open until the client closes it, like the device.
            while peer.recv(65536):
                pass


@pytest.fixture
def fake_printer(monkeypatch):
    printer = FakePrinter()
    monkeypatch.setattr(status, "open_printer", printer.open)
    monkeypatch.setattr(printing, "open_printer", printer.open)
    monkeypatch.setattr(status, "DRAIN_TIMEOUT", 0.01)
    return printer
