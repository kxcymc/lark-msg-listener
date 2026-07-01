"""基于标准库 http.server 的本地 HTTP 服务。

选用 ThreadingHTTPServer 而非新增 Web 框架，与项目「最小依赖、三端通用」约束一致。
仅监听给定 host（默认 127.0.0.1），把请求转交 Router 分发。
"""
from __future__ import annotations

import logging
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

from .router import Router

logger = logging.getLogger(__name__)

# 读取请求体的上限，避免异常大的 body 拖垮本地服务（导出请求体很小）。
_MAX_BODY_BYTES = 2 * 1024 * 1024


def build_handler_class(router: Router) -> type[BaseHTTPRequestHandler]:
    """绑定 router 生成 BaseHTTPRequestHandler 子类。"""

    class _Handler(BaseHTTPRequestHandler):
        server_version = "msg-listener-dashboard-api"

        def _handle(self, method: str) -> None:
            try:
                length = int(self.headers.get("Content-Length") or 0)
            except ValueError:
                length = 0
            length = max(0, min(length, _MAX_BODY_BYTES))
            raw_body = self.rfile.read(length) if length else b""

            response = router.dispatch(method, self.path, raw_body)
            payload, content_type = response.to_bytes()

            self.send_response(response.status)
            self.send_header("Content-Type", content_type)
            self.send_header("Content-Length", str(len(payload)))
            for key, value in response.headers.items():
                self.send_header(key, value)
            self.end_headers()
            if method != "HEAD":
                self.wfile.write(payload)

        def do_GET(self) -> None:  # noqa: N802
            self._handle("GET")

        def do_POST(self) -> None:  # noqa: N802
            self._handle("POST")

        def log_message(self, fmt: str, *args) -> None:  # noqa: A003
            # 收敛到 loguru，避免直接打到 stderr。
            logger.debug("%s - %s", self.address_string(), fmt % args)

    return _Handler


def serve(router: Router, *, host: str, port: int) -> None:
    """阻塞式启动 HTTP 服务。"""
    httpd = ThreadingHTTPServer((host, port), build_handler_class(router))
    logger.info("dashboard API 监听 http://%s:%d", host, port)
    try:
        httpd.serve_forever()
    finally:
        httpd.server_close()
