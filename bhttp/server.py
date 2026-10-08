"""Synchronous persistent BHTTP/1 file server."""

import argparse
from datetime import datetime, timezone
import errno
import os
from pathlib import Path
import socket
import stat
import sys

from . import protocol as p


def timestamp(seconds: float | None = None) -> str:
    time = datetime.now(timezone.utc) if seconds is None else datetime.fromtimestamp(seconds, timezone.utc)
    return (f"{time.year:04d}-{time.month:02d}-{time.day:02d}T"
            f"{time.hour:02d}:{time.minute:02d}:{time.second:02d}Z")


def response_headers(body: bytes, content_type: str, *, close: bool = False,
                     modified: float | None = None) -> dict[str, str]:
    headers = {
        "content-type": content_type,
        "content-length": str(len(body)),
        "server": "bserve/1",
        "connection": "close" if close else "keep-alive",
        "cache-control": "no-store",
        "date": timestamp(),
    }
    if modified is not None:
        headers["last-modified"] = timestamp(modified)
    return headers


def error_response(status: int, request_id: int, *, close: bool = False) -> bytes:
    body = {400: b"Bad request.\n", 404: b"File not found.\n",
            500: b"Unable to serve file.\n"}[status]
    headers = response_headers(body, "text/plain; charset=utf-8", close=close)
    return p.encode_response(status, headers, body, request_id)


def serve_file(root: Path, request: p.Request, request_id: int) -> bytes:
    try:
        decoded = p.validate_path(request.path)
        candidate = root.joinpath(*decoded[1:].split("/")).resolve()
        try:
            candidate.relative_to(root)
        except ValueError as exc:
            raise p.MalformedMessage("resolved path escapes document root") from exc
        if decoded == "/" or decoded.endswith("/"):
            return error_response(404, request_id)
        if not stat.S_ISREG(candidate.stat().st_mode):
            return error_response(404, request_id)
        # Snapshot before sending any bytes; do not trust a possibly stale file size.
        with candidate.open("rb") as source:
            body = source.read(p.MAX_BODY_BYTES + 1)
            modified = os.fstat(source.fileno()).st_mtime
        if len(body) > p.MAX_BODY_BYTES:
            return error_response(500, request_id)
        suffix = candidate.suffix.lower()
        content_type = {".html": "text/html; charset=utf-8",
                        ".htm": "text/html; charset=utf-8",
                        ".txt": "text/plain; charset=utf-8"}.get(suffix, "application/octet-stream")
        try:
            headers = response_headers(body, content_type, modified=modified)
        except (ValueError, OverflowError, OSError):
            return error_response(500, request_id)
        return p.encode_response(200, headers, body, request_id)
    except (p.MalformedMessage, ValueError, RuntimeError):
        return error_response(400, request_id)
    except (FileNotFoundError, NotADirectoryError):
        return error_response(404, request_id)
    except OSError as exc:
        status = 400 if exc.errno in (errno.ELOOP, errno.ENAMETOOLONG, errno.EINVAL) else 500
        return error_response(status, request_id)
    except OverflowError:
        return error_response(500, request_id)


def handle_connection(sock: socket.socket, root: Path) -> None:
    while True:
        header = None
        try:
            header = p.read_frame_header(sock)
            p.validate_frame_header(header, p.TYPE_REQUEST)
            if header.unknown:
                p.skip_payload(sock, header.payload_length)
                continue
            payload = p.read_exact(sock, header.payload_length)
            try:
                if header.flags != p.FLAGS_NONE:
                    raise p.MalformedMessage("nonzero request flags")
                request = p.decode_request(payload)
                response = serve_file(root, request, header.request_id)
            except p.MalformedMessage:
                response = error_response(400, header.request_id)
            sock.sendall(response)
        except p.CleanEOF:
            return
        except (p.FatalFrameError, p.TruncatedRead):
            request_id = header.diagnostic_id if header is not None else 0
            try:
                sock.sendall(error_response(400, request_id, close=True))
            except OSError:
                pass
            return
        except OSError:
            return


def port_number(value: str) -> int:
    try:
        number = int(value)
    except ValueError as exc:
        raise argparse.ArgumentTypeError("port must be an integer") from exc
    if not 1 <= number <= 65535:
        raise argparse.ArgumentTypeError("port must be in 1..65535")
    return number


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="bserve", description=__doc__)
    parser.add_argument("root", metavar="ROOT", type=Path)
    parser.add_argument("port", metavar="PORT", type=port_number)
    parser.add_argument("--bind", default="127.0.0.1", metavar="IPV4",
                        help="IPv4 bind address (default: 127.0.0.1)")
    args = parser.parse_args(argv)
    try:
        root = args.root.resolve(strict=True)
        if not root.is_dir():
            parser.error("ROOT must be an existing directory")
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as listener:
            listener.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
            listener.bind((args.bind, args.port))
            listener.listen()
            print(f"bserve listening on {args.bind}:{args.port}", file=sys.stderr, flush=True)
            while True:
                connection, _ = listener.accept()
                with connection:
                    handle_connection(connection, root)
    except KeyboardInterrupt:
        return 0
    except (OSError, ValueError, RuntimeError) as exc:
        print(f"bserve: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
