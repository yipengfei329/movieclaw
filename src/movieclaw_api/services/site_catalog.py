from __future__ import annotations

from urllib.parse import urlparse

from movieclaw_api.exceptions import BadRequestException, NotFoundException
from movieclaw_db.models.site_credential import AuthType
from movieclaw_tracker.registry import SiteConfig, get_site_config, list_sites

# 常见的多段顶级域名。用于从主机名推导"可注册域名"，与插件端 (apps/extension)
# 的同名逻辑保持一致，确保 kp.m-team.cc / api.m-team.cc 都能归到 m-team.cc。
_MULTI_PART_TLDS = frozenset(
    {
        "co.uk", "org.uk", "gov.uk", "ac.uk", "me.uk",
        "com.cn", "net.cn", "org.cn", "gov.cn",
        "com.hk", "com.tw", "com.au", "co.jp", "co.kr",
    }
)


def registrable_domain(host: str) -> str:
    """由主机名推导可注册域名，例如 ``kp.m-team.cc`` → ``m-team.cc``。

    简化实现：默认取末两段；遇到 ``co.uk`` 这类多段 TLD 时取末三段。
    对 PT 站点常见的单段 TLD（.cc/.im/.net/.co 等）足够可靠。
    """
    host = host.strip().lower().split(":", 1)[0]  # 去掉可能的端口
    parts = [p for p in host.split(".") if p]
    if len(parts) <= 2:
        return ".".join(parts)
    last_two = ".".join(parts[-2:])
    if last_two in _MULTI_PART_TLDS:
        return ".".join(parts[-3:])
    return last_two


class SiteCatalogService:
    """站点目录服务：暴露系统内置的"可选项"。

    目录数据来自 registry（由 sites/configs/*.yaml 在启动时加载），是**只读**的。
    它回答两类问题：
    1. 有哪些站点可供配置？各自支持哪些授权类型？（渲染前端"可选项"列表）
    2. 用户选的站点/授权类型是否合法？（配置写入前的校验）

    本服务不触碰数据库，纯粹基于内置目录，因此无需会话。
    """

    def list_catalog(self) -> list[SiteConfig]:
        """返回所有可配置站点（按注册顺序）。"""
        return list_sites()

    def get(self, site_id: str) -> SiteConfig:
        """获取单个站点目录项；站点不存在时抛 404。"""
        try:
            return get_site_config(site_id)
        except Exception as exc:  # registry 抛 SiteNotFoundError
            raise NotFoundException(f"站点不存在或未被系统支持：{site_id}") from exc

    def assert_auth_type_supported(self, site_id: str, auth_type: AuthType) -> SiteConfig:
        """校验某站点是否支持指定授权类型，不支持则抛 400。返回站点目录项。"""
        config = self.get(site_id)
        if auth_type.value not in config.supported_auth_types:
            supported = "、".join(config.supported_auth_types) or "（无）"
            raise BadRequestException(
                f"站点 {site_id} 不支持授权类型 '{auth_type.value}'，"
                f"支持的类型为：{supported}"
            )
        return config

    # -- 域名匹配（供浏览器插件按域名反查站点）-----------------------------

    @staticmethod
    def site_domain(config: SiteConfig) -> str:
        """取站点 base_url 的可注册域名，作为该站点的"匹配域名"。"""
        host = urlparse(config.base_url).hostname or ""
        return registrable_domain(host)

    def find_by_domain(self, hostname: str) -> SiteConfig | None:
        """按浏览器域名反查站点：可注册域名一致即命中；无匹配返回 None。

        注意：这里不区分是否支持 cookie —— 命中后由调用方决定如何处理
        （例如 M-Team 能按域名命中，但只支持 API-Key，应给出针对性提示）。
        """
        target = registrable_domain(hostname)
        if not target:
            return None
        for config in list_sites():
            if self.site_domain(config) == target:
                return config
        return None
