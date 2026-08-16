"""Generate the synthetic run fixture the gateway is developed and tested against.

Not a real run. No FASTQ, no `.h5ad`, no real donor, sample or operator
identity, no real hostname, no real git commit. Every value below is invented
for shape, not sampled from any actual analysis.

Run with:

    python fixtures/synthetic_runs/generate_fixture.py

It is deterministic and idempotent: re-running it overwrites the same two run
directories with the same bytes, and rewrites `MANIFEST.sha256`.
"""

from __future__ import annotations

import hashlib
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parent

RUN_A = "demo-2026-0001"  # completed, has a report
RUN_B = "demo-2026-0002"  # halted, waiting at a human gate
RUN_C = "demo-2026-0003"  # FASTQ route, carries FastQC and Cell Ranger numbers


def _write_json(path: Path, obj: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(obj, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")


def _write_jsonl(path: Path, events: list[dict]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("\n".join(json.dumps(e, ensure_ascii=False) for e in events) + "\n", encoding="utf-8")


def _metadata(run_id: str, condition_note: str) -> dict:
    return {
        "run_id": run_id,
        "runtime": {
            "started_at": "2026-01-01T00:00:00+00:00",
            "python_version": "3.11.15",
            "platform": "synthetic-fixture",
            "hostname": "synthetic-fixture",
        },
        "source": {
            "commit": "0" * 40,
            "branch": "synthetic",
            "dirty": False,
            "command": ["python", "-m", "src.run", "--input", "SYNTHETIC", "--species", "human"],
            "config": {"species": "human", "min_genes": 200, "max_pct_mito": 15},
            "config_sha256": hashlib.sha256(run_id.encode()).hexdigest(),
            "input_digest": hashlib.sha256(f"input:{run_id}".encode()).hexdigest(),
        },
        "packages": {"scanpy": "1.11.5", "anndata": "0.12.19", "langgraph": "1.2.10"},
        "seeds": {"random_state": 0},
        "study_design": {"n_libraries": 2, "n_conditions": 2, "note": condition_note},
        "judge_sessions": [
            {
                "session_id": "js-01-synthetic00",
                "recorded_at": "2026-01-01T00:00:01+00:00",
                "mode": "new",
                "hash_algorithm": "sha256",
                "backend": "stub",
                "default_model": None,
                "step_models": {},
                "base_prompt_sha256": None,
                "step_prompts": {},
                "temperature": None,
                "structured_output": None,
                "endpoint": None,
                "note": "synthetic fixture: no model was called",
            }
        ],
        "revisions": [],
    }


#: The FASTQ entry route, which is where FastQC and Cell Ranger run.
FASTQ_STEPS = ("resolve_reference", "fastq_preflight", "fastq_qc", "cellranger_count")

STEPS = ("ingest_validate", "matrix_preflight", "count_matrix_classify",
         "load_filtered_counts", "merge_samples", "post_load_validate",
         "run_qc_metrics", "apply_cell_qc_filter")


def _step_output(step: str) -> dict:
    return {
        "status": "ok",
        "warnings": [],
        "errors": [],
        "metrics": {"n_obs": 1200, "n_vars": 20000} if step == "post_load_validate" else {},
    }


# --- upstream QC, in the shape the real skills record ------------------------
#
# Field names and nesting are taken from `skills/fastq_qc/fastq_qc.py` and
# `skills/cellranger_count/cellranger_count.py`, not invented: `reports` and
# `per_read_role` come from `fastq_qc._summarize`, and `libraries[].
# metrics_summary` is Cell Ranger's own `metrics_summary.csv` first row as the
# skill parses it. The *values* are fabricated; the *shape* is the real one,
# which is what makes this a usable test of the projection.
#
# The absolute paths below (`outs`, `web_summary`, `bam`) are deliberately
# present and deliberately fake: the gateway must drop them, and a fixture
# with no paths in it could not prove that it does.


def _fastq_qc_output() -> dict:
    def report(name: str, role: str, q30: float, dup: float, failed: list[str]) -> dict:
        return {
            "file": name,
            "read_role": role,
            "total_sequences": 5_000_000,
            "sequence_length": "28" if role == "R1" else "91",
            "pct_gc": "48",
            "q30_fraction": q30,
            "duplicate_fraction": dup,
            "max_adapter_pct": 1.2 if role == "R2" else 0.0,
            "modules_failed": failed,
            "modules_warned": ["Per base sequence content"] if role == "R1" else [],
            "modules_expected_for_read_role": ["Per base sequence content", "Sequence Duplication Levels"],
            "module_status": {"Basic Statistics": "pass", "Adapter Content": "pass"},
        }

    reports = [
        report("SAMPLE_S1_L001_R1_001.fastq.gz", "R1", 0.9612, 0.71, []),
        report("SAMPLE_S1_L001_R2_001.fastq.gz", "R2", 0.9218, 0.68, ["Overrepresented sequences"]),
    ]
    return {
        "status": "ok",
        "reports": reports,
        "notes": [
            "cDNA (R2) duplication is 68%. Expected for scRNA-seq: the protocol "
            "amplifies by PCR and UMIs collapse the copies, which FastQC cannot see."
        ],
        # An absolute path the projection must not pass through.
        "multiqc_report": "/synthetic/runs/demo/fastq_qc/multiqc_report.html",
        "report_dir": "/synthetic/runs/demo/fastq_qc",
        "per_read_role": {
            "R1": {"n_files": 1, "q30_fraction": 0.9612, "duplicate_fraction": 0.71,
                   "total_sequences": 5_000_000},
            "R2": {"n_files": 1, "q30_fraction": 0.9218, "duplicate_fraction": 0.68,
                   "total_sequences": 5_000_000},
        },
        "module_failures": {"SAMPLE_S1_L001_R2_001.fastq.gz": ["Overrepresented sequences"]},
        "expected_module_flags": ["Per base sequence content", "Sequence Duplication Levels"],
        "metrics": {
            "n_files": 2,
            "total_sequences": 10_000_000,
            "q30_r2": 0.9218,
            "pct_duplicate_r2": 0.68,
            "max_adapter_pct": 1.2,
            "n_module_failures": 1,
        },
        "warnings": ["SAMPLE_S1_L001_R2_001.fastq.gz: FastQC failed Overrepresented sequences"],
        "errors": [],
    }


def _cellranger_output() -> dict:
    return {
        "status": "ok",
        "libraries": [
            {
                "library_id": "SAMPLE",
                "chemistry": "SC3Pv3",
                # Every one of these is an absolute path and must be dropped.
                "outs": "/synthetic/runs/demo/cellranger_count/SAMPLE/outs",
                "raw_feature_bc_matrix": "/synthetic/.../raw_feature_bc_matrix.h5",
                "filtered_feature_bc_matrix": "/synthetic/.../filtered_feature_bc_matrix.h5",
                "bam": "/synthetic/.../possorted_genome_bam.bam",
                "web_summary": "/synthetic/.../web_summary.html",
                # Cell Ranger's own column names, as the CSV carries them.
                "metrics_summary": {
                    "Estimated Number of Cells": "1,206",
                    "Mean Reads per Cell": "41,458",
                    "Median Genes per Cell": "3,201",
                    "Number of Reads": "50,000,000",
                    "Valid Barcodes": "97.4%",
                    "Sequencing Saturation": "62.1%",
                    "Q30 Bases in RNA Read": "92.2%",
                    "Reads Mapped Confidently to Transcriptome": "78.9%",
                    "Fraction Reads in Cells": "91.3%",
                    "Total Genes Detected": "21,004",
                },
            }
        ],
        "metrics": {"n_libraries": 1},
        "warnings": [],
        "errors": [],
    }


def _audit_events(run_id: str, steps: list[str], *, halt_at: str | None) -> list[dict]:
    events: list[dict] = []
    for step in steps:
        events.append({"ts": "2026-01-01T00:01:00+00:00", "event": "step_start",
                        "step": step, "run_id": run_id})
        events.append({"ts": "2026-01-01T00:01:05+00:00", "event": "step_end",
                        "step": step, "status": "ok", "warnings": [], "errors": [],
                        "output_keys": sorted(_step_output(step))})
        events.append({"ts": "2026-01-01T00:01:06+00:00", "event": "judge",
                        "judge_tool": f"judge_{step}", "model": None,
                        "judge_session_id": "js-01-synthetic00",
                        "step": step, "verdict": "pass", "score": 80,
                        "reasons": [f"{step} completed with no warnings or errors"],
                        "evidence": {}, "suggested_action": "continue",
                        "needs_human_review": False, "advice": []})
    if halt_at:
        events.append({"ts": "2026-01-01T00:02:00+00:00", "event": "human_gate_open",
                        "gate": "human_gate", "step": halt_at, "revise_target": halt_at,
                        "revisable": ["min_genes", "max_pct_mito"],
                        "verdict": "warn", "score": 55,
                        "reasons": ["synthetic: threshold not yet chosen"],
                        "suggested_action": "review the evidence", "advice": [],
                        "evidence": {"candidate_thresholds": [200, 500, 1000]}})
    return events


def _report_md(run_id: str) -> str:
    return f"""# Report — {run_id}

_Synthetic fixture. No real sample or patient data._

## Tier 1 — main results

M1. Cell-retention funnel: available (synthetic counts only).

## Tier 3 — audit

P0. Run identity: species=human, run_id={run_id}, git commit=0000000000000000000000000000000000000000 (synthetic).
"""


def build_run(run_id: str, *, halted_at: str | None, fastq_route: bool = False) -> None:
    run_dir = ROOT / run_id
    steps = (list(FASTQ_STEPS) if fastq_route else []) + list(STEPS)
    if halted_at:
        steps = steps[: steps.index(halted_at) + 1]

    _write_json(run_dir / "run_metadata.json", _metadata(
        run_id, "resting vs stimulated, synthetic" if not halted_at else "awaiting QC threshold, synthetic"))
    _write_jsonl(run_dir / "audit.jsonl", _audit_events(run_id, steps, halt_at=halted_at))
    for step in steps:
        if step == "fastq_qc":
            _write_json(run_dir / step / "output.json", _fastq_qc_output())
        elif step == "cellranger_count":
            _write_json(run_dir / step / "output.json", _cellranger_output())
        else:
            _write_json(run_dir / step / "output.json", _step_output(step))
    if not halted_at:
        (run_dir / "report.md").write_text(_report_md(run_id), encoding="utf-8")


def sha256_manifest() -> None:
    lines = []
    for path in sorted(ROOT.rglob("*")):
        if path.is_file() and path.name not in {"MANIFEST.sha256", "generate_fixture.py", "README.md"}:
            digest = hashlib.sha256(path.read_bytes()).hexdigest()
            lines.append(f"{digest}  {path.relative_to(ROOT)}")
    (ROOT / "MANIFEST.sha256").write_text("\n".join(lines) + "\n", encoding="utf-8")


if __name__ == "__main__":
    build_run(RUN_A, halted_at=None)
    build_run(RUN_B, halted_at="apply_cell_qc_filter")
    build_run(RUN_C, halted_at=None, fastq_route=True)
    sha256_manifest()
    print(f"wrote {RUN_A} (completed), {RUN_B} (halted at gate) and "
          f"{RUN_C} (FASTQ route) under {ROOT}")
