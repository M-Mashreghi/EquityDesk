"""Static guarantees of the container setup (the image itself is built and smoke-tested in CI)."""

from __future__ import annotations

import re
import threading
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path

import pytest

from equitydesk.bootstrap import build_container
from equitydesk.config import Settings
from equitydesk.healthcheck import main, probe

ROOT = Path(__file__).resolve().parents[2]
DOCKERFILE = (ROOT / "Dockerfile").read_text(encoding="utf-8")
COMPOSE = (ROOT / "docker-compose.yml").read_text(encoding="utf-8")
IGNORE = (ROOT / ".dockerignore").read_text(encoding="utf-8").splitlines()


def test_image_runs_as_non_root_with_healthcheck() -> None:
    users = re.findall(r"^USER\s+(\S+)", DOCKERFILE, re.M)
    assert users and users[-1] not in {"root", "0"}
    assert "useradd --system --uid 10001" in DOCKERFILE
    assert re.search(r"^HEALTHCHECK .*\n\s+CMD .*equitydesk.healthcheck", DOCKERFILE, re.M)
    assert chr(92) + "n" not in DOCKERFILE  # no literal escape sequences in instructions
    env_block = ("ENV PATH=" + DOCKERFILE.split("ENV PATH=", 1)[1]).splitlines()[:7]
    assert all(line.rstrip().endswith(chr(92)) for line in env_block)
    assert 'VOLUME ["/data"]' in DOCKERFILE and "-c constraints.txt" in DOCKERFILE


def test_no_secrets_enter_the_image() -> None:
    copies = re.findall(r"^COPY\s+(.+)$", DOCKERFILE, re.M)
    assert not any(".env" in c for c in copies)
    assert {".env", ".env.*", ".git", "secrets", "*.pem", "*.key"} <= set(IGNORE)
    assert not re.search(r"(?i)(api_key|token|password)\s*[:=]\s*\S+", DOCKERFILE + COMPOSE)


def test_compose_persists_data_and_exposes_only_localhost() -> None:
    assert "equitydesk-data:/data" in COMPOSE and re.search(
        r"^volumes:\n  equitydesk-data:", COMPOSE, re.M
    )
    ports = re.findall(r'- "([^"]+:\d+)"', COMPOSE)
    assert ports and all(p.startswith("127.0.0.1:") for p in ports)
    for service in ("api:", "dashboard:", "scheduler:"):
        assert f"\n  {service}" in COMPOSE
    assert COMPOSE.count("equitydesk.healthcheck") == 2 and "no-new-privileges:true" in COMPOSE
    assert "path: .env" in COMPOSE and "required: false" in COMPOSE
    # compose interpolates `$`: shell substitutions must be escaped as `$$(`
    assert "$(" not in COMPOSE.replace("$$(", "")


class _Handler(BaseHTTPRequestHandler):
    def do_GET(self) -> None:  # noqa: N802
        self.send_response(200 if self.path == "/ready" else 503)
        self.end_headers()

    def log_message(self, *args: object) -> None:
        pass


def test_healthcheck_probe() -> None:
    server = HTTPServer(("127.0.0.1", 0), _Handler)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    base = f"http://127.0.0.1:{server.server_port}"
    try:
        assert probe(f"{base}/ready") and main([f"{base}/ready"]) == 0
        assert not probe(f"{base}/down") and main([f"{base}/down"]) == 1
    finally:
        server.shutdown()
    assert not probe("http://127.0.0.1:9/ready", timeout=0.5)  # nothing listening
    with pytest.raises(ValueError):
        probe("http://example.com/ready")


def test_security_master_path_is_configurable(tmp_path: Path) -> None:
    master = tmp_path / "master.json"
    master.write_text(
        (ROOT / "data" / "fixtures" / "security_master.json").read_text(encoding="utf-8"),
        encoding="utf-8",
    )
    c = build_container(Settings(security_master_path=master), db_path=tmp_path / "x.db")
    assert c.research.resolve("AAPL").company is not None


CI = (ROOT / ".github" / "workflows" / "ci.yml").read_text(encoding="utf-8")
RELEASE = (ROOT / ".github" / "workflows" / "release.yml").read_text(encoding="utf-8")


def test_ci_runs_all_quality_gates_read_only() -> None:
    assert re.search(r"^permissions:\n  contents: read$", CI, re.M)
    assert "write" not in CI and "secrets." not in CI  # CI needs no secrets
    for gate in ("ruff check src tests", "ruff format --check src tests", "mypy", "pytest -q",
                 "test_point_in_time_audit.py", "pip-audit", "docker build", "/ready"):  # fmt: skip
        assert gate in CI, gate
    assert "needs: [quality, security]" in CI and "persist-credentials: false" in CI


def test_release_publishes_only_after_gates_with_scoped_permissions() -> None:
    assert re.search(r"^permissions:\n  contents: read$", RELEASE, re.M)  # default: read-only
    assert "uses: ./.github/workflows/ci.yml" in RELEASE
    publish = RELEASE[RELEASE.index("  publish:") :]
    assert "needs: gates" in publish
    assert "contents: write" in publish and "packages: write" in publish
    assert RELEASE.index("contents: write") > RELEASE.index("  publish:")  # write only in publish
    assert "Tag must match the package version" in publish
    assert "secrets.GITHUB_TOKEN" in publish and not re.search(
        r"secrets\.(?!GITHUB_TOKEN)", RELEASE
    )
