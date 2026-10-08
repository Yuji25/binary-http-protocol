# Binary HTTP Protocol

A Network Architecture course project implementing a small, project-specific
**BHTTP/1** binary protocol over TCP, with both a file server and a client.
The priorities are correctness, a clear wire specification, and a small Python
standard-library implementation that runs on Windows and Linux.

**Current stage: Phase 2 implemented.** The approved expanded specification is
[docs/protocol-draft.md](docs/protocol-draft.md). The final two-page specification
and annotated captured submission exchange are reserved for Phase 3.

## Wire design

- An 8-byte, big-endian frame header: packed version/type, flags, request ID,
  and payload length.
- One complete request frame and one complete response frame per exchange.
- Sequential requests on a persistent TCP connection; no multiplexing.
- Ten numeric header-name IDs, with a length-prefixed literal-name escape.
- Explicit size limits and rules for malformed input and unknown frame types.

The specification defines normative byte layouts, validation, limits, path
mapping, and connection lifetime. This is a project-specific protocol, not
textual HTTP. Both programs and all automated tests use only Python's standard
library. No installation or internet access is required.

## Layout

```text
bhttp/
    __init__.py
    protocol.py       # codec, validation, exact reads, skipping, hex display
    server.py         # synchronous persistent TCP file server
    client.py         # one-connect CLI, binary stdout, verbose stderr
docs/
    protocol-draft.md
tests/
    wire_peer.py      # independent wire oracle; no bhttp imports
    test_*.py         # codec, transport, server, client, interoperability
    smoke.py          # actual CLI process verification
www/
    index.html
    hello.txt
```

## Run

From the repository root, using Python 3.10 or newer:

```text
python -m bhttp.server ./www 9000
python -m bhttp.client localhost:9000/index.html
python -m bhttp.client -v localhost:9000/hello.txt
```

Run the server and client in separate terminals. The server binds IPv4 loopback
`127.0.0.1` by default. An optional `--bind IPV4` changes the listening address.
The client accepts `HOST:PORT/PATH`, resolves IPv4 addresses, chooses the first
endpoint, and makes one socket/connect attempt. It never retries another
address, reconnects, or follows a redirect. Its local socket timeout is 10 seconds.

The client writes only body bytes to `sys.stdout.buffer`. Verbose mode displays
every complete sent/received frame, including skipped unknown types, on stderr:
offsets, full hex bytes, fixed-header fields, header IDs/values, and body bounds.
Large unknown frames are displayed incrementally while being skipped in bounded
chunks. Full dumps can be large; there is no silent truncation.

For binary downloads, use a shell that preserves redirected native stdout,
such as Windows Command Prompt:

```text
python -m bhttp.client localhost:9000/image.bin > image.bin
```

Some shells can re-encode native redirected output. The client itself writes
exact binary bytes; the tests capture those bytes directly through subprocess pipes.

Tiny launchers delegate to the same modules. On Windows PowerShell:

```text
.\bserve.cmd .\www 9000
.\bcurl.cmd -v localhost:9000/index.html
```

On Linux, grant the extensionless scripts execute permission if necessary:

```sh
chmod +x bserve bcurl
./bserve ./www 9000
./bcurl -v localhost:9000/index.html
```

`python bserve ...` and `python bcurl ...` also work without executable bits.
Windows does not reliably record new executable bits in Git; no index changes
or installer are needed to use the modules or Windows wrappers.

## Supported behavior and limits

- GET only; binary-safe regular files. HTML/text types are explicit; other
  suffixes use `application/octet-stream`.
- All ten dictionary names are actually sent across a normal request and
  successful response. Well-formed literal custom headers are supported.
- The server handles one client at a time, accepts subsequent clients, and
  keeps each connection open for sequential requests.
- Fully consumed malformed requests return 400 and keep the connection usable.
  Missing paths/directories return 404. Read failures or oversized files return
  500. Fatal framing errors get a best-effort 400 and closure.
- Unknown version-1 frame types are skipped by their declared lengths, ignoring
  flags/IDs. Unsupported versions are fatal.
- Maximum global payload: 16 MiB; request/header block: 16 KiB; body: 8 MiB;
  path/value: 4096 bytes; literal name: 255 bytes; header count: 32.
- Strict exactly-once percent decoding and path-aware root containment;
  traversal, unsafe Windows aliases, and escaping symlinks/junctions are rejected.
  The document tree must be locally trusted against concurrent malicious
  replacement during file opening, as described in the approved specification.
- No directory listing or automatic `/index.html` for `/`. No request bodies,
  multiplexing, compression, TLS, authentication, or external dependencies.

## Exit codes

| Program | Code | Meaning |
| --- | --- | --- |
| Client | 0 | Valid 2xx/3xx response; body written |
| Client | 1 | Valid 4xx/5xx response; error body still written |
| Client | 2 | Usage, network, malformed response, output failure, or interruption |
| Server | 0 | Stopped with Ctrl+C |
| Server | 2 | Usage/startup failure |

## Verify

```text
python -m compileall -q bhttp tests
python -m unittest discover -s tests -v
python tests/smoke.py
```

Tests use local ephemeral ports and temporary file roots. The independent peer
constructs requests/responses and parses the opposite program's output without
calling our codec; the approved `/hello.txt` vector is checked byte-for-byte.
See [tests/README.md](tests/README.md) for coverage and platform notes.

Runtime verification was performed on Windows with Python 3.13. Linux execution
has not yet been verified. A real symlink test skips if Windows does not grant
creation privilege; path-aware containment is additionally tested independently
of that privilege. A real Windows directory junction escaping the root was
also separately verified: 400, then a valid 200 on the same connection.
Local test subprocesses require loopback socket permissions
when running in a restricted execution sandbox.

The locally provided `docs/references/` instructor files remain ignored and
untouched. The final condensed specification and captured annotated exchange
have not been produced.
