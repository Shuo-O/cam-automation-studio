"""Secure restore for customer backups.

Archive members are validated before any write.  Existing files are protected
unless the operator explicitly passes --overwrite.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import shutil
import tempfile
import zipfile
from pathlib import Path, PurePosixPath
from typing import Any

_WINDOWS_RESERVED = {
    "con",
    "prn",
    "aux",
    "nul",
    *(f"com{number}" for number in range(1, 10)),
    *(f"lpt{number}" for number in range(1, 10)),
}


def _safe_component(component: str) -> None:
    if (
        not component
        or component in {".", ".."}
        or "\x00" in component
        or ":" in component
        or component[-1] in {" ", "."}
        or component.casefold() in _WINDOWS_RESERVED
    ):
        raise ValueError(f"不安全的备份路径组件: {component!r}")


def _safe_member(name: str) -> PurePosixPath:
    if not name or "\x00" in name or "\\" in name:
        raise ValueError(f"不安全的备份成员名: {name!r}")
    path = PurePosixPath(name)
    if path.is_absolute():
        raise ValueError(f"不安全的备份成员名: {name!r}")
    for component in path.parts:
        _safe_component(component)
    return path


def _inside(root: Path, relative: PurePosixPath) -> Path:
    candidate = (root / Path(*relative.parts)).resolve()
    if candidate != root and root not in candidate.parents:
        raise ValueError(f"备份路径越界: {relative}")
    return candidate


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _preflight_destinations(
    target: Path,
    relatives: list[str],
    *,
    overwrite: bool,
) -> dict[str, Path]:
    parsed = {relative: _safe_member(relative) for relative in relatives}
    folded: dict[str, str] = {}
    for relative in relatives:
        key = relative.casefold()
        if key in folded and folded[key] != relative:
            raise ValueError(f"备份路径存在 Windows 大小写碰撞: {folded[key]} / {relative}")
        folded[key] = relative
    paths = {relative: _inside(target, path) for relative, path in parsed.items()}
    path_parts = {tuple(path.parts): relative for relative, path in parsed.items()}
    for relative, path in parsed.items():
        for index in range(1, len(path.parts)):
            if tuple(path.parts[:index]) in path_parts:
                raise ValueError(f"备份文件与目录冲突: {path_parts[tuple(path.parts[:index])]} / {relative}")
    for relative, destination in paths.items():
        parent = destination.parent
        while parent != target:
            if parent.exists() and (parent.is_symlink() or not parent.is_dir()):
                raise ValueError(f"恢复父路径不可用: {parent}")
            parent = parent.parent
        if destination.exists():
            if destination.is_symlink():
                raise ValueError(f"拒绝写入符号链接目标: {destination}")
            if destination.is_dir():
                raise IsADirectoryError(destination)
            if not overwrite:
                raise FileExistsError(f"目标文件已存在（默认不覆盖）: {destination}")
        if destination.parent.exists():
            siblings = {item.name.casefold(): item for item in destination.parent.iterdir()}
            collision = siblings.get(destination.name.casefold())
            if collision is not None and collision.name != destination.name:
                raise FileExistsError(f"目标路径存在 Windows 大小写碰撞: {collision}")
    return paths


def restore_backup(
    backup: str | os.PathLike[str],
    data_dir: str | os.PathLike[str],
    *,
    overwrite: bool = False,
) -> dict[str, Any]:
    archive_input = Path(backup).expanduser()
    if archive_input.is_symlink():
        raise ValueError("备份文件不能是符号链接。")
    archive_path = archive_input.resolve()
    target_input = Path(data_dir).expanduser()
    if target_input.is_symlink():
        raise ValueError("恢复目录不能是符号链接。")
    target = target_input.resolve()
    if not archive_path.is_file():
        raise FileNotFoundError(archive_path)
    with zipfile.ZipFile(archive_path) as archive:
        names = archive.namelist()
        if len(names) != len(set(names)):
            raise ValueError("备份包含重复成员名。")
        for name in names:
            _safe_member(name)
        if "manifest.json" not in names:
            raise ValueError("备份缺少 manifest.json。")
        manifest = json.loads(archive.read("manifest.json").decode("utf-8"))
        if not isinstance(manifest, dict) or manifest.get("format") != "cam-customer-backup-v1":
            raise ValueError("不支持的客户备份格式。")
        files = manifest.get("files", [])
        if not isinstance(files, list):
            raise ValueError("备份 manifest.files 无效。")
        expected: dict[str, dict[str, Any]] = {}
        for item in files:
            if not isinstance(item, dict) or not isinstance(item.get("path"), str):
                raise ValueError("备份 manifest 文件条目无效。")
            relative = item["path"]
            _safe_member(f"data/{relative}")
            member = f"data/{relative}"
            if member not in names:
                raise ValueError(f"备份缺少文件: {relative}")
            if relative in expected:
                raise ValueError(f"备份 manifest 有重复文件: {relative}")
            expected[relative] = item
        allowed_members = {"manifest.json"} | {f"data/{relative}" for relative in expected}
        unexpected = sorted(set(names) - allowed_members)
        if unexpected:
            raise ValueError(f"备份含未声明成员: {unexpected[0]}")
        if manifest.get("file_count") not in (None, len(expected)):
            raise ValueError("备份 file_count 与文件清单不一致。")
        target.mkdir(parents=True, exist_ok=True)
        destinations = _preflight_destinations(
            target, sorted(expected), overwrite=overwrite
        )
        staging = Path(tempfile.mkdtemp(prefix=".customer-restore-", dir=target.parent))
        try:
            for relative, item in expected.items():
                destination = _inside(staging, PurePosixPath(*relative.split("/")))
                destination.parent.mkdir(parents=True, exist_ok=True)
                with archive.open(f"data/{relative}") as source, destination.open("wb") as output:
                    shutil.copyfileobj(source, output)
                if item.get("sha256") and _sha256(destination) != item["sha256"]:
                    raise ValueError(f"备份校验失败: {relative}")
            restored = 0
            for relative in sorted(expected):
                source = _inside(staging, PurePosixPath(*relative.split("/")))
                destination = destinations[relative]
                destination.parent.mkdir(parents=True, exist_ok=True)
                os.replace(source, destination) if overwrite else source.replace(destination)
                restored += 1
            return {"schema_version": 1, "status": "restored", "data_dir": str(target), "files": restored}
        finally:
            shutil.rmtree(staging, ignore_errors=True)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="恢复客户数据；默认拒绝覆盖已有文件。")
    parser.add_argument("backup", type=Path)
    parser.add_argument("data_dir", type=Path)
    parser.add_argument("--overwrite", action="store_true")
    args = parser.parse_args(argv)
    result = restore_backup(args.backup, args.data_dir, overwrite=args.overwrite)
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
