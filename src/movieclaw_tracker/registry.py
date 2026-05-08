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


@dataclass(frozen=True)
class SiteConfig:
    """注册的 PT 站点完整配置。"""

    site_id: str
    display_name: str
    base_url: str
    framework: str
    site_class: type[BaseSite]
    selectors: Any | None = None
    category_map: dict[TorrentCategory, list[str]] = field(default_factory=dict)
    http2: bool = False
    timeout: float = 30.0
    max_retries: int = 3


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
                framework=framework,
                site_class=site_class,
                selectors=selectors,
                category_map=category_map,
                http2=raw.get("http2", False),
                timeout=raw.get("timeout", 30.0),
                max_retries=raw.get("max_retries", 3),
            )
        )
