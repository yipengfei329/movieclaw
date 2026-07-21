"""「站点取种 → 提交下载器」的公共编排，手动下载与订阅自动投递共用。

流程固定三步：
1. 通过站点访问管理器拿到已认证的站点客户端，用 download_url 取回 .torrent 字节
   （PT 站点的种子必须带登录态才能下载，不能把 URL 直接丢给下载器）；
2. 选定**默认且可用**的下载器（is_default + enabled + 连接测试通过）；
3. 提交。保存目录三级取值：调用方给的 ``save_path``（媒体库推导的入库路径）
   > 下载器配置的默认目录 > 下载器自身默认目录。
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
from movieclaw_db.models import DownloaderClient, DownloadHint, utcnow
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
    save_path: str | None = None,
    subtitle: str | None = None,
) -> tuple[SubmitResult, DownloaderClient]:
    """从站点取回种子并提交到默认下载器，返回（提交结果, 所用下载器记录）。

    tags 用于区分来源（如手动 movieclaw-manual / 订阅 movieclaw-sub），
    方便用户在下载器里筛选。save_path 由调用方按媒体库推导（缺省回落
    下载器配置的默认目录）。subtitle 是种子副标题：与库推导的 save_path
    同时在场时落一条 download_hint，供扫描器识别时取用（副标题里的中文
    片名是拼音命名种子唯一可用的查询词）——调用方只在 save_path 为
    **条目级**目录时传入，锚到库主根会波及根下所有文件。
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

    # 3. 提交（保存目录：库推导路径 > 下载器配置默认目录；幂等，重复种子不报错）
    effective_save_path = save_path or row.save_path
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
                save_path=effective_save_path,
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
        row.name,
        site_id,
        submit_result.name,
        submit_result.info_hash,
        submit_result.already_exists,
        effective_save_path or "（下载器默认）",
    )

    # 4. 落下载线索：只锚调用方给的库推导目录（下载器默认目录不在库根、
    # 扫描器看不见）。提交已成功，线索写失败只损失识别信号，不能连累提交。
    if save_path and subtitle and subtitle.strip():
        try:
            await _upsert_hint(
                session, save_path=save_path, subtitle=subtitle.strip(), site_id=site_id
            )
        except Exception:  # noqa: BLE001
            logger.warning(
                "下载线索写入失败（目录 %s），副标题识别信号将缺失", save_path, exc_info=True
            )
    return submit_result, row


async def _upsert_hint(
    session: AsyncSession, *, save_path: str, subtitle: str, site_id: str
) -> None:
    """按目录幂等落线索：同一条目重复提交（换版本重下）覆盖为最新副标题。"""
    result = await session.execute(select(DownloadHint).where(DownloadHint.save_path == save_path))
    existing = result.scalars().first()
    if existing is None:
        session.add(DownloadHint(save_path=save_path, subtitle=subtitle, site_id=site_id))
    else:
        existing.subtitle = subtitle
        existing.site_id = site_id
        existing.updated_at = utcnow()
    await session.commit()
