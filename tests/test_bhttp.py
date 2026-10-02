import os, socket, subprocess, sys, time, unittest, hashlib
HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, ROOT); sys.path.insert(0, HERE)
import bhttp as B
import interop_raw as R

WWW = os.path.join(ROOT, "www")
BCURL = os.path.join(ROOT, "bcurl")


def free_port():
    s = socket.socket(); s.bind(("", 0)); p = s.getsockname()[1]; s.close(); return p


class FakeSock:
    """recv returns at most `step` bytes; send accepts at most `step` bytes."""
    def __init__(self, data=b"", step=1):
        self.data, self.step, self.out = bytearray(data), step, bytearray()
    def recv(self, n):
        k = min(n, self.step, len(self.data)); c = bytes(self.data[:k]); del self.data[:k]; return c
    def send(self, b):
        k = min(len(b), self.step); self.out += bytes(b[:k]); return k


class Base(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.port = free_port()
        # use the 'observe' symlink name the user asked for, to prove aliases work
        cls.srv = subprocess.Popen([os.path.join(ROOT, "observe"), WWW, str(cls.port)],
                                   stderr=subprocess.PIPE)
        for _ in range(100):
            try:
                socket.create_connection(("127.0.0.1", cls.port), timeout=0.2).close(); break
            except OSError:
                time.sleep(0.05)
    @classmethod
    def tearDownClass(cls):
        cls.srv.terminate(); cls.srv.wait(); cls.srv.stderr.close()

    def conn(self):
        s = socket.create_connection(("127.0.0.1", self.port), timeout=10); self.addCleanup(s.close); return s

    def bcurl(self, *urls, v=False, exe="bcurll"):
        cmd = [os.path.join(ROOT, exe)] + (["-v"] if v else []) + ["localhost:%d%s" % (self.port, u) for u in urls]
        return subprocess.run(cmd, capture_output=True)

    def read(self, name):
        with open(os.path.join(WWW, name), "rb") as f:
            return f.read()

    def assert_closed(self, s):
        s.settimeout(5)
        while s.recv(65536):
            pass


class Unit(unittest.TestCase):
    def test_header_roundtrip_static_and_literal(self):
        pairs = [(":method", "GET"), ("x-custom", "val"), ("host", "h:1"), ("y", "")]
        blk = B.encode_headers(pairs)
        self.assertEqual(B.decode_headers(blk), pairs)
        self.assertEqual(blk[0], 1)             # indexed
        self.assertEqual(blk[6:8], b"\x00\x08") # literal: index 0, name length 8

    def test_ten_names(self):
        self.assertEqual(len(B.STATIC_NAMES) - 1, 10)

    def test_bad_blocks(self):
        for bad in (b"\x0b\x00\x00", b"\x00\x00", b"\x01\x00", b"\x01\x00\x05ab", b"\x00\x02a"):
            with self.assertRaises(B.ProtocolError): B.decode_headers(bad)

    def test_read_exact_partial_reads(self):
        data = bytes(range(200))
        self.assertEqual(B.read_exact(FakeSock(data, 1), 200), data)
        self.assertEqual(B.read_exact(FakeSock(b"", 1), 5), b"")
        with self.assertRaises(B.Truncated): B.read_exact(FakeSock(b"abc", 2), 5)

    def test_write_all_partial_writes(self):
        s = FakeSock(step=3); B.write_all(s, b"x" * 100); self.assertEqual(bytes(s.out), b"x" * 100)

    def test_header_parse_partial_and_multiple(self):
        f1 = B.pack_frame(3, 0, 1, b"hello"); f2 = B.pack_frame(3, 1, 1, b"")
        s = FakeSock(f1 + f2, 2)
        h = B.read_header(s); self.assertEqual(h[1:], (3, 0, 1, 5)); self.assertEqual(B.read_exact(s, 5), b"hello")
        h = B.read_header(s); self.assertEqual(h[4], 0); self.assertIsNone(B.read_header(s))

    def test_bad_version_and_length(self):
        with self.assertRaises(B.ProtocolError): B.read_header(FakeSock(bytes([9, 1, 0, 0, 1, 0, 0, 0, 0])))
        with self.assertRaises(B.ProtocolError): B.read_header(FakeSock(bytes([1, 1, 0, 0, 1, 0xff, 0xff, 0xff, 0xff])))


class Server(Base):
    def test_200_text(self):
        r = self.bcurl("/index.html"); self.assertEqual(r.returncode, 0); self.assertEqual(r.stdout, self.read("index.html"))

    def test_root_and_subdir(self):
        self.assertEqual(self.bcurl("/").stdout, self.read("index.html"))
        self.assertEqual(self.bcurl("/sub/page.txt").stdout, b"nested file\n")

    def test_404(self):
        r = self.bcurl("/nope.html"); self.assertEqual(r.returncode, 1); self.assertIn(b"not found", r.stdout)

    def test_binary_empty_large(self):
        for name in ("binary.bin", "empty.txt", "large.bin"):
            r = self.bcurl("/" + name, exe="bcurl")
            self.assertEqual(r.returncode, 0, name)
            self.assertEqual(hashlib.sha256(r.stdout).digest(), hashlib.sha256(self.read(name)).digest(), name)

    def test_multiple_requests_same_connection(self):
        s = self.conn()
        for i, name in enumerate(("index.html", "binary.bin", "missing", "empty.txt", "index.html")):
            st, h, b = R.raw_get(s, "/" + name, stream=2 * i + 1)
            self.assertEqual(st, 404 if name == "missing" else 200)
            if st == 200: self.assertEqual(b, self.read(name))

    def test_client_one_connection_multi_url(self):
        r = self.bcurl("/index.html", "/binary.bin", "/index.html")
        self.assertEqual(r.stdout, self.read("index.html") + self.read("binary.bin") + self.read("index.html"))
        self.assertEqual(r.returncode, 0)

    def test_unknown_frame_type_skipped(self):
        s = self.conn()
        s.sendall(R.frame(0x7f, 0xff, 5, b"future extension payload") + R.frame(0x00, 0, 0, b""))
        st, h, b = R.raw_get(s, "/index.html", stream=1)
        self.assertEqual((st, b), (200, self.read("index.html")))

    def test_unknown_frame_between_response_frames_client_side(self):
        # fake server that injects an unknown frame mid-response; bcurl must skip it
        ls = socket.socket(); ls.bind(("127.0.0.1", 0)); ls.listen(1); port = ls.getsockname()[1]
        import threading
        def srv():
            c, _ = ls.accept(); B.read_header(c)  # read request
            c.recv(65536)
            c.sendall(R.frame(2, 0, 1, R.hdr_entry(":status", "200")) + R.frame(0x42, 0, 1, b"zzz")
                      + R.frame(3, 0, 1, b"ab") + R.frame(0x99, 0, 0, b"") + R.frame(3, 1, 1, b"cd")); c.close()
        threading.Thread(target=srv, daemon=True).start()
        r = subprocess.run([BCURL, "127.0.0.1:%d/x" % port], capture_output=True)
        self.assertEqual((r.returncode, r.stdout), (0, b"abcd")); ls.close()

    def test_path_traversal(self):
        s = self.conn()
        for p in ("/../IMPLEMENTATION_PLAN.md", "/sub/../../bhttp.py", "/..", "//..//bhttp.py", "no-slash", "/a\\b", "/a\x00b"):
            st, h, b = R.raw_get(s, p, stream=1)
            self.assertIn(st, (400, 404), p); self.assertNotIn(b"BHTTP", b)
        self.assertEqual(R.raw_get(s, "//bhttp.py", 1)[0], 404)       # stays inside root

    def test_symlink_escape(self):
        link = os.path.join(WWW, "escape_link")
        os.symlink(ROOT, link); self.addCleanup(os.unlink, link)
        self.assertEqual(R.raw_get(self.conn(), "/escape_link/bhttp.py", 1)[0], 404)

    def test_400_malformed_header_block_keeps_connection(self):
        s = self.conn()
        s.sendall(R.frame(1, 1, 1, b"\x63garbage"))
        st, h, b = R.collect(s, 1); self.assertEqual(st, 400)
        st, h, b = R.raw_get(s, "/index.html", 3); self.assertEqual(st, 200)

    def test_400_missing_path_bad_method(self):
        s = self.conn()
        s.sendall(R.frame(1, 1, 1, R.hdr_entry(":method", "GET"))); self.assertEqual(R.collect(s, 1)[0], 400)
        s.sendall(R.frame(1, 1, 3, R.hdr_entry(":method", "POST") + R.hdr_entry(":path", "/"))); self.assertEqual(R.collect(s, 3)[0], 400)
        s.sendall(R.frame(1, 1, 0, R.hdr_entry(":method", "GET") + R.hdr_entry(":path", "/"))); self.assertEqual(R.collect(s, 0)[0], 400)

    def test_invalid_version(self):
        s = self.conn(); s.sendall(R.frame(1, 1, 1, b"", version=2))
        self.assertEqual(R.collect(s, 0)[0], 400); self.assert_closed(s)

    def test_invalid_length(self):
        s = self.conn(); s.sendall(bytes([1, 1, 1, 0, 1]) + b"\xff\xff\xff\xff")
        self.assertEqual(R.collect(s, 0)[0], 400); self.assert_closed(s)

    def test_oversize_request_frame(self):
        s = self.conn(); s.sendall(bytes([1, 1, 1, 0, 1]) + (1 << 20).to_bytes(4, "big"))
        self.assertEqual(R.collect(s, 1)[0], 400); self.assert_closed(s)

    def test_truncated_frame_does_not_crash(self):
        s = socket.create_connection(("127.0.0.1", self.port)); s.sendall(R.frame(1, 1, 1, b"x" * 50)[:20]); s.close()
        s = socket.create_connection(("127.0.0.1", self.port)); s.sendall(b"\x01\x01"); s.close()
        time.sleep(0.2); self.assertIsNone(self.srv.poll())
        self.assertEqual(R.raw_get(self.conn(), "/index.html", 1)[0], 200)

    def test_garbage_does_not_crash(self):
        s = self.conn(); s.sendall(b"GET / HTTP/1.1\r\n\r\n")      # text HTTP is not BHTTP
        self.assertEqual(R.collect(s, 0)[0], 400); self.assert_closed(s)
        self.assertIsNone(self.srv.poll())

    def test_partial_tcp_reads_server_side(self):
        s = self.conn(); s.setsockopt(socket.IPPROTO_TCP, socket.TCP_NODELAY, 1)
        def dribble(data):
            for i in range(len(data)): s.sendall(data[i:i + 1]); time.sleep(0.002)
        st, h, b = R.raw_get(s, "/index.html", 1, send=dribble); self.assertEqual((st, b), (200, self.read("index.html")))

    def test_two_frames_in_one_send(self):
        s = self.conn()
        r = lambda p, sid: R.frame(1, 1, sid, R.hdr_entry(":method", "GET") + R.hdr_entry(":path", p))
        s.sendall(r("/index.html", 1) + r("/binary.bin", 3) + r("/zzz", 5))
        self.assertEqual(R.collect(s, 1)[2], self.read("index.html"))
        self.assertEqual(R.collect(s, 3)[2], self.read("binary.bin"))
        self.assertEqual(R.collect(s, 5)[0], 404)

    def test_slow_reader_large_file_partial_writes(self):
        s = self.conn(); s.setsockopt(socket.SOL_SOCKET, socket.SO_RCVBUF, 4096)
        s.sendall(R.frame(1, 1, 1, R.hdr_entry(":method", "GET") + R.hdr_entry(":path", "/large.bin")))
        time.sleep(0.3)                                             # let server's send buffer fill
        st, h, b = R.collect(s, 1)
        self.assertEqual(b, self.read("large.bin")); self.assertEqual(h["content-length"], str(len(b)))

    def test_raw_interop_cli(self):
        r = subprocess.run([sys.executable, os.path.join(HERE, "interop_raw.py"), "127.0.0.1", str(self.port), "/binary.bin"], capture_output=True)
        self.assertEqual(r.stdout, self.read("binary.bin"))

    def test_response_headers(self):
        st, h, b = R.raw_get(self.conn(), "/index.html", 1)
        self.assertEqual(h["content-type"], "text/html"); self.assertEqual(h["content-length"], str(len(b))); self.assertIn("last-modified", h)


class Client(Base):
    def test_exit_codes(self):
        self.assertEqual(self.bcurl("/index.html").returncode, 0)
        self.assertEqual(self.bcurl("/missing").returncode, 1)
        self.assertEqual(self.bcurl("/index.html", "/missing").returncode, 1)
        self.assertEqual(self.bcurl("/../x").returncode, 1)             # 400
        p = subprocess.run([BCURL, "127.0.0.1:%d/x" % free_port()], capture_output=True); self.assertEqual(p.returncode, 2)
        self.assertNotEqual(subprocess.run([BCURL], capture_output=True).returncode, 0)   # usage error

    def test_verbose(self):
        r = self.bcurl("/index.html", v=True)
        self.assertEqual(r.stdout, self.read("index.html"))
        err = r.stderr.decode()
        self.assertIn("> frame type=REQUEST", err); self.assertIn("< frame type=RESPONSE", err)
        self.assertIn("< frame type=DATA", err); self.assertIn("flags=0x01", err)
        self.assertIn("|", err)

    def test_verbose_large_truncates_dump(self):
        r = self.bcurl("/large.bin", v=True); self.assertLess(len(r.stderr), 200000); self.assertIn("more payload bytes not shown", r.stderr.decode())

    def test_exactly_one_connection(self):
        # transparent TCP counting proxy
        import threading
        ls = socket.socket(); ls.bind(("127.0.0.1", 0)); ls.listen(5); pport = ls.getsockname()[1]
        count = []
        def pipe(a, b):
            try:
                while (d := a.recv(65536)): b.sendall(d)
            except OSError: pass
            finally:
                for x in (a, b):
                    try: x.shutdown(socket.SHUT_RDWR)
                    except OSError: pass
        def accept():
            while True:
                try: c, _ = ls.accept()
                except OSError: return
                count.append(1); u = socket.create_connection(("127.0.0.1", self.port))
                threading.Thread(target=pipe, args=(c, u), daemon=True).start(); threading.Thread(target=pipe, args=(u, c), daemon=True).start()
        threading.Thread(target=accept, daemon=True).start()
        r = subprocess.run([BCURL, "127.0.0.1:%d/index.html" % pport, "127.0.0.1:%d/binary.bin" % pport, "127.0.0.1:%d/nope" % pport], capture_output=True)
        ls.close(); self.assertEqual(len(count), 1); self.assertEqual(r.returncode, 1)


if __name__ == "__main__":
    unittest.main(verbosity=2)
