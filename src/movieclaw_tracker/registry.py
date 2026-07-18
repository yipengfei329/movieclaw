from __future__ import annotations

import dataclasses
import importlib
import logging
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import yaml

from movieclaw_tracker.base import BaseSite
from movieclaw_tracker.exceptions import SiteNotFoundError
from movieclaw_tracker.models import TorrentCategory

logger = logging.getLogger("movieclaw_tracker.registry")


# 各框架默认支持的授权类型。YAML 未显式声明 auth.supported 时按框架兜底：
# - api（如 M-Team）：只走 API-Key
# - nexusphp：既可粘 cookie，也可账号密码模拟登录
_DEFAULT_AUTH_BY_FRAMEWORK: dict[str, tuple[str, ...]] = {
    "api": ("apikey",),
    "nexusphp": ("cookie", "credential"),
}

# 合法的授权类型取值（与 movieclaw_db 的 AuthType 保持一致；此处用字符串避免
# tracker 反向依赖 db 层，保持纯领域库无外部依赖）
_VALID_AUTH_TYPES = frozenset({"cookie", "apikey", "credential"})


@dataclass(frozen=True)
class SiteConfig:
    """注册的 PT 站点完整配置。"""

    site_id: str
    display_name: str
    base_url: str
    framework: str
    site_class: type[BaseSite]
    # 网页访问域名（用户在浏览器打开的地址）。仅当与 base_url 不同才需配置：
    # API 类站点（如 M-Team）请求走 api.m-team.cc，但给用户展示的种子详情
    # 链接必须指向网页域名 tp.m-team.cc。None 表示与 base_url 相同。
    web_base_url: str | None = None
    selectors: Any | None = None
    category_map: dict[TorrentCategory, list[str]] = field(default_factory=dict)
    http2: bool = False
    timeout: float = 30.0
    max_retries: int = 3
    # 每站请求最小间隔（秒）：礼貌硬下限。None 表示未在 YAML 显式配置，
    # 由限流器回退到全局默认值；显式设了就用这个值。
    min_request_interval: float | None = None
    # 该站点支持的授权类型（供上层"可选项"展示，用户从中选一种来配置）
    # 用字符串元组，取值为 cookie / apikey / credential
    supported_auth_types: tuple[str, ...] = ()


# ---------------------------------------------------------------------------
# 模块级注册表
# ---------------------------------------------------------------------------

_registry: dict[str, SiteConfig] = {}


def register_site(config: SiteConfig) -> None:
    """注册一个站点配置。"""
    _registry[config.site_id] = config
    logger.info("Registered site: %s (%s)", config.site_id, config.display_name)


def get_site_config(site_id: str) -> SiteConfig:
    """查找已注册的站点。不存在则抛出 SiteNotFoundError。"""
    config = _registry.get(site_id)
    if config is None:
        raise SiteNotFoundError(site_id)
    return config


def list_sites() -> list[SiteConfig]:
    """返回所有已注册站点的列表。"""
    return list(_registry.values())


# ---------------------------------------------------------------------------
# YAML 配置加载
# ---------------------------------------------------------------------------


def _import_class(dotted_path: str) -> type[BaseSite]:
    """动态导入类。例如 'movieclaw_tracker.sites.custom.mteam.MTeamSite'。"""
    module_path, _, class_name = dotted_path.rpartition(".")
    module = importlib.import_module(module_path)
    cls = getattr(module, class_name)
    return cls


def _parse_supported_auth_types(raw: dict[str, Any], framework: str) -> tuple[str, ...]:
    """解析站点支持的授权类型。

    优先取 YAML 的 ``auth.supported``；未声明则按 framework 兜底。
    非法取值会被过滤并告警，避免脏配置流到上层。
    """
    auth_section = raw.get("auth") or {}
    declared = auth_section.get("supported")
    if declared is None:
        return _DEFAULT_AUTH_BY_FRAMEWORK.get(framework, ())

    result: list[str] = []
    for item in declared:
        value = str(item).lower()
        if value not in _VALID_AUTH_TYPES:
            logger.warning("站点 %s 声明了未知授权类型 '%s'，已忽略", raw.get("site_id"), value)
            continue
        result.append(value)
    return tuple(result)


