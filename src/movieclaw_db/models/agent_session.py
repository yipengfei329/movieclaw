"""Agent 会话索引表。

⚠️ 定位：本表**不是**会话数据的事实源。会话内容（全部消息）存放在
``data/agent-sessions/<id>.jsonl``（见 movieclaw_api.services.agent_sessions），
本表只冗余列表页需要查询/排序的字段，任何时候都可由文件整体重建。
因此这里绝不存消息内容——存了就会出现两份真相各自漂移。

运行状态的设计（调研 Claude Code 的教训）：不存 ``status="running"`` 这类
静态枚举——进程崩溃会把它永远留在"运行中"。改用两个可自愈的字段：
- ``active_run_id``：当前运行编号；正常结束时清空。
- ``last_heartbeat_at``：运行存活期间由心跳定期刷新。
「运行中」= active_run_id 非空 且 心跳在超时窗内；崩溃的运行随心跳过期
自动显示为已结束，无需任何清理任务。
"""

from __future__ import annotations

from datetime import datetime

from sqlmodel import Field

from movieclaw_db.models.base import TimestampMixin


class AgentSession(TimestampMixin, table=True):
    __tablename__ = "agent_session"

    #: 会话 id（uuid hex），与 JSONL 文件名一致
    id: str = Field(primary_key=True)
    # 会话标题：v1 取首条用户消息截断；将来支持用户改名/AI 起名时直接覆盖
    title: str | None = Field(default=None, description="会话标题（截断预览）")
    # 最后一条用户消息的截断文本，列表页副标题
    last_prompt: str | None = Field(default=None, description="最后提示预览")
    # 消息 entry 总数（不含头行）
    entry_count: int = Field(default=0, description="消息条数")
    # 链尾 entry 的 uuid；v1 线性链即最后一条，将来做分支时是当前活动叶
    leaf_uuid: str | None = Field(default=None, description="当前链尾 entry uuid")
    # 当前进行中的运行编号；无运行时为 None
    active_run_id: str | None = Field(default=None, description="进行中的运行编号")
    # 运行心跳（naive UTC）；active_run_id 非空但心跳超时 = 运行已异常终止
    last_heartbeat_at: datetime | None = Field(
        default=None, description="运行心跳时间（naive UTC）"
    )
