"""种子快照索引的数据访问层。

本模块包含两部分：
1. ``TorrentObservation``——面向持久化的**观测输入**。这是「上游前提 A」的落点：
   易变字段用 ``X | None`` 表达三态，把「解析不到该置什么」的决定权交给消费方
   （同步任务），而不是像 tracker 层的 ``TorrentListItem`` 那样把「未解析到」
   直接塌缩成 0/False/1.0。同步任务据来源（RSS 缺易变层等）构造它。
2. ``TorrentRepository``——封装对 ``SiteTorrent`` / ``SiteSyncCursor`` 的读写，
   核心是**空值安全的 upsert 合并**（首次入库与刷新策略不同，见各方法注释）。
"""

from __future__ import annotations

from datetime import datetime, timedelta

from pydantic import BaseModel, field_validator, model_validator
from sqlalchemy import func
from sqlalchemy.ext.asyncio import AsyncSession
from sqlmodel import select

from movieclaw_db.models.base import utcnow
from movieclaw_db.models.site_torrent import (
    SiteSyncCursor,
    SiteTorrent,
    TorrentSource,
)


class TorrentObservation(BaseModel):
    """一次对某个种子的观测结果（写入 SiteTorrent 的输入）。

    三态语义：字段为 ``None`` 一律表示「本次未观测到」，由 upsert 决定「保留旧值」
    还是「首次入库落 NULL」；只有非 None 才会真正写入。构造时顺便完成**异常值归一**
    （见各 validator），把脏值挡在入库之前。
    """

    # -- 身份（必填）------------------------------------------------------
    site_id: str
    torrent_id: str
    source: TorrentSource

    # -- 静态层（缺则 None）----------------------------------------------
    title: str  # 硬不变量：空标题的观测在构造阶段即被拒绝（见 validator）
    subtitle: str = ""
    category: str | None = None
    site_category_id: str | None = None
    size_bytes: int | None = None
    size_text: str | None = None
    publish_time: datetime | None = None
    uploader: str = ""

    # -- 易变层（None=未观测，务必与 0/1.0/False 区分）--------------------
    seeders: int | None = None
    leechers: int | None = None
    snatched: int | None = None
    download_volume_factor: float | None = None
    upload_volume_factor: float | None = None
    free_deadline: datetime | None = None

    # -- H&R（None=站点不提供/未适配，True/False=真观测到）-----------------
    hit_and_run: bool | None = None

    # -- 扩充层（由消费方调用 movieclaw_enrich 算好传入；None=本次未扩充）--
    attrs: dict | None = None
    enrich_version: int | None = None

    # -- 详情层（仅 DETAIL 来源填充）-------------------------------------
    imdb_id: str | None = None
    douban_id: str | None = None

    # -- 链接 ------------------------------------------------------------
    detail_url: str | None = None
    download_url: str | None = None

    @field_validator("title")
    @classmethod
    def _title_not_blank(cls, v: str) -> str:
        """标题去空白后不得为空——空标题的种子在索引里没有意义，直接拒绝构造。"""
        v = (v or "").strip()
        if not v:
            raise ValueError("种子标题为空，拒绝入库")
        return v

    @field_validator("seeders", "leechers", "snatched")
    @classmethod
    def _non_negative_count(cls, v: int | None) -> int | None:
        """计数为负数属解析异常——归一为 None（未观测），不存负值。"""
        if v is not None and v < 0:
            return None
        return v

    @field_validator("size_bytes")
    @classmethod
    def _size_positive(cls, v: int | None) -> int | None:
        """真实种子不会是 0 或负字节——非正数视为未解析到，归一为 None。"""
        if v is not None and v <= 0:
            return None
        return v

    @field_validator("download_volume_factor")
    @classmethod
    def _download_factor_in_range(cls, v: float | None) -> float | None:
        """下载系数只可能落在 [0, 1]，越界视为异常，归一为 None。"""
        if v is not None and not (0.0 <= v <= 1.0):
            return None
        return v

    @field_validator("upload_volume_factor")
    @classmethod
    def _upload_factor_in_range(cls, v: float | None) -> float | None:
        """上传系数不小于 1（1=正常，2=双倍），越界视为异常，归一为 None。"""
        if v is not None and v < 1.0:
            return None
        return v

    @model_validator(mode="after")
    def _normalize_free_deadline(self) -> TorrentObservation:
        """归一促销截止时间中的哨兵/过期值。

        - ``datetime.max``：tracker 层用它表示「长期免费无明确截止」，不应把 9999 年
          写进库，归一为 None（配合 is_free 表达「免费但无截止」）。
        - 朴素/带时区混用：本项目库内统一 naive UTC，这里剥掉 tzinfo 以免比较报错。
        """
        dl = self.free_deadline
        if dl is not None:
            if dl.tzinfo is not None:
                dl = dl.replace(tzinfo=None)
            if dl >= datetime.max.replace(microsecond=0):
                dl = None
            self.free_deadline = dl
        return self

    @property
    def has_volatile(self) -> bool:
        """本次观测是否带到了任一易变字段（决定要不要刷新 volatile_refreshed_at）。"""
        return any(
            v is not None
            for v in (
                self.seeders,
                self.leechers,
                self.snatched,
                self.download_volume_factor,
                self.upload_volume_factor,
            )
        )


