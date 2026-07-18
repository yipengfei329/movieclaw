from __future__ import annotations

import logging

from movieclaw_cache import StoredEntry
from movieclaw_db.engine import Database, get_database
from movieclaw_db.repositories.cache_repo import CacheRepository
from movieclaw_db.repositories.cookie_repo import CookieRepository

logger = logging.getLogger("movieclaw_db.stores")


class SqlCookieStore:
    """基于 SQLite 的 Cookie 持久化存储。

    它在结构上满足 ``movieclaw_tracker`` 的 ``CookieStore`` 协议
    （load / save / delete 三个 async 方法），因此可以直接传给 ``create_site``：

        store = SqlCookieStore()
        site = await create_site("mteam", auth_provider=..., cookie_store=store)

    设计要点
    --------
    - **无需 import tracker**：CookieStore 是结构化 Protocol（鸭子类型），
      本类方法签名一致即可满足，从而让 movieclaw_db 不反向依赖 tracker，保持分层干净。
    - **每次操作独立会话**：cookie 存储对象生命周期长（贯穿一个 site 实例），
      不适合持有请求级会话；因此每次读写都从全局 Database 现开一个短会话，用完即关，
      天然规避跨请求/跨协程共享会话的并发问题。
    """

    def __init__(self, database: Database | None = None) -> None:
        # 允许注入 Database（便于测试）；默认使用全局单例
        self._database = database

    def _db(self) -> Database:
        return self._database or get_database()

    async def load(self, site_id: str) -> dict[str, str] | None:
        async with self._db().session() as session:
            repo = CookieRepository(session)
            cookies = await repo.get(site_id)
            if cookies:
                logger.debug("从数据库加载 cookie：site=%s", site_id)
            return cookies

    async def save(self, site_id: str, cookies: dict[str, str]) -> None:
        async with self._db().session() as session:
            repo = CookieRepository(session)
            await repo.upsert(site_id, cookies)
            logger.debug("已保存 cookie 到数据库：site=%s", site_id)

    async def delete(self, site_id: str) -> None:
        async with self._db().session() as session:
            repo = CookieRepository(session)
            await repo.delete(site_id)
            logger.debug("已删除数据库中的 cookie：site=%s", site_id)


class SqlCacheStore:
    """基于 SQLite 的通用持久缓存存储（``cache_entry`` 表）。

    满足 ``movieclaw_cache`` 的 ``CacheStore`` 协议（get / set 两个方法），
    由装配层注入给各缓存使用方（首个租户是豆瓣客户端）。与 ``SqlCookieStore``
    同理：存储对象生命周期长，每次读写现开短会话，用完即关。

    依赖方向说明：movieclaw_cache 是不依赖任何业务包的底层模块，本包引用它
    的 ``StoredEntry`` 返回类型不构成循环依赖。
    """

    def __init__(self, database: Database | None = None) -> None:
        # 允许注入 Database（便于测试）；默认使用全局单例
        self._database = database

    def _db(self) -> Database:
        return self._database or get_database()

    async def get(self, namespace: str, key: str) -> StoredEntry | None:
        async with self._db().session() as session:
            row = await CacheRepository(session).get(namespace, key)
            if row is None:
                return None
            return StoredEntry(payload=row.payload, fetched_at=row.fetched_at)

    async def set(self, namespace: str, key: str, payload: str) -> None:
        async with self._db().session() as session:
            await CacheRepository(session).upsert(namespace, key, payload)
