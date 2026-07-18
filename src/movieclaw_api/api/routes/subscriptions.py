from __future__ import annotations

from fastapi import APIRouter, BackgroundTasks, Depends, Query
from sqlalchemy.ext.asyncio import AsyncSession

from movieclaw_api.exceptions import BadRequestException
from movieclaw_api.schemas.response import ApiResponse, ok
from movieclaw_api.schemas.subscription import (
    ActivityView,
    MediaBrief,
    PreparePayload,
    PrepareView,
    ResolveCandidateView,
    SeasonOverview,
    SubscriptionCreatePayload,
    SubscriptionDetailView,
    SubscriptionPausePayload,
    SubscriptionUpdatePayload,
    SubscriptionView,
)
from movieclaw_api.services.media_discover import get_tmdb_client
from movieclaw_api.services.media_library import MediaLibraryService
from movieclaw_api.services.subscription import SubscriptionService
from movieclaw_api.services.wanted_search import search_wanted
from movieclaw_db.engine import get_session
from movieclaw_media.library import ResolveStatus

router = APIRouter(prefix="/subscriptions", tags=["subscriptions"])


def _service(session: AsyncSession) -> SubscriptionService:
    library = MediaLibraryService(session, get_tmdb_client())
    return SubscriptionService(session, library)


@router.post(
    "/prepare",
    response_model=ApiResponse[PrepareView],
    summary="订阅预检：建档条目并返回季集结构（弹层数据源）",
)
async def prepare_subscription(
    payload: PreparePayload,
    session: AsyncSession = Depends(get_session),
) -> ApiResponse[PrepareView]:
    """幂等预检。TMDB 入口直接建档；豆瓣入口先收敛（命中→ready，
    歧义→candidates 让用户确认后以 tmdb_id 重新 prepare，未收录→not_found）。"""
    service = _service(session)

    if payload.source == "douban":
        if not payload.title:
            raise BadRequestException("豆瓣入口预检必须携带标题")
        library = MediaLibraryService(session, get_tmdb_client())
        resolution, item = await library.resolve_douban(
            payload.kind, payload.title, year=payload.year, douban_id=payload.douban_id
        )
        if resolution.status is ResolveStatus.NOT_FOUND:
            return ok(
                PrepareView(status="not_found"),
                message="TMDB 未收录该条目，暂无法订阅",
            )
        if resolution.status is ResolveStatus.AMBIGUOUS:
            return ok(
                PrepareView(
                    status="ambiguous",
                    candidates=[
                        ResolveCandidateView.from_model(c) for c in resolution.candidates
                    ],
                ),
                message="找到多个可能的条目，请确认是哪一部",
            )
        assert item is not None
        tmdb_id = item.tmdb_id
    else:
        if payload.tmdb_id is None:
            raise BadRequestException("TMDB 入口预检必须携带 tmdb_id")
        tmdb_id = payload.tmdb_id

    item, seasons, existing = await service.prepare(
        payload.kind, tmdb_id, douban_id=payload.douban_id
    )
    return ok(
        PrepareView(
            status="ready",
            media=MediaBrief.from_model(item),
            seasons=[SeasonOverview.from_row(s) for s in seasons],
            existing_subscription_id=existing.id if existing else None,
        )
    )


@router.post(
    "",
    response_model=ApiResponse[SubscriptionDetailView],
    summary="创建订阅（生成初始工单；同条目重复订阅幂等返回已有）",
)
async def create_subscription(
    payload: SubscriptionCreatePayload,
    background_tasks: BackgroundTasks,
    session: AsyncSession = Depends(get_session),
) -> ApiResponse[SubscriptionDetailView]:
    """创建订阅并**立即**踢一次缺口搜索（响应返回后异步执行，走 worker
    同一节流闸门）——首班车不用等最多 5 分钟的定时 tick。"""
    service = _service(session)
    subscription = await service.create(
        payload.kind,
        payload.tmdb_id,
        selected_seasons=payload.selected_seasons,
        follow_future=payload.follow_future,
        rule_set_id=payload.rule_set_id,
        douban_id=payload.douban_id,
    )
    assert subscription.id is not None
    sub, item, wanted = await service.detail(subscription.id)
    background_tasks.add_task(search_wanted)
    return ok(
        SubscriptionDetailView.from_detail(sub, item, wanted),
        message="已加入订阅，正在搜索资源",
    )


