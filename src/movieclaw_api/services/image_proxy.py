"""开放域名的受控远程图片代理。

产品要加载的图片来自大量无法枚举的域名（TMDB 图床、豆瓣图床、各 PT 站种子
详情页引用的第三方图床……），传统的域名白名单没法覆盖，因此安全模型改为
「开放域名 + SSRF 防护」，防线如下：

- 仅允许 http/https 标准端口，URL 不得携带用户名密码；
- 禁止 IP 字面量直连，域名的 DNS 解析结果必须全部是公网地址（防止打内网）；
- 不自动跟随重定向，由代理逐跳重新校验后再跳（最多 3 跳）；
- 响应必须是 image/* 且体积有上限；
- 路由挂载在登录保护区，未登录用户无法触达。

说明：DNS 校验与 httpx 实际连接之间理论上存在 rebinding 时间窗；本产品是
自托管、登录后才可访问的场景，风险可接受，不再引入固定 IP 连接的复杂度。
各图床需要的 Referer 等请求头由装配层按域名注入，业务调用方只提供图片 URL。
"""

from __future__ import annotations

import asyncio
import ipaddress
import logging
import socket
from collections.abc import Awaitable, Callable, Mapping
from urllib.parse import urlsplit

import httpx

from movieclaw_api.exceptions import BadRequestException, UpstreamServiceException

logger = logging.getLogger("movieclaw_api.image_proxy")

# 测试可注入的域名解析器：host -> 该域名解析到的全部 IP 字符串
Resolver = Callable[[str], Awaitable[list[str]]]

_STANDARD_PORTS = {"http": 80, "https": 443}

# Clash / Surge / sing-box 等代理工具的 fake-ip 模式会把所有域名解析到这个
# RFC 2544 基准测试保留段，真实连接由代理接管转发。它不是内网网段，判定为
# 内网会把开着代理的用户全部误杀，故显式放行。
_FAKE_IP_NET = ipaddress.ip_network("198.18.0.0/15")

# 部分图床（Cloudflare 防护等）会直接拒绝脚本类 User-Agent，统一伪装成浏览器
_BROWSER_UA = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36"
)


