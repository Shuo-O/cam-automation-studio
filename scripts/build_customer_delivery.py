"""Build a reproducible, offline customer ZIP for CAM Automation Studio.

The builder intentionally uses an allow-list of repository roots and excludes
development output.  It has no network or package-install step.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import sys
import tempfile
import zipfile
from pathlib import Path
from typing import Iterable
import re


REPO_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_OUTPUT = REPO_ROOT / "build" / "cam-automation-customer.zip"
SOURCE_DATE_EPOCH = 1_577_836_800  # 2020-01-01 00:00:00 UTC
_EXCLUDED_PARTS = {
    ".git",
    ".hg",
    ".svn",
    "__pycache__",
    ".pytest_cache",
    ".mypy_cache",
    ".ruff_cache",
    ".venv",
    "venv",
    "build",
    "dist",
}
_SECRET_NAME = re.compile(
    r"(^|[._-])(secret|secrets|password|passwd|token|credential|credentials|private|"
    r"apikey|api-key|\.env)([._-]|$)",
    re.IGNORECASE,
)
_INCLUDED_ROOTS = ("cam_automation", "plugins", "host_bridges", "delivery", "docs/customer")
_INCLUDED_FILES = ("LICENSE", "pyproject.toml")
_CUSTOMER_SCRIPTS = {
    "build_customer_delivery.py",
    "customer_backup.py",
    "customer_launcher.py",
    "customer_restore.py",
    "customer_self_check.py",
}


def _is_excluded(path: Path, repo_root: Path) -> bool:
    try:
        relative = path.resolve().relative_to(repo_root.resolve())
    except ValueError:
        return True
    if any(part in _EXCLUDED_PARTS for part in relative.parts):
        return True
    if any(_SECRET_NAME.search(part) for part in relative.parts):
        return True
    if path.name.endswith((".db", ".db-shm", ".db-wal", ".pyc", ".pyo")):
        return True
    return False


def iter_source_files(
    repo_root: Path = REPO_ROOT,
    *,
    exclude_paths: Iterable[str | os.PathLike[str]] = (),
) -> Iterable[tuple[str, Path]]:
    """Yield package-relative files in stable order.

    Only application/plugin assets and customer documentation are included.
    Tests, research notes, build output and local customer data are excluded.
    """

    excluded = {Path(path).expanduser().resolve() for path in exclude_paths}
    candidates: dict[str, Path] = {}
    for relative_root in _INCLUDED_ROOTS:
        root = repo_root / relative_root
        if not root.exists():
            continue
        for path in root.rglob("*"):
            if (
                not path.is_file()
                or path.resolve() in excluded
                or _is_excluded(path, repo_root)
            ):
                continue
            archive_name = path.relative_to(repo_root).as_posix()
            candidates[archive_name] = path
    scripts_root = repo_root / "scripts"
    for name in _CUSTOMER_SCRIPTS:
        path = scripts_root / name
        if (
            path.is_file()
            and path.resolve() not in excluded
            and not _is_excluded(path, repo_root)
        ):
            candidates[f"scripts/{name}"] = path
    for name in _INCLUDED_FILES:
        path = repo_root / name
        if path.is_file():
            candidates[name] = path
    yield from ((name, candidates[name]) for name in sorted(candidates))


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _project_version(repo_root: Path) -> str:
    """Read the PEP 621 version without importing the application."""

    pyproject = (repo_root / "pyproject.toml").read_text(encoding="utf-8")
    match = re.search(r"(?m)^\s*version\s*=\s*[\"']([^\"']+)[\"']\s*$", pyproject)
    if not match:
        raise ValueError("pyproject.toml 缺少 project.version。")
    return match.group(1)


def _zip_info(name: str, size: int) -> zipfile.ZipInfo:
    info = zipfile.ZipInfo(name, date_time=(2020, 1, 1, 0, 0, 0))
    info.compress_type = zipfile.ZIP_DEFLATED
    info.external_attr = (0o100644 & 0xFFFF) << 16
    info.create_system = 0
    info.file_size = size
    return info


def build_customer_zip(
    output: str | os.PathLike[str],
    *,
    repo_root: str | os.PathLike[str] = REPO_ROOT,
) -> Path:
    """Build a deterministic ZIP and a sibling SHA-256 file.

    The output is replaced atomically.  The destination itself is never read as
    source, even when it is placed below the repository's build directory.
    """

    root = Path(repo_root).resolve()
    destination = Path(output).expanduser().resolve()
    destination.parent.mkdir(parents=True, exist_ok=True)
    source = list(
        iter_source_files(
            root,
            exclude_paths=(
                destination,
                destination.with_suffix(destination.suffix + ".sha256"),
            ),
        )
    )
    entries = [
        {"path": archive_name, "sha256": _sha256(path), "size": path.stat().st_size}
        for archive_name, path in source
    ]
    manifest = _manifest_for_root(entries, root)

    fd, temporary_name = tempfile.mkstemp(
        prefix=f".{destination.stem}-", suffix=".tmp", dir=destination.parent
    )
    os.close(fd)
    temporary = Path(temporary_name)
    try:
        with zipfile.ZipFile(
            temporary,
            "w",
            compression=zipfile.ZIP_DEFLATED,
            compresslevel=9,
            strict_timestamps=False,
        ) as archive:
            for archive_name, path in source:
                data = path.read_bytes()
                archive.writestr(_zip_info(archive_name, len(data)), data)
            archive.writestr(_zip_info("delivery/BUILD-MANIFEST.json", len(manifest)), manifest)
        os.replace(temporary, destination)
    finally:
        temporary.unlink(missing_ok=True)
    digest = _sha256(destination)
    destination.with_suffix(destination.suffix + ".sha256").write_text(
        f"{digest}  {destination.name}\n", encoding="ascii"
    )
    return destination


def _manifest_for_root(entries: list[dict[str, object]], root: Path) -> bytes:
    """Build a manifest using the selected source root's version."""

    project_version = _project_version(root)
    value = {
        "schema_version": 1,
        "package_format": "cam-automation-customer-zip-v1",
        "application_version": project_version,
        "requires_python": ">=3.10",
        "offline": True,
        "dry_run_only": True,
        "source_policy": {
            "included": ["cam_automation", "plugins", "host_bridges", "delivery", "docs/customer"],
            "excluded": ["build", ".git", "tests", "research", "secrets", "cache"],
        },
        "files": entries,
    }
    return (json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True) + "\n").encode("utf-8")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Build a reproducible customer ZIP.")
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    parser.add_argument("--repo-root", type=Path, default=REPO_ROOT)
    args = parser.parse_args(argv)
    try:
        path = build_customer_zip(args.output, repo_root=args.repo_root)
    except (OSError, ValueError) as error:
        print(f"构建失败: {error}", file=sys.stderr)
        return 2
    print(f"已生成: {path}")
    print(f"SHA-256: {path.with_suffix(path.suffix + '.sha256')}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
