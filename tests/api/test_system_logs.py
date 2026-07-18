"""系统日志查看接口的端到端测试。

覆盖：日期列表（倒序）、按天读取、tail 截断、tail=0 全量、不存在的日期 404、
非法日期格式拒绝（路径穿越防护）。
鉴权由 test_auth 的守护测试统一覆盖（/system/logs 挂在受保护区）。
"""
from __future__ import annotations

from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from movieclaw_api.core.config import get_settings
from movieclaw_api.settings.store import reset_setting_store
from movieclaw_db.crypto import reset_secret_box


@pytest.fixture
def log_dir(tmp_path) -> Path:
    return tmp_path / "logs"


@pytest.fixture
def client(tmp_path, monkeypatch, log_dir):
    monkeypatch.setenv("DATABASE_URL", f"sqlite+aiosqlite:///{tmp_path / 'test.db'}")
    monkeypatch.setenv("SECRET_KEY_FILE", str(tmp_path / ".secret_key"))
    monkeypatch.setenv("SCHEDULER_ENABLED", "false")
    monkeypatch.setenv("LOG_DIR", str(log_dir))
    get_settings.cache_clear()
    reset_setting_store()
    reset_secret_box()

    from movieclaw_api.api.deps import require_login
    from movieclaw_api.app import create_app

    app = create_app()
    app.dependency_overrides[require_login] = lambda: "tester"
    with TestClient(app) as c:
        yield c

    reset_setting_store()
    reset_secret_box()
    get_settings.cache_clear()


def write_log(log_dir: Path, day: str, lines: list[str]) -> None:
    log_dir.mkdir(parents=True, exist_ok=True)
    (log_dir / f"movieclaw-{day}.log").write_text(
        "".join(f"{line}\n" for line in lines), encoding="utf-8"
    )


def test_list_days_sorted_desc(client: TestClient, log_dir: Path) -> None:
    """列表包含全部按天存档的文件且按日期倒序。

    应用自身的运行日志可能已在目录里写下「今天」的文件（这正是本功能的
    行为），故只断言手工写入的历史日期的相对顺序，不断言列表恰好等于它们。
    """
    write_log(log_dir, "2026-01-01", ["a"])
    write_log(log_dir, "2026-01-03", ["b", "c"])
    # 命名不符合规则的文件不应出现在列表里
    log_dir.joinpath("other.txt").write_text("x", encoding="utf-8")

    resp = client.get("/api/v1/system/logs")
    assert resp.status_code == 200
    days = resp.json()["data"]["days"]
    listed = [d["day"] for d in days]
    assert listed == sorted(listed, reverse=True)
    assert "other.txt" not in listed
    assert listed[-2:] == ["2026-01-03", "2026-01-01"]
    assert all(d["size_bytes"] > 0 for d in days)


def test_read_day_full_and_tail(client: TestClient, log_dir: Path) -> None:
    # 用历史日期：当天的文件应用自身会持续追加访问日志，内容不可控
    lines = [f"line-{i}" for i in range(10)]
    write_log(log_dir, "2026-01-02", lines)

    resp = client.get("/api/v1/system/logs/2026-01-02")
    data = resp.json()["data"]
    assert data["lines"] == lines
    assert data["total_lines"] == 10
    assert data["truncated"] is False

    resp = client.get("/api/v1/system/logs/2026-01-02", params={"tail": 3})
    data = resp.json()["data"]
    assert data["lines"] == ["line-7", "line-8", "line-9"]
    assert data["total_lines"] == 10
    assert data["truncated"] is True

    # tail=0 表示全量
    resp = client.get("/api/v1/system/logs/2026-01-02", params={"tail": 0})
    data = resp.json()["data"]
    assert data["lines"] == lines
    assert data["truncated"] is False


def test_read_missing_day_404(client: TestClient) -> None:
    resp = client.get("/api/v1/system/logs/2020-01-01")
    assert resp.status_code == 404


def test_invalid_day_rejected(client: TestClient) -> None:
    """日期格式经严格校验，路径穿越类输入直接被拒。"""
    for bad in ["..%2F..%2Fetc", "2026-7-8", "not-a-date"]:
        resp = client.get(f"/api/v1/system/logs/{bad}")
        assert resp.status_code in (404, 422)
        assert resp.json().get("data") is None
