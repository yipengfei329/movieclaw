"""把训练好的多任务模型导出为 ONNX（三输出）并做 int8 动态量化。

自定义三头模型走 torch.onnx.export（optimum 的任务封装只认标准单头），
量化用 onnxruntime 原生 quantize_dynamic。产物目录内含 tokenizer 和
labels.json（标签/枚举表），线上推理只依赖这个目录 + onnxruntime。

    ml/.venv/bin/python ml/torrent_ner/export.py
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import torch
from transformers import AutoTokenizer

from torrent_ner.labels import CONTENT_TYPES, LABELS, MAX_LENGTH, MEDIA_TYPES
from torrent_ner.model import TorrentNerModel


def main() -> None:
    parser = argparse.ArgumentParser(description="导出 ONNX 三输出 + int8 量化")
    parser.add_argument("--model", default="ml/artifacts/torrent-ner/model", help="训练产物目录")
    parser.add_argument("--out", default="ml/artifacts/torrent-ner/onnx", help="导出目录")
    args = parser.parse_args()

    out = Path(args.out)
    out.mkdir(parents=True, exist_ok=True)

    model = TorrentNerModel.from_pretrained(args.model)
    model.eval()
    tokenizer = AutoTokenizer.from_pretrained(args.model)
    tokenizer.save_pretrained(out)

    # 线上运行时的全部枚举信息，一个文件说清，避免推理端 import 训练代码
    (out / "labels.json").write_text(
        json.dumps(
            {
                "labels": list(LABELS),
                "media_types": list(MEDIA_TYPES),
                "content_types": list(CONTENT_TYPES),
                "max_length": MAX_LENGTH,
                "base_model": model.config.base_model,
            },
            ensure_ascii=False,
            indent=2,
        ),
        encoding="utf-8",
    )

    print("导出 ONNX（三输出）...")
    sample = tokenizer("Sample Title 2025 1080p", "示例副标题", return_tensors="pt")
    dynamic = {0: "batch", 1: "seq"}
    fp32_path = out / "model.onnx"
    torch.onnx.export(
        model,
        (sample["input_ids"], sample["attention_mask"], sample["token_type_ids"]),
        fp32_path,
        input_names=["input_ids", "attention_mask", "token_type_ids"],
        output_names=["span_logits", "media_logits", "content_logits"],
        dynamic_axes={
            "input_ids": dynamic,
            "attention_mask": dynamic,
            "token_type_ids": dynamic,
            "span_logits": dynamic,
            "media_logits": {0: "batch"},
            "content_logits": {0: "batch"},
        },
        opset_version=17,
        dynamo=False,  # 经典导出器：BERT 系结构成熟稳定，避免 dynamo 路径的新坑
    )

    print("int8 动态量化...")
    from onnxruntime.quantization import QuantType, quantize_dynamic

    int8_path = out / "model.int8.onnx"
    quantize_dynamic(fp32_path, int8_path, weight_type=QuantType.QInt8)

    for f in sorted(out.glob("*.onnx")):
        print(f"  {f.name}: {f.stat().st_size / 1024 / 1024:.1f} MB")
    print(f"完成 → {out}，下一步: python ml/torrent_ner/evaluate.py")


if __name__ == "__main__":
    main()
