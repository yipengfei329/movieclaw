from __future__ import annotations

import logging

from fastapi import APIRouter, BackgroundTasks, Depends
from sqlalchemy.ext.asyncio import AsyncSession

from movieclaw_api.api.deps import require_login, require_sync_token
from movieclaw_api.core.config import get_settings
from movieclaw_api.exceptions import NotFoundException
from movieclaw_api.schemas.extension import (
    CookiePushRequest,
    CookieSyncResult,
    ExtensionSiteView,
    PingResult,
    SyncTokenView,
)
from movieclaw_api.schemas.response import ApiResponse, ok
from movieclaw_api.services import SiteCatalogService, SiteConfigService, verify_site
from movieclaw_api.settings.schemas import (
    generate_sync_token,
    get_sync_setting,
    revoke_sync_token,
)
from movieclaw_db.engine import get_session
from movieclaw_db.models.site_credential import AuthType, ConfigStatus
from movieclaw_db.repositories.credential_repo import CredentialRepository

logger = logging.getLogger("movieclaw_api.extension")

router = APIRouter(prefix="/extension", tags=["extension"])


# ===========================================================================
# 插件侧接口（需同步令牌鉴权）
# ===========================================================================


@router.get(
    "/ping",
    response_model=ApiResponse[PingResult],
    summary="连接与令牌自检",
    dependencies=[Depends(require_sync_token)],
)
async def ping() -> ApiResponse[PingResult]:
    """插件"测试连接"按钮调用：能走到这里即代表地址可达且令牌有效。"""
    return ok(PingResult(app_name=get_settings().app_name))


@router.get(
    "/sites",
    response_model=ApiResponse[list[ExtensionSiteView]],
    summary="列出支持 Cookie 同步的站点及配置状态",
    dependencies=[Depends(require_sync_token)],
)
async def list_cookie_sites(
    session: AsyncSession = Depends(get_session),
) -> ApiResponse[list[ExtensionSiteView]]:
    """返回所有"支持 cookie 授权"的站点及其匹配域名与当前配置状态。

    插件据此判断"当前标签页所在站点是否被 MovieClaw 支持"，并展示已配置/已可用等状态。
    """
    catalog = SiteCatalogService()
    configured = {c.site_id: c for c in await CredentialRepository(session).list_all()}

    views: list[ExtensionSiteView] = []
    for cfg in catalog.list_catalog():
        # 只暴露支持 cookie 的站点——API-Key 站点（如 M-Team）不走本插件同步
        if "cookie" not in cfg.supported_auth_types:
            continue
        cred = configured.get(cfg.site_id)
        views.append(
            ExtensionSiteView(
                site_id=cfg.site_id,
                display_name=cfg.display_name,
                domain=catalog.site_domain(cfg),
                configured=cred is not None,
                status=cred.status if cred else None,
                usable=bool(cred and cred.enabled and cred.status == ConfigStatus.ACTIVE),
            )
        )
    return ok(views)


@router.post(
    "/cookies",
    response_model=ApiResponse[CookieSyncResult],
    summary="推送某站点的 Cookie（保存后异步验证）",
    dependencies=[Depends(require_sync_token)],
)
async def push_cookies(
    payload: CookiePushRequest,
    background_tasks: BackgroundTasks,
    session: AsyncSession = Depends(get_session),
) -> ApiResponse[CookieSyncResult]:
    """接收插件推送的 Cookie，等价于"用户为该站点配置 cookie 凭据"。

    流程：按域名反查站点 → 以 cookie 授权更新凭据（状态重置）→ 同步占位为
    VERIFYING 并在后台异步验证。与手动配置站点走的是同一套校验与验证链路，
    因此 Web 后台能实时看到该站点的健康状态。

    可能的错误：
    - 域名未匹配到任何受支持站点 → 404。
    - 命中的站点不支持 cookie（如 M-Team 仅 API-Key）→ 400（由 configure 抛出）。
    - 站点正在验证中（上一次推送尚未验证完）→ 409（插件可稍后重试）。
    """
    # cookie 条数（按分号粗略统计），只记数量不记内容，避免登录态泄漏进日志
    cookie_count = len([p for p in payload.cookie.split(";") if p.strip()])

    catalog = SiteCatalogService()
    site = catalog.find_by_domain(payload.domain)
    if site is None:
        logger.warning("插件推送的域名未匹配到受支持站点：域名=%s", payload.domain)
        raise NotFoundException(f"该域名未匹配到任何受支持的站点：{payload.domain}")

    logger.info(
        "收到插件 Cookie 推送：域名=%s → 站点=%s（%s），共 %d 条 Cookie，开始保存并验证",
        payload.domain,
        site.site_id,
        site.display_name,
        cookie_count,
    )

    service = SiteConfigService(session)
    # configure 内部会校验该站点是否支持 cookie（不支持则 400），并将状态重置为 PENDING
    await service.configure(
        site_id=site.site_id,
        auth_type=AuthType.COOKIE,
        cookie=payload.cookie,
    )
    row = await service.start_verification(site.site_id)
    background_tasks.add_task(verify_site, site.site_id)

    logger.info("站点 %s 已保存，已转入后台验证（VERIFYING）", site.site_id)

    return ok(
        CookieSyncResult.from_model(row, display_name=site.display_name, domain=payload.domain),
        message="已接收 Cookie，正在验证",
    )


# ===========================================================================
# 令牌管理（Web 后台用，需管理员登录——令牌本身就是密钥，绝不能匿名读写）
# ===========================================================================


@router.get(
    "/token",
    response_model=ApiResponse[SyncTokenView],
    summary="查看当前同步令牌",
    dependencies=[Depends(require_login)],
)
async def get_token() -> ApiResponse[SyncTokenView]:
    """返回当前令牌明文，供用户复制进插件；未启用时 enabled=False。"""
    setting = await get_sync_setting()
    return ok(
        SyncTokenView(
            enabled=bool(setting.token),
            token=setting.token or None,
            created_at=setting.created_at or None,
        )
    )


@router.post(
    "/token",
    response_model=ApiResponse[SyncTokenView],
    summary="生成 / 重新生成同步令牌",
    dependencies=[Depends(require_login)],
)
async def create_token() -> ApiResponse[SyncTokenView]:
    """生成新令牌；若已存在则重新生成，**旧令牌立即失效**（强制过期）。"""
    setting = await generate_sync_token()
    return ok(
        SyncTokenView(enabled=True, token=setting.token, created_at=setting.created_at),
        message="已生成新令牌，旧令牌立即失效",
    )


@router.delete(
    "/token",
    response_model=ApiResponse[SyncTokenView],
    summary="关闭同步（撤销令牌）",
    dependencies=[Depends(require_login)],
)
async def delete_token() -> ApiResponse[SyncTokenView]:
    """撤销令牌、关闭插件同步。此后插件侧接口一律 401。"""
    await revoke_sync_token()
    return ok(SyncTokenView(enabled=False), message="已关闭插件同步")
