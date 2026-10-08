"""Proposed wire constants; see docs/protocol-draft.md.

Phase 1 only: frame encoding, decoding, and socket I/O are not implemented.
These constants remain provisional until the protocol draft is approved.
"""

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
