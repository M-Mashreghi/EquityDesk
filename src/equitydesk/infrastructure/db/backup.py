"""Backup, verification and restore of the SQLite system of record (plus the raw source store).

- The database is copied with SQLite's online backup API, so a backup is consistent even while
  the API or scheduler are writing.
- Each archive carries `manifest.json`: creation time, app and schema version, row counts per
  table and the SHA-256 of every file. `verify_backup` re-checks all of it and runs
  `PRAGMA integrity_check` on the database copy before anything is restored.
- Restore never silently overwrites: an existing database (and raw store) is moved aside first.

CLI: `python -m equitydesk.infrastructure.db.backup create|verify|restore|prune ...`
"""

from __future__ import annotations

import argparse
import hashlib
import json
import shutil
import sqlite3
import tarfile
import tempfile
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path, PurePosixPath
from typing import Any

from equitydesk import __version__
from equitydesk.infrastructure.db.sqlite import SCHEMA_VERSION

DB_MEMBER = "equitydesk.db"
RAW_PREFIX = "raw/"
MANIFEST = "manifest.json"
BACKUP_FORMAT = 1


class BackupError(RuntimeError):
    pass


@dataclass(frozen=True)
class BackupInfo:
    path: Path
    manifest: dict[str, Any]


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as fh:
        for block in iter(lambda: fh.read(1 << 20), b""):
            digest.update(block)
    return digest.hexdigest()


def _table_counts(db: Path) -> dict[str, int]:
    conn = sqlite3.connect(db)
    try:
        tables = [
            r[0]
            for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table' ORDER BY name")
        ]
        return {t: conn.execute(f'SELECT COUNT(*) FROM "{t}"').fetchone()[0] for t in tables}  # noqa: S608
    finally:
        conn.close()


def _schema_version(db: Path) -> int | None:
    conn = sqlite3.connect(db)
    try:
        row = conn.execute("SELECT value FROM schema_meta WHERE key='schema_version'").fetchone()
        return int(row[0]) if row else None
    except sqlite3.DatabaseError:
        return None
    finally:
        conn.close()


def create_backup(
    db_path: Path,
    out_dir: Path,
    raw_root: Path | None = None,
    clock: Callable[[], datetime] = lambda: datetime.now(UTC),
) -> BackupInfo:
    """Write `equitydesk-<UTC timestamp>.tar.gz` into `out_dir` and return it with its manifest."""
    if not db_path.exists():
        raise BackupError(f"database {db_path} does not exist")
    now = clock()
    out_dir.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory() as tmp:
        snapshot = Path(tmp) / DB_MEMBER
        src = sqlite3.connect(db_path)
        dst = sqlite3.connect(snapshot)
        try:
            src.backup(dst)  # consistent online copy
        finally:
            dst.close()
            src.close()
        files: dict[str, str] = {DB_MEMBER: _sha256(snapshot)}
        raw_files: list[Path] = []
        if raw_root is not None and raw_root.exists():
            raw_files = sorted(
                p for p in raw_root.rglob("*") if p.is_file() and not p.name.startswith(".tmp-")
            )
            for p in raw_files:
                files[RAW_PREFIX + p.relative_to(raw_root).as_posix()] = _sha256(p)
        manifest = {
            "format": BACKUP_FORMAT,
            "created_at": now.isoformat(),
            "app_version": __version__,
            "schema_version": _schema_version(snapshot),
            "tables": _table_counts(snapshot),
            "includes_raw": raw_root is not None,
            "files": files,
        }
        manifest_path = Path(tmp) / MANIFEST
        manifest_path.write_text(json.dumps(manifest, indent=2, sort_keys=True), encoding="utf-8")
        archive = out_dir / f"equitydesk-{now.strftime('%Y%m%dT%H%M%S%fZ')}.tar.gz"
        partial = archive.with_suffix(".partial")
        with tarfile.open(partial, "w:gz") as tar:
            tar.add(manifest_path, arcname=MANIFEST)
            tar.add(snapshot, arcname=DB_MEMBER)
            for p in raw_files:
                assert raw_root is not None
                tar.add(p, arcname=RAW_PREFIX + p.relative_to(raw_root).as_posix())
        partial.replace(archive)
    return BackupInfo(archive, manifest)


def _safe_members(tar: tarfile.TarFile) -> list[tarfile.TarInfo]:
    members = tar.getmembers()
    for m in members:
        name = PurePosixPath(m.name)
        if name.is_absolute() or ".." in name.parts or not (m.isfile()):
            raise BackupError(f"unsafe archive member {m.name!r}")
    return members


