"""BHTTP/1 codec and bounded TCP primitives; see docs/spec.md."""

from dataclasses import dataclass
import re
import socket
import struct
from typing import Callable, Mapping, TextIO

VERSION = 1
FRAME_HEADER_FORMAT = "!BBHI"
FRAME_HEADER_SIZE = 8
TYPE_REQUEST = 1
TYPE_RESPONSE = 2
FLAGS_NONE = 0
METHOD_GET = 1

MAX_FRAME_PAYLOAD = 16 * 1024 * 1024
MAX_REQUEST_PAYLOAD = 16 * 1024
MAX_HEADER_BLOCK = 16 * 1024
MAX_BODY_BYTES = 8 * 1024 * 1024
MAX_PATH_BYTES = 4096
MAX_HEADER_COUNT = 32
MAX_HEADER_NAME_BYTES = 255
MAX_HEADER_VALUE_BYTES = 4096
SKIP_CHUNK_BYTES = 64 * 1024

HEADER_NAME_IDS = {
    "content-type": 1,
    "content-length": 2,
    "host": 3,
    "accept": 4,
    "user-agent": 5,
    "server": 6,
    "connection": 7,
    "cache-control": 8,
    "date": 9,
    "last-modified": 10,
}

HEADER_ID_NAMES = {number: name for name, number in HEADER_NAME_IDS.items()}
_FRAME = struct.Struct(FRAME_HEADER_FORMAT)
_U16 = struct.Struct("!H")
_NAME = re.compile(r"[a-z][a-z0-9-]*", re.ASCII)
_DECIMAL = re.compile(r"0|[1-9][0-9]*", re.ASCII)
_DEVICES = {"CON", "PRN", "AUX", "NUL", "CONIN$", "CONOUT$"}
_DEVICES.update(prefix + digit for prefix in ("COM", "LPT")
                for digit in "123456789¹²³")


class ProtocolError(Exception):
    """Invalid wire input."""


class MalformedMessage(ProtocolError):
    """A bounded message has invalid fields or semantics."""


class FatalFrameError(ProtocolError):
    """The connection must close without resynchronization."""


class CleanEOF(Exception):
    """EOF before the first byte of the next frame header."""


class TruncatedRead(ProtocolError):
    def __init__(self, expected: int, partial: bytes):
        super().__init__(f"truncated read: expected {expected} bytes, got {len(partial)}")
        self.partial = partial


@dataclass(frozen=True)
class FrameHeader:
    version: int
    frame_type: int
    flags: int
    request_id: int
    payload_length: int

    @property
    def unknown(self) -> bool:
        return self.frame_type not in (TYPE_REQUEST, TYPE_RESPONSE)

    @property
    def diagnostic_id(self) -> int:
        if self.version == VERSION and self.frame_type == TYPE_REQUEST:
            return self.request_id
        return 0


@dataclass(frozen=True)
class Request:
    path: str
    headers: dict[str, str]


@dataclass(frozen=True)
class Response:
    status: int
    headers: dict[str, str]
    body: bytes


def pack_frame_header(header: FrameHeader) -> bytes:
    bounds = ((header.version, 15), (header.frame_type, 15),
              (header.flags, 255), (header.request_id, 65535),
              (header.payload_length, MAX_FRAME_PAYLOAD))
    if any(not isinstance(value, int) or not 0 <= value <= limit
           for value, limit in bounds):
        raise FatalFrameError("frame header field out of range")
    return _FRAME.pack((header.version << 4) | header.frame_type,
                       header.flags, header.request_id, header.payload_length)


def unpack_frame_header(raw: bytes) -> FrameHeader:
    if len(raw) != FRAME_HEADER_SIZE:
        raise FatalFrameError("frame header must be exactly 8 bytes")
    packed, flags, request_id, length = _FRAME.unpack(raw)
    return FrameHeader(packed >> 4, packed & 15, flags, request_id, length)


def validate_frame_header(header: FrameHeader, expected_type: int) -> None:
    if header.version != VERSION:
        raise FatalFrameError("unsupported protocol version")
    if header.payload_length > MAX_FRAME_PAYLOAD:
        raise FatalFrameError("frame payload exceeds limit")
    if header.unknown:
        return  # Unknown flags and IDs have no meaning in version 1.
    if header.frame_type != expected_type:
        raise FatalFrameError("known frame type in wrong direction")
    if expected_type == TYPE_REQUEST:
        if header.request_id == 0:
            raise FatalFrameError("request ID zero is reserved")
        if header.payload_length > MAX_REQUEST_PAYLOAD:
            raise FatalFrameError("request payload exceeds limit")
    # Known flags are checked after consuming the bounded payload on the server.


