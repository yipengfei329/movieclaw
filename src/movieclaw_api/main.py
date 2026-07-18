import uvicorn
from fastapi import FastAPI

from movieclaw_api.app import create_app
from movieclaw_api.core.config import get_settings
from movieclaw_api.core.logging import configure_logging


def app() -> FastAPI:
    return create_app()


def run() -> None:
    settings = get_settings()
    # 启动 uvicorn 前先装配好根 logger：让「Started server process」等启动日志
    # 也进入按天落盘的日志文件（设置页「系统日志」的数据来源）。
    configure_logging(settings.log_level, settings.log_dir, settings.log_retention_days)
    uvicorn.run(
        "movieclaw_api.main:app",
        factory=True,
        host=settings.host,
        port=settings.port,
        reload=settings.reload,
        # 不让 uvicorn 接管日志配置：它的 logger 直接向根 logger 传播，
        # 与业务日志走同一套「控制台 + 按天落盘」Handler、同一格式。
        log_config=None,
        # uvicorn 自带的访问日志与 middleware.py 的访问日志重复，且后者
        # 更详细（含耗时）并受 APP_ACCESS_LOG_ENABLED 开关控制，故关掉前者。
        access_log=False,
    )


if __name__ == "__main__":
    run()

