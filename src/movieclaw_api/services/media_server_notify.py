"""入库后通知媒体服务器刷新（媒体库 L4，可选）。

配置 MEDIA_SERVER_URL / MEDIA_SERVER_TYPE / MEDIA_SERVER_TOKEN 后，每次整理
入库成功触发一次库刷新，新内容即刻出现在 Emby/Jellyfin 里（两者同源，
刷新接口一致：POST /Library/Refresh，X-Emby-Token 鉴权）。

失败语义：通知是锦上添花，任何失败只记中文告警，绝不影响入库结果。
"""

from __future__ import annotations

import logging

import httpx

from movieclaw_api.core.config import get_settings
from movieclaw_net import EgressScope, egress_transport

logger = logging.getLogger("movieclaw_api.media_server_notify")

_SUPPORTED = {"emby", "jellyfin"}


async def notify_media_server_refresh() -> bool:
    """触发媒体服务器库刷新；未配置返回 False，通知成功返回 True。"""
    settings = get_settings()
    url = settings.media_server_url.strip().rstrip("/")
    if not url:
        return False
    server_type = settings.media_server_type.strip().lower()
    if server_type not in _SUPPORTED:
        logger.warning(
            "不支持的媒体服务器类型：%s（支持 emby / jellyfin），已跳过通知",
            settings.media_server_type,
        )
        return False
    try:
        # LAN 范围：媒体服务器在内网，任何代理配置下都必须直连
        transport = egress_transport("media_server", scope=EgressScope.LAN)
        async with httpx.AsyncClient(timeout=10.0, transport=transport) as client:
            response = await client.post(
                f"{url}/Library/Refresh",
                headers={"X-Emby-Token": settings.media_server_token},
            )
        response.raise_for_status()
    except Exception as exc:  # noqa: BLE001 -- 通知失败绝不影响入库
        logger.warning("通知媒体服务器刷新失败（不影响入库）：%s（%s）", url, exc)
        return False
    logger.info("已通知 %s 刷新媒体库：%s", server_type, url)
    return True
