# ml/ — 种子名信息抽取模型

从 PT 种子的英文种子名（`title`）+ 中文副标题（`subtitle`）中抽取
**片名（中/英）、年份、季数、集数** 的 NER 模型。训练数据、训练方法、
模型迭代全部在本目录内闭环，与主项目运行时解耦。

## 设计决策（定案记录）

| 决策点 | 结论 |
|--------|------|
| 任务形式 | **多任务单模型**：共享编码器 + 三个头 ①token BIO 序列标注（NER）②序列分类 media_type ③序列分类 content_type。均判别式，不用生成式模型 |
| 基座模型 | `hfl/chinese-lert-small`（15k 数据六模型对决胜出：NER 宏 F1 0.921，片名两轴领先 minirbt 2-3 分；int8 15.6MB / p50 3.3ms)。对决记录：minirbt-h256 0.909/1.7ms、h288 0.918/2.0ms、electra-small 0.895、rbt3 0.905（偏科：集数强片名弱）、roberta-wwm-ext fp32≈0.95 为天花板但 26.8ms 且 **int8 动态量化崩塌**（大基座须量化校准/QAT） |
| 输入 | 双段编码 `(title, subtitle)`，max_length=256 —— 两段互证是年份消歧的关键 |
| span 标签 | BIO × {TITLE_ZH, TITLE_EN, YEAR, SEASON, EPISODE, EPISODE_TOTAL} + O，共 13 类，见 [torrent_ner/labels.py](torrent_ner/labels.py) |
| 整条分类（两条正交轴） | 结构轴 media_type ∈ {movie, series, other}（"电影还是剧集"）；内容轴 content_type ∈ {anime, documentary, variety, music, other}（只标特殊题材，普通真人影视归 other）。两轴独立，"动漫剧场版"=movie+anime、"纯音频专辑"=other+music、"演唱会蓝光"=movie+music，永不塌缩 |
| 分工原则 | **模型负责理解**（片名边界、当前集 vs 总集数等语义判定），**代码只做机械转换**（span→int、CJK 数字、字符定位）。语义分类靠标签让模型学，不靠下游正则事后猜 |
| 任务头 | 线性分类头，不加 CRF；BIO 非法序列在解码时确定性修复 |
| 部署形态 | ONNX + int8 动态量化（~11MB），onnxruntime CPU 推理，单条 3-8ms |
| 分工边界 | 模型只圈 span，数值解析（"第三季"→2）由确定性代码做；分辨率/编码等封闭词表字段**永远走规则**（`movieclaw_enrich`），模型不碰 |

## 目录结构

```
ml/
├── torrent_ner/          # 全部管线代码（labels.py 是标签 schema 的单一事实源）
├── data/
│   ├── sources/          # 外部来源数据；external_pool.jsonl.gz（17 站 ~7.3 万条
│   │                     #   精简池，由 extract_source.py 从外部 517MB 库抽取，
│   │                     #   原库已弃，此文件例外入 git）
│   ├── raw/              # 抽样生成的待标注队列（不提交，可随时重新生成）
│   └── labeled/          # 标注数据（★ 长期资产，必须提交 git）
├── artifacts/            # 训练与导出产物（不提交，正式版本发 GitHub Release）
├── tests/                # span/BIO 转换的单元测试
└── requirements.txt      # 训练环境依赖（数据管线零依赖）
```

## 工作流

```
1. 抽样    python ml/torrent_ner/sample.py            # 零依赖，主项目环境即可
           # 合并 site_torrent + 外部精简池，17 站分层各 400 条，确定性乱序
2. 标注    python ml/torrent_ner/annotate.py --limit 50   # 蒸馏标注，先小批试跑
           # --engine claude（默认，sonnet-5）或 --engine codex（gpt-5.6，走 ~/.codex 配置）
           → grep '"review"' 复核定位失败的样本；确认标注规范后再跑全量
3. 校验    python ml/torrent_ner/validate.py          # 结构校验 + 统计，训练前必跑
--- 以下需要训练环境（requirements.txt）---
4. 训练    HF_ENDPOINT=https://hf-mirror.com python ml/torrent_ner/train.py
5. 导出    python ml/torrent_ner/export.py            # ONNX + int8（发布 x86 版加 --arch avx2）
6. 终检    python ml/torrent_ner/evaluate.py          # 测试集逐字段 span F1 + 延迟
```

数据集切分按 id 稳定哈希（80/10/10，见 `dataio.split_of`）：增量补数据、
反复重训，同一样本永远落同一集合，测试集不会被污染。

## 标注的稳定性与留存（annotate.py 内建，无需额外操作）

