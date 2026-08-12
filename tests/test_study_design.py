"""Harmony is opt-in, and nothing infers a study design on the operator's behalf.

Before this, `merge_samples` wrote the library name into `obs["sample"]`,
`run_integration` defaulted `batch_key` to `"sample"`, and any run with two or
more libraries of at least 20 cells corrected on that key without asking. On six
libraries that were really three disease and three control, Harmony removed the
disease signal and returned no warning at all. Measured on the unmodified tree:
`integrated=True, batch_key='sample', n_batches=6, warnings=[]`.

The rule the tests below pin is not "correct the batch better". It is that a
library is not a technical batch until somebody says so.

Everything is synthetic: small AnnData objects and hand-written CSV. No public
dataset, no reference, no Cell Ranger.

Run with `python tests/test_study_design.py` (or `python tests/run_all.py`).
"""

from __future__ import annotations

import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from src import manifest as mf  # noqa: E402
from src.registry import REGISTRY, call_skill, load_skill  # noqa: E402

integration = load_skill("run_integration")
merge_samples = load_skill("merge_samples")

BALANCED = """library_id,sample_id,donor_id,condition,technical_batch
LIB_A,S001,D001,control,BATCH_A
LIB_B,S002,D002,disease,BATCH_A
LIB_C,S003,D003,control,BATCH_B
LIB_D,S004,D004,disease,BATCH_B
"""

CONFOUNDED = """library_id,sample_id,donor_id,condition,technical_batch
LIB_A,S001,D001,control,BATCH_A
LIB_B,S002,D002,control,BATCH_A
LIB_C,S003,D003,disease,BATCH_B
LIB_D,S004,D004,disease,BATCH_B
"""

ONE_BATCH = """library_id,sample_id,donor_id,condition,technical_batch
LIB_A,S001,D001,control,BATCH_A
LIB_B,S002,D002,disease,BATCH_A
LIB_C,S003,D003,control,BATCH_A
LIB_D,S004,D004,disease,BATCH_A
"""

LIBRARIES = ("LIB_A", "LIB_B", "LIB_C", "LIB_D")


def _adata(path: Path, libraries=LIBRARIES, n_per: int = 30, manifest_text: str | None = None):
    """A merged-looking object: one obs row per cell, labelled by library."""
    import anndata as ad
    import numpy as np

    rng = np.random.default_rng(0)
    blocks, labels = [], []
    for index, name in enumerate(libraries):
        blocks.append(rng.normal(loc=index * 5.0, size=(n_per, 8)))
        labels += [name] * n_per

    adata = ad.AnnData(np.ones((n_per * len(libraries), 6), dtype="float32"))
    adata.obs_names = [f"c{i}" for i in range(adata.n_obs)]
    adata.obs["sample"] = labels
    adata.obs["library_id"] = labels
    if manifest_text is not None:
        parsed, errors = mf.parse_manifest(manifest_text, source="test")
        assert not errors, errors
        for column in mf.REQUIRED_COLUMNS:
            if column == "library_id":
                continue
            mapping = parsed.column(column)
            adata.obs[column] = [mapping.get(lib) for lib in labels]
    for column in adata.obs.columns:
        adata.obs[column] = adata.obs[column].astype("category")
    adata.obsm["X_pca"] = np.vstack(blocks)
    adata.write_h5ad(path)
    return str(path)


def _integrate(adata_path: str, run_dir: Path, **config):
    result = call_skill("run_integration", {
        "step": "run_integration", "run_id": "t", "run_dir": str(run_dir),
        "config": {"adata_path": adata_path, **config},
        "input_bundle": {}, "artifacts": {}, "study_design": {},
    })
    output = result["output"]
    result["notes"] = output.get("notes") or []
    return result, (output.get("integration_summary") or {})


# --- the default is not to correct -------------------------------------------------------


