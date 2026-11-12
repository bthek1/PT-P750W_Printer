"""Find and talk to a PT-P750W over the network (Wi-Fi or Wi-Fi Direct).

The raster spec (5.6) treats TCP 9100 as one-way: print data goes in, nothing
comes back. So identity and status come from SNMP (UDP 161), and discovery
only ever sends read-only SNMP GETs, never print data to an unknown host.

Stdlib only. Nothing here raises PrinterError, so status.py can import it.
"""
import fcntl
import os
import random
import socket
import struct
import time
from pathlib import Path

MODEL = b"PT-P750W"
PRINT_PORT = 9100
SNMP_PORT = 161
COMMUNITY = b"public"
SCHEME = "tcp://"
# Standard MIB-II / Host Resources ids, plus Brother's IEEE 1284 device id string.
IDENTITY_OIDS = (
    "1.3.6.1.4.1.2435.2.3.9.1.1.7.0",  # Brother 1284 id: "MFG:Brother;...;MDL:PT-P750W;..."
    "1.3.6.1.2.1.25.3.2.1.3.1",  # hrDeviceDescr
    "1.3.6.1.2.1.1.1.0",  # sysDescr
)
# Brother private OID that returns the same 32-byte block as ESC i S over USB.
# Not in the raster spec; not yet confirmed on a PT-P750W.
STATUS_OID = "1.3.6.1.4.1.2435.3.3.9.1.6.1.0"
DISCOVERY_TIMEOUT = 1.0
CHECK_TIMEOUT = 0.5
SIOCGIFBRDADDR = 0x8919
_last_scan = float("-inf")


def cache_file() -> Path:
    base = os.environ.get("XDG_CACHE_HOME") or Path.home() / ".cache"
    return Path(base) / "pt-p750w" / "host"


# --- SNMP v1 GET, just enough BER for it ------------------------------------

