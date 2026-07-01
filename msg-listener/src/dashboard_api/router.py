"""路由分发与 Request/Response 契约。

所有 handler 的签名固定为 `handler(request: Request) -> Response`，
由 main.py 在路由表中登记 (method, path_pattern) -> handler。
path_pattern 支持 `:name` 命名段与结尾 `*` 通配（用于 media 子路径）。

handler 只需读取 request 并返回 Response，不关心 HTTP 细节；
统一的参数解析、404/405/500 在本模块处理。
"""
from __future__ import annotations

import json
import logging
import re
import traceback
from dataclasses import dataclass, field
from typing import Any, Callable
from urllib.parse import parse_qs, unquote, urlsplit

logger = logging.getLogger(__name__)


@dataclass
class Request:
    method: str
    path: str
    # 路径命名参数，如 /api/records/:id -> {"id": "..."}；通配 * 收敛到 "wildcard"
    params: dict[str, str] = field(default_factory=dict)
    # query string 解析结果，重复键取首值
    query: dict[str, str] = field(default_factory=dict)
    # 原始请求体字节
    raw_body: bytes = b""

    def query_int(self, key: str, default: int) -> int:
        try:
            return int(self.query.get(key, default))
        except (TypeError, ValueError):
            return default

    def json_body(self) -> dict[str, Any]:
        if not self.raw_body:
            return {}
        try:
            data = json.loads(self.raw_body.decode("utf-8"))
        except (json.JSONDecodeError, UnicodeDecodeError):
            return {}
        return data if isinstance(data, dict) else {}


@dataclass
class Response:
    status: int = 200
    # JSON 响应：传 body（可被 json 序列化的对象）。
    body: Any = None
    # 二进制响应（媒体、xlsx）：传 raw 与 content_type。
    raw: bytes | None = None
    content_type: str = "application/json; charset=utf-8"
    headers: dict[str, str] = field(default_factory=dict)

    @classmethod
    def json(cls, body: Any, status: int = 200) -> "Response":
        return cls(status=status, body=body)

    @classmethod
    def error(cls, status: int, message: str) -> "Response":
        return cls(status=status, body={"error": message})

    @classmethod
    def binary(
        cls, raw: bytes, content_type: str, *, headers: dict[str, str] | None = None
    ) -> "Response":
        return cls(status=200, raw=raw, content_type=content_type, headers=headers or {})

    def to_bytes(self) -> tuple[bytes, str]:
        """返回 (响应字节, content_type)。"""
        if self.raw is not None:
            return self.raw, self.content_type
        payload = json.dumps(self.body, ensure_ascii=False, default=str).encode("utf-8")
        return payload, self.content_type


Handler = Callable[[Request], Response]

# path_pattern 中的命名段：:name
_PARAM_RE = re.compile(r":([A-Za-z_][A-Za-z0-9_]*)")


def _compile_pattern(pattern: str) -> re.Pattern[str]:
    """把 /api/records/:id/media/* 编译成正则。

    :name 捕获单段（不含 /）；结尾 /* 捕获剩余整段路径到 wildcard 组。
    pattern 全部由 main.py 的路由表常量提供，仅含安全字符，无需通用转义。
    """
    wildcard = pattern.endswith("/*")
    base = pattern[:-2] if wildcard else pattern
    regex = _PARAM_RE.sub(lambda m: f"(?P<{m.group(1)}>[^/]+)", base)
    if wildcard:
        regex += r"/(?P<wildcard>.*)"
    return re.compile(f"^{regex}$")


@dataclass
class Route:
    method: str
    pattern: str
    handler: Handler
    regex: re.Pattern[str] = field(init=False)

    def __post_init__(self) -> None:
        self.regex = _compile_pattern(self.pattern)


class Router:
    def __init__(self) -> None:
        self._routes: list[Route] = []

    def add(self, method: str, pattern: str, handler: Handler) -> None:
        self._routes.append(Route(method.upper(), pattern, handler))

    def dispatch(self, method: str, raw_path: str, raw_body: bytes) -> Response:
        split = urlsplit(raw_path)
        path = split.path.rstrip("/") or "/"
        query = {k: v[0] for k, v in parse_qs(split.query).items()}

        matched_path = False
        for route in self._routes:
            m = route.regex.match(path)
            if not m:
                continue
            matched_path = True
            if route.method != method.upper():
                continue
            params = {k: unquote(v) for k, v in m.groupdict().items() if v is not None}
            request = Request(
                method=method.upper(),
                path=path,
                params=params,
                query=query,
                raw_body=raw_body,
            )
            try:
                return route.handler(request)
            except NotImplementedError:
                return Response.error(501, "端点尚未实现")
            except Exception as exc:  # noqa: BLE001
                logger.error("handler 异常 %s %s: %s", method, path, exc)
                logger.debug("%s", traceback.format_exc())
                return Response.error(500, "内部错误")

        if matched_path:
            return Response.error(405, "方法不允许")
        return Response.error(404, "未找到")
