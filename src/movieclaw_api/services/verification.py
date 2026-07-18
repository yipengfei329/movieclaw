from __future__ import annotations

import logging

import httpx

from movieclaw_api.services.auth_factory import build_auth_provider
from movieclaw_api.services.site_access import invalidate_site_access
from movieclaw_db.engine import get_database
from movieclaw_db.models.site_credential import ConfigStatus
from movieclaw_db.repositories.credential_repo import CredentialRepository
from movieclaw_db.repositories.profile_repo import ProfileRepository
from movieclaw_db.stores import SqlCookieStore
from movieclaw_tracker import create_site
from movieclaw_tracker.exceptions import (
    TrackerAuthError,
    TrackerNetworkError,
    TrackerParseError,
)

logger = logging.getLogger("movieclaw_api.verification")


def _friendly_error(exc: Exception) -> str:
    """把底层异常归类成非开发者也能看懂的中文原因。

    记录到 last_error 展示给用户，因此措辞尽量给出"可操作的下一步"。
    tracker 的业务异常本身已是中文，直接透传；网络/解析等归类为通用提示。
    """
    if isinstance(exc, TrackerAuthError):
        # 如"登录失败：用户名或密码错误"，本身已是清晰中文
        return f"认证失败：{exc.message}"
    if isinstance(exc, (TrackerNetworkError, httpx.TimeoutException, httpx.ConnectError)):
        return "无法连接站点，请检查网络、代理，或确认站点当前是否可访问"
    if isinstance(exc, TrackerParseError):
        return "站点响应格式异常：可能站点已改版，或凭据实际已失效导致返回了登录页"
    if isinstance(exc, httpx.HTTPStatusError):
        code = exc.response.status_code
        if code in (401, 403):
            return f"站点拒绝访问（状态码 {code}），凭据可能已失效，请重新验证或更新凭据"
        if code == 429:
            return f"访问过于频繁，被站点限流（状态码 {code}），系统会自动放缓节奏"
        if code >= 500:
            # 含 Cloudflare 的 520-526（源站宕机/超时等）——是站点自身故障，
            # 与凭据无关，不要误导用户去改配置
            return f"站点服务器暂时不可用（状态码 {code}），一般是站点自身故障，等待恢复即可"
        return f"站点返回异常状态码：{code}，请稍后重试或检查凭据"
    # 兜底：保留类型名 + 简短信息，便于进阶用户/日志排查
    return f"验证时发生未知错误（{type(exc).__name__}）：{exc}"


def _is_transient_error(exc: Exception) -> bool:
    """判断失败是否为「瞬时故障」——站点/网络暂时不可用，等待即可自愈。

    这是失败降级策略的分流依据（见 torrent_sync）：
    - **瞬时**（返回 True）：网络不可达、超时、5xx（含 Cloudflare 520-526）、429 限流。
      处理方式是指数退避后重试，**不作废**已认证会话——对着挂掉的站反复重登录
      既无意义，还可能触发站点的登录频控。
    - **非瞬时**（返回 False）：认证失败、其余 4xx、解析异常（可能站点改版，也可能
      凭据失效被重定向到登录页）。此时作废共享会话，下轮重建并重新认证以自愈。
    """
    if isinstance(exc, (TrackerNetworkError, httpx.TransportError)):
        # httpx.TransportError 覆盖 ConnectError / ReadError / TimeoutException 等
        return True
    if isinstance(exc, httpx.HTTPStatusError):
        code = exc.response.status_code
        return code >= 500 or code == 429
    return False


async def verify_site(site_id: str) -> None:
    """异步验证某站点配置是否真实可用，并把结论写回状态字段。

    验证判据：用配置的凭据实际认证，再拉取一次用户资料 —— 能拿到用户名，
    就证明凭据真实有效（这一判据对 cookie / 账号密码 / API-Key 都成立，
    因为 get_user_profile 会发起需要认证的真实请求）。

    前置约定：调用本函数前，站点状态**已被 start_verification 同步置为 VERIFYING**
    （并发守卫需要在请求内就抢占该状态）。因此这里不再改 VERIFYING，只负责跑验证
    并写最终结论。

    本函数被设计为"背景任务"，因此：
    - **自开独立数据库会话**：不复用已随请求关闭的会话。
    - **绝不向外抛异常**：任何失败都转成 FAILED 状态 + last_error 记录，
      否则会变成无人处理的后台异常。

    状态流转（由本函数负责的部分）：VERIFYING → ACTIVE / FAILED。
    """
    db = get_database()

    # 1. 读取待验证凭据快照（独立短会话）
    async with db.session() as session:
        repo = CredentialRepository(session)
        credential = await repo.get_by_site(site_id)
        if credential is None:
            logger.warning("待验证的站点配置不存在，跳过：site=%s", site_id)
            return
        # 取出验证需要的快照（会话关闭后仍可用，expire_on_commit=False 已保证）
        credential_snapshot = credential

    # 2. 执行真实认证 + 拉取资料
    status = ConfigStatus.FAILED
    error: str | None = None
    profile = None  # 验证成功时保留资料，稍后落库为站点资料快照
    site = None
    try:
        provider = build_auth_provider(credential_snapshot)
        site = await create_site(
            site_id,
            auth_provider=provider,
            cookie_store=SqlCookieStore(),
        )
        auth_result = await site.authenticate()
        if not auth_result.success:
            error = auth_result.message or "认证未通过"
        else:
            profile = await site.get_user_profile()
            if profile and profile.username:
                status = ConfigStatus.ACTIVE
                logger.info("站点验证成功：site=%s 用户=%s", site_id, profile.username)
            else:
                error = "认证成功但无法获取用户资料，请检查账号状态"
    except Exception as exc:  # noqa: BLE001 -- 背景任务需吞掉所有异常并记录原因
        error = _friendly_error(exc)
        # 日志里保留完整堆栈便于开发者排查，落库的 last_error 则是友好中文
        logger.warning("站点验证失败：site=%s，原因：%s", site_id, error, exc_info=True)
    finally:
        # 关闭 HTTP 连接，避免验证过程遗留未释放的连接
        if site is not None:
            try:
                await site.client.close()
            except Exception:  # noqa: BLE001
                logger.debug("关闭站点 HTTP 客户端时出错（忽略）", exc_info=True)

    # 3. 写回最终结论（又一个独立短会话）
    async with db.session() as session:
        repo = CredentialRepository(session)
        await repo.update_status(site_id, status, last_error=error)
        # 验证成功时顺手把刚拉到的用户资料落为快照（零额外站点请求）；
        # 失败不动旧快照，保留上一次成功的数据供页面继续展示
        if status == ConfigStatus.ACTIVE and profile is not None:
            await ProfileRepository(session).upsert(
                site_id=site_id,
                user_id=profile.user_id,
                username=profile.username,
                user_class=profile.user_class,
                uploaded_bytes=profile.uploaded_bytes,
                downloaded_bytes=profile.downloaded_bytes,
                ratio=profile.ratio,
                bonus=profile.bonus,
                seeding_count=profile.seeding_count,
                leeching_count=profile.leeching_count,
                avatar_url=profile.avatar_url,
                join_date=profile.join_date,
            )
    # 4. 验证结论已变（尤其转 ACTIVE）：作废共享缓存，下次访问按新状态/新会话重建
    await invalidate_site_access(site_id)
