# BHTTP/1 — Phase 1 protocol draft

**Status:** proposed for approval; not an implemented protocol or a captured
exchange. This is the project's own wire format. MUST, MUST NOT, and SHOULD
express requirements of this draft. Offsets are zero-based; integers are
unsigned and big-endian. Lengths count bytes, never Unicode characters.
There is no padding, terminator, handshake, or textual HTTP envelope.
String encodings do not insert byte-order marks; opaque bodies may contain
any bytes, including byte-order marks.

## 1. Fixed frame header

Every frame is exactly this 8-byte header followed by `payload_length` bytes:

| Offset | Width | Field | Meaning in version 1 |
| --- | --- | --- | --- |
| 0 | 1 byte | version/type | High 4 bits: version `1`; low 4 bits: type |
| 1 | 1 byte | flags | `0x00` for known types; no flags defined |
| 2 | 2 bytes | request_id | `1..65535` for requests and their responses |
| 4 | 4 bytes | payload_length | Payload only; excludes the 8-byte header |

Packing formula: byte 0 is `(version << 4) | type`.
The corresponding Python `struct` format is `!BBHI`, with the first two
fields each represented as a full byte. Maximum accepted payload length is
16 MiB (`16,777,216`); the header can represent larger values, but they are
invalid in this version. A receiver validates length before allocation.

| Type nibble | Name | Direction | Payload |
| --- | --- | --- | --- |
| `0x1` | REQUEST | Client to server | One complete GET request |
| `0x2` | RESPONSE | Server to client | One complete final response |
| `0x0`, `0x3..0xf` | Unknown | Either | Opaque, skippable bytes |

For a version-1 unknown type with an allowed length, receivers MUST consume
exactly that payload without decoding it, then read the next frame. Ignore
its flags and request ID entirely. Even a zero-length unknown frame is valid.
Unknown frames do not create, complete, or cancel an exchange. Discard them
in chunks of at most 64 KiB, rather than allocating the whole payload.

Unknown *versions* are unsupported framing contracts: close after a best-effort
400 on the server; do not interpret their payload or attempt resynchronization.
An excessive length is likewise fatal, even for an unknown type. Known frame
types in the wrong direction are errors, not skippable extensions.

## 2. Request payload

| Relative offset | Width | Field |
| --- | --- | --- |
| 0 | 1 byte | method: `0x01` means GET; all other values are invalid |
| 1 | 2 bytes | path_length: `1..4096` |
| 3 | path_length bytes | path: strict UTF-8; mapping rules in section 6 |
| 3 + path_length | variable | Header block, including its count byte |

The header block MUST end exactly at the end of the request payload. There
is no request body. Extra bytes, missing bytes, unsupported method codes,
and invalid UTF-8 are malformed requests. A request payload is at most
16 KiB (`16,384`), including all fields. Minimum valid size is 5 bytes
(GET, one-byte `/` path, and zero headers).

## 3. Response payload

| Relative offset | Width | Field |
| --- | --- | --- |
| 0 | 2 bytes | status: one final code in `200..599` |
| 2 | variable | Header block, including its count byte |
| Immediately after header block | Remaining payload bytes | Body: opaque bytes |

The header count and entry lengths identify where metadata ends; the remaining
payload is the body. There is no separate body-length field or DATA frame.
Body length is `payload_length - 2 - encoded_header_block_length` and MUST
be nonnegative and at most 8 MiB (`8,388,608`). Empty files have zero body bytes.
Body bytes are never interpreted as UTF-8 by the codec.

Every response MUST contain exactly one each of:

- `content-type`: nonempty UTF-8 media-type text, e.g. `text/plain; charset=utf-8`
  or `application/octet-stream`. Receivers need not parse it to recover bytes.
- `content-length`: ASCII decimal body byte count; exactly `0` for zero,
  otherwise `[1-9][0-9]*`. It MUST equal the body length derived above.
- `connection`: exactly `keep-alive` or `close`. `close` announces closure
  after this response. `keep-alive` permits another sequential request.

The initial server uses 200 (file served), 400 (malformed request/frame),
404 (missing path or non-file), and 500 (failure to read/serve a file,
including a file exceeding the body limit). Error bodies are short UTF-8
text with `text/plain; charset=utf-8`, without local paths or tracebacks.
The client accepts any structurally valid final `200..599` status, writes
its body, and exits nonzero for `400..599`. It does not follow redirects.
Invalid responses or transport failures also produce a nonzero exit.

## 4. Header blocks and dictionary

A block starts with `header_count` (1 byte, `0..32`), followed immediately by
that many entries. The whole block, including count and all entry bytes, is
at most 16 KiB. An entry is one of:

