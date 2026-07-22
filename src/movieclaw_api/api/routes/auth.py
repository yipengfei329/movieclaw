"""登录鉴权路由：首次初始化、登录、登出、会话查询、修改密码。

安全分区（与 api/router.py 的三分区对应）：
- 公开：GET/POST /auth/bootstrap、POST /auth/login、POST /auth/logout。
  其中 POST /auth/bootstrap 由服务层的一次性锁自我封闭（管理员已存在即 409），
  logout 只是清 Cookie，无需登录也无危害（会话过期后也能顺利登出）。
- 登录后：GET /auth/me、PUT /auth/password、PUT /auth/profile、
  POST/GET /auth/avatar（头像上传与读取；头像属于个人信息，读取也要求登录，
  同源部署下 <img> 自动携带会话 Cookie，前端零改造）。

会话凭证放 HttpOnly Cookie（同源部署下前端零改造自动携带；XSS 偷不走，
SameSite=Lax 挡跨站请求伪造）。
"""

from __future__ import annotations

import logging

from fastapi import APIRouter, Depends, File, Response, UploadFile
from fastapi.responses import FileResponse

from movieclaw_api.api.deps import require_login
from movieclaw_api.core.config import get_settings
from movieclaw_api.exceptions import BadRequestException, NotFoundException
from movieclaw_api.schemas.auth import (
    BootstrapRequest,
    BootstrapStatus,
    ChangePasswordRequest,
    LoginRequest,
    SessionView,
    UpdateProfileRequest,
)
from movieclaw_api.schemas.response import ApiResponse, ok
from movieclaw_api.services import auth as auth_service
from movieclaw_api.services import avatar as avatar_media
from movieclaw_api.settings import AdminAccountSetting, mark_initialized

logger = logging.getLogger("movieclaw_api.auth")

router = APIRouter(prefix="/auth", tags=["auth"])


def _avatar_url() -> str | None:
    """构造头像的带版本号相对地址；未上传过头像时返回 None（前端显示首字徽标）。

    版本号取文件 mtime 纳秒值：换头像 → URL 变化，绕开浏览器 <img> 缓存。
    """
    version = avatar_media.avatar_version()
    if version is None:
        return None
    return f"{get_settings().api_v1_prefix}/auth/avatar?v={version}"


def _session_view(account: AdminAccountSetting) -> SessionView:
    """账号 → 会话视图。老账号可能没存过昵称（字段后加的），回退到用户名。"""
    return SessionView(
        username=account.username,
        nickname=account.nickname or account.username,
        avatar_url=_avatar_url(),
    )


def _set_session_cookie(response: Response, token: str, max_age: int) -> None:
    """统一的会话 Cookie 写入口，安全属性集中在这一处维护。"""
    response.set_cookie(
        key=auth_service.SESSION_COOKIE_NAME,
        value=token,
        max_age=max_age,
        httponly=True,  # JS 不可读，XSS 无法窃取会话
        samesite="lax",  # 跨站发起的 POST 不携带，天然防 CSRF
        # 自托管常见 LAN 内 http 直连，Secure 默认关闭；公网 https 部署时开启
        secure=get_settings().session_cookie_secure,
        path="/",
    )


@router.get(
    "/bootstrap",
    response_model=ApiResponse[BootstrapStatus],
    summary="查询系统是否已完成首次初始化",
)
async def bootstrap_status() -> ApiResponse[BootstrapStatus]:
    """公开接口：仅返回布尔状态，供前端决定进 /setup 还是 /login。"""
    return ok(BootstrapStatus(initialized=await auth_service.is_admin_initialized()))


@router.post(
    "/bootstrap",
    response_model=ApiResponse[SessionView],
    summary="首次初始化：创建超级管理员（全生命周期仅一次）",
)
async def bootstrap_create(
    payload: BootstrapRequest, response: Response
) -> ApiResponse[SessionView]:
    """创建管理员并自动登录。管理员已存在时一律 409，锁在服务端，不可绕过。"""
    account = await auth_service.create_admin(payload.username, payload.password)
    await mark_initialized()

    token, max_age = await auth_service.issue_session_token(account.username)
    _set_session_cookie(response, token, max_age)
    return ok(_session_view(account), message="初始化完成，已自动登录")


