"""微调多任务模型（共享编码器 + NER 头 + media/content 两个分类头）。

需要训练环境（ml/.venv，见 ml/requirements.txt）。国内先设镜像：

    export HF_ENDPOINT=https://hf-mirror.com
    ml/.venv/bin/python ml/torrent_ner/train.py                # 默认 MiniRBT
    ml/.venv/bin/python ml/torrent_ner/train.py --base hfl/chinese-electra-180g-small-discriminator
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import numpy as np
import torch
from transformers import (
    AutoTokenizer,
    DataCollatorForTokenClassification,
    Trainer,
    TrainingArguments,
)

from torrent_ner.dataio import load_split
from torrent_ner.encoding import build_char_tags, token_label_ids
from torrent_ner.labels import CONTENT_TYPE2ID, ID2LABEL, MAX_LENGTH, MEDIA_TYPE2ID
from torrent_ner.model import TorrentNerModel


def encode_items(items: list[dict], tokenizer) -> list[dict]:
    """双段编码（title, subtitle），产出 NER 标签 + 两个分类标签。"""
    encoded = []
    for item in items:
        title, subtitle = item["title"], item.get("subtitle", "")
        # 空副标题传占位空格：保持双段结构稳定，避免空串在部分 tokenizer 下出错
        enc = tokenizer(
            title,
            subtitle or " ",
            truncation=True,
            max_length=MAX_LENGTH,
            return_offsets_mapping=True,
        )
        char_tags = (
            build_char_tags(len(title), [s for s in item["spans"] if s["source"] == "title"]),
            build_char_tags(len(subtitle), [s for s in item["spans"] if s["source"] == "subtitle"]),
        )
        labels = token_label_ids(enc["offset_mapping"], enc.sequence_ids(), char_tags)
        encoded.append(
            {
                "input_ids": enc["input_ids"],
                "attention_mask": enc["attention_mask"],
                **({"token_type_ids": enc["token_type_ids"]} if "token_type_ids" in enc else {}),
                "labels": labels,
                # 旧数据（v7 之前）无分类字段时兜底 other——两轴的残差项
                "media_label": MEDIA_TYPE2ID[item.get("media_type", "other")],
                "content_label": CONTENT_TYPE2ID[item.get("content_type", "other")],
            }
        )
    return encoded


class MultiTaskCollator:
    """token 标签走标准 padding 逻辑，两个整条分类标签原样成批。"""

    def __init__(self, tokenizer):
        self.base = DataCollatorForTokenClassification(tokenizer)

    def __call__(self, features: list[dict]) -> dict:
        features = [dict(f) for f in features]  # 拷贝：Trainer 每个 epoch 重用同一批 dict
        media = torch.tensor([f.pop("media_label") for f in features])
        content = torch.tensor([f.pop("content_label") for f in features])
        batch = self.base(features)
        batch["media_label"] = media
        batch["content_label"] = content
        return batch


def make_compute_metrics():
    from seqeval.metrics import f1_score, precision_score, recall_score

    def compute_metrics(eval_pred):
        # 顺序由 model.forward 的输出字段序 / TrainingArguments.label_names 决定
        span_logits, media_logits, content_logits = eval_pred.predictions
        span_labels, media_labels, content_labels = eval_pred.label_ids

        predictions = np.argmax(span_logits, axis=-1)
        true_seqs, pred_seqs = [], []
        for pred_row, label_row in zip(predictions, span_labels):
            true_seqs.append([ID2LABEL[l] for l in label_row if l != -100])
            pred_seqs.append([ID2LABEL[p] for p, l in zip(pred_row, label_row) if l != -100])
        return {
            "precision": precision_score(true_seqs, pred_seqs),
            "recall": recall_score(true_seqs, pred_seqs),
            "f1": f1_score(true_seqs, pred_seqs),
            "media_acc": float((np.argmax(media_logits, -1) == media_labels).mean()),
            "content_acc": float((np.argmax(content_logits, -1) == content_labels).mean()),
        }

    return compute_metrics


def main() -> None:
    parser = argparse.ArgumentParser(description="微调 torrent NER 多任务模型")
    parser.add_argument("--data", default="ml/data/labeled/annotations.jsonl")
    parser.add_argument("--base", default="hfl/minirbt-h256", help="基座模型")
    parser.add_argument("--out", default="ml/artifacts/torrent-ner", help="输出目录")
    # 12 轮实证：5 轮明显欠拟合（dev F1 0.73），12 轮 0.87 且未见过拟合迹象
    parser.add_argument("--epochs", type=int, default=12)
    parser.add_argument("--batch-size", type=int, default=32)
    parser.add_argument("--lr", type=float, default=5e-5)
    args = parser.parse_args()

    tokenizer = AutoTokenizer.from_pretrained(args.base)
    model = TorrentNerModel.from_base(args.base)

    # clean_only：带 review 标记的记录 span 不完整（假阴性），排除出训练/开发集
    train_items = load_split(args.data, "train", clean_only=True)
    dev_items = load_split(args.data, "dev", clean_only=True)
    quarantined = len(load_split(args.data, "train")) - len(train_items)
    print(f"训练集 {len(train_items)} 条 / 开发集 {len(dev_items)} 条（测试集留给 evaluate.py）")
    if quarantined:
        print(f"另有 {quarantined} 条训练样本带 review 标记被隔离（span 不完整，待人工修正）")
    if not train_items or not dev_items:
        sys.exit("训练/开发集为空，请先完成标注（annotate.py）并校验（validate.py）")

    trainer = Trainer(
        model=model,
        args=TrainingArguments(
            output_dir=f"{args.out}/checkpoints",
            num_train_epochs=args.epochs,
            per_device_train_batch_size=args.batch_size,
            learning_rate=args.lr,
            eval_strategy="epoch",
            save_strategy="epoch",
            load_best_model_at_end=True,
            metric_for_best_model="f1",
            # 三组标签都要在 eval 时传给 compute_metrics，顺序与解包一致
            label_names=["labels", "media_label", "content_label"],
            logging_steps=20,
            save_total_limit=2,
            report_to=[],
        ),
        train_dataset=encode_items(train_items, tokenizer),
        eval_dataset=encode_items(dev_items, tokenizer),
        data_collator=MultiTaskCollator(tokenizer),
        compute_metrics=make_compute_metrics(),
    )
    trainer.train()

    final_dir = f"{args.out}/model"
    trainer.save_model(final_dir)
    tokenizer.save_pretrained(final_dir)
    metrics = trainer.evaluate()
    print(f"开发集指标: {metrics}")
    print(f"模型已保存 → {final_dir}，下一步: python ml/torrent_ner/export.py")


if __name__ == "__main__":
    main()
