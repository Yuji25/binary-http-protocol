# Binary HTTP Protocol

A Network Architecture course project implementing a small, project-specific
**BHTTP/1** binary protocol over TCP, with both a file server and a client.
The priorities are correctness, a clear wire specification, and a small Python
standard-library implementation that runs on Windows and Linux.

**Current stage: Phase 1 — protocol proposal and scaffold, awaiting approval.**
The codec, networking, file serving, and verbose frame display are not implemented.
There are no completed protocol tests or captured exchanges yet.

## Proposed design

- An 8-byte, big-endian frame header: packed version/type, flags, request ID,
  and payload length.
- One complete request frame and one complete response frame per exchange.
- Sequential requests on a persistent TCP connection; no multiplexing.
- Ten numeric header-name IDs, with a length-prefixed literal-name escape.
- Explicit size limits and rules for malformed input and unknown frame types.

See [the protocol draft](docs/protocol-draft.md) for normative byte layouts,
limits, error handling, path mapping, and the Phase 1 self-review.

## Layout

```text
bhttp/
    __init__.py
    protocol.py       # proposed constants; codec reserved for Phase 2
    server.py         # placeholder
    client.py         # placeholder
docs/
    protocol-draft.md
tests/
    README.md         # verification plan, not test results
www/
    index.html
    hello.txt
```

## Planned execution

From the repository root, using Python 3.10 or newer:

```text
python -m bhttp.server ./www 9000
python -m bhttp.client -v localhost:9000/index.html
```

These are planned module entry points, corresponding to the assignment's
`bserve` and `bcurl` commands. At this stage they exit with a clear
"not implemented" message. A later phase can add command wrappers if the grader
requires those exact executable names.

The eventual client writes body bytes to stdout and verbose diagnostics to
stderr. Tests will use the standard library's `unittest`:

```text
python -m unittest discover -s tests -v
```

The final submission will also include a concise approved protocol specification,
one annotated captured request/response exchange, and verified execution and
testing instructions. Those artifacts will be produced after implementation.
