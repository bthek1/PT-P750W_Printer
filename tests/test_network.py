import socket
import struct
import sys
import threading

import pytest

from conftest import GOOD_STATUS
from pt_p750w import network, printing, status
from pt_p750w.network import read_tlv, tlv
from pt_p750w.status import PrinterError

REAL_BROADCAST_ADDRESSES = network.broadcast_addresses  # conftest stubs it out for every test
P750W_ID = b"MFG:Brother;CMD:PT-CBP;MDL:PT-P750W;CLS:PRINTER;"
# Replies seen from the HL-L2305 laser on the LAN.
LASER = {
    network.IDENTITY_OIDS[0]: b"MFG:Brother;CMD:PJL,HBP,URF;MDL:HL-L2305 series;CLS:PRINTER;",
    network.IDENTITY_OIDS[1]: b"Brother HL-L2305 series",
    network.IDENTITY_OIDS[2]: b"Brother NC-8300w, Firmware Ver.P  ,MID 84U-F02",
}


def response(request_id: int, value: bytes | None, error: int = 0, tag: int = 0x04) -> bytes:
    body = b"" if value is None else value
    varbind = tlv(0x30, network.ber_oid("1.3.6.1.2.1.1.1.0") + tlv(tag, body))
    pdu = tlv(0xA2, network.ber_int(request_id) + network.ber_int(error) + network.ber_int(0) + tlv(0x30, varbind))
    return tlv(0x30, network.ber_int(0) + tlv(0x04, b"public") + pdu)


def request_fields(data: bytes) -> tuple[int, bytes]:
    """(request id, encoded oid) of a GetRequest."""
    _, message, _ = read_tlv(data, 0)
    _, _, pos = read_tlv(message, 0)
    _, _, pos = read_tlv(message, pos)
    tag, pdu, _ = read_tlv(message, pos)
    assert tag == 0xA0
    _, request_id, pos = read_tlv(pdu, 0)
    _, _, pos = read_tlv(pdu, pos)
    _, _, pos = read_tlv(pdu, pos)
    _, varbinds, _ = read_tlv(pdu, pos)
    _, varbind, _ = read_tlv(varbinds, 0)
    _, oid, _ = read_tlv(varbind, 0)
    return int.from_bytes(request_id, "big"), oid


class Agent:
    """A one-host SNMP agent on 127.0.0.1 that answers the oids in `values`."""

    def __init__(self, values: dict[str, bytes]) -> None:
        self.values = values
        self.extra: list[bytes] = []  # sent before each real answer
        self.sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        self.sock.bind(("127.0.0.1", 0))
        self.port = self.sock.getsockname()[1]
        threading.Thread(target=self._serve, daemon=True).start()

    def _serve(self) -> None:
        by_oid = {network.ber_oid(oid)[2:]: value for oid, value in self.values.items()}
        while True:
            try:
                data, peer = self.sock.recvfrom(4096)
            except OSError:
                return
            request_id, oid = request_fields(data)
            for extra in self.extra:
                self.sock.sendto(extra, peer)
            value = by_oid.get(oid)
            self.sock.sendto(response(request_id, value, error=0 if value else 2, tag=0x04 if value else 0x05), peer)


@pytest.fixture
def agent(monkeypatch):
    agents = []

    def start(values):
        agents.append(Agent(values))
        monkeypatch.setattr(network, "SNMP_PORT", agents[-1].port)
        return agents[-1]

    yield start
    for a in agents:
        a.sock.close()


@pytest.fixture
def lan(monkeypatch):
    """Broadcasts go to 127.0.0.1, where the agent listens."""
    monkeypatch.setattr(network, "broadcast_addresses", lambda: ["127.0.0.1"])
    monkeypatch.setattr(network, "DISCOVERY_TIMEOUT", 0.1)
    monkeypatch.setattr(network, "CHECK_TIMEOUT", 0.1)


# --- BER -------------------------------------------------------------------------

