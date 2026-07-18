"""movieclaw 定时任务调度层。

分层职责：本包是**调度基础设施**，只提供「引擎 + 注册 + 执行 + 台账」这套通用能力，
不含任何具体业务。它依赖 movieclaw_db（读写调度定义与运行历史），但**绝不依赖**
movieclaw_api，也**不主动依赖**各业务领域包——业务任务反过来 import 本包完成注册。

对外暴露：
- ``register_task``：领域包用它把自己的任务注册进来（引擎与业务分离的关键）。
- ``TriggerType``：声明触发方式（interval / cron）。
- ``SchedulerConfig`` / ``init_scheduler`` / ``get_scheduler``：由 lifespan 初始化与驱动。

典型接线（见 movieclaw_api.lifespan）::

    init_scheduler(SchedulerConfig(timezone=..., task_run_retention_days=...))
    # ……在此之前 import 各领域的任务模块以触发 @register_task……
    await get_scheduler().start()
    ...
    await get_scheduler().shutdown()
"""

from __future__ import annotations

from movieclaw_db.models.scheduled_task import TriggerType
from movieclaw_scheduler.config import SchedulerConfig
from movieclaw_scheduler.registry import (
    TaskDefinition,
    get_task,
    iter_tasks,
    register_task,
)
from movieclaw_scheduler.service import (
    SchedulerService,
    get_scheduler,
    init_scheduler,
)

__all__ = [
    "TriggerType",
    "SchedulerConfig",
    "TaskDefinition",
    "register_task",
    "get_task",
    "iter_tasks",
    "SchedulerService",
    "init_scheduler",
    "get_scheduler",
]
