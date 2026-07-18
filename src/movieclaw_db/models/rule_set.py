from __future__ import annotations

from sqlalchemy import JSON, Column
from sqlmodel import Field

from movieclaw_db.models.base import TimestampMixin


class RuleSet(TimestampMixin, table=True):
    """规则组——"什么样的载体可接受"的可复用参数包。

    职责边界（docs/design/subscription-plan.md 数据模型汇总）：
    - 回答"候选可不可接受"（硬过滤）与"谁更好"（偏好排序），预留洗版 cutoff；
    - **纯参数**：判断逻辑全在 movieclaw_matcher，本表一行不含行为；
    - 订阅只持引用、不做 per-订阅 override——想微调就复制一个规则组；
    - 被订阅引用时禁删（服务层保证）；修改只影响之后的评估，不追溯已 grabbed。

    ``spec`` 的 schema 是 ``movieclaw_matcher.RuleSetSpec``（此处存其 JSON 序列化，
    db 层不反向依赖 matcher 包）。空 spec = 全不限，即默认规则组的形态。
    """

    __tablename__ = "rule_set"

    id: int | None = Field(default=None, primary_key=True)

    name: str = Field(index=True, unique=True, description="规则组名（唯一，展示用）")
    is_default: bool = Field(default=False, description="新订阅默认选中；全表至多一个 True")
    spec: dict = Field(
        default_factory=dict,
        sa_column=Column(JSON, nullable=False),
        description="RuleSetSpec 的 JSON；空对象=全不限",
    )
