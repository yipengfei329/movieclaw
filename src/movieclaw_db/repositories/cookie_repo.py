from __future__ import annotations

from sqlalchemy.ext.asyncio import AsyncSession
from sqlmodel import select

from movieclaw_db.models.base import utcnow
from movieclaw_db.models.site_cookie import SiteCookie


class CookieRepository:
    """Cookie 缓存表的数据访问层。

    封装对 ``SiteCookie`` 表的所有读写，让上层（如 SqlCookieStore）不直接接触 SQL。
    每个实例绑定一个会话，事务提交由本层负责（单条写入即提交，语义简单）。
    """

    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def get(self, site_id: str) -> dict[str, str] | None:
        """读取指定站点的缓存 cookie；不存在返回 None。"""
        result = await self._session.execute(
            select(SiteCookie).where(SiteCookie.site_id == site_id)
        )
        row = result.scalar_one_or_none()
        return dict(row.cookies) if row else None

    async def upsert(self, site_id: str, cookies: dict[str, str]) -> None:
        """写入或更新站点的 cookie 会话。

        存在则覆盖 cookies 并刷新 updated_at，不存在则新建。
        采用"先查后写"而非数据库原生 UPSERT，是为了保持跨数据库可移植性，
        且本场景写入频率低，性能足够。
        """
        result = await self._session.execute(
            select(SiteCookie).where(SiteCookie.site_id == site_id)
        )
        row = result.scalar_one_or_none()
        if row is None:
            row = SiteCookie(site_id=site_id, cookies=cookies)
            self._session.add(row)
        else:
            row.cookies = cookies
            row.updated_at = utcnow()
        await self._session.commit()

    async def delete(self, site_id: str) -> None:
        """删除站点的 cookie 缓存（登出或凭据失效时调用）。不存在则静默返回。"""
        result = await self._session.execute(
            select(SiteCookie).where(SiteCookie.site_id == site_id)
        )
        row = result.scalar_one_or_none()
        if row is not None:
            await self._session.delete(row)
            await self._session.commit()
