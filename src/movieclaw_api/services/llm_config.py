"""LLM 供应商配置服务：单例配置的读写与连接验证。

与下载器配置（downloader_config）同构，差异只有两点：
- 单例语义：全局至多一份配置，PUT 即 upsert，无多实例管理；
- 验证判据：用 default_model 发一次最小对话（max_tokens=1）——比只调
  /models 列表更真实，能一次性证明 key、端点、模型 id 三者都有效；
  模型列表另行 best-effort 拉取，仅用于设置页的选择提示，失败不影响结论。
"""

from __future__ import annotations

import logging

from sqlalchemy.ext.asyncio import AsyncSession

from movieclaw_api.exceptions import (
    BadRequestException,
    ConflictException,
    NotFoundException,
)
from movieclaw_db.engine import get_database
from movieclaw_db.models.llm_provider import LlmProvider
from movieclaw_db.models.site_credential import ConfigStatus
from movieclaw_db.repositories.llm_provider_repo import LlmProviderRepository
from movieclaw_llm import ChatMessage, ChatRequest, LlmError, LlmRouter, ModelInfo, ModelSettings
from movieclaw_llm.models import LlmProviderConfig, ProviderPreset
from movieclaw_llm.protocols import PROTOCOLS
from movieclaw_llm.providers import get_preset, list_presets

logger = logging.getLogger("movieclaw_api.llm_config")

# 验证用较短超时：只回答"通不通"，没必要等 SDK 默认的十分钟
_TEST_TIMEOUT = 30.0


def to_domain_config(row: LlmProvider, api_key: str) -> LlmProviderConfig:
    """ORM 记录 → movieclaw_llm 领域配置（未来 agent 构建 LlmRouter 也用它）。"""
    preset = get_preset(row.provider_type)
    return LlmProviderConfig(
        name=preset.display_name,
        provider_type=row.provider_type,
        api_key=api_key,
        base_url=row.base_url,
        default_model=row.default_model,
        extra_models=[ModelInfo.model_validate(m) for m in row.extra_models or []],
        is_default=True,
    )


class LlmConfigService:
    """LLM 供应商配置的业务服务。绑定一个数据库会话。"""

    def __init__(self, session: AsyncSession) -> None:
        self._session = session
        self._repo = LlmProviderRepository(session)

    @staticmethod
    def _assert_not_verifying(row: LlmProvider) -> None:
        """若正在测试连接，拒绝当前操作（409）。"""
        if row.status == ConfigStatus.VERIFYING:
            raise ConflictException("正在测试模型连接，请等待完成后再操作")

    # -- 查询 --------------------------------------------------------------

    async def get_or_none(self) -> LlmProvider | None:
        """返回当前配置；尚未配置返回 None（设置页据此渲染空态）。"""
        return await self._repo.get()

    async def get(self) -> LlmProvider:
        """返回当前配置；尚未配置抛 404。"""
        row = await self._repo.get()
        if row is None:
            raise NotFoundException("尚未配置模型供应商")
        return row

    # -- 写入 --------------------------------------------------------------

    @staticmethod
    def _assert_default_model_configured(
        preset: ProviderPreset,
        extra_models: list[ModelInfo],
        default_model: str,
    ) -> None:
        """默认模型的严格校验，按供应商类型分两条规则：

        - 有内置目录的供应商（官方渠道）：默认模型必须在目录内，自定义
          模型不参与——官方渠道的模型集合以预设目录为准；
        - 无目录的自定义端点：模型必须在 extra_models 里带完整参数。
          其中「借用」自其它预设目录的模型（按 id 识别）参数随目录，
          豁免手填规则——共享窗口类模型（如 Kimi）本就没有独立输出上限。

        agent 做上下文预算、思考预算、并发决策都依赖这些参数，
        缺参数会让下游全部退化成瞎猜，所以在入口就拦住。
        """
        if preset.models:
            if any(m.id == default_model for m in preset.models):
                return
            raise BadRequestException(
                f"模型「{default_model}」不在「{preset.display_name}」的模型目录中，"
                "请从下拉列表中选择"
            )
        custom = next((m for m in extra_models if m.id == default_model), None)
        if custom is None:
            raise BadRequestException(
                f"模型「{default_model}」不在预设目录中，请先补全它的参数配置"
                "（上下文长度、最大输出等）后再保存"
            )
        # 借用目录模型：任一预设目录里有同 id 条目即豁免手填参数规则
        if any(m.id == custom.id for p in list_presets() for m in p.models):
            return
        if not custom.context_window or not custom.max_output_tokens:
            raise BadRequestException(
                f"自定义模型「{default_model}」缺少必要参数：上下文长度与最大输出为必填"
            )
        if custom.supports_thinking and not custom.max_thinking_tokens:
            raise BadRequestException(
                f"自定义模型「{default_model}」开启了思考模式，必须填写思考预算上限"
            )

    async def upsert(
        self,
        *,
        provider_type: str,
        base_url: str | None,
        api_key: str,
        default_model: str,
        extra_models: list[ModelInfo] | None = None,
    ) -> LlmProvider:
        """保存配置（有则覆盖）。状态置 PENDING，等待后台验证。"""
        try:
            preset = get_preset(provider_type)
        except LlmError as exc:
            # 未知供应商类型：领域错误翻译成 400（错误信息本身已含可选项提示）
            raise BadRequestException(str(exc)) from exc
        if preset.requires_base_url and not base_url:
            raise BadRequestException(f"接入「{preset.display_name}」必须填写 API 端点地址")
        extras = extra_models or []
        self._assert_default_model_configured(preset, extras, default_model)
        existing = await self._repo.get()
        if existing is not None:
            self._assert_not_verifying(existing)
        return await self._repo.upsert(
            provider_type=provider_type,
            base_url=base_url,
            api_key=api_key,
            default_model=default_model,
            extra_models=[m.model_dump() for m in extras] or None,
        )

    async def start_verification(self) -> LlmProvider:
        """同步占位为 VERIFYING 并返回，随后由调用方排队后台测试任务。

        并发守卫原理见 SiteConfigService.start_verification。
        """
        row = await self.get()
        self._assert_not_verifying(row)
        await self._repo.update_status(ConfigStatus.VERIFYING)
        return await self.get()

    async def delete(self) -> None:
        """删除配置；不存在抛 404，正在验证中抛 409。"""
        row = await self.get()
        self._assert_not_verifying(row)
        await self._repo.delete()


