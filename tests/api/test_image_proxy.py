"""开放域名图片代理测试：SSRF 防护、上游请求头、重定向逐跳校验、类型与体积边界。"""

from __future__ import annotations

import httpx
import pytest

from movieclaw_api.exceptions import BadRequestException, UpstreamServiceException
from movieclaw_api.services.image_proxy import ImageProxy

# 测试用静态 DNS：公网图床 / 解析到内网的恶意域名
_DNS: dict[str, list[str]] = {
    "img3.doubanio.com": ["93.184.216.34"],
    "image.tmdb.org": ["93.184.216.34"],
    "img.example-host.com": ["93.184.216.34"],
    "intranet.evil.example": ["10.0.0.8"],
    "mixed.evil.example": ["93.184.216.34", "192.168.1.5"],
    "mapped.evil.example": ["::ffff:192.168.1.5"],
    "fakeip.local-proxy.com": ["198.18.31.22"],
}


async def _fake_resolver(host: str) -> list[str]:
    return _DNS.get(host, [])


def _proxy(handler, *, max_bytes: int = 1024) -> ImageProxy:
    return ImageProxy(
        headers_by_host={"doubanio.com": {"Referer": "https://m.douban.com/"}},
        max_bytes=max_bytes,
        transport=httpx.MockTransport(handler),
        resolver=_fake_resolver,
    )


async def test_proxy_sends_provider_headers_and_returns_image() -> None:
    async def handler(request: httpx.Request) -> httpx.Response:
        assert request.headers["referer"] == "https://m.douban.com/"
        return httpx.Response(200, headers={"Content-Type": "image/jpeg"}, content=b"jpeg")

    proxy = _proxy(handler)
    content, content_type = await proxy.fetch(
        "https://img3.doubanio.com/view/photo/public/example.jpg"
    )
    assert content == b"jpeg"
    assert content_type == "image/jpeg"
    await proxy.aclose()


async def test_proxy_allows_arbitrary_public_hosts_with_hotlink_headers() -> None:
    """图床域名不可枚举：任意公网域名都可代理；默认带同源 Referer 和浏览器 UA 过防盗链。"""

    async def handler(request: httpx.Request) -> httpx.Response:
        assert request.headers["referer"] == "https://img.example-host.com/"
        assert request.headers["user-agent"].startswith("Mozilla/5.0")
        return httpx.Response(200, headers={"Content-Type": "image/png"}, content=b"png")

    proxy = _proxy(handler)
    content, _ = await proxy.fetch("https://img.example-host.com/a.png")
    assert content == b"png"
    await proxy.aclose()


@pytest.mark.parametrize(
    "url",
    [
        "ftp://img3.doubanio.com/a.jpg",  # 非 http(s)
        "https://127.0.0.1/a.jpg",  # IP 字面量
        "https://[::1]/a.jpg",  # IPv6 字面量
        "https://img3.doubanio.com:8443/a.jpg",  # 非标准端口
        "https://user:pass@img3.doubanio.com/a.jpg",  # 携带用户名密码
        "https://intranet.evil.example/a.jpg",  # DNS 解析到内网
        "https://mixed.evil.example/a.jpg",  # 解析结果混有内网地址
        "https://mapped.evil.example/a.jpg",  # IPv4-mapped IPv6 包装的内网地址
    ],
)
async def test_proxy_rejects_unsafe_urls(url: str) -> None:
    proxy = _proxy(lambda _request: httpx.Response(200))
    with pytest.raises(BadRequestException):
        await proxy.fetch(url)
    await proxy.aclose()


async def test_proxy_allows_fake_ip_resolution() -> None:
    """Clash/Surge 等 fake-ip 模式把所有域名解析到 198.18.0.0/15，不能误判为内网。"""
    proxy = _proxy(
        lambda _request: httpx.Response(
            200, headers={"Content-Type": "image/png"}, content=b"png"
        )
    )
    content, _ = await proxy.fetch("https://fakeip.local-proxy.com/a.png")
    assert content == b"png"
    await proxy.aclose()


async def test_proxy_follows_redirects_with_revalidation() -> None:
    """重定向逐跳校验：公网跳公网可以，跳到解析内网的域名必须拒绝。"""

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.host == "img3.doubanio.com":
            return httpx.Response(302, headers={"Location": "https://image.tmdb.org/final.jpg"})
        return httpx.Response(200, headers={"Content-Type": "image/jpeg"}, content=b"ok")

    proxy = _proxy(handler)
    content, _ = await proxy.fetch("https://img3.doubanio.com/a.jpg")
    assert content == b"ok"

    def evil_handler(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(302, headers={"Location": "https://intranet.evil.example/a.jpg"})

    evil_proxy = _proxy(evil_handler)
    with pytest.raises(BadRequestException):
        await evil_proxy.fetch("https://img3.doubanio.com/a.jpg")
    await proxy.aclose()
    await evil_proxy.aclose()


async def test_proxy_rejects_endless_redirects() -> None:
    proxy = _proxy(
        lambda _request: httpx.Response(302, headers={"Location": "https://image.tmdb.org/a.jpg"})
    )
    with pytest.raises(UpstreamServiceException, match="重定向次数过多"):
        await proxy.fetch("https://img3.doubanio.com/a.jpg")
    await proxy.aclose()


async def test_proxy_rejects_non_image_response() -> None:
    proxy = _proxy(
        lambda _request: httpx.Response(
            200, headers={"Content-Type": "text/html"}, content=b"not image"
        )
    )
    with pytest.raises(UpstreamServiceException, match="不是图片"):
        await proxy.fetch("https://img3.doubanio.com/a.jpg")
    await proxy.aclose()


async def test_proxy_rejects_oversized_stream() -> None:
    proxy = _proxy(
        lambda _request: httpx.Response(
            200, headers={"Content-Type": "image/png"}, content=b"x" * 20
        ),
        max_bytes=10,
    )
    with pytest.raises(UpstreamServiceException, match="超过"):
        await proxy.fetch("https://img3.doubanio.com/a.png")
    await proxy.aclose()