```text
Known name:   name_id:u8 | value_length:u16 | value[value_length]
Literal name: 0x00:u8 | name_length:u8 | name[name_length]
                     | value_length:u16 | value[value_length]
```

`name_length` is `1..255`. Literal names are ASCII and MUST match
`[a-z][a-z0-9-]*`; names are lowercase and case-sensitive on the wire.
`value_length` is `0..4096`. Values use strict UTF-8 and MUST NOT begin with
the UTF-8 BOM bytes `ef bb bf`; empty
values are valid unless prohibited by a particular header's semantics.
Values MUST NOT contain U+0000..U+001F or U+007F. Whitespace is not trimmed.
Counts and lengths are explicit, so colons, separators, and terminators
are not transmitted. Each decoded name can occur only once per block.

| ID (decimal / hex) | Name | Planned use |
| --- | --- | --- |
| 1 / `01` | content-type | Every response |
| 2 / `02` | content-length | Every response |
| 3 / `03` | host | Client endpoint authority, informational |
| 4 / `04` | accept | Client sends `*/*`, informational |
| 5 / `05` | user-agent | Client sends `bcurl/1`, informational |
| 6 / `06` | server | Server sends `bserve/1`, informational |
| 7 / `07` | connection | Response lifetime indicator |
| 8 / `08` | cache-control | Server sends `no-store`, informational |
| 9 / `09` | date | Response generation time, informational |
| 10 / `0a` | last-modified | File modification time on 200, informational |

`0x00` is exclusively the literal escape. Selectors `0x0b..0xff` are invalid
header-name IDs in version 1. Unknown *header names* MUST use the literal
form; unlike unknown frame types, unknown numeric header IDs are malformed.
This dictionary is fixed for version 1.

Senders MUST use the numeric form for dictionary names and sort entries by
decoded ASCII name, not numeric ID. Receivers MUST accept any entry order
and either name form, including a valid literal spelling of a dictionary
name, but MUST reject duplicate decoded names. This gives deterministic
output while keeping receivers tolerant of equivalent representations.
Given identical field values, encoding has exactly one canonical result.

Well-formed unknown literal headers are accepted and otherwise ignored.
Request headers are optional. The server does not do content negotiation,
virtual hosting, caching, or request bodies; it MUST NOT let informational
or unknown headers override framing, request IDs, path checks, or methods.
If a request includes `connection`, its only allowed value is `keep-alive`.
If it includes `content-length`, its only allowed value is `0`.
Date values, when present, use UTC `YYYY-MM-DDTHH:MM:SSZ` with decimal fields
and no fractional seconds; `last-modified` uses the same format. Optional
metadata is not required for decoding or for the illustrative example below.
Receivers need only apply generic header validation to informational values;
they do not need to parse authorities, media negotiation, or timestamps.

## 5. Request IDs and connection lifetime

The client selects one resolved endpoint and attempts exactly one TCP
connection for an invocation, without trying another address on failure. It sends a
complete REQUEST and waits for its complete RESPONSE before sending another
REQUEST. There is at most one outstanding request; no multiplexing, pipelining,
automatic reconnect, or redirect connection. Unknown frames may appear between
complete frames while waiting. A valid response echoes the request ID.

A client starts with ID 1, increments after each completed exchange, and wraps
65535 to 1. Servers accept any nonzero ID and do not enforce monotonicity;
an ID can be reused once its exchange is complete. ID 0 is forbidden in
requests. It is permitted only on a server's fatal diagnostic RESPONSE with
status 400 and `connection: close` when no trustworthy request ID is available.
A client awaiting a response accepts this diagnostic and terminates with a
nonzero status. Other mismatched/zero response IDs are invalid.

Successful responses, 404s, 500s, and recoverable 400s use `keep-alive`; the
server continues reading frames. Fatal errors use `close`. The one-URL CLI
closes its socket after its response, while the protocol supports repeated
requests on that same socket. There is no protocol-mandated idle timeout.
Either peer can close; an implementation may impose a local timeout, which
terminates the connection without promising a response.

## 6. Paths, files, and serving limits

Paths are origin paths beginning with `/`, not full URLs. Query strings and
fragments are unsupported. Decode percent escapes exactly once: every `%`
in the wire path MUST be followed by two ASCII hex digits (`0..9`, `a..f`,
or `A..F`). Starting with the
UTF-8 path bytes, replace each escape with its byte, then decode the result
as strict UTF-8. Do not treat `+` as space. Do not percent-decode a second time;
for example `%252e` becomes the literal filename text `%2e`.

