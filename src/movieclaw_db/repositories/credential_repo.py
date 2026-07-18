from __future__ import annotations

from datetime import datetime

from sqlalchemy.ext.asyncio import AsyncSession
from sqlmodel import select

from movieclaw_db.models.base import utcnow
from movieclaw_db.models.site_credential import AuthType, ConfigStatus, SiteCredential


class CredentialRepository:
    """站点授权凭据表的数据访问层。

    封装对 ``SiteCredential`` 表的增删改查。这是用户配置数据的入口，
    上层 Service / API 通过本层读写站点账号信息。
    """

    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def get_by_site(self, site_id: str) -> SiteCredential | None:
        """按站点标识查询凭据；不存在返回 None。"""
        result = await self._session.execute(
            select(SiteCredential).where(SiteCredential.site_id == site_id)
        )
        return result.scalar_one_or_none()

    async def list_all(self) -> list[SiteCredential]:
        """返回所有站点凭据（含已停用），按站点标识排序，便于管理界面展示。"""
        result = await self._session.execute(
            select(SiteCredential).order_by(SiteCredential.site_id)
        )
        return list(result.scalars().all())

    async def list_enabled(self) -> list[SiteCredential]:
        """返回所有已启用的站点凭据，供聚合搜索等批量操作使用。"""
        result = await self._session.execute(
            select(SiteCredential)
            .where(SiteCredential.enabled == True)  # noqa: E712 -- SQL 表达式需用 ==
            .order_by(SiteCredential.site_id)
        )
        return list(result.scalars().all())

    async def upsert(
        self,
        *,
        site_id: str,
        auth_type: AuthType,
        cookie: str | None = None,
        api_key: str | None = None,
        username: str | None = None,
        password: str | None = None,
        enabled: bool = True,
    ) -> SiteCredential:
        """新增或整体更新一个站点的凭据，返回落库后的记录。

        存在则覆盖全部字段（含把未提供的敏感字段重置为 None，语义清晰、避免残留
        旧认证方式的脏数据），不存在则新建。

        重要：凭据一旦新增或变更，验证状态重置为 ``PENDING`` 并清空历史错误 ——
        新凭据是否有效尚未可知，必须重新走一遍异步验证才能回到 ACTIVE。
        """
        row = await self.get_by_site(site_id)
        if row is None:
            row = SiteCredential(
                site_id=site_id,
                auth_type=auth_type,
                cookie=cookie,
                api_key=api_key,
                username=username,
                password=password,
                enabled=enabled,
                status=ConfigStatus.PENDING,
            )
            self._session.add(row)
        else:
            row.auth_type = auth_type
            row.cookie = cookie
            row.api_key = api_key
            row.username = username
            row.password = password
            row.enabled = enabled
            # 凭据已变更，作废旧的验证结论
            row.status = ConfigStatus.PENDING
            row.last_error = None
            row.updated_at = utcnow()
        await self._session.commit()
        await self._session.refresh(row)
        return row

    async def update_status(
        self,
        site_id: str,
        status: ConfigStatus,
        *,
        last_error: str | None = None,
        last_verified_at: datetime | None = None,
    ) -> bool:
        """更新站点的验证状态，供异步验证流程回写结论。返回是否命中记录。

        - 成功（ACTIVE）：记 last_verified_at + last_checked_at，并清空 last_error。
        - 失败（FAILED）：记 last_error + last_checked_at 说明原因与时间。
        - 中间态（VERIFYING）：仅改状态，不动检查时间戳。
        """
        row = await self.get_by_site(site_id)
        if row is None:
            return False
        now = utcnow()
        row.status = status
        if status == ConfigStatus.ACTIVE:
            row.last_error = None
            row.last_verified_at = last_verified_at or now
            row.last_checked_at = now
        elif status == ConfigStatus.FAILED:
            # 失败原因归类后的中文文本；同时记录本次检查时间
            if last_error is not None:
                row.last_error = last_error
            row.last_checked_at = now
        row.updated_at = now
        await self._session.commit()
        return True

    async def reset_stale_verifying(self) -> int:
        """把残留在 VERIFYING 的记录重置为 PENDING，返回重置条数。

        用途：进程若在验证过程中被重启，这些记录会永久卡在 VERIFYING。
        应用启动时调用一次即可自愈，让它们重新排队验证。
        """
        result = await self._session.execute(
            select(SiteCredential).where(SiteCredential.status == ConfigStatus.VERIFYING)
        )
        rows = list(result.scalars().all())
        for row in rows:
            row.status = ConfigStatus.PENDING
            row.updated_at = utcnow()
        if rows:
            await self._session.commit()
        return len(rows)

    async def set_enabled(self, site_id: str, enabled: bool) -> bool:
        """启用/停用某站点。返回是否命中记录（False 表示站点不存在）。"""
        row = await self.get_by_site(site_id)
        if row is None:
            return False
        row.enabled = enabled
        row.updated_at = utcnow()
        await self._session.commit()
        return True

    async def delete(self, site_id: str) -> bool:
        """删除某站点凭据。返回是否命中记录。"""
        row = await self.get_by_site(site_id)
        if row is None:
            return False
        await self._session.delete(row)
        await self._session.commit()
        return True
