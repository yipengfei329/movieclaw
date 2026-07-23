"""入库的公共命名约定与共享常量（扫描 / 监听导入 / 整理共用）。

历史沿革：本模块曾承载订阅专属的"下载完成 → 硬链入库"管线
（import_completed_torrent）。架构定稿"订阅止于投递"后，搬运统一由
监听导入（library_ingest，按 info_hash 认领订阅身份）与库扫描（原地
入账）完成，工单由库存对账关闭（wanted_fulfillment），订阅专属管线
退役。这里沉淀的是三个入库引擎共用的约定：

- ``VIDEO_EXTS``：视频文件扩展名（入库对象）；
- ``IN_PROGRESS_MARKERS``：下载器/浏览器的"未完成"标记后缀
  （扫描与监听导入的完整性检测共用）；
- ``_entry_base_name``：条目级规范名 ``标题 (年份)``——库目录名与
  规范文件名的公共前缀，与 ``derive_save_path`` 的目录名一致。
"""

from __future__ import annotations

from movieclaw_api.services.library_config import sanitize_folder_name
from movieclaw_db.models import MediaItem

# 视频文件扩展名（入库对象）；其余（字幕/nfo/图片）v1 不搬运
VIDEO_EXTS = {
    ".mkv",
    ".mp4",
    ".avi",
    ".ts",
    ".m2ts",
    ".wmv",
    ".mov",
    ".flv",
    ".rmvb",
    ".mpg",
    ".mpeg",
    ".m4v",
    ".webm",
}
# 文件名/路径含这些标记的视频不入库（样品片段等）
_IGNORE_MARKERS = ("sample",)

# 下载器/浏览器的"未完成"标记（文件名小写后缀匹配）：qBittorrent .!qb、
# aria2 控制文件 .aria2、Chrome .crdownload、Firefox/迅雷等 .part/.td、
# BitComet .bc!、通用临时后缀。扫描器与监听导入共用（放在本模块避免
# scan ↔ ingest 的循环导入）
IN_PROGRESS_MARKERS = (
    ".!qb",
    ".part",
    ".aria2",
    ".crdownload",
    ".download",
    ".downloading",
    ".td",
    ".bc!",
    ".tmp",
    ".temp",
    ".unfinished",
)


def _entry_base_name(item: MediaItem) -> str:
    """条目级规范名：``标题 (年份)``（中文优先，与库目录名一致）。"""
    base = sanitize_folder_name(item.title)
    return f"{base} ({item.year})" if item.year is not None else base
