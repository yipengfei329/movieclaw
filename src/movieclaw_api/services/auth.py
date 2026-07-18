"""超级管理员登录鉴权服务。

职责边界（"原语用库、编排手写"）：
- 密码哈希 / 校验     → pwdlib（argon2，常量时间比较由库保证）
- 会话令牌签名 / 验签 → itsdangerous（Flask session 同款签名机制）
- 本模块只写编排：一次性初始化锁、登录限速、会话生命周期。

★ 一次性初始化锁（本模块最核心的安全保证）
------------------------------------------
建号接口 ``create_admin`` 必须做到"只要管理员已存在，任何请求都不可能再次建号"：

1. 进程内并发：模块级 ``asyncio.Lock`` 串行化所有建号请求，杜绝
   两个并发请求同时通过"账号不存在"检查的 TOCTOU 窗口。
2. 缓存绕过：锁内先 ``invalidate`` 再读，强制从数据库取最新状态，
   避免多进程部署（uvicorn --workers N）下本进程缓存过期导致误判。
3. 写后复核：落库后立即再次从数据库读回，若读到的用户名与本次写入不符，
   说明极端并发下被其他进程抢先，本次建号作废并报错。
   （2+3 无法做到跨进程严格原子，但把竞争窗口压缩到毫秒级，且要求攻击者
   与合法用户在同一毫秒内竞争——配合"首次部署即初始化"的使用方式已足够。）
"""

from __future__ import annotations

import asyncio
import logging
import secrets
import time
from datetime import UTC, datetime

from itsdangerous import BadSignature, URLSafeSerializer
from pwdlib import PasswordHash

from movieclaw_api.exceptions import (
    AppException,
    BadRequestException,
    ConflictException,
    UnauthorizedException,
)
from movieclaw_api.settings import (
    AdminAccountSetting,
    SessionSecretSetting,
    get_descriptor_by_model,
    get_setting_store,
)

logger = logging.getLogger("movieclaw_api.auth")

# 会话 Cookie 名与有效期。签名令牌里带过期时间戳，轮换签名密钥即全端下线。
SESSION_COOKIE_NAME = "movieclaw_session"
SESSION_TTL_SECONDS = 7 * 24 * 3600
SESSION_TTL_REMEMBER_SECONDS = 30 * 24 * 3600
# itsdangerous 的签名域隔离标识：即使密钥泄漏复用，其他用途的签名也不能伪造会话
_SESSION_SALT = "movieclaw.session.v1"

# argon2（pwdlib 推荐配置）。verify 内部是常量时间比较。
_password_hash = PasswordHash.recommended()

# 建号一次性锁（进程内串行化，详见模块 docstring）
_bootstrap_lock = asyncio.Lock()


# ---------------------------------------------------------------------------
# 登录限速：全局连续失败计数（单管理员场景按账号维度限速最有效，
# 按 IP 反而会被反代地址稀释）。成功登录清零。
# ---------------------------------------------------------------------------


class LoginThrottle:
    """连续失败达到阈值后强制等待，等待时间随失败次数翻倍，封顶 5 分钟。"""

    THRESHOLD = 5
    BASE_DELAY_SECONDS = 30
    MAX_DELAY_SECONDS = 300

    def __init__(self) -> None:
        self._failures = 0
        self._locked_until = 0.0

    def ensure_allowed(self) -> None:
        remaining = self._locked_until - time.monotonic()
        if remaining > 0:
            raise AppException(
                status_code=429,
                code="TOO_MANY_ATTEMPTS",
                message=f"登录失败次数过多，请 {int(remaining) + 1} 秒后再试",
            )

    def record_failure(self) -> None:
        self._failures += 1
        if self._failures >= self.THRESHOLD:
            delay = min(
                self.BASE_DELAY_SECONDS * 2 ** (self._failures - self.THRESHOLD),
                self.MAX_DELAY_SECONDS,
            )
            self._locked_until = time.monotonic() + delay
            logger.warning("连续登录失败 %d 次，锁定 %d 秒", self._failures, delay)

    def reset(self) -> None:
        self._failures = 0
        self._locked_until = 0.0


_throttle = LoginThrottle()


# ---------------------------------------------------------------------------
# 初始化状态与一次性建号
# ---------------------------------------------------------------------------


async def _load_admin_fresh() -> AdminAccountSetting:
    """绕过缓存、强制从数据库读取管理员账号（用于安全判定，不吃过期缓存）。"""
    store = get_setting_store()
    store.invalidate(get_descriptor_by_model(AdminAccountSetting).namespace)
    return await store.get(AdminAccountSetting)


async def is_admin_initialized() -> bool:
    """管理员是否已创建。每次都查库，保证多进程部署下状态实时准确。"""
    admin = await _load_admin_fresh()
    return bool(admin.password_hash)


