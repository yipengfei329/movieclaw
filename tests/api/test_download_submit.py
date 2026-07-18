"""手动提交下载接口（POST /downloaders/submit）的端到端测试。

覆盖：成功提交（保存目录/标签/种子字节正确传递）、幂等重复提交、
无默认可用下载器、站点不可用、站点取种失败、下载器提交失败、参数校验。
站点访问与下载器适配器均为假实现，不发真实网络请求。
"""
from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

import movieclaw_api.services.downloader_config as downloader_config_service
import movieclaw_api.services.torrent_submit as torrent_submit_service
from movieclaw_api.core.config import get_settings
from movieclaw_api.services.site_access import SiteUnavailableError
from movieclaw_downloader import DownloaderInfo
from movieclaw_downloader.models import DownloadRequest, SubmitResult

# 每次提交收到的 DownloadRequest，供断言"种子字节/目录/标签传对了"
_captured_requests: list[DownloadRequest] = []


class _FakeSite:
    """假站点客户端：把 download_url 原样编码成"种子字节"，便于断言透传。"""

    async def download_torrent(self, url: str) -> bytes:
        if "sitefail" in url:
            raise RuntimeError("站点返回 500")
        return url.encode()


class _FakeSiteAccess:
    """假站点访问管理器：按 site_id 决定可用与否。"""

    async def get(self, site_id: str):
        if site_id == "nosite":
            raise SiteUnavailableError(f"站点未配置：{site_id}")
        return _FakeSite()


class _FakeDownloader:
    """假下载器适配器：按种子字节内容决定提交结果。"""

    def __init__(self, config) -> None:
        self.config = config

    async def test_connection(self) -> DownloaderInfo:
        return DownloaderInfo(type=self.config.type, version="v5.0.2")

    async def submit(self, request: DownloadRequest) -> SubmitResult:
        _captured_requests.append(request)
        assert request.torrent_bytes is not None
        if b"clientfail" in request.torrent_bytes:
            raise RuntimeError("下载器拒绝了请求")
        return SubmitResult(
            info_hash="a" * 40,
            name="Some.Movie.2024.2160p",
            already_exists=b"dup" in request.torrent_bytes,
        )

    async def close(self) -> None:
        pass


@pytest.fixture
def client(tmp_path, monkeypatch):
    db_file = tmp_path / "test.db"
    monkeypatch.setenv("DATABASE_URL", f"sqlite+aiosqlite:///{db_file}")
    monkeypatch.setenv("SECRET_KEY_FILE", str(tmp_path / ".secret_key"))
    get_settings.cache_clear()

    _captured_requests.clear()
    # 配置侧（连接测试）与提交侧共用同一个假适配器
    monkeypatch.setattr(downloader_config_service, "create_downloader", _FakeDownloader)
    monkeypatch.setattr(torrent_submit_service, "create_downloader", _FakeDownloader)
    monkeypatch.setattr(torrent_submit_service, "get_site_access", lambda: _FakeSiteAccess())

    from movieclaw_api.api.deps import require_login
    from movieclaw_api.app import create_app

    app = create_app()
    app.dependency_overrides[require_login] = lambda: "tester"
    with TestClient(app) as c:
        yield c
    get_settings.cache_clear()


def _add_default_downloader(c: TestClient, **overrides) -> int:
    """建一台下载器（第一台自动成为默认），返回 id。"""
    payload = {
        "name": overrides.pop("name", "家里的 qBittorrent"),
        "client_type": "qbittorrent",
        "url": "http://192.168.1.10:8080",
        "save_path": "/downloads/movies",
        **overrides,
    }
    r = c.post("/api/v1/downloaders", json=payload)
    assert r.status_code == 200
    return r.json()["data"]["id"]


_SUBMIT = {"site_id": "mteam", "download_url": "https://example.org/download.php?id=1"}


def test_submit_success(client) -> None:
    _add_default_downloader(client)
    r = client.post("/api/v1/downloaders/submit", json=_SUBMIT)
    assert r.status_code == 200
    body = r.json()
    assert "已提交到" in body["message"]
    data = body["data"]
    assert data["info_hash"] == "a" * 40
    assert data["already_exists"] is False
    assert data["downloader_name"] == "家里的 qBittorrent"
    assert data["save_path"] == "/downloads/movies"

    # 种子字节 = 站点按 download_url 取回的内容；目录/分类/标签按约定传递
    req = _captured_requests[-1]
    assert req.torrent_bytes == _SUBMIT["download_url"].encode()
    assert req.save_path == "/downloads/movies"
    assert req.category == "movieclaw"
    assert req.tags == ["movieclaw-manual"]


def test_submit_duplicate_is_idempotent(client) -> None:
    _add_default_downloader(client)
    r = client.post(
        "/api/v1/downloaders/submit",
        json={**_SUBMIT, "download_url": "https://example.org/dup.torrent"},
    )
    assert r.status_code == 200
    assert r.json()["data"]["already_exists"] is True
    assert "已在下载器中" in r.json()["message"]


def test_submit_without_any_downloader(client) -> None:
    r = client.post("/api/v1/downloaders/submit", json=_SUBMIT)
    assert r.status_code == 400
    assert "默认下载器" in r.json()["message"]


def test_submit_with_disabled_default_downloader(client) -> None:
    did = _add_default_downloader(client)
    client.patch(f"/api/v1/downloaders/{did}/status", json={"enabled": False})
    r = client.post("/api/v1/downloaders/submit", json=_SUBMIT)
    assert r.status_code == 400
    assert "默认下载器" in r.json()["message"]


def test_submit_site_unavailable(client) -> None:
    _add_default_downloader(client)
    r = client.post("/api/v1/downloaders/submit", json={**_SUBMIT, "site_id": "nosite"})
    assert r.status_code == 400
    assert "站点未配置" in r.json()["message"]


def test_submit_site_download_failed(client) -> None:
    _add_default_downloader(client)
    r = client.post(
        "/api/v1/downloaders/submit",
        json={**_SUBMIT, "download_url": "https://example.org/sitefail.torrent"},
    )
    assert r.status_code == 502
    assert "取回种子失败" in r.json()["message"]


def test_submit_downloader_rejected(client) -> None:
    _add_default_downloader(client)
    r = client.post(
        "/api/v1/downloaders/submit",
        json={**_SUBMIT, "download_url": "https://example.org/clientfail.torrent"},
    )
    assert r.status_code == 502
    assert "提交到下载器" in r.json()["message"]


def test_submit_validation(client) -> None:
    assert client.post("/api/v1/downloaders/submit", json={"site_id": "mteam"}).status_code == 422
    assert (
        client.post(
            "/api/v1/downloaders/submit", json={"site_id": "", "download_url": "x"}
        ).status_code
        == 422
    )
