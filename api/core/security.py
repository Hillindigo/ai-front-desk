"""Phase I I1-E2：安全中间件（纯 ASGI，避免破坏 SSE 流式）。

- SecurityHeadersMiddleware：注入 X-Content-Type-Options / Referrer-Policy /
  X-Frame-Options；生产模式追加 HSTS；CSP 默认不注入（避免破坏 Phase H 既有
  内联脚本页面），仅在显式配置 CSP_POLICY 时注入。
- TrustedHostMiddleware：仅当设置 TRUSTED_HOSTS 时启用宿主校验。
- RateLimitMiddleware：进程内滑动窗口限流（纯 ASGI 预检，不缓冲响应体，
  不影响 SSE 流式）；keyed by client IP，阈值与窗口可用环境变量覆盖。

用纯 ASGI 而非 BaseHTTPMiddleware：Starlette 的 BaseHTTPMiddleware 包裹会
干预流式/SSE 响应，本项目 turns 端点依赖 SSE 持续输出，故改为手写 ASGI。
"""

from __future__ import annotations

import json
import time
import threading
from collections import defaultdict, deque
from typing import Dict, List, Optional

from starlette.datastructures import MutableHeaders


# ---------------------------------------------------------------------------
# Security headers
# ---------------------------------------------------------------------------

class SecurityHeadersMiddleware:
    """向每个 HTTP 响应注入安全响应头。"""

    def __init__(self, app, *, base_headers: Optional[Dict[str, str]] = None,
                 production: bool = False, csp_policy: Optional[str] = None):
        self.app = app
        self.headers: List[tuple] = [
            ("x-content-type-options", "nosniff"),
            ("referrer-policy", "strict-origin-when-cross-origin"),
            ("x-frame-options", "DENY"),
        ]
        if production:
            self.headers.append(("strict-transport-security",
                                 "max-age=31536000; includeSubDomains"))
        if csp_policy:
            self.headers.append(("content-security-policy", csp_policy))

    async def __call__(self, scope, receive, send):
        if scope["type"] != "http":
            return await self.app(scope, receive, send)

        async def send_wrapper(message):
            if message["type"] == "http.response.start":
                message.setdefault("headers", [])
                headers = MutableHeaders(scope=message)
                for key, value in self.headers:
                    headers.append(key, value)
            await send(message)

        await self.app(scope, receive, send_wrapper)


# ---------------------------------------------------------------------------
# 基础限流（进程内滑动窗口）
# ---------------------------------------------------------------------------

class RateLimitMiddleware:
    """纯 ASGI 限流：请求进入时预检，超限返回 429，不缓冲响主体。"""

    _ACTIVE: "List[RateLimitMiddleware]" = []

    def __init__(self, app, *, max_requests: int = 600, window_seconds: int = 60,
                 enabled: bool = True):
        self.app = app
        self.max_requests = max_requests
        self.window = window_seconds
        self.enabled = enabled
        self._hits: Dict[str, deque] = defaultdict(deque)
        self._lock = threading.Lock()
        RateLimitMiddleware._ACTIVE.append(self)

    def _allow(self, key: str) -> bool:
        now = time.time()
        with self._lock:
            dq = self._hits[key]
            while dq and now - dq[0] > self.window:
                dq.popleft()
            if len(dq) >= self.max_requests:
                return False
            dq.append(now)
            return True

    def clear(self) -> None:
        with self._lock:
            self._hits.clear()

    async def __call__(self, scope, receive, send):
        if scope["type"] != "http":
            return await self.app(scope, receive, send)
        if not self.enabled:
            return await self.app(scope, receive, send)

        client = scope.get("client")
        key = client[0] if client else "unknown"
        if not self._allow(key):
            body = json.dumps(
                {"code": "RATE_LIMITED", "message": "请求过于频繁，请稍后重试"},
                ensure_ascii=False,
            ).encode("utf-8")
            headers = [
                (b"content-type", b"application/json; charset=utf-8"),
                (b"content-length", str(len(body)).encode("ascii")),
                (b"x-content-type-options", b"nosniff"),
            ]
            await send({
                "type": "http.response.start",
                "status": 429,
                "headers": headers,
            })
            await send({"type": "http.response.body", "body": body})
            return
        await self.app(scope, receive, send)


def reset_rate_limiters() -> None:
    """清空所有已注册限流器的窗口状态（供测试在用例间调用）。"""
    for limiter in RateLimitMiddleware._ACTIVE:
        limiter.clear()
