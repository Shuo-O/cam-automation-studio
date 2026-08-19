from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path

from .models import Pattern


_HINTS = {
    "cam.group.select.program": "Resolve the target program group by a stable shop name.",
    "cam.group.select.method": "Resolve the method group; do not reuse recorded object IDs.",
    "cam.group.select.tool": "Resolve a reviewed tool assembly from the active NX tool library.",
    "cam.group.select.geometry": "Resolve MCS/workpiece geometry by stable attributes or names.",
    "cam.operation.create": "Create the operation with the target release's CAMOperationCollection API.",
    "cam.toolpath.generate": "Generate the reviewed operation, then inspect NX generation status.",
    "cam.verify.run": "Run NX CAM verification and collision/gouge checks.",
    "part.save": "Save only to an explicit test copy during validation.",
}


def scaffold_nx_candidate(
    pattern: Pattern,
    output_dir: str | Path,
    *,
    force: bool = False,
) -> list[Path]:
    output = Path(output_dir)
    output.mkdir(parents=True, exist_ok=True)
    targets = [
        output / "recipe.json",
        output / "nx_preview_journal.py",
        output / "REVIEW_CHECKLIST.md",
    ]
    existing = [path for path in targets if path.exists()]
    if existing and not force:
        names = ", ".join(path.name for path in existing)
        raise FileExistsError(f"refusing to overwrite generated files: {names}")

    recipe = {
        "schema_version": 1,
        "recipe_id": f"nx-{pattern.pattern_id}",
        "created_at": datetime.now(timezone.utc).isoformat(),
        "product": "nx",
        "status": "draft",
        "execution_mode": "dry-run",
        "evidence": {
            "support": pattern.support,
            "session_count": pattern.session_count,
            "confidence": round(pattern.confidence, 4),
            "occurrences": pattern.occurrences,
        },
        "steps": [
            {
                "order": index,
                "action": action,
                "implementation_hint": _HINTS.get(
                    action,
                    "Map this action against the Python stubs shipped with the target NX release.",
                ),
                "reviewed": False,
            }
            for index, action in enumerate(pattern.steps, 1)
        ],
        "release_constraints": {
            "nx_release": None,
            "python_minor": None,
            "machine": None,
            "postprocessor": None,
        },
        "required_gates": [
            "reviewed_tool_and_holder",
            "stable_geometry_resolution",
            "toolpath_generation_success",
            "collision_and_gouge_check",
            "machine_simulation",
            "human_approval",
        ],
    }
    targets[0].write_text(
        json.dumps(recipe, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    targets[1].write_text(_preview_journal(recipe), encoding="utf-8")
    targets[2].write_text(_review_checklist(recipe), encoding="utf-8")
    return targets


def _preview_journal(recipe: dict[str, object]) -> str:
    embedded = json.dumps(recipe, ensure_ascii=True, separators=(",", ":"))
    return f'''"""Generated NX workflow preview.

This MVP journal is dry-run only. It never creates operations, generates NC code,
or saves a part. Complete handlers against the target NX release's local Python
stubs only after reviewing recipe.json.
"""

import json

RECIPE = json.loads({embedded!r})


def _lines():
    yield "[UG CAM Copilot] dry-run recipe: " + str(RECIPE["recipe_id"])
    for step in RECIPE["steps"]:
        yield "{{order:02d}}. {{action}} -- {{implementation_hint}}".format(**step)
    yield "No NX object was modified."


def main():
    try:
        import NXOpen
    except ImportError:
        for line in _lines():
            print(line)
        return

    session = NXOpen.Session.GetSession()
    listing = session.ListingWindow
    listing.Open()
    for line in _lines():
        listing.WriteLine(line)


if __name__ == "__main__":
    main()
'''


def _review_checklist(recipe: dict[str, object]) -> str:
    return f"""# NX automation review — {recipe['recipe_id']}

- [ ] Confirm the exact NX release and bundled Python minor version.
- [ ] Replace recorded object IDs with stable names, attributes, or queries.
- [ ] Review machine, workholding, stock, MCS, material, tool and holder.
- [ ] Map every step against the local `UGOPEN/pythonStubs` API.
- [ ] Run only on a disposable copy of the part first.
- [ ] Confirm toolpath generation reports no failed/dirty operation.
- [ ] Run collision and gouge checking.
- [ ] Run machine simulation with the approved machine kit.
- [ ] Review postprocessor and NC output under existing shop controls.
- [ ] Obtain named human approval before production use.

The generated preview journal is intentionally non-mutating and cannot satisfy
these checks by itself.
"""

