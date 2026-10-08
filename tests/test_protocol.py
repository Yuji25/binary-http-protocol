import io
import socket
import threading
import unittest

from bhttp import protocol as p
from tests import wire_peer as wire


class FragmentSocket:
    def __init__(self, data, fragment=3):
        self.data = data
        self.fragment = fragment
        self.sizes = []

    def recv(self, size):
        self.sizes.append(size)
        count = min(size, self.fragment, len(self.data))
        result, self.data = self.data[:count], self.data[count:]
        return result


class ProtocolTests(unittest.TestCase):
    def test_exact_frame_header(self):
        header = p.FrameHeader(1, 1, 0, 0x1234, 0x10203)
        expected = bytes.fromhex("11 00 12 34 00 01 02 03")
        self.assertEqual(p.pack_frame_header(header), expected)
        self.assertEqual(p.unpack_frame_header(expected), header)

    def test_exact_approved_request_vector(self):
        self.assertEqual(p.encode_request("/hello.txt"), wire.HELLO_REQUEST)
        self.assertEqual(wire.request(), wire.HELLO_REQUEST)
        self.assertEqual(p.decode_request(wire.HELLO_REQUEST[8:]).path, "/hello.txt")

    def test_numeric_and_literal_header_bytes(self):
        expected = bytes.fromhex("02 03 00 01 78 00 06 78 2d 6e 6f 74 65 00 02 68 69")
        encoded = p.encode_headers({"x-note": "hi", "host": "x"})
        self.assertEqual(encoded, expected)
        self.assertEqual(p.decode_headers(expected), ({"host": "x", "x-note": "hi"}, len(expected)))

    def test_deterministic_name_order(self):
        first = {"user-agent": "bcurl/1", "host": "localhost:9000", "accept": "*/*"}
        second = dict(reversed(list(first.items())))
        self.assertEqual(p.encode_headers(first), p.encode_headers(second))
        self.assertEqual(p.encode_headers(first)[1], 4)  # accept before host, not ID order

    def test_receiver_accepts_any_order_and_literal_known_name(self):
        raw = wire.header_block([(5, "peer"), ("host", "x"), (4, "*/*")])
        headers, end = p.decode_headers(raw)
        self.assertEqual(headers, {"user-agent": "peer", "host": "x", "accept": "*/*"})
        self.assertEqual(end, len(raw))

    def test_malformed_header_blocks(self):
        cases = [b"", b"\x21", b"\x01\x0b\x00\x00", b"\x01\x00\x00",
                 wire.header_block([("X-name", "x")]), b"\x01\x00\x01\xff\x00\x00",
                 b"\x01\x03\x00", b"\x01\x03\x00\x02x",
                 wire.header_block([(3, b"\xff")]), wire.header_block([(3, b"a\nb")]),
                 wire.header_block([(3, b"\xef\xbb\xbfx")]),
                 wire.header_block([(3, "a"), ("host", "b")]),
                 wire.header_block([(3, b"x" * 4097)])]
        for raw in cases:
            with self.subTest(raw=raw[:32]), self.assertRaises(p.MalformedMessage):
                p.decode_headers(raw)

    def test_header_encoding_limits_and_strings(self):
        for headers in ({"Host": "x"}, {"x": "\ud800"}, {"x": "\x00"},
                        {"x" * 256: ""}, {f"x-{i}": "" for i in range(33)},
                        {f"x-{i}": "a" * 4096 for i in range(4)}):
            with self.subTest(headers=list(headers)), self.assertRaises(p.MalformedMessage):
                p.encode_headers(headers)
        good = {"a" * 255: "x" * 4096}
        raw = p.encode_headers(good)
        self.assertEqual(p.decode_headers(raw)[0], good)

    def test_decoded_header_block_limit(self):
        raw = wire.header_block([(f"x-{i}", "a" * 4096) for i in range(4)])
        with self.assertRaises(p.MalformedMessage):
            p.decode_headers(raw)

    def test_malformed_request_fields(self):
        payloads = [b"", b"\x01", b"\x01\x00\x00\x00", b"\x01\x10\x01",
                    b"\x01\x00\x02/", wire.request(method=2)[8:],
                    wire.request(path=b"/\xff")[8:], wire.request()[8:] + b"extra",
                    wire.request(entries=[(7, "close")])[8:],
                    wire.request(entries=[(2, "1")])[8:]]
        for raw in payloads:
            with self.subTest(raw=raw[:20]), self.assertRaises(p.MalformedMessage):
                p.decode_request(raw)

    def test_request_total_limit(self):
        with self.assertRaises(p.MalformedMessage):
            p.encode_request("/" + "a" * 4095, {name: "b" * 4096 for name in "abc"})
        with self.assertRaises(p.MalformedMessage):
            p.decode_request(b"x" * (p.MAX_REQUEST_PAYLOAD + 1))

    def test_path_rejections(self):
        paths = ["../x", "/../x", "/%2e%2e/x", "/%2E%2E/x", "/./x", "/a//b",
                 "/a\\b", "/%5cb", "/C:/x", "/a%3ab", "/a?b", "/a%3fb",
                 "/a#b", "/a%23b", "/a\x00b", "/a%00b", "/a%7fb", "/a\nb",
                 "/a%", "/%0", "/%zz", "/%ff", "/a.", "/a%20", "/NUL",
                 "/con.txt", "/CON .txt", "/lpt9.log", "/COM¹", "/CONOUT$",
                 '/a"b', "/a*b", "/a|b", "/a<b", "/a>b", "/" + "a" * 4096]
        for path in paths:
            with self.subTest(path=path), self.assertRaises(p.MalformedMessage):
                p.validate_path(path)

    def test_exactly_once_decoding_and_valid_paths(self):
        for path, expected in [("/%252e%252e/x", "/%2e%2e/x"), ("/a+b", "/a+b"),
                               ("/a%20b", "/a b"), ("/%c3%a9", "/é"),
                               ("/folder/", "/folder/"), ("/", "/")]:
            with self.subTest(path=path):
                self.assertEqual(p.validate_path(path), expected)

    def test_binary_response_from_independent_peer(self):
        raw = wire.response(b"\x00\xff\xef\xbb\xbfhello")
        decoded = p.decode_response(raw[8:])
        self.assertEqual(decoded.body, b"\x00\xff\xef\xbb\xbfhello")
        self.assertEqual(decoded.status, 200)

    def test_response_validation(self):
        headers = {"content-type": "x", "content-length": "0", "connection": "keep-alive"}
        for changes, status, body in [({"content-length": "1"}, 200, b""),
                                      ({"content-length": "00"}, 200, b""),
                                      ({"content-length": "+0"}, 200, b""),
                                      ({"content-type": ""}, 200, b""),
                                      ({"connection": "maybe"}, 200, b""),
                                      ({}, 199, b""), ({}, 600, b"")]:
            merged = headers | changes
            raw = status.to_bytes(2, "big") + wire.header_block(list(merged.items())) + body
            with self.subTest(changes=changes, status=status), self.assertRaises(p.MalformedMessage):
                p.decode_response(raw)
        for name in headers:
            raw = b"\x00\xc8" + wire.header_block([(key, value) for key, value in headers.items() if key != name])
            with self.subTest(missing=name), self.assertRaises(p.MalformedMessage):
                p.decode_response(raw)

    def test_response_body_limit(self):
        body = b"x" * (p.MAX_BODY_BYTES + 1)
        headers = {"content-type": "x", "content-length": str(len(body)), "connection": "keep-alive"}
        with self.assertRaises(p.MalformedMessage):
            p.encode_response(200, headers, body, 1)
        raw = b"\x00\xc8" + wire.header_block(list(headers.items())) + body
        with self.assertRaises(p.MalformedMessage):
            p.decode_response(raw)

    def test_zero_diagnostic_and_mismatched_ids(self):
        good = p.decode_response(wire.response(status=400, request_id=0, connection="close")[8:])
        p.validate_response_id(p.FrameHeader(1, 2, 0, 0, 0), good, 1)
        for header in (p.FrameHeader(1, 2, 1, 1, 0), p.FrameHeader(1, 2, 0, 2, 0)):
            with self.assertRaises(p.FatalFrameError):
                p.validate_response_id(header, good, 1)
        bad = p.decode_response(wire.response(request_id=0)[8:])
        with self.assertRaises(p.FatalFrameError):
            p.validate_response_id(p.FrameHeader(1, 2, 0, 0, 0), bad, 1)
        with self.assertRaises(p.MalformedMessage):
            p.encode_response(200, bad.headers, b"", 0)

    def test_fatal_header_validation_before_reading(self):
        for header in (p.FrameHeader(2, 1, 0, 1, 0), p.FrameHeader(1, 2, 0, 1, 0),
                       p.FrameHeader(1, 1, 0, 0, 0), p.FrameHeader(1, 1, 0, 1, 16385),
                       p.FrameHeader(1, 3, 255, 0, 16777217)):
            with self.subTest(header=header), self.assertRaises(p.FatalFrameError):
                p.validate_frame_header(header, p.TYPE_REQUEST)
        p.validate_frame_header(p.FrameHeader(1, 0, 255, 0, 0), p.TYPE_REQUEST)
        p.validate_frame_header(p.FrameHeader(1, 15, 255, 65535, 3), p.TYPE_RESPONSE)

    def test_dump_offsets_annotations_and_chunk_boundaries(self):
        stream = io.StringIO()
        header = p.unpack_frame_header(wire.HELLO_REQUEST[:8])
        dump = p.FrameDumper("SEND", header, stream)
        for byte in wire.HELLO_REQUEST[8:]:
            dump.feed(bytes([byte]))
        dump.finish()
        p.annotate_frame(header, wire.HELLO_REQUEST[8:], stream)
        output = stream.getvalue()
        self.assertIn("SEND frame bytes=22", output)
        self.assertIn("00000010", output)
        self.assertIn("path='/hello.txt'", output)
        self.assertLess(len(dump.pending), 16)


