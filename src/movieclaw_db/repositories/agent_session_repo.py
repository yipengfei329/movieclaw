"""Agent 会话索引（``agent_session`` 表）的数据访问层。

本表是 JSONL 转录文件的查询索引（事实源与重建关系见模型 docstring）。
写入顺序约定：调用方**先 append 文件、后调本层更新索引**——因此这里的
每个写方法都按「行不存在也能自愈」实现（upsert 语义或静默跳过）。
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

from sqlalchemy.ext.asyncio import AsyncSession
from sqlmodel import select

from movieclaw_db.models.agent_session import AgentSession
from movieclaw_db.models.base import utcnow

#: 心跳超时窗：active_run_id 非空但心跳超过该时长未刷新，视为运行已异常终止
HEARTBEAT_TIMEOUT_SECONDS = 30


def is_running(row: AgentSession, *, now: datetime | None = None) -> bool:
    """派生运行状态：有活动运行编号且心跳仍在超时窗内。"""
    if row.active_run_id is None or row.last_heartbeat_at is None:
        return False
    now = now or utcnow()
    return now - row.last_heartbeat_at < timedelta(seconds=HEARTBEAT_TIMEOUT_SECONDS)


class AgentSessionRepository:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def get(self, session_id: str) -> AgentSession | None:
        return await self._session.get(AgentSession, session_id)

    async def list_recent(self, *, limit: int = 50, offset: int = 0) -> list[AgentSession]:
        """按最后活跃时间（updated_at）倒序分页。"""
        stmt = (
            select(AgentSession)
            .order_by(AgentSession.updated_at.desc(), AgentSession.created_at.desc())
            .offset(offset)
            .limit(limit)
        )
        return list((await self._session.execute(stmt)).scalars().all())

    async def create(self, session_id: str, *, title: str | None) -> AgentSession:
        row = AgentSession(id=session_id, title=title)
        self._session.add(row)
        await self._session.commit()
        return row

    async def touch_after_append(
        self,
        session_id: str,
        *,
        leaf_uuid: str | None,
        entry_count: int,
        last_prompt: str | None = None,
        title: str | None = None,
    ) -> None:
        """entry 追加落盘后刷新索引；行缺失（索引落后）时静默跳过，
        留给下次整体重建自愈。``last_prompt/title`` 传 None 表示不更新。"""
        row = await self.get(session_id)
        if row is None:
            return
        row.leaf_uuid = leaf_uuid
        row.entry_count = entry_count
        if last_prompt is not None:
            row.last_prompt = last_prompt
        if title is not None and row.title is None:
            row.title = title
        row.updated_at = utcnow()
        self._session.add(row)
        await self._session.commit()

    async def mark_running(self, session_id: str, run_id: str) -> None:
        """运行开始：记录运行编号并起跳第一拍心跳。"""
        row = await self.get(session_id)
        if row is None:
            return
        row.active_run_id = run_id
        row.last_heartbeat_at = utcnow()
        row.updated_at = utcnow()
        self._session.add(row)
        await self._session.commit()

    async def heartbeat(self, session_id: str) -> None:
        """刷新运行心跳；不动 updated_at（心跳不是内容活跃）。"""
        row = await self.get(session_id)
        if row is None:
            return
        row.last_heartbeat_at = utcnow()
        self._session.add(row)
        await self._session.commit()

    async def finish_run(self, session_id: str) -> None:
        """运行终态（完成/出错/取消统一）：清空运行标记，立即显示为已结束。"""
        row = await self.get(session_id)
        if row is None:
            return
        row.active_run_id = None
        row.last_heartbeat_at = None
        row.updated_at = utcnow()
        self._session.add(row)
        await self._session.commit()

    async def rename(self, session_id: str, title: str) -> AgentSession | None:
        """用户自定义会话标题；行不存在返回 None。

        改名是元数据操作，不刷新 updated_at（不影响列表的活跃排序）；
        非空标题在整体重建（rebuild）时会被保留，不会被文件预览覆盖。
        """
        row = await self.get(session_id)
        if row is None:
            return None
        row.title = title
        self._session.add(row)
        await self._session.commit()
        return row

    async def delete(self, session_id: str) -> None:
        row = await self.get(session_id)
        if row is not None:
            await self._session.delete(row)
            await self._session.commit()

    async def rebuild(
        self, summaries: list[tuple[str, str, int, str | None, str | None, str | None]]
    ) -> int:
        """用文件扫描结果整体校准索引，返回修正的行数。

        入参为 (session_id, created_at_iso, entry_count, leaf_uuid, title,
        last_prompt) 元组列表。规则：
        - 文件有、行没有 → 补行（created_at 取文件头时间）；
        - 行有、文件没有 → 删行（文件才是事实源）；
        - 都有但 entry_count / leaf_uuid 不一致 → 按文件覆盖。
        标题只在行里为空时回填，避免覆盖用户将来的自定义命名。
        """
        existing = {
            row.id: row
            for row in (await self._session.execute(select(AgentSession))).scalars().all()
        }
        changed = 0
        seen: set[str] = set()
        for session_id, created_iso, entry_count, leaf_uuid, title, last_prompt in summaries:
            seen.add(session_id)
            row = existing.get(session_id)
            if row is None:
                row = AgentSession(
                    id=session_id,
                    created_at=_parse_naive_utc(created_iso),
                    title=title,
                    last_prompt=last_prompt,
                    entry_count=entry_count,
                    leaf_uuid=leaf_uuid,
                )
                self._session.add(row)
                changed += 1
                continue
            if row.entry_count != entry_count or row.leaf_uuid != leaf_uuid:
                row.entry_count = entry_count
                row.leaf_uuid = leaf_uuid
                row.last_prompt = last_prompt
                if row.title is None:
                    row.title = title
                row.updated_at = utcnow()
                self._session.add(row)
                changed += 1
        for session_id, row in existing.items():
            if session_id not in seen:
                await self._session.delete(row)
                changed += 1
        if changed:
            await self._session.commit()
        return changed


def _parse_naive_utc(iso: str) -> datetime:
    """ISO 串（带 +00:00）→ 项目约定的 naive UTC（见 base.utcnow 说明）。"""
    return datetime.fromisoformat(iso).astimezone(UTC).replace(tzinfo=None)
