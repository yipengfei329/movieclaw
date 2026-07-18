from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class SchedulerConfig:
    """调度器运行期配置。

    由应用启动时（lifespan）从 Settings 读取后注入，**而非**由调度包反向 import
    应用配置——保持 movieclaw_scheduler 只依赖 movieclaw_db、不依赖 movieclaw_api
    的分层约定（与 init_db 从外部接收 database_url 的做法一致）。
    """

    # 计算 cron 触发时刻所用的时区。数据库统一存 UTC，但用户写「每天 3 点」是按
    # 本地时间理解的，因此 cron 必须带一个明确时区，否则触发时刻会与预期错位。
    timezone: str = "Asia/Shanghai"
    # 任务执行历史保留天数，超期由内置清理任务归档，避免 task_run 无限增长。
    task_run_retention_days: int = 30


# 模块级单例：init 时写入，任务体（如清理任务）按需读取。
_config: SchedulerConfig | None = None


def set_scheduler_config(config: SchedulerConfig) -> None:
    """写入全局调度配置。应在初始化调度器时调用一次。"""
    global _config
    _config = config


def get_scheduler_config() -> SchedulerConfig:
    """读取全局调度配置；未初始化时回退到默认值，保证任务体总能取到配置。"""
    return _config or SchedulerConfig()
