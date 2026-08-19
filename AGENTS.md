# Repository collaboration boundaries

This repository develops Siemens NX and Autodesk PowerMill automation in parallel.

- Keep the shared event contract backward compatible. Add optional fields instead of renaming or removing existing fields.
- NX-specific parsing belongs in `plugins/ug-cam-copilot/src/ugcam_ai/adapters/nx_journal.py`.
- PowerMill-specific parsing stays in the existing PowerMill app or a future `powermill_macro.py` adapter. Do not modify the NX parser for PowerMill syntax.
- Both products may use canonical `cam.*` actions. Product-only actions must use an `nx.*` or `powermill.*` prefix.
- Generated production actions remain dry-run until a human reviews the recipe and the result passes CAM simulation, collision checks, and shop approval.
- Never emit or send machine-ready NC code directly from learned logs.