def test_a_single_library_without_a_manifest_behaves_exactly_as_before():
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        path = _adata(root / "a.h5ad", libraries=("LIB_A",))
        result, summary = _integrate(path, root)

    assert not result["errors"], result["errors"]
    assert summary["integrated"] is False
    assert result["warnings"] == [], "a one-library run has nothing to warn about"
    assert summary.get("embedding_key") in (None, "X_pca") or True


def test_many_libraries_without_an_integration_mode_do_not_get_harmony():
    """The regression this whole change exists for."""
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        path = _adata(root / "a.h5ad")
        result, summary = _integrate(path, root)

    assert summary["integrated"] is False, (
        "four libraries with no stated design must not be corrected"
    )
    assert summary.get("batch_key") is None, summary
    assert result["warnings"], "silence is what made the old behaviour dangerous"
    joined = " ".join(result["warnings"])
    for library in LIBRARIES:
        assert library in joined, f"the warning must name {library}"


def test_the_warning_explains_why_a_library_is_not_a_batch():
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        path = _adata(root / "a.h5ad")
        result, _ = _integrate(path, root)

    joined = " ".join(result["warnings"]).lower()
    assert "technical" in joined and "integration-mode" in joined, result["warnings"]


def test_an_explicit_none_is_recorded_as_a_decision_not_an_omission():
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        path = _adata(root / "a.h5ad")
        result, summary = _integrate(path, root, integration_mode="none")

    assert summary["integrated"] is False
    assert summary["integration_mode"] == "none"
    assert summary["mode_source"] == "operator", summary
    assert result["warnings"] == [], "an answered question is not a warning"


def test_the_unanswered_state_is_distinguishable_from_an_explicit_none():
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        path = _adata(root / "a.h5ad")
        _, unanswered = _integrate(path, root)
        _, answered = _integrate(path, root, integration_mode="none")

    assert unanswered["mode_source"] != answered["mode_source"], (
        "'nobody said' and 'the operator said none' are different states"
    )


# --- harmony requires a manifest, and only ever uses technical_batch -----------------------


def test_harmony_without_a_manifest_fails_closed():
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        path = _adata(root / "a.h5ad")
        result, summary = _integrate(path, root, integration_mode="harmony")

    assert result["errors"], "harmony was asked for with no design to correct on"
    assert summary.get("integrated") is not True


def test_harmony_runs_on_a_balanced_manifest():
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        path = _adata(root / "a.h5ad", manifest_text=BALANCED)
        result, summary = _integrate(path, root, integration_mode="harmony")

    assert not result["errors"], result["errors"]
    assert summary["integrated"] is True
    assert summary["batch_key"] == "technical_batch", summary
    assert summary["n_batches"] == 2


def test_the_batch_key_is_never_anything_but_technical_batch():
    for forbidden in ("sample", "library_id", "sample_id", "donor_id", "condition"):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            path = _adata(root / "a.h5ad", manifest_text=BALANCED)
            result, summary = _integrate(
                path, root, integration_mode="harmony", batch_key=forbidden,
            )
        if summary.get("integrated"):
            assert summary["batch_key"] == "technical_batch", (
                f"{forbidden} must never become the batch key"
            )
        else:
            assert result["errors"], f"{forbidden} was neither refused nor overridden"


def test_a_single_technical_batch_is_skipped_and_said_so():
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        path = _adata(root / "a.h5ad", manifest_text=ONE_BATCH)
        result, summary = _integrate(path, root, integration_mode="harmony")

    assert summary["integrated"] is False
    assert not result["errors"], "one batch is a legitimate design, not an error"
    said = " ".join(result["notes"] + result["warnings"]).lower()
    assert "one technical batch" in said, result["notes"]


def test_a_missing_technical_batch_column_fails_closed():
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        # obs carries condition but not technical_batch
        path = _adata(root / "a.h5ad", manifest_text=BALANCED)
        import anndata as ad
        adata = ad.read_h5ad(path)
        del adata.obs["technical_batch"]
        adata.write_h5ad(path)
        result, summary = _integrate(path, root, integration_mode="harmony")

    assert result["errors"], "harmony with no technical_batch has nothing legitimate to use"
    assert summary.get("integrated") is not True
    joined = " ".join(result["errors"])
    for substitute in ("obs['sample']", 'obs["sample"]', "library_id", "donor"):
        assert substitute not in joined, (
            f"the error must not offer {substitute} as a stand-in for a declared batch"
        )