class ImageProxy:
    """带 SSRF 防护、类型校验和体积上限的异步图片代理。"""

    def __init__(
        self,
        *,
        headers_by_host: Mapping[str, Mapping[str, str]] | None = None,
        max_bytes: int = 15 * 1024 * 1024,
        max_redirects: int = 3,
        transport: httpx.AsyncBaseTransport | None = None,
        resolver: Resolver | None = None,
    ) -> None:
        self._headers_by_host = {
            host.lower().lstrip("*. "): dict(headers)
            for host, headers in (headers_by_host or {}).items()
        }
        self._max_bytes = max_bytes
        self._max_redirects = max_redirects
        self._resolver = resolver
        self._client = httpx.AsyncClient(timeout=20, follow_redirects=False, transport=transport)

    async def _resolve(self, host: str) -> list[str]:
        if self._resolver is not None:
            return await self._resolver(host)
        loop = asyncio.get_running_loop()
        infos = await loop.getaddrinfo(host, None, type=socket.SOCK_STREAM)
        return [str(info[4][0]) for info in infos]

    async def _validated_host(self, url: str) -> str:
        """校验单个 URL（含重定向的每一跳），返回小写域名。"""
        parsed = urlsplit(url)
        host = (parsed.hostname or "").lower()
        if parsed.scheme not in _STANDARD_PORTS or not host or parsed.username or parsed.password:
            raise BadRequestException("图片地址必须是合法的 http(s) URL 且不得携带用户名密码")
        if parsed.port not in (None, _STANDARD_PORTS[parsed.scheme]):
            raise BadRequestException("图片地址不允许使用非标准端口")
        # 禁止 IP 字面量：正常图床都有域名，直连 IP 几乎只出现在探测内网的攻击里
        try:
            ipaddress.ip_address(host)
        except ValueError:
            pass
        else:
            raise BadRequestException("图片地址不允许直接使用 IP")
        try:
            addresses = await self._resolve(host)
        except (socket.gaierror, OSError) as exc:
            raise UpstreamServiceException(f"图片域名 {host} 解析失败") from exc
        if not addresses:
            raise UpstreamServiceException(f"图片域名 {host} 解析失败")
        for address in addresses:
            addr = ipaddress.ip_address(address)
            # IPv4-mapped IPv6（::ffff:a.b.c.d）按内嵌的 IPv4 判定，防止绕过
            if isinstance(addr, ipaddress.IPv6Address) and addr.ipv4_mapped is not None:
                addr = addr.ipv4_mapped
            if addr in _FAKE_IP_NET:
                continue
            if not addr.is_global:
                logger.warning("拒绝代理解析到内网地址的图片域名：%s -> %s", host, address)
                raise BadRequestException("图片域名解析到内网地址，已拒绝代理")
        return host

    def _headers_for(self, url: str, host: str) -> dict[str, str]:
        headers = {
            "Accept": "image/avif,image/webp,image/apng,image/*,*/*;q=0.8",
            "User-Agent": _BROWSER_UA,
            # PT 图床普遍开防盗链（如 img.m-team.cc 无 Referer 返回 403），
            # 默认带上图片自身域名的同源 Referer——等价于"在图床自己的页面里
            # 看图"，是各家防盗链都放行的口径。
            "Referer": f"{urlsplit(url).scheme}://{host}/",
        }
        for suffix, extra_headers in self._headers_by_host.items():
            if host == suffix or host.endswith(f".{suffix}"):
                # 需要特定 Referer 的图床（如豆瓣要求主站来源）按域名覆盖默认值
                headers.update(extra_headers)
                break
        return headers

    async def fetch(self, url: str) -> tuple[bytes, str]:
        """获取并验证远端图片，返回图片字节与 Content-Type。"""
        current = url
        try:
            for _ in range(self._max_redirects + 1):
                host = await self._validated_host(current)
                async with self._client.stream(
                    "GET", current, headers=self._headers_for(current, host)
                ) as response:
                    if response.is_redirect:
                        location = response.headers.get("location")
                        if not location:
                            raise UpstreamServiceException("图床返回的重定向缺少目标地址")
                        # 逐跳校验：新地址在下一轮循环里重新过一遍全部安全检查
                        current = str(httpx.URL(current).join(location))
                        continue
                    response.raise_for_status()
                    content_type = (
                        response.headers.get("content-type", "").split(";", 1)[0].lower()
                    )
                    if not content_type.startswith("image/"):
                        raise UpstreamServiceException("远端地址返回的内容不是图片")
                    declared_size = int(response.headers.get("content-length") or 0)
                    if declared_size > self._max_bytes:
                        raise UpstreamServiceException("远端图片超过代理允许的大小")
                    chunks: list[bytes] = []
                    size = 0
                    async for chunk in response.aiter_bytes():
                        size += len(chunk)
                        if size > self._max_bytes:
                            raise UpstreamServiceException("远端图片超过代理允许的大小")
                        chunks.append(chunk)
                    return b"".join(chunks), content_type
            raise UpstreamServiceException("图床重定向次数过多，已放弃")
        except UpstreamServiceException:
            raise
        except httpx.HTTPError as exc:
            logger.warning("代理图片请求失败：%s（%s）", current, exc)
            raise UpstreamServiceException("远端图片加载失败，请稍后重试") from exc

    async def aclose(self) -> None:
        await self._client.aclose()


_proxy: ImageProxy | None = None


def get_image_proxy() -> ImageProxy:
    """取得进程级代理单例；需要特殊请求头的图床在此按域名注入。"""
    global _proxy
    if _proxy is None:
        _proxy = ImageProxy(
            headers_by_host={"doubanio.com": {"Referer": "https://m.douban.com/"}},
        )
    return _proxy


async def close_image_proxy() -> None:
    global _proxy
    if _proxy is not None:
        await _proxy.aclose()
        _proxy = None


def reset_image_proxy() -> None:
    """仅供测试清理单例。"""
    global _proxy
    _proxy = None