class _Reader:
    """Cursor that cannot read outside a bounded payload."""

    def __init__(self, data: bytes, offset: int = 0):
        self.data = data
        self.offset = offset

    def take(self, size: int) -> bytes:
        end = self.offset + size
        if size < 0 or end > len(self.data):
            raise MalformedMessage("field extends past payload boundary")
        result = self.data[self.offset:end]
        self.offset = end
        return result

    def u8(self) -> int:
        return self.take(1)[0]

    def u16(self) -> int:
        return _U16.unpack(self.take(2))[0]


def _utf8(raw: bytes) -> str:
    try:
        return raw.decode("utf-8", errors="strict")
    except UnicodeDecodeError as exc:
        raise MalformedMessage("invalid UTF-8") from exc


def _encode_utf8(text: str) -> bytes:
    try:
        return text.encode("utf-8", errors="strict")
    except (UnicodeEncodeError, AttributeError) as exc:
        raise MalformedMessage("invalid UTF-8 string") from exc


def _has_control(text: str) -> bool:
    return any(ord(char) < 32 or ord(char) == 127 for char in text)


def _validate_name(name: str) -> None:
    if not isinstance(name, str) or not _NAME.fullmatch(name):
        raise MalformedMessage("invalid lowercase ASCII header name")
    if len(name) > MAX_HEADER_NAME_BYTES:
        raise MalformedMessage("header name exceeds limit")


def _validate_value(raw: bytes) -> str:
    if len(raw) > MAX_HEADER_VALUE_BYTES or raw.startswith(b"\xef\xbb\xbf"):
        raise MalformedMessage("invalid header value length or BOM")
    value = _utf8(raw)
    if _has_control(value):
        raise MalformedMessage("control character in header value")
    return value


def encode_headers(headers: Mapping[str, str]) -> bytes:
    if len(headers) > MAX_HEADER_COUNT:
        raise MalformedMessage("too many headers")
    for name in headers:
        _validate_name(name)
    result = bytearray([len(headers)])
    for name in sorted(headers):
        value = _encode_utf8(headers[name])
        _validate_value(value)
        number = HEADER_NAME_IDS.get(name, 0)
        result.append(number)
        if number == 0:
            raw_name = name.encode("ascii")
            result.append(len(raw_name))
            result.extend(raw_name)
        result.extend(_U16.pack(len(value)))
        result.extend(value)
        if len(result) > MAX_HEADER_BLOCK:
            raise MalformedMessage("header block exceeds limit")
    return bytes(result)


def decode_headers(data: bytes, offset: int = 0) -> tuple[dict[str, str], int]:
    reader = _Reader(data, offset)
    start = offset
    count = reader.u8()
    if count > MAX_HEADER_COUNT:
        raise MalformedMessage("too many headers")
    headers = {}
    for _ in range(count):
        number = reader.u8()
        if number == 0:
            length = reader.u8()
            try:
                name = reader.take(length).decode("ascii")
            except UnicodeDecodeError as exc:
                raise MalformedMessage("non-ASCII header name") from exc
            _validate_name(name)
        else:
            name = HEADER_ID_NAMES.get(number)
            if name is None:
                raise MalformedMessage("unknown numeric header selector")
        if name in headers:
            raise MalformedMessage("duplicate header name")
        length = reader.u16()
        if length > MAX_HEADER_VALUE_BYTES:
            raise MalformedMessage("header value exceeds limit")
        headers[name] = _validate_value(reader.take(length))
        if reader.offset - start > MAX_HEADER_BLOCK:
            raise MalformedMessage("header block exceeds limit")
    return headers, reader.offset


