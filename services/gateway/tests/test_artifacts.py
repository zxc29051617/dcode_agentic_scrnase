"""Artifact manifest and content: what may be served, and what may not.

The security claim under test is not "paths are sanitised" but "there is no
way to name a file" — a client sends an opaque id, and only ids the manifest
produced resolve to anything. These tests try to break that from both ends:
by asking for things the whitelist excludes, and by planting escapes inside a
run directory.

Run with:  pytest tests/test_artifacts.py -v
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

FIXTURE_ROOT = Path(__file__).resolve().parents[3] / "fixtures" / "synthetic_runs"
FASTQ_RUN = "demo-2026-0003"


@pytest.fixture
def client(monkeypatch):
    monkeypatch.setenv("GATEWAY_RUNS_ROOT", str(FIXTURE_ROOT))
    from app.config import get_settings
    get_settings.cache_clear()
    from app.main import app
    with TestClient(app) as c:
        yield c
    get_settings.cache_clear()


def manifest(client, run_id=FASTQ_RUN):
    r = client.get(f"/v1/scientific-runs/{run_id}/artifacts")
    assert r.status_code == 200
    return r.json()


def by_kind(entries, kind):
    return [e for e in entries if e["kind"] == kind]


# --- the manifest lists exactly the whitelist -------------------------------

def test_manifest_lists_each_whitelisted_kind(client):
    kinds = {e["kind"] for e in manifest(client)}
    assert kinds == {
        "fastqc_html",
        "multiqc_html",
        "cellranger_web_summary",
        "report_html",
        "figure",
    }


def test_manifest_excludes_files_that_match_no_rule(client):
    listed = {e["relative_path"] for e in manifest(client)}
    # Each of these sits inside a directory the whitelist does reach, so only
    # the pattern keeps them out.
    for excluded in (
        "build_report/report_model.json",
        "build_report/figures/notes.txt",
        "fastq_qc/fastqc.log",
        "cellranger_count/SAMPLE/outs/possorted_genome_bam.bam",
    ):
        assert excluded not in listed


def test_manifest_carries_no_absolute_path(client):
    body = json.dumps(manifest(client))
    assert str(FIXTURE_ROOT) not in body
    assert "/home/" not in body


def test_manifest_of_a_matrix_run_has_no_upstream_qc(client):
    # demo-2026-0001 never ran FastQC or Cell Ranger, so those kinds are
    # absent rather than present-and-empty.
    kinds = {e["kind"] for e in manifest(client, "demo-2026-0001")}
    assert "fastqc_html" not in kinds
    assert "cellranger_web_summary" not in kinds
    assert "figure" in kinds


def test_manifest_for_unknown_run_is_404(client):
    assert client.get("/v1/scientific-runs/no-such-run/artifacts").status_code == 404


# --- content ---------------------------------------------------------------

def test_html_artifact_is_served_with_sandbox_headers(client):
    entry = by_kind(manifest(client), "multiqc_html")[0]
    r = client.get(f"/v1/scientific-runs/{FASTQ_RUN}/artifacts/{entry['artifact_id']}")
    assert r.status_code == 200
    assert r.headers["content-type"].startswith("text/html")
    assert "SYNTHETIC-MULTIQC-MARKER" in r.text
    # Third-party HTML: isolated even when opened directly, not only inside
    # the app's iframe.
    assert "sandbox" in r.headers["content-security-policy"]
    assert "allow-same-origin" not in r.headers["content-security-policy"]
    assert r.headers["x-content-type-options"] == "nosniff"


def test_png_figure_has_the_right_content_type(client):
    figures = by_kind(manifest(client), "figure")
    png = next(e for e in figures if e["name"].endswith(".png"))
    r = client.get(f"/v1/scientific-runs/{FASTQ_RUN}/artifacts/{png['artifact_id']}")
    assert r.status_code == 200
    assert r.headers["content-type"] == "image/png"
    assert r.content.startswith(b"\x89PNG\r\n")


def test_svg_figure_has_the_right_content_type(client):
    figures = by_kind(manifest(client), "figure")
    svg = next(e for e in figures if e["name"].endswith(".svg"))
    r = client.get(f"/v1/scientific-runs/{FASTQ_RUN}/artifacts/{svg['artifact_id']}")
    assert r.status_code == 200
    assert r.headers["content-type"] == "image/svg+xml"
    assert "SYNTHETIC-SVG-MARKER" in r.text


def test_cellranger_web_summary_is_served(client):
    entry = by_kind(manifest(client), "cellranger_web_summary")[0]
    r = client.get(f"/v1/scientific-runs/{FASTQ_RUN}/artifacts/{entry['artifact_id']}")
    assert r.status_code == 200
    assert "SYNTHETIC-CELLRANGER-MARKER" in r.text


def test_download_flag_switches_to_attachment(client):
    entry = by_kind(manifest(client), "fastqc_html")[0]
    base = f"/v1/scientific-runs/{FASTQ_RUN}/artifacts/{entry['artifact_id']}"
    assert "inline" in client.get(base).headers["content-disposition"]
    assert "attachment" in client.get(f"{base}?download=true").headers["content-disposition"]


# --- the id is not a path ---------------------------------------------------

@pytest.mark.parametrize("bad_id", [
    "../../../etc/passwd",
    "..%2F..%2Fetc%2Fpasswd",
    "build_report/report_model.json",
    "report.md",
    "0000000000000000",
])
def test_an_id_the_manifest_did_not_produce_resolves_to_nothing(client, bad_id):
    r = client.get(f"/v1/scientific-runs/{FASTQ_RUN}/artifacts/{bad_id}")
    assert r.status_code in (404, 405), f"{bad_id!r} returned {r.status_code}"
    assert "root:" not in r.text
    assert "internal" not in r.text


def test_an_empty_id_falls_back_to_the_manifest_rather_than_a_file(client):
    # `/artifacts/` redirects to `/artifacts`, so an empty id yields the
    # listing — which is already public for this run — and never a file.
    # Recorded as a test because "returns 200" on an artifact URL deserves an
    # explanation.
    r = client.get(f"/v1/scientific-runs/{FASTQ_RUN}/artifacts/")
    assert r.status_code == 200
    body = r.json()
    assert isinstance(body, list)
    assert all("artifact_id" in entry for entry in body)


def test_an_id_from_one_run_does_not_open_a_file_in_another(client):
    # Ids are hashes of a path relative to the run, so the same relative path
    # in two runs hashes alike — the run directory in the URL is what keeps
    # them apart, and it must actually be honoured.
    figure = next(e for e in by_kind(manifest(client), "figure") if e["name"].endswith(".png"))
    other = client.get(f"/v1/scientific-runs/demo-2026-0002/artifacts/{figure['artifact_id']}")
    # demo-2026-0002 halted before build_report, so it has no such figure.
    assert other.status_code == 404


# --- escapes planted inside a run ------------------------------------------

@pytest.fixture
def escape_client(tmp_path, monkeypatch):
    """A run directory containing symlinks that point outside it."""
    root = tmp_path / "runs"
    run = root / "escape-run-0001"
    (run / "build_report" / "figures").mkdir(parents=True)
    (run / "run_metadata.json").write_text(
        json.dumps({"runtime": {"started_at": "2026-01-01T00:00:00Z"}, "source": {}}),
        encoding="utf-8",
    )
    (run / "audit.jsonl").write_text("", encoding="utf-8")

    secret = tmp_path / "outside_secret.png"
    secret.write_bytes(b"\x89PNG\r\n SHOULD-NEVER-BE-SERVED")
    secret_html = tmp_path / "outside_secret.html"
    secret_html.write_text("<html>SHOULD-NEVER-BE-SERVED</html>", encoding="utf-8")

    # Both sit at whitelisted paths and match whitelisted patterns. Only the
    # resolve-and-contain check keeps them out.
    (run / "build_report" / "figures" / "stolen.png").symlink_to(secret)
    (run / "build_report" / "report.html").symlink_to(secret_html)

    # A real figure, so the test can tell "nothing is served" from "the run
    # is broken".
    (run / "build_report" / "figures" / "real.png").write_bytes(b"\x89PNG\r\n real")

    monkeypatch.setenv("GATEWAY_RUNS_ROOT", str(root))
    from app.config import get_settings
    get_settings.cache_clear()
    from app.main import app
    with TestClient(app) as c:
        yield c
    get_settings.cache_clear()


def test_symlinks_pointing_outside_the_run_are_not_listed(escape_client):
    entries = escape_client.get("/v1/scientific-runs/escape-run-0001/artifacts").json()
    names = {e["name"] for e in entries}
    assert "stolen.png" not in names
    assert "report.html" not in names
    assert "real.png" in names


def test_the_escaped_bytes_are_not_reachable_by_any_listed_id(escape_client):
    entries = escape_client.get("/v1/scientific-runs/escape-run-0001/artifacts").json()
    for entry in entries:
        r = escape_client.get(
            f"/v1/scientific-runs/escape-run-0001/artifacts/{entry['artifact_id']}"
        )
        assert b"SHOULD-NEVER-BE-SERVED" not in r.content


# --- no mutation ------------------------------------------------------------

@pytest.mark.parametrize("method", ["post", "put", "patch", "delete"])
def test_artifact_routes_reject_mutating_methods(client, method):
    entry = manifest(client)[0]
    for path in (
        f"/v1/scientific-runs/{FASTQ_RUN}/artifacts",
        f"/v1/scientific-runs/{FASTQ_RUN}/artifacts/{entry['artifact_id']}",
    ):
        assert getattr(client, method)(path).status_code in (404, 405)
