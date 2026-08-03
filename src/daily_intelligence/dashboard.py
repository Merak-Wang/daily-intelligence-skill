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
    """处理：返回本地监控页面静态资源目录。
    输入：
    - 无显式业务参数：不接收参数；根据当前模块位置定位仓库内受控的 assets 目录。
    输出：指向“返回本地监控页面静态资源目录”所生成、定位或确认产物的本地路径。
    """
    return project_root() / "assets" / "monitor"


def _json_bytes(payload: object) -> bytes:
    """处理：把对象编码为可直接返回的 UTF-8 JSON 字节。
    输入：
    - ``payload``：上游传入的结构化对象；函数只读取处理说明列出的受支持字段。
    输出：受大小边界约束的字节内容，可直接写入文件或 HTTP 响应。
    """
    return json.dumps(payload, ensure_ascii=False, separators=(",", ":")).encode(
        "utf-8"
    )


def _handler_factory(
    data_dir: Path,
    assets: Path,
    *,
    quiet: bool,
) -> type[BaseHTTPRequestHandler]:
    """处理：创建绑定数据目录与配置的本地监控 HTTP 处理器。
    输入：
    - ``data_dir``：当前运行的唯一数据根；所有状态和版本化产物都必须位于其中。
    - ``assets``：监控 Web UI 的静态资源目录；HTTP 处理器只允许从该根目录读取。
    - ``quiet``：是否抑制正常的本地监控 HTTP 访问日志。
    输出：封装“创建绑定数据目录与配置的本地监控 HTTP 处理器”业务结果的 ``type[BaseHTTPRequestHan
      dler]`` 对象；调用方据此继续相邻阶段或识别无结果状态。
    """
    class MonitorHandler(BaseHTTPRequestHandler):
        """处理：路由本地监控的静态资产、快照、健康状态和刷新请求。
        输入：
        - 无显式业务参数：不声明额外构造字段；该定义以 ``BaseHTTPRequestHandler`` 为基础，
          通过类成员承担“路由本地监控的静态资产、快照、健康状态和刷新请求”职责。
        输出：构造后的 ``MonitorHandler`` 实例或枚举定义；其字段和方法共同承担上述职责。
        """
        server_version = "DailyIntelligenceMonitor/2.0"

        def _send(
            self,
            content: bytes,
            content_type: str,
            status: HTTPStatus = HTTPStatus.OK,
        ) -> None:
            """处理：写入状态码、响应头和字节正文。
            输入：
            - ``content``：待编码、解析或写入的原始内容；边界和可信级别由当前函数说明。
            - ``content_type``：HTTP 内容类型或待上传文件 MIME 类型；用于解析、校验和响应头。
            - ``status``：当前操作或来源状态；值必须属于对应的显式状态模型。
            输出：不返回新数据；完成“写入状态码、响应头和字节正文”，
              副作用限于该处理声明的受控对象或产物。
            """
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
            """处理：把对象编码为 JSON 后发送 HTTP 响应。
            输入：
            - ``payload``：上游传入的结构化对象；函数只读取处理说明列出的受支持字段。
            - ``status``：当前操作或来源状态；值必须属于对应的显式状态模型。
            输出：不返回新数据；完成“把对象编码为 JSON 后发送 HTTP 响应”，
              副作用限于该处理声明的受控对象或产物。
            """
            self._send(_json_bytes(payload), _CONTENT_TYPES[".json"], status)

        def _serve_file(self, path: Path) -> None:
            """处理：校验静态资源路径后读取文件并返回正确内容类型。
            输入：
            - ``path``：当前函数要读取、校验或写入的本地文件路径。
            输出：不返回新数据；完成“校验静态资源路径后读取文件并返回正确内容类型”，
              副作用限于该处理声明的受控对象或产物。
            """
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
            """处理：复用监控 GET 路由校验并仅返回状态与响应头，不发送正文。
            输入：
            - 无显式业务参数：不接收额外业务参数；
              从当前实例读取“复用监控 GET 路由校验并仅返回状态与响应头，不发送正文”所需状态；
              实现会明确读取属性 do_GET。
            输出：不返回新数据；完成“复用监控 GET 路由校验并仅返回状态与响应头，不发送正文”，
              副作用限于该处理声明的受控对象或产物。
            """
            self.do_GET()

        def do_GET(self) -> None:  # noqa: N802
            """处理：按受控路由返回监控快照、健康状态或白名单内的静态资源。
            输入：
            - 无显式业务参数：不接收额外业务参数；
              从当前实例读取“按受控路由返回监控快照、健康状态或白名单内的静态资源”所需状态；
              实现会明确读取属性 _send_json、_serve_file、path。
            输出：不返回新数据；完成“按受控路由返回监控快照、健康状态或白名单内的静态资源”，
              副作用限于该处理声明的受控对象或产物。
            """
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
            """处理：按监控服务的日志格式记录请求。
            输入：
            - ``message_format``：BaseHTTPRequestHandler 提供的日志格式字符串。
            - ``*args``：HTTP 处理器用于填充日志格式字符串的参数。
            输出：不返回新数据；完成“按监控服务的日志格式记录请求”，
              副作用限于该处理声明的受控对象或产物。
            """
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
    """处理：创建只监听本地地址并绑定监控快照的线程式 HTTP 服务。
    输入：
    - ``data_dir``：当前运行的唯一数据根；所有状态和版本化产物都必须位于其中。
    - ``host``：本地监控服务器绑定的主机地址；远程地址需要显式授权。
    - ``port``：本地监控服务器监听端口；0 表示让操作系统分配可用端口。
    - ``quiet``：是否抑制正常的本地监控 HTTP 访问日志。
    输出：封装“创建只监听本地地址并绑定监控快照的线程式 HTTP 服务”业务结果的 ``ThreadingHTTPServ
      er`` 对象；调用方据此继续相邻阶段或识别无结果状态。
    """
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
    """处理：按固定间隔刷新监控快照，并保留上一份可用结果。
    输入：
    - ``stop_event``：后台刷新线程的停止事件；置位后结束下一轮等待和刷新。
    - ``config``：已校验的应用配置；提供时区、来源策略、并发限制、预算和输出选项。
    - ``data_dir``：当前运行的唯一数据根；所有状态和版本化产物都必须位于其中。
    - ``refresh_minutes``：监控后台线程两次刷新之间的分钟数。
    输出：不返回新数据；完成“按固定间隔刷新监控快照，并保留上一份可用结果”，
      副作用限于该处理声明的受控对象或产物。
    """
    while not stop_event.wait(refresh_minutes * 60):
        try:
            refresh_monitor(config, data_dir)
        except Exception:
            # 刷新失败时保留上一份可读快照；来源级错误继续写入 health.json，
            # 下一次成功刷新再清除连续失败状态。
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
    """处理：启动本地监控服务、可选刷新线程并保持进程运行。
    输入：
    - ``config``：已校验的应用配置；提供时区、来源策略、并发限制、预算和输出选项。
    - ``data_dir``：当前运行的唯一数据根；所有状态和版本化产物都必须位于其中。
    - ``host``：本地监控服务器绑定的主机地址；远程地址需要显式授权。
    - ``port``：本地监控服务器监听端口；0 表示让操作系统分配可用端口。
    - ``open_browser``：服务器启动后是否在默认浏览器打开监控页面。
    - ``allow_remote``：是否允许监控服务器绑定非环回地址；默认只允许本机访问。
    - ``refresh_minutes``：监控后台线程两次刷新之间的分钟数。
    输出：不返回新数据；完成“启动本地监控服务、可选刷新线程并保持进程运行”，
      副作用限于该处理声明的受控对象或产物。
    """
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
