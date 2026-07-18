"""跨站点聚合搜索——把一个关键词并发投递给所有可用站点，合并结果。

为什么可以并发
--------------
搜索是**只读**操作，不像种子同步那样对同站有写唯一键约束，因此天然适合并发扇出：
本模块对所有「已启用且验证通过」的站点同时发起 ``search``，并以两种口径消费结果——
``stream_search_all_sites`` 按站点完成先后**流式**产出事件（SSE 端点用，快站先出结果），
``search_all_sites`` 等全部完成后合并返回（阻塞版，基于前者实现）。

错误隔离（核心不变量）
--------------------
单站失败（认证过期 / 网络异常 / 站点改版解析失败）**绝不能拖垮整次搜索**。每个站点
的搜索都包在 ``_search_one`` 的 try/except 里，失败降级为「该站 0 条 + 可读中文原因」，
其它站点照常返回。错误文案复用 ``verification._friendly_error``，与站点验证的报错口径一致。

站点实例一律通过 ``SiteAccessManager`` 复用（已认证、连接池共享），调用方**不 close**。
"""

from __future__ import annotations

import asyncio
import logging
import time
from collections.abc import AsyncIterator

from pydantic import BaseModel

from movieclaw_api.schemas.search import (
    SearchResponse,
    SearchStreamDone,
    SearchStreamSite,
    SearchStreamStart,
    SiteSearchStatus,
    SiteStreamError,
    SiteStreamResult,
    TorrentHit,
)
from movieclaw_api.services.site_access import get_site_access
from movieclaw_api.services.verification import _friendly_error
from movieclaw_db.engine import get_database
from movieclaw_db.models.site_credential import ConfigStatus, SiteCredential
from movieclaw_db.repositories.credential_repo import CredentialRepository
from movieclaw_enrich import enrich
from movieclaw_tracker.models import SearchQuery, TorrentCategory
from movieclaw_tracker.registry import SiteNotFoundError, get_site_config

logger = logging.getLogger("movieclaw_api.site_search")


def _display_name(site_id: str) -> str:
    """取站点展示名；站点未注册时退回 site_id，保证结果始终可标注来源。"""
    try:
        return get_site_config(site_id).display_name
    except SiteNotFoundError:
        return site_id


async def _active_sites() -> list[SiteCredential]:
    """取所有「已启用且验证通过」的站点——只有这些才参与搜索。

    判据与种子同步保持一致（``enabled`` 且 ``status == ACTIVE``）：``enabled`` 只是用户
    意愿开关，真正「能发起访问」还要求验证通过。
    """
    async with get_database().session() as session:
        creds = await CredentialRepository(session).list_all()
    return [c for c in creds if c.enabled and c.status == ConfigStatus.ACTIVE]


async def _search_one(
    cred: SiteCredential, query: SearchQuery
) -> tuple[list[TorrentHit], SiteSearchStatus]:
    """搜索单个站点，全程吞异常：失败降级为带 error 的状态，不向上抛。

    成功与失败的状态里都带 ``elapsed_ms``：失败站的耗时尤其有诊断价值
    （十几秒后才失败的基本是超时，秒失败的多半是认证/解析问题）。
    """
    site_id = cred.site_id
    name = _display_name(site_id)
    started = time.monotonic()
    elapsed = lambda: int((time.monotonic() - started) * 1000)  # noqa: E731
    try:
        site = await get_site_access().get(site_id)  # 已认证共享实例，勿 close
        result = await site.search(query)
        # 给每条结果挂上来源站点标识 + 扩充属性（纯本地正则，微秒级，直接内联算）
        hits = [
            TorrentHit(
                site_id=site_id,
                site_name=name,
                attrs=enrich(
                    item.title,
                    item.subtitle,
                    item.category.value if item.category else None,
                ),
                **item.model_dump(),
            )
            for item in result.items
        ]
        return hits, SiteSearchStatus(
            site_id=site_id, site_name=name, count=len(hits), elapsed_ms=elapsed()
        )
    except Exception as exc:  # noqa: BLE001 —— 单站失败必须隔离，不能拖垮整次搜索
        reason = _friendly_error(exc)
        logger.warning("站点 %s 搜索失败：%s", site_id, reason)
        return [], SiteSearchStatus(
            site_id=site_id, site_name=name, count=0, error=reason, elapsed_ms=elapsed()
        )


