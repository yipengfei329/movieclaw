"""扩充属性的存量重算——"升级程序即全量生效"机制的落地。

提取逻辑改动时只需把 ``movieclaw_enrich.ENRICH_VERSION`` +1；应用启动阶段调用
本模块，把库里 ``enrich_version`` 落后（或从未扩充）的种子行按当前提取器重算。
attrs 是从行内已有的 title/subtitle 纯本地推导的，不发任何站点请求，
几万行也只是秒级——因此直接在启动流程里同步执行，不搞后台任务的复杂度。

分批提交：每批一个短会话 + commit，避免超大库时单事务过长；任一批失败只记
可读中文日志，不阻断启动（下次启动会重试，行为幂等）。
"""

from __future__ import annotations

import logging

from sqlalchemy import or_
from sqlmodel import select

from movieclaw_db.engine import get_database
from movieclaw_db.models.site_torrent import SiteTorrent
from movieclaw_enrich import ENRICH_VERSION, enrich

logger = logging.getLogger("movieclaw_api.enrich_backfill")

_BATCH_SIZE = 500


async def reenrich_stale_torrents() -> int:
    """把扩充版本过期（或从未扩充）的种子行重算一遍，返回处理行数。"""
    total = 0
    db = get_database()
    while True:
        try:
            async with db.session() as session:
                rows = (
                    (
                        await session.execute(
                            select(SiteTorrent)
                            .where(
                                or_(
                                    SiteTorrent.enrich_version.is_(None),  # type: ignore[union-attr]
                                    SiteTorrent.enrich_version != ENRICH_VERSION,
                                )
                            )
                            .limit(_BATCH_SIZE)
                        )
                    )
                    .scalars()
                    .all()
                )
                if not rows:
                    break
                for row in rows:
                    row.attrs = enrich(row.title, row.subtitle, row.category).model_dump(
                        mode="json", exclude_defaults=True
                    )
                    row.enrich_version = ENRICH_VERSION
                await session.commit()
                total += len(rows)
        except Exception:  # noqa: BLE001 -- 重算失败不阻断启动，下次启动幂等重试
            logger.warning("扩充属性重算中断（已完成 %d 条），下次启动将继续", total, exc_info=True)
            break
    if total:
        logger.info("已按提取器 v%d 重算 %d 条种子的扩充属性", ENRICH_VERSION, total)
    return total
