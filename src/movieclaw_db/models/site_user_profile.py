from __future__ import annotations

from datetime import datetime

from sqlmodel import Field

from movieclaw_db.models.base import TimestampMixin, utcnow


class SiteUserProfile(TimestampMixin, table=True):
    """站点用户资料快照表：缓存每个站点最近一次抓取到的账号数据。

    定位是**派生缓存**，与 ``site_credential``（用户输入的配置）职责分离：
    - 数据来源：站点验证流程（verify_site）成功后顺手落库 —— 验证的判据本身
      就是"认证 + 拉一次用户资料"，因此这里不产生任何额外的站点请求。
    - 每个站点仅一行（``site_id`` 唯一），验证成功即整行覆盖为最新快照；
      验证失败不动旧数据（保留上一次成功的快照，新鲜度看 ``fetched_at``）。
    - 删除站点配置时连带删除本表对应行。

    字段与 ``movieclaw_tracker.models.UserProfile`` 对应；上传/下载量只存字节数
    （站点原始文本如 "1.5 TB" 各站格式不一，统一由前端格式化展示）。
    """

    __tablename__ = "site_user_profile"

    id: int | None = Field(default=None, primary_key=True)
    # 站点标识，对应 registry 里注册的 site_id，一个站点仅一份资料快照
    site_id: str = Field(index=True, unique=True, description="站点标识，如 mteam、ttg")

    user_id: str = Field(default="", description="站点用户 ID")
    username: str = Field(description="站点用户名")
    user_class: str = Field(default="", description="用户等级，如 Power User")
    uploaded_bytes: int = Field(default=0, description="上传量（字节）")
    downloaded_bytes: int = Field(default=0, description="下载量（字节）")
    # None = 站点未提供/未解析到；0.0 有实际含义（无上传），二者不可混淆
    ratio: float | None = Field(default=None, description="分享率")
    bonus: float | None = Field(default=None, description="魔力值（积分）")
    seeding_count: int = Field(default=0, description="当前做种数")
    leeching_count: int = Field(default=0, description="当前下载数")
    avatar_url: str | None = Field(default=None, description="头像地址")
    join_date: datetime | None = Field(default=None, description="注册日期")
    # 本次快照的抓取时间；验证失败不刷新，因此它反映"数据有多新鲜"
    fetched_at: datetime = Field(default_factory=utcnow, description="快照抓取时间")