@router.get(
    "",
    response_model=ApiResponse[list[SubscriptionView]],
    summary="订阅列表（含工单进度）",
)
async def list_subscriptions(
    kind: str | None = Query(default=None, description="movie / tv，缺省全部"),
    session: AsyncSession = Depends(get_session),
) -> ApiResponse[list[SubscriptionView]]:
    service = _service(session)
    rows = await service.list_with_progress(kind=kind)
    return ok([SubscriptionView.from_model(s, m, c) for s, m, c in rows])


@router.get(
    "/{subscription_id}",
    response_model=ApiResponse[SubscriptionDetailView],
    summary="订阅详情（含工单明细）",
)
async def get_subscription(
    subscription_id: int,
    session: AsyncSession = Depends(get_session),
) -> ApiResponse[SubscriptionDetailView]:
    service = _service(session)
    sub, item, wanted = await service.detail(subscription_id)
    return ok(SubscriptionDetailView.from_detail(sub, item, wanted))


@router.get(
    "/{subscription_id}/activities",
    response_model=ApiResponse[list[ActivityView]],
    summary="订阅活动时间线（系统对该订阅做过的每个动作，时间倒序）",
)
async def list_subscription_activities(
    subscription_id: int,
    limit: int = Query(default=100, ge=1, le=500),
    session: AsyncSession = Depends(get_session),
) -> ApiResponse[list[ActivityView]]:
    service = _service(session)
    rows = await service.activities(subscription_id, limit=limit)
    return ok([ActivityView.from_model(r) for r in rows])


@router.patch(
    "/{subscription_id}",
    response_model=ApiResponse[SubscriptionDetailView],
    summary="修改订阅（季选择/追新/规则组，diff 重算工单）",
)
async def update_subscription(
    subscription_id: int,
    payload: SubscriptionUpdatePayload,
    background_tasks: BackgroundTasks,
    session: AsyncSession = Depends(get_session),
) -> ApiResponse[SubscriptionDetailView]:
    service = _service(session)
    await service.update(
        subscription_id,
        selected_seasons=payload.selected_seasons,
        follow_future=payload.follow_future,
        rule_set_id=payload.rule_set_id,
    )
    sub, item, wanted = await service.detail(subscription_id)
    # diff 可能补了新的补旧工单，同样立即发车
    background_tasks.add_task(search_wanted)
    return ok(SubscriptionDetailView.from_detail(sub, item, wanted), message="订阅已调整")


@router.patch(
    "/{subscription_id}/pause",
    response_model=ApiResponse[SubscriptionDetailView],
    summary="暂停 / 恢复订阅",
)
async def pause_subscription(
    subscription_id: int,
    payload: SubscriptionPausePayload,
    background_tasks: BackgroundTasks,
    session: AsyncSession = Depends(get_session),
) -> ApiResponse[SubscriptionDetailView]:
    service = _service(session)
    await service.set_paused(subscription_id, payload.paused)
    sub, item, wanted = await service.detail(subscription_id)
    if payload.paused:
        message = "已暂停，匹配与搜索将跳过该订阅"
    else:
        message = "已恢复追踪"
        background_tasks.add_task(search_wanted)  # 暂停期间积压的到期工单立即处理
    return ok(SubscriptionDetailView.from_detail(sub, item, wanted), message=message)


@router.delete(
    "/{subscription_id}",
    response_model=ApiResponse[dict],
    summary="删除订阅（不影响已下载内容）",
)
async def delete_subscription(
    subscription_id: int,
    session: AsyncSession = Depends(get_session),
) -> ApiResponse[dict]:
    service = _service(session)
    await service.delete(subscription_id)
    return ok({}, message="已取消订阅")
