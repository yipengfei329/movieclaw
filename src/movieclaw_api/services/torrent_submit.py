"""「站点取种 → 提交下载器」的公共编排，手动下载与订阅自动投递共用。

流程固定四步：
1. 选定**默认且可用**的下载器（is_default + enabled + 连接测试通过）；
2. 确定保存目录并守门。目录三级取值：调用方给的 ``save_path``（媒体库推导的
   入库路径）> 下载器配置的默认目录 > 下载器自身默认目录。界面上配置的路径
   一律是 **movieclaw 视角**；下载器与 movieclaw 不在同一容器/主机时两边看到
   的路径不同，提交前按下载器配置的路径映射（``path_mappings``，最长前缀优先）
   翻译成下载器视角。**守门**：配了映射但目录不被任何映射覆盖 → 拒绝提交
   （投出去会落进下载器容器内的"黑洞"路径，movieclaw 永远看不到完成的文件）；
3. 通过站点访问管理器拿到已认证的站点客户端，用 download_url 取回 .torrent 字节
   （PT 站点的种子必须带登录态才能下载，不能把 URL 直接丢给下载器）；
4. 提交。幂等：种子已存在时不报错，结果里以 already_exists 标记。

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


def _best_match(
    path: str, mappings: list[dict[str, str]], *, source_key: str, target_key: str
) -> tuple[str, str] | None:
    """在映射表里找 path 的最长前缀命中，返回（命中前缀, 对端前缀）。

    前缀必须落在路径分隔符边界上（``/data/downloads`` 不会误配
    ``/data/downloads2``）；无命中返回 None。source_key/target_key
    决定翻译方向（local→remote 或 remote→local）。
    """
    best: tuple[str, str] | None = None
    for mapping in mappings:
        source = (mapping.get(source_key) or "").rstrip("/")
        target = (mapping.get(target_key) or "").rstrip("/")
        if not source or not target:
            continue
        if (path == source or path.startswith(source + "/")) and (
            best is None or len(source) > len(best[0])
        ):
            best = (source, target)
    return best


def translate_save_path(
    path: str | None, mappings: list[dict[str, str]] | None
) -> str | None:
    """把 movieclaw 视角的保存目录翻译成下载器视角（最长前缀匹配）。

    映射形如 ``[{"local": "/data/downloads", "remote": "/downloads"}]``。
    未命中任何映射时原样返回——视角一致的部署（映射为空）零影响；
    配了映射却未覆盖的路径由 ``mapping_covers`` 在提交前拦截。
    """
    if not path or not mappings:
        return path
    best = _best_match(path, mappings, source_key="local", target_key="remote")
    if best is None:
        return path
    local, remote = best
    return remote + path[len(local) :]


def translate_to_local(
    path: str | None, mappings: list[dict[str, str]] | None
) -> str | None:
    """反向翻译：把下载器上报的路径翻译回 movieclaw 视角（最长前缀匹配）。

    救援巡检核验落点用。未命中原样返回（视角一致部署）。
    """
    if not path or not mappings:
        return path
    best = _best_match(path, mappings, source_key="remote", target_key="local")
    if best is None:
        return path
    remote, local = best
    return local + path[len(remote) :]


def mapping_covers(path: str, mappings: list[dict[str, str]]) -> bool:
    """movieclaw 视角的路径是否被某条映射的 local 前缀覆盖。"""
    return _best_match(path, mappings, source_key="local", target_key="remote") is not None


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

    # 1. 选默认可用下载器（先于取种：注定投不出去时不白打站点请求）
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

    # 2. 保存目录（库推导路径 > 下载器配置默认目录，均为 movieclaw 视角）+
    # 守门：下载器配了路径映射（跨容器部署的声明），保存目录却不在任何映射
    # 覆盖范围内 → 下载器大概率无法访问，投出去会落进容器黑洞（下载器在
    # 自己文件系统里凭空创建该路径），movieclaw 永远看不到完成的文件。
    # 拒绝并给出可操作的中文指引，比静默挂起好得多
    effective_save_path = save_path or row.save_path
    if (
        effective_save_path
        and row.path_mappings
        and not mapping_covers(effective_save_path, row.path_mappings)
    ):
        raise BadRequestException(
            f"保存目录 {effective_save_path} 不在下载器「{row.name}」的路径映射覆盖范围内，"
            "下载器可能无法访问该目录——请在「设置 → 下载器」为它补一条路径映射"
            "（下载器实际可直达同名路径时，添加一条两边相同的映射即可），"
            "或改用监听导入规则"
        )
    submit_save_path = translate_save_path(effective_save_path, row.path_mappings)

    # 3. 站点取种：站点不可用（未配置/停用/未验证）是配置问题，站点请求失败是上游问题
    try:
        site = await get_site_access().get(site_id)
    except SiteUnavailableError as exc:
        raise BadRequestException(str(exc)) from exc
    try:
        torrent_bytes = await site.download_torrent(download_url)
    except Exception as exc:
        raise UpstreamServiceException(f"从站点 {site_id} 取回种子失败：{exc}") from exc

    # 4. 提交（幂等，重复种子不报错）
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
                save_path=submit_save_path,
                category="movieclaw",
                tags=tags,
            )
        )
    except Exception as exc:
        raise UpstreamServiceException(f"提交到下载器「{row.name}」失败：{exc}") from exc
    finally:
        await downloader.close()

    # 目录日志：有映射翻译时同时打两个视角，方便核对跨容器部署是否配对
    if submit_save_path != effective_save_path:
        dir_text = f"{effective_save_path} →（映射）{submit_save_path}"
    else:
        dir_text = effective_save_path or "（下载器默认）"
    logger.info(
        "种子已提交到下载器「%s」：site=%s name=%s hash=%s 已存在=%s 目录=%s",
        row.name,
        site_id,
        submit_result.name,
        submit_result.info_hash,
        submit_result.already_exists,
        dir_text,
    )

    # 5. 落下载线索：只锚调用方给的库推导目录（下载器默认目录不在库根、
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
