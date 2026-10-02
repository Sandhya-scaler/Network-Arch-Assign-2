# Annotated hexdump of one real exchange

Produced by running the real server and client:

```
$ ./observe ./www 9000 &
$ ./bcurll -v localhost:9000/index.html      # -v dumps every frame to stderr
```
`www/index.html` is the 40 bytes `<html><body>Hello, BHTTP!</body></html>\n` (mtime pinned to 2026-01-01 00:00:00 UTC). Offsets are from the start of each frame; header = bytes 0–8; field names refer to SPEC.md §1–§2. The client was a single TCP connection; one request, three response frames, then the client closed.

## 1. Client → server: REQUEST (79 bytes)

```
00000000  01 01 01 00 01 00 00 00 46 01 00 03 47 45 54 02  |........F...GET.|
00000010  00 0b 2f 69 6e 64 65 78 2e 68 74 6d 6c 06 00 0e  |../index.html...|
00000020  6c 6f 63 61 6c 68 6f 73 74 3a 39 30 30 30 07 00  |localhost:9000..|
00000030  07 62 63 75 72 6c 2f 31 08 00 03 2a 2f 2a 00 08  |.bcurl/1...*/*..|
00000040  78 2d 63 6c 69 65 6e 74 00 05 62 63 75 72 6c     |x-client..bcurl|
```

| Bytes | Hex | Meaning |
|---|---|---|
| 0 | `01` | Version = 1 |
| 1 | `01` | Type = REQUEST |
| 2 | `01` | Flags = END_STREAM |
| 3–4 | `00 01` | Stream ID = 1 |
| 5–8 | `00 00 00 46` | Length = 70 payload bytes (79 − 9) |
| 9–14 | `01 00 03 47 45 54` | idx 1 `:method`, value-len 3, "GET" |
| 15–28 | `02 00 0b` + `/index.html` | idx 2 `:path`, value-len 11 |
| 29–45 | `06 00 0e` + `localhost:9000` | idx 6 `host`, value-len 14 |
| 46–55 | `07 00 07` + `bcurl/1` | idx 7 `user-agent`, value-len 7 |
| 56–61 | `08 00 03 2a 2f 2a` | idx 8 `accept`, value-len 3, "\*/\*" |
| 62–78 | `00 08` + `x-client` + `00 05` + `bcurl` | **literal** name: idx 0, name-len 8, name, value-len 5, value |

## 2. Server → client: RESPONSE (75 bytes)

```
00000000  01 02 00 00 01 00 00 00 42 03 00 03 32 30 30 04  |........B...200.|
00000010  00 09 74 65 78 74 2f 68 74 6d 6c 05 00 02 34 30  |..text/html...40|
00000020  09 00 08 62 73 65 72 76 65 2f 31 0a 00 1d 54 68  |...bserve/1...Th|
00000030  75 2c 20 30 31 20 4a 61 6e 20 32 30 32 36 20 30  |u, 01 Jan 2026 0|
00000040  30 3a 30 30 3a 30 30 20 47 4d 54                 |0:00:00 GMT|
```

| Bytes | Hex | Meaning |
|---|---|---|
| 0–4 | `01 02 00 00 01` | Version 1, Type RESPONSE, Flags 0, Stream 1 |
| 5–8 | `00 00 00 42` | Length = 66 |
| 9–14 | `03 00 03 32 30 30` | idx 3 `:status`, "200" |
| 15–26 | `04 00 09` + `text/html` | idx 4 `content-type` |
| 27–31 | `05 00 02 34 30` | idx 5 `content-length`, "40" |
| 32–42 | `09 00 08` + `bserve/1` | idx 9 `server` |
| 43–74 | `0a 00 1d` + 29-byte date | idx 10 `last-modified`, value-len 29, "Thu, 01 Jan 2026 00:00:00 GMT" |

## 3. Server → client: DATA (49 bytes, final)

```
00000000  01 03 01 00 01 00 00 00 28 3c 68 74 6d 6c 3e 3c  |........(<html><|
00000010  62 6f 64 79 3e 48 65 6c 6c 6f 2c 20 42 48 54 54  |body>Hello, BHTT|
00000020  50 21 3c 2f 62 6f 64 79 3e 3c 2f 68 74 6d 6c 3e  |P!</body></html>|
00000030  0a                                               |.|
```

| Bytes | Hex | Meaning |
|---|---|---|
| 0–4 | `01 03 01 00 01` | Version 1, Type DATA, **Flags = END_STREAM**, Stream 1 |
| 5–8 | `00 00 00 28` | Length = 40 |
| 9–48 | `3c 68 74 …  3e 0a` | the file body (`content-length` 40 matches) |
