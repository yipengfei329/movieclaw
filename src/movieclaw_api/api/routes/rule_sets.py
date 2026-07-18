from __future__ import annotations

from fastapi import APIRouter, Depends
from sqlalchemy.ext.asyncio import AsyncSession

from movieclaw_api.schemas.response import ApiResponse, ok
from movieclaw_api.schemas.subscription import RuleSetPayload, RuleSetView
from movieclaw_api.services.rule_sets import RuleSetService
from movieclaw_db.engine import get_session

router = APIRouter(prefix="/rule-sets", tags=["rule-sets"])


@router.get(
    "",
    response_model=ApiResponse[list[RuleSetView]],
    summary="规则组列表（首次访问自动创建默认组）",
)
async def list_rule_sets(
    session: AsyncSession = Depends(get_session),
) -> ApiResponse[list[RuleSetView]]:
    service = RuleSetService(session)
    rows = await service.list_all()
    return ok([RuleSetView.from_model(r) for r in rows])


@router.post(
    "",
    response_model=ApiResponse[RuleSetView],
    summary="创建规则组",
)
async def create_rule_set(
    payload: RuleSetPayload,
    session: AsyncSession = Depends(get_session),
) -> ApiResponse[RuleSetView]:
    service = RuleSetService(session)
    row = await service.create(payload.name, payload.spec)
    return ok(RuleSetView.from_model(row), message="规则组已创建")


@router.put(
    "/{rule_set_id}",
    response_model=ApiResponse[RuleSetView],
    summary="更新规则组（只影响之后的匹配评估）",
)
async def update_rule_set(
    rule_set_id: int,
    payload: RuleSetPayload,
    session: AsyncSession = Depends(get_session),
) -> ApiResponse[RuleSetView]:
    service = RuleSetService(session)
    row = await service.update(rule_set_id, name=payload.name, spec=payload.spec)
    return ok(RuleSetView.from_model(row), message="规则组已更新")


@router.delete(
    "/{rule_set_id}",
    response_model=ApiResponse[dict],
    summary="删除规则组（默认组与被引用的组禁删）",
)
async def delete_rule_set(
    rule_set_id: int,
    session: AsyncSession = Depends(get_session),
) -> ApiResponse[dict]:
    service = RuleSetService(session)
    await service.delete(rule_set_id)
    return ok({}, message="已删除")
