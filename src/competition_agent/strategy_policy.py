from __future__ import annotations

from collections.abc import Iterable
import re

ILLEGAL_STRATEGY_TERMS = {"围标", "串标", "利益输送", "非法获取报价"}
_NEGATION_MARKERS = {"不得", "禁止", "严禁", "避免", "杜绝", "拒绝", "不参与", "防止", "防范", "合规"}
_CLAUSE_BOUNDARY = re.compile(r"[，,。；;！!？?]|但是|但|然而|同时|反而|转而|建议|可以|可采用|可通过")


def _is_negated_illegal_mention(text: str, term_start: int) -> bool:
    prefix = text[:term_start]
    boundaries = list(_CLAUSE_BOUNDARY.finditer(prefix))
    clause_start = boundaries[-1].end() if boundaries else 0
    clause_prefix = prefix[clause_start:]
    return any(marker in clause_prefix for marker in _NEGATION_MARKERS)


def sanitize_competition_strategies(strategies: Iterable[str]) -> tuple[list[str], list[str]]:
    accepted: list[str] = []
    filtered: list[str] = []
    for raw in strategies:
        if not isinstance(raw, str) or not raw.strip():
            continue
        strategy = raw.strip()
        matches = [
            (term, match.start())
            for term in ILLEGAL_STRATEGY_TERMS
            for match in re.finditer(re.escape(term), strategy)
        ]
        if not matches:
            accepted.append(strategy)
            continue
        if all(_is_negated_illegal_mention(strategy, start) for _term, start in matches):
            filtered.append(strategy)
            continue
        raise ValueError("illegal competition strategy")
    return accepted, filtered
