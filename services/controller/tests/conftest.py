"""Fixtures for the controller tests.

Everything here is synthetic. No test in this directory reaches a real dataset,
a real model, Cell Ranger, or the network, and the worker tests drive a fake
executor rather than the real one — `tests/test_web_intake_flow.py` at the
repository root is where the real seam is exercised against the real graph.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

REPO_ROOT = Path(__file__).resolve().parents[3]
SERVICE_ROOT = Path(__file__).resolve().parents[1]
for entry in (str(REPO_ROOT), str(SERVICE_ROOT)):
    if entry not in sys.path:
        sys.path.insert(0, entry)


@pytest.fixture
def data_root(tmp_path: Path) -> Path:
    """An allowlisted data root holding one matrix bundle and one manifest."""
    root = tmp_path / "data"
    bundle = root / "pbmc_demo" / "outs"
    bundle.mkdir(parents=True)
    (bundle / "filtered_feature_bc_matrix.h5").write_bytes(b"not really an h5")

    fastq = root / "fastq_demo" / "fastqs"
    fastq.mkdir(parents=True)
    (fastq / "demo_S1_L001_R1_001.fastq.gz").write_bytes(b"")

    manifest = root / "designs" / "pbmc.csv"
    manifest.parent.mkdir(parents=True)
    manifest.write_text(
        "library_id,sample_id,donor_id,condition,technical_batch\n"
        "lib1,s1,d1,control,b1\n"
        "lib2,s2,d2,treated,b2\n",
        encoding="utf-8",
    )
    return root


@pytest.fixture
def catalog_file(tmp_path: Path, data_root: Path) -> Path:
    path = tmp_path / "catalog.json"
    path.write_text(
        json.dumps({
            "datasets": {
                "pbmc_demo": {
                    "path": str(data_root / "pbmc_demo" / "outs"),
                    "display_name": "PBMC demo (counted)",
                    "kind": "matrix",
                    "species": "human",
                    "description": "A Cell Ranger outs directory.",
                },
                "fastq_demo": {
                    "path": str(data_root / "fastq_demo" / "fastqs"),
                    "display_name": "FASTQ demo",
                    "kind": "fastq",
                    "species": "human",
                },
            },
            "manifests": {
                "pbmc_demo": {
                    "path": str(data_root / "designs" / "pbmc.csv"),
                    "display_name": "PBMC demo design",
                },
            },
        }),
        encoding="utf-8",
    )
    return path


@pytest.fixture
def runs_root(tmp_path: Path) -> Path:
    root = tmp_path / "runs"
    root.mkdir()
    return root


@pytest.fixture
def settings_env(monkeypatch, tmp_path: Path, catalog_file: Path, data_root: Path, runs_root: Path):
    monkeypatch.setenv("CONTROLLER_DB", str(tmp_path / "controller" / "controller.sqlite"))
    monkeypatch.setenv("CONTROLLER_RUNS_ROOT", str(runs_root))
    monkeypatch.setenv("CONTROLLER_DATA_ROOTS", str(data_root))
    monkeypatch.setenv("CONTROLLER_CATALOG", str(catalog_file))
    from app.config import get_settings

    get_settings.cache_clear()
    yield
    get_settings.cache_clear()


@pytest.fixture
def client(settings_env) -> TestClient:
    from app.main import app

    with TestClient(app) as c:
        yield c


@pytest.fixture
def store(settings_env):
    from app.config import get_settings
    from app.store import Store

    s = Store(get_settings().db_path)
    yield s
    s.close()


def write_gate_event(runs_root: Path, run_id: str, **fields) -> None:
    """Append a `human_gate_open` to a synthetic run's audit log."""
    run_dir = runs_root / run_id
    run_dir.mkdir(parents=True, exist_ok=True)
    (run_dir / "run_metadata.json").write_text(json.dumps({"source": {}}), encoding="utf-8")
    event = {
        "ts": "2026-01-01T00:00:00Z",
        "event": "human_gate_open",
        "gate": "human_gate",
        "step": "run_qc_metrics",
        "revise_target": "run_qc_metrics",
        "revisable": [],
        "verdict": "warn",
        "score": 60,
        "reasons": ["synthetic"],
        "suggested_action": None,
        "advice": [],
        "evidence": {},
        **fields,
    }
    with (run_dir / "audit.jsonl").open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(event) + "\n")


def write_gate_close(runs_root: Path, run_id: str) -> None:
    run_dir = runs_root / run_id
    with (run_dir / "audit.jsonl").open("a", encoding="utf-8") as handle:
        handle.write(json.dumps({"ts": "2026-01-01T00:00:01Z", "event": "human_gate_close"}) + "\n")