class TransportTests(unittest.TestCase):
    def test_fragmented_exact_read(self):
        sock = FragmentSocket(b"abcdefgh", fragment=1)
        self.assertEqual(p.read_exact(sock, 8), b"abcdefgh")
        self.assertEqual(len(sock.sizes), 8)

    def test_clean_eof(self):
        with self.assertRaises(p.CleanEOF):
            p.read_frame_header(FragmentSocket(b""))
        self.assertEqual(p.read_exact(FragmentSocket(b""), 0), b"")

    def test_truncated_header(self):
        for length in range(1, 8):
            with self.subTest(length=length), self.assertRaises(p.TruncatedRead):
                p.read_frame_header(FragmentSocket(wire.HELLO_REQUEST[:length]))

    def test_truncated_payload(self):
        with self.assertRaises(p.TruncatedRead) as context:
            p.read_exact(FragmentSocket(b"abc"), 5)
        self.assertEqual(context.exception.partial, b"abc")

    def test_bounded_unknown_skip_and_next_frame(self):
        length = 2 * p.SKIP_CHUNK_BYTES + 7
        sock = FragmentSocket(b"x" * length + wire.HELLO_REQUEST, fragment=999999)
        chunks = []
        p.skip_payload(sock, length, lambda chunk: chunks.append(len(chunk)))
        self.assertEqual(chunks, [p.SKIP_CHUNK_BYTES, p.SKIP_CHUNK_BYTES, 7])
        self.assertLessEqual(max(sock.sizes), p.SKIP_CHUNK_BYTES)
        self.assertEqual(p.read_exact(sock, 22), wire.HELLO_REQUEST)

    def test_zero_length_unknown_skip(self):
        sock = FragmentSocket(wire.HELLO_REQUEST)
        p.skip_payload(sock, 0)
        self.assertEqual(sock.sizes, [])
        self.assertEqual(p.read_frame_header(sock).frame_type, 1)

    def test_oversized_reads_and_skips_do_not_recv(self):
        sock = FragmentSocket(b"anything")
        for helper in (p.read_exact, p.skip_payload):
            with self.assertRaises(p.FatalFrameError):
                helper(sock, p.MAX_FRAME_PAYLOAD + 1)
        self.assertEqual(sock.sizes, [])

    def test_real_socket_stream(self):
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as listener, \
                socket.socket(socket.AF_INET, socket.SOCK_STREAM) as left:
            listener.bind(("127.0.0.1", 0))
            listener.listen()
            listener.settimeout(3)
            left.settimeout(3)
            left.connect(listener.getsockname())
            right, _ = listener.accept()
            with right:
                right.settimeout(3)
                def send_fragments():
                    for part in (wire.HELLO_REQUEST[:1], wire.HELLO_REQUEST[1:7], wire.HELLO_REQUEST[7:]):
                        right.sendall(part)
                    right.shutdown(socket.SHUT_WR)
                worker = threading.Thread(target=send_fragments)
                worker.start()
                header = p.read_frame_header(left)
                self.assertEqual(p.read_exact(left, header.payload_length), wire.HELLO_REQUEST[8:])
                with self.assertRaises(p.CleanEOF):
                    p.read_frame_header(left)
                worker.join(3)
                self.assertFalse(worker.is_alive())
