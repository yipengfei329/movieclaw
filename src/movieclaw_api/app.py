from fastapi import FastAPI

from movieclaw_api.api.router import api_router
from movieclaw_api.core.config import get_settings
from movieclaw_api.core.logging import configure_logging
from movieclaw_api.handlers import register_exception_handlers
from movieclaw_api.middleware import register_middlewares


def create_app() -> FastAPI:
    settings = get_settings()
    configure_logging(settings.log_level)

    app = FastAPI(
        title=settings.app_name,
        version="0.1.0",
        openapi_url=f"{settings.api_v1_prefix}/openapi.json",
        docs_url="/docs",
        redoc_url="/redoc",
    )

    register_exception_handlers(app)
    register_middlewares(app, settings)
    app.include_router(api_router, prefix=settings.api_v1_prefix)
    return app

