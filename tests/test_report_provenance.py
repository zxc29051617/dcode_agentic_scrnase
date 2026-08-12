"""The report says who decided what, what changed, and what judged it.

All of this was already being recorded — `judge_sessions`, `revisions`,
`resume_plan`, the gate decisions with their operator and their refused
overrides — and none of it reached the report. A reader could see the numbers
and not the history that produced them.

Two rules run through every test here:

  **Report, do not recompute.** Reuse comes from the `resume_plan` event the run
  wrote, not from which artifacts happen to exist now; a checkpoint was
  *continued* only if an event says so, not because the database is on disk.

  **A gap is not a guess.** A run recorded before a field existed shows
  `Not recorded`. Filling in today's username for a missing `operator` would be
  a claim about a person who may never have seen the run.

Run with `python tests/test_report_provenance.py` (or `python tests/run_all.py`).
"""

from __future__ import annotations

import json
import re
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from src import persistence  # noqa: E402
from src.policy import GatePolicy  # noqa: E402
from src.registry import call_skill  # noqa: E402
from src.run import continue_workflow, run_workflow  # noqa: E402
from tests import fixtures  # noqa: E402

PROJECT = "PBMC pilot study"
THRESHOLDS = {"min_genes": 1, "max_pct_mito": 100}

#: Planted in the environment and in an endpoint; must reach neither rendering.
SECRETS = ("sk-report-must-never-print-this-1234", "hunter2", "Bearer abcdef123456")


# --- helpers ----------------------------------------------------------------------------


#: Built once per run directory. Regenerating it re-gzips the matrix, and gzip
#: writes its own mtime into the header — so the bytes change, `plan_resume`
#: correctly reads that as new input, and a resume test would reuse nothing for
#: a reason that has nothing to do with what it is testing.
_BUNDLES: dict[str, tuple[dict, dict]] = {}


def _setup(root: Path):
    key = str(root)
    if key not in _BUNDLES:
        matrix = fixtures.bundle_for({"input_type": "matrix", "matrix_kind": "filtered"},
                                     root / "bundle")
        reference = fixtures.make_reference(root, "ref", genomes=["GRCh38"])
        _BUNDLES[key] = ({"paths": [str(matrix)]},
                         {"species": "human", "transcriptome": str(reference)})
    return _BUNDLES[key]


def _run(root: Path, *, config=None, decide=None, policy=None, **kwargs):
    bundle, base = _setup(root)
    return run_workflow(
        project=PROJECT, input_bundle=bundle, config={**base, **(config or {})},
        runs_dir=str(root / "runs"),
        policy=policy or GatePolicy(headless_decision="accept"),
        decide=decide, **kwargs,
    )


def _reports(final) -> tuple[str, str]:
    run_dir = Path(final["run_metadata_path"]).parent / "build_report"
    return ((run_dir / "report.md").read_text(encoding="utf-8"),
            (run_dir / "report.html").read_text(encoding="utf-8"))


def _section(markdown: str, key: str) -> str:
    match = re.search(rf"### {key} · .*?(?=\n### |\Z)", markdown, flags=re.DOTALL)
    assert match, f"section {key} is missing from the report"
    return match.group(0)


def _rebuild(run_dir: Path, **payload_extra) -> dict:
    """Re-render a report from a run directory, as the standalone CLI would."""
    return call_skill("build_report", {
        "step": "build_report", "run_id": run_dir.name, "run_dir": str(run_dir),
        "project": PROJECT, "config": {}, "input_bundle": {}, "sample_metadata": {},
        "artifacts": {}, **payload_extra,
    })


# --- identity ------------------------------------------------------------------------------


def test_the_project_name_reaches_the_report():
    """`build_payload` did not pass it, so every report was titled with a run id."""
    with tempfile.TemporaryDirectory() as tmp:
        final = _run(Path(tmp), config=THRESHOLDS)
        markdown, html = _reports(final)

    assert markdown.startswith(f"# {PROJECT}")
    assert f"<h1>{PROJECT}</h1>" in html
    assert PROJECT in _section(markdown, "P0")


def test_run_identity_reports_what_was_recorded():
    with tempfile.TemporaryDirectory() as tmp:
        final = _run(Path(tmp), config=THRESHOLDS)
        identity = _section(_reports(final)[0], "P0")

    for field in ("project", "run id", "input", "species", "started", "git commit",
                  "config sha256", "status at report time"):
        assert field in identity, f"P0 does not report {field}"
    assert final["run_id"] in identity
    assert "human" in identity


