"""目录浏览接口（/fs/browse）的端到端测试。

覆盖：只列子目录（文件/隐藏目录不出现）、按名称排序、parent 计算、
根目录无上级、相对路径与不存在路径的 400 拒绝。
"""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from movieclaw_api.core.config import get_settings


@pytest.fixture
def client(tmp_path, monkeypatch):
    monkeypatch.setenv("DATABASE_URL", f"sqlite+aiosqlite:///{tmp_path / 'test.db'}")
    monkeypatch.setenv("SECRET_KEY_FILE", str(tmp_path / ".secret_key"))
    monkeypatch.setenv("LIBRARY_DEFAULT_ROOT", str(tmp_path / "library"))
    get_settings.cache_clear()

    from movieclaw_api.api.deps import require_login
    from movieclaw_api.app import create_app

    app = create_app()
    # 本文件只测目录浏览，登录鉴权用依赖覆盖绕过（鉴权本身在 test_auth 覆盖）
    app.dependency_overrides[require_login] = lambda: "tester"
    with TestClient(app) as c:
        yield c
    get_settings.cache_clear()


def test_browse_lists_only_visible_dirs_sorted(client, tmp_path) -> None:
    root = tmp_path / "media"
    (root / "Movies").mkdir(parents=True)
    (root / "anime").mkdir()
    (root / ".hidden").mkdir()
    (root / "note.txt").write_text("x")

    data = client.get("/api/v1/fs/browse", params={"path": str(root)}).json()["data"]
    assert data["path"] == str(root)
    assert data["parent"] == str(tmp_path)
    # 只有可见目录，按名称（忽略大小写）排序；文件与隐藏目录不出现
    assert [e["name"] for e in data["entries"]] == ["anime", "Movies"]
    assert data["entries"][1]["path"] == str(root / "Movies")


def test_browse_normalizes_double_leading_slash(client, tmp_path) -> None:
    # POSIX normpath 保留 "//" 前缀，接口必须归一化成单斜杠
    data = client.get("/api/v1/fs/browse", params={"path": f"/{tmp_path}"}).json()["data"]
    assert data["path"] == str(tmp_path)


def test_browse_defaults_to_filesystem_root(client) -> None:
    data = client.get("/api/v1/fs/browse").json()["data"]
    assert data["path"] == "/"
    assert data["parent"] is None


def test_browse_rejects_bad_paths(client, tmp_path) -> None:
    resp = client.get("/api/v1/fs/browse", params={"path": "relative/path"})
    assert resp.status_code == 400

    resp = client.get("/api/v1/fs/browse", params={"path": str(tmp_path / "missing")})
    assert resp.status_code == 400
    assert "不存在" in resp.json()["message"]

    file_path = tmp_path / "a.txt"
    file_path.write_text("x")
    resp = client.get("/api/v1/fs/browse", params={"path": str(file_path)})
    assert resp.status_code == 400
    assert "不是目录" in resp.json()["message"]
