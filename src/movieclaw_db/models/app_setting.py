from __future__ import annotations

from sqlmodel import Field

from movieclaw_db.models.base import TimestampMixin


class AppSetting(TimestampMixin, table=True):
    """通用配置表：承载系统里一切"用户在运行时可修改、需持久化"的配置。

    设计取舍：为什么是"一张通用表 + JSON 值"，而不是每个集成建一张表
    -------------------------------------------------------------------
    系统未来的集成配置会持续膨胀（大模型、BT 下载器、媒体服务器……）。若每
    新增一类集成就建一张表 + 写一次迁移，是一笔持续的开发税。这里改用"按域
    （namespace）存一条 JSON"的通用结构，好处是**新增一个集成 = 加一个 Pydantic
    模型 + 注册，零数据库迁移**。

    代价是数据库层没有列级 schema 约束——这部分交由应用边界的 Pydantic 模型补回
    （枚举、URL、区间校验反而比数据库约束更有表达力）。读写都必须过
    ``SettingStore`` 的校验，不要绕过它直接塞 JSON。

    与 ``SiteCredential`` 的关系：二者同构。一个是"每站点一条凭据"，一个是
    "每配置域一条设置"，共用 TimestampMixin、Repository 分层与字段加密能力。

    namespace 约定
    --------------
    用点号分层的稳定字符串标识一个配置域，例如：
    - ``system.bootstrap``       —— 首次引导状态（是否完成初始化）
    - ``llm.openai``             —— OpenAI 兼容大模型设定
    - ``downloader.qbittorrent`` —— qBittorrent 下载器设定
    - ``media.emby``             —— Emby 媒体服务器设定

    敏感字段（api_key/password/token 等）在写入 ``value_json`` 前，由
    ``SettingStore`` 借助 ``SecretBox`` 逐字段加密，因此本表里存的是含密文的 JSON。
    """

    __tablename__ = "app_setting"

    id: int | None = Field(default=None, primary_key=True)
    # 配置域标识，全局唯一。一个域一条记录，整体覆盖式读写。
    namespace: str = Field(
        index=True,
        unique=True,
        description="配置域标识，点号分层，如 llm.openai、downloader.qbittorrent",
    )
    # 该域配置的完整 JSON 序列化字符串（其中敏感字段为密文）。
    # 存字符串而非 SQLModel 的 JSON 列：SQLite 下行为最可控，且加解密在应用层完成。
    value_json: str = Field(description="该配置域的 JSON 值，敏感字段已加密")
