"""Run the actual CLI programs on loopback; no captured submission artifacts."""

from pathlib import Path
import socket
import subprocess
import sys


def main():
    repo = Path(__file__).resolve().parents[1]
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as reservation:
        reservation.bind(("127.0.0.1", 0))
        port = reservation.getsockname()[1]
    command = [sys.executable, "-m", "bhttp.server", "./www", str(port)]
    process = subprocess.Popen(command, cwd=repo, stdout=subprocess.DEVNULL,
                               stderr=subprocess.PIPE, text=True)
    try:
        ready = process.stderr.readline().strip()
        assert ready == f"bserve listening on 127.0.0.1:{port}", ready
        print(f"PASS python -m bhttp.server ./www {port} (ready)")
        for name in ("index.html", "hello.txt"):
            target = f"localhost:{port}/{name}"
            result = subprocess.run([sys.executable, "-m", "bhttp.client", target],
                                    cwd=repo, capture_output=True, timeout=15)
            assert result.returncode == 0, result.stderr
            assert result.stdout == (repo / "www" / name).read_bytes()
            assert result.stderr == b"", result.stderr
            print(f"PASS python -m bhttp.client {target} (exact {len(result.stdout)} body bytes)")
        target = f"localhost:{port}/hello.txt"
        verbose = subprocess.run([sys.executable, "-m", "bhttp.client", "-v", target],
                                 cwd=repo, capture_output=True, timeout=15)
        assert verbose.returncode == 0
        assert verbose.stdout == (repo / "www/hello.txt").read_bytes()
        assert b"SEND frame" in verbose.stderr and b"RECV frame" in verbose.stderr
        assert b"status=200" in verbose.stderr and b"body offset=" in verbose.stderr
        print(f"PASS python -m bhttp.client -v {target} (hex/annotations only on stderr)")
        missing = f"localhost:{port}/does-not-exist.txt"
        result = subprocess.run([sys.executable, "-m", "bhttp.client", "-v", missing],
                                cwd=repo, capture_output=True, timeout=15)
        assert result.returncode == 1
        assert result.stdout == b"File not found.\n"
        assert b"status=404" in result.stderr
        print(f"PASS python -m bhttp.client -v {missing} (404, exit 1)")
        result = subprocess.run([sys.executable, "bcurl", target], cwd=repo,
                                capture_output=True, timeout=15)
        assert result.returncode == 0 and result.stdout == (repo / "www/hello.txt").read_bytes()
        print("PASS python bcurl (launcher delegates to client)")
    finally:
        process.terminate()
        try:
            process.wait(timeout=5)
        except subprocess.TimeoutExpired:
            process.kill()
            process.wait(timeout=5)
        process.stderr.close()
        print("Server process cleaned up.")


if __name__ == "__main__":
    main()
