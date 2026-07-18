"""「站点取种 → 提交下载器」的公共编排，手动下载与订阅自动投递共用。

流程固定三步：
1. 通过站点访问管理器拿到已认证的站点客户端，用 download_url 取回 .torrent 字节
   （PT 站点的种子必须带登录态才能下载，不能把 URL 直接丢给下载器）；
2. 选定**默认且可用**的下载器（is_default + enabled + 连接测试通过）；
3. 按下载器配置的默认保存目录提交（save_path 为空则用下载器自身默认目录），
   提交幂等：种子已存在时不报错，结果里以 already_exists 标记。

错误统一抛 AppException 子类，消息为可读中文——API 路由直接透传给前端展示，
订阅投递路径捕获后进活动台账，两边都不需要再翻译。
"""

from __future__ import annotations

import logging

from sqlalchemy.ext.asyncio import AsyncSession
from sqlmodel import select

from movieclaw_api.exceptions import BadRequestException, UpstreamServiceException
from movieclaw_api.services.site_access import SiteUnavailableError, get_site_access
from movieclaw_db.models import DownloaderClient
from movieclaw_db.models.site_credential import ConfigStatus
from movieclaw_db.repositories.downloader_repo import DownloaderRepository
from movieclaw_downloader.factory import create_downloader
from movieclaw_downloader.models import DownloaderConfig, DownloadRequest, SubmitResult

logger = logging.getLogger("movieclaw_api.torrent_submit")


async def submit_torrent(
    session: AsyncSession,
    *,
    site_id: str,
    download_url: str | None,
    tags: list[str],
) -> tuple[SubmitResult, DownloaderClient]:
    """从站点取回种子并提交到默认下载器，返回（提交结果, 所用下载器记录）。

    tags 用于区分来源（如手动 movieclaw-manual / 订阅 movieclaw-sub），
    方便用户在下载器里筛选。
    """
    if not download_url:
        raise BadRequestException("该种子没有可用的下载入口（download_url 缺失）")

    # 1. 站点取种：站点不可用（未配置/停用/未验证）是配置问题，站点请求失败是上游问题
    try:
        site = await get_site_access().get(site_id)
    except SiteUnavailableError as exc:
        raise BadRequestException(str(exc)) from exc
    try:
        torrent_bytes = await site.download_torrent(download_url)
    except Exception as exc:
        raise UpstreamServiceException(f"从站点 {site_id} 取回种子失败：{exc}") from exc

    # 2. 选默认可用下载器
    result = await session.execute(
        select(DownloaderClient).where(
            DownloaderClient.is_default.is_(True),  # type: ignore[attr-defined]
            DownloaderClient.enabled.is_(True),  # type: ignore[attr-defined]
            DownloaderClient.status == ConfigStatus.ACTIVE,
        )
    )
    row = result.scalars().first()
    if row is None:
        raise BadRequestException("没有可用的默认下载器（请在「设置 → 下载器」里添加并设为默认）")

    # 3. 提交（保存目录用下载器配置的默认目录；幂等，重复种子不报错）
    repo = DownloaderRepository(session)
    config = DownloaderConfig(
        type=row.client_type.value,
        url=row.url,
        username=row.username,
        password=repo.decrypted_password(row),
    )
    downloader = create_downloader(config)
    try:
        submit_result = await downloader.submit(
            DownloadRequest(
                torrent_bytes=torrent_bytes,
                save_path=row.save_path,
                category="movieclaw",
                tags=tags,
            )
        )
    except Exception as exc:
        raise UpstreamServiceException(f"提交到下载器「{row.name}」失败：{exc}") from exc
    finally:
        await downloader.close()

    logger.info(
        "种子已提交到下载器「%s」：site=%s name=%s hash=%s 已存在=%s 目录=%s",
        row.name, site_id, submit_result.name, submit_result.info_hash,
        submit_result.already_exists, row.save_path or "（下载器默认）",
    )
    return submit_result, row
