"""数据访问层（Repository）统一导出。

Repository 模式的意义：把 SQL / ORM 细节收敛在这一层，上层业务只面向
"存取对象"的语义方法（get/list/upsert/delete），未来若更换存储实现或优化查询，
影响范围被限制在本包内。
"""

from __future__ import annotations

from movieclaw_db.repositories.cookie_repo import CookieRepository
from movieclaw_db.repositories.credential_repo import CredentialRepository
from movieclaw_db.repositories.media_repo import MediaItemRepository
from movieclaw_db.repositories.profile_repo import ProfileRepository
from movieclaw_db.repositories.scheduled_task_repo import (
    ScheduledTaskRepository,
    TaskRunRepository,
)
from movieclaw_db.repositories.setting_repo import SettingRepository
from movieclaw_db.repositories.subscription_repo import (
    RuleSetRepository,
    SubscriptionRepository,
)
from movieclaw_db.repositories.torrent_repo import (
    TorrentObservation,
    TorrentRepository,
    UpsertStats,
)

__all__ = [
    "CookieRepository",
    "CredentialRepository",
    "MediaItemRepository",
    "ProfileRepository",
    "ScheduledTaskRepository",
    "TaskRunRepository",
    "SettingRepository",
    "RuleSetRepository",
    "SubscriptionRepository",
    "TorrentRepository",
    "TorrentObservation",
    "UpsertStats",
]
