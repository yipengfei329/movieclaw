from __future__ import annotations

import json
import logging
from typing import TypeVar

from movieclaw_api.settings.base import (
    SettingDescriptor,
    SettingSchema,
    get_descriptor_by_model,
    list_descriptors,
)
from movieclaw_db.crypto import SecretBox, get_secret_box
from movieclaw_db.engine import Database, get_database
from movieclaw_db.repositories.setting_repo import SettingRepository

logger = logging.getLogger("movieclaw_api.settings.store")

T = TypeVar("T", bound=SettingSchema)


class SettingStore:
    """配置读写的统一内核：串起「注册表 + 校验 + 加密 + 缓存 + 持久化」。

    它是上层业务（引导向导、集成模块、API）操作配置的唯一入口。职责链条：

        读：DB 里的 JSON → 解密敏感字段 → Pydantic 校验 → 返回强类型模型
        写：强类型模型 → 加密敏感字段 → JSON → 落库 → 刷新缓存

    关键设计
    --------
    - **缺记录返默认，而非报错**：某配置域从未配置时，返回该模型的零参数默认
      实例。这是"空库也能启动、引导页得以运行"的架构红线在代码里的落点。
    - **读多写少，带内存缓存**：配置在运行期被频繁读取（每次搜索都要看下载器/
      站点设置），故读取结果按 namespace 缓存，写入时失效对应缓存。
    - **短会话即用即弃**：本对象生命周期长（贯穿整个应用），不持有请求级会话；
      每次读写从全局 Database 现开一个短会话，规避跨协程共享会话的并发问题
      （与 ``SqlCookieStore`` 同款策略）。
    - **敏感字段透明加解密**：调用方始终只跟明文模型打交道，加密对其不可见。
    """

    def __init__(
        self,
        database: Database | None = None,
        secret_box: SecretBox | None = None,
    ) -> None:
        # 允许注入（便于测试）；默认使用全局单例
        self._database = database
        self._secret_box = secret_box
        # namespace -> 已解密并校验过的模型实例
        self._cache: dict[str, SettingSchema] = {}

    def _db(self) -> Database:
        return self._database or get_database()

    def _crypto(self) -> SecretBox:
        return self._secret_box or get_secret_box()

    # ------------------------------------------------------------------
    # 读取
    # ------------------------------------------------------------------
    async def get(self, model_cls: type[T]) -> T:
        """读取某配置域的有效配置，返回强类型模型实例。

        从未配置过则返回模型默认值（零参数构造）。结果会被缓存。
        """
        descriptor = get_descriptor_by_model(model_cls)
        cached = self._cache.get(descriptor.namespace)
        if cached is not None:
            return cached  # type: ignore[return-value]

        instance = await self._load(descriptor)
        self._cache[descriptor.namespace] = instance
        return instance  # type: ignore[return-value]

    async def _load(self, descriptor: SettingDescriptor) -> SettingSchema:
        """从数据库加载并解密、校验成模型实例；无记录时返回默认实例。"""
        async with self._db().session() as session:
            row = await SettingRepository(session).get(descriptor.namespace)

        if row is None:
            # 缺记录 → 返回默认配置（不报错），保证空库可用
            return descriptor.model()

        raw: dict = json.loads(row.value_json)
        decrypted = self._decrypt_fields(raw, descriptor)
        # 交给 Pydantic 校验并补齐默认值；结构不合法会在此抛出，便于尽早发现脏数据
        return descriptor.model.model_validate(decrypted)

    # ------------------------------------------------------------------
    # 写入
    # ------------------------------------------------------------------
    async def set(self, instance: SettingSchema) -> None:
        """整体覆盖式保存一个配置域。敏感字段落库前自动加密。"""
        descriptor = get_descriptor_by_model(type(instance))
        # mode="json"：把枚举、datetime 等转成 JSON 原生类型，便于序列化
        data = instance.model_dump(mode="json")
        encrypted = self._encrypt_fields(data, descriptor)
        value_json = json.dumps(encrypted, ensure_ascii=False)

        async with self._db().session() as session:
            await SettingRepository(session).upsert(descriptor.namespace, value_json)

        # 缓存存入"明文模型"，供后续读取直接命中（与刚写入的值一致）
        self._cache[descriptor.namespace] = instance
        logger.info("已保存配置域：%s", descriptor.namespace)

    async def delete(self, model_cls: type[SettingSchema]) -> bool:
        """删除某配置域，返回是否命中记录。"""
        descriptor = get_descriptor_by_model(model_cls)
        async with self._db().session() as session:
            hit = await SettingRepository(session).delete(descriptor.namespace)
        self._cache.pop(descriptor.namespace, None)
        if hit:
            logger.info("已删除配置域：%s", descriptor.namespace)
        return hit

    def invalidate(self, namespace: str | None = None) -> None:
        """失效缓存。传 namespace 只失效单个域，不传则清空全部。

        供"绕过本 Store 直接改动 DB"或多进程等场景手动同步缓存（当前单进程
        部署一般用不到，留作扩展）。
        """
        if namespace is None:
            self._cache.clear()
        else:
            self._cache.pop(namespace, None)

    # ------------------------------------------------------------------
    # 导出 / 导入（备份友好：整块 volume 已在盘上，导出便于迁移与排障）
    # ------------------------------------------------------------------
    async def export_all(self, *, reveal_secrets: bool = False) -> dict[str, dict]:
        """导出所有已注册配置域的当前值，返回 ``{namespace: 配置字典}``。

        默认对敏感字段打码（``reveal_secrets=False``），供展示/日志安全使用；
        仅在做完整备份且明确知情时才传 ``reveal_secrets=True`` 导出明文。
        """
        result: dict[str, dict] = {}
        for descriptor in list_descriptors():
            instance = await self.get(descriptor.model)
            data = instance.model_dump(mode="json")
            if not reveal_secrets:
                for name in descriptor.secret_fields:
                    if data.get(name):
                        data[name] = "***"
            result[descriptor.namespace] = data
        return result

    # ------------------------------------------------------------------
    # 敏感字段加解密
    # ------------------------------------------------------------------
    def _encrypt_fields(self, data: dict, descriptor: SettingDescriptor) -> dict:
        """就地加密声明为敏感的顶层字段（仅对非空字符串加密）。"""
        if not descriptor.secret_fields:
            return data
        box = self._crypto()
        for name in descriptor.secret_fields:
            value = data.get(name)
            if isinstance(value, str) and value and not SecretBox.is_encrypted(value):
                data[name] = box.encrypt(value)
        return data

    def _decrypt_fields(self, data: dict, descriptor: SettingDescriptor) -> dict:
        """就地解密声明为敏感的顶层字段。"""
        if not descriptor.secret_fields:
            return data
        box = self._crypto()
        for name in descriptor.secret_fields:
            value = data.get(name)
            if isinstance(value, str) and value:
                data[name] = box.decrypt(value)
        return data


# ---------------------------------------------------------------------------
# 模块级单例（与 engine / crypto 保持一致的 init/get 风格）
# ---------------------------------------------------------------------------
_store: SettingStore | None = None


def init_setting_store(
    database: Database | None = None,
    secret_box: SecretBox | None = None,
) -> SettingStore:
    """初始化全局配置存储单例。应在应用启动（lifespan）时调用一次。"""
    global _store
    if _store is not None:
        logger.warning("配置存储已初始化，重复调用 init_setting_store 被忽略")
        return _store
    _store = SettingStore(database=database, secret_box=secret_box)
    return _store


def get_setting_store() -> SettingStore:
    """获取全局配置存储单例。未初始化时抛错，提示调用方检查启动流程。"""
    if _store is None:
        raise RuntimeError("配置存储尚未初始化，请确认应用启动时已调用 init_setting_store()")
    return _store


def reset_setting_store() -> None:
    """清空全局配置存储单例（主要供测试在用例间隔离状态）。"""
    global _store
    _store = None
