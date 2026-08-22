"""What `revise` means once a person can answer it with numbers.

Before this, `revise` re-ran a deterministic step against an unchanged config:
the same result, the same verdict, the same question. The behaviour worth
pinning is not that a value arrives — it is that the value arrives *and*
nothing downstream is allowed to keep describing the run it replaced.

Four things have to hold together, and each is a way of getting it wrong:

  - a gate offers only what answering it would actually redo
  - what is refused is said out loud, never dropped
  - the step re-runs and the new value is the one it used
  - the run's recorded config hash moves, so a later resume cannot reuse
    artifacts these values replaced

Run with `python tests/test_revision.py` (or `python tests/run_all.py`).
"""

from __future__ import annotations

import json
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from src import persistence  # noqa: E402
from src.policy import GatePolicy  # noqa: E402
from src.provenance import config_digest  # noqa: E402
from src.registry import (  # noqa: E402
    REGISTRY,
    REVISABLE_PARAMETERS,
    coerce_overrides,
    per_sample_int,
    revisable_from,
    steps_invalidated_by,
)
from src.run import per_sample_int as cli_per_sample_int, run_workflow  # noqa: E402
from src.state import summarize  # noqa: E402
from tests import fixtures  # noqa: E402


# --- the allowlist ----------------------------------------------------------------


def test_every_revisable_name_has_a_declared_type():
    """A name in `revisable` with no converter would fail at a gate, on a real run."""
    for name, spec in REGISTRY.items():
        for key in spec.revisable:
            assert key in REVISABLE_PARAMETERS, f"{name} offers {key} with no declared type"


def test_the_steps_that_stop_for_a_choice_are_the_ones_that_offer_one():
    """The steps that refuse to guess are the ones a person can answer.

    `run_integration` joined them when Harmony became opt-in: it now reports
    "several libraries, and nobody said which differences are technical" and
    stops rather than correcting on the library name, so the gate it reaches
    has to be able to take the answer.
    """
    offering = {name for name, spec in REGISTRY.items() if spec.revisable}
    assert offering == {
        "cell_calling_review",
        "apply_cell_qc_filter",
        "run_integration",
        "annotate_cells",
        "cross_check_annotation",
    }


def test_a_gate_offers_only_the_parameters_of_the_step_it_would_rerun():
    assert REGISTRY["apply_cell_qc_filter"].revisable == (
        "min_genes", "min_counts", "max_pct_mito",
    )
    assert REGISTRY["cell_calling_review"].revisable == ("force_cells", "min_umi")


def test_the_mainline_gate_offers_everything_its_revise_would_rerun():
    """It re-enters at `annotate_cells`, so both steps after that are in scope.

    Keyed off the revise target rather than the step last judged: the mainline
    gate has just judged `cross_check_annotation` but routes back further, and
    offering that one step's parameters would silently drop the model choice
    that is the more likely thing to be wrong at the end of a run.
    """
    assert revisable_from("annotate_cells") == ("celltypist_model", "scmayomap_tissue")
    assert revisable_from("build_report") == ()


# --- reading what a person typed --------------------------------------------------


def test_values_typed_at_a_terminal_arrive_as_numbers():
    accepted, rejected = coerce_overrides(
        {"min_genes": "200", "max_pct_mito": "15"}, ("min_genes", "max_pct_mito")
    )
    assert accepted == {"min_genes": 200.0, "max_pct_mito": 15.0}
    assert rejected == []


def test_a_parameter_this_gate_does_not_own_is_refused_with_a_reason():
    """A real parameter, simply not this gate's to set."""
    accepted, rejected = coerce_overrides({"celltypist_model": "Immune_All_Low.pkl"},
                                          ("min_genes",))
    assert accepted == {}
    assert len(rejected) == 1 and "not offered at this gate" in rejected[0]


def test_an_unreadable_value_is_refused_rather_than_guessed():
    accepted, rejected = coerce_overrides({"min_genes": "about two hundred"}, ("min_genes",))
    assert accepted == {}
    assert "not a valid float" in rejected[0]


