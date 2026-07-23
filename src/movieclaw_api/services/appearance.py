from __future__ import annotations

import logging
import re
from pathlib import Path
from uuid import uuid4

from movieclaw_api.core.config import get_settings

logger = logging.getLogger("movieclaw_api.appearance")

# 支持的图片 MIME 类型 → 落盘扩展名。只接受常见位图/网络图格式：
# 刻意不收 image/svg+xml —— SVG 可内嵌脚本，作为背景图存下来存在 XSS 风险。
_CONTENT_TYPE_EXT: dict[str, str] = {
    "image/jpeg": ".jpg",
    "image/png": ".png",
    "image/webp": ".webp",
    "image/avif": ".avif",
    "image/gif": ".gif",
}
# 反查：扩展名 → MIME，供读取时回填 Content-Type
_EXT_CONTENT_TYPE: dict[str, str] = {ext: ct for ct, ext in _CONTENT_TYPE_EXT.items()}

# ---------------------------------------------------------------------------
# 存储模型：背景图「图库」
#
# 用户上传的背景图全部保留在 media_dir/backdrops/ 下，文件名 <id>.<ext>，
# id 为 uuid4 的 32 位十六进制——同时充当对外的资源 id 与防路径穿越的白名单格式。
# 「当前生效哪张」记录在图库目录下的 .active 纯文本标记文件里（内容就是 id）；
# 标记不存在（或指向已删除的图）即表示使用内置默认背景。
# 不引入数据库表：图库本身就是文件，标记文件与图同目录，Docker 卷一起持久化。
# ---------------------------------------------------------------------------

_GALLERY_DIR = "backdrops"
_ACTIVE_MARKER = ".active"
# 旧版单槽位文件（media_dir/backdrop.*），首次访问图库时自动迁移进来
_LEGACY_STEM = "backdrop"

# id 白名单：uuid4().hex（32 位小写十六进制）。路径参数必须先过这道校验，
# 杜绝 "../" 之类的路径穿越输入触达文件系统。
_ID_RE = re.compile(r"^[0-9a-f]{32}$")

# 单张背景图体积上限。前端上传前会把长边压到 2560px 的 JPEG（通常不足 1MB），
# 这里留足冗余并作为防滥用的硬上限——直接调 API 传超大图会被挡下。
MAX_BACKDROP_BYTES = 10 * 1024 * 1024

# 图库张数上限：防止无限上传把磁盘吃满。达到上限后需先删除旧图再传新图。
MAX_BACKDROP_COUNT = 20


def _media_dir() -> Path:
    return Path(get_settings().media_dir)


def _gallery_dir() -> Path:
    return _media_dir() / _GALLERY_DIR


def _marker_path() -> Path:
    return _gallery_dir() / _ACTIVE_MARKER


def is_supported_content_type(content_type: str | None) -> bool:
    """判断上传的 MIME 是否为受支持的图片类型。"""
    return content_type in _CONTENT_TYPE_EXT


def is_valid_id(backdrop_id: str) -> bool:
    """校验背景图 id 是否为合法格式（uuid4 hex），不合法的一律视为不存在。"""
    return bool(_ID_RE.match(backdrop_id))


def content_type_for(path: Path) -> str:
    """按文件扩展名回填 Content-Type，未知类型退回二进制流。"""
    return _EXT_CONTENT_TYPE.get(path.suffix.lower(), "application/octet-stream")


def _migrate_legacy() -> None:
    """把旧版单槽位的 media_dir/backdrop.* 迁移进图库并设为当前生效图。

    旧版同一时刻只保留一张、且上传即生效，语义上等价于「图库里唯一一张 + 生效」。
    迁移是幂等的：旧文件搬走后不会再次触发。
    """
    media = _media_dir()
    if not media.is_dir():
        return
    for path in sorted(media.glob(f"{_LEGACY_STEM}.*")):
        if not path.is_file():
            continue
        gallery = _gallery_dir()
        gallery.mkdir(parents=True, exist_ok=True)
        new_id = uuid4().hex
        target = gallery / f"{new_id}{path.suffix.lower()}"
        path.rename(target)
        _write_active(new_id)
        logger.info("已将旧版单张背景图迁移进图库：%s", target)


