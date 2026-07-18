from __future__ import annotations

from sqlalchemy import JSON
from sqlmodel import Field

from movieclaw_db.models.base import TimestampMixin


class SiteCookie(TimestampMixin, table=True):
    """运行期 Cookie 缓存表：保存认证成功后获得的 cookie 会话。

    与 ``SiteCredential`` 的区别：
    - ``SiteCredential`` 是"用户配置的凭据"（账号密码等），由用户填写，很少变化。
    - ``SiteCookie`` 是"程序登录后拿到的会话 cookie"，由程序自动写入，会随
      登录/过期而刷新。把它单独落库，进程重启后无需重新登录，减少对站点的登录请求。

    这张表正是 tracker 层 ``CookieStore`` 协议的持久化后端（见 stores.py 的
    ``SqlCookieStore``）。cookies 以 JSON 字典形式整体存储。
    """

    __tablename__ = "site_cookie"

    id: int | None = Field(default=None, primary_key=True)
    site_id: str = Field(index=True, unique=True, description="站点标识")
    # cookie 键值对整体以 JSON 存储，读写都是一个完整的会话，无需拆表
    cookies: dict[str, str] = Field(default_factory=dict, sa_type=JSON)
