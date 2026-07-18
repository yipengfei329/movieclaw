from __future__ import annotations

import abc

from movieclaw_tracker.auth import AuthManager
from movieclaw_tracker.http import HttpClient
from movieclaw_tracker.models import (
    AuthResult,
    SearchQuery,
    SearchResult,
    TorrentCategory,
    TorrentDetail,
    TorrentListPage,
    UserProfile,
)


class BaseSite(abc.ABC):
    """所有 PT 站点的操作契约。

    基类仅声明 client 和 auth_manager 两个公共依赖。
    选择器、分类映射等框架特有依赖下放到子类。
    """

    def __init__(
        self,
        *,
        site_id: str,
        base_url: str,
        client: HttpClient,
        auth_manager: AuthManager,
        web_base_url: str | None = None,
    ) -> None:
        self.site_id = site_id
        self.base_url = base_url.rstrip("/")
        # 网页访问域名：拼接给用户看的链接（如种子详情页）时必须用它，
        # 而不是 base_url —— API 类站点两者不同（api.m-team.cc vs tp.m-team.cc）。
        # 未配置时与 base_url 相同（HTML 爬虫类站点请求域名即网页域名）。
        self.web_base_url = (web_base_url or base_url).rstrip("/")
        self.client = client
        self.auth_manager = auth_manager

    # -- 认证（非 abstract，委托给 AuthManager 处理） -----------------------

    async def authenticate(self) -> AuthResult:
        """通过 AuthManager 执行认证。返回 AuthResult 表示认证状态。"""
        return await self.auth_manager.authenticate(self.client)

    async def check_auth(self) -> bool:
        """检查当前会话是否有效。"""
        return await self.auth_manager.check_auth(self.client)

    async def deauthenticate(self) -> None:
        """登出/清除认证状态。"""
        await self.auth_manager.deauthenticate(self.client)

    # -- 种子操作 ----------------------------------------------------------

    @abc.abstractmethod
    async def list_torrents(
        self,
        *,
        categories: list[TorrentCategory] | None = None,
        page: int = 1,
    ) -> TorrentListPage:
        """返回分页的种子列表。"""

    @abc.abstractmethod
    async def search(self, query: SearchQuery) -> SearchResult:
        """按关键词和分类搜索种子。"""

    @abc.abstractmethod
    async def get_torrent_detail(self, url: str) -> TorrentDetail:
        """获取种子详情。url 来自列表/搜索结果中的 detail_url。"""

    @abc.abstractmethod
    async def download_torrent(self, url: str) -> bytes:
        """下载 .torrent 文件。url 来自列表/搜索结果中的 download_url。"""

    # -- 用户操作 ----------------------------------------------------------

    @abc.abstractmethod
    async def get_user_profile(
        self,
        user_id: str | None = None,
    ) -> UserProfile:
        """获取用户资料。user_id 为 None 时返回当前登录用户。"""

    # -- 工具方法 ----------------------------------------------------------

    def _url(self, path: str) -> str:
        """拼接完整 URL。"""
        return f"{self.base_url}/{path.lstrip('/')}"

    def _to_request_url(self, url: str) -> str:
        """把用户可见链接换回程序请求地址。

        detail_url 等展示链接用 web_base_url 域名拼接，用户回传（如获取详情）
        时需要换回 base_url 才能带上正确的认证上下文发请求。
        两个域名相同（未配置 web_base_url）时为无操作。
        """
        if self.web_base_url != self.base_url and url.startswith(self.web_base_url):
            return self.base_url + url[len(self.web_base_url):]
        return url
