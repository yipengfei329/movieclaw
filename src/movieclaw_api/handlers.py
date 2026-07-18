import logging
from typing import Any

from fastapi import FastAPI, HTTPException, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse

from movieclaw_api.exceptions import AppException
from movieclaw_api.schemas.response import ErrorResponse

logger = logging.getLogger("movieclaw_api.errors")

DEFAULT_ERROR_CODE_BY_STATUS: dict[int, str] = {
    400: "BAD_REQUEST",
    401: "UNAUTHORIZED",
    403: "FORBIDDEN",
    404: "RESOURCE_NOT_FOUND",
    409: "CONFLICT",
    422: "VALIDATION_ERROR",
    500: "INTERNAL_SERVER_ERROR",
}


def _build_error_response(
    *,
    status_code: int,
    code: str,
    message: str,
    details: list[dict[str, Any]] | None = None,
) -> JSONResponse:
    payload = ErrorResponse(code=code, message=message, details=details)
    return JSONResponse(status_code=status_code, content=payload.model_dump(exclude_none=True))


def _validation_details(exc: RequestValidationError) -> list[dict[str, Any]]:
    return [
        {
            "type": error["type"],
            "location": [str(item) for item in error["loc"]],
            "message": error["msg"],
            "input": error.get("input"),
        }
        for error in exc.errors()
    ]


def register_exception_handlers(app: FastAPI) -> None:
    @app.exception_handler(AppException)
    async def handle_app_exception(_: Request, exc: AppException) -> JSONResponse:
        return _build_error_response(
            status_code=exc.status_code,
            code=exc.code,
            message=exc.message,
            details=exc.details,
        )

    @app.exception_handler(RequestValidationError)
    async def handle_validation_exception(_: Request, exc: RequestValidationError) -> JSONResponse:
        return _build_error_response(
            status_code=422,
            code="VALIDATION_ERROR",
            message="request validation failed",
            details=_validation_details(exc),
        )

    @app.exception_handler(HTTPException)
    async def handle_http_exception(_: Request, exc: HTTPException) -> JSONResponse:
        detail = exc.detail
        code = DEFAULT_ERROR_CODE_BY_STATUS.get(exc.status_code, "HTTP_ERROR")
        message = "request failed"
        details = None

        if isinstance(detail, dict):
            code = detail.get("code", code)
            message = detail.get("message", message)
            details = detail.get("details")
        elif isinstance(detail, str):
            message = detail

        return _build_error_response(
            status_code=exc.status_code,
            code=code,
            message=message,
            details=details,
        )

    @app.exception_handler(Exception)
    async def handle_unexpected_exception(request: Request, exc: Exception) -> JSONResponse:
        logger.exception(
            "Unhandled application error method=%s path=%s",
            request.method,
            request.url.path,
            exc_info=exc,
        )
        return _build_error_response(
            status_code=500,
            code="INTERNAL_SERVER_ERROR",
            message="internal server error",
        )
