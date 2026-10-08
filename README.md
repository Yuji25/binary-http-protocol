# Binary HTTP Protocol

**BHTTP/1** is a small HTTP-like binary file protocol over TCP for a Network
Architecture course project. It includes both a synchronous file server and
a single-connection client. Python **3.10+**, standard library only; no
installation or internet access is needed to run the programs or tests.

## Submission artifacts

- [Protocol specification](docs/spec.md) - authoritative wire format.
- [Two-page specification PDF](docs/spec.pdf) - the same normative content.
- [Annotated complete request and response](docs/hexdump.md) - captured from
  the actual programs serving `/hello.txt`.

## Run

From the repository root, start the server and client in separate terminals.
On Linux, the launchers are stored in Git with executable mode `100755`:

```sh
./bserve ./www 9000
./bcurl -v localhost:9000/index.html
```

The equivalent module commands work on Windows and Linux:

```text
python -m bhttp.server ./www 9000
python -m bhttp.client localhost:9000/index.html
python -m bhttp.client -v localhost:9000/hello.txt
```

Windows PowerShell also supports the small command wrappers:

```text
.\bserve.cmd .\www 9000
.\bcurl.cmd -v localhost:9000/index.html
```

The server binds IPv4 loopback `127.0.0.1` by default; `--bind IPV4` changes
the address. The client accepts `HOST:PORT/PATH`, chooses one resolved IPv4
endpoint, and makes one connect attempt, with no fallback or reconnect.
Its local socket timeout is 10 seconds. Stop the server with Ctrl+C.

Body bytes alone go to stdout. `-v` writes complete frame hex dumps, byte
offsets, fields, header IDs/values, and body annotations to stderr, including
unknown frames that are skipped. For binary redirection, use a shell that
preserves native stdout bytes, such as Windows Command Prompt.

Client exit codes: **0** for valid 2xx/3xx; **1** for valid 4xx/5xx (error body
still written); **2** for usage, transport, protocol, output failure, or
interruption. Server exit codes: **0** on Ctrl+C, **2** for usage/startup failure.

## Protocol summary

Each message has an 8-byte fixed header: packed version/type, flags, a
16-bit request ID, and a 32-bit payload length, all integers big-endian.
Ten header names use numeric IDs; other names use a length-prefixed literal
form. Explicit payload lengths allow unknown version-1 frame types to be
skipped while preserving the next frame boundary.

GET requests receive one complete response with status, headers, and opaque
file bytes. The server accepts clients sequentially and keeps each connection
open for further requests, including after recoverable 400/404/500 responses.
Fatal framing errors close the connection after a best-effort 400. Resolved
paths must remain under the locally administered document root; only regular
files are served. `/` and directories return 404, with no implicit index/listing.
Bodies are capped at 8 MiB; full validation and limits are in the specification.

## Layout

```text
bhttp/             protocol.py, server.py, client.py
bserve, bcurl      executable launchers; Windows .cmd wrappers alongside
docs/              spec.md, spec.pdf, hexdump.md
tests/             unittest suite, independent wire peer, CLI smoke harness
www/               index.html, hello.txt
```

## Verify

```text
python -m compileall -q bhttp tests
python -m unittest discover -s tests -v
python tests/smoke.py
```

Tests use temporary roots and local ephemeral ports. Independent peers build
and parse raw bytes without the project codec. Detailed coverage and platform
notes are in [tests/README.md](tests/README.md).

Runtime verification was performed on Windows with Python 3.13. Linux runtime
testing remains outstanding; executable-mode checks do not establish it.
The real symlink test skips when Windows denies creation privilege.
