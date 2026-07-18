"""文件三件套：read / write / edit。

落地细节（按参考提示词推定）：
- 路径：相对路径以工作目录为基准解析，绝对路径原样使用；
- read：文本文件，从头（或 offset，1 起）截 2000 行 / 50KB 先到为准，
  截断时在尾部附「继续读」的 offset 提示；参考里的图片附件能力我们的
  文本管线承载不了，明确不支持并在报错里说明；
- write：覆盖写，自动创建父目录，回执带字符/行数；
- edit：多处精确替换。每处 oldText 必须在**原文件**中唯一命中，多处
  编辑之间不得重叠；任一处不满足则整体不落盘（全有或全无），错误信息
  指明是第几处、什么原因，模型可自行修正后重试。
"""

from __future__ import annotations

from pathlib import Path

from movieclaw_agent.toolkit import AgentTool
from movieclaw_llm import ToolDefinition

_MAX_LINES = 2000
_MAX_BYTES = 50_000

_IMAGE_SUFFIXES = {".jpg", ".jpeg", ".png", ".gif", ".webp", ".bmp"}


def _resolve(workdir: Path, raw: str) -> Path:
    p = Path(raw)
    return p if p.is_absolute() else workdir / p


# ---------------------------------------------------------------------------
# read
# ---------------------------------------------------------------------------


def make_read_tool(workdir: Path) -> AgentTool:
    async def handler(args: dict) -> str:
        path = _resolve(workdir, args["path"])
        if not path.exists():
            raise ValueError(f"文件不存在：{path}")
        if path.is_dir():
            raise ValueError(f"{path} 是目录不是文件；用 bash 工具（如 ls）查看目录内容")
        if path.suffix.lower() in _IMAGE_SUFFIXES:
            raise ValueError("暂不支持读取图片文件，只支持文本文件")
        text = path.read_text(errors="replace")
        lines = text.splitlines()
        total = len(lines)

        offset = max(int(args.get("offset") or 1), 1)
        limit = int(args.get("limit") or _MAX_LINES)
        if offset > total and total > 0:
            raise ValueError(f"offset 超出范围：文件共 {total} 行，offset={offset}")

        window = lines[offset - 1 : offset - 1 + min(limit, _MAX_LINES)]
        # 字节上限：逐行累加，超限即停（保证不超 50KB）
        picked: list[str] = []
        size = 0
        for line in window:
            size += len(line.encode(errors="replace")) + 1
            if picked and size > _MAX_BYTES:
                break
            picked.append(line)
        end = offset - 1 + len(picked)

        body = "\n".join(picked)
        if end < total:
            body += (
                f"\n（文件共 {total} 行，本次返回第 {offset}-{end} 行；"
                f"继续读取请传 offset={end + 1}）"
            )
        return body if body else "（空文件）"

    return AgentTool(
        definition=ToolDefinition(
            name="read",
            description=(
                "读取文本文件内容。"
                f"单次最多返回 {_MAX_LINES} 行或 50KB（先到为准），"
                "大文件用 offset/limit 分段读取，直到读完为止。不支持图片。"
            ),
            parameters={
                "type": "object",
                "properties": {
                    "path": {"type": "string", "description": "文件路径（相对或绝对）"},
                    "offset": {"type": "number", "description": "起始行号（1 起，可选）"},
                    "limit": {"type": "number", "description": "最多读取的行数（可选）"},
                },
                "required": ["path"],
            },
        ),
        handler=handler,
    )


# ---------------------------------------------------------------------------
# write
# ---------------------------------------------------------------------------


def make_write_tool(workdir: Path) -> AgentTool:
    async def handler(args: dict) -> str:
        path = _resolve(workdir, args["path"])
        content: str = args["content"]
        path.parent.mkdir(parents=True, exist_ok=True)
        existed = path.exists()
        path.write_text(content)
        action = "已覆盖" if existed else "已创建"
        return f"{action} {path}（{len(content)} 字符，{len(content.splitlines())} 行）"

    return AgentTool(
        definition=ToolDefinition(
            name="write",
            description="写入文件：不存在则创建（自动创建父目录），存在则整体覆盖。",
            parameters={
                "type": "object",
                "properties": {
                    "path": {"type": "string", "description": "文件路径（相对或绝对）"},
                    "content": {"type": "string", "description": "要写入的完整内容"},
                },
                "required": ["path", "content"],
            },
        ),
        handler=handler,
    )


# ---------------------------------------------------------------------------
# edit
# ---------------------------------------------------------------------------


def make_edit_tool(workdir: Path) -> AgentTool:
    async def handler(args: dict) -> str:
        path = _resolve(workdir, args["path"])
        if not path.exists():
            raise ValueError(f"文件不存在：{path}")
        original = path.read_text(errors="replace")

        # 先对**原文件**逐处定位并校验（唯一、互不重叠），全部通过才落盘
        spans: list[tuple[int, int, str]] = []
        for i, edit in enumerate(args["edits"], start=1):
            old, new = edit["oldText"], edit["newText"]
            if not old:
                raise ValueError(f"第 {i} 处编辑的 oldText 不能为空")
            count = original.count(old)
            if count == 0:
                raise ValueError(
                    f"第 {i} 处编辑的 oldText 未在文件中找到，请先 read 确认当前内容"
                )
            if count > 1:
                raise ValueError(
                    f"第 {i} 处编辑的 oldText 在文件中匹配到 {count} 处，"
                    "必须唯一；请扩大 oldText 的上下文使其唯一"
                )
            start = original.index(old)
            spans.append((start, start + len(old), new))

        spans.sort(key=lambda s: s[0])
        for (_, prev_end, _), (cur_start, _, _) in zip(spans, spans[1:], strict=False):
            if cur_start < prev_end:
                raise ValueError(
                    "编辑区域相互重叠：请把影响同一区域的多处修改合并为一处编辑"
                )

        result: list[str] = []
        cursor = 0
        for start, end, new in spans:
            result.append(original[cursor:start])
            result.append(new)
            cursor = end
        result.append(original[cursor:])
        path.write_text("".join(result))
        return f"已完成 {len(spans)} 处替换：{path}"

    return AgentTool(
        definition=ToolDefinition(
            name="edit",
            description=(
                "对单个文件做精确文本替换。每处 edits[].oldText 必须与原文件中"
                "唯一的一段内容完全一致，且各处编辑不得重叠；影响同一区域或相邻"
                "行的修改应合并为一处。不要为连接相距很远的改动而包含大段未变内容。"
                "任一处不满足则整体不生效。"
            ),
            parameters={
                "type": "object",
                "properties": {
                    "path": {"type": "string", "description": "文件路径（相对或绝对）"},
                    "edits": {
                        "type": "array",
                        "description": "一处或多处替换；每处都基于原文件匹配，而非逐步应用",
                        "items": {
                            "type": "object",
                            "properties": {
                                "oldText": {
                                    "type": "string",
                                    "description": "被替换的原文（须在原文件中唯一命中）",
                                },
                                "newText": {"type": "string", "description": "替换后的新文本"},
                            },
                            "required": ["oldText", "newText"],
                        },
                        "minItems": 1,
                    },
                },
                "required": ["path", "edits"],
            },
        ),
        handler=handler,
    )
