"""外观设置（首页背景图库）接口的端到端测试。

覆盖：空库默认无自定义背景、上传入库并设为生效、多张累积不覆盖、点选切换、
切回默认不删图、删除非生效图 / 生效图（回退默认）、旧版单槽位文件自动迁移、
图库张数上限、以及对非图片 / 空文件 / 超大文件的拒绝。图片存储目录用临时目录
隔离，保证用例之间互不影响、也不污染真实的 data/uploads。
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

# 接口统一挂在 /api/v1 前缀下（见 create_app）。
_BASE = "/api/v1/appearance"


@pytest.fixture
def media_dir(tmp_path: Path) -> Path:
    return tmp_path / "uploads"


@pytest.fixture
def client(tmp_path: Path, media_dir: Path, monkeypatch):
    # 每个用例独立临时库 / 密钥 / 媒体目录，彻底隔离
    monkeypatch.setenv("DATABASE_URL", f"sqlite+aiosqlite:///{tmp_path / 'test.db'}")
    monkeypatch.setenv("SECRET_KEY_FILE", str(tmp_path / ".secret_key"))
    monkeypatch.setenv("MEDIA_DIR", str(media_dir))
    # 本套测试不涉及定时任务，关掉调度器避免其进程内单例跨用例残留
    monkeypatch.setenv("SCHEDULER_ENABLED", "false")
    get_settings.cache_clear()
    reset_setting_store()
    reset_secret_box()

    from movieclaw_api.api.deps import require_login
    from movieclaw_api.app import create_app

    app = create_app()
    # 背景图上传/删除需要管理员登录，这里用依赖覆盖绕过（鉴权本身在 test_auth 覆盖）
    app.dependency_overrides[require_login] = lambda: "tester"
    with TestClient(app) as c:
        yield c

    reset_setting_store()
    reset_secret_box()
    get_settings.cache_clear()


def _upload(client: TestClient, name: str = "bg.png", content_type: str = "image/png"):
    return client.post(
        f"{_BASE}/backdrops", files={"file": (name, _PNG_1X1, content_type)}
    )


def test_default_is_empty_gallery(client: TestClient):
    """空库首启：图库为空、无生效图（前端回退内置默认背景）。"""
    resp = client.get(_BASE)
    assert resp.status_code == 200
    data = resp.json()["data"]
    assert data["active_id"] is None
    assert data["active_url"] is None
    assert data["backdrops"] == []


def test_upload_adds_to_gallery_and_activates(client: TestClient, media_dir: Path):
    """上传：图片入库、成为生效图、URL 带版本号且可原样读回。"""
    resp = _upload(client)
    assert resp.status_code == 200
    data = resp.json()["data"]
    assert len(data["backdrops"]) == 1
    item = data["backdrops"][0]
    assert data["active_id"] == item["id"]
    assert data["active_url"] == item["url"]
    assert item["url"].startswith(f"{_BASE}/backdrops/{item['id']}?v=")

    # 落盘到隔离的媒体目录的图库子目录
    saved = media_dir / "backdrops" / f"{item['id']}.png"
    assert saved.is_file()
    assert saved.read_bytes() == _PNG_1X1

    # 读回图片本体：字节一致、Content-Type 正确
    img = client.get(f"{_BASE}/backdrops/{item['id']}")
    assert img.status_code == 200
    assert img.content == _PNG_1X1
    assert img.headers["content-type"] == "image/png"


def test_uploads_accumulate_without_overwrite(client: TestClient):
    """多次上传：旧图全部保留，新图追加到末尾并成为生效图。"""
    first = _upload(client, "a.png").json()["data"]["backdrops"][0]
    data = _upload(client, "b.jpg", "image/jpeg").json()["data"]

    assert len(data["backdrops"]) == 2
    # 上传时间升序：先传的在前
    assert data["backdrops"][0]["id"] == first["id"]
    assert data["active_id"] == data["backdrops"][1]["id"]
    # 旧图仍可读取
    assert client.get(f"{_BASE}/backdrops/{first['id']}").status_code == 200


def test_switch_active_between_gallery_and_default(client: TestClient):
    """点选切换：图库任意一张 ↔ 内置默认，切回默认不删除任何图。"""
    first_id = _upload(client).json()["data"]["backdrops"][0]["id"]
    _upload(client)

    # 切回第一张
    resp = client.put(f"{_BASE}/active", json={"backdrop_id": first_id})
    assert resp.status_code == 200
    assert resp.json()["data"]["active_id"] == first_id

    # 切回内置默认：生效图清空，但图库两张原样保留
    resp = client.put(f"{_BASE}/active", json={"backdrop_id": None})
    data = resp.json()["data"]
    assert data["active_id"] is None
    assert len(data["backdrops"]) == 2

    # 切换到不存在的 id → 404
    assert (
        client.put(f"{_BASE}/active", json={"backdrop_id": "f" * 32}).status_code == 404
    )


def test_delete_inactive_keeps_active(client: TestClient):
    """删除非生效图：生效图不受影响。"""
    first_id = _upload(client).json()["data"]["backdrops"][0]["id"]
    second_id = _upload(client).json()["data"]["active_id"]

    resp = client.delete(f"{_BASE}/backdrops/{first_id}")
    assert resp.status_code == 200
    data = resp.json()["data"]
    assert data["active_id"] == second_id
    assert [item["id"] for item in data["backdrops"]] == [second_id]
    # 被删的图本体转为 404
    assert client.get(f"{_BASE}/backdrops/{first_id}").status_code == 404


def test_delete_active_falls_back_to_default(client: TestClient):
    """删除当前生效图：自动回退内置默认，其余图保留。"""
    first_id = _upload(client).json()["data"]["backdrops"][0]["id"]
    active_id = _upload(client).json()["data"]["active_id"]

    data = client.delete(f"{_BASE}/backdrops/{active_id}").json()["data"]
    assert data["active_id"] is None
    assert [item["id"] for item in data["backdrops"]] == [first_id]

    # 删除不存在的 id → 404
    assert client.delete(f"{_BASE}/backdrops/{'f' * 32}").status_code == 404


def test_legacy_single_backdrop_is_migrated(client: TestClient, media_dir: Path):
    """旧版单槽位文件（media_dir/backdrop.*）首次访问时自动迁入图库并保持生效。"""
    media_dir.mkdir(parents=True, exist_ok=True)
    (media_dir / "backdrop.png").write_bytes(_PNG_1X1)

    data = client.get(_BASE).json()["data"]
    assert len(data["backdrops"]) == 1
    assert data["active_id"] == data["backdrops"][0]["id"]
    # 旧文件已搬入图库目录，不再留在根目录
    assert not (media_dir / "backdrop.png").exists()
    assert client.get(f"{_BASE}/backdrops/{data['active_id']}").status_code == 200


def test_gallery_count_limit(client: TestClient, monkeypatch):
    """图库张数达到上限后拒绝新上传，提示先删旧图。"""
    from movieclaw_api.services import appearance as appearance_media

    monkeypatch.setattr(appearance_media, "MAX_BACKDROP_COUNT", 2)
    assert _upload(client).status_code == 200
    assert _upload(client).status_code == 200

    resp = _upload(client)
    assert resp.status_code == 400
    assert "最多保留" in resp.json()["message"]


def test_reject_non_image(client: TestClient):
    """拒绝非图片类型（如纯文本），返回 400 与中文提示。"""
    resp = client.post(
        f"{_BASE}/backdrops",
        files={"file": ("note.txt", b"hello", "text/plain")},
    )
    assert resp.status_code == 400
    assert resp.json()["code"] == "BAD_REQUEST"


def test_reject_svg(client: TestClient):
    """明确拒绝 SVG：其可内嵌脚本，不适合作为背景图直接托管。"""
    svg = b'<svg xmlns="http://www.w3.org/2000/svg"></svg>'
    resp = client.post(
        f"{_BASE}/backdrops",
        files={"file": ("x.svg", svg, "image/svg+xml")},
    )
    assert resp.status_code == 400


def test_reject_empty_file(client: TestClient):
    """拒绝空文件上传。"""
    resp = client.post(
        f"{_BASE}/backdrops",
        files={"file": ("empty.png", b"", "image/png")},
    )
    assert resp.status_code == 400


def test_invalid_id_is_not_found(client: TestClient):
    """非法 id（含路径穿越尝试）一律按不存在处理，不触达文件系统。"""
    assert client.get(f"{_BASE}/backdrops/not-a-valid-id").status_code == 404
    assert client.delete(f"{_BASE}/backdrops/..%2f..%2fsecret").status_code == 404
