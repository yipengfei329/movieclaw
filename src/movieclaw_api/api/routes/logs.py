"""系统日志查看接口：读取按天落盘的后端运行日志。

日志由 core/logging.py 的 DailyFileHandler 写入 ``{LOG_DIR}/movieclaw-YYYY-MM-DD.log``，
本路由只做只读展示：

- ``GET /system/logs``        列出所有可查看的日期（按日期倒序，含文件大小）；
- ``GET /system/logs/{day}``  读取某天的日志内容，默认只取末尾若干行（tail），
                              前端可传 ``tail=0`` 加载全天完整内容。

日期参数用严格的 YYYY-MM-DD 正则校验后再拼文件名，杜绝路径穿越。
文件读取是同步 IO，但单个日志文件体量小（天级切分 + 超期清理），直接读即可。
"""

from __future__ import annotations

from datetime import date
from pathlib import Path

from fastapi import APIRouter, Path as PathParam, Query
from pydantic import BaseModel

from movieclaw_api.core.config import get_settings
from movieclaw_api.core.logging import LOG_FILE_PATTERN, log_file_path
from movieclaw_api.exceptions import NotFoundException
from movieclaw_api.schemas.response import ApiResponse, ok

router = APIRouter(prefix="/system/logs", tags=["system"])

# 单次默认返回的行数：足够回溯当天问题，又不至于把超大日志一次塞给浏览器
DEFAULT_TAIL_LINES = 2000


class LogDay(BaseModel):
    """一个可查看的日志日期（对应磁盘上的一个日志文件）。"""

    day: str
    size_bytes: int


class LogDayList(BaseModel):
    days: list[LogDay]


class LogContent(BaseModel):
    """某天的日志内容（可能只是末尾片段，truncated 标记是否被截断）。"""

    day: str
    lines: list[str]
    total_lines: int
    truncated: bool
    size_bytes: int


def _log_dir() -> Path:
    return Path(get_settings().log_dir)


@router.get(
    "",
    response_model=ApiResponse[LogDayList],
    summary="列出可查看的日志日期",
)
async def list_log_days() -> ApiResponse[LogDayList]:
    """扫描日志目录，返回全部按天存档的日志文件（日期倒序，最新在前）。"""
    log_dir = _log_dir()
    days: list[LogDay] = []
    if log_dir.is_dir():
        for path in log_dir.iterdir():
            match = LOG_FILE_PATTERN.match(path.name)
            if match:
                days.append(LogDay(day=match.group(1), size_bytes=path.stat().st_size))
    days.sort(key=lambda d: d.day, reverse=True)
    return ok(LogDayList(days=days))


@router.get(
    "/{day}",
    response_model=ApiResponse[LogContent],
    summary="读取某天的日志内容",
)
async def read_log_day(
    day: str = PathParam(pattern=r"^\d{4}-\d{2}-\d{2}$", description="日期，如 2026-07-18"),
    tail: int = Query(
        default=DEFAULT_TAIL_LINES,
        ge=0,
        description="只返回末尾多少行；0 表示返回全天完整日志",
    ),
) -> ApiResponse[LogContent]:
    path = log_file_path(_log_dir(), date.fromisoformat(day))
    if not path.is_file():
        raise NotFoundException(f"没有 {day} 的日志文件，可能当天服务未运行或日志已超期清理")

    all_lines = path.read_text(encoding="utf-8", errors="replace").splitlines()
    truncated = tail > 0 and len(all_lines) > tail
    return ok(
        LogContent(
            day=day,
            lines=all_lines[-tail:] if truncated else all_lines,
            total_lines=len(all_lines),
            truncated=truncated,
            size_bytes=path.stat().st_size,
        )
    )
