from __future__ import annotations

from fastapi import APIRouter, BackgroundTasks, Depends
from sqlalchemy.ext.asyncio import AsyncSession

from movieclaw_api.schemas.llm import LlmPresetView, LlmProviderPayload, LlmProviderView
from movieclaw_api.schemas.response import ApiResponse, ok
from movieclaw_api.services.llm_config import LlmConfigService, verify_llm_provider
from movieclaw_db.engine import get_session
from movieclaw_llm.providers import list_presets

router = APIRouter(prefix="/llm", tags=["llm"])


@router.get(
    "/presets",
    response_model=ApiResponse[list[LlmPresetView]],
    summary="列出可接入的供应商类型及其模型目录",
)
async def list_llm_presets() -> ApiResponse[list[LlmPresetView]]:
    """返回内置供应商预设（OpenAI / 阿里云百炼 / 通用兼容端点），
    设置页据此渲染类型选项、端点默认值与模型选择提示。"""
    return ok([LlmPresetView.from_preset(p) for p in list_presets()])


@router.get(
    "/provider",
    response_model=ApiResponse[LlmProviderView | None],
    summary="获取当前的模型供应商配置",
)
async def get_llm_provider(
    session: AsyncSession = Depends(get_session),
) -> ApiResponse[LlmProviderView | None]:
    """单例配置：尚未配置时 data 为 null（不是 404），设置页据此渲染空态。"""
    service = LlmConfigService(session)
    row = await service.get_or_none()
    return ok(LlmProviderView.from_model(row) if row else None)


@router.put(
    "/provider",
    response_model=ApiResponse[LlmProviderView],
    summary="保存模型供应商配置（保存后异步测试连接）",
)
async def save_llm_provider(
    payload: LlmProviderPayload,
    background_tasks: BackgroundTasks,
    session: AsyncSession = Depends(get_session),
) -> ApiResponse[LlmProviderView]:
    """单例 upsert：有配置则整体覆盖。保存后状态置 pending 并在后台
    用默认模型发一次最小对话验证；前端可轮询 GET /llm/provider 观察
    status：pending → verifying → active / failed（见 last_error）。"""
    service = LlmConfigService(session)
    await service.upsert(
        provider_type=payload.provider_type,
        base_url=payload.base_url,
        api_key=payload.api_key,
        default_model=payload.default_model,
        extra_models=payload.extra_models,
    )
    row = await service.start_verification()
    background_tasks.add_task(verify_llm_provider)
    return ok(LlmProviderView.from_model(row), message="已保存，正在测试模型连接")


@router.post(
    "/provider/verify",
    response_model=ApiResponse[LlmProviderView],
    summary="手动重新测试模型连接",
)
async def reverify_llm_provider(
    background_tasks: BackgroundTasks,
    session: AsyncSession = Depends(get_session),
) -> ApiResponse[LlmProviderView]:
    """行为：未配置 → 404；已在测试中 → 409；否则同步占位为 VERIFYING
    并在后台重新测试。"""
    service = LlmConfigService(session)
    row = await service.start_verification()
    background_tasks.add_task(verify_llm_provider)
    return ok(LlmProviderView.from_model(row), message="已重新发起连接测试")


@router.delete(
    "/provider",
    response_model=ApiResponse[dict],
    summary="删除模型供应商配置",
)
async def delete_llm_provider(
    session: AsyncSession = Depends(get_session),
) -> ApiResponse[dict]:
    service = LlmConfigService(session)
    await service.delete()
    return ok({}, message="已删除")
