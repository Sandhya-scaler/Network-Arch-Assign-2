# BHTTP v1 — a binary request/response protocol over TCP

BHTTP carries "GET a file" over **one persistent TCP connection** as a sequence of binary **frames**. All multi-byte integers are **big-endian** (network order). Key words MUST/MAY are used as in RFC 2119.

## 1. Frame layout (fixed 9-byte header + payload)

| Offset | Size | Field | Meaning |
|---|---|---|---|
| 0 | 1 | Version | MUST be `0x01`. Anything else: framing cannot be trusted (see §6). |
| 1 | 1 | Type | `0x01` REQUEST, `0x02` RESPONSE, `0x03` DATA. All others unknown (§5). |
| 2 | 1 | Flags | bit0 `0x01` = END_STREAM. Other bits reserved: send 0, ignore on receipt. |
| 3 | 2 | Stream ID | Names the request/response pair. Client picks odd IDs `1,3,5…`, non-zero, never reused on a connection. `0` is reserved for connection-level errors sent by the server. |
| 5 | 4 | Length | Number of payload bytes that follow (header excluded). |
| 9 | Length | Payload | Type-specific. |

*Why these widths.* **Version (8):** a cheap, explicit escape hatch; a v2 changes it and v1 peers fail loudly instead of misparsing. **Type (8) / Flags (8):** 256 types and 8 flags, only 3+1 used — room to grow. **Stream ID (16):** lets responses be matched to requests today and multiplexing be added later; 65 k requests per connection is ample (HTTP/2 uses 31 bits but this protocol has no priorities/server push). **Length (32):** a 24-bit length (HTTP/2) caps frames at 16 MiB; 32 bits costs one byte and removes the question — receivers still cap at 16 MiB (§6) and files are split into DATA frames. Every field is byte-aligned so no bit shifting is needed. The header is fixed-size so a receiver can always read exactly 9 bytes, then exactly Length more, without parsing the payload — this is what makes skipping unknown frames possible.

## 2. Header block (payload of REQUEST and RESPONSE)

A header block is a concatenation of entries until the payload ends. Each entry:

```
 indexed name :  [idx u8 = 1..10] [value-len u16] [value bytes]
 literal name :  [0x00]           [name-len u8 (1..255)] [name bytes] [value-len u16] [value bytes]
```

Static name table (index → name): **1** `:method`, **2** `:path`, **3** `:status`, **4** `content-type`, **5** `content-length`, **6** `host`, **7** `user-agent`, **8** `accept`, **9** `server`, **10** `last-modified`. Indices 11–255 are invalid (malformed). Names are lower-case ASCII/UTF-8; values are UTF-8 text (numbers as decimal ASCII). A sender SHOULD use the index when the name is in the table; a receiver MUST accept either form. Unknown literal names MUST be ignored by the receiver. Duplicates: last wins.

## 3. Requests

The client sends one **REQUEST** frame (Type 1, Stream ID odd, flags SHOULD have END_STREAM) whose header block MUST contain `:method` = `GET` and `:path` (starts with `/`, bytes used literally, no percent-decoding, no query handling). Other headers (`host`, `user-agent`, `accept`, custom literals) are optional. Requests carry no body; any DATA frame a server receives is skipped. A client MAY send the next request before the previous response finishes, and servers answer in arrival order, but v1 clients SHOULD wait.

## 4. Responses

For a REQUEST on stream *S* the server sends, all with Stream ID *S*: one **RESPONSE** frame (Type 2; header block with `:status` = 3-digit decimal, plus `content-type`, `content-length` and `server` on every response, and `last-modified` on 200), then **one or more DATA** frames (Type 3) carrying the body in order, the last having END_STREAM. An empty body is exactly one zero-length DATA frame with END_STREAM. The server splits bodies into DATA frames ≤ 65 536 bytes. `content-length` equals the sum of DATA payload lengths; a client SHOULD verify it.

Status codes: **200** file sent; **404** no such regular file under the root (also if the resolved path escapes the root via symlink); **400** malformed (§6). Error responses have a short `text/plain` body. The server resolves `:path` under its root; a directory path serves `index.html` in it (`/` → `/index.html`).

## 5. Unknown frame types — MUST skip

A receiver that reads a frame whose Type it does not understand (or one that is not meaningful at that point, e.g. DATA sent to a server) MUST read and discard exactly Length payload bytes and continue, without error, without closing, and without replying. This applies to both server and client, at any point (including between a RESPONSE and its DATA frames). Unknown flag bits are ignored. This is the mechanism by which a version 2 may add frame types without breaking v1 peers that share Version `0x01`.

## 6. Malformed input and limits

* **Frame-level errors** — Version ≠ 1, or Length > 16 777 216 (2^24), or REQUEST Length > 65 536: the receiver cannot resynchronise. The server MUST send one 400 response (RESPONSE+DATA; Stream ID 0 for a Version/Length error, the REQUEST's own stream for an oversize REQUEST) and then close the connection. A client that meets such a frame aborts the connection.
* **Payload-level errors** — REQUEST whose header block is invalid (bad index, truncated entry, invalid UTF-8), missing `:method`/`:path`, method ≠ GET, Stream ID 0, path not starting with `/`, containing a `..` segment, NUL or `\`: the framing is intact, so the server answers **400 on that stream and keeps the connection open**.
* **Truncation** — EOF in the middle of a frame: the receiver drops the connection silently. EOF between frames is a normal close.
* TCP delivers a byte stream: a frame may arrive in many reads, and one read may contain many frames. Implementations MUST read exactly 9 bytes, then exactly Length bytes (loop until complete) and MUST loop on partial writes.

## 7. Connection behaviour

The server keeps the connection open after every response (error or not) until the client closes it or a frame-level error occurs. A client uses a single connection for all its requests. There is no idle-timeout requirement; servers MAY close idle connections after ≥ 5 minutes.

## 8. Complete example (bytes are exactly those produced by the reference implementation; annotated in HEXDUMP.md)

Request `GET /index.html` (79 bytes): header `01 01 01 0001 00000046` then block `01 0003 "GET"`, `02 000b "/index.html"`, `06 000e "localhost:9000"`, `07 0007 "bcurl/1"`, `08 0003 "*/*"`, and the literal `00 08 "x-client" 0005 "bcurl"`.
Response: RESPONSE `01 02 00 0001 00000042` + block (`03 0003 "200"`, `04 0009 "text/html"`, `05 0002 "40"`, `09 0008 "bserve/1"`, `0a 001d "Thu, 01 Jan 2026 00:00:00 GMT"`), then DATA `01 03 01 0001 00000028` + the 40 body bytes.
