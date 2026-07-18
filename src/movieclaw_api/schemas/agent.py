from __future__ import annotations

from datetime import UTC, datetime
from typing import Literal

from pydantic import BaseModel, Field, field_serializer, field_validator

from movieclaw_db.models import AgentSession
from movieclaw_db.repositories.agent_session_repo import is_running


def _iso_utc(value: datetime | None) -> str | None:
    """naive UTC → 带 +00:00 的 ISO 串（项目时间约定，见 base.utcnow）。"""
    if value is None:
        return None
    if value.tzinfo is None:
        value = value.replace(tzinfo=UTC)
    return value.isoformat()


class AgentHistoryMessage(BaseModel):
    """多轮会话的一条历史消息（前端会话页逐轮累积后随请求回传）。

    只接受 user / assistant 两种角色——system 由服务端编排、tool 结果
    属于运行内部产物，都不该由前端注入。
    """

    role: Literal["user", "assistant"]
    content: str = Field(max_length=32000)


class AgentStartPayload(BaseModel):
    """启动一次 Agent 运行的请求体。

    骨架版只暴露最小参数面：任务描述 + 多轮历史 + 可选模型引用。
    system_prompt / 工具集 / 采样参数属于服务端的 agent 编排职责，
    不开放给前端。
    """

    input: str = Field(min_length=1, max_length=4000, description="任务描述（自然语言）")
    #: 服务端会话 id：给出则续聊该会话（历史由服务端从转录重建），
    #: 留空则新建会话。给出 session_id 时 history 字段被忽略。
    session_id: str | None = Field(default=None, max_length=64)
    #: （过渡期兼容）前端本地累积的历史；仅在未给 session_id 时用于拼装
    #: LLM 上下文，不会写入服务端转录。前端切到服务端会话后删除。
    history: list[AgentHistoryMessage] = Field(default_factory=list, max_length=100)
    model: str = Field(default="", description="模型引用（留空用默认供应商的默认模型）")

    @field_validator("input", "model", mode="before")
    @classmethod
    def _strip(cls, value: str | None) -> str | None:
        return value.strip() if isinstance(value, str) else value


class AgentStartView(BaseModel):
    """异步 Agent 创建回执。

    run_id 用于订阅事件流或取消；session_id 是本轮所属的服务端会话
    （新建会话时由服务端分配），前端续聊时原样带回。
    """

    run_id: str
    session_id: str


class AgentSessionRenamePayload(BaseModel):
    """重命名会话的请求体。

    标题只存索引表（元数据不入转录文件，见 agent_sessions 模块的
    append-only 约定）；索引整体重建时非空标题会被保留。
    """

    title: str = Field(min_length=1, max_length=80, description="新的会话标题")

    @field_validator("title", mode="before")
    @classmethod
    def _strip_title(cls, value: str | None) -> str | None:
        return value.strip() if isinstance(value, str) else value


class AgentSessionListItem(BaseModel):
    """会话列表项（索引表投影 + 派生的运行状态）。"""

    id: str
    title: str | None
    last_prompt: str | None
    entry_count: int
    #: 是否有存活的运行（active_run_id 非空且心跳在超时窗内）
    running: bool
    #: running 为 true 时前端可用它重新挂上 SSE 事件流
    active_run_id: str | None
    created_at: datetime
    updated_at: datetime

    @field_serializer("created_at", "updated_at")
    def _serialize_utc(self, value: datetime | None) -> str | None:
        return _iso_utc(value)

    @classmethod
    def from_model(cls, row: AgentSession) -> AgentSessionListItem:
        running = is_running(row)
        return cls(
            id=row.id,
            title=row.title,
            last_prompt=row.last_prompt,
            entry_count=row.entry_count,
            running=running,
            active_run_id=row.active_run_id if running else None,
            created_at=row.created_at,
            updated_at=row.updated_at,
        )


class AgentSessionDetailView(BaseModel):
    """会话详情：列表项字段 + 完整消息 entry 回放。

    entries 是转录文件里 SessionEntry 的原样 JSON（信封 + API 格式消息），
    前端按 message.role 分发渲染组件，tool 结果用 tool_call_id 合并进
    对应调用卡片；缺回执的调用显示为进行中/已中断。
    """

    session: AgentSessionListItem
    entries: list[dict]
