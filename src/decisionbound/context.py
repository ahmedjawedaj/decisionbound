"""Deterministic task-context selection with non-negotiable protected slots."""
from __future__ import annotations
from collections.abc import Iterable
from .models import ContextCapsule, ContextItem
from .ports import ContextCompiler


class ContextBudgetExceeded(ValueError):
    """Protected requirements exceed a token budget and cannot be omitted."""


def estimated_tokens(text: str) -> int:
    """Heuristic token estimate (ceil(chars/4)); *not* provider tokenization."""
    return (len(text) + 3) // 4


class SelectiveContextCompiler(ContextCompiler):
    """Small, explainable compiler that never truncates protected evidence.

    The base compiler is intentionally lexical and deterministic. To add
    semantic RAG, subclass :class:`ContextCompiler` and implement ``compile``
    while retaining the protected-input and provenance contract.
    """

    def compile(self, task: str, items: Iterable[ContextItem], max_tokens: int) -> ContextCapsule:
        """Select whole context items by priority then lexical task relevance.

        Raises:
            ValueError: Budget is non-positive or inputs have duplicate keys.
            ContextBudgetExceeded: Task and protected constraints do not fit.
        """
        if not task.strip() or max_tokens <= 0:
            raise ValueError("Nonempty task and positive token budget are required")
        values = list(items)
        if len({i.key for i in values}) != len(values):
            raise ValueError("Context keys must be unique")
        budget = max_tokens - estimated_tokens(task)
        protected = [i for i in values if i.protected]
        unprotected = [i for i in values if not i.protected]
        cost = sum(estimated_tokens(i.text) for i in protected)
        if cost > budget:
            raise ContextBudgetExceeded("Protected context exceeds budget; increase cap or resolve requirements")
        selected = list(protected)
        terms = set(task.lower().split())
        ranked = sorted(unprotected, key=lambda i: (-i.priority, -len(terms & set(i.text.lower().split())), i.key))
        for item in ranked:
            item_cost = estimated_tokens(item.text)
            if cost + item_cost <= budget:
                selected.append(item)
                cost += item_cost
        present = {i.key for i in selected}
        return ContextCapsule(task, tuple(selected), tuple(i.key for i in values if i.key not in present),
                              estimated_tokens(task) + cost, max_tokens)
