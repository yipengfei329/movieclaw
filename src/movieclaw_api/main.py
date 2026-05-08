import uvicorn
from fastapi import FastAPI

from movieclaw_api.app import create_app
from movieclaw_api.core.config import get_settings


def app() -> FastAPI:
    return create_app()


def run() -> None:
    settings = get_settings()
    uvicorn.run(
        "movieclaw_api.main:app",
        factory=True,
        host=settings.host,
        port=settings.port,
        reload=settings.reload,
    )


if __name__ == "__main__":
    run()

