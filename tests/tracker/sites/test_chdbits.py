"""彩虹岛（CHDBits）站点集成测试。

使用方法
--------
1. 用浏览器登录 https://chdbits.co，打开 DevTools →
   Network → 任意请求 → Request Headers → 复制 Cookie 整行的值。
2. 在仓库根目录的 .env 中填写::

       MOVIECLAW_TEST_COOKIES_CHDBITS="c_secure_uid=...; c_secure_pass=...; c_secure_ssl=yes; c_secure_tracker_ssl=yes"

   .env 已在 .gitignore 中，凭据不会进 git。
3. 运行测试::

       pytest tests/tracker/sites/test_chdbits.py -v -s

注意
----
- 所有 case 依赖真实 cookie，未填写时自动跳过，不影响 CI。
- 测试只读取远端数据，不会下载种子文件。
"""
from __future__ import annotations

import os

import pytest

from movieclaw_tracker import create_site, load_all_sites
from movieclaw_tracker.auth import CookieAuthProvider
from movieclaw_tracker.models import SearchQuery, TorrentCategory

# Cookie 来源于环境变量（由 tests/conftest.py 从 .env 注入），避免硬编码。
TEST_COOKIES: str = os.getenv("MOVIECLAW_TEST_COOKIES_CHDBITS", "")

pytestmark = pytest.mark.integration

_NO_COOKIE = not TEST_COOKIES.strip()
_SKIP = pytest.mark.skipif(_NO_COOKIE, reason="未配置 MOVIECLAW_TEST_COOKIES_CHDBITS，跳过集成测试")


# ── Fixtures ─────────────────────────────────────────────────────

@pytest.fixture(scope="module", autouse=True)
def _load_configs() -> None:
    """加载所有站点 YAML 配置（幂等，重复调用无副作用）。"""
    load_all_sites()


@pytest.fixture
async def site():
    """创建并完成认证的彩虹岛站点实例。"""
    s = await create_site(
        "chdbits",
        auth_provider=CookieAuthProvider(TEST_COOKIES),
    )
    await s.authenticate()
    return s


# ── 种子列表 ─────────────────────────────────────────────────────

@_SKIP
async def test_list_first_page(site):
    """获取首页种子列表，验证基本字段完整性。"""
    page = await site.list_torrents()

    assert page.items, "首页种子列表不应为空"
    assert page.page == 1

    first = page.items[0]
    assert first.torrent_id, "torrent_id 不应为空"
    assert first.title,      "title 不应为空"
    assert first.detail_url, "detail_url 不应为空"

    print(f"\n首页共 {len(page.items)} 条，估计总页数: {page.total_pages}")
    print(f"第 1 条: [{first.category}] {first.title}")
    print(f"  做种={first.seeders}  吸血={first.leechers}  大小={first.size}  免费={first.free}")


@_SKIP
async def test_list_movie_category(site):
    """按电影分类筛选，确认分类参数生效。"""
    page = await site.list_torrents(categories=[TorrentCategory.MOVIE])

    assert page.items, "电影分类列表不应为空"
    print(f"\n电影分类共 {len(page.items)} 条")
    for item in page.items[:5]:
        print(f"  [{item.site_category_id}] {item.title[:60]}")


@_SKIP
async def test_list_tv_category(site):
    """按剧集分类筛选（含 TV Series / TV Shows / Sports）。"""
    page = await site.list_torrents(categories=[TorrentCategory.TV])

    assert page.items, "剧集分类列表不应为空"
    print(f"\n剧集分类共 {len(page.items)} 条")


@_SKIP
async def test_list_page_2(site):
    """翻页测试：第 2 页与第 1 页的种子 ID 不重叠。"""
    page1 = await site.list_torrents(page=1)
    page2 = await site.list_torrents(page=2)

    if page1.items and page2.items:
        ids1 = {item.torrent_id for item in page1.items}
        ids2 = {item.torrent_id for item in page2.items}
        assert ids1.isdisjoint(ids2), "两页之间不应出现重复种子 ID"
        print(f"\n第 1 页 {len(page1.items)} 条，第 2 页 {len(page2.items)} 条，无重叠 ✓")


# ── 搜索 ─────────────────────────────────────────────────────────

@_SKIP
async def test_search_by_keyword(site):
    """关键词搜索，验证返回结构正确。"""
    result = await site.search(SearchQuery(keyword="Batman"))

    print(f"\n关键词 'Batman' 返回 {len(result.items)} 条")
    for item in result.items[:3]:
        print(f"  [{item.category}] {item.title[:60]}")

    if result.items:
        first = result.items[0]
        assert first.torrent_id, "torrent_id 不应为空"
        assert first.title,      "title 不应为空"


@_SKIP
async def test_search_by_imdb_id(site):
    """用 IMDB ID 搜索（The Dark Knight / tt0468569）。"""
    result = await site.search(SearchQuery(keyword="tt0468569"))

    print(f"\nIMDB tt0468569 返回 {len(result.items)} 条")
    for item in result.items[:5]:
        print(f"  [{item.category}] {item.title[:60]}")
        print(f"    做种={item.seeders}  大小={item.size}  免费={item.free}")


@_SKIP
async def test_search_with_category_filter(site):
    """带分类过滤的搜索。"""
    result = await site.search(
        SearchQuery(keyword="Marvel", categories=[TorrentCategory.MOVIE])
    )

    print(f"\n'Marvel' + 电影分类 返回 {len(result.items)} 条")
    for item in result.items[:3]:
        print(f"  [{item.site_category_id}] {item.title[:60]}")


# ── 用户资料 ─────────────────────────────────────────────────────

@_SKIP
async def test_get_user_profile(site):
    """获取当前登录用户的资料，验证基本字段可解析。"""
    profile = await site.get_user_profile()

    assert profile.username, "用户名不应为空"
    print(f"\n用户名:   {profile.username}")
    print(f"VIP:      {profile.vip_group}")
    print(f"上传量:   {profile.uploaded}")
    print(f"下载量:   {profile.downloaded}")
    print(f"分享率:   {profile.ratio}")
    print(f"做种数:   {profile.seeding_count}")
    print(f"下载数:   {profile.leeching_count}")