def test_identity_says_the_run_had_not_finished_rather_than_guessing_a_status():
    """The report is written during the run; there is no final status to state."""
    with tempfile.TemporaryDirectory() as tmp:
        identity = _section(_reports(_run(Path(tmp), config=THRESHOLDS))[0], "P0")
    assert "still running" in identity or "stopped the run" in identity
    assert "completed" not in identity.lower(), "the report cannot claim an outcome it precedes"


# --- human decisions -------------------------------------------------------------------------


def _revise_once(overrides: dict, operator: str = "alice"):
    used = {"done": False}

    def decide(request):
        if request.get("step") == "apply_cell_qc_filter" and not used["done"]:
            used["done"] = True
            return {"decision": "revise", "rationale": "supply thresholds",
                    "operator": operator, "overrides": overrides}
        return {"decision": "accept", "rationale": "", "operator": operator}

    return decide


def test_human_decisions_carry_every_recorded_field():
    with tempfile.TemporaryDirectory() as tmp:
        final = _run(
            Path(tmp),
            policy=GatePolicy(interactive=True),
            checkpointer=persistence.make_checkpointer("memory"),
            decide=_revise_once({"min_genes": "1", "max_pct_mito": "100",
                                 "celltypist_model": "wrong-gate"}),
        )
        markdown, html = _reports(final)

    decisions = _section(markdown, "P3")
    for column in ("gate", "step", "revise target", "decision", "operator",
                   "decided at", "rationale", "applied overrides", "refused"):
        assert column in decisions, f"P3 is missing the {column!r} column"
    assert "alice" in decisions, "the operator who decided has to be named"
    assert "revise" in decisions and "accept" in decisions
    assert "min_genes=1.0" in decisions, "what actually took effect"
    assert "celltypist_model is not offered at this gate" in decisions, (
        "a refused override and the reason have to be visible"
    )
    assert "alice" in html and "celltypist_model is not offered" in html


def test_a_revise_that_changed_nothing_reads_differently_from_one_that_did():
    """Answered at `normalize_hvg_prepare`, which warns but does not block.

    Not at `apply_cell_qc_filter`: revising there with no value leaves the
    thresholds unset, so the run correctly stops without a report and there is
    nothing to read.
    """
    used = {"done": False}

    def decide(request):
        if request.get("step") == "normalize_hvg_prepare" and not used["done"]:
            used["done"] = True
            return {"decision": "revise", "rationale": "just try again",
                    "operator": "alice"}
        return {"decision": "accept", "rationale": "", "operator": "alice"}

    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        final = _run(
            root, config=THRESHOLDS,
            policy=GatePolicy(interactive=True),
            checkpointer=persistence.make_checkpointer("memory"),
            decide=decide,
        )
        decisions = _section(_reports(final)[0], "P3")

    assert "none — the step re-ran unchanged" in decisions
    assert "supplied a new value" in decisions, "the summary counts them separately"


def test_a_missing_operator_is_not_filled_in_with_the_current_user():
    """An older run recorded no operator; the report must not invent one."""
    import getpass

    with tempfile.TemporaryDirectory() as tmp:
        run_dir = Path(tmp) / "run"
        (run_dir).mkdir(parents=True)
        (run_dir / "run_metadata.json").write_text(json.dumps({"run_id": "old"}), encoding="utf-8")
        (run_dir / "audit.jsonl").write_text(json.dumps({
            "ts": "2024-01-01T00:00:00+00:00", "event": "human_gate_close",
            "gate": "human_gate", "step": "run_pca", "decision": "accept",
        }) + "\n", encoding="utf-8")

        result = _rebuild(run_dir)
        markdown = (run_dir / "build_report" / "report.md").read_text(encoding="utf-8")

    assert result["status"] == "ok", result["errors"]
    decisions = _section(markdown, "P3")
    assert "Not recorded" in decisions
    assert getpass.getuser() not in decisions, "the report guessed who decided"


# --- judge provenance ---------------------------------------------------------------------------


