"""Independent test peer, written from the spec; deliberately no bhttp imports."""

import struct


NAMES = {1: "content-type", 2: "content-length", 3: "host", 4: "accept",
         5: "user-agent", 6: "server", 7: "connection", 8: "cache-control",
         9: "date", 10: "last-modified"}
HELLO_REQUEST = bytes.fromhex(
    "11 00 00 01 00 00 00 0e 01 00 0a 2f 68 65 6c 6c 6f 2e 74 78 74 00")


def frame(payload=b"", *, kind=1, flags=0, request_id=1, version=1, length=None):
    return struct.pack("!BBHI", (version << 4) | kind, flags, request_id,
                       len(payload) if length is None else length) + payload


def header_block(entries):
    """Entries are (numeric selector or literal name, value). Preserve given order."""
    result = bytes([len(entries)])
    for selector, value in entries:
        value = value.encode("utf-8") if isinstance(value, str) else value
        if isinstance(selector, int):
            result += bytes([selector])
        else:
            name = selector.encode("ascii")
            result += b"\x00" + bytes([len(name)]) + name
        result += len(value).to_bytes(2, "big") + value
    return result


def request(path="/hello.txt", *, request_id=1, entries=(), method=1, flags=0):
    raw = path.encode("utf-8") if isinstance(path, str) else path
    payload = bytes([method]) + len(raw).to_bytes(2, "big") + raw + header_block(entries)
    return frame(payload, request_id=request_id, flags=flags)


def response(body=b"", *, status=200, request_id=1, connection="keep-alive", extra=()):
    entries = [(1, "application/octet-stream"), (2, str(len(body))), (7, connection), *extra]
    return frame(status.to_bytes(2, "big") + header_block(entries) + body,
                 kind=2, request_id=request_id)


def exact(sock, length):
    result = b""
    while len(result) < length:
        part = sock.recv(length - len(result))
        if not part:
            raise AssertionError("independent peer received truncated frame")
        result += part
    return result


def read_frame(sock):
    raw = exact(sock, 8)
    tag, flags, request_id, length = struct.unpack("!BBHI", raw)
    assert length <= 16777216
    return (tag >> 4, tag & 15, flags, request_id, length), exact(sock, length)


def parse_headers(payload, offset):
    count = payload[offset]
    offset += 1
    assert count <= 32
    headers = {}
    for _ in range(count):
        selector = payload[offset]
        offset += 1
        if selector:
            name = NAMES[selector]
        else:
            size = payload[offset]
            offset += 1
            name = payload[offset:offset + size].decode("ascii")
            offset += size
        size = int.from_bytes(payload[offset:offset + 2], "big")
        offset += 2
        assert size <= 4096 and offset + size <= len(payload)
        assert name not in headers
        headers[name] = payload[offset:offset + size].decode("utf-8")
        offset += size
    return headers, offset


def parse_response(header, payload):
    assert header[:3] == (1, 2, 0)
    assert header[4] == len(payload)
    status = int.from_bytes(payload[:2], "big")
    assert 200 <= status <= 599
    headers, offset = parse_headers(payload, 2)
    body = payload[offset:]
    assert headers["content-length"] == str(len(body))
    assert headers["content-type"]
    assert headers["connection"] in ("keep-alive", "close")
    return status, headers, body


def parse_request(header, payload):
    assert header[:4] == (1, 1, 0, 1)
    assert header[4] == len(payload) <= 16384
    assert payload[0] == 1
    size = int.from_bytes(payload[1:3], "big")
    path = payload[3:3 + size].decode("utf-8")
    headers, end = parse_headers(payload, 3 + size)
    assert end == len(payload)
    return path, headers
