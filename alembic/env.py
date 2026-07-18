"""Alembic 迁移环境。

关键设计：
1. 目标元数据取自 ``SQLModel.metadata``。为此必须先导入所有表模型（通过
   ``movieclaw_db.models`` 的集中导入），否则 autogenerate 会认为没有任何表。
2. 数据库 URL 从应用的 Settings 读取，并把异步驱动 ``sqlite+aiosqlite`` 转换为
   同步的 ``sqlite``（pysqlite，标准库自带）。迁移是一次性、短暂的 DDL 操作，
   用同步驱动最简单可靠，无需在 Alembic 里处理 async。
"""

from __future__ import annotations

from logging.config import fileConfig

from alembic import context
from sqlalchemy import engine_from_config, pool
from sqlmodel import SQLModel

# 导入所有 ORM 模型 —— 触发它们注册到 SQLModel.metadata（autogenerate 必需）
import movieclaw_db.models  # noqa: F401
from movieclaw_api.core.config import get_settings

# Alembic Config 对象，读取 alembic.ini
config = context.config

# 配置日志（供独立运行 alembic CLI 时按 alembic.ini 输出迁移日志）。
# 关键：必须传 disable_existing_loggers=False。fileConfig 默认会禁用所有"配置文件
# 未显式声明"的既有 logger——而本 env 也会在应用启动时被 run_migrations() 调用，
# 若采用默认值，会连带把应用自己的 logger（如 movieclaw_api.access 访问日志、
# 错误日志）一并禁用，导致启动迁移后日志静默失效。
if config.config_file_name is not None:
    fileConfig(config.config_file_name, disable_existing_loggers=False)

# autogenerate 的对比基准
target_metadata = SQLModel.metadata


def _sync_database_url() -> str:
    """从应用 Settings 取 URL，并转为同步 sqlite 驱动供迁移使用。"""
    url = get_settings().database_url
    # sqlite+aiosqlite:///xxx -> sqlite:///xxx
    return url.replace("+aiosqlite", "")


def run_migrations_offline() -> None:
    """离线模式：不建立真实连接，仅生成 SQL 脚本。"""
    context.configure(
        url=_sync_database_url(),
        target_metadata=target_metadata,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
        # SQLite 的 ALTER 能力有限，开启批处理模式以支持列变更等操作
        render_as_batch=True,
    )
    with context.begin_transaction():
        context.run_migrations()


def run_migrations_online() -> None:
    """在线模式：建立连接并执行迁移。"""
    configuration = config.get_section(config.config_ini_section) or {}
    configuration["sqlalchemy.url"] = _sync_database_url()

    connectable = engine_from_config(
        configuration,
        prefix="sqlalchemy.",
        poolclass=pool.NullPool,
    )

    with connectable.connect() as connection:
        context.configure(
            connection=connection,
            target_metadata=target_metadata,
            # SQLite 不支持大多数 ALTER TABLE，批处理模式会以"建新表+拷贝"方式实现
            render_as_batch=True,
        )
        with context.begin_transaction():
            context.run_migrations()


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
