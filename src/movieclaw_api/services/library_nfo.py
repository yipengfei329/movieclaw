"""NFO 写出（媒体库 L4）：入库时在条目目录生成身份 NFO，反哺播放器生态。

价值双向：
- **给 Emby/Jellyfin**：NFO 里的 tmdbid 让下游零歧义入档（命名歧义导致的
  误合并是 Emby 社区最大痛点，见设计文档 1.5 节）；
- **给自己**：库目录一旦重扫（换库、迁移后重建台账），扫描器的 NFO 优先
  识别链直接读回精确身份，免去再走 TMDB 收敛。

原则：**已存在的 NFO 绝不覆盖**（存量目录的 NFO 可能来自 TMM/Emby，内容
比我们的最小 NFO 丰富）；写出失败只告警不阻断入库。
"""

from __future__ import annotations

import logging
from pathlib import Path
from xml.sax.saxutils import escape

from movieclaw_db.models import MediaItem
from movieclaw_media.models import MediaKind

logger = logging.getLogger("movieclaw_api.library_nfo")


def write_entry_nfo(entry_dir: Path, item: MediaItem) -> None:
    """在条目目录写出最小身份 NFO（电影 movie.nfo / 剧集 tvshow.nfo）。

    同步函数（调用方放线程池）。目录不存在或 NFO 已存在时直接返回。
    """
    if not entry_dir.is_dir():
        return
    kind = MediaKind(item.kind)
    root_tag = "movie" if kind is MediaKind.MOVIE else "tvshow"
    nfo_path = entry_dir / ("movie.nfo" if kind is MediaKind.MOVIE else "tvshow.nfo")
    if nfo_path.exists():
        return  # 尊重既有刮削成果（TMM/Emby 的 NFO 比我们的更丰富）

    lines = [
        '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>',
        f"<{root_tag}>",
        f"  <title>{escape(item.title)}</title>",
        f"  <originaltitle>{escape(item.original_title)}</originaltitle>",
    ]
    if item.year is not None:
        lines.append(f"  <year>{item.year}</year>")
    lines.append(f"  <tmdbid>{item.tmdb_id}</tmdbid>")
    lines.append(f'  <uniqueid type="tmdb" default="true">{item.tmdb_id}</uniqueid>')
    if item.imdb_id:
        lines.append(f'  <uniqueid type="imdb">{escape(item.imdb_id)}</uniqueid>')
    lines.append(f"</{root_tag}>")

    try:
        nfo_path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    except OSError as exc:
        logger.warning("NFO 写出失败（不影响入库）：%s（%s）", nfo_path, exc)
        return
    logger.info("已写出 NFO：%s（tmdbid=%s）", nfo_path, item.tmdb_id)
