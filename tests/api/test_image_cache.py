"""图片磁盘缓存测试：命中/回源、哈希分片、并发去重、容量清理、路由集成。"""

from __future__ import annotations

import asyncio
import hashlib
import json
import os
from pathlib import Path

import httpx
import pytest
from fastapi.testclient import TestClient

from movieclaw_api.core.config import get_settings
from movieclaw_api.services import image_proxy as image_proxy_module
from movieclaw_api.services.auth import reset_auth_state
from movieclaw_api.services.image_cache import ImageCache, reset_image_cache
from movieclaw_api.services.image_proxy import ImageProxy
from movieclaw_api.settings.store import reset_setting_store
from movieclaw_db.crypto import reset_secret_box


async def _fake_resolver(_host: str) -> list[str]:
    return ["93.184.216.34"]


def _make_cache(tmp_path: Path, handler, *, max_bytes: int = 10 * 1024 * 1024) -> ImageCache:
    proxy = ImageProxy(transport=httpx.MockTransport(handler), resolver=_fake_resolver)
    return ImageCache(tmp_path / "images", proxy, max_bytes=max_bytes)


async def test_miss_fetches_and_hit_reads_local(tmp_path: Path) -> None:
    calls = 0

    def handler(_request: httpx.Request) -> httpx.Response:
        nonlocal calls
        calls += 1
        return httpx.Response(200, headers={"Content-Type": "image/jpeg"}, content=b"jpeg-bytes")

    cache = _make_cache(tmp_path, handler)
    url = "https://img.host-a.com/poster.jpg"

    first = await cache.get_or_fetch(url)
    assert first.content_type == "image/jpeg"
    assert first.path.read_bytes() == b"jpeg-bytes"
    # 哈希分片：内容文件位于哈希前两位的子目录，旁边有记录来源的元数据文件
    digest = hashlib.sha256(url.encode()).hexdigest()
    assert first.path == tmp_path / "images" / digest[:2] / digest
    meta = json.loads(first.path.with_suffix(".json").read_text(encoding="utf-8"))
    assert meta["url"] == url
    assert meta["content_type"] == "image/jpeg"

    second = await cache.get_or_fetch(url)
    assert second.path == first.path
    assert calls == 1, "第二次访问应命中本地缓存，不再回源"


async def test_same_path_on_different_hosts_do_not_collide(tmp_path: Path) -> None:
    """域名参与哈希：不同图床上的同名路径必须是两个独立的缓存条目。"""

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200, headers={"Content-Type": "image/png"}, content=request.url.host.encode()
        )

    cache = _make_cache(tmp_path, handler)
    a = await cache.get_or_fetch("https://img.host-a.com/x/poster.png")
    b = await cache.get_or_fetch("https://img.host-b.com/x/poster.png")
    assert a.path != b.path
    assert a.path.read_bytes() == b"img.host-a.com"
    assert b.path.read_bytes() == b"img.host-b.com"


async def test_concurrent_requests_fetch_once(tmp_path: Path) -> None:
    calls = 0
    release = asyncio.Event()

    async def handler(_request: httpx.Request) -> httpx.Response:
        nonlocal calls
        calls += 1
        await release.wait()
        return httpx.Response(200, headers={"Content-Type": "image/webp"}, content=b"webp")

    cache = _make_cache(tmp_path, handler)
    url = "https://img.host-a.com/hot.webp"
    tasks = [asyncio.create_task(cache.get_or_fetch(url)) for _ in range(5)]
    await asyncio.sleep(0.01)  # 让 5 个请求都进入等待
    release.set()
    results = await asyncio.gather(*tasks)
    assert calls == 1, "同一 URL 的并发请求应只回源一次"
    assert all(r.path == results[0].path for r in results)


async def test_purge_evicts_least_recently_used(tmp_path: Path) -> None:
    def handler(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, headers={"Content-Type": "image/png"}, content=b"x" * 100)

    # 上限很小：写入若干条后触发清理，最旧的条目被淘汰
    cache = _make_cache(tmp_path, handler, max_bytes=500)
    paths = []
    for i in range(6):
        cached = await cache.get_or_fetch(f"https://img.host-a.com/{i}.png")
        paths.append(cached.path)
        # 拉开 mtime，保证淘汰顺序稳定可断言
        os.utime(cached.path, (i, i))

    cache._purge_if_over_limit()
    survivors = [p for p in paths if p.exists()]
    assert not paths[0].exists(), "最久未访问的条目应最先被淘汰"
    assert not paths[0].with_suffix(".json").exists(), "元数据应与内容一并删除"
    total = sum(p.stat().st_size for p in survivors)
    assert total <= 500 * 0.9, "清理后总量应降到上限的 90% 以内"

    # 被淘汰的条目再次访问：重新回源，缓存自动恢复
    again = await cache.get_or_fetch("https://img.host-a.com/0.png")
    assert again.path.read_bytes() == b"x" * 100


# ---------------------------------------------------------------------------
# 路由集成：登录 → /images/proxy → 缓存落盘 → FileResponse + 长缓存头
# ---------------------------------------------------------------------------


@pytest.fixture
def client(tmp_path: Path, monkeypatch):
    monkeypatch.setenv("DATABASE_URL", f"sqlite+aiosqlite:///{tmp_path / 'test.db'}")
    monkeypatch.setenv("SECRET_KEY_FILE", str(tmp_path / ".secret_key"))
    monkeypatch.setenv("SCHEDULER_ENABLED", "false")
    monkeypatch.setenv("IMAGE_CACHE_DIR", str(tmp_path / "img-cache"))
    get_settings.cache_clear()
    reset_setting_store()
    reset_secret_box()
    reset_auth_state()
    reset_image_cache()
    # 把共享代理单例替换成 Mock 传输 + 静态 DNS，用例不出网
    monkeypatch.setattr(
        image_proxy_module,
        "_proxy",
        ImageProxy(
            transport=httpx.MockTransport(
                lambda _request: httpx.Response(
                    200, headers={"Content-Type": "image/jpeg"}, content=b"route-jpeg"
                )
            ),
            resolver=_fake_resolver,
        ),
    )

    from movieclaw_api.app import create_app

    app = create_app()
    with TestClient(app) as c:
        c.post("/api/v1/auth/bootstrap", json={"username": "admin", "password": "s3cret-pass"})
        yield c

    reset_image_cache()
    reset_setting_store()
    reset_secret_box()
    reset_auth_state()
    get_settings.cache_clear()


def test_proxy_route_serves_cached_image(client: TestClient, tmp_path: Path) -> None:
    url = "https://img.host-a.com/poster.jpg"
    resp = client.get("/api/v1/images/proxy", params={"url": url})
    assert resp.status_code == 200
    assert resp.content == b"route-jpeg"
    assert resp.headers["content-type"] == "image/jpeg"
    assert "immutable" in resp.headers["cache-control"]
    # 已按 URL 哈希落盘到配置的缓存目录
    digest = hashlib.sha256(url.encode()).hexdigest()
    assert (tmp_path / "img-cache" / digest[:2] / digest).is_file()
    # 二次访问命中缓存，同样成功
    assert client.get("/api/v1/images/proxy", params={"url": url}).status_code == 200
