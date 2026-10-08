# Verification

Run from the repository root with Python 3.10+ and no third-party packages:

```text
python -m compileall -q bhttp tests
python -m unittest discover -s tests -v
python tests/smoke.py
```

`test_protocol.py` covers the codec and fragmented/exact transport reads.
`test_server.py` sends independently generated raw bytes through TCP to the
real server connection handler and independently parses its responses.
`test_client.py` runs the actual client CLI against a separate reference peer,
inspects its raw request, constructs its response independently, checks binary
stdout, and checks that no second connection is queued. Mocked resolver/socket
tests additionally prove no fallback connect attempt even when resolution returns
multiple addresses and the first connect fails.

`wire_peer.py` is the independent oracle: no imports from `bhttp`, no codec
calls. Its dictionary, layouts, raw framing, cursor parsing, and hard-coded
22-byte `/hello.txt` request come directly from the approved specification.
It intentionally transmits some valid headers in noncanonical order and uses
literal known-name spellings, exercising the specified tolerant decoder.

`smoke.py` starts the actual server CLI, waits for its readiness line, runs the
actual client CLI for HTML/text/verbose/404 requests, compares body bytes to
the files, and always terminates the temporary server process. It verifies the
extensionless client launcher too. It does not save a final submission hexdump.

TCP tests use IPv4 loopback and ephemeral ports. Thread joins, socket timeouts,
and process timeouts bound tests; no sleeps are used to guess readiness.
Symlink tests skip when the OS denies creation privilege. Run the suite on Linux
as well before final submission; only Windows runtime results are currently
available. Restricted sandboxes may need permission for loopback subprocesses.

Coverage includes:

- Independent expected-byte fixtures for framing, numeric/literal headers,
  length boundaries, and deterministic header ordering.
- Exact reads with deliberately fragmented headers and payloads; clean EOF,
  partial header, partial payload, and zero-length unknown payloads.
- Unknown frames before and between exchanges, including ignored flags/IDs;
  skip in bounded chunks, then decode the next known frame correctly.
- Malformed lengths, header selectors, names, duplicates, values, UTF-8,
  methods, paths, flags, versions, IDs, and trailing request bytes.
- A recoverable 400 followed by a successful request on the same socket;
  fatal errors close the connection and oversized declarations do not cause
  oversized allocations.
- Multiple successful requests and a 404 on one connection with echoed IDs.
- Binary bodies containing NUL bytes and bytes that are not UTF-8; accurate
  content-length, response limits, and malformed response rejection.
- Raw/encoded traversal, backslashes, drive paths, directories, missing files,
  resolved escapes, and real symlinks when creation privilege is available.
- Client stdout/stderr separation, useful verbose dumps, exit statuses,
  and exactly one TCP connection with no automatic reconnect or redirect.
- Run the relevant checks on both Windows and Linux before final submission.

A real Windows directory-junction escape was also checked separately using a
temporary tree and the independent raw peer: the server returned 400 and then
200 for a valid request on that same connection. This manual platform check
is not counted as a portable unittest case.
