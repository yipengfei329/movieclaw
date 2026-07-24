"""TMDB v3 API 的最小异步客户端。

认证方式（对齐 2026-07 的 TMDB 官方文档）
----------------------------------------
- 官方推荐：v4 API Read Access Token（JWT 格式，"eyJ" 开头），走
  ``Authorization: Bearer`` 请求头；
- 兼容方式：v3 API Key（32 位十六进制），走 ``api_key`` 查询参数。
本客户端按密钥形态自动识别，两种都支持——用户从 TMDB 后台复制哪个都能用。

网络健壮性
----------
与 movieclaw_tracker.http 同款策略：httpx 异步客户端 + tenacity 对瞬时异常
（超时/连接失败）指数退避重试 + aiolimiter 漏桶限流（TMDB 官方限流约
50 req/s，这里限得更保守，发现页整页也只有不到 20 个请求且有缓存）。
"""

from __future__ import annotations

import logging
from typing import Any

import httpx
from aiolimiter import AsyncLimiter
from tenacity import (
    retry,
    retry_if_exception_type,
    stop_after_attempt,
    wait_exponential_jitter,
)

logger = logging.getLogger("movieclaw_media.tmdb")

DEFAULT_API_BASE_URL = "https://api.themoviedb.org/3"
DEFAULT_IMAGE_BASE_URL = "https://image.tmdb.org/t/p"

# 仅对这些瞬时网络异常重试；4xx/5xx 状态码属于确定性结果，不重试
_TRANSIENT_EXCEPTIONS = (
    httpx.TimeoutException,
    httpx.ConnectError,
    httpx.ReadError,
    httpx.RemoteProtocolError,
)


class TmdbError(Exception):
    """TMDB 请求失败的基类。message 面向最终用户展示，必须是可读中文。"""


class TmdbNotConfiguredError(TmdbError):
    """尚未配置 TMDB API Key。"""


class TmdbAuthError(TmdbError):
    """API Key 无效或被 TMDB 拒绝。"""


class TmdbNotFoundError(TmdbError):
    """请求的条目在 TMDB 中不存在。"""


class TmdbNetworkError(TmdbError):
    """网络层面无法连通 TMDB（连接失败/超时/熔断）。

    与"TMDB 可达但返回错误"区分开：API 层据此产出结构化的
    UPSTREAM_UNREACHABLE 错误，引导用户去「设置 → 网络」配置代理/镜像。
    """


class TmdbClient:
    """TMDB HTTP 客户端：只负责「带认证地发 GET 并把错误翻译成中文」。

    榜单编排、字段映射等业务逻辑一律放在 service.py，保持客户端可被
    任何用途复用（后续订阅匹配、搜索联想都会用到它）。
    """

    def __init__(
        self,
        api_key: str,
        *,
        base_url: str = DEFAULT_API_BASE_URL,
        timeout: float = 15.0,
        max_attempts: int = 3,
        transport: httpx.AsyncBaseTransport | None = None,
    ) -> None:
        self._base_url = base_url.rstrip("/")
        # 重试档位：后台任务用默认 3 次；发现页等用户在等的交互路径应传更小
        # 的 timeout/max_attempts，配合出口层熔断把失败反馈压到秒级
        self._max_attempts = max(1, max_attempts)
        # v4 Read Access Token 是 JWT（"eyJ" 开头），走 Bearer 头；
        # 其余按 v3 API Key 处理，走 api_key 查询参数
        self._use_bearer = api_key.startswith("eyJ")
        self._api_key = api_key
        headers = {"Accept": "application/json"}
        if self._use_bearer:
            headers["Authorization"] = f"Bearer {api_key}"
        # transport 仅供测试注入 MockTransport，生产环境走默认网络栈
        self._client = httpx.AsyncClient(
            timeout=httpx.Timeout(timeout), headers=headers, transport=transport
        )
        self._limiter = AsyncLimiter(20, 1)

    async def get(self, path: str, params: dict[str, Any] | None = None) -> dict[str, Any]:
        """GET 一个 TMDB 端点并返回 JSON；所有失败都抛 TmdbError 系异常。"""
        query: dict[str, Any] = dict(params or {})
        if not self._use_bearer:
            query["api_key"] = self._api_key
        url = f"{self._base_url}/{path.lstrip('/')}"
        try:
            response = await self._request(url, query)
        except httpx.HTTPError as exc:
            logger.warning("TMDB 请求失败：%s %s（%s）", path, type(exc).__name__, exc)
            raise TmdbNetworkError(
                "无法连通 TMDB（api.themoviedb.org）。所在网络可能无法直连，"
                "请到「设置 → 网络」配置代理或镜像地址，并用连通性测试验证"
            ) from exc

        if response.status_code == 401:
            raise TmdbAuthError("TMDB API Key 无效或已被禁用，请检查 TMDB_API_KEY 配置")
        if response.status_code == 404:
            raise TmdbNotFoundError("TMDB 中不存在该条目")
        if response.status_code >= 400:
            logger.warning("TMDB 返回异常状态：%s -> HTTP %s", path, response.status_code)
            raise TmdbError(f"TMDB 服务返回异常状态码 {response.status_code}，请稍后重试")
        return response.json()

    async def _request(self, url: str, params: dict[str, Any]) -> httpx.Response:
        # 重试次数随实例配置，故装饰器在调用期构造（与 movieclaw_tracker.http 同款写法）
        @retry(
            stop=stop_after_attempt(self._max_attempts),
            wait=wait_exponential_jitter(initial=1, max=8, jitter=1),
            retry=retry_if_exception_type(_TRANSIENT_EXCEPTIONS),
            reraise=True,
        )
        async def _do() -> httpx.Response:
            async with self._limiter:
                return await self._client.get(url, params=params)

        return await _do()

    async def aclose(self) -> None:
        """关闭底层连接池（应用关闭时由 lifespan 调用）。"""
        await self._client.aclose()