def test_every_verdict_links_to_the_session_that_produced_it():
    with tempfile.TemporaryDirectory() as tmp:
        final = _run(Path(tmp), config=THRESHOLDS)
        markdown, html = _reports(final)
        sessions = json.loads(
            Path(final["run_metadata_path"]).read_text(encoding="utf-8"))["judge_sessions"]

    judge = _section(markdown, "P6")
    session_id = sessions[0]["session_id"]
    assert session_id in judge, "the session id has to appear"
    assert judge.count(session_id) > 5, "each verdict cites it"
    for column in ("mode", "backend", "default model", "temperature",
                   "structured output", "endpoint"):
        assert column in judge, f"P6 is missing {column!r}"
    assert "verdict" in judge and "model" in judge
    assert session_id in html


def test_the_stub_is_reported_as_having_called_no_model():
    with tempfile.TemporaryDirectory() as tmp:
        judge = _section(_reports(_run(Path(tmp), config=THRESHOLDS))[0], "P6")
    assert "none — no model was called" in judge
    assert "none — stub" in judge


def test_a_second_session_is_shown_with_its_mode():
    """A resumed run is judged twice; the report must not merge them.

    The resume changes `scmayomap_tissue` so the cut lands on
    `cross_check_annotation` and the report is rebuilt. An unchanged resume
    reuses `build_report` itself — correctly — and the report on disk would
    still be the first run's.
    """
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        first = _run(root, config=THRESHOLDS)
        second = _run(root, config={**THRESHOLDS, "scmayomap_tissue": "blood"},
                      resume_run_id=first["run_id"])
        judge = _section(_reports(second)[0], "P6")

    assert "new" in judge and "artifact_resume" in judge
    assert "judged by 2 sessions" in judge


def test_per_step_model_overrides_are_shown_without_repeating_the_default():
    """Twenty-five identical rows is not provenance anyone reads."""
    with tempfile.TemporaryDirectory() as tmp:
        run_dir = Path(tmp) / "run"
        run_dir.mkdir(parents=True)
        (run_dir / "run_metadata.json").write_text(json.dumps({
            "run_id": "r",
            "judge_sessions": [{
                "session_id": "js-00-abc123abc123", "mode": "new", "backend": "local",
                "default_model": "gpt-oss:120b",
                "step_models": {"run_pca": "gpt-oss:120b", "find_markers": "gpt-oss:120b",
                                "run_qc_metrics": "small:1b"},
                "base_prompt_sha256": "a" * 64,
                "step_prompts": {"run_qc_metrics": {"prompt_sha256": "b" * 64,
                                                    "addendum": "run_qc_metrics.md",
                                                    "addendum_sha256": "c" * 64}},
                "temperature": 0.0, "structured_output": "with_structured_output",
                "endpoint": "http://lab:11434/v1", "recorded_at": "2026-01-01T00:00:00+00:00",
            }],
        }), encoding="utf-8")
        (run_dir / "audit.jsonl").write_text("", encoding="utf-8")

        _rebuild(run_dir)
        judge = _section((run_dir / "build_report" / "report.md").read_text(encoding="utf-8"), "P6")

    assert "small:1b" in judge, "the override has to be visible"
    assert judge.count("gpt-oss:120b") == 1, (
        "the default belongs in the session row once, not on every step"
    )
    assert "run_qc_metrics.md" in judge, "the step prompt is part of what judged it"
    assert "a" * 16 in judge, "the base prompt hash"


# --- resume, revisions, checkpoint -----------------------------------------------------------------


def test_a_run_that_reused_nothing_says_so():
    with tempfile.TemporaryDirectory() as tmp:
        resume = _section(_reports(_run(Path(tmp), config=THRESHOLDS))[0], "P7")
    assert "did not reuse prior artifacts" in resume


def test_a_resumed_run_reports_what_it_reused_and_where_it_restarted():
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        first = _run(root, config=THRESHOLDS)
        second = _run(root, config={**THRESHOLDS, "min_genes": 2},
                      resume_run_id=first["run_id"])
        markdown, html = _reports(second)

    resume = _section(markdown, "P7")
    assert "apply_cell_qc_filter" in resume, "it re-ran from the step that reads min_genes"
    assert "run_qc_metrics" in resume, "and reused the one that does not"
    assert "min_genes" in resume, "the reason names the key that changed"
    assert "apply_cell_qc_filter" in html


