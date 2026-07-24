from __future__ import annotations

import logging
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI

from movieclaw_api.core.config import Settings
from movieclaw_api.core.logging import configure_logging
from movieclaw_api.services.agent_runs import (
    close_agent_run_registry,
    init_agent_run_registry,
)
from movieclaw_api.services.image_proxy import close_image_proxy
from movieclaw_api.services.media_discover import close_media_service
from movieclaw_api.services.site_access import get_site_access, init_site_access
from movieclaw_api.settings import init_setting_store
from movieclaw_db.crypto import init_secret_box
from movieclaw_db.engine import dispose_db, get_database, init_db
from movieclaw_db.migrations import run_migrations
from movieclaw_db.repositories.credential_repo import CredentialRepository
from movieclaw_db.repositories.llm_provider_repo import LlmProviderRepository
from movieclaw_scheduler import SchedulerConfig, get_scheduler, init_scheduler
from movieclaw_tracker import load_all_sites

logger = logging.getLogger("movieclaw_api.lifespan")


async def _reset_stale_verifying() -> None:
    """把上次进程遗留在 VERIFYING 的记录重置为 PENDING（崩溃/重启自愈）。"""
    async with get_database().session() as session:
        count = await CredentialRepository(session).reset_stale_verifying()
        if count:
            logger.info("已重置 %d 条卡在验证中的站点配置为待验证", count)
        if await LlmProviderRepository(session).reset_stale_verifying():
            logger.info("已重置卡在验证中的 LLM 供应商配置为待验证")


def build_lifespan(settings: Settings):
    """构造 FastAPI 生命周期管理器。

    启动阶段（yield 之前）：
      1. 初始化数据库引擎（创建 data 目录、建立连接池、注册 WAL 等 PRAGMA）。
      2. 自动执行 Alembic 迁移，把表结构升级到最新 —— 部署者升级镜像后
         首次启动即自动补齐结构，无需手动运行任何命令。
      3. 启动定时任务调度器（按开关决定是否启用）。

    关闭阶段（yield 之后）：
      先停调度器，再释放数据库连接池。

    用闭包接收 settings，避免在生命周期函数内部再次读取全局配置。
    """

    @asynccontextmanager
    async def lifespan(_app: FastAPI) -> AsyncIterator[None]:
        # 先初始化引擎（会顺带创建 SQLite 文件所在目录），再执行迁移
        init_db(settings.database_url, echo=settings.db_echo)
        await run_migrations()
        # Alembic 的 fileConfig 会按 alembic.ini 重置 root logger：级别设回 WARNING、
        # Handler 换成仅剩它的 console（应用挂的按天落盘 Handler 也被移除）。迁移一跑完
        # 就重新应用一次应用日志配置，恢复 INFO 级别并重挂文件 Handler，否则访问日志
        # 静默、迁移之后的日志全部不落盘。
        configure_logging(settings.log_level, settings.log_dir, settings.log_retention_days)
        # 初始化配置内核：先备好加密器（方案 A/B 解析主密钥），再建配置存储单例。
        # 顺序在迁移之后即可——首次空库启动时 app_setting 表已建好，读取缺记录会
        # 返回默认值，不会报错（这是"空库也能启动、进引导页"的关键红线）。
        init_secret_box(settings.master_key, Path(settings.secret_key_file))
        init_setting_store()
        # 加载网络出口配置（代理路由/镜像地址）：须在任何出网客户端首次构造前生效
        from movieclaw_api.services.network_egress import load_network_egress

        await load_network_egress()
        # 加载站点目录（sites/configs/*.yaml → registry），供"可选项"接口使用
        load_all_sites()
        # 初始化站点访问管理器：进程级单例，持有每站已认证的共享客户端。
        # 须在调度器之前，因为种子同步任务依赖它访问站点。
        init_site_access()
        # 重启自愈：清理上次遗留的"验证中"状态
        await _reset_stale_verifying()
        # 媒体库首启种子：库表为空时创建"电影库/剧集库"两个默认库
        from movieclaw_api.services.library_config import seed_default_libraries

        await seed_default_libraries(settings.library_default_root)
        # Agent 运行注册表必须与当前事件循环同生共死：它持有后台 task 和
        # asyncio.Condition，不能跨 FastAPI 生命周期复用。
        init_agent_run_registry()
        # Agent 会话索引自愈：JSONL 转录是事实源，启动时把 SQLite 索引
        # 校准到与文件一致（上次崩溃在两步写入之间也能恢复）。
        from movieclaw_api.services.agent_session_recorder import (
            rebuild_agent_session_index,
        )

        await rebuild_agent_session_index()
        # 扩充属性重算：提取器升级（ENRICH_VERSION +1）后，把存量种子行按新
        # 逻辑重算——纯本地推导秒级完成，失败不阻断启动（内部自吞异常）
        from movieclaw_api.services.enrich_backfill import reenrich_stale_torrents

        await reenrich_stale_torrents()
        # 启动定时任务调度器：注册内置任务、从数据库重建 job 并开始调度。
        # 领域业务任务在此处 import 其任务模块以触发 @register_task 注册（须在 start() 前）。
        if settings.scheduler_enabled:
            from movieclaw_api.services import (  # noqa: F401  订阅管线三任务注册  # noqa: F401  下载完成检测与入库任务注册  # noqa: F401  媒体库对账任务注册
                download_progress,
                library_ingest,  # noqa: F401  下载监听导入任务注册
                library_scan,
                media_refresh,
                torrent_matcher,
                torrent_sync,  # noqa: F401  触发种子同步任务注册
                wanted_search,
            )

            init_scheduler(
                SchedulerConfig(
                    timezone=settings.scheduler_timezone,
                    task_run_retention_days=settings.task_run_retention_days,
                )
            )
            await get_scheduler().start()
        else:
            logger.info("定时任务调度器已按配置关闭（SCHEDULER_ENABLED=false）")
        # 媒体库实时监控（L4）：库根路径文件事件 → 去抖 → 增量扫描；
        # watchdog 缺失/根路径未就绪时优雅降级为仅对账任务兜底。
        from movieclaw_api.services.library_watch import init_library_watcher

        await init_library_watcher()
        # 下载监听导入：监听目录文件事件 → 去抖 → 完成检测 → 硬链/复制入库；
        # 同样在 watchdog 缺失时降级为仅兜底巡检
        from movieclaw_api.services.library_ingest import init_ingest_watcher

        await init_ingest_watcher()
        logger.info("应用启动完成，数据库就绪")
        try:
            yield
        finally:
            # 先停媒体库监听（观察者线程持有事件循环引用，须在循环关闭前退出）
            from movieclaw_api.services.library_ingest import close_ingest_watcher
            from movieclaw_api.services.library_watch import close_library_watcher

            await close_ingest_watcher()
            await close_library_watcher()
            # 先停止 Agent，避免它在下游 HTTP 客户端和数据库开始释放后继续工作。
            await close_agent_run_registry()
            if settings.scheduler_enabled:
                await get_scheduler().shutdown()
            # 关闭所有站点共享客户端的连接池，再释放数据库
            await get_site_access().aclose()
            await close_media_service()
            await close_image_proxy()
            await dispose_db()
            logger.info("应用已关闭，数据库连接已释放")

    return lifespan
