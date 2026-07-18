from __future__ import annotations

from sqlalchemy.ext.asyncio import AsyncSession
from sqlmodel import select

from movieclaw_db.crypto import get_secret_box
from movieclaw_db.models.base import utcnow
from movieclaw_db.models.llm_provider import LlmProvider
from movieclaw_db.models.site_credential import ConfigStatus


class LlmProviderRepository:
    """LLM 供应商配置表的数据访问层（单例语义）。

    - 全表至多一行：读用 get()（返回唯一行或 None），写用 upsert()
      （有则整体覆盖、无则创建），不提供多行的 list/按 id 操作；
    - api_key 的加解密统一收口在本层，与 DownloaderRepository 同理。
    """

    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    # -- 查询 --------------------------------------------------------------

    async def get(self) -> LlmProvider | None:
        """返回唯一的一行配置；尚未配置返回 None。"""
        result = await self._session.execute(select(LlmProvider).limit(1))
        return result.scalar_one_or_none()

    @staticmethod
    def decrypted_api_key(row: LlmProvider) -> str:
        """解密 API Key 密文，仅在真正要调模型时使用。"""
        return get_secret_box().decrypt(row.api_key)

    # -- 写入 --------------------------------------------------------------

    async def upsert(
        self,
        *,
        provider_type: str,
        base_url: str | None,
        api_key: str,
        default_model: str,
        extra_models: list[dict] | None = None,
    ) -> LlmProvider:
        """保存配置：有则整体覆盖、无则创建（维护单例不变量）。

        连接信息变更后验证状态重置为 PENDING、清空历史错误与模型列表。
        """
        row = await self.get()
        encrypted = get_secret_box().encrypt(api_key)
        if row is None:
            row = LlmProvider(
                provider_type=provider_type,
                base_url=base_url,
                api_key=encrypted,
                default_model=default_model,
                extra_models=extra_models,
            )
            self._session.add(row)
        else:
            row.provider_type = provider_type
            row.base_url = base_url
            row.api_key = encrypted
            row.default_model = default_model
            row.extra_models = extra_models
            row.status = ConfigStatus.PENDING
            row.last_error = None
            row.available_models = None
            row.updated_at = utcnow()
        await self._session.commit()
        await self._session.refresh(row)
        return row

    async def update_status(
        self,
        status: ConfigStatus,
        *,
        last_error: str | None = None,
        available_models: list[str] | None = None,
    ) -> bool:
        """回写验证结论。返回是否存在配置行。

        - 成功（ACTIVE）：清空 last_error，记录可用模型列表与检查时间；
        - 失败（FAILED）：记录 last_error 与检查时间；
        - 中间态（VERIFYING）：仅改状态。
        """
        row = await self.get()
        if row is None:
            return False
        now = utcnow()
        row.status = status
        if status == ConfigStatus.ACTIVE:
            row.last_error = None
            if available_models is not None:
                row.available_models = available_models
            row.last_checked_at = now
        elif status == ConfigStatus.FAILED:
            if last_error is not None:
                row.last_error = last_error
            row.last_checked_at = now
        row.updated_at = now
        await self._session.commit()
        return True

    async def reset_stale_verifying(self) -> int:
        """把残留在 VERIFYING 的配置重置为 PENDING（进程重启自愈），返回条数。"""
        row = await self.get()
        if row is None or row.status != ConfigStatus.VERIFYING:
            return 0
        row.status = ConfigStatus.PENDING
        row.updated_at = utcnow()
        await self._session.commit()
        return 1

    async def delete(self) -> bool:
        """删除配置。返回是否存在配置行。"""
        row = await self.get()
        if row is None:
            return False
        await self._session.delete(row)
        await self._session.commit()
        return True
