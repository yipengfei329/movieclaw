"""M-Team（馒头）站点集成测试。

M-Team 走官方 REST API，认证使用 API-Key（请求头 x-api-key），
而非 cookie。因此这里用 ApiKeyAuthProvider。

使用方法
--------
1. 登录 https://kp.m-team.cc → 控制台 → 实验室 → 密钥(API Key)，创建并复制。
2. 在仓库根目录的 .env 中填写::

       MOVIECLAW_TEST_APIKEY_MTEAM="你的-api-key"

   .env 已在 .gitignore 中，密钥不会进 git。
3. 运行测试::

       pytest tests/tracker/sites/test_mteam.py -v -s

注意
----
- 所有 case 依赖真实 API-Key，未填写时自动跳过，不影响 CI。
- 测试只读取远端数据，不会下载种子文件。
"""
from __future__ import annotations

import os

import pytest

from movieclaw_tracker import ApiKeyAuthProvider, create_site, load_all_sites
from movieclaw_tracker.models import SearchQuery, TorrentCategory

# API-Key 来源于环境变量（由 tests/conftest.py 从 .env 注入），避免硬编码。
TEST_APIKEY: str = os.getenv("MOVIECLAW_TEST_APIKEY_MTEAM", "")

pytestmark = pytest.mark.integration

_NO_KEY = not TEST_APIKEY.strip()
_SKIP = pytest.mark.skipif(_NO_KEY, reason="未配置 MOVIECLAW_TEST_APIKEY_MTEAM，跳过集成测试")


# ── Fixtures ─────────────────────────────────────────────────────

@pytest.fixture(scope="module", autouse=True)
def _load_configs() -> None:
    """加载所有站点 YAML 配置（幂等，重复调用无副作用）。"""
    load_all_sites()


@pytest.fixture
async def site():
    """创建并完成认证的 M-Team 站点实例。"""
    s = await create_site(
        "mteam",
        auth_provider=ApiKeyAuthProvider(TEST_APIKEY),
    )
    await s.authenticate()
    return s


# ── 种子列表 ─────────────────────────────────────────────────────

@_SKIP
async def test_list_first_page(site):
    """获取首页种子列表，验证基本字段完整性。"""
    page = await site.list_torrents(categories=[TorrentCategory.MOVIE])

    assert page.items, "电影首页种子列表不应为空"
    assert page.page == 1

    first = page.items[0]
    assert first.torrent_id, "torrent_id 不应为空"
    assert first.title,      "title 不应为空"
    assert first.detail_url, "detail_url 不应为空"

    print(f"\n首页共 {len(page.items)} 条，估计总页数: {page.total_pages}")
    print(f"第 1 条: [{first.category}] {first.title}")
    print(f"  做种={first.seeders}  吸血={first.leechers}  大小={first.size}  免费={first.free}")


@_SKIP
async def test_list_tv_category(site):
    """按剧集分类筛选，确认分类参数生效。"""
    page = await site.list_torrents(categories=[TorrentCategory.TV])

    assert page.items, "剧集分类列表不应为空"
    print(f"\n剧集分类共 {len(page.items)} 条")
    for item in page.items[:5]:
        print(f"  [{item.site_category_id}] {item.title[:60]}")


# ── 搜索 ─────────────────────────────────────────────────────────

@_SKIP
async def test_search_by_keyword(site):
    """关键词搜索，验证返回结构正确。"""
    result = await site.search(SearchQuery(keyword="Batman", categories=[TorrentCategory.MOVIE]))

    print(f"\n关键词 'Batman' 返回 {len(result.items)} 条")
    for item in result.items[:3]:
        print(f"  [{item.category}] {item.title[:60]}  免费={item.free}")

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
        print(f"  [{item.category}] {item.title[:60]}  大小={item.size}")


# ── 用户资料 ─────────────────────────────────────────────────────

@_SKIP
async def test_get_user_profile(site):
    """获取当前登录用户的资料，验证基本字段可解析。"""
    profile = await site.get_user_profile()

    assert profile.username, "用户名不应为空"
    print(f"\n用户名:   {profile.username}")
    print(f"用户组:   {profile.user_class}")
    print(f"上传量:   {profile.uploaded}")
    print(f"下载量:   {profile.downloaded}")
    print(f"分享率:   {profile.ratio}")
    print(f"魔力值:   {profile.bonus}")
    print(f"做种数:   {profile.seeding_count}")
    print(f"下载数:   {profile.leeching_count}")


# ── 种子详情 ─────────────────────────────────────────────────────

@_SKIP
async def test_get_torrent_detail(site):
    """从搜索结果取第一条，获取其详情。"""
    result = await site.search(SearchQuery(keyword="Batman", categories=[TorrentCategory.MOVIE]))
    if not result.items:
        pytest.skip("搜索无结果，无法测试详情")

    detail = await site.get_torrent_detail(result.items[0].detail_url)
    assert detail.torrent_id, "详情 torrent_id 不应为空"
    assert detail.title,      "详情 title 不应为空"
    print(f"\n详情: {detail.title}")
    print(f"  分类={detail.category}  大小={detail.size}  下载系数={detail.download_volume_factor}")