def validate_path(path: str) -> str:
    """Validate a wire path and return its exactly-once decoded filesystem path."""
    raw = _encode_utf8(path)
    if not 1 <= len(raw) <= MAX_PATH_BYTES or not path.startswith("/"):
        raise MalformedMessage("invalid origin path or path length")
    if _has_control(path) or any(char in path for char in "\\?#"):
        raise MalformedMessage("forbidden character in path")
    decoded = bytearray()
    offset = 0
    while offset < len(raw):
        if raw[offset] == 37:  # Validate escapes before decoding; never decode twice.
            digits = raw[offset + 1:offset + 3]
            if len(digits) != 2 or any(c not in b"0123456789abcdefABCDEF" for c in digits):
                raise MalformedMessage("malformed percent escape")
            decoded.append(int(digits, 16))
            offset += 3
        else:
            decoded.append(raw[offset])
            offset += 1
    text = _utf8(bytes(decoded))
    if not text.startswith("/") or "//" in text:
        raise MalformedMessage("invalid path separators")
    if _has_control(text) or any(char in text for char in "\\?#"):
        raise MalformedMessage("forbidden character in decoded path")
    for part in text.split("/")[1:]:
        if not part:  # Root and a single trailing slash are valid non-file paths.
            continue
        if part in (".", "..") or part.endswith((".", " ")):
            raise MalformedMessage("unsafe path component")
        if any(char in part for char in ':<>"|*'):
            raise MalformedMessage("forbidden filesystem character")
        stem = part.split(".", 1)[0].rstrip(" ").upper()
        if stem in _DEVICES:
            raise MalformedMessage("reserved device name")
    return text


def _validate_request_headers(headers: Mapping[str, str]) -> None:
    if "connection" in headers and headers["connection"] != "keep-alive":
        raise MalformedMessage("request connection must be keep-alive")
    if "content-length" in headers and headers["content-length"] != "0":
        raise MalformedMessage("request bodies are unsupported")


def _frame(frame_type: int, request_id: int, payload: bytes) -> bytes:
    header = FrameHeader(VERSION, frame_type, FLAGS_NONE, request_id, len(payload))
    return pack_frame_header(header) + payload


def encode_request(path: str, headers: Mapping[str, str] | None = None,
                   request_id: int = 1) -> bytes:
    if not 1 <= request_id <= 65535:
        raise MalformedMessage("invalid request ID")
    validate_path(path)
    headers = {} if headers is None else headers
    _validate_request_headers(headers)
    raw = _encode_utf8(path)
    payload = bytes([METHOD_GET]) + _U16.pack(len(raw)) + raw + encode_headers(headers)
    if len(payload) > MAX_REQUEST_PAYLOAD:
        raise MalformedMessage("request payload exceeds limit")
    return _frame(TYPE_REQUEST, request_id, payload)


def decode_request(payload: bytes) -> Request:
    if len(payload) > MAX_REQUEST_PAYLOAD:
        raise MalformedMessage("request payload exceeds limit")
    reader = _Reader(payload)
    if reader.u8() != METHOD_GET:
        raise MalformedMessage("unsupported method")
    length = reader.u16()
    if not 1 <= length <= MAX_PATH_BYTES:
        raise MalformedMessage("invalid path length")
    path = _utf8(reader.take(length))
    validate_path(path)
    headers, end = decode_headers(payload, reader.offset)
    if end != len(payload):
        raise MalformedMessage("trailing request bytes")
    _validate_request_headers(headers)
    return Request(path, headers)


def _validate_response(status: int, headers: Mapping[str, str], body_length: int) -> None:
    if not 200 <= status <= 599:
        raise MalformedMessage("invalid final status")
    if body_length > MAX_BODY_BYTES:
        raise MalformedMessage("response body exceeds limit")
    if not headers.get("content-type"):
        raise MalformedMessage("missing or empty content-type")
    length = headers.get("content-length", "")
    if not _DECIMAL.fullmatch(length) or length != str(body_length):
        raise MalformedMessage("invalid or mismatched content-length")
    if headers.get("connection") not in ("keep-alive", "close"):
        raise MalformedMessage("invalid or missing connection header")


def encode_response(status: int, headers: Mapping[str, str], body: bytes,
                    request_id: int) -> bytes:
    _validate_response(status, headers, len(body))
    if not 0 <= request_id <= 65535:
        raise MalformedMessage("invalid response ID")
    if request_id == 0 and (status != 400 or headers["connection"] != "close"):
        raise MalformedMessage("ID zero requires a fatal 400 diagnostic")
    payload = _U16.pack(status) + encode_headers(headers) + body
    return _frame(TYPE_RESPONSE, request_id, payload)


def decode_response(payload: bytes) -> Response:
    if len(payload) > MAX_FRAME_PAYLOAD:
        raise MalformedMessage("response payload exceeds limit")
    reader = _Reader(payload)
    status = reader.u16()
    headers, end = decode_headers(payload, reader.offset)
    _validate_response(status, headers, len(payload) - end)
    return Response(status, headers, payload[end:])