def verify_backup(archive: Path) -> dict[str, Any]:
    """Check manifest, file hashes, database integrity and schema compatibility."""
    if not archive.exists():
        raise BackupError(f"backup {archive} does not exist")
    with tempfile.TemporaryDirectory() as tmp, tarfile.open(archive, "r:gz") as tar:
        members = _safe_members(tar)
        names = {m.name for m in members}
        if MANIFEST not in names or DB_MEMBER not in names:
            raise BackupError("archive lacks manifest.json or equitydesk.db")
        tar.extractall(tmp, filter="data")
        root = Path(tmp)
        manifest: dict[str, Any] = json.loads((root / MANIFEST).read_text(encoding="utf-8"))
        expected = manifest.get("files", {})
        if set(expected) != names - {MANIFEST}:
            raise BackupError("archive contents do not match the manifest")
        for name, digest in expected.items():
            if _sha256(root / name) != digest:
                raise BackupError(f"checksum mismatch for {name}")
        conn = sqlite3.connect(root / DB_MEMBER)
        try:
            result = conn.execute("PRAGMA integrity_check").fetchone()[0]
        finally:
            conn.close()
        if result != "ok":
            raise BackupError(f"database integrity check failed: {result}")
        version = _schema_version(root / DB_MEMBER)
        if version is None or version > SCHEMA_VERSION:
            raise BackupError(
                f"backup schema {version} is not restorable by code schema {SCHEMA_VERSION}"
            )
        if _table_counts(root / DB_MEMBER) != manifest.get("tables"):
            raise BackupError("row counts differ from the manifest")
        return manifest


def restore_backup(
    archive: Path,
    db_path: Path,
    raw_root: Path | None = None,
    clock: Callable[[], datetime] = lambda: datetime.now(UTC),
) -> dict[str, Any]:
    """Verify, then restore. Existing files are moved to `*.pre-restore-<ts>` (never deleted)."""
    manifest = verify_backup(archive)
    stamp = clock().strftime("%Y%m%dT%H%M%SZ")
    with tempfile.TemporaryDirectory() as tmp, tarfile.open(archive, "r:gz") as tar:
        _safe_members(tar)
        tar.extractall(tmp, filter="data")
        root = Path(tmp)
        db_path.parent.mkdir(parents=True, exist_ok=True)
        for suffix in ("", "-wal", "-shm"):
            existing = Path(f"{db_path}{suffix}")
            if existing.exists():
                existing.replace(Path(f"{existing}.pre-restore-{stamp}"))
        shutil.copy2(root / DB_MEMBER, db_path)
        if raw_root is not None and (root / "raw").exists():
            if raw_root.exists() and any(raw_root.iterdir()):
                raw_root.replace(raw_root.with_name(f"{raw_root.name}.pre-restore-{stamp}"))
            shutil.copytree(root / "raw", raw_root, dirs_exist_ok=True)
    return manifest


def prune_backups(out_dir: Path, keep: int) -> list[Path]:
    """Delete all but the newest `keep` archives in `out_dir`; returns the removed paths."""
    if keep < 1:
        raise ValueError("keep at least one backup")
    archives = sorted(out_dir.glob("equitydesk-*.tar.gz"))
    removed = archives[:-keep]
    for p in removed:
        p.unlink()
    return removed


def main(argv: list[str] | None = None) -> int:  # pragma: no cover - thin CLI over tested functions
    from equitydesk.config import load_settings

    settings = load_settings(env_file=Path(".env"))
    raw_default = settings.data_dir / "raw"
    parser = argparse.ArgumentParser(description="EquityDesk backup / restore")
    sub = parser.add_subparsers(dest="cmd", required=True)
    c = sub.add_parser("create")
    c.add_argument("--out", type=Path, default=settings.data_dir / "backups")
    c.add_argument("--no-raw", action="store_true", help="database only (raw store excluded)")
    c.add_argument("--keep", type=int, default=None, help="prune to the newest N archives")
    v = sub.add_parser("verify")
    v.add_argument("archive", type=Path)
    r = sub.add_parser("restore")
    r.add_argument("archive", type=Path)
    r.add_argument("--db", type=Path, default=settings.db_path)
    r.add_argument("--raw", type=Path, default=raw_default)
    args = parser.parse_args(argv)
    if args.cmd == "create":
        info = create_backup(settings.db_path, args.out, None if args.no_raw else raw_default)
        print(info.path)
        if args.keep:
            for p in prune_backups(args.out, args.keep):
                print(f"pruned {p}")
    elif args.cmd == "verify":
        print(json.dumps(verify_backup(args.archive), indent=2))
    else:
        manifest = restore_backup(args.archive, args.db, args.raw)
        print(
            f"restored backup created {manifest['created_at']} (schema {manifest['schema_version']})"
        )
    return 0


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
