"""远程图片的本地磁盘缓存 —— 静态资源统一收口的核心。

前端所有远程图片（TMDB 海报、豆瓣剧照、PT 站种子详情图……）都经
/images/proxy 走到这里：首次访问由 ImageProxy 回源抓取并落盘，之后同一
URL 的访问直接读本地文件，不再消耗外网流量，也不受图床限速/失联影响。

设计要点：
- 缓存目录默认 data/cache/images，与 SQLite、uploads 同在 data/ 下，
  Docker 部署只需挂载 data 一个卷即可整体持久化（丢了也只是重新回源）。
- 缓存键 = sha256(原始 URL)。URL 含域名，不同图床的同名路径天然不冲突；
  按哈希前两位分片子目录，避免单目录文件数过大拖慢文件系统。
- 每个条目两个文件：<hash>（图片字节）和 <hash>.json（源 URL、Content-Type、
  抓取时间，便于排查"这张缓存是哪来的"）。以 .json 的存在作为条目有效的
  标志；写入先落临时文件再原子 rename（先内容后元数据），进程中途崩溃
  不会留下"看似有效实则残缺"的条目。
- 同一 URL 的并发请求 singleflight 去重，只有一个真正回源，其余等它落盘。
- 容量控制：写入量累计到一档阈值后同步触发一次清理，按 mtime 从最旧的
  条目开始删除，降到上限的 90% 为止；命中时刷新内容文件 mtime，等效 LRU。
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import logging
import os
import time
from dataclasses import dataclass
from pathlib import Path

from movieclaw_api.core.config import get_settings
from movieclaw_api.services.image_proxy import ImageProxy, get_image_proxy

logger = logging.getLogger("movieclaw_api.image_cache")


@dataclass(frozen=True)
class CachedImage:
    """一条已落盘的缓存：路由层用 FileResponse 直接把 path 发回浏览器。"""

    path: Path
    content_type: str


class ImageCache:
    """按 URL 哈希落盘的图片缓存，回源由 ImageProxy 完成。"""

    def __init__(self, cache_dir: Path, proxy: ImageProxy, *, max_bytes: int) -> None:
        self._dir = cache_dir
        self._proxy = proxy
        self._max_bytes = max_bytes
        # 每写入约 1/10 容量（上限 64MB）检查一次总量，避免每次写入都全目录扫描
        self._purge_interval = max(1, min(64 * 1024 * 1024, max_bytes // 10))
        self._bytes_since_purge = 0
        self._inflight: dict[str, asyncio.Task[CachedImage]] = {}

    def _entry_paths(self, url: str) -> tuple[Path, Path]:
        """URL -> (内容文件, 元数据文件)。sha256 全量 URL 参与哈希，域名不同则键必不同。"""
        digest = hashlib.sha256(url.encode("utf-8")).hexdigest()
        content = self._dir / digest[:2] / digest
        return content, content.with_suffix(".json")

    # ---- 磁盘操作（均为同步函数，调用方用 asyncio.to_thread 包装） ----------

    @staticmethod
    def _read_hit(content_path: Path, meta_path: Path) -> str | None:
        """命中则返回 Content-Type 并刷新 mtime（供 LRU 排序），未命中返回 None。"""
        try:
            meta = json.loads(meta_path.read_text(encoding="utf-8"))
            content_type = str(meta["content_type"])
            if not content_path.is_file():
                return None
            os.utime(content_path)
        except (OSError, ValueError, KeyError):
            return None
        return content_type

    @staticmethod
    def _atomic_write(path: Path, data: bytes) -> None:
        tmp = path.with_name(path.name + ".tmp")
        tmp.write_bytes(data)
        os.replace(tmp, path)

    def _store(self, url: str, content_path: Path, meta_path: Path, data: bytes, ct: str) -> None:
        content_path.parent.mkdir(parents=True, exist_ok=True)
        # 先写内容、后写元数据：元数据文件的出现即"条目已完整"的提交点
        self._atomic_write(content_path, data)
        meta = {"url": url, "content_type": ct, "fetched_at": int(time.time())}
        self._atomic_write(meta_path, json.dumps(meta, ensure_ascii=False).encode("utf-8"))

    def _purge_if_over_limit(self) -> None:
        """总量超上限时按 mtime 淘汰最旧条目，直到降至上限的 90%。"""
        entries: list[tuple[float, int, Path]] = []  # (mtime, 条目总字节, 内容文件路径)
        total = 0
        for meta_path in self._dir.glob("*/*.json"):
            content_path = meta_path.with_suffix("")
            try:
                stat = content_path.stat()
                size = stat.st_size + meta_path.stat().st_size
            except OSError:
                continue
            entries.append((stat.st_mtime, size, content_path))
            total += size
        if total <= self._max_bytes:
            return
        entries.sort()
        target = int(self._max_bytes * 0.9)
        removed = 0
        for _mtime, size, content_path in entries:
            if total <= target:
                break
            # 先删元数据（撤销"有效"标志），再删内容，保证残留状态永远可识别
            meta_path = content_path.with_suffix(".json")
            meta_path.unlink(missing_ok=True)
            content_path.unlink(missing_ok=True)
            total -= size
            removed += 1
        logger.info("图片缓存超过容量上限，已清理最久未访问的 %d 个条目", removed)

    # ---- 对外接口 -----------------------------------------------------------

    async def get_or_fetch(self, url: str) -> CachedImage:
        """命中直接返回本地文件；未命中回源下载并落盘（同 URL 并发只回源一次）。"""
        content_path, meta_path = self._entry_paths(url)
        content_type = await asyncio.to_thread(self._read_hit, content_path, meta_path)
        if content_type is not None:
            return CachedImage(content_path, content_type)

        key = content_path.name
        task = self._inflight.get(key)
        if task is None:
            task = asyncio.create_task(self._fetch_and_store(url, content_path, meta_path))
            self._inflight[key] = task
            task.add_done_callback(lambda _t: self._inflight.pop(key, None))
        # shield：浏览器中途取消某个 <img> 请求时，不连累共享同一下载的其他请求
        return await asyncio.shield(task)

    async def _fetch_and_store(self, url: str, content_path: Path, meta_path: Path) -> CachedImage:
        data, content_type = await self._proxy.fetch(url)
        await asyncio.to_thread(self._store, url, content_path, meta_path, data, content_type)
        self._bytes_since_purge += len(data)
        if self._bytes_since_purge >= self._purge_interval:
            self._bytes_since_purge = 0
            await asyncio.to_thread(self._purge_if_over_limit)
        return CachedImage(content_path, content_type)


_cache: ImageCache | None = None


def get_image_cache() -> ImageCache:
    """取得进程级缓存单例（目录与容量来自配置，回源走共享的 ImageProxy）。"""
    global _cache
    if _cache is None:
        settings = get_settings()
        _cache = ImageCache(
            Path(settings.image_cache_dir),
            get_image_proxy(),
            max_bytes=settings.image_cache_max_mb * 1024 * 1024,
        )
    return _cache


def reset_image_cache() -> None:
    """仅供测试清理单例。"""
    global _cache
    _cache = None