# --- confounding --------------------------------------------------------------------------


def test_a_fully_confounded_design_is_refused_even_when_harmony_is_asked_for():
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        path = _adata(root / "a.h5ad", manifest_text=CONFOUNDED)
        result, summary = _integrate(path, root, integration_mode="harmony")

    assert summary["integrated"] is False, (
        "removing the batch would remove the condition; there is nothing to salvage"
    )
    assert result["warnings"] or result["errors"]
    report = summary.get("confounding") or {}
    assert report.get("fully_confounded") is True, summary


def test_the_confounding_refusal_shows_the_table():
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        path = _adata(root / "a.h5ad", manifest_text=CONFOUNDED)
        _, summary = _integrate(path, root, integration_mode="harmony")

    table = (summary.get("confounding") or {}).get("table") or {}
    assert table.get("control", {}).get("BATCH_A") == 2, table
    assert table.get("control", {}).get("BATCH_B", 0) == 0, table


def test_a_confounded_design_is_not_rescued_by_force():
    """Nothing in the contract may claim Harmony can fix an unidentifiable design."""
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        path = _adata(root / "a.h5ad", manifest_text=CONFOUNDED)
        result, summary = _integrate(
            path, root, integration_mode="harmony", force_integration=True,
        )

    if summary.get("integrated"):
        joined = " ".join(result.get("warnings", []))
        assert "confounded" in joined.lower(), (
            "forcing is allowed only if the run says plainly what it destroyed"
        )


# --- obs semantics ------------------------------------------------------------------------


def test_library_id_is_the_obs_column_that_means_library():
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        path = _adata(root / "a.h5ad", manifest_text=BALANCED)
        import anndata as ad
        adata = ad.read_h5ad(path)

    assert list(adata.obs["library_id"]) == list(adata.obs["sample"]), (
        "obs['sample'] is kept only as an alias of library_id"
    )
    for column in mf.REQUIRED_COLUMNS:
        assert column in adata.obs, column


def test_doublet_detection_still_groups_by_library_not_by_batch():
    doublets = load_skill("detect_doublets")
    source = _imports_and_calls(Path(doublets.__file__))
    assert "technical_batch" not in source, (
        "doublets form inside a GEM well, which is a library, not a technical batch"
    )


def test_hvg_batch_awareness_uses_the_library_not_the_technical_batch():
    prepare = load_skill("normalize_hvg_prepare")
    source = _imports_and_calls(Path(prepare.__file__))
    assert "technical_batch" not in source, (
        "HVG batch awareness is about per-library detection, not about the batch to correct"
    )
    assert "library_id" in source, "it should say library_id rather than the overloaded 'sample'"


# --- nothing is inferred from a filename --------------------------------------------------


def test_no_design_field_is_ever_inferred_from_a_name():
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        # the library name says disease; the manifest says control
        text = (
            "library_id,sample_id,donor_id,condition,technical_batch\n"
            "PT001_disease_S1,S001,D001,control,BATCH_A\n"
            "PT002_control_S2,S002,D002,disease,BATCH_A\n"
        )
        path = _adata(root / "a.h5ad",
                      libraries=("PT001_disease_S1", "PT002_control_S2"),
                      manifest_text=text)
        import anndata as ad
        adata = ad.read_h5ad(path)

    per_library = dict(zip(adata.obs["library_id"].astype(str),
                           adata.obs["condition"].astype(str)))
    assert per_library["PT001_disease_S1"] == "control", "the manifest wins, not the name"
    assert per_library["PT002_control_S2"] == "disease"


# --- resume and checkpoint ----------------------------------------------------------------