def test_nothing_is_dropped_without_being_said():
    """Anything that does not arrive in `config` has to come back as a complaint.

    A value that neither takes effect nor produces a refusal is the worst of the
    three outcomes: a person believing they changed something they did not.
    """
    raw = {"min_genes": "200", "celltypist_model": "x", "max_pct_mito": "nonsense"}
    accepted, rejected = coerce_overrides(raw, ("min_genes", "max_pct_mito"))
    assert accepted == {"min_genes": 200.0}
    for key in raw:
        assert key in accepted or any(key in message for message in rejected), \
            f"{key} was neither applied nor refused"


# --- a cell count per library, at the gate ------------------------------------------
#
# `cell_calling_review` stops with "Set force_cells or min_umi — a single value
# for every sample, **or a mapping per sample** — and re-run", prints
# `revise can set: force_cells, min_umi`, and `_for(setting, name)` has always
# read either. Neither front end could express the mapping: `--force-cells` took
# `type=int` and `REVISABLE_PARAMETERS` said `int`, so on 2026-08-22, for two
# libraries whose knees sat at rank 1,039 and rank 1,198, the gate refused its
# own instruction. Fixing the command line alone left the terminal prompt — the
# path the gate actually points at — still refusing it.


def test_a_cell_count_per_library_is_a_valid_answer_at_the_gate():
    accepted, rejected = coerce_overrides(
        {"force_cells": '{"pbmc_1k_v2": 1039, "pbmc_1k_v3": 1198}'},
        REGISTRY["cell_calling_review"].revisable,
    )
    assert accepted == {"force_cells": {"pbmc_1k_v2": 1039, "pbmc_1k_v3": 1198}}
    assert rejected == []


def test_one_number_at_the_gate_still_means_every_library():
    accepted, rejected = coerce_overrides(
        {"min_umi": "500"}, REGISTRY["cell_calling_review"].revisable
    )
    assert accepted == {"min_umi": 500} and rejected == []


def test_the_terminal_and_the_command_line_read_the_answer_the_same_way():
    """The drift this is guarding against is the one that happened.

    `ask_for_overrides` validates nothing precisely so that `coerce_overrides`
    is the single place a value acquires meaning. That only holds while the
    command line goes through the same function — when `--force-cells` learned
    the mapping form on its own, the two front ends disagreed for a day about
    what the gate had asked for.
    """
    for parameter in ("force_cells", "min_umi"):
        assert REVISABLE_PARAMETERS[parameter] is per_sample_int, (
            f"{parameter} at a gate no longer reads what the command line reads"
        )
    for answer in ("1039", '{"a": 1039, "b": 1198}'):
        assert cli_per_sample_int(answer) == per_sample_int(answer)


def test_a_per_library_count_is_refused_in_terms_the_typist_can_act_on():
    """Refused, and told what would have worked.

    A refusal naming the Python converter — "not a valid per_sample_int" —
    would repeat the original mistake in a smaller way: the gate says what you
    may set, then declines it in words that are not about the answer.
    """
    allowed = REGISTRY["cell_calling_review"].revisable
    for bad in ('{"pbmc_1k_v2": 0}', '{"pbmc_1k_v2": -1}', '{"pbmc_1k_v2": 1.5}',
                '{"pbmc_1k_v2": "1039"}', "{}", "0", "-5", "plenty"):
        accepted, rejected = coerce_overrides({"force_cells": bad}, allowed)
        assert not accepted, f"{bad!r} reached config"
        assert len(rejected) == 1 and "force_cells" in rejected[0], rejected
        assert "per_sample_int" not in rejected[0], (
            f"the refusal names the converter instead of the answer: {rejected[0]}"
        )

    _, rejected = coerce_overrides({"force_cells": '{"pbmc_1k_v2": 1039,'}, allowed)
    assert "library_id" in rejected[0], "a broken mapping is shown the mapping form"


