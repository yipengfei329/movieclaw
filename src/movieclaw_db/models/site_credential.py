from __future__ import annotations

from datetime import datetime
from enum import StrEnum

from sqlmodel import Field

from movieclaw_db.models.base import TimestampMixin


class AuthType(StrEnum):
    """站点认证方式。与 ``movieclaw_tracker`` 的三种 AuthProvider 一一对应。

    - ``COOKIE``：用户直接粘贴浏览器 cookie（最简单，见 CookieAuthProvider）
    - ``APIKEY``：走站点 API 的密钥认证（如 M-Team，见 ApiKeyAuthProvider）
    - ``CREDENTIAL``：用户名 + 密码，由程序模拟登录（见 CredentialAuthProvider）
    """

    COOKIE = "cookie"
    APIKEY = "apikey"
    CREDENTIAL = "credential"


class ConfigStatus(StrEnum):
    """站点配置的验证状态机。

    用户填入授权信息后并不立刻可用，需异步验证通过才算数。状态流转：

        PENDING ──► VERIFYING ──► ACTIVE   （验证成功，可用）
                          └─────► FAILED   （验证失败，见 last_error）

    - ``PENDING``：已保存，等待验证（刚配置或刚更新后的初始态）。
    - ``VERIFYING``：验证进行中（异步任务已接手）。
    - ``ACTIVE``：验证通过，凭据真实有效。
    - ``FAILED``：验证失败（密码错误、cookie 过期、网络不通等，原因见 last_error）。

    注意：「一个站点是否可用」= ``enabled=True`` 且 ``status=ACTIVE``。
    ``enabled`` 是用户的启用开关（意图），``status`` 是系统的验证结果，二者正交。
    """

    PENDING = "pending"
    VERIFYING = "verifying"
    ACTIVE = "active"
    FAILED = "failed"


class SiteCredential(TimestampMixin, table=True):
    """站点授权凭据表：保存用户为每个站点配置的登录信息。

    这是应用的核心配置数据。每个站点一条记录（``site_id`` 唯一），
    根据 ``auth_type`` 使用不同的字段组合：
    - COOKIE 模式 → 使用 ``cookie``
    - APIKEY 模式 → 使用 ``api_key``
    - CREDENTIAL 模式 → 使用 ``username`` + ``password``

    ⚠️ 安全提示：cookie / api_key / password 属于敏感信息，当前为明文存储。
    后续应接入字段级加密（用启动时注入的主密钥加密），详见项目 TODO。
    """

    __tablename__ = "site_credential"

    id: int | None = Field(default=None, primary_key=True)
    # 站点标识，对应 registry 里注册的 site_id，一个站点仅一套凭据
    site_id: str = Field(index=True, unique=True, description="站点标识，如 mteam、ttg")
    auth_type: AuthType = Field(description="认证方式")

    # 以下敏感字段按 auth_type 选择性填写，未使用的保持 None
    cookie: str | None = Field(default=None, description="COOKIE 模式：原始 cookie 字符串")
    api_key: str | None = Field(default=None, description="APIKEY 模式：API 密钥")
    username: str | None = Field(default=None, description="CREDENTIAL 模式：用户名")
    password: str | None = Field(default=None, description="CREDENTIAL 模式：密码")

    # 是否启用该站点；停用后不参与聚合搜索等操作，但保留凭据便于随时恢复
    enabled: bool = Field(default=True, description="用户启用开关")

    # ------------------------------------------------------------------
    # 验证状态机（见 ConfigStatus）
    # ------------------------------------------------------------------
    status: ConfigStatus = Field(
        default=ConfigStatus.PENDING,
        index=True,
        description="验证状态：pending/verifying/active/failed",
    )
    # 最近一次验证成功的时间；None 表示从未验证成功
    last_verified_at: datetime | None = Field(default=None, description="最近验证成功时间")
    # 最近一次验证尝试的时间（无论成败都刷新），供页面显示"上次检查于何时"
    last_checked_at: datetime | None = Field(default=None, description="最近验证尝试时间")
    # 最近一次验证失败的原因，已归类为清晰中文，直接展示给用户帮助非开发者排查
    last_error: str | None = Field(default=None, description="最近验证失败原因")