def validate_response_id(header: FrameHeader, response: Response, expected_id: int) -> None:
    if header.flags != FLAGS_NONE:
        raise FatalFrameError("nonzero response flags")
    if header.request_id == 0:
        if response.status == 400 and response.headers["connection"] == "close":
            return
        raise FatalFrameError("invalid zero-ID diagnostic")
    if header.request_id != expected_id:
        raise FatalFrameError("response ID does not match request")


def read_exact(sock: socket.socket, size: int, *, allow_eof: bool = False) -> bytes:
    if not 0 <= size <= MAX_FRAME_PAYLOAD:
        raise FatalFrameError("invalid exact-read size")
    result = bytearray()
    while len(result) < size:
        chunk = sock.recv(min(size - len(result), SKIP_CHUNK_BYTES))
        if not chunk:
            if allow_eof and not result:
                raise CleanEOF()
            raise TruncatedRead(size, bytes(result))
        result.extend(chunk)
    return bytes(result)


def read_frame_header(sock: socket.socket) -> FrameHeader:
    return unpack_frame_header(read_exact(sock, FRAME_HEADER_SIZE, allow_eof=True))


def skip_payload(sock: socket.socket, length: int,
                 on_chunk: Callable[[bytes], None] | None = None) -> None:
    if not 0 <= length <= MAX_FRAME_PAYLOAD:
        raise FatalFrameError("invalid skip size")
    remaining = length
    while remaining:
        chunk = read_exact(sock, min(remaining, SKIP_CHUNK_BYTES))
        if on_chunk:
            on_chunk(chunk)
        remaining -= len(chunk)


class FrameDumper:
    """Stream a complete hex dump, including unknown payloads, with bounded storage."""

    def __init__(self, direction: str, header: FrameHeader, stream: TextIO):
        self.stream = stream
        self.offset = 0
        self.pending = bytearray()
        name = {1: "REQUEST", 2: "RESPONSE"}.get(header.frame_type, "UNKNOWN")
        print(f"{direction} frame bytes={8 + header.payload_length} version={header.version} "
              f"type={header.frame_type}({name}) flags=0x{header.flags:02x} "
              f"request_id={header.request_id} payload_length={header.payload_length}",
              file=stream)
        # Pack directly so even unsupported/oversized incoming headers can be shown.
        self.feed(_FRAME.pack((header.version << 4) | header.frame_type,
                              header.flags, header.request_id, header.payload_length))

    def feed(self, chunk: bytes) -> None:
        data = bytes(self.pending) + chunk
        end = len(data) // 16 * 16
        for start in range(0, end, 16):
            self._line(data[start:start + 16])
        self.pending = bytearray(data[end:])

    def _line(self, row: bytes) -> None:
        ascii_text = "".join(chr(c) if 32 <= c < 127 else "." for c in row)
        print(f"  {self.offset:08x}  {row.hex(' '):47}  |{ascii_text}|", file=self.stream)
        self.offset += len(row)

    def finish(self, note: str | None = None) -> None:
        if self.pending:
            self._line(bytes(self.pending))
            self.pending.clear()
        if note:
            print(f"  {note}", file=self.stream)


def annotate_frame(header: FrameHeader, payload: bytes, stream: TextIO) -> None:
    """Annotate already validated fields using absolute frame byte offsets."""
    if header.frame_type == TYPE_REQUEST:
        request = decode_request(payload)
        path_length = _U16.unpack(payload[1:3])[0]
        print(f"  [8] method=1 GET; [9..10] path_length={path_length}; "
              f"[11..{10 + path_length}] path={request.path!r}", file=stream)
        block_offset = 3 + path_length
    else:
        response = decode_response(payload)
        print(f"  [8..9] status={response.status}", file=stream)
        block_offset = 2
    reader = _Reader(payload, block_offset)
    count = reader.u8()
    print(f"  [{8 + block_offset}] header_count={count}", file=stream)
    for _ in range(count):
        start = reader.offset
        number = reader.u8()
        name = reader.take(reader.u8()).decode("ascii") if number == 0 else HEADER_ID_NAMES[number]
        value_length = reader.u16()
        value = reader.take(value_length).decode("utf-8")
        form = "literal" if number == 0 else f"ID {number}"
        print(f"  [{8 + start}..{7 + reader.offset}] {name} ({form}, "
              f"value_length={value_length}) = {value!r}", file=stream)
    if header.frame_type == TYPE_RESPONSE:
        length = len(payload) - reader.offset
        print(f"  body offset={8 + reader.offset} length={length} opaque bytes", file=stream)
