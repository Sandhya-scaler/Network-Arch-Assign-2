# BHTTP — binary HTTP over TCP (Net Arch Assignment 2)

Spec: [SPEC.md](SPEC.md) (authoritative) · Annotated bytes: [HEXDUMP.md](HEXDUMP.md) · Notes: [IMPLEMENTATION_PLAN.md](IMPLEMENTATION_PLAN.md) · Audit: [FINAL_CHECKLIST.md](FINAL_CHECKLIST.md)

Requires Python 3.8+ (stdlib only). No build step.

```
./bserve ./www 9000                      # server  (alias: ./observe)
./bcurl -v localhost:9000/index.html     # client  (alias: ./bcurll)
./bcurl localhost:9000/index.html localhost:9000/binary.bin   # many files, ONE connection
```
The slide names the programs `bserve`/`bcurl`; `observe`/`bcurll` are symlinks to them, so either spelling works.

Client: body → stdout (binary-safe), `-v` frame hexdumps → stderr (payloads truncated after 256 bytes). Exit: 0 ok, 1 any 4xx/5xx, 2 network/protocol/usage error.

Files: `bhttp.py` (framing, header blocks, read_exact/write_all), `bserve`, `bcurl`, `www/` samples, `tests/` (`test_bhttp.py`; `interop_raw.py` is an independent client that implements SPEC.md with only `socket`).

Tests: `python3 -m unittest discover -s tests -v`

Decisions: only GET; paths used literally (no percent-decoding/queries); `..`/NUL/backslash → 400, symlink escape → 404; directory → `index.html`; thread per connection.
