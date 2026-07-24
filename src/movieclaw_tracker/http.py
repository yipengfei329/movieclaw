from __future__ import annotations

import logging
from typing import Any

import httpx
from tenacity import (
    retry,
    retry_if_exception_type,
    stop_after_attempt,
    wait_exponential_jitter,
)

from movieclaw_net import egress_transport
from movieclaw_tracker.ratelimit import SiteRateLimiter, get_site_limiter

logger = logging.getLogger("movieclaw_tracker.http")

_DEFAULT_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/120.0.0.0 Safari/537.36"
    ),
    "Accept-Language": "zh-CN,zh;q=0.9,en;q=0.8",
}

_TRANSIENT_EXCEPTIONS = (
    httpx.ConnectTimeout,
    httpx.ReadTimeout,
    httpx.ConnectError,
    httpx.PoolTimeout,
)


class HttpClient:
    """纯 HTTP 传输层。httpx.AsyncClient 包装器，内置 tenacity 重试。

    不含任何站点业务逻辑，仅负责可靠地发送 HTTP 请求。
    """

    def __init__(
        self,
        *,
        timeout: float = 30.0,
        max_retries: int = 3,
        headers: dict[str, str] | None = None,
        cookies: dict[str, str] | None = None,
        http2: bool = False,
        site_id: str | None = None,
        min_request_interval: float | None = None,
        transport: httpx.AsyncBaseTransport | None = None,
    ) -> None:
        self._max_retries = max_retries
        # 每站限流器：给了 site_id 才启用（跨临时客户端按 site_id 全进程共享同一闸门）。
        # 未给 site_id（如单元测试直接构造）则不限流。间隔为 None 时用默认值。
        self._limiter: SiteRateLimiter | None = (
            get_site_limiter(site_id, min_request_interval) if site_id else None
        )
        # 统一出口：有 site_id 的正式客户端默认走 movieclaw_net 出口层
        # （标签 site:<id>，用户可在「设置 → 网络」按站点选择是否走代理）；
        # 测试注入的 transport 优先，未给 site_id 时保持直连行为
        if transport is None and site_id:
            transport = egress_transport(f"site:{site_id}", http2=http2)
        merged_headers = {**_DEFAULT_HEADERS, **(headers or {})}
        self._client = httpx.AsyncClient(
            timeout=httpx.Timeout(timeout),
            headers=merged_headers,
            cookies=cookies,
            http2=http2,
            follow_redirects=True,
            transport=transport,
        )

    @property
    def cookies(self) -> httpx.Cookies:
        return self._client.cookies

    @cookies.setter
    def cookies(self, value: dict[str, str]) -> None:
        self._client.cookies = httpx.Cookies(value)

    def set_header(self, key: str, value: str) -> None:
        """设置（或覆盖）一个默认请求头。

        用于 API-Key 等需要在认证后注入到后续所有请求的场景，
        例如 M-Team 的 ``x-api-key``。cookie 走 cookies 属性，
        非 cookie 的认证凭据走这里。
        """
        self._client.headers[key] = value

    async def get(self, url: str, **kwargs: Any) -> httpx.Response:
        return await self._request("GET", url, **kwargs)

    async def post(self, url: str, **kwargs: Any) -> httpx.Response:
        return await self._request("POST", url, **kwargs)

    async def download(self, url: str, **kwargs: Any) -> bytes:
        response = await self._request("GET", url, **kwargs)
        return response.content

    async def raw_request(self, method: str, url: str, **kwargs: Any) -> httpx.Response:
        """发送原始 HTTP 请求，不重试、不抛出状态码异常。

        适用于登录等需要自行检查响应状态码和内容的场景，
        调用方根据业务语义判断成功/失败，而非依赖 HTTP 状态码。
        """
        if self._limiter is not None:
            await self._limiter.acquire()
        return await self._client.request(method, url, **kwargs)

    async def raw_get(self, url: str, **kwargs: Any) -> httpx.Response:
        """raw_request 的 GET 快捷方式。"""
        return await self.raw_request("GET", url, **kwargs)

    async def raw_post(self, url: str, **kwargs: Any) -> httpx.Response:
        """raw_request 的 POST 快捷方式。"""
        return await self.raw_request("POST", url, **kwargs)

    async def _request(self, method: str, url: str, **kwargs: Any) -> httpx.Response:
        @retry(
            stop=stop_after_attempt(self._max_retries),
            wait=wait_exponential_jitter(initial=1, max=10, jitter=2),
            retry=retry_if_exception_type(_TRANSIENT_EXCEPTIONS),
            before_sleep=lambda rs: logger.warning(
                "Retrying %s %s (attempt %d)", method, url, rs.attempt_number
            ),
            reraise=True,
        )
        async def _do() -> httpx.Response:
            # 闸门放在每次尝试内部：重试发出的请求同样被节流，不会绕过限流
            if self._limiter is not None:
                await self._limiter.acquire()
            response = await self._client.request(method, url, **kwargs)
            response.raise_for_status()
            return response

        return await _do()

    async def close(self) -> None:
        await self._client.aclose()

    async def __aenter__(self) -> HttpClient:
        return self

    async def __aexit__(self, *args: Any) -> None:
        await self.close()
