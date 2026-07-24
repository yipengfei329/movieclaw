"""网络与代理设置接口（「设置 → 网络」页的后端）。

三个端点：
- GET  /network/config —— 当前配置 + 服务开关目录（内置服务 + 已配置的 PT 站）
  + 镜像地址的生效默认值（前端做 placeholder 展示）；
- PUT  /network/config —— 保存并**立即生效**（代理路由热切换，无需重启）；
  镜像地址变更时重建媒体服务单例；
- POST /network/test   —— 按服务标签发一次最小探测请求，返回延迟或中文错误。
  测试绕过熔断器（就是要真发请求），测通后顺手闭合该服务的熔断，
  让业务请求立刻恢复，不用干等冷却期。
"""

from __future__ import annotations

import logging
import time
from typing import Literal
from urllib.parse import urlsplit

import httpx
from fastapi import APIRouter, Depends
from pydantic import BaseModel, Field
from sqlalchemy.ext.asyncio import AsyncSession

from movieclaw_api.core.config import get_settings
from movieclaw_api.exceptions import BadRequestException
from movieclaw_api.schemas.response import ApiResponse, ok
from movieclaw_api.services.media_discover import close_media_service
from movieclaw_api.services.network_egress import (
    current_network_setting,
    effective_douban_api_base_url,
    effective_tmdb_api_base_url,
    effective_tmdb_image_base_url,
    save_network_egress,
)
from movieclaw_api.settings import BUILTIN_EGRESS_SERVICES, NetworkEgressSetting
from movieclaw_db.engine import get_session
from movieclaw_db.repositories.credential_repo import CredentialRepository
from movieclaw_db.repositories.llm_provider_repo import LlmProviderRepository
from movieclaw_net import (
    PROXY_SCHEMES,
    egress_transport,
    env_proxy_url,
    get_breaker,
    reset_all_breakers,
)
from movieclaw_tracker import get_site_config
from movieclaw_tracker.exceptions import SiteNotFoundError

logger = logging.getLogger("movieclaw_api.network")

router = APIRouter(prefix="/network", tags=["network"])


# ---------------------------------------------------------------------------
# 模型
# ---------------------------------------------------------------------------


class EgressServiceOption(BaseModel):
    """设置页「走代理」开关列表里的一项。"""

    id: str
    label: str
    description: str = ""


class NetworkConfigPayload(BaseModel):
    """保存请求体：与配置域字段一一对应。"""

    proxy_mode: Literal["off", "env", "manual"] = "env"
    proxy_url: str = ""
    proxy_services: list[str] = Field(default_factory=list)
    tmdb_api_base_url: str = ""
    tmdb_image_base_url: str = ""
    douban_api_base_url: str = ""


class NetworkConfigView(NetworkConfigPayload):
    """读取响应：配置本体 + 前端渲染所需的目录与默认值。"""

    services: list[EgressServiceOption] = Field(default_factory=list)
    mirror_defaults: dict[str, str] = Field(
        default_factory=dict, description="三个镜像地址的生效默认值（设置为空时的回落）"
    )
    env_proxy_detected: str = Field(
        default="", description="环境变量中探测到的代理地址；env 模式下供用户确认"
    )


class NetworkTestPayload(BaseModel):
    service: str = Field(min_length=1, max_length=100)


class NetworkTestResult(BaseModel):
    ok: bool
    latency_ms: int | None = None
    message: str


# ---------------------------------------------------------------------------
# 配置读写
# ---------------------------------------------------------------------------


async def _service_catalog(session: AsyncSession) -> list[EgressServiceOption]:
    """开关目录 = 内置服务 + 已配置的 PT 站（site:<id>）。"""
    options = [EgressServiceOption(**item) for item in BUILTIN_EGRESS_SERVICES]
    for cred in await CredentialRepository(session).list_all():
        try:
            display_name = get_site_config(cred.site_id).display_name
        except SiteNotFoundError:
            display_name = cred.site_id  # 站点定义被移除时仍展示，保留用户配置
        options.append(
            EgressServiceOption(
                id=f"site:{cred.site_id}",
                label=display_name,
                description="PT 站点（国内直连通常更快，按需开启）",
            )
        )
    return options


