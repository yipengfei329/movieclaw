from __future__ import annotations

from pydantic import BaseModel, Field

from movieclaw_db.models.site_credential import ConfigStatus, SiteCredential

# ---------------------------------------------------------------------------
# 插件推送 Cookie
# ---------------------------------------------------------------------------


class CookiePushRequest(BaseModel):
    """插件推送某站点 Cookie 的请求体。

    插件只需上报"当前浏览器域名 + 拼好的 Cookie 串"，站点识别交给后端按域名反查。
    """

    domain: str = Field(description="浏览器域名，如 kp.m-team.cc")
    cookie: str = Field(description="拼好的 Cookie 请求头字符串：name=value; name2=value2")


class CookieSyncResult(BaseModel):
    """推送结果：告诉插件命中了哪个站点、当前验证状态如何。"""

    site_id: str
    display_name: str
    domain: str = Field(description="命中该站点所用的浏览器域名")
    status: ConfigStatus = Field(description="凭据验证状态（推送后通常为 verifying）")
    usable: bool = Field(description="是否可用 = 已启用且验证通过")

    @classmethod
    def from_model(cls, row: SiteCredential, *, display_name: str, domain: str) -> CookieSyncResult:
        return cls(
            site_id=row.site_id,
            display_name=display_name,
            domain=domain,
            status=row.status,
            usable=row.enabled and row.status == ConfigStatus.ACTIVE,
        )


# ---------------------------------------------------------------------------
# 插件查询：支持 Cookie 同步的站点
# ---------------------------------------------------------------------------


class ExtensionSiteView(BaseModel):
    """供插件识别"当前站点是否被支持"的站点视图。"""

    site_id: str
    display_name: str
    domain: str = Field(description="该站点的匹配域名（可注册域名），插件据此比对当前标签页")
    configured: bool = Field(description="用户是否已配置该站点")
    status: ConfigStatus | None = Field(default=None, description="已配置时的验证状态")
    usable: bool = Field(default=False, description="是否可用 = 已启用且验证通过")


# ---------------------------------------------------------------------------
# 令牌管理（Web 后台用）
# ---------------------------------------------------------------------------


class SyncTokenView(BaseModel):
    """同步令牌的对外视图。"""

    enabled: bool = Field(description="是否已启用同步（令牌是否存在）")
    token: str | None = Field(default=None, description="当前令牌明文，供复制进插件；未启用为 None")
    created_at: str | None = Field(default=None, description="令牌生成时间")


class PingResult(BaseModel):
    """连接自检结果。"""

    ok: bool = True
    app_name: str
