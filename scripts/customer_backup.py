"""Secure backup of customer data with path traversal protection."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import sqlite3
import tempfile
import zipfile
from pathlib import Path, PurePosixPath

_WINDOWS_RESERVED = {
    "con",
    "prn",
    "aux",
    "nul",
    *(f"com{number}" for number in range(1, 10)),
    *(f"lpt{number}" for number in range(1, 10)),
}


def _safe_member(name: str) -> PurePosixPath:
    if not name or "\x00" in name or "\\" in name:
        raise ValueError(f"不安全的备份成员名: {name!r}")
    path = PurePosixPath(name)
    if path.is_absolute():
        raise ValueError(f"不安全的备份成员名: {name!r}")
    for component in path.parts:
        if (
            not component
            or component in {".", ".."}
            or ":" in component
            or component[-1] in {" ", "."}
            or component.casefold() in _WINDOWS_RESERVED
        ):
            raise ValueError(f"不安全的备份路径组件: {component!r}")
    return path


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _is_sqlite_file(path: Path) -> bool:
    """Detect SQLite by its file signature instead of trusting an extension."""

    try:
        with path.open("rb") as stream:
            return stream.read(16) == b"SQLite format 3\x00"
    except OSError:
        return False


def _sqlite_backup(source: Path, destination: Path) -> None:
    """Take a consistent snapshot, including a live WAL, via SQLite backup API."""

    destination.parent.mkdir(parents=True, exist_ok=True)
    try:
        source_connection = sqlite3.connect(f"file:{source.as_posix()}?mode=ro", uri=True)
        target_connection = sqlite3.connect(destination)
        try:
            source_connection.backup(target_connection)
            target_connection.commit()
        finally:
            target_connection.close()
            source_connection.close()
    except sqlite3.Error as error:
        raise ValueError(f"SQLite 备份失败: {source}") from error


def create_backup(
    data_dir: str | os.PathLike[str],
    output: str | os.PathLike[str],
    *,
    overwrite: bool = False,
) -> Path:
    source_input = Path(data_dir).expanduser()
    if source_input.is_symlink():
        raise ValueError("客户数据目录不能是符号链接。")
    source = source_input.resolve()
    destination_input = Path(output).expanduser()
    if destination_input.exists() and destination_input.is_symlink():
        raise ValueError("备份目标不能是符号链接。")
    destination = destination_input.resolve()
    if not source.is_dir():
        raise NotADirectoryError(source)
    if destination == source or source in destination.parents:
        raise ValueError("备份目标不能位于客户数据目录内。")
    if destination.exists() and not overwrite:
        raise FileExistsError(f"备份目标已存在（默认不覆盖）: {destination}")
    destination.parent.mkdir(parents=True, exist_ok=True)
    files: list[tuple[str, Path]] = []
    with tempfile.TemporaryDirectory(prefix=".customer-backup-data-", dir=destination.parent) as staging_name:
        staging_root = Path(staging_name)
        discovered: list[tuple[str, Path]] = []
        for path in source.rglob("*"):
            if path.is_symlink():
                raise ValueError(f"备份不接受符号链接: {path}")
            if not path.is_file():
                continue
            relative = path.relative_to(source).as_posix()
            _safe_member(f"data/{relative}")
            discovered.append((relative, path))
        sqlite_files = {relative for relative, path in discovered if _is_sqlite_file(path)}
        for relative, path in discovered:
            if any(
                relative == f"{database}{suffix}"
                for database in sqlite_files
                for suffix in ("-wal", "-shm")
            ):
                # The SQLite backup API captures WAL state in the main snapshot.
                continue
            if relative in sqlite_files:
                snapshot = staging_root / relative
                _sqlite_backup(path, snapshot)
                files.append((relative, snapshot))
            else:
                files.append((relative, path))
        files.sort()
        manifest = {
            "schema_version": 1,
            "format": "cam-customer-backup-v1",
            "file_count": len(files),
            "sqlite_backup_api": True,
            "files": [
                {"path": name, "sha256": _sha256(path), "size": path.stat().st_size}
                for name, path in files
            ],
        }
        fd, temporary_name = tempfile.mkstemp(
            prefix=".customer-backup-", suffix=".tmp", dir=destination.parent
        )
        os.close(fd)
        temporary = Path(temporary_name)
        try:
            with zipfile.ZipFile(
                temporary, "w", compression=zipfile.ZIP_DEFLATED, compresslevel=9
            ) as archive:
                archive.writestr(
                    "manifest.json",
                    json.dumps(manifest, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
                )
                for relative, path in files:
                    archive.write(path, f"data/{relative}")
            os.replace(temporary, destination)
        finally:
            temporary.unlink(missing_ok=True)
    return destination


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="备份客户数据；默认拒绝覆盖已有文件。")
    parser.add_argument("data_dir", type=Path)
    parser.add_argument("output", type=Path)
    parser.add_argument("--overwrite", action="store_true")
    args = parser.parse_args(argv)
    path = create_backup(args.data_dir, args.output, overwrite=args.overwrite)
    print(f"备份完成: {path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
