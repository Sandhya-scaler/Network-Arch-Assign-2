#!/usr/bin/env python3
"""Independent BHTTP v1 client written from SPEC.md only.
Deliberately imports NOTHING from bhttp.py: only socket and struct.
Usage: interop_raw.py HOST PORT PATH   -> writes body to stdout, prints status to stderr.
Can also be imported: raw_get(sock, path, stream) / frame(...)."""
import socket, struct, sys

STATIC = {":method": 1, ":path": 2, ":status": 3, "content-type": 4, "content-length": 5,
          "host": 6, "user-agent": 7, "accept": 8, "server": 9, "last-modified": 10}
NAMES = {v: k for k, v in STATIC.items()}


def frame(ftype, flags, stream, payload=b"", version=1):
    # offsets: 0 version, 1 type, 2 flags, 3..4 stream id, 5..8 length (all big-endian)
    return bytes([version, ftype, flags]) + stream.to_bytes(2, "big") + len(payload).to_bytes(4, "big") + payload


def hdr_entry(name, value):
    v = value.encode()
    if name in STATIC:
        return bytes([STATIC[name]]) + len(v).to_bytes(2, "big") + v
    n = name.encode()
    return b"\x00" + bytes([len(n)]) + n + len(v).to_bytes(2, "big") + v


def recv_n(sock, n):
    b = b""
    while len(b) < n:
        c = sock.recv(n - len(b))
        if not c:
            raise EOFError("closed after %d/%d bytes" % (len(b), n))
        b += c
    return b


def read_frame(sock):
    h = recv_n(sock, 9)
    return h[0], h[1], h[2], int.from_bytes(h[3:5], "big"), recv_n(sock, int.from_bytes(h[5:9], "big"))


def parse_block(p):
    out, i = {}, 0
    while i < len(p):
        idx = p[i]; i += 1
        if idx == 0:
            nl = p[i]; i += 1
            name = p[i:i + nl].decode(); i += nl
        else:
            name = NAMES[idx]
        vl = int.from_bytes(p[i:i + 2], "big"); i += 2
        out[name] = p[i:i + vl].decode(); i += vl
    return out


def raw_get(sock, path, stream=1, send=None):
    """Send REQUEST (via send() callback to allow dribbling) and collect response."""
    req = frame(1, 0x01, stream, hdr_entry(":method", "GET") + hdr_entry(":path", path))
    (send or sock.sendall)(req)
    return collect(sock, stream)


def collect(sock, stream):
    status, body, hdrs = None, b"", {}
    while True:
        ver, t, fl, sid, payload = read_frame(sock)
        assert ver == 1
        if t == 2:
            hdrs = parse_block(payload); status = int(hdrs[":status"])
        elif t == 3:
            body += payload
            if fl & 1:
                return status, hdrs, body
        # unknown types ignored


if __name__ == "__main__":
    host, port, path = sys.argv[1], int(sys.argv[2]), sys.argv[3]
    with socket.create_connection((host, port)) as s:
        st, h, b = raw_get(s, path)
    print("status", st, file=sys.stderr)
    sys.stdout.buffer.write(b)