async def stream_search_all_sites(
    keyword: str,
    categories: list[TorrentCategory] | None = None,
    site_ids: list[str] | None = None,
    label: str | None = None,
    page: int = 1,
) -> AsyncIterator[tuple[str, BaseModel]]:
    """流式跨站搜索：按「站点实际完成的先后」逐个产出事件，供 SSE 端点直接转发。

    事件序列固定为 ``start → site_start × N → (site_result | site_error) × N → done``
    （载荷定义见 schemas.search 的流式事件段）。快的站点先出结果，前端边收边渲染，
    彻底摆脱「最慢站点决定整体等待时间」的木桶效应。

    错误隔离口径与阻塞版完全一致：单站失败降级为 ``site_error`` 事件，绝不中断整个流。
    调用方（客户端断开等）提前关闭生成器时，finally 会取消所有未完成的站点搜索任务，
    不留孤儿请求。

    :param keyword: 关键词（支持 IMDb ID，具体识别由各站实现决定）。
    :param categories: 分类组合过滤（tracker 层原生支持多分类）；空/None 表示不限分类。
    :param site_ids: 站点子集；空/None 表示全部可用站点。勾选的站点当前不可用
        （禁用/验证未通过）时直接跳过，不产生错误——口径与「全部站点」一致。
    :param label: 本次搜索的展示名（分类中文名/自定义分类名），原样回显给前端。
    :param page: 页码（各站点独立分页，不做跨站统一分页）。
    """
    sites = await _active_sites()
    if site_ids:
        wanted = set(site_ids)
        sites = [c for c in sites if c.site_id in wanted]
    query = SearchQuery(keyword=keyword, categories=categories or None, page=page)
    started = time.monotonic()

    yield (
        "start",
        SearchStreamStart(
            keyword=keyword,
            label=label,
            categories=[c.value for c in categories] if categories else [],
            page=page,
            sites=[
                SearchStreamSite(site_id=c.site_id, site_name=_display_name(c.site_id))
                for c in sites
            ],
        ),
    )

    # 扇出并发：先建齐所有任务再逐个宣告 site_start，各站从此刻起同时在跑
    tasks = [asyncio.create_task(_search_one(c, query)) for c in sites]
    for c in sites:
        yield (
            "site_start",
            SearchStreamSite(site_id=c.site_id, site_name=_display_name(c.site_id)),
        )

    total = 0
    statuses: list[SiteSearchStatus] = []
    try:
        # as_completed：谁先搜完谁先出事件，这正是流式搜索的全部意义
        for fut in asyncio.as_completed(tasks):
            hits, status = await fut
            statuses.append(status)
            elapsed_ms = status.elapsed_ms or 0
            if status.error is not None:
                yield (
                    "site_error",
                    SiteStreamError(
                        site_id=status.site_id,
                        site_name=status.site_name,
                        error=status.error,
                        elapsed_ms=elapsed_ms,
                    ),
                )
            else:
                total += len(hits)
                yield (
                    "site_result",
                    SiteStreamResult(
                        site_id=status.site_id,
                        site_name=status.site_name,
                        count=len(hits),
                        elapsed_ms=elapsed_ms,
                        items=hits,
                    ),
                )
        yield (
            "done",
            SearchStreamDone(
                total=total,
                elapsed_ms=int((time.monotonic() - started) * 1000),
                sites=statuses,
            ),
        )
    finally:
        # 客户端中途断开时生成器被提前关闭：取消尚未完成的站点搜索，不留孤儿请求
        for task in tasks:
            task.cancel()


async def search_all_sites(
    keyword: str,
    categories: list[TorrentCategory] | None = None,
    site_ids: list[str] | None = None,
    label: str | None = None,
    page: int = 1,
) -> SearchResponse:
    """并发搜索可用站点并合并结果（阻塞版：等全部站点返回后一次性给出）。

    基于 ``stream_search_all_sites`` 实现——消费整个事件流再组装成 ``SearchResponse``，
    与流式端点共享同一套扇出/隔离逻辑，避免两头维护。参数含义见流式版 docstring。
    """
    items: list[TorrentHit] = []
    statuses: list[SiteSearchStatus] = []
    async for event, payload in stream_search_all_sites(
        keyword=keyword, categories=categories, site_ids=site_ids, label=label, page=page
    ):
        if event == "site_result":
            assert isinstance(payload, SiteStreamResult)
            items.extend(payload.items)
        elif event == "done":
            assert isinstance(payload, SearchStreamDone)
            statuses = payload.sites

    return SearchResponse(
        keyword=keyword,
        label=label,
        categories=[c.value for c in categories] if categories else [],
        total=len(items),
        items=items,
        sites=statuses,
    )
