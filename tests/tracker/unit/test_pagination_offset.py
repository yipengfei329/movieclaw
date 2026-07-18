"""分页 offset 单元测试。

NexusPHP 的 torrents.php 分页从 0 开始（``?page=0`` 是第一页）。应用层页码从 1 起算，
需要 ``page_offset = -1`` 把第 1 页映射成 ``page=0``，否则会漏掉真正第一页的全部结果
（搜索/同步都受影响）。

本测试有两层保障：
1. 配置层：确认 0 索引站点（chdbits / ssd）的 YAML 确实带上了 ``page_offset: -1``。
2. 行为层：用 Mock HttpClient 验证 ``search`` / ``list_torrents`` 真的把第 1 页发成 ``page=0``。
"""
from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import pytest

from movieclaw_tracker.frameworks.nexusphp import NexusPHPSite
from movieclaw_tracker.models import SearchQuery
from movieclaw_tracker.registry import get_site_config
from movieclaw_tracker.selectors import NexusPHPSelectors


@pytest.fixture(scope="module", autouse=True)
def _load_configs() -> None:
    from movieclaw_tracker import load_all_sites

    load_all_sites()


@pytest.mark.parametrize("site_id", ["chdbits", "ssd"])
def test_zero_indexed_sites_declare_offset(site_id: str) -> None:
    """0 索引站点的配置必须带 page_offset=-1（防止有人误删）。"""
    config = get_site_config(site_id)
    assert config.selectors is not None
    assert config.selectors.page_offset == -1, (
        f"{site_id} 是 0 索引站点，page_offset 应为 -1"
    )


def _mock_site(page_offset: int) -> tuple[NexusPHPSite, AsyncMock]:
    """构造一个带 Mock HttpClient 的 NexusPHPSite，返回 (site, client.get)。"""
    client = MagicMock()
    response = MagicMock()
    response.text = "<html><body></body></html>"  # 空页，解析出 0 行即可
    client.get = AsyncMock(return_value=response)

    site = NexusPHPSite(
        selectors=NexusPHPSelectors(page_offset=page_offset),
        category_map={},
        site_id="chdbits",
        base_url="https://example.test",
        client=client,
        auth_manager=MagicMock(),
    )
    return site, client.get


async def test_search_first_page_maps_to_zero() -> None:
    """page_offset=-1 时，应用第 1 页应发送 page=0（NexusPHP 的首页）。"""
    site, get = _mock_site(page_offset=-1)
    await site.search(SearchQuery(keyword="沙丘", page=1))
    assert get.call_args.kwargs["params"]["page"] == 0


async def test_list_second_page_maps_to_one() -> None:
    """应用第 2 页应发送 page=1，确认 offset 是线性平移而非只对第一页生效。"""
    site, get = _mock_site(page_offset=-1)
    await site.list_torrents(page=2)
    assert get.call_args.kwargs["params"]["page"] == 1
