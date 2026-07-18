"""movieclaw 持久化层。

对外暴露数据库生命周期管理、会话依赖、Repository 与存储适配。
分层约定：本包依赖 SQLModel/SQLAlchemy，但**不依赖** movieclaw_tracker，
tracker 保持为纯领域库；两者通过结构化协议（CookieStore）解耦。
"""

from __future__ import annotations

from movieclaw_db.crypto import (
    SecretBox,
    get_secret_box,
    init_secret_box,
    reset_secret_box,
)
from movieclaw_db.engine import (
    Database,
    dispose_db,
    get_database,
    get_session,
    init_db,
)
from movieclaw_db.repositories import (
    CookieRepository,
    CredentialRepository,
    SettingRepository,
)
from movieclaw_db.stores import SqlCookieStore

__all__ = [
    "Database",
    "init_db",
    "get_database",
    "dispose_db",
    "get_session",
    "CookieRepository",
    "CredentialRepository",
    "SettingRepository",
    "SqlCookieStore",
    "SecretBox",
    "init_secret_box",
    "get_secret_box",
    "reset_secret_box",
]
