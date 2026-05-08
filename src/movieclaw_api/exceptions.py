from typing import Any, Optional


class AppException(Exception):
    def __init__(
        self,
        *,
        status_code: int,
        code: str,
        message: str,
        details: Optional[list[dict[str, Any]]] = None,
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
        details: Optional[list[dict[str, Any]]] = None,
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
        details: Optional[list[dict[str, Any]]] = None,
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
        details: Optional[list[dict[str, Any]]] = None,
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
        details: Optional[list[dict[str, Any]]] = None,
    ) -> None:
        super().__init__(
            status_code=404,
            code="RESOURCE_NOT_FOUND",
            message=message,
            details=details,
        )


class ConflictException(AppException):
    def __init__(
        self,
        message: str = "conflict",
        details: Optional[list[dict[str, Any]]] = None,
    ) -> None:
        super().__init__(
            status_code=409,
            code="CONFLICT",
            message=message,
            details=details,
        )
