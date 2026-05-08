"""CredentialAuthProvider 账号密码登录认证 单元测试。

所有测试通过 Mock HttpClient 完成，不发送真实 HTTP 请求。
"""
from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, PropertyMock

import httpx
import pytest

from movieclaw_tracker.auth import CredentialAuthProvider
from movieclaw_tracker.exceptions import TrackerAuthError
from movieclaw_tracker.models import AuthState
from movieclaw_tracker.selectors import LoginSelectors

# ---------------------------------------------------------------------------
# 测试用 HTML 模板
# ---------------------------------------------------------------------------

LOGIN_PAGE_NO_CAPTCHA = """
<html><body>
<form action="login.php" method="post">
  <input name="username" />
  <input name="password" type="password" />
  <input type="submit" value="Login" />
</form>
</body></html>
"""

LOGIN_PAGE_WITH_CAPTCHA = """
<html><body>
<form action="login.php" method="post">
  <input name="username" />
  <input name="password" type="password" />
  <img src="imagecode.php?action=regimage&imagehash=abc123" />
  <input name="imagestring" />
  <input name="imagehash" type="hidden" value="abc123" />
  <input type="submit" value="Login" />
</form>
</body></html>
"""

SUCCESS_PAGE = """
<html><body>
<a href="logout.php">登出</a>
<p>欢迎回来</p>
</body></html>
"""

ERROR_PAGE_WRONG_PASSWORD = """
<html><body>
<td class="text">用户名或密码错误</td>
</body></html>
"""

ERROR_PAGE_CAPTCHA = """
<html><body>
<td class="text">验证码错误，请重新输入</td>
</body></html>
"""

AUTHENTICATED_INDEX = """
<html><body>
<a href="logout.php">登出</a>
<div>首页内容</div>
</body></html>
"""

EXPIRED_INDEX = """
<html><body>
<a href="login.php">登录</a>
<div>请先登录</div>
</body></html>
"""


# ---------------------------------------------------------------------------
# 辅助工具
# ---------------------------------------------------------------------------


def _make_response(text: str, status_code: int = 200) -> httpx.Response:
    """构造一个模拟的 httpx.Response。"""
    return httpx.Response(
        status_code=status_code,
        text=text,
        request=httpx.Request("GET", "http://test"),
    )


def _make_client(*responses: httpx.Response) -> MagicMock:
    """构造一个模拟的 HttpClient，按顺序返回指定的响应。"""
    client = MagicMock()
    client.raw_get = AsyncMock(side_effect=list(responses))
    client.raw_post = AsyncMock(side_effect=list(responses))

    # cookies 属性模拟
    cookie_jar = httpx.Cookies()
    type(client).cookies = PropertyMock(
        return_value=cookie_jar,
    )
    # cookies setter
    client.cookies = cookie_jar
    return client


def _make_provider(**kwargs) -> CredentialAuthProvider:
    """创建已绑定站点上下文的 CredentialAuthProvider。"""
    provider = CredentialAuthProvider(
        username=kwargs.pop("username", "testuser"),
        password=kwargs.pop("password", "testpass"),
        **kwargs,
    )
    provider.bind(
        base_url=kwargs.pop("base_url", "https://example.com"),
        login_selectors=kwargs.pop("login_selectors", LoginSelectors()),
    )
    return provider


# ---------------------------------------------------------------------------
# 测试用例
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_login_success_no_captcha() -> None:
    """无验证码时，正常用户名密码登录成功。"""
    login_page_resp = _make_response(LOGIN_PAGE_NO_CAPTCHA)
    success_resp = _make_response(SUCCESS_PAGE)

    client = MagicMock()
    client.raw_get = AsyncMock(return_value=login_page_resp)
    client.raw_post = AsyncMock(return_value=success_resp)

    cookie_jar = httpx.Cookies({"uid": "123", "pass": "abc"})
    type(client).cookies = PropertyMock(return_value=cookie_jar)

    provider = CredentialAuthProvider(username="user", password="pass")
    provider.bind(base_url="https://example.com")

    result = await provider.authenticate(client)

    assert result.success is True
    assert result.state == AuthState.AUTHENTICATED
    assert result.cookies is not None
    assert "uid" in result.cookies

    # 验证 POST 提交了正确的表单数据
    call_kwargs = client.raw_post.call_args
    assert call_kwargs.kwargs["data"]["username"] == "user"
    assert call_kwargs.kwargs["data"]["password"] == "pass"


@pytest.mark.asyncio
async def test_login_success_with_captcha() -> None:
    """带验证码时，solver 识别后登录成功。"""
    login_page_resp = _make_response(LOGIN_PAGE_WITH_CAPTCHA)
    captcha_img_resp = _make_response("fake-image-bytes")
    captcha_img_resp._content = b"fake-image-bytes"
    success_resp = _make_response(SUCCESS_PAGE)

    # solver mock
    solver = AsyncMock()
    solver.solve = AsyncMock(return_value="x7k9")

    client = MagicMock()
    # raw_get: 第一次返回登录页，第二次返回验证码图片
    client.raw_get = AsyncMock(side_effect=[login_page_resp, captcha_img_resp])
    client.raw_post = AsyncMock(return_value=success_resp)

    cookie_jar = httpx.Cookies({"sid": "session123"})
    type(client).cookies = PropertyMock(return_value=cookie_jar)

    provider = CredentialAuthProvider(
        username="user", password="pass", captcha_solver=solver,
    )
    provider.bind(base_url="https://example.com")

    result = await provider.authenticate(client)

    assert result.success is True
    assert result.state == AuthState.AUTHENTICATED
    solver.solve.assert_called_once_with(b"fake-image-bytes")

    # 验证表单中包含验证码字段
    form_data = client.raw_post.call_args.kwargs["data"]
    assert form_data["imagestring"] == "x7k9"
    assert form_data["imagehash"] == "abc123"


