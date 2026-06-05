"""Prompt 组装与上下文预算控制。

这个模块负责决定：每一轮到底把多少 prefix、memory、相关笔记、历史
以及当前用户请求送进模型。
"""

from __future__ import annotations

from dataclasses import dataclass

from .context_usage import ContextUsageAnalyzer
from src.Features import memory as memorylib, skills as skillslib
from .turn_history import TurnHistoryBuilder, tail_clip

DEFAULT_TOTAL_BUDGET = 60000
DEFAULT_SECTION_BUDGETS = {
    "prefix": 12000,
    "memory": 8000,
    "skills": 4000,
    "relevant_memory": 6000,
    "history": 30000,
}
DEFAULT_SECTION_FLOORS = {
    "prefix": 4000,
    "memory": 1200,
    "skills": 600,
    "relevant_memory": 1000,
    "history": 6000,
}
# 当 prompt 超预算时，会优先压缩这些 section。
DEFAULT_REDUCTION_ORDER = ("relevant_memory", "skills", "history", "memory", "prefix")
SECTION_ORDER = (
    "prefix",
    "memory",
    "skills",
    "relevant_memory",
    "history",
    "current_request",
)
CURRENT_REQUEST_SECTION = "current_request"
RELEVANT_MEMORY_LIMIT = 3


@dataclass
class SectionRender:
    raw: str
    budget: int
    rendered: str
    details: dict | None = None

    @property
    def raw_chars(self):
        return len(self.raw)

    @property
    def rendered_chars(self):
        return len(self.rendered)


class ContextManager:
    def __init__(
        self,
        agent,
        total_budget=DEFAULT_TOTAL_BUDGET,
        section_budgets=None,
        section_floors=None,
        reduction_order=None,
    ):
        self.agent = agent
        self.total_budget = int(total_budget)
        self.section_budgets = dict(DEFAULT_SECTION_BUDGETS)
        if section_budgets:
            self.section_budgets.update(
                {str(key): int(value) for key, value in section_budgets.items()}
            )
        self._section_floor_overrides = {
            str(key): int(value) for key, value in (section_floors or {}).items()
        }
        self.section_floors = self._compute_section_floors()
        self.reduction_order = tuple(reduction_order or DEFAULT_REDUCTION_ORDER)
        self.history_builder = TurnHistoryBuilder(agent)

    def _compute_section_floors(self):
        floors = {
            section: max(20, int(budget) // 4)
            for section, budget in self.section_budgets.items()
        }
        floors.update(self._section_floor_overrides)
        return floors
