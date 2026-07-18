from __future__ import annotations

import asyncio
import logging
from pathlib import Path

from alembic import command
from alembic.config import Config

logger = logging.getLogger("movieclaw_db.migrations")

# 项目根目录：src/movieclaw_db/migrations.py 向上三级
_PROJECT_ROOT = Path(__file__).resolve().parents[2]
_ALEMBIC_INI = _PROJECT_ROOT / "alembic.ini"
_ALEMBIC_DIR = _PROJECT_ROOT / "alembic"


def _build_config() -> Config:
    """构造指向项目 alembic.ini 的 Config，并用绝对路径锁定脚本目录。

    用绝对路径覆盖 script_location，是为了让迁移无论从哪个工作目录启动
    （容器内 / IDE / 测试）都能找到迁移脚本。
    """
    cfg = Config(str(_ALEMBIC_INI))
    cfg.set_main_option("script_location", str(_ALEMBIC_DIR))
    return cfg


def _upgrade_to_head() -> None:
    """同步执行 alembic upgrade head。"""
    command.upgrade(_build_config(), "head")


async def run_migrations() -> None:
    """应用启动时调用：把数据库结构升级到最新版本。

    Alembic 的命令是同步阻塞的，这里放到线程池执行，避免阻塞 FastAPI 的事件循环；
    同时也让 env.py 内部可以独立管理自己的（同步）数据库连接，互不干扰。

    对非开发者部署者的意义：升级容器镜像后首次启动会自动补齐表结构，
    无需手动敲任何 alembic 命令。
    """
    logger.info("开始执行数据库迁移（upgrade head）……")
    await asyncio.to_thread(_upgrade_to_head)
    logger.info("数据库迁移完成，结构已是最新")