def _parse_category_map(raw: dict[str, Any]) -> dict[TorrentCategory, list[str]]:
    """从 YAML 原始数据解析分类映射。"""
    raw_categories = raw.get("categories", {})
    category_map: dict[TorrentCategory, list[str]] = {}
    for key, ids in raw_categories.items():
        try:
            cat = TorrentCategory(key)
        except ValueError:
            logger.warning("Unknown category '%s' in config, skipping", key)
            continue
        category_map[cat] = [str(i) for i in ids]
    return category_map


def load_all_sites() -> None:
    """扫描 sites/configs/*.yaml，加载并注册所有站点配置。"""
    from movieclaw_tracker.frameworks.nexusphp import NexusPHPSite
    from movieclaw_tracker.selectors import NexusPHPSelectors

    framework_defaults: dict[str, tuple[type[BaseSite], type]] = {
        "nexusphp": (NexusPHPSite, NexusPHPSelectors),
    }

    configs_dir = Path(__file__).parent / "sites" / "configs"
    if not configs_dir.exists():
        logger.warning("Site configs directory not found: %s", configs_dir)
        return

    for yaml_file in sorted(configs_dir.glob("*.yaml")):
        if yaml_file.name.startswith("_"):
            continue

        try:
            raw = yaml.safe_load(yaml_file.read_text(encoding="utf-8"))
        except Exception:
            logger.exception("Failed to load config: %s", yaml_file)
            continue

        if not raw or not isinstance(raw, dict):
            logger.warning("Empty or invalid config: %s", yaml_file)
            continue

        site_id = raw.get("site_id", "")
        framework = raw.get("framework", "")
        category_map = _parse_category_map(raw)
        supported_auth_types = _parse_supported_auth_types(raw, framework)

        # 确定站点类和选择器
        # 若同时指定了 custom_class 和 framework，则：
        #   - custom_class 替换默认站点类（可继承 framework 的基类并扩展）
        #   - framework 继续负责解析 selectors，使 YAML 中的选择器覆盖正常生效
        # 若仅有 custom_class 而无 framework，selectors 为 None（自定义类自行管理）
        if "custom_class" in raw:
            site_class = _import_class(raw["custom_class"])
            if framework in framework_defaults:
                _, selector_cls = framework_defaults[framework]
                defaults = selector_cls()
                overrides = raw.get("selectors", {})
                for rule_key in ("promo_download_rules", "promo_upload_rules"):
                    if rule_key in overrides and isinstance(overrides[rule_key], dict):
                        overrides[rule_key] = tuple(
                            (css, float(factor))
                            for css, factor in overrides[rule_key].items()
                        )
                selectors = dataclasses.replace(defaults, **overrides) if overrides else defaults
            else:
                selectors = None
        elif framework in framework_defaults:
            site_class, selector_cls = framework_defaults[framework]
            defaults = selector_cls()
            overrides = raw.get("selectors", {})
            # 促销规则在 YAML 中写成字典（可读），加载时转为 tuple of tuples（不可变）
            for rule_key in ("promo_download_rules", "promo_upload_rules"):
                if rule_key in overrides and isinstance(overrides[rule_key], dict):
                    overrides[rule_key] = tuple(
                        (css, float(factor))
                        for css, factor in overrides[rule_key].items()
                    )
            selectors = dataclasses.replace(defaults, **overrides) if overrides else defaults
        else:
            logger.warning("Unknown framework '%s' in %s, skipping", framework, yaml_file)
            continue

        register_site(
            SiteConfig(
                site_id=site_id,
                display_name=raw.get("display_name", site_id),
                base_url=raw.get("base_url", ""),
                web_base_url=raw.get("web_base_url"),
                framework=framework,
                site_class=site_class,
                selectors=selectors,
                category_map=category_map,
                http2=raw.get("http2", False),
                timeout=raw.get("timeout", 30.0),
                max_retries=raw.get("max_retries", 3),
                min_request_interval=raw.get("min_request_interval"),
                supported_auth_types=supported_auth_types,
            )
        )
