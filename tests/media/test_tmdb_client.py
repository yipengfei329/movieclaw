"""TmdbClient 的单元测试：双认证格式识别与错误翻译（MockTransport，不出网）。"""

from __future__ import annotations

import httpx
import pytest

from movieclaw_media.tmdb import TmdbAuthError, TmdbClient, TmdbError, TmdbNotFoundError

# 形态与真实密钥一致的假密钥
_V3_KEY = "0123456789abcdef0123456789abcdef"
_V4_TOKEN = "eyJhbGciOiJIUzI1NiJ9.fake.token"


def _client_capturing(api_key: str, captured: list[httpx.Request]) -> TmdbClient:
    def handler(request: httpx.Request) -> httpx.Response:
        captured.append(request)
        return httpx.Response(200, json={"ok": True})

    return TmdbClient(api_key, transport=httpx.MockTransport(handler))


async def test_v3_key_goes_to_query_param() -> None:
    """32 位十六进制按 v3 API Key 处理：拼 api_key 查询参数，不带 Bearer 头。"""
    captured: list[httpx.Request] = []
    client = _client_capturing(_V3_KEY, captured)
    await client.get("movie/popular", {"language": "zh-CN"})

    request = captured[0]
    assert f"api_key={_V3_KEY}" in str(request.url)
    assert "authorization" not in request.headers


async def test_v4_token_goes_to_bearer_header() -> None:
    """eyJ 开头的 JWT 按 v4 Read Access Token 处理：走 Authorization: Bearer 头。"""
    captured: list[httpx.Request] = []
    client = _client_capturing(_V4_TOKEN, captured)
    await client.get("movie/popular")

    request = captured[0]
    assert request.headers["authorization"] == f"Bearer {_V4_TOKEN}"
    assert "api_key" not in str(request.url)


@pytest.mark.parametrize(
    ("status", "exc_type"),
    [(401, TmdbAuthError), (404, TmdbNotFoundError), (500, TmdbError)],
)
async def test_error_status_translated_to_chinese_error(status: int, exc_type: type) -> None:
    """非 2xx 状态码翻译成对应的 TmdbError 子类，message 为可读中文。"""

    def handler(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(status, json={})

    client = TmdbClient(_V3_KEY, transport=httpx.MockTransport(handler))
    with pytest.raises(exc_type):
        await client.get("movie/popular")