async def create_admin(username: str, password: str) -> AdminAccountSetting:
    """创建超级管理员（首次初始化，全生命周期只允许成功一次）。

    锁定策略见模块 docstring。已初始化时抛 409，错误信息刻意不区分
    "谁创建的/何时创建"，不给探测者任何额外信息。
    """
    async with _bootstrap_lock:
        current = await _load_admin_fresh()
        if current.password_hash:
            logger.warning("拒绝重复初始化：管理员账号已存在，来路请求被 409 拦截")
            raise ConflictException("系统已初始化，禁止重复创建管理员账号")

        account = AdminAccountSetting(
            username=username,
            password_hash=_password_hash.hash(password),
            nickname=username,  # 初始昵称即用户名，用户可在「个人信息」里随时改
            created_at=datetime.now(UTC).isoformat(timespec="seconds"),
        )
        await get_setting_store().set(account)

        # 写后复核：防御多进程并发下的抢先写入（详见模块 docstring 第 3 点）
        persisted = await _load_admin_fresh()
        if persisted.username != username or persisted.password_hash != account.password_hash:
            logger.error("初始化竞争检测：落库结果与本次写入不符，本次建号作废")
            raise ConflictException("系统已初始化，禁止重复创建管理员账号")

        logger.info("超级管理员账号已创建：%s", username)
        return account


# ---------------------------------------------------------------------------
# 登录认证
# ---------------------------------------------------------------------------


async def authenticate(username: str, password: str) -> AdminAccountSetting:
    """校验用户名密码；失败计入限速，成功清零。"""
    _throttle.ensure_allowed()

    admin = await get_setting_store().get(AdminAccountSetting)
    if not admin.password_hash:
        raise BadRequestException("系统尚未初始化，请先完成首次引导创建管理员账号")

    # 无论用户名是否匹配都执行一次密码哈希校验，避免通过响应时间差探测用户名
    password_ok = _password_hash.verify(password, admin.password_hash)
    username_ok = secrets.compare_digest(username.encode(), admin.username.encode())
    if not (password_ok and username_ok):
        _throttle.record_failure()
        logger.warning("登录失败：用户名或密码错误（用户名输入：%s）", username)
        raise UnauthorizedException("用户名或密码错误")

    _throttle.reset()
    logger.info("管理员 %s 登录成功", admin.username)
    return admin


async def get_admin_account() -> AdminAccountSetting:
    """读取管理员账号（走缓存即可，供展示类接口使用）。"""
    return await get_setting_store().get(AdminAccountSetting)


async def update_nickname(nickname: str) -> AdminAccountSetting:
    """修改展示昵称。昵称只影响界面展示，登录仍使用用户名。"""
    admin = await get_setting_store().get(AdminAccountSetting)
    if not admin.password_hash:
        raise BadRequestException("系统尚未初始化，无法修改昵称")
    admin.nickname = nickname
    await get_setting_store().set(admin)
    logger.info("管理员昵称已更新为：%s", nickname)
    return admin


async def change_password(old_password: str, new_password: str) -> None:
    """修改管理员密码，并轮换会话签名密钥（所有已登录会话立即失效）。"""
    admin = await get_setting_store().get(AdminAccountSetting)
    if not admin.password_hash:
        raise BadRequestException("系统尚未初始化，无法修改密码")
    if not _password_hash.verify(old_password, admin.password_hash):
        raise UnauthorizedException("原密码错误")

    admin.password_hash = _password_hash.hash(new_password)
    await get_setting_store().set(admin)
    await rotate_session_secret()
    logger.info("管理员密码已修改，所有登录会话已强制下线")


# ---------------------------------------------------------------------------
# 会话令牌：签发 / 验签 / 轮换
# ---------------------------------------------------------------------------


async def _get_session_secret() -> str:
    """读取会话签名密钥；首次使用时自动生成并加密落库（用户无感）。"""
    store = get_setting_store()
    setting = await store.get(SessionSecretSetting)
    if not setting.secret:
        setting = SessionSecretSetting(secret=secrets.token_urlsafe(48))
        await store.set(setting)
        logger.info("已自动生成登录会话签名密钥")
    return setting.secret


async def rotate_session_secret() -> None:
    """轮换签名密钥：所有已签发的会话令牌立即验签失败（全端下线）。"""
    await get_setting_store().set(SessionSecretSetting(secret=secrets.token_urlsafe(48)))


async def issue_session_token(username: str, *, remember: bool = False) -> tuple[str, int]:
    """签发会话令牌，返回 (令牌, 有效秒数)。过期时间写进签名负载，无法篡改。"""
    max_age = SESSION_TTL_REMEMBER_SECONDS if remember else SESSION_TTL_SECONDS
    serializer = URLSafeSerializer(await _get_session_secret(), salt=_SESSION_SALT)
    token = serializer.dumps({"u": username, "exp": int(time.time()) + max_age})
    return token, max_age


async def verify_session_token(token: str | None) -> str:
    """校验会话令牌，返回登录用户名；无效/过期统一抛 401 提示重新登录。"""
    if not token:
        raise UnauthorizedException("未登录，请先登录")

    serializer = URLSafeSerializer(await _get_session_secret(), salt=_SESSION_SALT)
    try:
        payload = serializer.loads(token)
    except BadSignature:
        raise UnauthorizedException("登录状态无效，请重新登录") from None

    if not isinstance(payload, dict) or int(payload.get("exp", 0)) < time.time():
        raise UnauthorizedException("登录已过期，请重新登录")
    return str(payload.get("u", ""))


def reset_auth_state() -> None:
    """清空模块级可变状态（登录限速计数）。仅供测试在用例间隔离。"""
    _throttle.reset()
