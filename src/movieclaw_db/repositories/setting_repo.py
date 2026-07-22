from __future__ import annotations

from sqlalchemy.ext.asyncio import AsyncSession
from sqlmodel import select

from movieclaw_db.models.app_setting import AppSetting
from movieclaw_db.models.base import utcnow


class SettingRepository:
    """通用配置表（``app_setting``）的数据访问层。

    职责边界：本层只做"按 namespace 存取一段 JSON 字符串"的原始读写，
    **不理解 JSON 里的业务含义、不做校验、不做加解密**。这些语义交给上层的
    ``SettingStore`` 处理，从而让持久化层保持业务无关、可复用。
    """

    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def get(self, namespace: str) -> AppSetting | None:
        """按配置域读取一条记录；不存在返回 None。"""
        result = await self._session.execute(
            select(AppSetting).where(AppSetting.namespace == namespace)
        )
        return result.scalar_one_or_none()

    async def list_all(self) -> list[AppSetting]:
        """返回全部配置记录，按 namespace 排序，便于导出备份与管理界面展示。"""
        result = await self._session.execute(select(AppSetting).order_by(AppSetting.namespace))
        return list(result.scalars().all())

    async def upsert(self, namespace: str, value_json: str) -> AppSetting:
        """新增或整体覆盖某配置域的 JSON 值，返回落库后的记录。"""
        row = await self.get(namespace)
        if row is None:
            row = AppSetting(namespace=namespace, value_json=value_json)
            self._session.add(row)
        else:
            row.value_json = value_json
            row.updated_at = utcnow()
        await self._session.commit()
        await self._session.refresh(row)
        return row

    async def delete(self, namespace: str) -> bool:
        """删除某配置域。返回是否命中记录（False 表示该域本就不存在）。"""
        row = await self.get(namespace)
        if row is None:
            return False
        await self._session.delete(row)
        await self._session.commit()
        return True
