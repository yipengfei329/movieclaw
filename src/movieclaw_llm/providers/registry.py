"""供应商预设注册表：加载随代码分发的 yaml 预设。

与 movieclaw_tracker 的 sites/configs 同构——接入新的 OpenAI 兼容端点
（DeepSeek 官方、Ollama、vLLM…）只需加一份 yaml，不写代码。
"""

from __future__ import annotations

from functools import lru_cache
from pathlib import Path

import yaml

from movieclaw_llm.exceptions import LlmRoutingError
from movieclaw_llm.models import ProviderPreset

_PRESET_DIR = Path(__file__).parent / "presets"


@lru_cache(maxsize=1)
def _load_all() -> dict[str, ProviderPreset]:
    presets: dict[str, ProviderPreset] = {}
    for path in sorted(_PRESET_DIR.glob("*.yaml")):
        data = yaml.safe_load(path.read_text(encoding="utf-8"))
        preset = ProviderPreset.model_validate(data)
        presets[preset.id] = preset
    return presets


def get_preset(provider_type: str) -> ProviderPreset:
    presets = _load_all()
    if provider_type not in presets:
        raise LlmRoutingError(
            f"未知的供应商类型「{provider_type}」，可选：{', '.join(sorted(presets))}"
        )
    return presets[provider_type]


def list_presets() -> list[ProviderPreset]:
    return list(_load_all().values())