- **断点续标**：已完成 id 自动跳过，任何时刻 Ctrl-C / 断电都安全；
- **残行自修复**：进程被杀写了半行，下次启动自动剔除坏行并重标该样本；
- **进程锁**：同一输出文件只允许一个标注进程（`.lock` 文件记 pid，陈锁自动接管）；
- **批次重试 + 熔断**：每批失败重试一次；连续 3 批失败（登录态过期/额度耗尽）
  立即退出止损，修复后重跑自动续接；
- **逐条溯源**：每条记录带 `annotator`（引擎:模型）、`prompt_version`、
  `annotated_at`——审计、按版本重标、双引擎对比都靠它；
- **review 样本自动隔离**：带 `review` 标记（模型抽取子串定位失败，span 可能
  不完整）的记录，训练/评估时经 `load_split(clean_only=True)` 自动排除——
  入训会教模型漏抽、入评会误判假阳性。人工修正后删除/清空 `review` 即自动回流；
- **留存**：labeled/*.jsonl 进 git，每完成一个标注里程碑就 commit 一次
  （这是唯一不可再生的资产；raw/ 和模型产物都可重建）。

## 数据格式

`data/labeled/*.jsonl`，每行一条，span 是**字符级半开区间**（与分词器无关，
换基座不用重标）：

```json
{"id": "chdbits:12345",
 "title": "Tomb of Fallen Gods 2025 S03E50 2160p WEB-DL ...",
 "subtitle": "神墓 年番/神墓3/神墓 第三季 | 第50集 | ...",
 "spans": [
   {"source": "title",    "field": "TITLE_EN", "start": 0,  "end": 19},
   {"source": "title",    "field": "YEAR",     "start": 20, "end": 24},
   {"source": "subtitle", "field": "TITLE_ZH", "start": 0,  "end": 2}
 ],
 "media_type": "series",
 "content_type": "anime",
 "review": ["可选：标注器留下的待复核原因"]}
```

> content_type 只标 anime/documentary/variety/music 四个特殊题材，普通真人影视与
> 其它非影视都归 other——"是不是影视"由 media_type 区分，故内容轴不重复 live_action。
> music 与结构正交：纯音频专辑=other+music，演唱会/MV 影像=movie+music。

- `spans`：token 级 NER 的监督信号（字符 span）。
- `media_type` / `content_type`：整条分类的监督信号，**不是 span**，是对整条
  种子的两个正交判断（结构 / 题材），训练时各喂给一个序列分类头。

### 多头模型（训练时落地）

一个共享编码器（MiniRBT）+ 三个头：token 分类头出 BIO、两个序列分类头（接
`[CLS]`/pooled 输出）分别出 media_type、content_type。三头共享同一次前向，两个
分类近乎零额外推理成本。当前 `train.py`/`export.py` 是单头（NER）骨架，装训练
环境后改成自定义多头模型（`AutoModel` + 三个 head + 加权损失），ONNX 同时导出
三个输出。分类标签从第一条标注就已入库，改多头时无需重新标注。

## 长期迭代守则

- **标注规范 = annotate.py 里的提示词**。改提示词就是改标注标准，旧数据要
  重标或按批次隔离；labels.py 加新字段只能追加在 FIELDS 末尾（保持旧 id 稳定）。
  当前 `PROMPT_VERSION=3`：v1/v2 的试标数据是旧标签 schema（episode 未拆分），
  正式全量标注前应清空 labeled/ 重标（试标量小，不值得迁移）。
- **非影视内容（软件/音乐/体育/MV 等）不标任何 span**——负样本教模型对
  域外输入保持沉默，与"绝不返回猜测值"的管线约定一致。
- **接入新站点后**：sample.py 重抽（新站自动纳入）→ annotate 增量标注（已标
  id 自动跳过）→ 重训。测试集 F1 掉了说明新格式带来了分布漂移，属预期。
- **模型版本**：发布产物命名 `torrent-ner-v{N}.onnx`，附当次 evaluate.py 的
  指标；线上通过 GitHub Release 分发，不进 git。
- **错例回流**：线上规则与模型分歧、或用户反馈的坏例，追加到 labeled/ 里
  （人工给正确标注）是最划算的精度投资。
- **双引擎交叉质检**：同一批样本分别用 claude / codex 各标一次（写入不同
  文件），diff 出分歧样本人工裁决——比通读全部标注省力得多，适合在全量
  标注后抽 10% 做质量审计。
- **已接入线上**（2026-07-13）：`movieclaw_enrich.inference` 消费本目录产物
  （片名/年份/季集/双轴分类走模型，封闭词表技术字段仍走规则）。模型文件部署到
  `data/models/torrent-ner/`（model.int8.onnx + tokenizer.json + labels.json，
  可用 MOVIECLAW_NER_DIR 覆盖路径），缺席时优雅降级。**改动模型/重训后**：
  export.py 产物覆盖部署目录 + 主项目 `ENRICH_VERSION` +1 触发存量重算。
- 待办：TMDB 匹配回填规范片名/年份；线上低置信样本回流标注。