def test_answering_with_a_mapping_invalidates_from_cell_calling_review():
    """A dict is a changed value like any other, and cuts the resume plan.

    Worth pinning separately: `earliest_step_reading` compares config values,
    and a type it had never seen before this change is exactly where a
    comparison quietly starts returning "unchanged".
    """
    invalid = steps_invalidated_by("cell_calling_review")
    assert invalid[0] == "cell_calling_review"
    assert "cellranger_count" not in invalid, (
        "answering the cell-calling gate must not recount the libraries"
    )


# --- what a change invalidates ------------------------------------------------------


def test_invalidation_starts_at_the_revised_step_and_runs_to_the_end():
    invalid = steps_invalidated_by("apply_cell_qc_filter")
    assert invalid[0] == "apply_cell_qc_filter"
    assert invalid[-1] == "build_report"
    for later in ("detect_doublets", "run_pca", "run_clustering", "annotate_cells"):
        assert later in invalid, f"{later} runs after the filter and cannot be reused"
    for earlier in ("ingest_validate", "merge_samples", "run_qc_metrics"):
        assert earlier not in invalid, f"{earlier} runs before it and is still valid"


# --- end to end ---------------------------------------------------------------------


def _bundle(root: Path):
    bundle = fixtures.bundle_for({"input_type": "matrix", "matrix_kind": "filtered"}, root / "b")
    reference = fixtures.make_reference(root, "ref", genomes=["GRCh38"])
    # Deliberately no thresholds: apply_cell_qc_filter refuses to filter and
    # stops, which is the case revise exists for.
    return {"paths": [str(bundle)]}, {"species": "human", "transcriptome": str(reference)}


def _revise_once_at_the_filter(overrides):
    """Answer `revise` the first time the QC filter asks, then accept everything."""
    used = {"done": False}

    def decide(request):
        if request.get("step") == "apply_cell_qc_filter" and not used["done"]:
            used["done"] = True
            return {"decision": "revise", "rationale": "thresholds",
                    "operator": "tester", "overrides": overrides}
        return {"decision": "accept", "rationale": "", "operator": "tester"}

    return decide


def _run_with(root: Path, decide, **policy_kwargs):
    bundle, config = _bundle(root)
    return run_workflow(
        project="test", input_bundle=bundle, config=config,
        runs_dir=str(root / "runs"),
        policy=GatePolicy(interactive=True, **policy_kwargs),
        checkpointer=persistence.make_checkpointer("memory"),
        decide=decide,
    ), config


def test_revising_the_threshold_changes_what_the_filter_actually_did():
    """The whole point: the number a person typed is the number the step used."""
    with tempfile.TemporaryDirectory() as tmp:
        final, _ = _run_with(
            Path(tmp),
            _revise_once_at_the_filter({"min_genes": "1", "max_pct_mito": "100"}),
        )

    ran = [r["step"] for r in final["step_results"]]
    assert ran.count("apply_cell_qc_filter") == 2, "the step has to actually run again"

    output = final["artifacts"]["apply_cell_qc_filter"]
    assert output["filter_state"] == "applied", "it refused before, it must not now"
    assert output["thresholds"]["min_genes"] == 1.0
    assert output["thresholds"]["chosen_by"] == "operator"
    assert final["config"]["min_genes"] == 1.0, "the run's config carries it too"
    assert "build_report" in ran, "and the run continues on the new result"


def test_a_refused_override_is_recorded_rather_than_silently_dropped():
    with tempfile.TemporaryDirectory() as tmp:
        final, _ = _run_with(
            Path(tmp),
            _revise_once_at_the_filter(
                {"min_genes": "1", "max_pct_mito": "100", "celltypist_model": "wrong_gate"}
            ),
        )
    revised = next(d for d in final["human_decisions"] if d["decision"] == "revise")
    assert revised["overrides"] == {"min_genes": 1.0, "max_pct_mito": 100.0}
    assert any("celltypist_model" in message for message in revised["rejected_overrides"])
    assert final["config"].get("celltypist_model") is None, "a refusal must not reach config"


