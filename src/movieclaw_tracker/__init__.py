from __future__ import annotations

from movieclaw_tracker.auth import (
    ApiKeyAuthProvider,
    AuthManager,
    AuthProvider,
    CaptchaSolver,
    CookieAuthProvider,
    CookieInput,
    CookieStore,
    CredentialAuthProvider,
    MemoryCookieStore,
    parse_cookies,
)
from movieclaw_tracker.base import BaseSite
from movieclaw_tracker.http import HttpClient
from movieclaw_tracker.registry import get_site_config, list_sites, load_all_sites
from movieclaw_tracker.selectors import LoginSelectors

__all__ = [
    "create_site",
    "get_site_config",
    "list_sites",
    "load_all_sites",
    "CookieAuthProvider",
    "CredentialAuthProvider",
    "ApiKeyAuthProvider",
    "CaptchaSolver",
    "LoginSelectors",
    "CookieInput",
    "parse_cookies",
    "AuthProvider",
    "AuthManager",
    "CookieStore",
    "MemoryCookieStore",
]


async def create_site(
    site_id: str,
    *,
    auth_provider: AuthProvider,
    cookie_store: CookieStore | None = None,
) -> BaseSite:
    """工厂函数：构建可用的站点实例。

    调用方决定认证方式。

    Cookie 模式（直接提供浏览器 cookie）::

        site = await create_site(
            "xxx",
            auth_provider=CookieAuthProvider(cookies={...}),
        )

    账号密码模式（自动模拟登录）::

        site = await create_site(
            "xxx",
            auth_provider=CredentialAuthProvider(
                username="user", password="pass",
            ),
        )
    """
    config = get_site_config(site_id)

    # 将站点上下文（base_url、登录选择器）注入 Provider
    # CookieAuthProvider 的 bind 为空操作；CredentialAuthProvider 需要这些信息完成登录
    login_selectors: LoginSelectors | None = None
    if config.selectors is not None and hasattr(config.selectors, "to_login_selectors"):
        login_selectors = config.selectors.to_login_selectors()
    auth_provider.bind(base_url=config.base_url, login_selectors=login_selectors)

    client = HttpClient(
        timeout=config.timeout,
        max_retries=config.max_retries,
        http2=config.http2,
        # 注入 site_id + 每站间隔：启用按 site_id 全进程共享的请求限流器
        site_id=config.site_id,
        min_request_interval=config.min_request_interval,
    )

    store = cookie_store or MemoryCookieStore()
    auth_manager = AuthManager(
        provider=auth_provider,
        store=store,
        site_id=site_id,
    )

    kwargs: dict = {
        "site_id": config.site_id,
        "base_url": config.base_url,
        "client": client,
        "auth_manager": auth_manager,
    }

    # 网页访问域名与请求域名不同的站点（如 M-Team），额外注入 web_base_url
    if config.web_base_url:
        kwargs["web_base_url"] = config.web_base_url

    if config.selectors is not None:
        kwargs["selectors"] = config.selectors
    if config.category_map:
        kwargs["category_map"] = config.category_map

    return config.site_class(**kwargs)
