from __future__ import annotations

from datetime import datetime
from enum import StrEnum

from sqlmodel import Field, SQLModel

from movieclaw_db.models.base import TimestampMixin, utcnow


class TriggerType(StrEnum):
    """定时任务的触发方式。

    - ``INTERVAL``：固定间隔重复（如每 300 秒一次），配 ``interval_seconds``。
    - ``CRON``：按 cron 表达式在具体时刻触发（如每天 03:00），配 ``cron_expr``。

    两种方式覆盖了绝大多数周期性任务场景，且都能被 APScheduler 原生表达。
    """

    INTERVAL = "interval"
    CRON = "cron"


class TaskRunStatus(StrEnum):
    """单次任务执行的状态。

        RUNNING ──► SUCCESS   （正常跑完）
                └─► FAILED    （抛异常，原因见 error）

    ``RUNNING`` 是执行开始时写入的中间态；若进程在任务执行中崩溃/重启，
    这条记录会永久停留在 RUNNING，调度器启动时会把它们自愈为 FAILED
    （见 ``TaskRunRepository.reap_orphan_running``），与站点验证的自愈思路一致。
    """

    RUNNING = "running"
    SUCCESS = "success"
    FAILED = "failed"


class ScheduledTask(TimestampMixin, table=True):
    """定时任务的调度定义表——本项目定时任务的**唯一事实来源**。

    设计要点（为什么不用 APScheduler 自带的 JobStore）：
    APScheduler 的持久化会把「函数引用 + 参数」pickle 进库，重构代码即失效，
    也无法查询和管理。这里改为：**调度定义以纯数据存在本表**，APScheduler 只当
    内存执行引擎。应用启动时读本表 → 重建 APScheduler 的 job；用户对周期/启停的
    修改落在本表，重启后依然生效。

    ``task_key`` 关联到代码里通过 ``@register_task`` 注册的任务处理器。表里只存
    「哪个任务、怎么调度、是否启用」这类数据，绝不存放可执行代码——这也是未来
    支持「用户自定义定时任务」时的安全边界：用户只能引用白名单内的 task_key 并
    配置参数/周期，不可能注入任意代码。
    """

    __tablename__ = "scheduled_task"

    id: int | None = Field(default=None, primary_key=True)
    # 任务标识，对应 registry 中 @register_task 注册的键；一个任务一条调度记录
    task_key: str = Field(index=True, unique=True, description="任务标识，对应代码中注册的处理器键")
    # 用户启用开关：停用后不参与调度，但保留调度定义便于随时恢复
    enabled: bool = Field(default=True, description="是否启用该定时任务")

    trigger_type: TriggerType = Field(description="触发方式：interval / cron")
    # INTERVAL 模式使用：两次触发之间的秒数
    interval_seconds: int | None = Field(default=None, description="INTERVAL 模式：间隔秒数")
    # CRON 模式使用：标准 5 段 cron 表达式（分 时 日 月 周），交给 APScheduler 解析
    cron_expr: str | None = Field(
        default=None, description="CRON 模式：5 段 cron 表达式，如 '0 3 * * *'"
    )

    # ------------------------------------------------------------------
    # 运行台账（供管理界面展示「上次跑于何时、下次何时触发」）
    # ------------------------------------------------------------------
    # 最近一次触发执行的时间；None 表示尚未执行过
    last_run_at: datetime | None = Field(default=None, description="最近一次执行时间")
    # 下次预计触发时间；由调度器在建立/刷新 job 时回填，是缓存值而非事实来源
    next_run_at: datetime | None = Field(default=None, description="下次预计触发时间")


class TaskRun(SQLModel, table=True):
    """定时任务的单次执行历史。

    每次任务执行落一条记录，供管理界面查看执行情况、供排错定位失败原因。
    ``error`` 字段记录失败原因（面向部署者的可读文本），契合本项目「非开发者也要
    看得懂错误」的原则。历史会随运行不断增长，因此内置了一个清理任务定期归档
    （见 ``movieclaw_scheduler.tasks``）。

    不使用 TimestampMixin：本表是「事件流水」，``started_at`` 即创建时刻、执行结束
    后不再变更业务时间，无需 created_at/updated_at 双时间戳。
    """

    __tablename__ = "task_run"

    id: int | None = Field(default=None, primary_key=True)
    # 冗余存 task_key（不做外键）：即便对应的调度定义被删，历史仍可独立留存与查询
    task_key: str = Field(index=True, description="任务标识")
    status: TaskRunStatus = Field(default=TaskRunStatus.RUNNING, index=True, description="执行状态")
    started_at: datetime = Field(default_factory=utcnow, index=True, description="开始执行时间")
    finished_at: datetime | None = Field(default=None, description="执行结束时间")
    # 执行耗时（毫秒），成功/失败均记录，便于观察任务是否变慢
    duration_ms: int | None = Field(default=None, description="执行耗时（毫秒）")
    # 失败原因（已归类为可读文本）；成功时为 None
    error: str | None = Field(default=None, description="失败原因")
