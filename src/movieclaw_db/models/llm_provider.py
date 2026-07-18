from __future__ import annotations

from datetime import datetime

from sqlalchemy import JSON, Column
from sqlmodel import Field

from movieclaw_db.models.base import TimestampMixin
from movieclaw_db.models.site_credential import ConfigStatus


class LlmProvider(TimestampMixin, table=True):
    """LLM 供应商配置表：**单例**，全表至多一行。

    与下载器（可配多台）不同，LLM 只需要接入一个供应商就够用——
    多供应商路由能力保留在 movieclaw_llm 领域库里，应用层不暴露。
    单例语义由 Repository 的 upsert 维护（有则覆盖、无则创建）。

    ``provider_type`` 关联 movieclaw_llm 的供应商预设（openai / bailian /
    openai_compat），base_url 留空时用预设默认端点。

    验证状态机与站点/下载器一致（复用 ConfigStatus）：保存后置 PENDING，
    后台用 default_model 发一次最小对话验证，成功 ACTIVE / 失败 FAILED。

    安全：``api_key`` 经 SecretBox 加密后落库（``enc::`` 前缀密文），
    加解密统一在 Repository 层完成。
    """

    __tablename__ = "llm_provider"

    id: int | None = Field(default=None, primary_key=True)
    provider_type: str = Field(description="供应商预设 id：openai / bailian / openai_compat")
    base_url: str | None = Field(default=None, description="API 端点（留空用预设默认）")
    api_key: str = Field(description="API Key（SecretBox 加密密文）")
    # 用户指定的默认模型：未来 agent 不显式选模型时都用它
    default_model: str = Field(description="默认使用的模型 id，如 qwen-plus")

    # 验证状态机（语义见 ConfigStatus）
    status: ConfigStatus = Field(default=ConfigStatus.PENDING, description="连接验证状态")
    last_error: str | None = Field(default=None, description="最近一次测试失败原因（中文）")
    last_checked_at: datetime | None = Field(default=None, description="最近一次测试时间")
    # 最近一次验证成功时端点上报的可用模型列表（JSON 数组），供设置页选择
    available_models: list[str] | None = Field(
        default=None, sa_column=Column(JSON), description="端点上报的可用模型列表"
    )
    # 用户补录的自定义模型及其参数（JSON 数组，元素结构见 movieclaw_llm.ModelInfo）。
    # 自定义端点（openai_compat）没有内置目录，模型的上下文/输出上限/思考预算等
    # 参数全靠这里——只存裸模型 id 不够，agent 做预算决策需要完整元数据。
    extra_models: list[dict] | None = Field(
        default=None, sa_column=Column(JSON), description="用户补录的模型目录（含参数）"
    )
