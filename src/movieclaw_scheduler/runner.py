from __future__ import annotations

import logging

from movieclaw_db.engine import get_database
from movieclaw_db.models.base import utcnow
from movieclaw_db.models.scheduled_task import TaskRunStatus
from movieclaw_db.repositories.scheduled_task_repo import (
    ScheduledTaskRepository,
    TaskRunRepository,
)
from movieclaw_scheduler.registry import TaskDefinition

logger = logging.getLogger("movieclaw_scheduler.runner")


async def run_task(definition: TaskDefinition) -> None:
    """所有定时任务真正被 APScheduler 调用的统一入口（执行包装层）。

    职责：
    - 落一条 RUNNING 历史，任务结束后收尾为 SUCCESS / FAILED；
    - **绝不向外抛异常**——APScheduler 里未捕获的异常会变成无人处理的后台错误，
      因此这里吞掉一切异常并转成 FAILED + 可读原因记入 task_run.error；
    - 记录耗时、回写调度定义的 last_run_at，供管理界面观察。

    历史记录用**独立于任务自身**的短会话读写，与任务体内部的数据库操作互不干扰，
    避免任务的长事务把历史写入也一起拖住。
    """
    started = utcnow()

    # 1. 开一条 RUNNING 记录（独立短会话）
    async with get_database().session() as session:
        run_id = await TaskRunRepository(session).start_run(definition.key, started)

    # 2. 执行任务体，捕获一切异常
    status = TaskRunStatus.SUCCESS
    error: str | None = None
    try:
        await definition.handler()
        logger.info("定时任务执行成功：%s（%s）", definition.key, definition.title)
    except Exception as exc:  # noqa: BLE001 -- 定时任务需吞掉所有异常并记录原因
        status = TaskRunStatus.FAILED
        error = f"{type(exc).__name__}：{exc}"
        # 日志保留完整堆栈供开发者排查，落库的 error 是精简可读文本
        logger.warning(
            "定时任务执行失败：%s，原因：%s", definition.key, error, exc_info=True
        )

    # 3. 收尾：写最终状态 + 耗时，并刷新调度定义的 last_run_at（独立短会话）
    finished = utcnow()
    duration_ms = int((finished - started).total_seconds() * 1000)
    async with get_database().session() as session:
        await TaskRunRepository(session).finish_run(
            run_id,
            status=status,
            finished_at=finished,
            duration_ms=duration_ms,
            error=error,
        )
        await ScheduledTaskRepository(session).mark_run(definition.key, finished)
