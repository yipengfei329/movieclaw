"""业务服务层。

在"HTTP 接口"与"数据/领域库"之间做编排：接口只负责收发，服务负责校验、
组合 Repository 与 tracker、触发验证等业务逻辑。这一层同时依赖 movieclaw_db
（存储）与 movieclaw_tracker（领域能力），是二者的组合点。
"""

from __future__ import annotations

from movieclaw_api.services.auth_factory import (
    build_auth_provider,
    missing_required_fields,
    required_fields,
)
from movieclaw_api.services.downloader_config import (
    DownloaderConfigService,
    verify_downloader,
)
from movieclaw_api.services.site_catalog import SiteCatalogService
from movieclaw_api.services.site_config import SiteConfigService
from movieclaw_api.services.verification import verify_site

__all__ = [
    "DownloaderConfigService",
    "SiteCatalogService",
    "SiteConfigService",
    "verify_downloader",
    "verify_site",
    "build_auth_provider",
    "required_fields",
    "missing_required_fields",
]
