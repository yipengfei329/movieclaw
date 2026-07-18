"""界面偏好接口：全站页面样式设定的读写。

设定按页面分组存在 ``ui.preferences`` 配置域（见 settings.schemas 的
``UiPreferencesSetting``），本路由只做整体读写透传——纯用户偏好、无敏感
字段、无业务校验（结构校验由 Pydantic 完成，未知字段按前向兼容忽略），
因此请求/响应体直接复用配置域模型，不另抄一份 schema。

前端在应用启动时 GET 一次、Context 全站共享；每次改动 PUT 整体覆盖。
未来新增页面的样式设定：配置域模型加字段 → 前端类型同步 → 无需动本文件。
"""

from __future__ import annotations

from fastapi import APIRouter

from movieclaw_api.schemas.response import ApiResponse, ok
from movieclaw_api.settings.schemas import (
    UiPreferencesSetting,
    get_ui_preferences,
    save_ui_preferences,
)

router = APIRouter(prefix="/ui", tags=["ui"])


@router.get(
    "/preferences",
    response_model=ApiResponse[UiPreferencesSetting],
    summary="读取界面偏好（按页面分组的样式设定）",
)
async def get_preferences() -> ApiResponse[UiPreferencesSetting]:
    """返回全站界面样式偏好；从未配置过的页面返回其默认值。"""
    return ok(await get_ui_preferences())


@router.put(
    "/preferences",
    response_model=ApiResponse[UiPreferencesSetting],
    summary="保存界面偏好（整体覆盖）",
)
async def update_preferences(
    payload: UiPreferencesSetting,
) -> ApiResponse[UiPreferencesSetting]:
    """整体覆盖式保存界面偏好，返回保存后的值。"""
    return ok(await save_ui_preferences(payload), message="界面设置已保存")
