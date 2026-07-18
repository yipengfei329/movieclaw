"""管理员头像的文件存储服务。

存储模型：单槽位文件
--------------------
系统只有一个超级管理员，头像因此是「单槽位」：文件固定存在
``media_dir/avatar.<ext>``，上传新头像即替换旧文件（扩展名可能变化，
先删旧再写新）。不入库、不建图库——与背景图库（appearance.py）相比，
头像没有"保留多张随时切换"的需求，一个文件最简单也最够用。

与背景图一致的取舍：
- 只接受常见位图格式，刻意不收 SVG（可内嵌脚本，存在 XSS 风险）；
- 存放在 MEDIA_DIR 下，随 data/ 目录一起被 Docker 卷持久化；
- 对外 URL 带 mtime 版本号做缓存键，换头像后 URL 变化以绕开浏览器缓存。
"""

from __future__ import annotations

import logging
from pathlib import Path

from movieclaw_api.core.config import get_settings

logger = logging.getLogger("movieclaw_api.avatar")

# 支持的图片 MIME 类型 → 落盘扩展名（与背景图一致，拒绝 SVG）
_CONTENT_TYPE_EXT: dict[str, str] = {
    "image/jpeg": ".jpg",
    "image/png": ".png",
    "image/webp": ".webp",
    "image/avif": ".avif",
    "image/gif": ".gif",
}
_EXT_CONTENT_TYPE: dict[str, str] = {ext: ct for ct, ext in _CONTENT_TYPE_EXT.items()}

_AVATAR_STEM = "avatar"

# 头像体积上限。前端上传前会把长边压到 512px 的 JPEG（通常几十 KB），
# 这里留足冗余并作为防滥用的硬上限——直接调 API 传超大图会被挡下。
MAX_AVATAR_BYTES = 5 * 1024 * 1024


def _media_dir() -> Path:
    return Path(get_settings().media_dir)


def is_supported_content_type(content_type: str | None) -> bool:
    """判断上传的 MIME 是否为受支持的图片类型。"""
    return content_type in _CONTENT_TYPE_EXT


def content_type_for(path: Path) -> str:
    """按文件扩展名回填 Content-Type，未知类型退回二进制流。"""
    return _EXT_CONTENT_TYPE.get(path.suffix.lower(), "application/octet-stream")


def find_avatar() -> Path | None:
    """定位当前头像文件；尚未上传过头像时返回 None。"""
    media = _media_dir()
    if not media.is_dir():
        return None
    for path in sorted(media.glob(f"{_AVATAR_STEM}.*")):
        if path.is_file() and path.suffix.lower() in _EXT_CONTENT_TYPE:
            return path
    return None


def avatar_version() -> int | None:
    """头像的版本号（文件 mtime 纳秒值），无头像时返回 None。

    版本号拼进对外 URL：内容变化 → URL 变化，浏览器按 URL 命中缓存，
    借此在替换头像后强制所有展示处加载新图。
    """
    path = find_avatar()
    return path.stat().st_mtime_ns if path else None


def save_avatar(data: bytes, content_type: str) -> Path:
    """保存（替换）头像，返回落盘路径。

    调用方须先用 ``is_supported_content_type`` 与 ``MAX_AVATAR_BYTES`` 校验。
    新旧头像扩展名可能不同（如 PNG 换 JPEG），先删旧文件再写新文件。
    """
    media = _media_dir()
    media.mkdir(parents=True, exist_ok=True)
    old = find_avatar()
    if old is not None:
        old.unlink(missing_ok=True)
    target = media / f"{_AVATAR_STEM}{_CONTENT_TYPE_EXT[content_type]}"
    target.write_bytes(data)
    logger.info("已保存管理员头像：%s（%d 字节）", target, len(data))
    return target