def test_reuse_is_read_from_the_recorded_plan_not_from_the_artifacts():
    """An artifact present now may have been written by this run."""
    with tempfile.TemporaryDirectory() as tmp:
        run_dir = Path(tmp) / "run"
        (run_dir / "run_pca").mkdir(parents=True)
        (run_dir / "run_pca" / "adata.h5ad").write_bytes(b"an artifact that exists")
        (run_dir / "run_metadata.json").write_text(json.dumps({"run_id": "r"}), encoding="utf-8")
        (run_dir / "audit.jsonl").write_text("", encoding="utf-8")

        _rebuild(run_dir)
        resume = _section((run_dir / "build_report" / "report.md").read_text(encoding="utf-8"), "P7")

    assert "did not reuse prior artifacts" in resume, (
        "an artifact on disk is not evidence that it was reused"
    )


def test_a_revision_with_an_override_is_reported():
    with tempfile.TemporaryDirectory() as tmp:
        final = _run(
            Path(tmp),
            policy=GatePolicy(interactive=True),
            checkpointer=persistence.make_checkpointer("memory"),
            decide=_revise_once({"min_genes": "1", "max_pct_mito": "100"}),
        )
        revisions = _section(_reports(final)[0], "P8")

    assert "apply_cell_qc_filter" in revisions
    assert "min_genes=1.0" in revisions
    assert "config_sha256" in revisions, "the digest moving is the consequence that matters"


def test_a_run_with_no_revision_says_the_values_came_from_the_command_line():
    with tempfile.TemporaryDirectory() as tmp:
        revisions = _section(_reports(_run(Path(tmp), config=THRESHOLDS))[0], "P8")
    assert "No parameter was changed" in revisions


def test_a_checkpoint_on_disk_is_not_reported_as_having_been_continued():
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        paused = _run(root, policy=GatePolicy(interactive=True),
                      checkpointer_kind="sqlite", decide=None, config=THRESHOLDS)
        # The run stopped at a gate before the report; render one from the CLI.
        run_dir = Path(paused["run_metadata_path"]).parent
        _rebuild(run_dir)
        checkpoint = _section((run_dir / "build_report" / "report.md").read_text(encoding="utf-8"),
                              "P9")

    assert "durable checkpoint written" in checkpoint
    assert "yes" in checkpoint, "the database is there"
    assert "continued from a checkpoint | no" in checkpoint.replace("  ", " "), (
        "existence of the database must not be reported as a continue"
    )


def test_a_continued_run_reports_where_it_was_picked_up():
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        paused = _run(root, policy=GatePolicy(interactive=True),
                      checkpointer_kind="sqlite", decide=None, config=THRESHOLDS)
        continue_workflow(
            run_id=paused["run_id"], runs_dir=str(root / "runs"),
            policy=GatePolicy(interactive=True),
            decide=lambda _r: {"decision": "accept", "rationale": "", "operator": "bob"},
        )
        run_dir = Path(paused["run_metadata_path"]).parent
        markdown = (run_dir / "build_report" / "report.md").read_text(encoding="utf-8")

    checkpoint = _section(markdown, "P9")
    assert "continued from a checkpoint" in checkpoint
    assert "yes, 1 time(s)" in checkpoint
    assert paused["run_id"] in checkpoint, "the thread it was picked up on"
    judge = _section(markdown, "P6")
    assert "checkpoint_continue" in judge, "the session that continued it"


# --- old runs and broken records ----------------------------------------------------------------


def test_a_run_recorded_before_any_of_this_still_renders():
    with tempfile.TemporaryDirectory() as tmp:
        run_dir = Path(tmp) / "run"
        run_dir.mkdir(parents=True)
        (run_dir / "run_metadata.json").write_text(
            json.dumps({"run_id": "ancient", "runtime": {}, "source": {}}), encoding="utf-8")
        (run_dir / "audit.jsonl").write_text("", encoding="utf-8")

        result = _rebuild(run_dir)
        markdown = (run_dir / "build_report" / "report.md").read_text(encoding="utf-8")

    assert result["status"] == "ok", result["errors"]
    for key in ("P0", "P3", "P6", "P7", "P8", "P9"):
        _section(markdown, key)
    assert "Not recorded" in markdown


