"""BHTTP v1 -- shared protocol helpers (framing, header blocks, hexdump).

Everything here follows SPEC.md. TCP is a byte stream: all reads go through
read_exact(), all writes through write_all().
"""
import struct

VERSION = 1
HEADER = struct.Struct(">BBBHI")          # version, type, flags, stream id, length
HEADER_LEN = HEADER.size                  # 9
T_REQUEST, T_RESPONSE, T_DATA = 1, 2, 3
F_END_STREAM = 0x01
MAX_PAYLOAD = 1 << 24                     # 16 MiB hard cap on any frame
MAX_HEADER_PAYLOAD = 1 << 16              # cap for REQUEST/RESPONSE frames
DATA_CHUNK = 1 << 16

STATIC_NAMES = [None, ":method", ":path", ":status", "content-type",
                "content-length", "host", "user-agent", "accept", "server",
                "last-modified"]
STATIC_INDEX = {n: i for i, n in enumerate(STATIC_NAMES) if n}


class ProtocolError(Exception):
    """Peer sent something the spec forbids (reply 400 where possible)."""


class Truncated(Exception):
    """Connection ended in the middle of a frame."""


def read_exact(sock, n):
    """Read exactly n bytes. Returns b'' only if EOF came before any byte and n>0.
    Raises Truncated if EOF arrives after at least one byte."""
    buf = bytearray()
    while len(buf) < n:
        chunk = sock.recv(n - len(buf))
        if not chunk:
            if not buf:
                return b""
            raise Truncated("got %d of %d bytes" % (len(buf), n))
        buf += chunk
    return bytes(buf)


def write_all(sock, data):
    """Write all of data, coping with partial sends."""
    view = memoryview(data)
    while len(view):
        sent = sock.send(view)
        if sent <= 0:
            raise ConnectionError("send returned %r" % sent)
        view = view[sent:]


def skip_exact(sock, n):
    while n > 0:
        chunk = sock.recv(min(n, 65536))
        if not chunk:
            raise Truncated("EOF while skipping frame")
        n -= len(chunk)


def pack_frame(ftype, flags, stream, payload=b""):
    return HEADER.pack(VERSION, ftype, flags, stream, len(payload)) + payload


def read_header(sock):
    """Return (version, type, flags, stream, length) or None on clean EOF.
    Raises ProtocolError for bad version / length (payload is NOT consumed)."""
    raw = read_exact(sock, HEADER_LEN)
    if not raw:
        return None
    ver, ftype, flags, stream, length = HEADER.unpack(raw)
    if ver != VERSION:
        raise ProtocolError("unsupported version %d" % ver)
    if length > MAX_PAYLOAD:
        raise ProtocolError("frame length %d exceeds maximum" % length)
    return ver, ftype, flags, stream, length


def encode_headers(pairs):
    out = bytearray()
    for name, value in pairs:
        name = name.lower()
        value = value.encode() if isinstance(value, str) else value
        if len(value) > 0xFFFF:
            raise ValueError("header value too long")
        idx = STATIC_INDEX.get(name, 0)
        out.append(idx)
        if idx == 0:
            nb = name.encode()
            if not 1 <= len(nb) <= 255:
                raise ValueError("bad header name length")
            out.append(len(nb))
            out += nb
        out += struct.pack(">H", len(value)) + value
    return bytes(out)


def decode_headers(block):
    """Return list of (name, value) str pairs. Raises ProtocolError."""
    pairs, i, n = [], 0, len(block)
    try:
        while i < n:
            idx = block[i]; i += 1
            if idx == 0:
                nl = block[i]; i += 1
                if nl == 0 or i + nl > n:
                    raise ProtocolError("bad literal name")
                name = block[i:i + nl].decode("utf-8"); i += nl
            elif idx < len(STATIC_NAMES):
                name = STATIC_NAMES[idx]
            else:
                raise ProtocolError("unknown header index %d" % idx)
            if i + 2 > n:
                raise ProtocolError("truncated header value length")
            (vl,) = struct.unpack_from(">H", block, i); i += 2
            if i + vl > n:
                raise ProtocolError("truncated header value")
            pairs.append((name, block[i:i + vl].decode("utf-8"))); i += vl
    except (IndexError, UnicodeDecodeError) as e:
        raise ProtocolError("malformed header block: %s" % e)
    return pairs


def hexdump(data, indent=""):
    lines = []
    for off in range(0, len(data), 16):
        row = data[off:off + 16]
        hx = " ".join("%02x" % b for b in row).ljust(47)
        asc = "".join(chr(b) if 32 <= b < 127 else "." for b in row)
        lines.append("%s%08x  %s  |%s|" % (indent, off, hx, asc))
    return "\n".join(lines)


def dump_frame(label, frame_bytes, payload_limit=256):
    """Hexdump header + (possibly truncated) payload of one complete frame."""
    ver, ftype, flags, stream, length = HEADER.unpack(frame_bytes[:HEADER_LEN])
    names = {1: "REQUEST", 2: "RESPONSE", 3: "DATA"}
    out = "%s frame type=%s(0x%02x) flags=0x%02x stream=%d length=%d\n" % (
        label, names.get(ftype, "UNKNOWN"), ftype, flags, stream, length)
    shown = frame_bytes[:HEADER_LEN + payload_limit]
    out += hexdump(shown, "  ")
    if len(frame_bytes) > len(shown):
        out += "\n  ... (%d more payload bytes not shown)" % (len(frame_bytes) - len(shown))
    return out
