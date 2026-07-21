"""ORM 表模型集中导出。

⚠️ 所有 ``table=True`` 的模型都必须在此导入，原因有二：
1. 只有被导入，模型才会注册到 ``SQLModel.metadata``，Alembic 自动生成迁移、
   以及 create_all 才能感知到这些表。
2. 给上层提供统一的导入入口：``from movieclaw_db.models import SiteCredential``。
"""

from __future__ import annotations

from movieclaw_db.models.agent_session import AgentSession
from movieclaw_db.models.app_setting import AppSetting
from movieclaw_db.models.base import TimestampMixin, utcnow
from movieclaw_db.models.cache_entry import CacheEntry
from movieclaw_db.models.download_hint import DownloadHint
from movieclaw_db.models.downloader_client import ClientType, DownloaderClient
from movieclaw_db.models.library import Library
from movieclaw_db.models.library_file import FileSource, LibraryFile
from movieclaw_db.models.llm_provider import LlmProvider
from movieclaw_db.models.media_item import MediaItem, MediaSeason
from movieclaw_db.models.rule_set import RuleSet
from movieclaw_db.models.scheduled_task import (
    ScheduledTask,
    TaskRun,
    TaskRunStatus,
    TriggerType,
)
from movieclaw_db.models.search_history import SearchHistory
from movieclaw_db.models.site_cookie import SiteCookie
from movieclaw_db.models.site_credential import AuthType, ConfigStatus, SiteCredential
from movieclaw_db.models.site_torrent import (
    SiteSyncCursor,
    SiteTorrent,
    TorrentSource,
)
from movieclaw_db.models.site_user_profile import SiteUserProfile
from movieclaw_db.models.subscription import (
    Subscription,
    SubscriptionStatus,
    WantedItem,
    WantedStatus,
)
from movieclaw_db.models.subscription_activity import ActivityType, SubscriptionActivity

__all__ = [
    "TimestampMixin",
    "utcnow",
    "AgentSession",
    "CacheEntry",
    "SiteCookie",
    "SiteCredential",
    "AuthType",
    "ConfigStatus",
    "ScheduledTask",
    "TaskRun",
    "TaskRunStatus",
    "TriggerType",
    "AppSetting",
    "ClientType",
    "DownloadHint",
    "DownloaderClient",
    "FileSource",
    "Library",
    "LibraryFile",
    "LlmProvider",
    "MediaItem",
    "MediaSeason",
    "RuleSet",
    "Subscription",
    "SubscriptionStatus",
    "WantedItem",
    "WantedStatus",
    "ActivityType",
    "SubscriptionActivity",
    "SearchHistory",
    "SiteTorrent",
    "SiteSyncCursor",
    "TorrentSource",
    "SiteUserProfile",
]
