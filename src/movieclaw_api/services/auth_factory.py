from __future__ import annotations

from movieclaw_db.models.site_credential import AuthType, SiteCredential
from movieclaw_tracker import (
    ApiKeyAuthProvider,
    AuthProvider,
    CookieAuthProvider,
    CredentialAuthProvider,
)

# ---------------------------------------------------------------------------
# 授权类型 → 必填字段
# ---------------------------------------------------------------------------
# 这是"目录告诉前端要填什么"以及"配置时校验是否填全"的唯一事实来源。
# 字段名与 SiteCredential 的列名一致，便于直接映射。
REQUIRED_FIELDS_BY_AUTH_TYPE: dict[AuthType, tuple[str, ...]] = {
    AuthType.COOKIE: ("cookie",),
    AuthType.APIKEY: ("api_key",),
    AuthType.CREDENTIAL: ("username", "password"),
}


def required_fields(auth_type: AuthType) -> tuple[str, ...]:
    """返回某授权类型需要用户填写的字段名列表。"""
    return REQUIRED_FIELDS_BY_AUTH_TYPE.get(auth_type, ())


def missing_required_fields(auth_type: AuthType, values: dict[str, object]) -> list[str]:
    """校验必填字段是否齐全，返回缺失（None 或空串）的字段名列表。"""
    missing: list[str] = []
    for name in required_fields(auth_type):
        value = values.get(name)
        if value is None or (isinstance(value, str) and not value.strip()):
            missing.append(name)
    return missing


def build_auth_provider(credential: SiteCredential) -> AuthProvider:
    """把数据库里的凭据记录转换成 tracker 可用的 AuthProvider。

    这是"存储层凭据"与"tracker 认证层"之间的桥梁：验证流程、以及未来真正
    调用站点功能时，都通过它把用户填的授权信息还原成可执行的认证策略。

    调用前应确保必填字段已齐全（由 SiteConfigService 在写入时保证）。
    """
    auth_type = credential.auth_type
    if auth_type == AuthType.COOKIE:
        return CookieAuthProvider(credential.cookie or "")
    if auth_type == AuthType.APIKEY:
        return ApiKeyAuthProvider(credential.api_key or "")
    if auth_type == AuthType.CREDENTIAL:
        return CredentialAuthProvider(
            username=credential.username or "",
            password=credential.password or "",
        )
    # 理论上不会到这里（auth_type 是受约束的枚举），做防御式兜底
    raise ValueError(f"不支持的授权类型：{auth_type}")
