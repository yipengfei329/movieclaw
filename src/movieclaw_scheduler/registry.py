from __future__ import annotations

import logging
from collections.abc import Awaitable, Callable
from dataclasses import dataclass

from movieclaw_db.models.scheduled_task import TriggerType

logger = logging.getLogger("movieclaw_scheduler.registry")

# 任务处理器签名：无参协程。任务体自行按需开数据库会话（与 verify_site 一致），
# 避免调度器把一个长会话/事务贯穿整个任务，规避 SQLite 写锁长时间占用。
TaskHandler = Callable[[], Awaitable[None]]


@dataclass(frozen=True)
class TaskDefinition:
    """一个可被调度的任务的「代码侧定义」。

    它描述任务是什么、由哪个函数执行、以及**默认**怎么调度。真正生效的调度参数
    以数据库 ``scheduled_task`` 表为准——本定义里的默认值仅在该任务首次出现、
    数据库还没有对应记录时用于播种一行（之后用户的调整优先）。

    这是「引擎与业务分离」的关键载体：引擎只认识 TaskDefinition，不关心任务体
    具体做什么；各领域包只需 ``@register_task`` 贡献自己的任务，无需改动引擎。
    """

    key: str
    title: str
    handler: TaskHandler
    default_trigger_type: TriggerType
    default_interval_seconds: int | None = None
    default_cron: str | None = None
    default_enabled: bool = True
    description: str = ""


# 全局注册表：task_key -> TaskDefinition。模块导入时由 @register_task 填充。
_REGISTRY: dict[str, TaskDefinition] = {}


def register_task(
    key: str,
    *,
    title: str,
    trigger_type: TriggerType,
    interval_seconds: int | None = None,
    cron: str | None = None,
    enabled: bool = True,
    description: str = "",
) -> Callable[[TaskHandler], TaskHandler]:
    """把一个无参协程注册为可调度任务的装饰器。

    用法::

        @register_task(
            "cleanup_task_runs",
            title="清理任务历史",
            trigger_type=TriggerType.CRON,
            cron="30 4 * * *",
        )
        async def cleanup() -> None:
            ...

    校验：INTERVAL 必须给 interval_seconds，CRON 必须给 cron，且 key 不可重复注册，
    以便在启动早期就暴露配置错误，而不是等到调度时才失败。
    """

    if trigger_type == TriggerType.INTERVAL and interval_seconds is None:
        raise ValueError(f"任务 {key} 使用 INTERVAL 触发，必须提供 interval_seconds")
    if trigger_type == TriggerType.CRON and not cron:
        raise ValueError(f"任务 {key} 使用 CRON 触发，必须提供 cron 表达式")

    def decorator(handler: TaskHandler) -> TaskHandler:
        if key in _REGISTRY:
            raise ValueError(f"定时任务 key 重复注册：{key}")
        _REGISTRY[key] = TaskDefinition(
            key=key,
            title=title,
            handler=handler,
            default_trigger_type=trigger_type,
            default_interval_seconds=interval_seconds,
            default_cron=cron,
            default_enabled=enabled,
            description=description,
        )
        logger.debug("已注册定时任务：%s（%s）", key, title)
        return handler

    return decorator


def get_task(key: str) -> TaskDefinition | None:
    """按 key 取任务定义；未注册返回 None。"""
    return _REGISTRY.get(key)


def iter_tasks() -> list[TaskDefinition]:
    """返回所有已注册任务定义（按 key 排序，输出稳定）。"""
    return [_REGISTRY[k] for k in sorted(_REGISTRY)]
