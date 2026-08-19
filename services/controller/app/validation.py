"""What a preview checks, and the three different things it can say back.

The three are kept apart because they mean different things to whoever is
reading them, and collapsing them into one list of "problems" was the first
thing that made the intake conversation unusable:

`validation_errors`   this request is wrong. Nothing can start.
`missing_questions`   this request is incomplete. Ask the person, do not guess.
`warnings`            this request will run, and here is what it will do that
                      you may not have meant.

The distinction that matters most is the second. An intake assistant that fills
in a species, a manifest or a CellTypist model because the conversation did not
mention one produces a request that looks complete and describes an analysis
nobody asked for. So an absent required value is a *question*, carried in the
response, and the confirm endpoint refuses while any required one is open.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from .catalog import Catalog, RefError
from .domain import (
    ALWAYS_ON_SWITCHES,
    ANALYSIS_TO_CONFIG,
    EMBEDDING_METHODS,
    INTEGRATION_MODES,
    UNSUPPORTED_ANALYSES,
)

#: Species the executor can resolve constants and a reference for. Read from the
#: scientific package rather than restated, so this list cannot fall behind it.
try:  # pragma: no cover - exercised by whichever import path is live
    from src.species import known as _known_species  # type: ignore[import-not-found]

    def known_species() -> list[str]:
        return _known_species()

except Exception:  # noqa: BLE001 - the controller must start without the science env
    def known_species() -> list[str]:
        # The controller can run in a venv that does not have the scientific
        # package installed. Refusing every species then would be wrong, and
        # inventing a list would be worse: say so and let the worker's own
        # `resolve_reference` be the check, which it is in every case anyway.
        return []


def _question(field: str, question: str, *, required: bool = True, options: list[Any] | None = None) -> dict[str, Any]:
    return {"field": field, "question": question, "required": required, "options": options or []}


def validate_species(species: Any) -> tuple[str | None, list[str], list[dict[str, Any]]]:
    text = str(species or "").strip()
    if not text:
        return None, [], [_question(
            "species",
            "Which species is this sample from? It selects the reference genome and the "
            "mitochondrial and haemoglobin gene sets used for QC.",
            options=known_species(),
        )]
    catalogue = known_species()
    if catalogue and text.strip().lower() not in {s.lower() for s in catalogue}:
        # Aliases (`小鼠`, `mouse`) resolve inside the scientific package, so a
        # miss here is checked against the canonical names before it is called
        # an error.
        try:
            from src.species import canonical  # type: ignore[import-not-found]

            if canonical(text):
                return text, [], []
        except Exception:  # noqa: BLE001
            pass
        return None, [
            f"{text!r} is not a species this pipeline has QC constants for "
            f"(it knows: {', '.join(catalogue)})"
        ], []
    return text, [], []


def validate_analysis(analysis: Any) -> tuple[dict[str, Any], list[str], list[str], list[str]]:
    """Split the `analysis` block into what runs, what is wrong and what is not built.

    Returns `(clean, errors, warnings, unsupported)`. `clean` holds only keys
    this pipeline can act on; everything else is reported rather than dropped,
    because a setting that vanishes without a word is a person believing they
    configured something.
    """
    if analysis is None:
        analysis = {}
    if not isinstance(analysis, dict):
        return {}, ["analysis must be an object"], [], []

    clean: dict[str, Any] = {}
    errors: list[str] = []
    warnings: list[str] = []
    unsupported: list[str] = []

    for key, value in analysis.items():
        if key in ALWAYS_ON_SWITCHES:
            step = ALWAYS_ON_SWITCHES[key]
            if value is False:
                unsupported.append(
                    f"{key}=false is not supported: {step} is on the workflow's mainline and "
                    f"every route that produces a report runs it. There is no way to skip it."
                )
            continue
        if key in UNSUPPORTED_ANALYSES:
            unsupported.append(
                f"{UNSUPPORTED_ANALYSES[key]} is not implemented in this workflow. "
                f"No step produces it, so it cannot be requested."
            )
            continue
        if key not in ANALYSIS_TO_CONFIG:
            errors.append(f"{key!r} is not a setting this pipeline accepts")
            continue
        clean[key] = value

    method = clean.get("embedding_method")
    if method is not None and method not in EMBEDDING_METHODS:
        errors.append(f"embedding_method must be one of {', '.join(EMBEDDING_METHODS)}, got {method!r}")
        clean.pop("embedding_method", None)

    dims = clean.get("embedding_dimensions")
    if dims is not None:
        if not isinstance(dims, list) or not dims or any(d not in (2, 3) for d in dims):
            errors.append("embedding_dimensions must be a non-empty list drawn from [2, 3]")
            clean.pop("embedding_dimensions", None)

    mode = clean.get("integration_mode")
    if mode is not None and mode not in INTEGRATION_MODES:
        errors.append(f"integration_mode must be one of {', '.join(INTEGRATION_MODES)}, got {mode!r}")
        clean.pop("integration_mode", None)

    for numeric in ("resolution", "min_genes", "min_counts", "max_pct_mito"):
        value = clean.get(numeric)
        if value is not None and not isinstance(value, (int, float)):
            errors.append(f"{numeric} must be a number, got {type(value).__name__}")
            clean.pop(numeric, None)

    for integer in ("embedding_max_cells", "random_state"):
        value = clean.get(integer)
        if value is not None and (not isinstance(value, int) or isinstance(value, bool)):
            errors.append(f"{integer} must be an integer")
            clean.pop(integer, None)

    if clean.get("celltypist_model") is None:
        warnings.append(
            "No CellTypist model was chosen. The run will reach annotate_cells, list the "
            "candidate models as evidence and stop at a human gate for someone to pick one — "
            "a model trained on the wrong tissue returns confident wrong labels rather than failing."
        )
    if clean.get("scmayomap_tissue") is None:
        warnings.append(
            "No marker-database tissue was chosen. cross_check_annotation will list its "
            "candidates and stop at a human gate rather than scoring against a guessed tissue."
        )
    for threshold in ("min_genes", "min_counts", "max_pct_mito"):
        if clean.get(threshold) is None:
            warnings.append(
                "No cell QC thresholds were given. apply_cell_qc_filter will report what each "
                "candidate cut would cost and stop at a human gate; filtering is destructive and "
                "the thresholds are the operator's call."
            )
            break

    return clean, errors, warnings, unsupported


def validate_integration(
    analysis: dict[str, Any], study_design_ref: str | None
) -> tuple[list[str], list[dict[str, Any]]]:
    """Harmony needs a manifest, and a missing one is a question, not a default.

    `run_integration` corrects on the manifest's `technical_batch` and nothing
    else. Without a manifest there is no statement of which differences are
    technical, and assuming one library is one batch is exactly the assumption
    the executor refuses to make on its own.
    """
    if analysis.get("integration_mode") != "harmony":
        return [], []
    if study_design_ref:
        return [], []
    return [], [_question(
        "study_design_ref",
        "Batch correction needs a study manifest: one row per sequencing library, saying which "
        "sample, donor, condition and technical batch it came from. Which manifest describes "
        "these libraries? Without it the pipeline will not assume a library is a batch.",
    )]


def validate_research_question(text: Any) -> tuple[str | None, list[dict[str, Any]]]:
    value = str(text or "").strip()
    if not value:
        return None, [_question(
            "research_question",
            "What is this analysis for? One sentence — it is recorded with the run and it is "
            "what decides whether the settings below are the right ones.",
        )]
    return value, []


def comparison_needs_manifest(research_question: str | None, study_design_ref: str | None) -> list[dict[str, Any]]:
    """A question about differences *between* samples needs to know what a sample is.

    This is the one place a natural-language sentence influences the request, and
    what it produces is a question rather than a setting. The pipeline has no
    step that compares conditions, so the manifest is not being requested in
    order to run a comparison — it is requested because without it the report
    cannot even say which cells came from which sample, and the request would
    silently be for something narrower than what was asked for.
    """
    if study_design_ref or not research_question:
        return []
    text = research_question.lower()
    triggers = ("compare", "between", "across", "versus", " vs ", "condition", "treatment",
                "control", "group", "比較", "不同", "組別", "對照")
    if not any(word in text for word in triggers):
        return []
    return [_question(
        "study_design_ref",
        "This asks about differences between samples, so the run needs a study manifest to know "
        "which library is which sample and condition. Which manifest describes them? "
        "Note that this workflow has no differential-expression step: it will label cell types "
        "per sample, and comparing the composition is done from that report.",
    )]


def validate_input_ref(
    catalog: Catalog,
    *,
    input_ref: str | None,
    input_path: str | None,
    known_local: dict[str, str],
) -> tuple[str | None, Path | None, str | None, list[str], list[dict[str, Any]]]:
    """Resolve whichever of the two the caller supplied, preferring the reference.

    Returns `(input_ref, resolved_path, admitted_raw_path, errors, questions)`.
    `admitted_raw_path` is non-None only when a raw path was admitted and the
    store therefore has a new token to record.
    """
    if input_ref:
        try:
            path = catalog.resolve_input_ref(input_ref, known_local=known_local)
        except RefError as exc:
            return None, None, None, [str(exc)], []
        return input_ref, path, None, [], []

    if input_path:
        try:
            entry = catalog.admit_path(input_path)
        except RefError as exc:
            return None, None, None, [str(exc)], []
        # A path that matched a catalog entry comes back as that entry and needs
        # no token; only an ad-hoc one does.
        admitted = str(entry.path) if entry.input_ref.startswith("local:") else None
        return entry.input_ref, entry.path, admitted, [], []

    return None, None, None, [], [_question(
        "input_ref",
        "Which data should this analyse? Name one of the datasets this server offers, or a "
        "location inside the directories it is allowed to read.",
        options=[d["input_ref"] for d in catalog.list_datasets()],
    )]


def validate_manifest_ref(
    catalog: Catalog, study_design_ref: str | None
) -> tuple[str | None, Path | None, list[str]]:
    if not study_design_ref:
        return None, None, []
    try:
        path = catalog.resolve_manifest_ref(study_design_ref)
    except RefError as exc:
        return None, None, [str(exc)]
    return study_design_ref, path, []
