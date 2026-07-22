"""Agent 会话的 JSONL 持久化存储（事实源）。

设计定案（调研 pi / codex / Claude Code 三家后的共识 + 项目取舍）：

1. **一个会话一个 append-only JSONL 文件**：首行是会话头，之后每行一条
   消息 entry。历史行永不改写——中断、重启都只会追加，不会破坏已有内容。
2. **只落定稿消息，不落流式 delta**：SSE 增量属于 UI 通道；文件里的
   ``message`` 就是 LLM API 原样格式（ChatMessage），resume 重建上下文
   零转换。
3. **entry 带 uuid / parent_uuid**：v1 是纯线性链（parent 永远指向上一
   条），字段先留好，将来做回退分支时无需迁移文件格式。
4. **SQLite 的 agent_session 表只是查询索引**：任何时候都能由本目录的
   文件整体重建（见 repository 层的 rebuild），因此写入顺序固定为
   「先 append 文件、后更新 DB」，两步之间崩溃只会让索引落后，不会产生
   幽灵会话。
5. **读取容错**：进程崩溃可能留下半行，逐行解析时静默跳过坏行并计数，
   绝不让整个会话打不开。
"""

from __future__ import annotations

import json
import logging
import uuid as uuid_mod
from datetime import UTC, datetime
from pathlib import Path
from typing import Literal

from pydantic import BaseModel, Field, ValidationError

from movieclaw_api.exceptions import NotFoundException
from movieclaw_llm import ChatMessage, TokenUsage

logger = logging.getLogger("movieclaw_api.agent_sessions")

#: 文件格式版本；未来结构变化时 +1，读取端按版本做迁移
SESSION_FORMAT_VERSION = 1

#: 会话标题 / 最后提示预览的截断长度（DB 索引列用，全文始终在文件里）
PREVIEW_MAX_CHARS = 80


def _now_iso() -> str:
    """当前 UTC 时间的 ISO 串（带 +00:00，文件格式统一用 aware 时间）。"""
    return datetime.now(UTC).isoformat()


class SessionHeader(BaseModel):
    """JSONL 文件首行：会话身份与创建信息。

    标题、活跃时间等会变化的元数据不放这里——那些是 DB 索引的职责，
    头一旦写入就不再变化（append-only 原则）。
    """

    type: Literal["session"] = "session"
    version: int = SESSION_FORMAT_VERSION
    session_id: str
    created_at: str


class SessionEntry(BaseModel):
    """JSONL 消息行：信封 + LLM API 原样消息。

    ``model / usage / finish_reason`` 仅 assistant 消息携带（运行元数据，
    不属于 API message 本身，故放信封层）。``finish_reason`` 约定含
    ``"aborted"``：运行被取消时由收尾逻辑写入。
    """

    type: Literal["message"] = "message"
    uuid: str
    parent_uuid: str | None = None
    timestamp: str
    message: ChatMessage
    model: str | None = None
    usage: TokenUsage | None = None
    finish_reason: str | None = None


class SessionSummary(BaseModel):
    """扫描一个会话文件得到的索引摘要（rebuild 与列表回填用）。"""

    session_id: str
    created_at: str
    entry_count: int
    leaf_uuid: str | None
    #: 首条 user 消息的截断文本；作为无自定义标题时的会话标题
    title: str | None
    #: 最后一条 user 消息的截断文本（列表页副标题）
    last_prompt: str | None
    #: 最后一条 entry 的时间戳（文件为空时取头的 created_at）
    last_timestamp: str