def tlv(tag: int, value: bytes) -> bytes:
    n = len(value)
    length = bytes([n]) if n < 0x80 else bytes([0x80 | ((n.bit_length() + 7) // 8)]) + n.to_bytes((n.bit_length() + 7) // 8, "big")
    return bytes([tag]) + length + value


def ber_int(value: int) -> bytes:
    return tlv(0x02, value.to_bytes(max(1, (value.bit_length() + 8) // 8), "big", signed=True))


def ber_oid(oid: str) -> bytes:
    arcs = [int(a) for a in oid.split(".")]
    out = bytearray([40 * arcs[0] + arcs[1]])
    for arc in arcs[2:]:
        chunk = [arc & 0x7F]
        while arc := arc >> 7:
            chunk.append(0x80 | (arc & 0x7F))
        out += bytes(reversed(chunk))
    return tlv(0x06, bytes(out))


def get_request(request_id: int, oid: str, community: bytes = COMMUNITY) -> bytes:
    varbind = tlv(0x30, ber_oid(oid) + b"\x05\x00")
    pdu = tlv(0xA0, ber_int(request_id) + ber_int(0) + ber_int(0) + tlv(0x30, varbind))
    return tlv(0x30, ber_int(0) + tlv(0x04, community) + pdu)


def read_tlv(data: bytes, pos: int) -> tuple[int, bytes, int]:
    """(tag, value, position after it). Raises ValueError on truncated data."""
    if pos + 2 > len(data):
        raise ValueError("truncated")
    tag, n = data[pos], data[pos + 1]
    pos += 2
    if n & 0x80:
        size = n & 0x7F
        n = int.from_bytes(data[pos:pos + size], "big")
        pos += size
    if pos + n > len(data):
        raise ValueError("truncated")
    return tag, data[pos:pos + n], pos + n


def parse_response(data: bytes) -> tuple[int, bytes | None]:
    """(request id, octet-string value or None on an SNMP error). Raises ValueError if malformed."""
    tag, message, _ = read_tlv(data, 0)
    _, _, pos = read_tlv(message, 0)  # version
    _, _, pos = read_tlv(message, pos)  # community
    tag_pdu, pdu, _ = read_tlv(message, pos)
    if tag != 0x30 or tag_pdu != 0xA2:
        raise ValueError("not a GetResponse")
    _, request_id, pos = read_tlv(pdu, 0)
    _, error, pos = read_tlv(pdu, pos)
    _, _, pos = read_tlv(pdu, pos)  # error index
    _, varbinds, _ = read_tlv(pdu, pos)
    _, varbind, _ = read_tlv(varbinds, 0)
    _, _, pos = read_tlv(varbind, 0)  # oid
    value_tag, value, _ = read_tlv(varbind, pos)
    ok = int.from_bytes(error, "big") == 0 and value_tag == 0x04
    return int.from_bytes(request_id, "big", signed=True), value if ok else None


def snmp_get(targets: list[str], oids: tuple[str, ...], timeout: float,
             broadcast: bool = False) -> dict[str, dict[str, bytes]]:
    """GET each oid from each target and collect {host: {oid: value}}.

    Unicast returns once every request is answered; broadcast listens until the timeout.
    SNMPv1 fails a whole request if one oid is missing, so each oid goes in its own request.
    """
    replies: dict[str, dict[str, bytes]] = {}
    with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as sock:
        sock.setsockopt(socket.SOL_SOCKET, socket.SO_BROADCAST, 1)
        base = random.randrange(1, 1 << 30)
        for target in targets:
            for i, oid in enumerate(oids):
                try:
                    sock.sendto(get_request(base + i, oid), (target, SNMP_PORT))
                except OSError:
                    pass  # e.g. no route on an interface that just went down
        deadline = time.monotonic() + timeout
        pending = {(target, i) for target in targets for i in range(len(oids))}
        while (broadcast or pending) and (remaining := deadline - time.monotonic()) > 0:
            sock.settimeout(remaining)
            try:
                data, (host, _) = sock.recvfrom(4096)
                request_id, value = parse_response(data)
            except (TimeoutError, ValueError):
                continue
            if not 0 <= request_id - base < len(oids):
                continue
            pending.discard((host, request_id - base))
            if value is not None:
                replies.setdefault(host, {})[oids[request_id - base]] = value
    return replies


# --- discovery ----------------------------------------------------------------

def broadcast_addresses() -> list[str]:
    """IPv4 broadcast address of every interface except loopback, then 255.255.255.255."""
    found = []
    with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as sock:
        for _, name in socket.if_nameindex():
            if name == "lo":
                continue
            try:
                raw = fcntl.ioctl(sock.fileno(), SIOCGIFBRDADDR, struct.pack("256s", name.encode()[:15]))
            except OSError:
                continue  # no IPv4 address on this interface
            address = socket.inet_ntoa(raw[20:24])
            if address != "0.0.0.0" and address not in found:
                found.append(address)
    return found + ["255.255.255.255"]


def is_model(values: dict[str, bytes]) -> bool:
    return any(MODEL in value for value in values.values())


def discover(timeout: float | None = None) -> list[str]:
    """Addresses of every PT-P750W that answers an SNMP broadcast on a local network."""
    replies = snmp_get(broadcast_addresses(), IDENTITY_OIDS, timeout or DISCOVERY_TIMEOUT, broadcast=True)
    return sorted(host for host, values in replies.items() if is_model(values))


def is_printer(host: str, timeout: float | None = None) -> bool:
    return is_model(snmp_get([host], IDENTITY_OIDS, timeout or CHECK_TIMEOUT).get(host, {}))


def remembered() -> str | None:
    try:
        return cache_file().read_text().strip() or None
    except OSError:
        return None


def remember(host: str) -> None:
    try:
        cache_file().parent.mkdir(parents=True, exist_ok=True)
        cache_file().write_text(host + "\n")
    except OSError:
        pass  # a read-only home only costs a rescan next time


def find_printer(rescan_after: float = 0.0) -> str | None:
    """The last known printer if it still answers, else a fresh broadcast scan.

    rescan_after limits how often a scan runs when nothing is found, so a
    status poll every 2 s does not broadcast every time.
    """
    global _last_scan
    cached = remembered()
    if cached and is_printer(cached):
        return cached
    if time.monotonic() - _last_scan < rescan_after:
        return None
    _last_scan = time.monotonic()
    hosts = discover()
    if hosts:
        remember(hosts[0])
        return hosts[0]
    return None


# --- device nodes ---------------------------------------------------------------

def node_for(host: str, port: int = PRINT_PORT) -> str:
    return f"{SCHEME}{host}:{port}"


def parse_node(node: str) -> tuple[str, int] | None:
    """(host, port) for a network node, None for a device path."""
    if not node.startswith(SCHEME):
        return None
    host, _, port = node[len(SCHEME):].rpartition(":")
    return host, int(port)


def normalise(device: str) -> str:
    """A user-given device: a path stays a path; 'host', 'host:port' or 'tcp://host:port' become a node."""
    if device.startswith(("/", SCHEME)):
        return device
    host, _, port = device.partition(":")
    return node_for(host, int(port) if port else PRINT_PORT)


def read_status(host: str, timeout: float = 2.0) -> bytes | None:
    return snmp_get([host], (STATUS_OID,), timeout).get(host, {}).get(STATUS_OID)


def send_job(host: str, port: int, job: bytes, timeout: float = 15) -> None:
    """Write the job to the raw port. Raises OSError."""
    with socket.create_connection((host, port), timeout=timeout) as sock:
        sock.sendall(job)
        sock.shutdown(socket.SHUT_WR)