@router.post(
    "/login",
    response_model=ApiResponse[SessionView],
    summary="管理员登录",
)
async def login(payload: LoginRequest, response: Response) -> ApiResponse[SessionView]:
    """校验账号密码并种下会话 Cookie。连续失败会触发限速（429）。"""
    account = await auth_service.authenticate(payload.username, payload.password)
    token, max_age = await auth_service.issue_session_token(
        account.username, remember=payload.remember
    )
    _set_session_cookie(response, token, max_age)
    return ok(_session_view(account), message="登录成功")


@router.post(
    "/logout",
    response_model=ApiResponse[None],
    summary="退出登录",
)
async def logout(response: Response) -> ApiResponse[None]:
    """清除会话 Cookie。无需登录态即可调用（会话已过期时也能正常登出）。"""
    response.delete_cookie(auth_service.SESSION_COOKIE_NAME, path="/")
    return ok(None, message="已退出登录")


@router.get(
    "/me",
    response_model=ApiResponse[SessionView],
    summary="查询当前登录状态",
    dependencies=[Depends(require_login)],
)
async def me() -> ApiResponse[SessionView]:
    return ok(_session_view(await auth_service.get_admin_account()))


@router.put(
    "/profile",
    response_model=ApiResponse[SessionView],
    summary="修改个人信息（昵称）",
    dependencies=[Depends(require_login)],
)
async def update_profile(payload: UpdateProfileRequest) -> ApiResponse[SessionView]:
    """昵称只影响界面展示；登录用户名与会话均不受影响。"""
    account = await auth_service.update_nickname(payload.nickname.strip())
    return ok(_session_view(account), message="个人信息已更新")


@router.post(
    "/avatar",
    response_model=ApiResponse[SessionView],
    summary="上传（替换）头像",
    dependencies=[Depends(require_login)],
)
async def upload_avatar(file: UploadFile = File(...)) -> ApiResponse[SessionView]:
    """接收一张图片存为头像；已有头像直接替换（单槽位，不保留历史）。

    校验：只接受常见位图格式（拒绝可内嵌脚本的 SVG）、大小有上限。
    错误信息为中文，方便非开发者按提示处理。
    """
    if not avatar_media.is_supported_content_type(file.content_type):
        raise BadRequestException("不支持的图片格式，请上传 JPG / PNG / WebP / GIF / AVIF 图片")

    data = await file.read()
    if not data:
        raise BadRequestException("上传的图片为空，请重新选择")
    if len(data) > avatar_media.MAX_AVATAR_BYTES:
        limit_mb = avatar_media.MAX_AVATAR_BYTES // (1024 * 1024)
        raise BadRequestException(f"图片过大，请控制在 {limit_mb}MB 以内")

    # 已在上面校验过 content_type 属于受支持集合，此处必定命中
    avatar_media.save_avatar(data, file.content_type)  # type: ignore[arg-type]
    return ok(_session_view(await auth_service.get_admin_account()), message="头像已更新")


@router.get(
    "/avatar",
    summary="读取头像文件",
    response_class=Response,
    dependencies=[Depends(require_login)],
)
async def read_avatar() -> FileResponse:
    """直接返回头像图片本体，供 <img> 加载；地址由会话视图的 avatar_url 给出。"""
    path = avatar_media.find_avatar()
    if path is None:
        raise NotFoundException("尚未上传头像")
    return FileResponse(
        path,
        media_type=avatar_media.content_type_for(path),
        # URL 带版本号做缓存键，这里可放心让浏览器长期缓存，换头像时 URL 会变。
        headers={"Cache-Control": "private, max-age=31536000"},
    )


@router.put(
    "/password",
    response_model=ApiResponse[SessionView],
    summary="修改管理员密码（其余会话全部强制下线）",
)
async def change_password(
    payload: ChangePasswordRequest,
    response: Response,
    username: str = Depends(require_login),
) -> ApiResponse[SessionView]:
    """改密后轮换签名密钥（全端下线），随即为当前会话重新签发 Cookie，
    保证操作者本人不被踢出。"""
    await auth_service.change_password(payload.old_password, payload.new_password)
    token, max_age = await auth_service.issue_session_token(username)
    _set_session_cookie(response, token, max_age)
    return ok(
        _session_view(await auth_service.get_admin_account()),
        message="密码已修改，其他设备已全部下线",
    )
