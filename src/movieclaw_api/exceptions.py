from typing import Any


class AppException(Exception):
    def __init__(
        self,
        *,
        status_code: int,
        code: str,
        message: str,
        details: list[dict[str, Any]] | None = None,
    ) -> None:
        super().__init__(message)
        self.status_code = status_code
        self.code = code
        self.message = message
        self.details = details


class BadRequestException(AppException):
    def __init__(
        self,
        message: str = "bad request",
        details: list[dict[str, Any]] | None = None,
    ) -> None:
        super().__init__(
            status_code=400,
            code="BAD_REQUEST",
            message=message,
            details=details,
        )


class UnauthorizedException(AppException):
    def __init__(
        self,
        message: str = "unauthorized",
        details: list[dict[str, Any]] | None = None,
    ) -> None:
        super().__init__(
            status_code=401,
            code="UNAUTHORIZED",
            message=message,
            details=details,
        )


class ForbiddenException(AppException):
    def __init__(
        self,
        message: str = "forbidden",
        details: list[dict[str, Any]] | None = None,
    ) -> None:
        super().__init__(
            status_code=403,
            code="FORBIDDEN",
            message=message,
            details=details,
        )


class NotFoundException(AppException):
    def __init__(
        self,
        message: str = "resource not found",
        details: list[dict[str, Any]] | None = None,
    ) -> None:
        super().__init__(
            status_code=404,
            code="RESOURCE_NOT_FOUND",
            message=message,
            details=details,
        )


class UpstreamServiceException(AppException):
    """上游数据服务（如 TMDB）不可用、未配置或请求失败。"""

    def __init__(
        self,
        message: str = "upstream service error",
        details: list[dict[str, Any]] | None = None,
    ) -> None:
        super().__init__(
            status_code=502,
            code="UPSTREAM_ERROR",
            message=message,
            details=details,
        )


class UpstreamUnreachableException(AppException):
    """上游服务在网络层面不可达（连接失败/超时/熔断）。

    与 ``UpstreamServiceException``（服务可达但出错）区分：本异常携带结构化的
    service 与 hint，前端据 code=UPSTREAM_UNREACHABLE 渲染引导式错误态
    （说明原因 + 跳转「设置 → 网络」），而不是一句裸的 502。
    """

    def __init__(self, message: str, *, service: str, hint: str) -> None:
        super().__init__(
            status_code=502,
            code="UPSTREAM_UNREACHABLE",
            message=message,
            details=[{"service": service, "hint": hint}],
        )


class ConflictException(AppException):
    def __init__(
        self,
        message: str = "conflict",
        details: list[dict[str, Any]] | None = None,
    ) -> None:
        super().__init__(
            status_code=409,
            code="CONFLICT",
            message=message,
            details=details,
        )
