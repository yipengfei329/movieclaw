"""影视元数据层（movieclaw_media）——对接 TMDB 的独立领域包。

架构定位
--------
与 movieclaw_tracker（PT 站点抓取）、movieclaw_enrich（种子标题解析）平级的
领域包：负责「影视作品本身」的元数据（榜单、海报、简介、演职员……），
数据源为 TMDB v3 API。不依赖 movieclaw_api / movieclaw_db，由 API 层负责
读配置并装配（见 movieclaw_api.services.media_discover）。

组成
----
- tmdb.py    TMDB 异步客户端（双认证格式 / 重试 / 限流）
- service.py 发现页编排（Hero + 分类行聚合）与条目详情
- library.py 媒体身份层：条目档案拉取与豆瓣→TMDB 收敛（订阅功能的地基）
- models.py  对外数据模型（对齐前端发现页渲染需求）
- cache.py   进程内 TTL 缓存
"""

from movieclaw_media.douban import DoubanClient, DoubanDiscoverService, DoubanError
from movieclaw_media.library import (
    DoubanResolution,
    MediaProfile,
    ResolveCandidate,
    ResolveStatus,
    SeasonProfile,
    fetch_media_profile,
    resolve_douban_to_tmdb,
)
from movieclaw_media.models import (
    DiscoverPage,
    MediaCard,
    MediaDetail,
    MediaFacts,
    MediaImage,
    MediaKind,
    MediaRow,
    MediaSearchItem,
    MediaSource,
)
from movieclaw_media.service import MediaDiscoverService
from movieclaw_media.tmdb import (
    TmdbAuthError,
    TmdbClient,
    TmdbError,
    TmdbNotConfiguredError,
    TmdbNotFoundError,
)

__all__ = [
    "DiscoverPage",
    "MediaCard",
    "MediaDetail",
    "MediaFacts",
    "MediaImage",
    "MediaKind",
    "MediaRow",
    "MediaSearchItem",
    "MediaSource",
    "DoubanClient",
    "DoubanDiscoverService",
    "DoubanError",
    "DoubanResolution",
    "MediaProfile",
    "ResolveCandidate",
    "ResolveStatus",
    "SeasonProfile",
    "fetch_media_profile",
    "resolve_douban_to_tmdb",
    "MediaDiscoverService",
    "TmdbAuthError",
    "TmdbClient",
    "TmdbError",
    "TmdbNotConfiguredError",
    "TmdbNotFoundError",
]
