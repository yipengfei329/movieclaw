from __future__ import annotations

from movieclaw_tracker.base import BaseSite
from movieclaw_tracker.models import (
    SearchQuery,
    SearchResult,
    TorrentCategory,
    TorrentDetail,
    TorrentListPage,
    UserProfile,
)


class MTeamSite(BaseSite):
    """MTeam — 使用站点 API 而非 HTML 解析。方法预留待实现。"""

    async def list_torrents(
        self,
        *,
        categories: list[TorrentCategory] | None = None,
        page: int = 1,
    ) -> TorrentListPage:
        raise NotImplementedError("MTeam API not yet implemented")

    async def search(self, query: SearchQuery) -> SearchResult:
        raise NotImplementedError("MTeam API not yet implemented")

    async def get_torrent_detail(self, url: str) -> TorrentDetail:
        raise NotImplementedError("MTeam API not yet implemented")

    async def download_torrent(self, url: str) -> bytes:
        raise NotImplementedError("MTeam API not yet implemented")

    async def get_user_profile(
        self,
        user_id: str | None = None,
    ) -> UserProfile:
        raise NotImplementedError("MTeam API not yet implemented")