Reject with 400 if the raw or decoded path contains a backslash, a NUL/control
character (U+0000..U+001F or U+007F), `?`, or `#`. The decoded path MUST still
begin with `/`. Reject repeated `/` separators and components equal to `.`
or `..`, including encoded forms. To avoid Windows path aliases, also reject
components containing `:`, `<`, `>`, `"`, `|`, or `*`, or ending in a dot or
space. For the device-name check, take the component text before its first dot,
remove trailing ASCII spaces from that stem, and compare case-insensitively
against `CON`, `PRN`, `AUX`, `NUL`, `CONIN$`, `CONOUT$`, `COM1..COM9`,
`LPT1..LPT9`, and COM/LPT with a superscript digit ¹, ², or ³. These same rules
apply on Linux so the accepted path syntax is consistent.

Resolve the configured root once at startup to an existing directory. Build
the candidate from decoded components under that root (do not join the leading
`/` as an absolute filesystem path). Resolve the candidate, including symlinks
and Windows junctions, and verify that it is the root or a descendant using
path-aware comparison, never a string prefix. A resolved escape is a 400.
Symlink loops/invalid paths are 400; nonexistent paths are 404. Only regular
files are served. Directories, `/`, and trailing-slash paths return 404;
there is no implicit index file or directory listing.
File-name case matching follows the host filesystem; the protocol performs
no case folding or Unicode normalization of paths.

This is a read-only server. Its document tree is locally administered and
not writable by network clients. The containment check assumes no adversary
concurrently replaces filesystem components between resolution and opening;
a portable race-proof filesystem sandbox is outside this project's scope.
Ordinary symlinks/junctions pointing outside the root MUST be rejected.

Read the file in binary mode into a bounded snapshot before sending a response
header. Read at most the 8 MiB limit plus one byte to detect excess; file size
metadata alone is not sufficient. Derive content-length from the actual snapshot.
An oversized file or read/permission failure gets 500, keeping the connection
open. This avoids sending a successful frame that is later short or inconsistent
if a file changes during reading. A socket failure during transmission closes
the connection; do not send a second response for that request.

Use `text/html; charset=utf-8` for `.html`/`.htm`,
`text/plain; charset=utf-8` for `.txt`, and
`application/octet-stream` for other files in the initial implementation.
Compare these suffixes case-insensitively. Bodies are preserved byte-for-byte
regardless of content-type. Additional media types can be added later without
changing the wire format.

## 7. Exact reads and malformed input

TCP supplies a byte stream. An exact-read helper MUST loop until it has the
requested number of bytes or encounters EOF/failure. A write MUST likewise
send all frame bytes (`sendall` or equivalent). Receivers parse only within
the declared payload and check every field boundary before accessing it.

| Condition at server | Response and connection |
| --- | --- |
| EOF before any byte of the next frame header | Clean disconnect; no response |
| Complete version-1 REQUEST, nonzero ID, allowed request size, fully consumed payload; bad method, path, UTF-8, headers, trailing bytes, or nonzero flags | 400 with echoed ID; keep open |
| Partial header or payload | Best-effort 400; close |
| Unsupported version, global oversize, REQUEST over 16 KiB, request ID 0, or known type in wrong direction | Best-effort 400; close without draining payload |
| Valid unknown type and allowed global size | Skip; no response; continue |
| I/O reset, broken pipe, local timeout, or inability to write response | Close; a 400 cannot be guaranteed |

For fatal errors use the ID only if a complete version-1 REQUEST header with
a nonzero ID was received; otherwise use diagnostic ID 0. In particular, a
truncated unknown frame's ID is still ignored. Error RESPONSEs always use
version 1, type 2, flags 0, valid headers and actual body length. Fully consumed
malformed REQUESTs retain a known frame boundary; fatal errors MUST NOT scan
for a guessed next frame. After a recoverable 400 the sender may issue another
request, not a continuation of the malformed payload.

After receiving EOF, sending a diagnostic is only best-effort (a peer that
half-closed its write side may still read it). A partial header or a declaration
without complete payload cannot always receive a usable 400. The client treats
truncation, unsupported version, oversize, malformed responses, and wrong
direction/ID as failure and closes; it does not send a diagnostic response.

## 8. Why these widths and limits?

