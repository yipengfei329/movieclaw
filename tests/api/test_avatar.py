"""管理员头像接口的端到端测试。

覆盖：未上传时会话视图无头像且读取 404、上传后 avatar_url 带版本号且可原样
读回、再次上传替换旧文件（含扩展名变化）、以及对非图片 / SVG / 空文件 /
超大文件的拒绝。头像存储目录用临时目录隔离，不污染真实的 data/uploads。
"""
from __future__ import annotations

import base64
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from movieclaw_api.core.config import get_settings
from movieclaw_api.settings.store import reset_setting_store
from movieclaw_db.crypto import reset_secret_box

# 一张 1x1 的合法 PNG（透明像素），用于模拟上传的真实图片字节。
_PNG_1X1 = base64.b64decode(
    "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAAC0lEQVR42mNkYPhfDwAChwGA60e6kgAAAABJRU5ErkJggg=="
)

_BASE = "/api/v1/auth/avatar"


@pytest.fixture
def media_dir(tmp_path: Path) -> Path:
    return tmp_path / "uploads"


@pytest.fixture
def client(tmp_path: Path, media_dir: Path, monkeypatch):
    # 每个用例独立临时库 / 密钥 / 媒体目录，彻底隔离
    monkeypatch.setenv("DATABASE_URL", f"sqlite+aiosqlite:///{tmp_path / 'test.db'}")
    monkeypatch.setenv("SECRET_KEY_FILE", str(tmp_path / ".secret_key"))
    monkeypatch.setenv("MEDIA_DIR", str(media_dir))
    monkeypatch.setenv("SCHEDULER_ENABLED", "false")
    get_settings.cache_clear()
    reset_setting_store()
    reset_secret_box()

    from movieclaw_api.api.deps import require_login
    from movieclaw_api.app import create_app

    app = create_app()
    # 头像上传/读取需要管理员登录，这里用依赖覆盖绕过（鉴权本身在 test_auth 覆盖）
    app.dependency_overrides[require_login] = lambda: "tester"
    with TestClient(app) as c:
        yield c

    reset_setting_store()
    reset_secret_box()
    get_settings.cache_clear()


def _upload(client: TestClient, name: str = "avatar.png", content_type: str = "image/png"):
    return client.post(_BASE, files={"file": (name, _PNG_1X1, content_type)})


def test_no_avatar_by_default(client: TestClient):
    """未上传过头像：会话视图 avatar_url 为空，读取头像文件 404。"""
    me = client.get("/api/v1/auth/me")
    assert me.status_code == 200
    assert me.json()["data"]["avatar_url"] is None
    assert client.get(_BASE).status_code == 404


def test_upload_and_read_back(client: TestClient, media_dir: Path):
    """上传：avatar_url 带版本号、文件落盘到媒体目录、可原样读回。"""
    resp = _upload(client)
    assert resp.status_code == 200
    avatar_url = resp.json()["data"]["avatar_url"]
    assert avatar_url is not None and avatar_url.startswith(f"{_BASE}?v=")

    saved = media_dir / "avatar.png"
    assert saved.is_file()
    assert saved.read_bytes() == _PNG_1X1

    img = client.get(_BASE)
    assert img.status_code == 200
    assert img.content == _PNG_1X1
    assert img.headers["content-type"] == "image/png"

    # /auth/me 也返回同一 avatar_url
    assert client.get("/api/v1/auth/me").json()["data"]["avatar_url"] == avatar_url


def test_reupload_replaces_old_file(client: TestClient, media_dir: Path):
    """再次上传（换扩展名）：旧文件被删除，单槽位只留最新一张。"""
    _upload(client, "a.png", "image/png")
    resp = _upload(client, "b.jpg", "image/jpeg")
    assert resp.status_code == 200

    assert not (media_dir / "avatar.png").exists()
    assert (media_dir / "avatar.jpg").is_file()
    assert client.get(_BASE).headers["content-type"] == "image/jpeg"


def test_reject_bad_uploads(client: TestClient):
    """拒绝非图片、SVG（可内嵌脚本）、空文件与超大文件。"""
    assert (
        client.post(_BASE, files={"file": ("x.txt", b"hi", "text/plain")}).status_code
        == 400
    )
    svg = b'<svg xmlns="http://www.w3.org/2000/svg"></svg>'
    assert (
        client.post(_BASE, files={"file": ("x.svg", svg, "image/svg+xml")}).status_code
        == 400
    )
    assert (
        client.post(_BASE, files={"file": ("x.png", b"", "image/png")}).status_code
        == 400
    )

    from movieclaw_api.services import avatar as avatar_media

    big = b"x" * (avatar_media.MAX_AVATAR_BYTES + 1)
    resp = client.post(_BASE, files={"file": ("big.png", big, "image/png")})
    assert resp.status_code == 400
    assert "过大" in resp.json()["message"]