def test_malformed_metadata_warns_rather_than_dropping_the_section():
    with tempfile.TemporaryDirectory() as tmp:
        run_dir = Path(tmp) / "run"
        run_dir.mkdir(parents=True)
        (run_dir / "run_metadata.json").write_text(json.dumps({
            "run_id": "r",
            "judge_sessions": "this should have been a list",
            "revisions": {"not": "a list either"},
            "source": "nor this",
        }), encoding="utf-8")
        (run_dir / "audit.jsonl").write_text("", encoding="utf-8")

        result = _rebuild(run_dir)
        markdown = (run_dir / "build_report" / "report.md").read_text(encoding="utf-8")

    assert result["status"] == "ok", result["errors"]
    assert "could not be read" in markdown, "a malformed field has to be reported"
    for key in ("P0", "P8"):
        section = _section(markdown, key)
        assert "expected" in section or "Not recorded" in section


def test_a_verdict_citing_an_unknown_session_is_flagged():
    with tempfile.TemporaryDirectory() as tmp:
        run_dir = Path(tmp) / "run"
        run_dir.mkdir(parents=True)
        (run_dir / "run_metadata.json").write_text(
            json.dumps({"run_id": "r", "judge_sessions": []}), encoding="utf-8")
        (run_dir / "audit.jsonl").write_text(json.dumps({
            "ts": "2026-01-01T00:00:00+00:00", "event": "judge", "step": "run_pca",
            "verdict": "pass", "score": 80, "model": None,
            "judge_session_id": "js-99-doesnotexist",
        }) + "\n", encoding="utf-8")

        _rebuild(run_dir)
        judge = _section((run_dir / "build_report" / "report.md").read_text(encoding="utf-8"), "P6")

    assert "js-99-doesnotexist" in judge
    assert "not recorded" in judge.lower()


# --- both renderings, and no secrets ----------------------------------------------------------------


def test_markdown_and_html_carry_the_same_sections():
    """One ReportModel, two renderers — a field cannot exist in only one."""
    with tempfile.TemporaryDirectory() as tmp:
        final = _run(
            Path(tmp),
            policy=GatePolicy(interactive=True),
            checkpointer=persistence.make_checkpointer("memory"),
            decide=_revise_once({"min_genes": "1", "max_pct_mito": "100"}),
        )
        markdown, html = _reports(final)

    md_keys = set(re.findall(r"^### ([A-Z]\d+) · ", markdown, flags=re.MULTILINE))
    html_keys = set(re.findall(r"<h3>([A-Z]\d+) ·", html))
    assert md_keys == html_keys, f"only in markdown: {md_keys - html_keys}; " \
                                 f"only in html: {html_keys - md_keys}"
    assert {"P0", "P3", "P6", "P7", "P8", "P9"} <= md_keys


def test_no_secret_reaches_either_rendering():
    import os

    saved = {k: os.environ.get(k) for k in ("SCRNA_JUDGE_API_KEY", "SCRNA_JUDGE_BASE_URL")}
    os.environ["SCRNA_JUDGE_API_KEY"] = SECRETS[0]
    os.environ["SCRNA_JUDGE_BASE_URL"] = f"https://user:{SECRETS[1]}@lab.example:11434/v1"
    try:
        with tempfile.TemporaryDirectory() as tmp:
            final = _run(Path(tmp), config=THRESHOLDS)
            markdown, html = _reports(final)
    finally:
        for key, value in saved.items():
            if value is None:
                os.environ.pop(key, None)
            else:
                os.environ[key] = value

    for rendering, name in ((markdown, "report.md"), (html, "report.html")):
        lowered = rendering.lower()
        for secret in SECRETS:
            assert secret not in rendering, f"{secret!r} leaked into {name}"
        for word in ("api_key", "authorization", "bearer", "password"):
            assert word not in lowered, f"{word!r} appears in {name}"


def test_the_scientific_sections_are_untouched():
    """Adding provenance must not disturb what the report already said."""
    with tempfile.TemporaryDirectory() as tmp:
        markdown, _ = _reports(_run(Path(tmp), config=THRESHOLDS))

    for key in ("M1", "M2", "M3", "M4", "M5", "M6", "M7", "A3", "P1", "P2", "P5"):
        _section(markdown, key)
    assert "Main results" in markdown and "Technical appendix" in markdown


def main() -> int:
    tests = [value for name, value in sorted(globals().items()) if name.startswith("test_")]
    failures = []
    for test in tests:
        try:
            test()
            print(f"  ok    {test.__name__}")
        except AssertionError as exc:
            failures.append(test.__name__)
            print(f"  FAIL  {test.__name__}: {exc}")
    print(f"\n{len(tests) - len(failures)}/{len(tests)} passed")
    return 1 if failures else 0


if __name__ == "__main__":
    raise SystemExit(main())
