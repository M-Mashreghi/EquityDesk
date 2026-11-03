"""Restore drill: back up a store holding a complete research run, restore it elsewhere, and
prove the restored system serves the same run, report, review and raw sources."""

from __future__ import annotations

import io
import json
import tarfile
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

import factories as f
from e2e_support import INPUTS
from equitydesk.api.app import create_app
from equitydesk.application.document_store import DocumentIngestionService
from equitydesk.bootstrap import build_container
from equitydesk.config import Settings
from equitydesk.infrastructure.db.backup import (
    BackupError,
    create_backup,
    prune_backups,
    restore_backup,
    verify_backup,
)
from equitydesk.infrastructure.db.raw_store import FilesystemRawStore
from equitydesk.infrastructure.db.sqlite import SCHEMA_VERSION

pytestmark = pytest.mark.e2e


@pytest.fixture
def populated(env, tmp_path: Path):
    container, make = env
    workflow, _ = make()
    run = container.research.create_run("AAPL", f.dt(2024, 1, 15))
    outcome = workflow.execute(run.research_run_id, INPUTS)
    assert outcome.run.status.value == "Completed"
    settings = container.settings
    return container, run.research_run_id, Path(container.repos.db.path), settings.data_dir / "raw"


def test_restore_drill(populated, tmp_path: Path) -> None:
    container, run_id, db_path, raw_root = populated
    info = create_backup(db_path, tmp_path / "backups", raw_root, clock=lambda: f.dt(2024, 1, 17))
    manifest = verify_backup(info.path)
    assert manifest["schema_version"] == SCHEMA_VERSION and manifest["includes_raw"]
    assert manifest["tables"]["research_runs"] == 1 and manifest["tables"]["research_reports"] == 1
    assert any(name.startswith("raw/") for name in manifest["files"])

    # restore into a fresh location, as on a replacement host
    new_db, new_raw = tmp_path / "restored" / "equitydesk.db", tmp_path / "restored" / "raw"
    restore_backup(info.path, new_db, new_raw)
    restored = build_container(Settings(data_dir=tmp_path / "restored"), db_path=new_db)
    original_report = container.repos.reports.reports_for_run(run_id)[-1]
    assert restored.repos.runs.get_run(run_id) == container.repos.runs.get_run(run_id)
    assert restored.repos.reports.reports_for_run(run_id)[-1] == original_report
    assert restored.repos.reports.latest_review(run_id) == container.repos.reports.latest_review(
        run_id
    )
    assert restored.repos.telemetry.get_telemetry(run_id) is not None
    # raw sources are present and still hash-verified
    ingest = DocumentIngestionService(restored.repos.documents, FilesystemRawStore(new_raw))
    for doc_id in restored.repos.snapshots.member_ids(run_id):
        assert ingest.load_content(restored.repos.documents.get_document(doc_id))
    client = TestClient(create_app(restored.research))
    assert (
        client.get(f"/research-runs/{run_id}/report").json()["report"]["report_id"]
        == original_report.report_id
    )


def test_restore_moves_existing_data_aside(populated, tmp_path: Path) -> None:
    _, _, db_path, raw_root = populated
    info = create_backup(db_path, tmp_path / "b", raw_root, clock=lambda: f.dt(2024, 1, 17))
    target = tmp_path / "live" / "equitydesk.db"
    target.parent.mkdir()
    target.write_bytes(b"current database")
    restore_backup(info.path, target, None, clock=lambda: f.dt(2024, 1, 18))
    kept = target.parent / "equitydesk.db.pre-restore-20240118T000000Z"
    assert kept.read_bytes() == b"current database"
    assert target.read_bytes() != b"current database"


def _rewrite(archive: Path, out: Path, mutate) -> Path:
    with tarfile.open(archive, "r:gz") as src, tarfile.open(out, "w:gz") as dst:
        for m in src.getmembers():
            data = src.extractfile(m).read()
            name, data = mutate(m.name, data)
            info = tarfile.TarInfo(name)
            info.size = len(data)
            dst.addfile(info, io.BytesIO(data))
    return out


def test_corrupted_or_unsafe_backups_are_refused(populated, tmp_path: Path) -> None:
    _, _, db_path, raw_root = populated
    good = create_backup(db_path, tmp_path / "b", raw_root, clock=lambda: f.dt(2024, 1, 17)).path

    tampered = _rewrite(good, tmp_path / "t.tar.gz",
                        lambda n, d: (n, d[:-10] + b"0" * 10) if n.startswith("raw/") else (n, d))  # fmt: skip
    with pytest.raises(BackupError, match="checksum"):
        verify_backup(tampered)

    def newer_schema(n: str, d: bytes):
        if n == "manifest.json":
            m = json.loads(d)
            m["files"]["extra.txt"] = "0" * 64
            return n, json.dumps(m).encode()
        return n, d

    with pytest.raises(BackupError, match="do not match"):
        verify_backup(_rewrite(good, tmp_path / "m.tar.gz", newer_schema))
    traversal = _rewrite(
        good, tmp_path / "x.tar.gz", lambda n, d: ("../evil" if n.startswith("raw/") else n, d)
    )
    with pytest.raises(BackupError, match="unsafe"):
        verify_backup(traversal)
    target = tmp_path / "never" / "equitydesk.db"
    with pytest.raises(BackupError):
        restore_backup(tampered, target)
    assert not target.exists()  # nothing restored from an invalid backup


def test_retention_prunes_oldest(populated, tmp_path: Path) -> None:
    _, _, db_path, _ = populated
    out = tmp_path / "keep"
    paths = [
        create_backup(db_path, out, None, clock=lambda d=d: f.dt(2024, 1, d)).path
        for d in (1, 2, 3)
    ]
    removed = prune_backups(out, keep=2)
    assert removed == [paths[0]] and sorted(out.glob("*.tar.gz")) == paths[1:]
    with pytest.raises(ValueError):
        prune_backups(out, keep=0)