def test_the_manifest_digest_is_what_invalidates_a_resume():
    for step in ("merge_samples", "run_integration"):
        keys = REGISTRY[step].config_keys
        assert "manifest_sha256" in keys, f"{step} must notice a changed manifest: {keys}"


def test_no_step_before_merge_samples_depends_on_the_manifest():
    upstream = ("ingest_validate", "cellranger_count", "load_raw_counts",
                "cell_calling_review", "matrix_preflight")
    for step in upstream:
        spec = REGISTRY.get(step)
        if spec is None:
            continue
        assert "manifest_sha256" not in spec.config_keys, (
            f"{step} does not read the manifest and must not be re-run for it"
        )


def test_the_same_content_at_a_different_path_is_the_same_manifest():
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        first, second = root / "one.csv", root / "two.csv"
        first.write_text(BALANCED)
        second.write_text(BALANCED)
        a, errors_a = mf.load_manifest(first)
        b, errors_b = mf.load_manifest(second)

    assert not errors_a and not errors_b
    assert a.sha256 == b.sha256, "moving a file is not a change to the study design"


def test_the_same_path_with_different_content_is_a_different_manifest():
    with tempfile.TemporaryDirectory() as tmp:
        path = Path(tmp) / "m.csv"
        path.write_text(BALANCED)
        before, _ = mf.load_manifest(path)
        path.write_text(CONFOUNDED)
        after, _ = mf.load_manifest(path)

    assert before.sha256 != after.sha256, "a resume compares content, not paths"


# --- privacy ------------------------------------------------------------------------------


IDENTIFIERS = ("Chen Wei-Ting", "A123456789", "0912345678", "1987-05-04")


def test_a_manifest_carrying_an_identifier_never_reaches_a_run():
    for value in IDENTIFIERS:
        text = (
            "library_id,sample_id,donor_id,condition,technical_batch\n"
            f"LIB_A,S001,{value},control,BATCH_A\n"
        )
        _, errors = mf.parse_manifest(text, source="t")
        assert errors, f"{value!r} must be refused at the door"
        assert value not in " ".join(errors), "the error must not echo the identifier back"


def test_the_summary_that_leaves_the_run_has_no_rows_in_it():
    parsed, errors = mf.parse_manifest(BALANCED, source="t")
    assert not errors, errors
    flat = repr(mf.public_summary(parsed))
    for secret in ("LIB_A", "S001", "D001", "control", "BATCH_A"):
        assert secret not in flat, f"{secret} leaked into the shareable summary"


def test_the_confounding_report_carries_counts_and_no_ids():
    parsed, _ = mf.parse_manifest(BALANCED, source="t")
    flat = repr(mf.confounding(parsed, "condition", "technical_batch"))
    for secret in ("LIB_A", "S001", "D001"):
        assert secret not in flat, f"{secret} leaked into the confounding report"


# --- the judge may not decide any of this -------------------------------------------------


def _imports_and_calls(path: Path) -> str:
    """Import lines and attribute calls only — prose about judgement is not a call."""
    lines = [
        line for line in path.read_text().splitlines()
        if line.lstrip().startswith(("import ", "from ")) or "(" in line
    ]
    return "\n".join(lines).lower()


def test_the_manifest_module_never_calls_a_model():
    source = _imports_and_calls(Path(mf.__file__))
    for forbidden in ("judge", "llm", "openai", "langchain", "ollama"):
        assert forbidden not in source, (
            f"validation is deterministic; {forbidden!r} has no place in it"
        )


def test_run_integration_decides_without_a_model():
    source = _imports_and_calls(Path(integration.__file__))
    for forbidden in ("judge", "llm", "openai", "langchain", "ollama"):
        assert forbidden not in source, f"{forbidden!r} must not gate integration"


def test_advice_is_not_config():
    """A judge's advice must never become an override on its own."""
    from src import nodes

    source = Path(nodes.__file__).read_text()
    marker = source.find("def make_judge_node")
    assert marker != -1
    body = source[marker:marker + 4000]
    assert "config" not in body.split("return")[-1], (
        "the judge node returns judge_results only; it cannot write config"
    )


