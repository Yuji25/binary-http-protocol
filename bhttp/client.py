"""Single-connection BHTTP/1 command-line client."""

import argparse
import socket
import sys
from typing import TextIO

from . import protocol as p


def parse_target(target: str) -> tuple[str, int, str, str]:
    authority, slash, remainder = target.partition("/")
    host, colon, port_text = authority.rpartition(":")
    if not slash or not colon or not host or any(char in host for char in ":[]?#\\"):
        raise ValueError("target must be HOST:PORT/PATH (IPv4 or hostname)")
    if not port_text.isascii() or not port_text.isdecimal():
        raise ValueError("port must be decimal digits")
    port = int(port_text)
    if not 1 <= port <= 65535:
        raise ValueError("port must be in 1..65535")
    path = "/" + remainder
    p.validate_path(path)
    return host, port, path, authority


def exchange(sock: socket.socket, frame: bytes, *, verbose: bool = False,
             stderr: TextIO | None = None) -> p.Response:
    stream = sys.stderr if stderr is None else stderr
    sent = p.unpack_frame_header(frame[:8])
    if verbose:
        dump = p.FrameDumper("SEND", sent, stream)
        dump.feed(frame[8:])
        dump.finish()
        p.annotate_frame(sent, frame[8:], stream)
    sock.sendall(frame)
    while True:
        header = p.read_frame_header(sock)
        dump = p.FrameDumper("RECV", header, stream) if verbose else None
        try:
            p.validate_frame_header(header, p.TYPE_RESPONSE)
            if header.unknown:
                p.skip_payload(sock, header.payload_length, dump.feed if dump else None)
                if dump:
                    dump.finish("unknown frame skipped; flags and request ID ignored")
                continue
            payload = p.read_exact(sock, header.payload_length)
        except p.TruncatedRead as exc:
            if dump:
                dump.feed(exc.partial)
                dump.finish("truncated frame")
            raise
        except p.FatalFrameError:
            if dump:
                dump.finish("fatal frame header; payload not read")
            raise
        if dump:
            dump.feed(payload)
            dump.finish()
        response = p.decode_response(payload)
        p.validate_response_id(header, response, sent.request_id)
        if verbose:
            p.annotate_frame(header, payload, stream)
        return response


def fetch(host: str, port: int, frame: bytes, *, verbose: bool = False,
          stderr: TextIO | None = None, timeout: float = 10.0) -> p.Response:
    # Resolve first; AF_INET matches the default server. No create_connection()
    # or address fallback: only one socket and one connect attempt are allowed.
    endpoints = socket.getaddrinfo(host, port, socket.AF_INET, socket.SOCK_STREAM,
                                  socket.IPPROTO_TCP)
    if not endpoints:
        raise OSError("no usable IPv4 endpoint")
    family, socktype, proto, _, address = endpoints[0]
    with socket.socket(family, socktype, proto) as sock:
        sock.settimeout(timeout)
        sock.connect(address)
        return exchange(sock, frame, verbose=verbose, stderr=stderr)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="bcurl", description=__doc__)
    parser.add_argument("-v", "--verbose", action="store_true", help="dump every frame to stderr")
    parser.add_argument("target", metavar="HOST:PORT/PATH")
    args = parser.parse_args(argv)
    try:
        host, port, path, authority = parse_target(args.target)
        frame = p.encode_request(path, {"host": authority, "accept": "*/*",
                                        "user-agent": "bcurl/1"}, request_id=1)
        response = fetch(host, port, frame, verbose=args.verbose)
        sys.stdout.buffer.write(response.body)
        sys.stdout.buffer.flush()
        return 1 if response.status >= 400 else 0
    except (p.ProtocolError, p.CleanEOF, OSError, ValueError) as exc:
        print(f"bcurl: {exc or 'connection closed before a response'}", file=sys.stderr)
        return 2
    except KeyboardInterrupt:
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
