"""测试全局配置：从仓库根目录的 .env 加载环境变量。

集成测试使用的真实站点 Cookie 通过环境变量传入，避免敏感凭据进入
git 历史。本地开发时复制 .env.example 为 .env 并填写即可，CI 环境
中保持环境变量为空，相关测试会自动跳过。
"""
from __future__ import annotations

import os
from pathlib import Path

_PROJECT_ROOT = Path(__file__).resolve().parent.parent
_ENV_FILE = _PROJECT_ROOT / ".env"


def _load_env_file(path: Path) -> None:
    """加载简单的 .env 文件，将 KEY=VALUE 注入 os.environ。

    设计要点：
    - 已存在的环境变量优先，不会被 .env 覆盖（CI/手动 export 的值更权威）；
    - 仅支持 KEY=VALUE 形式，忽略空行与 # 开头的注释；
    - 自动剥离值两端的成对单/双引号，方便粘贴含分号的 cookie 串。

    避免新增依赖（python-dotenv），保持测试侧零依赖。
    """
    if not path.is_file():
        return
    for raw_line in path.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#"):
            continue
        if "=" not in line:
            continue
        key, _, value = line.partition("=")
        key = key.strip()
        if not key:
            continue
        value = value.strip()
        if len(value) >= 2 and value[0] == value[-1] and value[0] in ("'", '"'):
            value = value[1:-1]
        os.environ.setdefault(key, value)


_load_env_file(_ENV_FILE)
