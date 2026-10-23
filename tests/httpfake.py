"""Tiny local HTTP server for download tests (no internet needed)."""

from __future__ import annotations

import socket
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer


class FakeServer:
    """Serves `files` (path -> bytes) on 127.0.0.1.

    honor_range: answer `Range: bytes=N-` with 206; otherwise always 200 + full body.
    fail_after: drop the connection after this many body bytes, once, then reset to None.
    requests: log of {"path", "range"} dicts, one per request.
    """

    def __init__(self, files: dict[str, bytes], *, honor_range: bool = True) -> None:
        self.files = files
        self.honor_range = honor_range
        self.fail_after: int | None = None
        self.requests: list[dict[str, str | None]] = []
        outer = self

        class Handler(BaseHTTPRequestHandler):
            def log_message(self, format: str, *args: object) -> None:
                pass

            def do_GET(self) -> None:
                rng = self.headers.get("Range")
                outer.requests.append({"path": self.path, "range": rng})
                body = outer.files.get(self.path)
                if body is None:
                    self.send_error(404)
                    return
                start = 0
                if rng and outer.honor_range and rng.startswith("bytes="):
                    start = int(rng[len("bytes=") :].split("-")[0])
                    self.send_response(206)
                    self.send_header("Content-Range", f"bytes {start}-{len(body) - 1}/{len(body)}")
                else:
                    self.send_response(200)
                payload = body[start:]
                self.send_header("Content-Length", str(len(payload)))
                self.end_headers()
                if outer.fail_after is not None:
                    n, outer.fail_after = outer.fail_after, None
                    self.wfile.write(payload[:n])
                    self.wfile.flush()
                    self.connection.shutdown(socket.SHUT_RDWR)
                    return
                self.wfile.write(payload)

        self._httpd = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        self._thread = threading.Thread(target=self._httpd.serve_forever, daemon=True)

    @property
    def base_url(self) -> str:
        return f"http://127.0.0.1:{self._httpd.server_address[1]}"

    def start(self) -> FakeServer:
        self._thread.start()
        return self

    def stop(self) -> None:
        self._httpd.shutdown()
        self._httpd.server_close()
