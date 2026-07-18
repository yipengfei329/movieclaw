"""站点访问管理器——进程级单例，持有每站"已认证的长生命周期客户端"。

为什么需要它
------------
早先每个操作（验证 / 同步 / 未来的搜索、下载）各自 ``create_site`` + ``authenticate``，
每次都新建 httpx 连接池、重复认证，既慢又不可靠。本管理器把"对某站点的访问入口"
收敛为**进程内唯一的共享对象**：按 ``site_id`` 缓存一个已认证的 ``BaseSite``，所有
需要访问该站的地方都通过 ``get(site_id)`` 复用它——认证一次、连接池复用、限流器
（本就按 site_id 全进程共享）三者对齐。

如何保证授权信息新鲜（核心不变量）
--------------------------------
``SiteCredential.updated_at`` 在**任何**会影响授权的改动上都会刷新（改凭据、验证
结论回写、启用/停用、VERIFYING 自愈——见 CredentialRepository）。因此：

- ``get()`` 每次都读一遍当前凭据，用 ``updated_at`` 作为**新鲜度戳**与缓存比对：
  戳一致才复用，戳变了就**重建并重新认证**。这让"配置一变、下次访问即用新授权"
  由构造保证，而不是依赖谁记得去失效缓存。
- 另外在写入路径显式调用 ``invalidate``（见 SiteConfigService / verify_site）：一是
  即时释放旧连接，二是双保险。
- 缓存会话还有 TTL 上限，超时强制重新认证，兜底会话在站点侧悄悄过期的情况。

管理器只在应用生命周期内存活（lifespan 初始化、关闭时统一释放连接），是与
``get_database`` / ``get_scheduler`` 一致的模块级单例。
"""

from __future__ import annotations

import asyncio
import logging
import time
from dataclasses import dataclass, field
from datetime import datetime

from movieclaw_api.services.auth_factory import build_auth_provider
from movieclaw_db.engine import get_database
from movieclaw_db.models.site_credential import ConfigStatus
from movieclaw_db.repositories.credential_repo import CredentialRepository
from movieclaw_db.stores import SqlCookieStore
from movieclaw_tracker import create_site
from movieclaw_tracker.base import BaseSite
from movieclaw_tracker.exceptions import TrackerAuthError

logger = logging.getLogger("movieclaw_api.site_access")

# 已认证会话的最长复用时长（秒）：超过即强制重建 + 重新认证，兜底会话悄悄过期。
_SESSION_TTL = 1800.0


class SiteUnavailableError(Exception):
    """站点当前不可访问（未配置 / 已停用 / 未验证通过）。消息为可读中文原因。"""


@dataclass
class _SiteEntry:
    """单个站点的缓存条目：活客户端 + 它所基于的凭据戳 + 每站锁。"""

    lock: asyncio.Lock = field(default_factory=asyncio.Lock)
    site: BaseSite | None = None
    stamp: datetime | None = None     # 构建时凭据的 updated_at（新鲜度戳）
    built_at: float = 0.0             # 构建时刻（monotonic），用于 TTL 判断


