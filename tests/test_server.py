from pathlib import Path
import socket
import tempfile
import threading
import unittest
from unittest import mock

from bhttp import protocol as p
from bhttp import server
from tests import wire_peer as wire


class ServerTests(unittest.TestCase):
    """TCP black-box requests/responses use only the independent wire peer."""

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name, "root")
        self.root.mkdir()
        self.root = self.root.resolve()
        (self.root / "hello.txt").write_bytes(b"hello\n")
        (self.root / "index.html").write_bytes(b"<h1>BHTTP</h1>\n")
        (self.root / "binary.bin").write_bytes(b"\x00\xff\x80\xef\xbb\xbf\r\n")
        (self.root / "folder").mkdir()
        outside = Path(self.temp.name, "root-other")
        outside.mkdir()
        self.outside = outside / "secret.txt"
        self.outside.write_bytes(b"secret must never be served")
        self.listener = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        self.listener.bind(("127.0.0.1", 0))
        self.listener.listen()
        self.listener.settimeout(0.1)
        self.address = self.listener.getsockname()
        self.stop = threading.Event()
        self.failures = []
        self.worker = threading.Thread(target=self.accept_loop, daemon=True)
        self.worker.start()

    def accept_loop(self):
        try:
            while not self.stop.is_set():
                try:
                    connection, _ = self.listener.accept()
                except socket.timeout:
                    continue
                except OSError:
                    if self.stop.is_set():
                        return
                    raise
                with connection:
                    connection.settimeout(3)
                    server.handle_connection(connection, self.root)
        except Exception as exc:
            self.failures.append(exc)

    def tearDown(self):
        self.stop.set()
        self.listener.close()
        self.worker.join(5)
        self.temp.cleanup()
        self.assertFalse(self.worker.is_alive(), "server thread did not stop")
        self.assertEqual(self.failures, [])

    def connect(self):
        connection = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        connection.settimeout(3)
        connection.connect(self.address)
        return connection

    def reply(self, connection, expected_status, expected_id=1, close=False):
        header, payload = wire.read_frame(connection)
        self.assertEqual(header[3], expected_id)
        status, headers, body = wire.parse_response(header, payload)
        self.assertEqual(status, expected_status)
        self.assertEqual(headers["connection"], "close" if close else "keep-alive")
        return headers, body

    def test_independent_approved_vector_and_all_response_headers(self):
        with self.connect() as sock:
            sock.sendall(wire.HELLO_REQUEST)
            headers, body = self.reply(sock, 200)
            self.assertEqual(body, b"hello\n")
            self.assertEqual(set(headers), {"content-type", "content-length", "server",
                                          "connection", "cache-control", "date", "last-modified"})
            self.assertEqual(headers["server"], "bserve/1")
            self.assertEqual(headers["cache-control"], "no-store")
            self.assertRegex(headers["date"], r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}Z$")

    def test_two_successful_requests_on_one_connection(self):
        with self.connect() as sock:
            for path, request_id, expected in [("/index.html", 65535, b"<h1>BHTTP</h1>\n"),
                                                ("/hello.txt", 1, b"hello\n")]:
                sock.sendall(wire.request(path, request_id=request_id))
                headers, body = self.reply(sock, 200, request_id)
                self.assertEqual(body, expected)
                self.assertIn("charset=utf-8", headers["content-type"])

    def test_unknown_then_valid_on_same_connection(self):
        with self.connect() as sock:
            sock.sendall(wire.frame(b"opaque", kind=15, flags=255, request_id=65535))
            sock.sendall(wire.frame(kind=0, flags=255, request_id=0))
            sock.sendall(wire.HELLO_REQUEST)
            self.assertEqual(self.reply(sock, 200)[1], b"hello\n")

    def test_large_unknown_bounded_skip_then_valid(self):
        with self.connect() as sock:
            sock.sendall(wire.frame(b"x" * (2 * 65536 + 1), kind=3, flags=254))
            sock.sendall(wire.HELLO_REQUEST)
            self.reply(sock, 200)

    def test_binary_file_and_empty_file(self):
        (self.root / "empty.bin").write_bytes(b"")
        with self.connect() as sock:
            for path, expected in [("/binary.bin", b"\x00\xff\x80\xef\xbb\xbf\r\n"), ("/empty.bin", b"")]:
                sock.sendall(wire.request(path))
                headers, body = self.reply(sock, 200)
                self.assertEqual(body, expected)
                self.assertEqual(headers["content-type"], "application/octet-stream")

    def test_missing_and_directories_keep_connection(self):
        with self.connect() as sock:
            for path in ("/missing", "/", "/folder", "/folder/", "/hello.txt/"):
                sock.sendall(wire.request(path))
                self.reply(sock, 404)
            sock.sendall(wire.HELLO_REQUEST)
            self.reply(sock, 200)

    def test_malformed_consumed_requests_recover(self):
        bad = [wire.request(method=7), wire.request(flags=1), wire.request(path=b"/\xff"),
               wire.frame(wire.HELLO_REQUEST[8:] + b"extra"),
               wire.request(entries=[(11, "bad selector")]),
               wire.request(entries=[(3, "a"), ("host", "b")]),
               wire.frame(b""), wire.request(entries=[(7, "close")])]
        with self.connect() as sock:
            for raw in bad:
                sock.sendall(raw)
                self.reply(sock, 400)
                sock.sendall(wire.HELLO_REQUEST)
                self.reply(sock, 200)

    def test_traversal_and_unsafe_paths_recover(self):
        paths = ["/../root-other/secret.txt", "/%2e%2e/root-other/secret.txt",
                 "/%2E%2E/secret.txt", "/./hello.txt", "/a//b", "/a\\b", "/C:/x",
                 "/a%", "/a%zz", "/%ff", "/a%00", "/a?query", "/a#fragment",
                 "/NUL.txt", "/CON", "/LPT¹.txt", "/a.", "/a%20"]
        with self.connect() as sock:
            for path in paths:
                with self.subTest(path=path):
                    sock.sendall(wire.request(path))
                    self.reply(sock, 400)
            sock.sendall(wire.HELLO_REQUEST)
            self.reply(sock, 200)

    def test_double_encoded_path_is_literal_and_plus_is_preserved(self):
        directory = self.root / "%2e%2e"
        directory.mkdir()
        (directory / "safe.txt").write_bytes(b"literal, not traversal")
        (self.root / "a+b.txt").write_bytes(b"plus")
        with self.connect() as sock:
            sock.sendall(wire.request("/%252e%252e/safe.txt"))
            self.assertEqual(self.reply(sock, 200)[1], b"literal, not traversal")
            sock.sendall(wire.request("/a+b.txt"))
            self.assertEqual(self.reply(sock, 200)[1], b"plus")

    def test_outside_symlink_and_inside_symlink(self):
        try:
            (self.root / "escape.txt").symlink_to(self.outside)
            (self.root / "inside.txt").symlink_to(self.root / "hello.txt")
        except OSError as exc:
            self.skipTest(f"platform cannot create symlinks: {exc}")
        with self.connect() as sock:
            sock.sendall(wire.request("/escape.txt"))
            self.reply(sock, 400)
            sock.sendall(wire.request("/inside.txt"))
            self.assertEqual(self.reply(sock, 200)[1], b"hello\n")

    def test_path_aware_containment_rejects_common_prefix(self):
        with self.connect() as sock, mock.patch.object(Path, "resolve", return_value=self.outside):
            sock.sendall(wire.HELLO_REQUEST)
            self.reply(sock, 400)

    def test_oversized_file_500_and_connection_survives(self):
        with (self.root / "big.bin").open("wb") as file:
            file.truncate(8 * 1024 * 1024 + 1)
        with self.connect() as sock:
            sock.sendall(wire.request("/big.bin"))
            _, body = self.reply(sock, 500)
            self.assertNotIn(str(self.root).encode(), body)
            sock.sendall(wire.HELLO_REQUEST)
            self.reply(sock, 200)

    def test_permission_failure_500_keeps_connection(self):
        with self.connect() as sock:
            with mock.patch.object(Path, "open", side_effect=PermissionError("secret local path")):
                sock.sendall(wire.HELLO_REQUEST)
                _, body = self.reply(sock, 500)
                self.assertNotIn(b"secret", body)
            sock.sendall(wire.HELLO_REQUEST)
            self.reply(sock, 200)

    def test_fatal_declarations_return_diagnostic_without_payload(self):
        cases = [(wire.frame(version=2, length=0xffffffff), 0),
                 (wire.frame(length=16385), 1), (wire.frame(length=0xffffffff), 1),
                 (wire.frame(kind=15, length=16777217, request_id=88), 0),
                 (wire.frame(request_id=0), 0), (wire.frame(kind=2, request_id=99), 0)]
        for raw, expected_id in cases:
            with self.subTest(raw=raw.hex()), self.connect() as sock:
                sock.sendall(raw)
                self.reply(sock, 400, expected_id, close=True)
                self.assertEqual(sock.recv(1), b"")

    def test_truncated_frames_and_diagnostic_ids(self):
        cases = [(wire.HELLO_REQUEST[:5], 0), (wire.HELLO_REQUEST[:-1], 1),
                 (wire.frame(b"x", kind=3, request_id=72, length=2), 0)]
        for raw, expected_id in cases:
            with self.subTest(raw=raw.hex()), self.connect() as sock:
                sock.sendall(raw)
                sock.shutdown(socket.SHUT_WR)
                self.reply(sock, 400, expected_id, close=True)
                self.assertEqual(sock.recv(1), b"")

    def test_clean_eof_then_another_client(self):
        with self.connect() as sock:
            sock.shutdown(socket.SHUT_WR)
            self.assertEqual(sock.recv(1), b"")
        with self.connect() as sock:
            sock.sendall(wire.HELLO_REQUEST)
            self.reply(sock, 200)

    def test_unknown_literal_header_is_accepted(self):
        with self.connect() as sock:
            sock.sendall(wire.request(entries=[("x-course", "independent peer")]))
            self.reply(sock, 200)
