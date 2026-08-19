from __future__ import annotations

import hashlib
import math
from collections import Counter
from typing import Mapping, Sequence

from .models import ActivityEvent, Pattern


class SequenceMiner:
    """Finds repeated contiguous action sequences across independent sessions."""

    def __init__(
        self,
        *,
        min_support: int = 2,
        min_length: int = 3,
        max_length: int = 8,
    ) -> None:
        if min_support < 1:
            raise ValueError("min_support must be at least 1")
        if min_length < 2 or max_length < min_length:
            raise ValueError("require 2 <= min_length <= max_length")
        self.min_support = min_support
        self.min_length = min_length
        self.max_length = max_length

    def mine(
        self,
        sessions: Mapping[str, Sequence[ActivityEvent]],
        *,
        product: str,
        limit: int = 20,
    ) -> list[Pattern]:
        support: Counter[tuple[str, ...]] = Counter()
        occurrences: Counter[tuple[str, ...]] = Counter()
        for events in sessions.values():
            tokens = tuple(event.token for event in events)
            seen: set[tuple[str, ...]] = set()
            upper = min(self.max_length, len(tokens))
            for length in range(self.min_length, upper + 1):
                for start in range(0, len(tokens) - length + 1):
                    candidate = tokens[start : start + length]
                    occurrences[candidate] += 1
                    seen.add(candidate)
            support.update(seen)

        session_count = len(sessions)
        candidates: list[Pattern] = []
        for steps, count in support.items():
            if count < self.min_support:
                continue
            score = count * len(steps) + math.log2(occurrences[steps] + 1)
            pattern_id = hashlib.sha256("\x1f".join(steps).encode()).hexdigest()[:12]
            candidates.append(
                Pattern(
                    pattern_id=pattern_id,
                    product=product,
                    steps=steps,
                    support=count,
                    session_count=session_count,
                    occurrences=occurrences[steps],
                    score=score,
                )
            )
        candidates.sort(
            key=lambda item: (
                item.score,
                item.support,
                len(item.steps),
                item.occurrences,
            ),
            reverse=True,
        )

        selected: list[Pattern] = []
        for candidate in candidates:
            if any(
                candidate.support == existing.support
                and _is_contiguous_subset(candidate.steps, existing.steps)
                for existing in selected
            ):
                continue
            selected.append(candidate)
            if len(selected) >= limit:
                break
        return selected


def _is_contiguous_subset(needle: tuple[str, ...], haystack: tuple[str, ...]) -> bool:
    if len(needle) > len(haystack):
        return False
    return any(
        haystack[index : index + len(needle)] == needle
        for index in range(len(haystack) - len(needle) + 1)
    )

