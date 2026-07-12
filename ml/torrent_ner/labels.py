"""标签 schema——标注、训练、评估、导出全流程共用的单一事实源。

改动标签集时只改这里，其余环节自动跟随。改动后已有标注数据需要重新校验
（跑 validate.py），已训模型需要重训。

模型有两类输出，机制不同：
1. **span 字段（token 级 / NER）**：在原文里圈出实体边界，见 FIELDS。
2. **整条分类（document 级）**：对整条种子给类别，共享编码器上的序列分类头。
   有两条**正交**的轴，各一个头，别混成一个枚举：
   - MEDIA_TYPES  结构轴：单部 vs 分集（"电影还是剧集"）
   - CONTENT_TYPES 内容轴：题材（真人/动漫/综艺/纪录片）
   正交的意义："动漫剧场版" = movie + anime，"动漫番剧" = series + anime，
   两轴分开存永不丢信息，追剧/是否完结的逻辑只看 MEDIA_TYPES 保持干净。

span 字段语义（与标注规范 annotate.py 中的提示词保持一致）：
- TITLE_ZH       中文片名及其中文别名（不含季数/集数字样）
- TITLE_EN       外文片名及外文别名（英文为主，也涵盖其它拉丁字母写法）
- YEAR           发行年份字样（若年份仅作为片名一部分出现则不标）
- SEASON         季数表达（"S01"、"第三季"）
- EPISODE        当前集号 / 集号区间（"E10"、"第50集"、"E01-E12"）——指这是第几集
- EPISODE_TOTAL  总集数 / 完结合集表达（"全12集"、"12集全"、"全26话"）——指这是共 N 集的合集

EPISODE vs EPISODE_TOTAL、以及 MEDIA_TYPE 的判定都由模型完成（它擅长理解语义），
不靠下游正则事后猜——这正是"让模型理解、代码只做机械转换"的分工体现。
"""

from __future__ import annotations

# 实体字段，顺序即 label id 顺序（追加新字段务必放在末尾，保持旧 id 稳定）
FIELDS: tuple[str, ...] = ("TITLE_ZH", "TITLE_EN", "YEAR", "SEASON", "EPISODE", "EPISODE_TOTAL")

# span 的来源字段：0 段是英文种子名，1 段是中文副标题（与双段编码顺序一致）
SOURCES: tuple[str, ...] = ("title", "subtitle")

# 结构轴（document 级，序列分类头）。按作品结构分，不按题材：
# - movie   单部影片（含剧场版、电影、纪录片单片）
# - series  分集连续作品（电视剧、剧集、综艺、番剧、多集合集）
# - other   非影视内容（软件/音乐/体育/游戏/电子书/MV）——与"负样本无 span"一致
# 顺序即 class id 顺序，追加新类只能放末尾（保持旧 id 稳定）
# collection 追加在末尾保持旧 id 稳定（v10 新增）：多部独立作品的打包
# （"六部合集"、导演电影合集、系列全集）；单一作品的多季/全集打包仍是 series
MEDIA_TYPES: tuple[str, ...] = ("movie", "series", "other", "collection")
MEDIA_TYPE2ID: dict[str, int] = {name: i for i, name in enumerate(MEDIA_TYPES)}
MEDIA_TYPE_ID2NAME: dict[int, str] = {i: name for i, name in enumerate(MEDIA_TYPES)}

# 内容轴（document 级，另一个序列分类头）。只标特殊题材，与结构轴正交：
# - anime        动画 / 动漫（番/剧场版/OVA/字幕社等线索）
# - documentary  纪录片（纪录/探索/BBC/NHK 等）
# - variety      综艺 / 真人秀（"第N期"、综艺、主持）
# - music        音乐（专辑/单曲/MV/演唱会；FLAC/APE/24bit 等无损音频线索）
# - other        普通真人影视 + 软件/体育/游戏/电子书等其它非影视
# 不设 live_action：普通真人影视靠 media_type(movie/series) 已能识别，是不是影视
# 也由 media_type 区分（movie/series=影视，other=非影视），故内容轴无需重复它。
# music 与结构正交：纯音频专辑 media_type=other，演唱会/MV 影像 media_type 按结构。
CONTENT_TYPES: tuple[str, ...] = ("anime", "documentary", "variety", "music", "other")
CONTENT_TYPE2ID: dict[str, int] = {name: i for i, name in enumerate(CONTENT_TYPES)}
CONTENT_TYPE_ID2NAME: dict[int, str] = {i: name for i, name in enumerate(CONTENT_TYPES)}

# BIO 标签表："O" 固定为 0，之后按 FIELDS 顺序展开 B-/I- 对
LABELS: tuple[str, ...] = ("O",) + tuple(
    f"{prefix}-{field}" for field in FIELDS for prefix in ("B", "I")
)
LABEL2ID: dict[str, int] = {label: i for i, label in enumerate(LABELS)}
ID2LABEL: dict[int, str] = {i: label for i, label in enumerate(LABELS)}

# 双段编码（title, subtitle）的 token 上限。队列长度 p99≈202 字符，128 会截断
# 约 10% 样本的副标题尾部；训练/推理都是动态 padding，加大上限对典型样本零成本。
MAX_LENGTH = 256

# 跨字段 span 冲突时的优先级：结构化字段 > 片名（数值小者优先保留）
FIELD_PRIORITY: dict[str, int] = {
    "YEAR": 0,
    "SEASON": 1,
    "EPISODE": 2,
    "EPISODE_TOTAL": 3,
    "TITLE_EN": 4,
    "TITLE_ZH": 5,
}