class UpsertStats(BaseModel):
    """一批 upsert 的统计，供同步任务更新游标与自适应节奏。"""

    inserted: int = 0  # 新入库（此前不存在）
    updated: int = 0   # 命中已有并刷新


class TorrentRepository:
    """种子快照索引的数据访问层。

    封装 ``SiteTorrent`` / ``SiteSyncCursor`` 的读写，把空值安全的合并逻辑收敛在此。
    沿用项目「先查后写」的可移植 upsert 风格（见 CookieRepository），不使用数据库
    方言相关的原生 UPSERT。
    """

    # 首次入库时按静态层字段整体赋值的白名单（缺失则各自为 None，符合三态）
    _STATIC_FILL_FIELDS = (
        "subtitle",
        "category",
        "site_category_id",
        "size_bytes",
        "size_text",
        "publish_time",
        "uploader",
        "detail_url",
        "download_url",
    )
    _VOLATILE_FIELDS = (
        "seeders",
        "leechers",
        "snatched",
        "download_volume_factor",
        "upload_volume_factor",
    )

    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    # -- 查询 --------------------------------------------------------------

    async def get(self, site_id: str, torrent_id: str) -> SiteTorrent | None:
        """按 (站点, 种子ID) 取单条快照；不存在返回 None。"""
        result = await self._session.execute(
            select(SiteTorrent).where(
                SiteTorrent.site_id == site_id,
                SiteTorrent.torrent_id == torrent_id,
            )
        )
        return result.scalar_one_or_none()

    async def existing_ids(self, site_id: str, torrent_ids: list[str]) -> set[str]:
        """批量判重：返回给定候选中「已在索引里」的 torrent_id 集合。

        增量同步用它判断「这一页有没有碰到已知种子」，从而决定是否停止翻页。
        """
        if not torrent_ids:
            return set()
        result = await self._session.execute(
            select(SiteTorrent.torrent_id).where(
                SiteTorrent.site_id == site_id,
                SiteTorrent.torrent_id.in_(torrent_ids),  # type: ignore[attr-defined]
            )
        )
        return set(result.scalars().all())

    async def count_by_site(self) -> dict[str, int]:
        """按站点统计已缓存的种子数——站点配置页「缓存感知」展示用。"""
        result = await self._session.execute(
            select(SiteTorrent.site_id, func.count()).group_by(SiteTorrent.site_id)
        )
        return dict(result.all())  # type: ignore[arg-type]

    async def all_cursors(self) -> list[SiteSyncCursor]:
        """取全部站点的同步游标——与 count_by_site 配合组装同步状态视图。"""
        result = await self._session.execute(select(SiteSyncCursor))
        return list(result.scalars().all())

    # -- 写入：空值安全合并 ------------------------------------------------

    async def upsert(self, obs: TorrentObservation) -> bool:
        """写入或刷新单条种子快照。返回 True 表示新入库、False 表示刷新已有。

        合并策略（三态铁律的落地）：
        - **首次入库**：静态层按观测赋值（缺则 None）；易变层仅写「本次观测到」的；
          详情层仅当来源为 DETAIL 才填。
        - **刷新已有**：
          * 易变层——只覆盖本次非 None 的字段，None（未观测）保留旧值；
          * 静态层——采用「补空」策略：仅当旧值为空且本次有值才填，
            绝不用列表刷新去改写已有静态值（避免重解析噪声/截断标题覆盖好数据）；
          * 详情层——仅 DETAIL 来源可写，列表/RSS/搜索刷新**绝不触碰**
            imdb/douban，避免把详情页专属字段清空。
        """
        row = await self.get(obs.site_id, obs.torrent_id)
        if row is None:
            self._session.add(self._build_new(obs))
            await self._session.commit()
            return True

        self._merge_into(row, obs)
        await self._session.commit()
        return False

    async def bulk_upsert(self, observations: list[TorrentObservation]) -> UpsertStats:
        """批量 upsert，返回统计。同一批内按 torrent_id 去重（保留首条）。"""
        stats = UpsertStats()
        seen: set[str] = set()
        for obs in observations:
            if obs.torrent_id in seen:
                continue
            seen.add(obs.torrent_id)
            row = await self.get(obs.site_id, obs.torrent_id)
            if row is None:
                self._session.add(self._build_new(obs))
                stats.inserted += 1
            else:
                self._merge_into(row, obs)
                stats.updated += 1
        await self._session.commit()
        return stats

    def _build_new(self, obs: TorrentObservation) -> SiteTorrent:
        """由观测构造一条全新的 SiteTorrent（首次入库）。"""
        now = utcnow()
        row = SiteTorrent(
            site_id=obs.site_id,
            torrent_id=obs.torrent_id,
            title=obs.title,
            source=obs.source,
            last_seen_at=now,
        )
        # 静态层：整体赋值（观测里缺失的本就是 None，符合三态）
        for name in self._STATIC_FILL_FIELDS:
            setattr(row, name, getattr(obs, name))
        # 易变层：仅写本次观测到的；派生 is_free / free_deadline
        self._apply_volatile(row, obs, now)
        # H&R 与扩充层：仅写本次带到的
        self._apply_enrich(row, obs)
        # 详情层：仅 DETAIL 来源
        if obs.source is TorrentSource.DETAIL:
            self._apply_detail(row, obs, now)
        return row

    def _merge_into(self, row: SiteTorrent, obs: TorrentObservation) -> None:
        """把一次观测合并进已有行（刷新），遵循三态与分层覆盖规则。"""
        now = utcnow()
        # 静态层：补空——仅当旧值为空且本次有值才填
        for name in self._STATIC_FILL_FIELDS:
            new_val = getattr(obs, name)
            if new_val in (None, "") :
                continue
            if _is_empty(getattr(row, name)):
                setattr(row, name, new_val)
        # 易变层：只覆盖本次非 None 的字段
        self._apply_volatile(row, obs, now)
        # H&R 与扩充层：只覆盖本次带到的
        self._apply_enrich(row, obs)
        # 详情层：仅 DETAIL 来源可写
        if obs.source is TorrentSource.DETAIL:
            self._apply_detail(row, obs, now)
        # 台账
        row.last_seen_at = now
        row.source = obs.source
        row.updated_at = now

    def _apply_volatile(
        self, row: SiteTorrent, obs: TorrentObservation, now: datetime
    ) -> None:
        """写入易变层：只覆盖本次观测到（非 None）的字段。"""
        for name in self._VOLATILE_FIELDS:
            new_val = getattr(obs, name)
            if new_val is not None:
                setattr(row, name, new_val)
        # is_free / free_deadline 仅在本次拿到下载系数时才一并更新，保持派生一致
        if obs.download_volume_factor is not None:
            row.is_free = obs.download_volume_factor == 0.0
            row.free_deadline = obs.free_deadline
        if obs.has_volatile:
            row.volatile_refreshed_at = now

    def _apply_enrich(self, row: SiteTorrent, obs: TorrentObservation) -> None:
        """写入 H&R 与扩充属性：均只在本次观测带到（非 None）时覆盖。

        attrs 与 enrich_version 成对更新——版本号是"这份 attrs 由哪版提取器算出"
        的凭证，绝不允许两者不同步。
        """
        if obs.hit_and_run is not None:
            row.hit_and_run = obs.hit_and_run
        if obs.attrs is not None:
            row.attrs = obs.attrs
            row.enrich_version = obs.enrich_version

    def _apply_detail(
        self, row: SiteTorrent, obs: TorrentObservation, now: datetime
    ) -> None:
        """写入详情层：补空 imdb/douban，记录富化时间。"""
        if obs.imdb_id:
            row.imdb_id = obs.imdb_id
        if obs.douban_id:
            row.douban_id = obs.douban_id
        row.detail_enriched_at = now

    # -- 同步游标 ----------------------------------------------------------

    async def get_cursor(self, site_id: str) -> SiteSyncCursor | None:
        result = await self._session.execute(
            select(SiteSyncCursor).where(SiteSyncCursor.site_id == site_id)
        )
        return result.scalar_one_or_none()

    async def ensure_cursor(self, site_id: str) -> SiteSyncCursor:
        """取站点游标，不存在则以「此刻」为 t0 创建——用户添加站点时调用。"""
        cursor = await self.get_cursor(site_id)
        if cursor is None:
            cursor = SiteSyncCursor(site_id=site_id, tracking_since=utcnow())
            self._session.add(cursor)
            await self._session.commit()
            await self._session.refresh(cursor)
        return cursor

    async def delete_site_data(self, site_id: str) -> int:
        """删除某站点的全部快照与同步游标——用户移除站点时调用。

        返回删除的种子行数。清掉游标是关键：否则同站被重新添加时会命中旧的高水位线，
        误判为「非首刷」而跳过基线、拿着过期的高水位去回补。删除是显式用户操作，
        整站清理符合预期。
        """
        rows = (
            await self._session.execute(
                select(SiteTorrent).where(SiteTorrent.site_id == site_id)
            )
        ).scalars().all()
        for row in rows:
            await self._session.delete(row)
        cursor = await self.get_cursor(site_id)
        if cursor is not None:
            await self._session.delete(cursor)
        await self._session.commit()
        return len(rows)

    async def update_cursor_after_sync(
        self,
        site_id: str,
        *,
        newest_publish_time: datetime | None = None,
        newest_torrent_id: str | None = None,
        new_count: int | None = None,
        full_page: bool | None = None,
        error: str | None = None,
        consecutive_failures: int = 0,
        next_interval_seconds: int | None = None,
    ) -> None:
        """一轮同步结束后回写游标进度、自适应节奏输入与下次到期时刻。

        ``next_interval_seconds`` 由同步任务算出（依 new_count/full_page 升降，
        失败时含指数退避）；据它同时更新 ``sync_interval_seconds`` 与
        ``next_sync_at = now + 间隔``，供下一次 tick 判断该站是否到期。
        ``error`` 为 None 表示本轮成功，顺带刷新 ``last_success_at``。
        """
        cursor = await self.ensure_cursor(site_id)
        now = utcnow()
        cursor.last_sync_at = now
        if error is None:
            cursor.last_success_at = now
        # 高水位线只增不减：新观测更晚才前移
        if newest_publish_time is not None and (
            cursor.newest_publish_time is None
            or newest_publish_time > cursor.newest_publish_time
        ):
            cursor.newest_publish_time = newest_publish_time
            cursor.newest_torrent_id = newest_torrent_id
        cursor.last_new_count = new_count
        cursor.last_full_page = full_page
        cursor.last_error = error
        cursor.consecutive_failures = consecutive_failures
        if next_interval_seconds is not None:
            cursor.sync_interval_seconds = next_interval_seconds
            cursor.next_sync_at = now + timedelta(seconds=next_interval_seconds)
        cursor.updated_at = now
        await self._session.commit()


def _is_empty(value: object) -> bool:
    """静态层补空判断：None 或空串视为「空、可填充」。"""
    return value is None or value == ""
