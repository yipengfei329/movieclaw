from __future__ import annotations

from typing import Any


class TrackerException(Exception):
    """所有 tracker 模块异常的基类。"""

    def __init__(
        self,
        message: str = "tracker operation failed",
        *,
        details: dict[str, Any] | None = None,
    ) -> None:
        super().__init__(message)
        self.message = message
        self.details = details


class TrackerAuthError(TrackerException):
    """认证失败（凭证无效、会话过期等）。"""

    def __init__(
        self,
        message: str = "authentication failed",
        *,
        details: dict[str, Any] | None = None,
    ) -> None:
        super().__init__(message, details=details)


class TrackerNetworkError(TrackerException):
    """网络错误（重试耗尽后仍无法访问）。"""

    def __init__(
        self,
        message: str = "site unreachable after retries",
        *,
        details: dict[str, Any] | None = None,
    ) -> None:
        super().__init__(message, details=details)


class TrackerParseError(TrackerException):
    """HTML/JSON 解析失败（页面结构不符合预期）。"""

    def __init__(
        self,
        message: str = "failed to parse site response",
        *,
        details: dict[str, Any] | None = None,
    ) -> None:
        super().__init__(message, details=details)


class SiteNotFoundError(TrackerException):
    """请求的 site_id 在注册表中不存在。"""

    def __init__(
        self,
        site_id: str,
        *,
        details: dict[str, Any] | None = None,
    ) -> None:
        super().__init__(f"site '{site_id}' is not registered", details=details)
        self.site_id = site_id
