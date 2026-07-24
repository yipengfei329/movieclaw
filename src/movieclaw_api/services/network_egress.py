"""网络出口配置的运行时装配：配置域 ↔ movieclaw_net 出口层的桥。

职责：
- 启动时（lifespan）与设置保存后，把 ``NetworkEgressSetting`` 灌进
  ``movieclaw_net.apply_egress_config`` 使代理路由立即生效（transport 按
  epoch 热切换，无需重启、无需重建单例）；
- 向装配层（media_discover 等）提供"生效的镜像地址"：设置页的覆盖值优先，
  空则回落到环境变量/内置默认——保证老部署（只配 env）行为不变。

镜像地址与代理不同：客户端把 base_url 绑死在构造期，改镜像地址后由保存
接口按需重建相关单例（见 routes/network.py）。
"""

from __future__ import annotations

import logging

from movieclaw_api.core.config import get_settings
from movieclaw_api.settings import NetworkEgressSetting
from movieclaw_api.settings.store import get_setting_store
from movieclaw_net import EgressConfig, ProxyMode, apply_egress_config

logger = logging.getLogger("movieclaw_api.network_egress")

# 当前生效配置的进程内快照：装配层同步读取（settings store 是 async 的，
# 而各单例 getter 是同步函数，故在加载/保存时同步一份到模块态）
_current: NetworkEgressSetting | None = None


def _apply(setting: NetworkEgressSetting) -> None:
    global _current
    _current = setting
    apply_egress_config(
        EgressConfig(
            proxy_mode=ProxyMode(setting.proxy_mode),
            proxy_url=setting.proxy_url.strip(),
            proxy_services=frozenset(setting.proxy_services),
        )
    )


async def load_network_egress() -> NetworkEgressSetting:
    """从配置域加载网络配置并使其生效。应用启动与设置保存后各调一次。"""
    setting = await get_setting_store().get(NetworkEgressSetting)
    _apply(setting)
    return setting


async def save_network_egress(setting: NetworkEgressSetting) -> NetworkEgressSetting:
    """保存网络配置并立即生效，返回保存后的值。"""
    await get_setting_store().set(setting)
    _apply(setting)
    return setting


def current_network_setting() -> NetworkEgressSetting:
    """当前生效的网络配置快照；启动加载前调用则按默认值处理。"""
    return _current or NetworkEgressSetting()


# ---------------------------------------------------------------------------
# 生效镜像地址：设置页覆盖 > 环境变量/内置默认
# ---------------------------------------------------------------------------


def effective_tmdb_api_base_url() -> str:
    override = current_network_setting().tmdb_api_base_url.strip()
    return override or get_settings().tmdb_api_base_url


def effective_tmdb_image_base_url() -> str:
    override = current_network_setting().tmdb_image_base_url.strip()
    return override or get_settings().tmdb_image_base_url


def effective_douban_api_base_url() -> str:
    override = current_network_setting().douban_api_base_url.strip()
    return override or get_settings().douban_api_base_url


def reset_network_egress() -> None:
    """仅供测试：清空快照，回到未加载状态。"""
    global _current
    _current = None