def test_the_decision_records_what_it_would_rerun_not_only_what_it_judged():
    with tempfile.TemporaryDirectory() as tmp:
        final, _ = _run_with(
            Path(tmp),
            _revise_once_at_the_filter({"min_genes": "1", "max_pct_mito": "100"}),
        )
    revised = next(d for d in final["human_decisions"] if d["decision"] == "revise")
    assert revised["revise_target"] == "apply_cell_qc_filter"


def test_the_runs_recorded_config_hash_moves_with_the_revision():
    """Otherwise `--resume-from` with the original flags reuses replaced artifacts.

    `resumable_steps` trusts a run directory when the hash recorded in
    `run_metadata.json` matches the config being resumed with. Leaving it at the
    hash of the config the run *started* with would make the original command
    line match a directory whose artifacts were computed from revised values.
    """
    with tempfile.TemporaryDirectory() as tmp:
        final, original_config = _run_with(
            Path(tmp),
            _revise_once_at_the_filter({"min_genes": "1", "max_pct_mito": "100"}),
        )
        metadata = json.loads(Path(final["run_metadata_path"]).read_text(encoding="utf-8"))

    assert metadata["revisions"], "the change has to be readable in the provenance"
    entry = metadata["revisions"][-1]
    assert entry["step"] == "apply_cell_qc_filter"
    assert entry["overrides"]["min_genes"] == 1.0
    assert entry["at"]

    recorded = metadata["source"]["config_sha256"]
    assert recorded != config_digest(original_config), \
        "the original command line must no longer match this directory"
    assert recorded == config_digest({**original_config, "min_genes": 1.0,
                                      "max_pct_mito": 100.0})


def test_a_revise_marks_everything_downstream_for_recomputation():
    """The flags are what a resumed run consults before skipping a step."""
    with tempfile.TemporaryDirectory() as tmp:
        final, _ = _run_with(
            Path(tmp),
            _revise_once_at_the_filter({"min_genes": "1", "max_pct_mito": "100"}),
        )
    flags = final["resumed_steps"]
    for later in ("apply_cell_qc_filter", "detect_doublets", "run_pca", "annotate_cells"):
        assert flags.get(later) is False, f"{later} must not be reusable after the change"


def test_an_endless_revise_stops_instead_of_running_forever():
    """`recursion_limit` cannot bound this — it resets on every `Command(resume=...)`.

    A person at a terminal stops on their own. A decider that returns `revise`
    on every warn does not, and it is the same code path, re-running real
    analysis each time round.
    """
    answers = {"n": 0}

    def always_revise(request):
        # Answering with no value is the pathological case on purpose: the step
        # still has no thresholds, so it stops again and asks the same question.
        # Supplying a working threshold would end the loop by fixing it, which
        # is the behaviour the other tests cover, not this one.
        if request.get("step") == "apply_cell_qc_filter":
            answers["n"] += 1
            return {"decision": "revise", "rationale": "again", "operator": "tester"}
        return {"decision": "accept", "rationale": "", "operator": "tester"}

    with tempfile.TemporaryDirectory() as tmp:
        final, _ = _run_with(Path(tmp), always_revise, max_revisions_per_step=2)

    assert summarize(final)["status"] == "halted"
    assert answers["n"] == 3, "two revisions, and the third is refused"
    last = final["human_decisions"][-1]
    assert last["decision"] == "stop"
    assert any("max_revisions_per_step" in message
               for message in last["rejected_overrides"])


def test_revise_without_any_value_still_only_reruns_the_step():
    """The old behaviour is still reachable, for a step worth simply retrying."""
    with tempfile.TemporaryDirectory() as tmp:
        final, original_config = _run_with(Path(tmp), _revise_once_at_the_filter({}))
        metadata = json.loads(Path(final["run_metadata_path"]).read_text(encoding="utf-8"))

    revised = next(d for d in final["human_decisions"] if d["decision"] == "revise")
    assert revised["overrides"] == {}
    assert "revisions" not in metadata, "nothing changed, so nothing is recorded as changed"
    assert metadata["source"]["config_sha256"] == config_digest(original_config)


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
