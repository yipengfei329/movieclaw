"""规则组服务：默认组懒种子、CRUD 与"被引用禁删"语义。

规则组是纯参数包（movieclaw_matcher.RuleSetSpec 定 schema），本服务只负责
校验与持久化；判断逻辑在匹配内核。修改规则组只影响之后的评估、不追溯已
grabbed 的工单——这一条不需要任何机制，天然成立。
"""

from __future__ import annotations

import logging

from sqlalchemy.ext.asyncio import AsyncSession

from movieclaw_api.exceptions import (
    BadRequestException,
    ConflictException,
    NotFoundException,
)
from movieclaw_db.models import RuleSet
from movieclaw_db.repositories import RuleSetRepository
from movieclaw_matcher import RuleSetSpec

logger = logging.getLogger("movieclaw_api.rule_sets")

_DEFAULT_NAME = "默认规则组"


class RuleSetService:
    def __init__(self, session: AsyncSession) -> None:
        self._repo = RuleSetRepository(session)

    async def ensure_default(self) -> RuleSet:
        """取默认规则组，不存在则懒种子一个"全不限"的（幂等）。

        放在服务层而非迁移里做 seed：迁移保持纯 DDL，且名字/形态想改时
        不用动历史迁移。
        """
        existing = await self._repo.get_default()
        if existing is not None:
            return existing
        row = await self._repo.save(RuleSet(name=_DEFAULT_NAME, is_default=True, spec={}))
        logger.info("已创建默认规则组（全不限），新订阅未指定规则组时使用它")
        return row

    async def list_all(self) -> list[RuleSet]:
        await self.ensure_default()
        return await self._repo.list_all()

    async def get(self, rule_set_id: int) -> RuleSet:
        row = await self._repo.get(rule_set_id)
        if row is None:
            raise NotFoundException(f"规则组不存在：#{rule_set_id}")
        return row

    async def create(self, name: str, spec: dict) -> RuleSet:
        cleaned = self._validate(name, spec)
        return await self._repo.save(RuleSet(name=name.strip(), spec=cleaned))

    async def update(self, rule_set_id: int, *, name: str, spec: dict) -> RuleSet:
        row = await self.get(rule_set_id)
        row.name = name.strip() or row.name
        row.spec = self._validate(row.name, spec)
        return await self._repo.save(row)

    async def delete(self, rule_set_id: int) -> None:
        """删除规则组。默认组与被订阅引用的组禁删（显式报错优于隐式改挂靠）。"""
        row = await self.get(rule_set_id)
        if row.is_default:
            raise BadRequestException("默认规则组不可删除")
        references = await self._repo.count_references(rule_set_id)
        if references > 0:
            raise ConflictException(
                f"规则组「{row.name}」正被 {references} 个订阅引用，"
                "请先把这些订阅改到其他规则组再删除"
            )
        await self._repo.delete(row)
        logger.info("规则组「%s」已删除", row.name)

    @staticmethod
    def _validate(name: str, spec: dict) -> dict:
        """经 RuleSetSpec 校验并规整（未知字段拒绝、类型收敛），存精简形态。"""
        if not name.strip():
            raise BadRequestException("规则组名称不能为空")
        try:
            parsed = RuleSetSpec.model_validate(spec)
        except ValueError as exc:
            raise BadRequestException(f"规则组参数不合法：{exc}") from exc
        return parsed.model_dump(exclude_defaults=True, mode="json")