class AgentSessionStore:
    """会话 JSONL 文件的读写入口。

    同一会话同一时刻只有一个运行在追加（路由层用 active_run_id 挡并发），
    因此这里不做文件锁；写入用同步 IO——单行 append 是微秒级操作，不值得
    为它引入线程池调度。
    """

    def __init__(self, root: Path) -> None:
        self._root = root
        #: session_id → 最后一条 entry 的 uuid（避免每次 append 都重读文件）
        self._leaf_cache: dict[str, str | None] = {}

    def path(self, session_id: str) -> Path:
        return self._root / f"{session_id}.jsonl"

    # ------------------------------------------------------------------
    # 写入
    # ------------------------------------------------------------------
    def create(self, session_id: str | None = None) -> SessionHeader:
        """新建会话文件并写入头行，返回头信息。"""
        header = SessionHeader(
            session_id=session_id or uuid_mod.uuid4().hex,
            created_at=_now_iso(),
        )
        path = self.path(header.session_id)
        self._root.mkdir(parents=True, exist_ok=True)
        # "x" 模式：会话 id 冲突（几乎不可能）时宁可报错也不覆盖已有文件
        with path.open("x", encoding="utf-8") as f:
            f.write(header.model_dump_json() + "\n")
        self._leaf_cache[header.session_id] = None
        return header

    def append(
        self,
        session_id: str,
        message: ChatMessage,
        *,
        model: str | None = None,
        usage: TokenUsage | None = None,
        finish_reason: str | None = None,
    ) -> SessionEntry:
        """追加一条定稿消息，自动接到当前链尾，返回写入的 entry。"""
        path = self.path(session_id)
        if not path.is_file():
            raise NotFoundException("Agent 会话不存在或转录文件已被删除")
        if session_id not in self._leaf_cache:
            _, entries, _ = self._read(path)
            self._leaf_cache[session_id] = entries[-1].uuid if entries else None
        entry = SessionEntry(
            uuid=uuid_mod.uuid4().hex[:12],
            parent_uuid=self._leaf_cache[session_id],
            timestamp=_now_iso(),
            message=message,
            model=model,
            usage=usage,
            finish_reason=finish_reason,
        )
        with path.open("a", encoding="utf-8") as f:
            f.write(entry.model_dump_json(exclude_none=True) + "\n")
        self._leaf_cache[session_id] = entry.uuid
        return entry

    def seal_pending_tool_calls(self, session_id: str) -> int:
        """中断收尾：给没有结果的 tool_call 补写错误回执，返回补写条数。

        保证文件里 assistant 的 tool_calls 与 tool 消息任何时刻都配对完整，
        resume 直接回喂 API 不需要修复逻辑（Claude Code 是吃到 400 再反应式
        修复，我们在写入侧一次做对更省事）。
        """
        _, entries, _ = self._read(self.path(session_id))
        answered = {e.message.tool_call_id for e in entries if e.message.role == "tool"}
        sealed = 0
        for e in entries:
            for tc in e.message.tool_calls or []:
                if tc.id in answered:
                    continue
                self.append(
                    session_id,
                    ChatMessage(
                        role="tool",
                        content="操作已被中断，工具未执行完成。",
                        tool_call_id=tc.id,
                        name=tc.name,
                    ),
                )
                sealed += 1
        return sealed

    def delete(self, session_id: str) -> None:
        """删除会话文件（幂等：文件不存在不报错）。"""
        self.path(session_id).unlink(missing_ok=True)
        self._leaf_cache.pop(session_id, None)

    # ------------------------------------------------------------------
    # 读取
    # ------------------------------------------------------------------
    def read(self, session_id: str) -> tuple[SessionHeader, list[SessionEntry]]:
        """读取整个会话（头 + 全部消息 entry），坏行静默跳过。"""
        path = self.path(session_id)
        if not path.is_file():
            raise NotFoundException("Agent 会话不存在或转录文件已被删除")
        header, entries, bad = self._read(path)
        if bad:
            logger.warning(
                "会话文件存在 %d 行无法解析的记录，已跳过（可能来自异常退出）：%s",
                bad,
                path.name,
            )
        return header, entries

    def build_history(self, session_id: str) -> list[ChatMessage]:
        """把会话重建成 LLM 上下文消息列表（resume 喂回模型用）。

        文件里存的就是 API 原样消息，这里只做投影不做转换；system 提示词
        不入库（随代码版本演进），由 runner 每次运行时重新拼装。
        """
        _, entries = self.read(session_id)
        return [e.message for e in entries]

    def summarize(self, session_id: str) -> SessionSummary:
        """扫描单个会话文件生成索引摘要。"""
        header, entries = self.read(session_id)
        user_texts = [
            e.message.text().strip()
            for e in entries
            if e.message.role == "user" and e.message.text().strip()
        ]
        return SessionSummary(
            session_id=header.session_id,
            created_at=header.created_at,
            entry_count=len(entries),
            leaf_uuid=entries[-1].uuid if entries else None,
            title=user_texts[0][:PREVIEW_MAX_CHARS] if user_texts else None,
            last_prompt=user_texts[-1][:PREVIEW_MAX_CHARS] if user_texts else None,
            last_timestamp=entries[-1].timestamp if entries else header.created_at,
        )

    def scan_all(self) -> list[SessionSummary]:
        """遍历目录下全部会话文件生成摘要（DB 索引整体重建用）。

        单个文件损坏（连头都解析不出）只告警跳过，不阻断其它会话重建。
        """
        if not self._root.is_dir():
            return []
        summaries: list[SessionSummary] = []
        for path in sorted(self._root.glob("*.jsonl")):
            try:
                summaries.append(self.summarize(path.stem))
            except Exception:  # noqa: BLE001 - 重建是自愈路径，单文件坏不拖垮全局
                logger.warning("会话文件无法解析，重建索引时已跳过：%s", path.name)
        return summaries

    def _read(self, path: Path) -> tuple[SessionHeader, list[SessionEntry], int]:
        """逐行解析文件；返回（头、消息列表、坏行数）。

        首行必须是合法会话头（否则整个文件视为损坏抛错）；其余行坏了只跳过。
        """
        entries: list[SessionEntry] = []
        bad = 0
        header: SessionHeader | None = None
        with path.open("r", encoding="utf-8") as f:
            for line_no, line in enumerate(f):
                line = line.strip()
                if not line:
                    continue
                if line_no == 0:
                    header = SessionHeader.model_validate_json(line)
                    continue
                try:
                    entries.append(SessionEntry.model_validate_json(line))
                except (ValidationError, json.JSONDecodeError):
                    bad += 1
        if header is None:
            raise NotFoundException("Agent 会话文件为空或头记录损坏")
        return header, entries, bad


_store: AgentSessionStore | None = None


def get_agent_session_store() -> AgentSessionStore:
    """进程级单例：按配置目录构建会话存储。"""
    global _store
    if _store is None:
        from movieclaw_api.core.config import get_settings

        _store = AgentSessionStore(Path(get_settings().agent_sessions_dir).resolve())
    return _store


def reset_agent_session_store() -> None:
    """重置单例（测试隔离用：换目录后重新构建）。"""
    global _store
    _store = None
