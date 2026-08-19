from __future__ import annotations

import json
from pathlib import Path
from typing import Iterable

from ..models import ActivityEvent


class JsonlActivityAdapter:
    """Imports the shared contract produced by any CAD/CAM adapter."""

    product = "contract"

    def supports(self, path: Path) -> bool:
        return path.suffix.lower() == ".jsonl"

    def parse(self, path: Path) -> Iterable[ActivityEvent]:
        with path.open("r", encoding="utf-8-sig") as stream:
            for line_number, line in enumerate(stream, 1):
                if not line.strip():
                    continue
                value = json.loads(line)
                yield ActivityEvent.from_dict(
                    value,
                    fallback_source=str(path.resolve()),
                    fallback_line=line_number,
                )