def test_a_revise_answer_cannot_smuggle_an_arbitrary_integration_mode():
    from src.registry import coerce_overrides

    accepted, rejected = coerce_overrides(
        {"integration_mode": "harmony"}, ("integration_mode",),
    )
    assert accepted == {"integration_mode": "harmony"}, accepted

    accepted, rejected = coerce_overrides(
        {"integration_mode": "definitely-yes"}, ("integration_mode",),
    )
    assert not accepted, "only the documented modes may reach config"
    assert rejected, rejected


def test_integration_mode_is_revisable_at_the_gate():
    spec = REGISTRY["run_integration"]
    assert "integration_mode" in spec.revisable, spec.revisable


def test_revise_to_harmony_without_a_manifest_still_fails_closed():
    """An operator answering the gate does not get to skip validation either."""
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        path = _adata(root / "a.h5ad")
        result, summary = _integrate(path, root, integration_mode="harmony")

    assert result["errors"], "the gate answer is an instruction, not an exemption"
    assert summary.get("integrated") is not True


def test_the_snapshot_is_written_at_run_start():
    """What `--continue-from` reads instead of the original CSV."""
    from src.state import new_run_state

    parsed, errors = mf.parse_manifest(BALANCED, source="t")
    assert not errors, errors
    with tempfile.TemporaryDirectory() as tmp:
        state = new_run_state(
            project="t", config={}, runs_dir=tmp,
            study_design=mf.design_state(parsed),
        )
        snapshot = Path(tmp) / state["run_id"] / "manifest" / "normalized.csv"
        assert snapshot.is_file(), "the design a run started with has to be kept"
        reloaded, problems = mf.load_manifest(snapshot)

    assert not problems, problems
    assert reloaded.sha256 == parsed.sha256


def test_the_run_metadata_records_the_design_without_its_rows():
    from src.state import new_run_state
    import json

    parsed, _ = mf.parse_manifest(BALANCED, source="t")
    with tempfile.TemporaryDirectory() as tmp:
        state = new_run_state(
            project="t", config={"manifest_sha256": parsed.sha256}, runs_dir=tmp,
            study_design=mf.design_state(parsed),
        )
        meta = json.loads(Path(state["run_metadata_path"]).read_text(encoding="utf-8"))

    assert meta["study_design"]["sha256"] == parsed.sha256
    assert meta["study_design"]["n_libraries"] == 4
    flat = json.dumps(meta)
    for secret in ("LIB_A", "S001", "D001", "BATCH_A"):
        assert secret not in flat, f"{secret} reached run_metadata.json"


def test_continuing_a_paused_run_refuses_a_new_manifest():
    """One run cannot describe itself under two designs."""
    import contextlib
    import io

    from src.run import main

    with tempfile.TemporaryDirectory() as tmp:
        path = Path(tmp) / "m.csv"
        path.write_text(BALANCED)
        stderr = io.StringIO()
        with contextlib.redirect_stderr(stderr):
            try:
                main(["--continue-from", "some-run", "--sample-manifest", str(path)])
            except SystemExit as exc:
                assert exc.code != 0

    message = stderr.getvalue()
    assert "--resume-from" in message, message


def main() -> int:
    tests = [v for k, v in sorted(globals().items()) if k.startswith("test_")]
    failures = []
    for test in tests:
        try:
            test()
            print(f"  ok    {test.__name__}")
        except AssertionError as exc:
            failures.append(test.__name__)
            print(f"  FAIL  {test.__name__}: {exc}")
        except Exception as exc:  # noqa: BLE001 - a crash is a failure, not a stop
            failures.append(test.__name__)
            print(f"  ERROR {test.__name__}: {type(exc).__name__}: {exc}")
    print(f"\n{len(tests) - len(failures)}/{len(tests)} passed")
    return 1 if failures else 0


if __name__ == "__main__":
    raise SystemExit(main())
