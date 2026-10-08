import io
from pathlib import Path
import socket
import subprocess
import sys
import threading
import unittest
from unittest import mock

from bhttp import client
from bhttp import protocol as p
from tests import wire_peer as wire


REPO = Path(__file__).resolve().parents[1]


class ClientTests(unittest.TestCase):
    def run_peer(self, outgoing, *, verbose=False):
        """Actual CLI subprocess versus an independently encoded/parsing TCP peer."""
        captured = {}
        failures = []
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as listener:
            listener.bind(("127.0.0.1", 0))
            listener.listen()
            listener.settimeout(5)
            port = listener.getsockname()[1]
            def peer():
                try:
                    conn, _ = listener.accept()
                    with conn:
                        conn.settimeout(5)
                        header, payload = wire.read_frame(conn)
                        captured["request"] = wire.parse_request(header, payload)
                        captured["raw"] = wire.frame(payload)
                        conn.sendall(outgoing)
                        conn.shutdown(socket.SHUT_WR)
                        # The client should close, never issue another request on this socket.
                        try:
                            captured["after_response"] = conn.recv(1)
                        except ConnectionResetError:
                            # A peer rejecting a header can close with unread payload.
                            captured["after_response"] = b""
                except Exception as exc:
                    failures.append(exc)
            worker = threading.Thread(target=peer, daemon=True)
            worker.start()
            command = [sys.executable, "-m", "bhttp.client"]
            if verbose:
                command.append("-v")
            command.append(f"localhost:{port}/hello.txt")
            try:
                result = subprocess.run(command, cwd=REPO, capture_output=True, timeout=10)
            finally:
                worker.join(6)
            self.assertFalse(worker.is_alive())
            self.assertEqual(failures, [], f"peer failed; client exit={result.returncode}, stderr={result.stderr!r}")
            self.assertEqual(captured["after_response"], b"")
            # Any second connection attempted during the invocation would be queued now.
            listener.setblocking(False)
            try:
                extra, _ = listener.accept()
            except BlockingIOError:
                pass
            else:
                extra.close()
                self.fail("client opened a second TCP connection")
        path, headers = captured["request"]
        self.assertEqual(path, "/hello.txt")
        self.assertEqual(headers, {"accept": "*/*", "host": f"localhost:{port}", "user-agent": "bcurl/1"})
        expected = wire.request(entries=[(4, "*/*"), (3, f"localhost:{port}"), (5, "bcurl/1")])
        self.assertEqual(captured["raw"], expected)
        return result

    def test_independent_response_binary_stdout_one_connection(self):
        body = b"\x00\xff\x80\xef\xbb\xbf\r\npeer response"
        result = self.run_peer(wire.response(body))
        self.assertEqual(result.returncode, 0)
        self.assertEqual(result.stdout, body)
        self.assertEqual(result.stderr, b"")

    def test_verbose_complete_frames_only_on_stderr(self):
        body = b"hello\x00\xff"
        result = self.run_peer(wire.response(body), verbose=True)
        self.assertEqual(result.returncode, 0)
        self.assertEqual(result.stdout, body)
        output = result.stderr.decode()
        for text in ("SEND frame", "RECV frame", "REQUEST", "RESPONSE", "00000000",
                     "version=1", "flags=0x00", "request_id=1", "payload_length=",
                     "path='/hello.txt'", "status=200", "body offset=", "opaque bytes"):
            self.assertIn(text, output)
        self.assertIn("ff", output)

    def test_unknown_frames_dumped_and_skipped(self):
        raw = (wire.frame(b"\xde\xad\xbe\xef", kind=15, flags=255, request_id=65535)
               + wire.frame(kind=0, flags=255, request_id=0) + wire.response(b"after unknown"))
        result = self.run_peer(raw, verbose=True)
        self.assertEqual(result.returncode, 0)
        self.assertEqual(result.stdout, b"after unknown")
        output = result.stderr.decode()
        self.assertEqual(output.count("RECV frame"), 3)
        self.assertIn("de ad be ef", output)
        self.assertIn("type=0(UNKNOWN)", output)
        self.assertIn("flags and request ID ignored", output)

    def test_error_statuses_nonzero_and_body_preserved(self):
        for status in (400, 404, 500, 599):
            with self.subTest(status=status):
                result = self.run_peer(wire.response(b"error body", status=status))
                self.assertEqual(result.returncode, 1)
                self.assertEqual(result.stdout, b"error body")

    def test_3xx_does_not_redirect_or_reconnect(self):
        result = self.run_peer(wire.response(b"no redirect", status=302, extra=[("location", "elsewhere")]))
        self.assertEqual(result.returncode, 0)
        self.assertEqual(result.stdout, b"no redirect")

    def test_valid_zero_id_diagnostic(self):
        result = self.run_peer(wire.response(b"fatal", status=400, request_id=0, connection="close"))
        self.assertEqual(result.returncode, 1)
        self.assertEqual(result.stdout, b"fatal")

    def test_malformed_responses_fail_without_reconnect(self):
        mismatch = b"\x00\xc8" + wire.header_block([(1, "x"), (2, "8"), (7, "keep-alive")]) + b"short"
        responses = [wire.frame(mismatch, kind=2), wire.response(request_id=2),
                     wire.response(request_id=0), wire.frame(b"", kind=2, flags=1),
                     wire.frame(b"", kind=1), wire.frame(version=2, kind=2),
                     wire.frame(kind=2, length=0xffffffff), wire.response(b"body")[:-1]]
        for raw in responses:
            with self.subTest(raw=raw[:12].hex()):
                result = self.run_peer(raw)
                self.assertEqual(result.returncode, 2)
                self.assertEqual(result.stdout, b"")
                self.assertIn(b"bcurl:", result.stderr)

    def test_no_response_is_failure(self):
        result = self.run_peer(b"")
        self.assertEqual(result.returncode, 2)
        self.assertEqual(result.stdout, b"")

    def test_resolver_multiple_addresses_connect_failure_attempted_once(self):
        endpoints = [(socket.AF_INET, socket.SOCK_STREAM, 6, "", ("127.0.0.1", 1)),
                     (socket.AF_INET, socket.SOCK_STREAM, 6, "", ("127.0.0.2", 2))]
        with mock.patch.object(socket, "getaddrinfo", return_value=endpoints), \
                mock.patch.object(socket, "socket") as factory:
            instance = factory.return_value.__enter__.return_value
            instance.connect.side_effect = OSError("first address failed")
            with self.assertRaises(OSError):
                client.fetch("example", 1, wire.HELLO_REQUEST)
            factory.assert_called_once()
            instance.connect.assert_called_once_with(("127.0.0.1", 1))
            instance.sendall.assert_not_called()

    def test_success_uses_exactly_one_socket_connect_and_sendall(self):
        endpoints = [(socket.AF_INET, socket.SOCK_STREAM, 6, "", ("127.0.0.1", 9))]
        raw = wire.response(b"x")
        with mock.patch.object(socket, "getaddrinfo", return_value=endpoints), \
                mock.patch.object(socket, "socket") as factory:
            instance = factory.return_value.__enter__.return_value
            instance.recv.side_effect = [raw[:8], raw[8:]]
            result = client.fetch("localhost", 9, wire.HELLO_REQUEST)
            self.assertEqual(result.body, b"x")
            factory.assert_called_once()
            instance.connect.assert_called_once_with(("127.0.0.1", 9))
            instance.sendall.assert_called_once_with(wire.HELLO_REQUEST)

    def test_simple_target_parser_and_usage_errors(self):
        self.assertEqual(client.parse_target("localhost:9000/index.html"),
                         ("localhost", 9000, "/index.html", "localhost:9000"))
        for target in ("localhost", "localhost:9000", "localhost:0/a", "localhost:65536/a",
                       "localhost:abc/a", "http://localhost:9000/a", "[::1]:9000/a"):
            with self.subTest(target=target), self.assertRaises(ValueError):
                client.parse_target(target)
        with mock.patch.object(sys, "stderr", io.StringIO()), \
                mock.patch.object(socket, "getaddrinfo") as resolver:
            self.assertEqual(client.main(["bad-target"]), 2)
            resolver.assert_not_called()


if __name__ == "__main__":
    unittest.main()
