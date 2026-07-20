"""API 路由总装：按鉴权级别分三区挂载。

┌─ 公开区 ──── health、auth（登录/初始化本身不能要求登录）
├─ 插件区 ──── extension 插件侧接口，路由级挂 require_sync_token（独立密钥体系）；
│              其中令牌管理接口（Web 后台用）在路由级挂 require_login
├─ 受保护区 ── 其余全部业务接口，挂载时统一注入 require_login
└─ 兜底 ────── tests/api/test_auth.py 的守护测试遍历全部路由，凡未挂鉴权
               又不在公开白名单的路由直接测试失败（默认拒绝，防止新路由漏挂）

新增业务路由时：默认加进下方 _PROTECTED_ROUTERS；确需公开的接口必须同时
更新守护测试的白名单，二者不一致 CI 会拦下来。
"""

from fastapi import APIRouter, Depends

from movieclaw_api.api.deps import require_login
from movieclaw_api.api.routes.agent import router as agent_router
from movieclaw_api.api.routes.appearance import router as appearance_router
from movieclaw_api.api.routes.auth import router as auth_router
from movieclaw_api.api.routes.discover import router as discover_router
from movieclaw_api.api.routes.downloaders import router as downloaders_router
from movieclaw_api.api.routes.extension import router as extension_router
from movieclaw_api.api.routes.health import router as health_router
from movieclaw_api.api.routes.images import router as images_router
from movieclaw_api.api.routes.libraries import router as libraries_router
from movieclaw_api.api.routes.llm import router as llm_router
from movieclaw_api.api.routes.logs import router as logs_router
from movieclaw_api.api.routes.rule_sets import router as rule_sets_router
from movieclaw_api.api.routes.search import router as search_router
from movieclaw_api.api.routes.sites import router as sites_router
from movieclaw_api.api.routes.subscriptions import router as subscriptions_router
from movieclaw_api.api.routes.ui import router as ui_router

api_router = APIRouter()

# ---- 公开区 ---------------------------------------------------------------
api_router.include_router(health_router, tags=["health"])
api_router.include_router(auth_router)

# ---- 插件区（鉴权在各路由上自行声明：插件侧 sync token / 管理侧 login）----
api_router.include_router(extension_router)

# ---- 外观（读公开：登录页也要加载背景图；写在路由级挂 require_login）------
api_router.include_router(appearance_router)

# ---- 受保护区（挂载时统一注入登录鉴权）------------------------------------
_PROTECTED_ROUTERS = [
    sites_router,
    downloaders_router,
    search_router,
    ui_router,
    discover_router,
    images_router,
    llm_router,
    agent_router,
    subscriptions_router,
    libraries_router,
    rule_sets_router,
    logs_router,
]
for _router in _PROTECTED_ROUTERS:
    api_router.include_router(_router, dependencies=[Depends(require_login)])
