# Annotated BHTTP/1 request and response

This is a real execution of the supplied programs on Windows, using the
sample `/hello.txt` resource and port **9000**. All frame bytes below come
verbatim from the verbose client's stderr; none are omitted or reconstructed
for display. The format is defined in [the specification](spec.md).

## Commands and capture

Run the server and client in separate terminals:

```text
python -m bhttp.server ./www 9000
python -m bhttp.client -v localhost:9000/hello.txt
```

For this capture, a subprocess ran those exact module invocations and collected
stdout and stderr separately as byte streams. The client exited 0; stdout
matched `www/hello.txt` byte-for-byte. Offsets at the left of dump rows are
hexadecimal; bracketed annotation offsets are decimal. Each dump restarts at 0.
Dots in the ASCII gutter mark nonprintable bytes; every corresponding hex byte
is present, so these are not omission marks.

## Complete REQUEST: 55 bytes

```text
SEND frame bytes=55 version=1 type=1(REQUEST) flags=0x00 request_id=1 payload_length=47
  00000000  11 00 00 01 00 00 00 2f 01 00 0a 2f 68 65 6c 6c  |......./.../hell|
  00000010  6f 2e 74 78 74 03 04 00 03 2a 2f 2a 03 00 0e 6c  |o.txt....*/*...l|
  00000020  6f 63 61 6c 68 6f 73 74 3a 39 30 30 30 05 00 07  |ocalhost:9000...|
  00000030  62 63 75 72 6c 2f 31                             |bcurl/1|
  [8] method=1 GET; [9..10] path_length=10; [11..20] path='/hello.txt'
  [21] header_count=3
  [22..27] accept (ID 4, value_length=3) = '*/*'
  [28..44] host (ID 3, value_length=14) = 'localhost:9000'
  [45..54] user-agent (ID 5, value_length=7) = 'bcurl/1'
```

Byte 0 is `11`: version 1 in the high nibble and REQUEST type 1 in the low
nibble. Byte 1 is zero flags; bytes 2-3 are ID 1; bytes 4-7 declare
**47 payload bytes**, excluding the fixed 8-byte header. Method 1
is GET, the path is the ten UTF-8 bytes `/hello.txt`, and there is no body.
The three headers use dictionary IDs 4, 3, and 5 in decoded-name order;
each ID is followed by a two-byte value length and the exact value bytes.

## Complete RESPONSE: 145 bytes

```text
RECV frame bytes=145 version=1 type=2(RESPONSE) flags=0x00 request_id=1 payload_length=137
  00000000  12 00 00 01 00 00 00 89 00 c8 07 08 00 08 6e 6f  |..............no|
  00000010  2d 73 74 6f 72 65 07 00 0a 6b 65 65 70 2d 61 6c  |-store...keep-al|
  00000020  69 76 65 02 00 02 32 30 01 00 19 74 65 78 74 2f  |ive...20...text/|
  00000030  70 6c 61 69 6e 3b 20 63 68 61 72 73 65 74 3d 75  |plain; charset=u|
  00000040  74 66 2d 38 09 00 14 32 30 32 36 2d 31 30 2d 30  |tf-8...2026-10-0|
  00000050  38 54 31 38 3a 30 39 3a 33 38 5a 0a 00 14 32 30  |8T18:09:38Z...20|
  00000060  32 36 2d 31 30 2d 30 38 54 31 36 3a 35 30 3a 34  |26-10-08T16:50:4|
  00000070  39 5a 06 00 08 62 73 65 72 76 65 2f 31 48 65 6c  |9Z...bserve/1Hel|
  00000080  6c 6f 20 66 72 6f 6d 20 42 48 54 54 50 2f 31 21  |lo from BHTTP/1!|
  00000090  0a                                               |.|
  [8..9] status=200
  [10] header_count=7
  [11..21] cache-control (ID 8, value_length=8) = 'no-store'
  [22..34] connection (ID 7, value_length=10) = 'keep-alive'
  [35..39] content-length (ID 2, value_length=2) = '20'
  [40..67] content-type (ID 1, value_length=25) = 'text/plain; charset=utf-8'
  [68..90] date (ID 9, value_length=20) = '2026-10-08T18:09:38Z'
  [91..113] last-modified (ID 10, value_length=20) = '2026-10-08T16:50:49Z'
  [114..124] server (ID 6, value_length=8) = 'bserve/1'
  body offset=125 length=20 opaque bytes
```

Byte 0 is `12`: version 1 and RESPONSE type 2. Flags are zero; the echoed
ID is 1. The declared payload is **137 bytes**. At offsets 8-9,
`00 c8` is status **200**; offset 10 is the count of seven response headers.
The annotations identify every numeric name ID, value length, and value.

The opaque body begins at **decimal offset 125 (hex 7d)**,
occupies offsets 125-144, and contains **20 bytes**,
agreeing with `content-length`. Its byte representation is `b'Hello from BHTTP/1!\n'`.
The request's three names and response's seven names together use all ten
dictionary entries. `connection: keep-alive` permits another sequential
request on this connection; the one-resource CLI closes after this response.
The displayed `date` and `last-modified` are the actual UTC generation and
file-modification timestamps observed during capture, not fixed example values.
