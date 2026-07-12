"""数据扩充层——从种子标题/副标题推导结构化属性的纯函数管线。

架构定位
--------
独立于 tracker（原始抓取）与 api/db（消费）的中间层：输入两段文本，输出
``TorrentAttrs``。无 I/O、无 async、无数据库依赖，正则跑短字符串是微秒级，
搜索链路现算现返、抓取链路算好落库，两边直接内联调用。

双通道提取
----------
- **词表通道**（extractors.py）：分辨率/编码等封闭词表字段，主标题与副标题
  各跑一遍、逐字段合并、主标题优先——主标题没提取到的字段才用副标题的补；
- **模型通道**（inference.py）：片名/年份/季集/题材由小模型**双段联合推理**
  一次产出（两段互证正是年份消歧的关键，不做分段合并）。

版本机制
--------
``ENRICH_VERSION`` 是提取逻辑的版本号：词表/提取器/模型有实质改动就 +1。
落库的 attrs 带着产出它的版本号，应用启动时会把旧版本的行自动重算一遍
（见 movieclaw_api.services.enrich_backfill），升级程序即全量生效。
"""

from __future__ import annotations

import logging

from movieclaw_enrich.extractors import EXTRACTORS
from movieclaw_enrich.inference import extract_with_model
from movieclaw_enrich.models import TorrentAttrs

__all__ = ["ENRICH_VERSION", "TorrentAttrs", "enrich"]

logger = logging.getLogger("movieclaw_enrich")

# 提取逻辑版本号：词表或提取器有实质改动时 +1，驱动存量数据自动重算
# v2: 新增 media_type（影视类型）推断
# v3: 片名/年份/季集/题材切换为小模型抽取（规则版 year/season_episode 提取器下线），
#     新增 titles_zh/titles_en/episodes_total/content_type 字段
# v4: 修复 v3 两个抽取缺陷（文本末尾年份被量词守卫误杀；单字碎片混入别名），
#     v3 标记的存量行需重算
# v5: "全N集"不再展开成 episodes=[1..N]（提取层只输出观测值，覆盖解释是消费方
#     业务——matcher 判整季 pack、前端 complete 含任意一集），episodes_total 承载数值
ENRICH_VERSION = 5


def _has_value(value: object) -> bool:
    """字段是否为"真观测值"：None / 空列表 / False（remux 默认）都算空。"""
    return value not in (None, False) and value != []


def _extract_all(text: str) -> dict[str, object]:
    """对单段文本跑全部提取器，合并有值字段。单个提取器失败只跳过自己。"""
    merged: dict[str, object] = {}
    for name, extractor in EXTRACTORS:
        try:
            for key, value in extractor(text).items():  # type: ignore[operator]
                if _has_value(value):
                    merged[key] = value
        except Exception:  # noqa: BLE001 -- 提取失败绝不拖垮整条数据
            logger.warning("提取器 %s 处理文本失败，已跳过：%.80r", name, text)
    return merged


def enrich(title: str, subtitle: str = "", category: str | None = None) -> TorrentAttrs:
    """从标题（+可选副标题）提取扩充属性。

    :param title: 种子主标题（通常是场景命名，技术信息的主要来源）。
    :param subtitle: 副标题/小描述（中文 PT 常在这里写集数、音轨、中字信息）。
    :param category: 应用级一级分类值（TorrentCategory 的字符串值）。站点分类
        明确标注 movie/tv 时优先于模型判定（这两类站点极少标错）。
    :return: 结构化属性；提取不到的字段保持空值。
    """
    # 词表通道：技术字段，双段各跑一遍、主标题优先
    fields = _extract_all(title) if title else {}
    if subtitle:
        for key, value in _extract_all(subtitle).items():
            if not _has_value(fields.get(key)):
                fields[key] = value

    # 模型通道：片名/年份/季集/题材，双段联合推理一次产出。
    # 模型缺席/失败返回空字典，相关字段保持空值——绝不拖垮整条数据。
    try:
        fields.update(extract_with_model(title, subtitle) if title else {})
    except Exception:  # noqa: BLE001
        logger.warning("模型提取失败，已跳过：%.80r", title)

    attrs = TorrentAttrs(**fields)  # type: ignore[arg-type]
    # 影视类型仲裁：站点分类明确标注 movie/tv 时信站点（极少标错），
    # 否则保留模型分类头的判定（extract_with_model 已产出 media_type）
    if category in ("movie", "tv"):
        attrs.media_type = category
    return attrs