async def _build_view(session: AsyncSession) -> NetworkConfigView:
    setting = current_network_setting()
    env_settings = get_settings()
    return NetworkConfigView(
        proxy_mode=setting.proxy_mode,
        proxy_url=setting.proxy_url,
        proxy_services=setting.proxy_services,
        tmdb_api_base_url=setting.tmdb_api_base_url,
        tmdb_image_base_url=setting.tmdb_image_base_url,
        douban_api_base_url=setting.douban_api_base_url,
        services=await _service_catalog(session),
        mirror_defaults={
            "tmdb_api_base_url": env_settings.tmdb_api_base_url,
            "tmdb_image_base_url": env_settings.tmdb_image_base_url,
            "douban_api_base_url": env_settings.douban_api_base_url,
        },
        env_proxy_detected=env_proxy_url() or "",
    )


@router.get(
    "/config",
    response_model=ApiResponse[NetworkConfigView],
    summary="读取网络与代理配置",
)
async def get_network_config(
    session: AsyncSession = Depends(get_session),
) -> ApiResponse[NetworkConfigView]:
    return ok(await _build_view(session))


def _validate_payload(payload: NetworkConfigPayload) -> None:
    """保存前校验：代理地址协议、镜像地址格式。错误信息中文直达前端。"""
    proxy_url = payload.proxy_url.strip()
    if payload.proxy_mode == "manual":
        if not proxy_url:
            raise BadRequestException("手动代理模式必须填写代理地址")
        scheme = urlsplit(proxy_url).scheme.lower()
        if scheme not in PROXY_SCHEMES:
            raise BadRequestException(
                f"代理地址协议不支持：{scheme or '（缺失）'}，支持 {'/'.join(PROXY_SCHEMES)}"
            )
    for name, value in (
        ("TMDB 接口镜像", payload.tmdb_api_base_url),
        ("TMDB 图床镜像", payload.tmdb_image_base_url),
        ("豆瓣接口地址", payload.douban_api_base_url),
    ):
        value = value.strip()
        if value and urlsplit(value).scheme not in ("http", "https"):
            raise BadRequestException(f"{name}必须是 http(s) 地址")


@router.put(
    "/config",
    response_model=ApiResponse[NetworkConfigView],
    summary="保存网络与代理配置（立即生效，无需重启）",
)
async def save_network_config(
    payload: NetworkConfigPayload,
    session: AsyncSession = Depends(get_session),
) -> ApiResponse[NetworkConfigView]:
    _validate_payload(payload)
    mirrors_before = (
        effective_tmdb_api_base_url(),
        effective_tmdb_image_base_url(),
        effective_douban_api_base_url(),
    )
    await save_network_egress(
        NetworkEgressSetting(
            proxy_mode=payload.proxy_mode,
            proxy_url=payload.proxy_url.strip(),
            proxy_services=sorted(set(payload.proxy_services)),
            tmdb_api_base_url=payload.tmdb_api_base_url.strip(),
            tmdb_image_base_url=payload.tmdb_image_base_url.strip(),
            douban_api_base_url=payload.douban_api_base_url.strip(),
        )
    )
    # 代理路由靠 transport 的 epoch 热切换，不需要重建；但镜像地址绑死在
    # 客户端构造期，变了就重建媒体服务单例（下次请求按新地址懒加载）
    mirrors_after = (
        effective_tmdb_api_base_url(),
        effective_tmdb_image_base_url(),
        effective_douban_api_base_url(),
    )
    if mirrors_before != mirrors_after:
        await close_media_service()
        logger.info("镜像地址已变更，媒体服务将按新地址重建")
    # 网络配置变了，之前的失败统计不再有参考意义——闭合全部熔断重新试
    reset_all_breakers()
    return ok(await _build_view(session))


# ---------------------------------------------------------------------------
# 连通性测试
# ---------------------------------------------------------------------------


