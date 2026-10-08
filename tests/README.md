# Planned verification

Phase 1 contains no implemented protocol tests. Phase 2 will use `unittest`,
temporary document roots, and loopback sockets without third-party packages.

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
  and symlinks/junctions pointing outside the root where the OS supports them.
- Client stdout/stderr separation, useful verbose dumps, exit statuses,
  and exactly one TCP connection with no automatic reconnect or redirect.
- Run the relevant checks on both Windows and Linux before final submission.
