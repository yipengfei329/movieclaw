"""多任务模型：共享编码器 + 三个头（README「多头模型」设计的实现）。

- span 头     token 级线性分类 → BIO 13 类（NER）
- media 头    [CLS] 向量 → media_type 3 类（结构轴：movie/series/other）
- content 头  [CLS] 向量 → content_type 5 类（内容轴：anime/documentary/…）

三头共享同一次编码器前向，推理成本 ≈ 单头 NER；训练时三个交叉熵直接求和
（分类任务简单、损失量级小，不会压过主任务 NER，实测无需调权）。

实现为 PreTrainedModel 子类：save_pretrained/from_pretrained 与 HF 生态兼容，
Trainer 直接可用，后续 torch.onnx.export 三输出导出也顺畅。
"""

from __future__ import annotations

import torch
from torch import nn
from transformers import AutoConfig, AutoModel, PretrainedConfig, PreTrainedModel
from transformers.utils import ModelOutput

from torrent_ner.labels import CONTENT_TYPES, LABELS, MEDIA_TYPES


class TorrentNerConfig(PretrainedConfig):
    model_type = "torrent-ner-multitask"

    def __init__(self, base_model: str = "hfl/minirbt-h256", dropout: float = 0.1, **kwargs):
        super().__init__(**kwargs)
        self.base_model = base_model
        self.dropout = dropout
        self.num_span_labels = len(LABELS)
        self.num_media_types = len(MEDIA_TYPES)
        self.num_content_types = len(CONTENT_TYPES)


class TorrentNerModel(PreTrainedModel):
    config_class = TorrentNerConfig

    def __init__(self, config: TorrentNerConfig):
        super().__init__(config)
        # from_config 只建结构不下载权重：from_pretrained 加载我们自己的
        # checkpoint 时不应再去拉基座；预训练权重的注入走 from_base()
        self.encoder = AutoModel.from_config(AutoConfig.from_pretrained(config.base_model))
        hidden = self.encoder.config.hidden_size
        self.dropout = nn.Dropout(config.dropout)
        self.span_head = nn.Linear(hidden, config.num_span_labels)
        self.media_head = nn.Linear(hidden, config.num_media_types)
        self.content_head = nn.Linear(hidden, config.num_content_types)

    @classmethod
    def from_base(cls, base_model: str) -> "TorrentNerModel":
        """从预训练基座初始化（首训入口）：编码器载基座权重，三头随机初始化。"""
        model = cls(TorrentNerConfig(base_model=base_model))
        model.encoder = AutoModel.from_pretrained(base_model)
        return model

    def forward(
        self,
        input_ids: torch.Tensor,
        attention_mask: torch.Tensor,
        token_type_ids: torch.Tensor | None = None,
        labels: torch.Tensor | None = None,
        media_label: torch.Tensor | None = None,
        content_label: torch.Tensor | None = None,
    ) -> ModelOutput:
        encoder_kwargs = {"input_ids": input_ids, "attention_mask": attention_mask}
        if token_type_ids is not None:
            encoder_kwargs["token_type_ids"] = token_type_ids
        sequence = self.dropout(self.encoder(**encoder_kwargs).last_hidden_state)

        span_logits = self.span_head(sequence)
        pooled = sequence[:, 0]  # [CLS]
        media_logits = self.media_head(pooled)
        content_logits = self.content_head(pooled)

        loss = None
        if labels is not None:
            ce = nn.CrossEntropyLoss()
            loss = ce(span_logits.view(-1, self.config.num_span_labels), labels.view(-1))
            if media_label is not None:
                loss = loss + ce(media_logits, media_label)
            if content_label is not None:
                loss = loss + ce(content_logits, content_label)

        # 字段顺序即 Trainer eval 时 predictions 元组的顺序，compute_metrics 依赖它
        return ModelOutput(
            loss=loss,
            logits=span_logits,
            media_logits=media_logits,
            content_logits=content_logits,
        )