# ---------------------------------------------------------------------------
# 进程级 LlmRouter 单例：所有 LLM 调用（agent 等）的运行时入口
# ---------------------------------------------------------------------------

_runtime_router = LlmRouter()


async def acquire_llm_router(session: AsyncSession) -> LlmRouter:
    """读取 DB 配置并组装进程级 LlmRouter。

    每次取用都重新读配置并 update_providers：配置指纹未变时底层客户端
    缓存直接复用（零开销），变了则自动重建——不需要「配置已修改」的
    显式通知链路。尚未配置供应商时抛 404（中文提示引导去设置页）。
    """
    repo = LlmProviderRepository(session)
    row = await repo.get()
    if row is None:
        raise NotFoundException("尚未配置模型供应商，请先在「设置 → AI 模型」中接入")
    config = to_domain_config(row, repo.decrypted_api_key(row))
    await _runtime_router.update_providers([config])
    return _runtime_router


# ---------------------------------------------------------------------------
# 后台连接测试（与 verify_downloader 同构的背景任务）
# ---------------------------------------------------------------------------


async def verify_llm_provider() -> None:
    """异步验证 LLM 供应商配置，并把结论写回状态字段。

    验证判据：用 default_model 发一次 max_tokens=1 的最小对话，能收到
    响应即证明 key、端点、模型 id 均有效。可用模型列表 best-effort
    拉取（部分兼容端点不提供 /models），失败只记日志不影响结论。

    前置约定：调用前状态已被 start_verification 置为 VERIFYING。
    作为背景任务：自开独立数据库会话，绝不向外抛异常 ——
    任何失败都转成 FAILED + last_error。
    """
    async with get_database().session() as session:
        repo = LlmProviderRepository(session)
        row = await repo.get()
        if row is None:
            logger.warning("测试连接时 LLM 供应商配置已被删除")
            return

        config = to_domain_config(row, repo.decrypted_api_key(row))
        config.timeout_seconds = _TEST_TIMEOUT
        preset = get_preset(row.provider_type)
        protocol = PROTOCOLS[preset.protocol](config, preset)
        try:
            await protocol.chat(
                ChatRequest(
                    messages=[ChatMessage(role="user", content="ping")],
                    settings=ModelSettings(max_tokens=1),
                ),
                row.default_model,
            )
        except LlmError as exc:
            # LlmError 的 message 本身已是清晰中文，直接展示
            logger.info("LLM 连接测试失败：%s", exc)
            await repo.update_status(ConfigStatus.FAILED, last_error=str(exc))
            return
        except Exception as exc:  # noqa: BLE001 -- 背景任务兜底，绝不外抛
            logger.exception("LLM 连接测试发生未知错误")
            await repo.update_status(
                ConfigStatus.FAILED,
                last_error=f"测试时发生未知错误（{type(exc).__name__}）：{exc}",
            )
            return
        else:
            # 对话验证已通过；模型列表仅是设置页的选择提示，拉不到不影响结论
            available: list[str] | None = None
            try:
                info = await protocol.test_connection()
                available = sorted(info.models)
            except Exception:  # noqa: BLE001
                logger.info("端点未提供模型列表接口，跳过（不影响验证结论）")
            logger.info("LLM 连接测试通过：%s / %s", preset.display_name, row.default_model)
            await repo.update_status(ConfigStatus.ACTIVE, available_models=available)
        finally:
            await protocol.close()