@pytest.mark.asyncio
async def test_login_no_solver_returns_needs_captcha() -> None:
    """检测到验证码但未配置 solver 时，返回 NEEDS_CAPTCHA 状态。"""
    login_page_resp = _make_response(LOGIN_PAGE_WITH_CAPTCHA)

    client = MagicMock()
    client.raw_get = AsyncMock(return_value=login_page_resp)

    cookie_jar = httpx.Cookies()
    type(client).cookies = PropertyMock(return_value=cookie_jar)

    provider = CredentialAuthProvider(username="user", password="pass")
    provider.bind(base_url="https://example.com")

    result = await provider.authenticate(client)

    assert result.success is False
    assert result.state == AuthState.NEEDS_CAPTCHA
    assert result.captcha_image_url is not None
    assert "imagecode.php" in result.captcha_image_url
    # 不应尝试 POST
    client.raw_post.assert_not_called()


@pytest.mark.asyncio
async def test_login_captcha_retry() -> None:
    """验证码识别错误时自动重试，第二次成功。"""
    login_page_resp = _make_response(LOGIN_PAGE_WITH_CAPTCHA)
    captcha_img_resp = _make_response("")
    captcha_img_resp._content = b"img-bytes"
    error_resp = _make_response(ERROR_PAGE_CAPTCHA)
    success_resp = _make_response(SUCCESS_PAGE)

    solver = AsyncMock()
    solver.solve = AsyncMock(side_effect=["wrong", "correct"])

    client = MagicMock()
    # 调用顺序：GET登录页 → GET验证码图片 → (POST失败) → GET登录页 → GET验证码图片 → (POST成功)
    client.raw_get = AsyncMock(
        side_effect=[login_page_resp, captcha_img_resp, login_page_resp, captcha_img_resp],
    )
    client.raw_post = AsyncMock(side_effect=[error_resp, success_resp])

    cookie_jar = httpx.Cookies({"sid": "ok"})
    type(client).cookies = PropertyMock(return_value=cookie_jar)

    provider = CredentialAuthProvider(
        username="user", password="pass", captcha_solver=solver,
    )
    provider.bind(base_url="https://example.com")

    result = await provider.authenticate(client)

    assert result.success is True
    assert result.state == AuthState.AUTHENTICATED
    assert solver.solve.call_count == 2
    assert client.raw_post.call_count == 2


@pytest.mark.asyncio
async def test_login_failure_wrong_password() -> None:
    """用户名或密码错误时抛出 TrackerAuthError。"""
    login_page_resp = _make_response(LOGIN_PAGE_NO_CAPTCHA)
    error_resp = _make_response(ERROR_PAGE_WRONG_PASSWORD)

    client = MagicMock()
    client.raw_get = AsyncMock(return_value=login_page_resp)
    client.raw_post = AsyncMock(return_value=error_resp)

    cookie_jar = httpx.Cookies()
    type(client).cookies = PropertyMock(return_value=cookie_jar)

    provider = CredentialAuthProvider(username="user", password="wrong")
    provider.bind(base_url="https://example.com")

    with pytest.raises(TrackerAuthError, match="用户名或密码错误"):
        await provider.authenticate(client)


@pytest.mark.asyncio
async def test_check_authenticated() -> None:
    """会话有效时 check() 返回 AUTHENTICATED。"""
    index_resp = _make_response(AUTHENTICATED_INDEX)

    client = MagicMock()
    client.raw_get = AsyncMock(return_value=index_resp)

    provider = CredentialAuthProvider(username="user", password="pass")
    provider.bind(base_url="https://example.com")

    state = await provider.check(client)
    assert state == AuthState.AUTHENTICATED


@pytest.mark.asyncio
async def test_check_expired() -> None:
    """会话过期时 check() 返回 EXPIRED。"""
    index_resp = _make_response(EXPIRED_INDEX)

    client = MagicMock()
    client.raw_get = AsyncMock(return_value=index_resp)

    provider = CredentialAuthProvider(username="user", password="pass")
    provider.bind(base_url="https://example.com")

    state = await provider.check(client)
    assert state == AuthState.EXPIRED


@pytest.mark.asyncio
async def test_unbind_raises_error() -> None:
    """未调用 bind() 时尝试认证应抛出 TrackerAuthError。"""
    client = MagicMock()

    provider = CredentialAuthProvider(username="user", password="pass")

    with pytest.raises(TrackerAuthError, match="尚未绑定站点"):
        await provider.authenticate(client)


@pytest.mark.asyncio
async def test_extra_form_data() -> None:
    """extra_form_data 中的额外字段应出现在 POST 表单中。"""
    login_page_resp = _make_response(LOGIN_PAGE_NO_CAPTCHA)
    success_resp = _make_response(SUCCESS_PAGE)

    client = MagicMock()
    client.raw_get = AsyncMock(return_value=login_page_resp)
    client.raw_post = AsyncMock(return_value=success_resp)

    cookie_jar = httpx.Cookies({"sid": "ok"})
    type(client).cookies = PropertyMock(return_value=cookie_jar)

    selectors = LoginSelectors(
        extra_form_data=(("passan", ""), ("passid", "0"), ("lang", "0")),
    )
    provider = CredentialAuthProvider(username="user", password="pass")
    provider.bind(base_url="https://example.com", login_selectors=selectors)

    result = await provider.authenticate(client)

    assert result.success is True
    form_data = client.raw_post.call_args.kwargs["data"]
    assert form_data["passan"] == ""
    assert form_data["passid"] == "0"
    assert form_data["lang"] == "0"