| Field | Reason |
| --- | --- |
| Version, 4 bits | Fifteen nonzero versions are ample for a course protocol; 0 is unsupported. Packing saves a byte. A new version can replace payload grammar. |
| Type, 4 bits | Sixteen codes, two assigned, fourteen unknown/skippable; enough room without a large registry. Type 0 is also skippable. |
| Flags, 8 bits | Explicit one-byte expansion room while preserving a simple 8-byte header. All bits reserved on known version-1 frames. |
| Request ID, 16 bits | 65,535 usable correlation IDs without implying multiplexing. Easy to echo and wrap, small in a dump. |
| Payload length, 32 bits | Self-contained frame boundaries and ample future capacity without a 64-bit field. The 16 MiB validation limit controls memory/work. |
| Method, 8 bits | One simple numeric selector; only GET is currently valid. Avoids text method parsing and supports a later version's extra methods. |
| Path length, 16 bits | A 1-byte length is too short for paths; 2 bytes are compact. The separate 4096-byte limit bounds decoding. |
| Header count, 8 bits | A compact bounded loop; at most 32 entries, well above what this application sends. |
| Header selector, 8 bits | Ten names plus an explicit zero escape fit naturally in one byte. |
| Literal name length, 8 bits | Up to 255 ASCII bytes is ample for a header name, with no variable integer encoding. |
| Value length, 16 bits | Values can exceed 255 bytes; a 4096-byte operational cap prevents huge metadata. |
| Status, 16 bits | Codes such as 400 and 500 exceed 8 bits; 2 bytes cover all accepted final statuses. |
| Body length, no field | The frame length and fully parsed header block already determine it; content-length supplies a required consistency check. |

Global payloads are capped at 16 MiB; known requests and header blocks at
16 KiB; file/body bytes at 8 MiB. These are deliberate submission-scale limits,
not claims that the integer fields are smaller than their wire widths.
The largest valid known response is 8,404,994 bytes of payload
(2 status bytes + 16,384 metadata bytes + 8,388,608 body bytes), below the
global limit. Unknown frames may use the remaining global capacity.

One complete frame per message avoids DATA-frame sequencing, end flags,
partial-response state, and multiplexing. Its tradeoff is bounded buffering
and no large-file streaming. Those are appropriate for this small project.

## 9. Hand-decodable request example (illustrative only)

GET `/hello.txt`, ID 1, zero headers. This example is computed from the draft;
it is not the final captured request/response artifact.

| Frame offsets | Hex | Meaning |
| --- | --- | --- |
| 0 | `11` | Version 1, REQUEST type 1 |
| 1 | `00` | No flags |
| 2..3 | `00 01` | Request ID 1 |
| 4..7 | `00 00 00 0e` | 14 payload bytes |
| 8 | `01` | GET |
| 9..10 | `00 0a` | 10 path bytes |
| 11..20 | `2f 68 65 6c 6c 6f 2e 74 78 74` | UTF-8 `/hello.txt` |
| 21 | `00` | Zero headers; payload ends |

The eventual verbose display will annotate frame offsets, fields, dictionary
names, and payload/body ranges, showing every sent/received frame, including
skipped unknown frames. Diagnostics go to stderr; raw body bytes go to stdout.

## 10. Phase 1 self-review and approval boundary

- **Unknown type skippable?** Yes: the fixed header declares its exact length;
  flags and IDs do not affect skipping. Oversize, unsupported versions, and
  truncation are explicitly fatal exceptions.
- **Independent interoperability?** Yes: widths, byte order, field boundaries,
  dictionary, canonical sending/accepted decoding, limits, IDs, statuses,
  connection behavior, and percent-decoding are specified without relying on
  implementation details.
- **Malformed input becomes a clean 400?** Yes for a complete bounded request;
  preserve the connection after consuming it. Fatal/truncated frames receive
  a best-effort 400 and closure; dead sockets cannot guarantee delivery.
- **Binary files safe?** Yes: opaque body bytes, binary reads, actual byte
  counts, bounded snapshots, and root containment under the stated filesystem
  ownership assumption.
- **Sequential requests on one TCP connection?** Yes, including a request
  following a recoverable 400 or 404; responses echo IDs.
- **Every example byte explainable by hand?** Yes; section 9 accounts for all
  22 bytes and the payload-length calculation.
- **Unnecessary complexity?** No handshake, DATA frames, request bodies,
  compression, multiplexing, authentication, encryption, or dependencies.
  The main tradeoff is the explicit 8 MiB body cap.

No wire-format ambiguity is intentionally deferred. The proposal still needs
your approval, particularly the complete-frame/buffering choice and size caps.
Whether the grader requires installed commands named `bserve`/`bcurl` rather
than equivalent module invocations is a packaging question, not a wire change.

Recommended Phase 2: implement and verify the shared codec/exact-read/skip
helpers first; then safe file mapping, the synchronous persistent server,
the single-connection client, and annotated stderr dumps. Use the planned
tests before producing the final captured exchange and condensed specification.
Do not begin implementation until this draft is approved.
