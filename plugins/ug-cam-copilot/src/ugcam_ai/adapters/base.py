from __future__ import annotations

from pathlib import Path
from typing import Iterable, Protocol

from ..models import ActivityEvent


class ActivityAdapter(Protocol):
    product: str

    def supports(self, path: Path) -> bool:
        ...

    def parse(self, path: Path) -> Iterable[ActivityEvent]:
        ...