class SiteAccessManager:
    """按 site_id 持有已认证客户端的进程级管理器。"""

    def __init__(self) -> None:
        self._entries: dict[str, _SiteEntry] = {}
        self._registry_lock = asyncio.Lock()  # 保护 _entries 的创建

    # -- 内部工具 ----------------------------------------------------------

    async def _entry(self, site_id: str) -> _SiteEntry:
        """取（或懒建）某站的缓存条目。仅创建空壳，不触发认证。"""
        async with self._registry_lock:
            entry = self._entries.get(site_id)
            if entry is None:
                entry = _SiteEntry()
                self._entries[site_id] = entry
            return entry

    async def _load_credential(self, site_id: str):
        """读取当前凭据快照（新鲜度戳来源）。独立短会话。"""
        async with get_database().session() as session:
            return await CredentialRepository(session).get_by_site(site_id)

    @staticmethod
    def _assert_usable(cred, site_id: str) -> None:
        """校验站点当前是否可访问；不可访问抛 SiteUnavailableError（可读中文）。"""
        if cred is None:
            raise SiteUnavailableError(f"站点未配置：{site_id}")
        if not cred.enabled:
            raise SiteUnavailableError(f"站点已停用：{site_id}")
        if cred.status != ConfigStatus.ACTIVE:
            raise SiteUnavailableError(
                f"站点尚未验证通过（当前状态 {cred.status.value}）：{site_id}"
            )

    def _is_fresh(self, entry: _SiteEntry, stamp: datetime | None) -> bool:
        """缓存是否可直接复用：有活客户端、凭据戳一致、且未超 TTL。"""
        return (
            entry.site is not None
            and entry.stamp == stamp
            and (time.monotonic() - entry.built_at) < _SESSION_TTL
        )

    async def _build(self, cred) -> BaseSite:
        """构建并认证一个站点客户端。认证失败则关闭连接并抛错。"""
        provider = build_auth_provider(cred)
        site = await create_site(
            cred.site_id, auth_provider=provider, cookie_store=SqlCookieStore()
        )
        result = await site.authenticate()
        if not result.success:
            await _safe_close(site)
            raise TrackerAuthError(result.message or "认证未通过，请重新验证站点凭据")
        return site

    # -- 对外接口 ----------------------------------------------------------

    async def get(self, site_id: str) -> BaseSite:
        """取某站已认证的共享客户端；缓存不新鲜则重建 + 重新认证。

        调用方**不得** close 返回的客户端——它是全进程共享的长生命周期对象。
        """
        cred = await self._load_credential(site_id)
        self._assert_usable(cred, site_id)
        entry = await self._entry(site_id)

        # 快路径：无需加锁即可复用
        if self._is_fresh(entry, cred.updated_at):
            return entry.site  # type: ignore[return-value]

        # 慢路径：每站锁内重建，双重检查避免并发重复认证
        async with entry.lock:
            cred = await self._load_credential(site_id)  # 等锁期间可能又变了，重读
            self._assert_usable(cred, site_id)
            if self._is_fresh(entry, cred.updated_at):
                return entry.site  # type: ignore[return-value]
            await self._close_site(entry)
            logger.info("为站点 %s 构建并认证共享客户端", site_id)
            entry.site = await self._build(cred)
            entry.stamp = cred.updated_at
            entry.built_at = time.monotonic()
            return entry.site

    async def invalidate(self, site_id: str) -> None:
        """作废某站缓存（关闭连接并清空），下次 get 会用最新凭据重建。

        在改动凭据 / 启用停用 / 删除 / 验证结论回写后调用，即时释放旧会话。
        """
        entry = self._entries.get(site_id)
        if entry is None:
            return
        async with entry.lock:
            await self._close_site(entry)
        logger.debug("已作废站点 %s 的共享客户端缓存", site_id)

    async def aclose(self) -> None:
        """关闭所有站点客户端连接。应用关闭时调用。"""
        for entry in list(self._entries.values()):
            async with entry.lock:
                await self._close_site(entry)
        self._entries.clear()

    async def _close_site(self, entry: _SiteEntry) -> None:
        """关闭并清空条目里的活客户端（须在持有 entry.lock 时调用）。"""
        if entry.site is not None:
            await _safe_close(entry.site)
            entry.site = None
            entry.stamp = None
            entry.built_at = 0.0


async def _safe_close(site: BaseSite) -> None:
    """关闭站点 HTTP 客户端，吞掉关闭时的异常。"""
    try:
        await site.client.close()
    except Exception:  # noqa: BLE001
        logger.debug("关闭站点 HTTP 客户端出错（忽略）", exc_info=True)


# ---------------------------------------------------------------------------
# 模块级单例（与 get_database / get_scheduler 风格一致）
# ---------------------------------------------------------------------------
_manager: SiteAccessManager | None = None


def init_site_access() -> SiteAccessManager:
    """初始化全局站点访问管理器单例。应在应用启动（lifespan）时调用一次。"""
    global _manager
    if _manager is not None:
        logger.warning("站点访问管理器已初始化，重复调用被忽略")
        return _manager
    _manager = SiteAccessManager()
    return _manager


def get_site_access() -> SiteAccessManager:
    """获取全局站点访问管理器；未初始化时抛错，提示检查启动流程。"""
    if _manager is None:
        raise RuntimeError("站点访问管理器尚未初始化，请确认应用启动时已调用 init_site_access()")
    return _manager


async def invalidate_site_access(site_id: str) -> None:
    """通知"某站授权信息已变更"，作废其共享缓存。

    对未初始化的场景（如单元测试、关闭调度器运行）安全 no-op——因此可在 Service 层
    的写入路径无条件调用，不必关心管理器是否已启动。
    """
    if _manager is not None:
        await _manager.invalidate(site_id)
