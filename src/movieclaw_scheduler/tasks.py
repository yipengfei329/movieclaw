"""调度器内置的系统级任务。

这里只放**调度基础设施自身**的运维任务（如清理运行历史）。真正的领域业务任务
（如重新验证站点凭据、周期抓取）应放在各自的领域包里（如 movieclaw_tracker），
用同样的 ``@register_task`` 装饰器注册进来——引擎不关心任务体做什么，从而避免
本包反向依赖各业务模块、形成循环依赖。这正是「引擎与业务分离」落到代码上的样子。
"""

from __future__ import annotations

import logging
from datetime import timedelta

from movieclaw_db.engine import get_database
from movieclaw_db.models.base import utcnow
from movieclaw_db.models.scheduled_task import TriggerType
from movieclaw_db.repositories.cache_repo import CacheRepository
from movieclaw_db.repositories.scheduled_task_repo import TaskRunRepository
from movieclaw_scheduler.config import get_scheduler_config
from movieclaw_scheduler.registry import register_task

logger = logging.getLogger("movieclaw_scheduler.tasks")

# 持久缓存的保留天数：覆盖全系统最长的缓存可用期（豆瓣详情 30 天），
# 超过即为任何调用方都不会再读的死数据，直接删除。
_CACHE_RETENTION_DAYS = 30


@register_task(
    "cleanup_task_runs",
    title="清理定时任务执行历史",
    trigger_type=TriggerType.CRON,
    cron="30 4 * * *",  # 每天凌晨 4:30（业务低谷）跑一次
    description="删除超过保留期的 task_run 历史记录，防止表无限增长。",
)
async def cleanup_task_runs() -> None:
    """删除超过保留天数的任务执行历史。

    保留天数由 ``SchedulerConfig.task_run_retention_days`` 控制（可经环境变量配置），
    这是一个自包含、只依赖 movieclaw_db 的示例任务：完整走通「注册→调度→执行→
    写历史」的闭环，同时不引入对上层业务模块的依赖。
    """
    retention_days = get_scheduler_config().task_run_retention_days
    cutoff = TaskRunRepository.cutoff_before(retention_days)
    async with get_database().session() as session:
        deleted = await TaskRunRepository(session).purge_older_than(cutoff)
    logger.info(
        "清理任务历史完成：删除 %d 条早于 %s 的记录（保留 %d 天）",
        deleted,
        cutoff,
        retention_days,
    )


@register_task(
    "cleanup_cache_entries",
    title="清理过期的持久缓存",
    trigger_type=TriggerType.CRON,
    cron="40 4 * * *",  # 每天凌晨 4:40（业务低谷，错开任务历史清理）
    description="删除 cache_entry 表中超过最长可用期的缓存行，防止缓存表无限增长。",
)
async def cleanup_cache_entries() -> None:
    """删除超过保留期的持久缓存行。

    缓存表里全是可随时删除、可从上游重建的派生数据（见 CacheEntry 的说明），
    因此清理策略可以非常粗暴：超过全系统最长可用期的行一律删除。
    """
    cutoff = utcnow() - timedelta(days=_CACHE_RETENTION_DAYS)
    async with get_database().session() as session:
        deleted = await CacheRepository(session).purge_older_than(cutoff)
    logger.info(
        "清理持久缓存完成：删除 %d 条早于 %s 的记录（保留 %d 天）",
        deleted,
        cutoff,
        _CACHE_RETENTION_DAYS,
    )