async def _probe_target(service: str, session: AsyncSession) -> tuple[str, dict[str, str]]:
    """返回某服务的探测 URL 与请求头；未配置/未知服务抛 BadRequest。"""
    if service == "tmdb":
        settings = get_settings()
        url = f"{effective_tmdb_api_base_url().rstrip('/')}/configuration"
        headers: dict[str, str] = {}
        key = settings.tmdb_api_key or ""
        if key.startswith("eyJ"):
            headers["Authorization"] = f"Bearer {key}"
        elif key:
            url += f"?api_key={key}"
        return url, headers
    if service == "douban":
        base = effective_douban_api_base_url().rstrip("/")
        return f"{base}/subject_collection/movie_hot_gaia/items?start=0&count=1", {
            "User-Agent": "Mozilla/5.0 (iPhone; CPU iPhone OS 17_0 like Mac OS X)",
            "Referer": "https://m.douban.com/movie/",
        }
    if service == "image":
        return effective_tmdb_image_base_url(), {}
    if service == "llm":
        row = await LlmProviderRepository(session).get()
        if row is None:
            raise BadRequestException("尚未配置 AI 模型供应商，无法测试")
        base = row.base_url
        if not base:
            from movieclaw_llm.providers.registry import get_preset

            base = get_preset(row.provider_type).base_url or ""
        if not base:
            raise BadRequestException("该供应商未配置 API 端点地址")
        api_key = LlmProviderRepository.decrypted_api_key(row)
        return f"{base.rstrip('/')}/models", {"Authorization": f"Bearer {api_key}"}
    if service.startswith("site:"):
        site_id = service.removeprefix("site:")
        try:
            return get_site_config(site_id).base_url, {}
        except SiteNotFoundError as exc:
            raise BadRequestException(f"未知站点：{site_id}") from exc
    raise BadRequestException(f"未知的服务标签：{service}")


def _classify_probe(service: str, status_code: int) -> NetworkTestResult:
    """探测拿到了 HTTP 响应 = 线路通；再按服务语义细化提示。"""
    if service == "tmdb":
        if status_code == 200:
            message = "网络连通，API Key 有效"
        elif status_code == 401:
            message = "网络连通，但 TMDB API Key 无效或未配置"
        else:
            message = f"网络连通（HTTP {status_code}）"
        return NetworkTestResult(ok=True, message=message)
    if service == "llm":
        if status_code == 200:
            message = "网络连通，API Key 有效"
        elif status_code in (401, 403):
            message = "网络连通，但 API Key 无效"
        else:
            message = f"网络连通（HTTP {status_code}）"
        return NetworkTestResult(ok=True, message=message)
    return NetworkTestResult(ok=True, message=f"网络连通（HTTP {status_code}）")


@router.post(
    "/test",
    response_model=ApiResponse[NetworkTestResult],
    summary="按服务做一次连通性测试（走当前保存的出口配置）",
)
async def test_network_service(
    payload: NetworkTestPayload,
    session: AsyncSession = Depends(get_session),
) -> ApiResponse[NetworkTestResult]:
    service = payload.service.strip()
    url, headers = await _probe_target(service, session)
    started = time.perf_counter()
    try:
        # 绕过熔断（测试就是要真发请求），跟随重定向（站点首页常见 301）
        async with httpx.AsyncClient(
            transport=egress_transport(service, use_breaker=False),
            timeout=10.0,
            follow_redirects=True,
        ) as client:
            response = await client.get(url, headers=headers)
    except httpx.TimeoutException:
        return ok(
            NetworkTestResult(ok=False, message="连接超时（10 秒无响应），当前出口无法访问该服务")
        )
    except httpx.HTTPError as exc:
        logger.info("连通性测试失败：service=%s（%s）", service, exc)
        return ok(
            NetworkTestResult(
                ok=False, message=f"连接失败：{type(exc).__name__}（{exc}）"
            )
        )
    latency_ms = int((time.perf_counter() - started) * 1000)
    # 测通了就闭合该服务的熔断：业务请求立刻恢复，不用等冷却期
    get_breaker(service).reset()
    result = _classify_probe(service, response.status_code)
    result.latency_ms = latency_ms
    return ok(result)
