"""网络与代理配置域（「设置 → 网络」页的数据模型）。

与 ``movieclaw_net`` 的关系：本模块只负责**声明与持久化**用户的网络配置；
把配置灌进出口层使其生效的运行时逻辑在 ``services/network_egress.py``。

字段设计对应设置页的三块内容：
1. 代理模式与地址（off / env / manual + http/socks5 地址）；
2. 每服务走代理开关（``proxy_services``，含 ``site:<id>`` 形式的 PT 站条目）；
3. 镜像地址覆盖（TMDB 接口/图床、豆瓣接口；空 = 用环境变量或内置默认值）。
"""

from __future__ import annotations

from typing import Literal

from pydantic import Field

from movieclaw_api.settings.base import SettingSchema, register_setting

# 服务标签目录：设置页开关列表的固定部分（PT 站条目按已配置站点动态生成）。
# label 面向用户展示，代码内一律用 id。
BUILTIN_EGRESS_SERVICES: list[dict[str, str]] = [
    {
        "id": "tmdb",
        "label": "TMDB 元数据",
        "description": "发现页/订阅建档的数据源 api.themoviedb.org",
    },
    {
        "id": "image",
        "label": "图片回源",
        "description": "海报/背景图代理回源（TMDB 图床及各站图床）",
    },
    {"id": "douban", "label": "豆瓣", "description": "豆瓣榜单与搜索（国内网络通常可直连）"},
    {"id": "llm", "label": "AI 模型", "description": "大语言模型供应商接口"},
]


@register_setting(
    namespace="network.egress",
    title="网络与代理",
    # 代理地址可能内嵌认证信息（http://user:pass@host:port），按敏感字段加密落库
    secret_fields=["proxy_url"],
)
class NetworkEgressSetting(SettingSchema):
    """统一网络出口配置。默认值 = 跟随环境变量，仅 TMDB 与图片回源走代理。

    这样 Docker 部署者只要 ``-e HTTPS_PROXY=...`` 就能解决最常见的
    「TMDB 被墙」问题，而 PT 站/豆瓣保持直连（国内直连通常更快，且部分
    PT 站风控在意出口 IP）。
    """

    proxy_mode: Literal["off", "env", "manual"] = Field(
        default="env",
        description="代理模式：off 全部直连；env 代理地址取自环境变量；manual 手动填写",
    )
    proxy_url: str = Field(
        default="",
        description="手动模式的代理地址，支持 http:// 与 socks5://（如 socks5://192.168.1.2:7891）",
    )
    proxy_services: list[str] = Field(
        default_factory=lambda: ["tmdb", "image"],
        description="走代理的服务标签列表；PT 站用 site:<站点id> 形式按站独立控制",
    )
    tmdb_api_base_url: str = Field(
        default="", description="TMDB 接口镜像地址覆盖；空 = 环境变量或官方默认"
    )
    tmdb_image_base_url: str = Field(
        default="", description="TMDB 图床镜像地址覆盖；空 = 环境变量或官方默认"
    )
    douban_api_base_url: str = Field(
        default="", description="豆瓣接口反代地址覆盖；空 = 环境变量或内置默认"
    )