def _write_active(backdrop_id: str | None) -> None:
    """写入/清除生效标记。传 None 表示恢复内置默认背景。"""
    marker = _marker_path()
    if backdrop_id is None:
        marker.unlink(missing_ok=True)
        return
    marker.parent.mkdir(parents=True, exist_ok=True)
    marker.write_text(backdrop_id, encoding="utf-8")


def _find(backdrop_id: str) -> Path | None:
    """在图库中按 id 定位图片文件（不触发旧版迁移的内部版本）。"""
    if not is_valid_id(backdrop_id):
        return None
    gallery = _gallery_dir()
    if not gallery.is_dir():
        return None
    for path in gallery.glob(f"{backdrop_id}.*"):
        if path.is_file():
            return path
    return None


def list_backdrops() -> list[Path]:
    """列出图库中的全部背景图，按上传时间（mtime）升序——新图排在末尾。"""
    _migrate_legacy()
    gallery = _gallery_dir()
    if not gallery.is_dir():
        return []
    files = [p for p in gallery.iterdir() if p.is_file() and p.stem != "" and is_valid_id(p.stem)]
    return sorted(files, key=lambda p: (p.stat().st_mtime_ns, p.name))


def find_backdrop(backdrop_id: str) -> Path | None:
    """按 id 查找背景图文件；不存在（或 id 非法）返回 None。"""
    _migrate_legacy()
    return _find(backdrop_id)


def get_active_id() -> str | None:
    """读取当前生效的背景图 id；未设置或指向已删除的图时返回 None（= 默认背景）。"""
    _migrate_legacy()
    marker = _marker_path()
    if not marker.is_file():
        return None
    backdrop_id = marker.read_text(encoding="utf-8").strip()
    if _find(backdrop_id) is None:
        return None
    return backdrop_id


def save_backdrop(data: bytes, content_type: str) -> Path:
    """把新背景图存入图库并设为当前生效图，返回落盘路径。

    调用方须先用 ``is_supported_content_type``、``MAX_BACKDROP_BYTES`` 与
    ``MAX_BACKDROP_COUNT`` 校验。旧图一律保留，供用户随时切换。
    """
    _migrate_legacy()
    ext = _CONTENT_TYPE_EXT[content_type]
    gallery = _gallery_dir()
    gallery.mkdir(parents=True, exist_ok=True)
    backdrop_id = uuid4().hex
    target = gallery / f"{backdrop_id}{ext}"
    target.write_bytes(data)
    _write_active(backdrop_id)
    logger.info("已保存新背景图并设为生效：%s（%d 字节）", target, len(data))
    return target


def set_active(backdrop_id: str | None) -> bool:
    """切换当前生效的背景图。

    传 None 切回内置默认背景（不删除任何图）。指定的 id 在图库中不存在时
    返回 False、不做任何修改。
    """
    _migrate_legacy()
    if backdrop_id is not None and _find(backdrop_id) is None:
        return False
    _write_active(backdrop_id)
    logger.info("已切换背景图：%s", backdrop_id or "内置默认")
    return True


def remove_backdrop(backdrop_id: str) -> bool:
    """从图库删除一张背景图；若删的是当前生效图，则回退到内置默认背景。

    返回是否确有文件被删除。
    """
    _migrate_legacy()
    path = _find(backdrop_id)
    if path is None:
        return False
    was_active = get_active_id() == backdrop_id
    path.unlink()
    if was_active:
        _write_active(None)
    logger.info("已删除背景图：%s%s", path, "（原为生效图，已回退内置默认）" if was_active else "")
    return True
