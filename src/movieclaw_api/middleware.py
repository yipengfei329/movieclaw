import logging
import time

from fastapi import FastAPI, Request

from movieclaw_api.core.config import Settings

logger = logging.getLogger("movieclaw_api.access")


def register_middlewares(app: FastAPI, settings: Settings) -> None:
    @app.middleware("http")
    async def access_log_middleware(request: Request, call_next):  # type: ignore[no-untyped-def]
        started_at = time.perf_counter()

        try:
            response = await call_next(request)
        except Exception:
            duration_ms = (time.perf_counter() - started_at) * 1000
            if settings.access_log_enabled:
                logger.error(
                    "method=%s path=%s status_code=%s duration_ms=%.2f message=%s",
                    request.method,
                    request.url.path,
                    500,
                    duration_ms,
                    "request failed",
                )
            raise

        duration_ms = (time.perf_counter() - started_at) * 1000

        if settings.access_log_enabled:
            log = logger.info
            if response.status_code >= 500:
                log = logger.error
            elif response.status_code >= 400:
                log = logger.warning

            log(
                "method=%s path=%s status_code=%s duration_ms=%.2f message=%s",
                request.method,
                request.url.path,
                response.status_code,
                duration_ms,
                "request completed",
            )

        return response