def test_ber_encodings():
    assert network.ber_oid("1.3.6.1.4.1.2435") == bytes.fromhex("06 07 2b 06 01 04 01 93 03")
    assert network.ber_int(0) == b"\x02\x01\x00"
    assert network.ber_int(128) == b"\x02\x02\x00\x80"
    assert tlv(0x04, b"x" * 200)[:3] == b"\x04\x81\xc8"
    assert tlv(0x04, b"x" * 300)[:4] == b"\x04\x82\x01\x2c"


def test_get_request_round_trips():
    request_id, oid = request_fields(network.get_request(1234, network.STATUS_OID))
    assert (request_id, oid) == (1234, network.ber_oid(network.STATUS_OID)[2:])


def test_parse_response_values_and_errors():
    assert network.parse_response(response(7, GOOD_STATUS)) == (7, GOOD_STATUS)
    assert network.parse_response(response(7, b"y" * 300)) == (7, b"y" * 300)
    assert network.parse_response(response(7, b"", error=2)) == (7, None)  # noSuchName
    assert network.parse_response(response(7, b"\x05", tag=0x02)) == (7, None)  # not an octet string


@pytest.mark.parametrize("data", [b"", b"\x30", b"\x30\x05\x02\x01", network.get_request(1, "1.3.6.1")])
def test_parse_response_rejects_malformed(data):
    with pytest.raises(ValueError):
        network.parse_response(data)


# --- SNMP over UDP ------------------------------------------------------------------

def test_snmp_get_collects_values_and_skips_noise(agent):
    a = agent(LASER)
    a.extra = [b"garbage", response(999_999_999, b"wrong request id")]
    oids = (network.IDENTITY_OIDS[1], network.STATUS_OID)
    assert network.snmp_get(["127.0.0.1", "no.such.host.invalid"], oids, 0.2) == {
        "127.0.0.1": {network.IDENTITY_OIDS[1]: b"Brother HL-L2305 series"},
    }


def test_discover_finds_only_the_label_printer(agent, lan):
    agent(LASER)
    assert network.discover() == []
    agent({network.IDENTITY_OIDS[0]: P750W_ID})
    assert network.discover() == ["127.0.0.1"]
    assert network.is_printer("127.0.0.1")


def test_read_status_over_snmp(agent):
    agent({network.STATUS_OID: GOOD_STATUS})
    assert network.read_status("127.0.0.1", 0.2) == GOOD_STATUS
    agent(LASER)
    assert network.read_status("127.0.0.1", 0.05) is None


def test_broadcast_addresses_reads_each_interface(monkeypatch):
    addresses = {"eth0": "192.168.2.255", "eth1": "192.168.2.255", "dummy0": "0.0.0.0"}

    def ioctl(fd, request, arg):
        name = arg.rstrip(b"\0").decode()
        if name not in addresses:
            raise OSError("no IPv4 address")
        return struct.pack("20s4s", b"", socket.inet_aton(addresses[name])) + bytes(232)

    monkeypatch.setattr(network.socket, "if_nameindex", lambda: [(1, "lo"), (2, "eth0"), (3, "wlan0"),
                                                                  (4, "eth1"), (5, "dummy0")])
    monkeypatch.setattr(network.fcntl, "ioctl", ioctl)
    assert REAL_BROADCAST_ADDRESSES() == ["192.168.2.255", "255.255.255.255"]


# --- finding the printer ----------------------------------------------------------------

def test_find_printer_scans_then_remembers(agent, lan):
    agent({network.IDENTITY_OIDS[0]: P750W_ID})
    assert network.remembered() is None
    assert network.find_printer() == "127.0.0.1"
    assert network.remembered() == "127.0.0.1"
    network.broadcast_addresses = lambda: pytest.fail("rescanned although the cached host answers")
    assert network.find_printer() == "127.0.0.1"


def test_find_printer_rescans_at_most_every_rescan_after(agent, lan, monkeypatch):
    agent(LASER)
    network.remember("127.0.0.1")  # stale: it is the laser now
    scans = []
    monkeypatch.setattr(network, "discover", lambda: scans.append(1) or [])
    assert network.find_printer(rescan_after=60) is None
    assert network.find_printer(rescan_after=60) is None
    assert len(scans) == 1
    assert network.find_printer() is None
    assert len(scans) == 2


