from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass, field

from pydantic import BaseModel, ConfigDict

# ---------------------------------------------------------------------------
# 配置域模型基类
# ---------------------------------------------------------------------------


class SettingSchema(BaseModel):
    """所有"配置域"模型的基类。

    每一类集成配置（大模型、下载器、媒体服务器……）都定义一个继承本类的
    Pydantic 模型，用**字段 + 默认值 + 校验规则**声明该域"长什么样"。这既是：
    - 落库/读取时的**校验与默认值来源**（缺字段自动补默认，非法值直接报错）；
    - 前端/引导向导**自动渲染表单**的元数据来源（字段名、类型、默认值一目了然）。

    约定
    ----
    - 模型必须能"零参数构造"（每个字段都带默认值），这样某个域从未配置时，
      ``SettingStore`` 能直接返回一份默认配置，保证空库也能启动（架构红线）。
    - 敏感字段（api_key/password/token 等）请在注册时通过 ``secret_fields``
      声明，由 ``SettingStore`` 负责落库前加密、读取后解密。
    """

    # extra="ignore"：从数据库读回旧数据时，即使模型后来删了某个字段也不报错，
    # 保证配置结构演进时的向前兼容。
    model_config = ConfigDict(extra="ignore")


# ---------------------------------------------------------------------------
# 配置域描述符 + 注册表
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class SettingDescriptor:
    """一个配置域的注册信息（把 Pydantic 模型和它的元数据绑在一起）。"""

    namespace: str
    """配置域标识，对应 ``app_setting.namespace``，全局唯一。"""

    model: type[SettingSchema]
    """该域的 Pydantic 模型类。"""

    title: str
    """人类可读的展示名，供管理界面 / 引导向导显示。"""

    required_for_bootstrap: bool
    """是否为"首次引导必填项"。向导会渲染所有此项为 True 的配置域。"""

    secret_fields: frozenset[str] = field(default_factory=frozenset)
    """需要加密落库的敏感字段名集合（顶层字段）。"""


# 全局注册表：namespace -> 描述符，以及 模型类 -> 描述符（便于 SettingStore 反查）。
_by_namespace: dict[str, SettingDescriptor] = {}
_by_model: dict[type[SettingSchema], SettingDescriptor] = {}


def register_setting(
    *,
    namespace: str,
    title: str,
    required_for_bootstrap: bool = False,
    secret_fields: Iterable[str] = (),
):
    """类装饰器：把一个 ``SettingSchema`` 子类注册为一个配置域。

    用法::

        @register_setting(
            namespace="llm.openai",
            title="OpenAI 兼容大模型",
            secret_fields=["api_key"],
        )
        class OpenAISetting(SettingSchema):
            base_url: str = "https://api.openai.com/v1"
            api_key: str = ""
            model: str = "gpt-4o"

    新增一类集成配置时，只需照此声明一个模型并注册，**无需任何数据库迁移**——
    这正是"通用表 + 注册表"架构为业务膨胀预留的扩展点。
    """

    secret = frozenset(secret_fields)

    def decorator(model_cls: type[SettingSchema]) -> type[SettingSchema]:
        if not issubclass(model_cls, SettingSchema):
            raise TypeError(f"{model_cls!r} 必须继承 SettingSchema 才能注册为配置域")
        if namespace in _by_namespace:
            raise ValueError(f"配置域 namespace 重复注册：{namespace}")
        # 校验声明的敏感字段确实是模型字段，避免拼写错误导致加密静默失效
        unknown = secret - set(model_cls.model_fields)
        if unknown:
            raise ValueError(f"配置域 {namespace} 声明了不存在的敏感字段：{sorted(unknown)}")

        descriptor = SettingDescriptor(
            namespace=namespace,
            model=model_cls,
            title=title,
            required_for_bootstrap=required_for_bootstrap,
            secret_fields=secret,
        )
        _by_namespace[namespace] = descriptor
        _by_model[model_cls] = descriptor
        return model_cls

    return decorator


def get_descriptor(namespace: str) -> SettingDescriptor:
    """按 namespace 取描述符；未注册时抛错。"""
    try:
        return _by_namespace[namespace]
    except KeyError as exc:
        raise KeyError(f"未注册的配置域：{namespace}") from exc


def get_descriptor_by_model(model_cls: type[SettingSchema]) -> SettingDescriptor:
    """按模型类取描述符；未注册时抛错。"""
    try:
        return _by_model[model_cls]
    except KeyError as exc:
        raise KeyError(f"配置模型未注册为配置域：{model_cls!r}") from exc


def list_descriptors() -> list[SettingDescriptor]:
    """返回所有已注册配置域，按 namespace 排序。"""
    return sorted(_by_namespace.values(), key=lambda d: d.namespace)


def list_bootstrap_required() -> list[SettingDescriptor]:
    """返回所有"首次引导必填"的配置域，供向导渲染步骤。"""
    return [d for d in list_descriptors() if d.required_for_bootstrap]
