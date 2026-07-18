"""选优（内核第三步）：同一单元的多个通过候选，投谁。

排序规则（已确认决策）：**整季包优先于单集**——一个种子覆盖一季，搜索与
下载管理成本最低；同类内按规则评分降序，评分相同做种多者优先（下得快）。
"""

from __future__ import annotations

from movieclaw_matcher.models import IdentityMatch, RuleVerdict, TorrentCandidate

Entry = tuple[TorrentCandidate, IdentityMatch, RuleVerdict]


def pick_best(entries: list[Entry]) -> Entry | None:
    """从"已通过规则"的候选里选一个投递目标；空列表/全拒绝返回 None。"""
    accepted = [e for e in entries if e[2].accepted]
    if not accepted:
        return None
    return max(
        accepted,
        key=lambda e: (e[1].is_pack, e[2].score, e[0].seeders or 0),
    )