def test_remember_survives_unwritable_cache(tmp_path, monkeypatch):
    blocker = tmp_path / "file"
    blocker.write_text("")
    monkeypatch.setenv("XDG_CACHE_HOME", str(blocker))
    network.remember("10.0.0.5")
    assert network.remembered() is None


def test_cache_defaults_to_home(monkeypatch, tmp_path):
    monkeypatch.delenv("XDG_CACHE_HOME")
    monkeypatch.setenv("HOME", str(tmp_path))
    assert network.cache_file() == tmp_path / ".cache" / "pt-p750w" / "host"


def test_nodes():
    assert network.node_for("10.0.0.5") == "tcp://10.0.0.5:9100"
    assert network.parse_node("tcp://10.0.0.5:9100") == ("10.0.0.5", 9100)
    assert network.parse_node("/dev/usb/lp0") is None
    assert network.normalise("/dev/usb/lp0") == "/dev/usb/lp0"
    assert network.normalise("10.0.0.5") == "tcp://10.0.0.5:9100"
    assert network.normalise("printer.lan:9101") == "tcp://printer.lan:9101"
    assert network.normalise("tcp://10.0.0.5:9100") == "tcp://10.0.0.5:9100"


# --- status.py and printing.py over the network ---------------------------------------------

def test_find_device_prefers_env_override(monkeypatch):
    monkeypatch.setenv(status.DEVICE_ENV, "10.0.0.5")
    monkeypatch.setattr(status, "find_usb", lambda: pytest.fail("looked at USB despite the override"))
    assert status.find_device() == "tcp://10.0.0.5:9100"


def test_find_device_falls_back_to_network(monkeypatch):
    calls = []
    monkeypatch.setattr(status, "find_usb", lambda: None)
    monkeypatch.setattr(network, "find_printer", lambda rescan_after: calls.append(rescan_after) or "10.0.0.5")
    assert status.find_device(rescan_after=10) == "tcp://10.0.0.5:9100"
    assert calls == [10]


def test_find_device_reports_nothing_found(monkeypatch):
    monkeypatch.setattr(status, "find_usb", lambda: None)
    monkeypatch.setattr(network, "find_printer", lambda rescan_after: None)
    with pytest.raises(PrinterError, match="USB .* or the network"):
        status.find_device()


def test_read_status_uses_snmp_for_network_nodes(monkeypatch):
    replies = {"10.0.0.5": GOOD_STATUS}
    monkeypatch.setattr(network, "read_status", lambda host, timeout: replies.get(host))
    monkeypatch.setattr(status, "open_printer", lambda node: pytest.fail("opened a device for a network node"))
    assert status.read_status("tcp://10.0.0.5:9100") == GOOD_STATUS
    with pytest.raises(PrinterError, match="No status from 10.0.0.9"):
        status.read_status("tcp://10.0.0.9:9100")


def test_status_main_accepts_a_host(monkeypatch, capsys):
    monkeypatch.setattr(sys, "argv", ["pt-status", "10.0.0.5"])
    monkeypatch.setattr(status, "read_status", lambda node: GOOD_STATUS)
    status.main()
    assert "tcp://10.0.0.5:9100" in capsys.readouterr().out


def test_send_writes_the_job_to_the_raw_port(capsys):
    with socket.create_server(("127.0.0.1", 0)) as server:
        port = server.getsockname()[1]
        received = bytearray()

        def accept():
            conn, _ = server.accept()
            with conn:
                while chunk := conn.recv(65536):
                    received.extend(chunk)

        thread = threading.Thread(target=accept)
        thread.start()
        printing.send(f"tcp://127.0.0.1:{port}", b"job" * 50_000, pages=2)
        thread.join(2)
    assert received == b"job" * 50_000
    assert "sent 2 pages to 127.0.0.1" in capsys.readouterr().out


def test_send_reports_unreachable_printer():
    with socket.create_server(("127.0.0.1", 0)) as server:
        port = server.getsockname()[1]
    with pytest.raises(PrinterError, match=f"Cannot send to 127.0.0.1:{port}"):
        printing.send(f"tcp://127.0.0.1:{port}", b"job", timeout=1)
