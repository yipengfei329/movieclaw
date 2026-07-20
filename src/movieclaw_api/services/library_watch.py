"""媒体库实时监控（L4）：watchdog 文件事件 → 去抖批处理 → 增量扫描。

设计（吸收 moviebot 的三个实现细节，见设计文档第 1 节核实备注）：
- **事件只进队列**：watchdog 的回调跑在它自己的观察者线程里，绝不做任何
  IO/识别，只把"哪个库有动静"投进 asyncio 队列（线程安全经
  call_soon_threadsafe 桥接）；
- **去抖批处理**：下载器/整理器落盘会在短时间产生大量事件，消费者收到
  首个事件后等待安静窗口（3s，最长 30s 兜底）再触发一次增量扫描——
  scan_library 本身对已知路径秒过，扫描即是最好的"批处理"；
- **写入完成检测**：不追踪单文件的写入进度（moviebot 在事件线程里
  sleep 轮询是反面教训）——去抖窗口天然给了写入落定的时间，且增量扫描
  遇到仍在写入的文件下轮对账会再补。

生命周期：应用启动时 start（库根路径可能不存在则跳过并告警），库增删改
后调用 ``refresh_watches`` 重建监听；关闭时 stop。watchdog 缺失或平台
不支持时优雅降级——只靠 6 小时对账任务兜底，功能不缺失只是不实时。
"""

from __future__ import annotations

import asyncio
import logging
from pathlib import Path

logger = logging.getLogger("movieclaw_api.library_watch")

# 去抖参数：首个事件后等安静 3 秒；持续有事件时最长 30 秒必触发一次
_QUIET_SECONDS = 3.0
_MAX_WAIT_SECONDS = 30.0


class LibraryWatcher:
    """库根路径的文件事件监听器（进程级单例，见 init_library_watcher）。"""

    def __init__(self) -> None:
        self._observer = None
        self._queue: asyncio.Queue[int] = asyncio.Queue()
        self._loop: asyncio.AbstractEventLoop | None = None
        self._consumer: asyncio.Task | None = None
        self._available = True

    # -- 生命周期 ----------------------------------------------------------

    async def start(self) -> None:
        self._loop = asyncio.get_running_loop()
        self._consumer = asyncio.create_task(self._consume())
        await self.refresh_watches()

    async def stop(self) -> None:
        if self._consumer is not None:
            self._consumer.cancel()
            self._consumer = None
        self._stop_observer()

    def _stop_observer(self) -> None:
        if self._observer is not None:
            self._observer.stop()
            self._observer.join(timeout=5)
            self._observer = None

    async def refresh_watches(self) -> None:
        """按当前库配置重建监听（库增删改根路径后调用）。"""
        if not self._available:
            return
        try:
            from watchdog.events import FileSystemEventHandler
            from watchdog.observers import Observer
        except ImportError:
            self._available = False
            logger.warning("未安装 watchdog，媒体库实时监控不可用——仅靠定期对账发现新文件")
            return

        from sqlmodel import select

        from movieclaw_db.engine import get_database
        from movieclaw_db.models import Library

        watcher = self

        class _Handler(FileSystemEventHandler):
            """事件回调（观察者线程）：只投递库 id，不做任何业务。"""

            def __init__(self, library_id: int) -> None:
                self._library_id = library_id

            def on_any_event(self, event) -> None:  # noqa: ANN001
                if event.is_directory and event.event_type not in ("moved", "deleted"):
                    return
                watcher._enqueue_threadsafe(self._library_id)

        db = get_database()
        async with db.session() as session:
            libraries = list((await session.execute(select(Library))).scalars().all())

        self._stop_observer()
        observer = Observer()
        watched = 0
        for library in libraries:
            assert library.id is not None
            for root in library.root_paths:
                path = Path(root)
                if not path.is_dir():
                    continue  # 根路径未就绪：不告警刷屏，对账任务会持续兜底
                try:
                    observer.schedule(_Handler(library.id), str(path), recursive=True)
                    watched += 1
                except OSError as exc:
                    logger.warning("监听根路径失败（%s）：%s", root, exc)
        if watched:
            observer.daemon = True
            observer.start()
            self._observer = observer
            logger.info("媒体库实时监控已启动：监听 %d 个根路径", watched)
        else:
            logger.info("没有可监听的库根路径，实时监控待命（对账任务兜底）")

    # -- 事件通道 ----------------------------------------------------------

    def _enqueue_threadsafe(self, library_id: int) -> None:
        loop = self._loop
        if loop is None or loop.is_closed():
            return
        loop.call_soon_threadsafe(self._queue.put_nowait, library_id)

    async def _consume(self) -> None:
        """去抖消费：首事件后等安静窗口，汇总本批涉及的库做增量扫描。"""
        from movieclaw_api.services.library_scan import is_scanning, scan_library

        while True:
            first = await self._queue.get()
            pending = {first}
            deadline = asyncio.get_running_loop().time() + _MAX_WAIT_SECONDS
            while True:
                remaining = deadline - asyncio.get_running_loop().time()
                timeout = min(_QUIET_SECONDS, max(remaining, 0))
                try:
                    pending.add(await asyncio.wait_for(self._queue.get(), timeout))
                except TimeoutError:
                    break  # 安静窗口达成
                if asyncio.get_running_loop().time() >= deadline:
                    break  # 兜底：持续有事件也要触发
            for library_id in sorted(pending):
                if is_scanning(library_id):
                    continue  # 扫描中产生的事件（自己写台账不产生文件事件，
                    # 但入库硬链会）——正在扫就不叠加
                logger.info("检测到媒体库 #%s 根路径变更，触发增量扫描", library_id)
                try:
                    await scan_library(library_id)
                except Exception:  # noqa: BLE001 -- 监控消费绝不崩
                    logger.exception("实时监控触发的扫描失败：库 #%s", library_id)


_watcher: LibraryWatcher | None = None


def get_library_watcher() -> LibraryWatcher | None:
    return _watcher


async def init_library_watcher() -> None:
    """启动进程级监听单例（lifespan 调用）。"""
    global _watcher
    _watcher = LibraryWatcher()
    await _watcher.start()


async def close_library_watcher() -> None:
    global _watcher
    if _watcher is not None:
        await _watcher.stop()
        _watcher = None
