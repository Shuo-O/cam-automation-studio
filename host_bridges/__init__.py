"""Explicit, read-only host export boundaries.

These adapters do not discover, attach to, or control a CAD/CAM process.  A
vendor-supplied in-process exporter (or an offline fixture) must provide the
structured data and evidence explicitly.
"""

from .nx import NxOpenSnapshotExporter, NxReadOnlyBridge
from .powermill import (
    PowerMillComReadOnlyExporter,
    PowerMillReadOnlyBridge,
    PowerMillSnapshotExporter,
)

__all__ = [
    "NxOpenSnapshotExporter",
    "NxReadOnlyBridge",
    "PowerMillComReadOnlyExporter",
    "PowerMillReadOnlyBridge",
    "PowerMillSnapshotExporter",
]
