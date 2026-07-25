from __future__ import annotations

import json
import threading
import webbrowser
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any

from .config import AppConfig, project_root
from .monitor import refresh_monitor

_CONTENT_TYPES = {
    ".css": "text/css; charset=utf-8",
    ".html": "text/html; charset=utf-8",
    ".js": "text/javascript; charset=utf-8",
    ".json": "application/json; charset=utf-8",
}


def _asset_dir() -> Path:
    return project_root() / "assets" / "monitor"


def _json_bytes(payload: object) -> bytes:
    return json.dumps(payload, ensure_ascii=False, separators=(",", ":")).encode(
        "utf-8"
    )


def _handler_factory(
    data_dir: Path,
    assets: Path,
    *,
    quiet: bool,
) -> type[BaseHTTPRequestHandler]:
    class MonitorHandler(BaseHTTPRequestHandler):
        server_version = "DailyIntelligenceMonitor/2.0"

        def _send(
            self,
            content: bytes,
            content_type: str,
            status: HTTPStatus = HTTPStatus.OK,
        ) -> None:
            self.send_response(status)
            self.send_header("Content-Type", content_type)
            self.send_header("Content-Length", str(len(content)))
            self.send_header("Cache-Control", "no-store")
            self.send_header("X-Content-Type-Options", "nosniff")
            self.send_header("Referrer-Policy", "no-referrer")
            self.send_header(
                "Content-Security-Policy",
                (
                    "default-src 'self'; img-src 'self' https: data:; "
                    "style-src 'self'; script-src 'self'; connect-src 'self'; "
                    "object-src 'none'; base-uri 'none'; frame-ancestors 'none'"
                ),
            )
            self.end_headers()
            if self.command != "HEAD":
                self.wfile.write(content)

        def _send_json(
            self,
            payload: object,
            status: HTTPStatus = HTTPStatus.OK,
        ) -> None:
            self._send(_json_bytes(payload), _CONTENT_TYPES[".json"], status)

        def _serve_file(self, path: Path) -> None:
            try:
                resolved = path.resolve(strict=True)
                resolved.relative_to(assets.resolve())
            except (FileNotFoundError, ValueError):
                self._send_json(
                    {"error": "not_found"}, HTTPStatus.NOT_FOUND
                )
                return
            self._send(
                resolved.read_bytes(),
                _CONTENT_TYPES.get(resolved.suffix.lower(), "application/octet-stream"),
            )

        def do_HEAD(self) -> None:  # noqa: N802
            self.do_GET()

        def do_GET(self) -> None:  # noqa: N802
            route = self.path.split("?", 1)[0]
            if route in {"/", "/index.html"}:
                self._serve_file(assets / "index.html")
                return
            if route == "/app.js":
                self._serve_file(assets / "app.js")
                return
            if route == "/styles.css":
                self._serve_file(assets / "styles.css")
                return
            if route == "/api/snapshot":
                snapshot = data_dir / "monitor" / "snapshot.json"
                if not snapshot.exists():
                    self._send_json(
                        {
                            "error": "snapshot_not_found",
                            "message": "请先运行 daily-intel refresh-monitor",
                        },
                        HTTPStatus.NOT_FOUND,
                    )
                    return
                try:
                    payload = json.loads(snapshot.read_text(encoding="utf-8"))
                except (OSError, ValueError) as exc:
                    self._send_json(
                        {"error": "snapshot_invalid", "message": str(exc)},
                        HTTPStatus.INTERNAL_SERVER_ERROR,
                    )
                    return
                self._send_json(payload)
                return
            if route == "/api/health":
                self._send_json({"status": "ok", "read_only": True})
                return
            self._send_json({"error": "not_found"}, HTTPStatus.NOT_FOUND)

        def log_message(self, message_format: str, *args: Any) -> None:
            if not quiet:
                super().log_message(message_format, *args)

    return MonitorHandler


def create_monitor_server(
    data_dir: Path,
    host: str = "127.0.0.1",
    port: int = 8765,
    *,
    quiet: bool = False,
) -> ThreadingHTTPServer:
    assets = _asset_dir()
    missing = [
        name for name in ("index.html", "app.js", "styles.css") if not (assets / name).is_file()
    ]
    if missing:
        raise FileNotFoundError(
            f"Monitor frontend assets are missing from {assets}: {', '.join(missing)}"
        )
    return ThreadingHTTPServer(
        (host, port),
        _handler_factory(data_dir.resolve(), assets, quiet=quiet),
    )


def _refresh_loop(
    stop_event: threading.Event,
    config: AppConfig,
    data_dir: Path,
    refresh_minutes: int,
) -> None:
    while not stop_event.wait(refresh_minutes * 60):
        try:
            refresh_monitor(config, data_dir)
        except Exception:
            # A failed refresh keeps the last good snapshot readable. Source-level
            # failures remain visible in health.json and the next successful cycle
            # clears the failure streak.
            continue


def serve_monitor(
    config: AppConfig,
    data_dir: Path,
    *,
    host: str = "127.0.0.1",
    port: int = 8765,
    open_browser: bool = False,
    allow_remote: bool = False,
    refresh_minutes: int = 0,
) -> None:
    if host not in {"127.0.0.1", "localhost", "::1"} and not allow_remote:
        raise ValueError(
            "Refusing to expose the local intelligence desk beyond this machine. "
            "Pass --allow-remote only on a trusted network."
        )
    if refresh_minutes < 0:
        raise ValueError("--refresh-minutes must be zero or a positive integer")
    server = create_monitor_server(data_dir, host, port)
    actual_host, actual_port = server.server_address[:2]
    display_host = "127.0.0.1" if actual_host in {"0.0.0.0", "::"} else actual_host
    url = f"http://{display_host}:{actual_port}/"
    print(f"本地情报台已启动：{url}")
    stop_event = threading.Event()
    refresh_thread = None
    if refresh_minutes:
        refresh_thread = threading.Thread(
            target=_refresh_loop,
            args=(stop_event, config, data_dir, refresh_minutes),
            name="daily-intelligence-monitor-refresh",
            daemon=True,
        )
        refresh_thread.start()
    if open_browser:
        webbrowser.open(url)
    try:
        server.serve_forever(poll_interval=0.25)
    except KeyboardInterrupt:
        pass
    finally:
        stop_event.set()
        server.shutdown()
        server.server_close()
        if refresh_thread:
            refresh_thread.join(timeout=2)
