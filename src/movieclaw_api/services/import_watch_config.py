"""监听导入规则的配置服务：CRUD 与校验（媒体库之上的独立功能）。

规则 = 源目录 → 目标库 的搬运声明（策略：硬链接/复制），由
``library_ingest`` 引擎消费。校验要点：

- 源目录不得与**任何**库的根路径前缀重叠（双头管理必乱；库侧改根路径
  时做反向校验，见 LibraryConfigService）；
- 源目录全局唯一（数据库唯一索引兜底，这里给可读中文报错）；
- 策略选硬链接时做**同盘检测**（源目录与目标库主根的 st_dev 比对）——
  把"跨文件系统无法硬链"从第一次搬运失败前置到保存配置时；任一目录
  尚不存在（挂载未就绪）时跳过检测，搬运失败的中文引导兜底。
"""

from __future__ import annotations

import logging
import os

from sqlalchemy.ext.asyncio import AsyncSession
from sqlmodel import select

from movieclaw_api.exceptions import BadRequestException, NotFoundException
from movieclaw_db.models import ImportWatch, Library
from movieclaw_db.models.base import utcnow

logger = logging.getLogger("movieclaw_api.import_watch_config")

STRATEGIES = ("hardlink", "copy")


class ImportWatchConfigService:
    """监听导入规则的业务服务。绑定一个数据库会话。"""

    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def list_all(self) -> list[ImportWatch]:
        result = await self._session.execute(select(ImportWatch).order_by(ImportWatch.id))
        return list(result.scalars().all())

    async def get(self, rule_id: int) -> ImportWatch:
        row = await self._session.get(ImportWatch, rule_id)
        if row is None:
            raise NotFoundException(f"监听导入规则不存在：id={rule_id}")
        return row

    async def create(self, *, source_path: str, strategy: str, library_id: int) -> ImportWatch:
        source, library = await self._validate(
            source_path=source_path, strategy=strategy, library_id=library_id
        )
        row = ImportWatch(source_path=source, strategy=strategy, library_id=library_id)
        self._session.add(row)
        await self._session.commit()
        await self._session.refresh(row)
        await _refresh_watcher()
        logger.info(
            "已创建监听导入规则：%s →「%s」（%s）",
            source,
            library.name,
            "硬链接" if strategy == "hardlink" else "复制",
        )
        return row

    async def update(
        self, rule_id: int, *, source_path: str, strategy: str, library_id: int
    ) -> ImportWatch:
        row = await self.get(rule_id)
        source, _library = await self._validate(
            source_path=source_path, strategy=strategy, library_id=library_id, exclude_id=rule_id
        )
        row.source_path = source
        row.strategy = strategy
        row.library_id = library_id
        row.updated_at = utcnow()
        await self._session.commit()
        await self._session.refresh(row)
        await _refresh_watcher()
        return row

    async def delete(self, rule_id: int) -> None:
        row = await self.get(rule_id)
        await self._session.delete(row)
        await self._session.commit()
        await _refresh_watcher()
        logger.info("已删除监听导入规则：%s", row.source_path)

    # -- 校验 --------------------------------------------------------------

    async def _validate(
        self, *, source_path: str, strategy: str, library_id: int, exclude_id: int | None = None
    ) -> tuple[str, Library]:
        source = source_path.strip().rstrip("/")
        if not source or not source.startswith("/"):
            raise BadRequestException("源目录必须是绝对路径")
        if strategy not in STRATEGIES:
            raise BadRequestException("搬运策略必须是硬链接（hardlink）或复制（copy）")

        library = await self._session.get(Library, library_id)
        if library is None:
            raise NotFoundException(f"目标媒体库不存在：id={library_id}")
        if not library.primary_root:
            raise BadRequestException(f"媒体库「{library.name}」没有配置根路径，无法作为导入目标")

        # 与所有库的根路径不重叠（不只是目标库：落在任何库根下都会被扫描双头管理）
        libraries = list((await self._session.execute(select(Library))).scalars().all())
        for lib in libraries:
            for root in lib.root_paths:
                r = root.rstrip("/")
                if source == r or source.startswith(r + "/") or r.startswith(source + "/"):
                    raise BadRequestException(
                        f"源目录与媒体库「{lib.name}」的根路径重叠：{source} ↔ {root}"
                    )

        # 源目录全局唯一
        existing = (
            await self._session.execute(
                select(ImportWatch).where(ImportWatch.source_path == source)
            )
        ).scalar_one_or_none()
        if existing is not None and existing.id != exclude_id:
            raise BadRequestException(f"该源目录已有监听导入规则：{source}")

        # 硬链接的同盘检测：跨盘当场提示，不留到第一次搬运失败
        if strategy == "hardlink":
            try:
                source_dev = os.stat(source).st_dev
                root_dev = os.stat(library.primary_root).st_dev
            except OSError:
                pass  # 目录未就绪（挂载中）：跳过检测，搬运失败的中文引导兜底
            else:
                if source_dev != root_dev:
                    raise BadRequestException(
                        f"源目录与媒体库「{library.name}」的主根不在同一文件系统，"
                        "硬链接无法工作；请把策略改为「复制」，或把两者放到同一存储卷"
                    )
        return source, library


async def resolve_dispatch_dir(session: AsyncSession, library_id: int | None) -> str | None:
    """投递目录：目标库的首条监听导入规则的源目录；没有规则返回 None。

    订阅/手动下载止于投递——把种子投到会被监听导入接管的目录（分离布局），
    或不指定目录退下载器默认（用户想原地入库就把下载器默认目录设在库根，
    库扫描接管）。多条规则指向同一库时取最早创建的一条。
    """
    if library_id is None:
        return None
    rule = (
        (
            await session.execute(
                select(ImportWatch)
                .where(ImportWatch.library_id == library_id)
                .order_by(ImportWatch.id)
            )
        )
        .scalars()
        .first()
    )
    return rule.source_path if rule else None


async def _refresh_watcher() -> None:
    """规则变更后重建监听（监听器未启动时为 no-op）。"""
    from movieclaw_api.services.library_ingest import get_ingest_watcher

    watcher = get_ingest_watcher()
    if watcher is not None:
        await watcher.refresh_watches()
