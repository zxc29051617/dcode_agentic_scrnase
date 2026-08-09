"""Assemble the run into a report. Reads recorded evidence; computes nothing.

The contract is `docs/report_contract.md`. Three things it fixes, all of which
this file exists to honour:

**One model, two renderings.** `collect()` builds a `ReportModel`; `report.md`
and `report.html` are both rendered from it. Assembling the same numbers twice
is how two documents drift apart while both keep working.

**No analysis.** No embedding, clustering, normalization or statistical test.
Anything with a seed or a parameter behind it belongs to the step that owns it,
so a figure always describes the run it claims to. Where a figure needed
something no step recorded, the fix was to record it upstream — that work is
already done, and this file is the reason it was done.

**Nothing vanishes silently.** Every section states the condition it needs. An
unmet condition renders with its reason, because an absent figure with a stated
reason is evidence and an absent figure without one is indistinguishable from
an oversight. A run that stopped early still gets a report describing how far
it got.

Judge verdicts and human decisions are read from the audit log rather than from
state: the skill payload does not carry them, and the audit log is the record
an auditor would read anyway.

Run standalone:
    python skills/build_report/build_report.py --run-dir runs/<run_id>
"""

from __future__ import annotations

import argparse
import base64
import html
import json
import sys
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

_PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent
if str(_PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(_PROJECT_ROOT))

from src import plots  # noqa: E402

TOOL_NAME = "build_report"
INPUT_FIELDS = (
    "artifacts",
    "run_dir",
    "config.inline_figures",
)
OUTPUT_FIELDS = (
    "markdown_path",
    "html_path",
    "model_path",
    "figure_paths",
    "sections",
    "warnings",
    "errors",
    "recommended_next_tool",
)

TIERS = (
    ("main", "Main results"),
    ("appendix", "Technical appendix"),
    ("audit", "Pipeline audit"),
)


@dataclass
class Table:
    caption: str
    columns: list[str]
    rows: list[list[Any]]


@dataclass
class Section:
    """One figure group from the contract, present or accounted for."""

    key: str
    title: str
    tier: str
    available: bool
    reason: str = ""
    body: list[str] = field(default_factory=list)
    figures: list[str] = field(default_factory=list)
    tables: list[Table] = field(default_factory=list)


@dataclass
class ReportModel:
    project: str
    run_id: str
    generated_at: str
    headline: list[tuple[str, str]]
    sections: list[Section]


def _fmt(value: Any) -> str:
    if isinstance(value, bool):
        return "yes" if value else "no"
    if isinstance(value, int):
        return f"{value:,}"
    if isinstance(value, float):
        return f"{value:,.4g}"
    if value is None or value == "":
        return "—"
    return str(value)


def _read_json(path: Path) -> dict[str, Any]:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}


def _read_audit(path: Path) -> list[dict[str, Any]]:
    if not path.exists():
        return []
    records = []
    try:
        with path.open(encoding="utf-8") as handle:
            for line in handle:
                if line.strip():
                    records.append(json.loads(line))
    except (OSError, ValueError):
        return records
    return records


def _load(path: str | None) -> Any:
    """Read an AnnData a step wrote, or None if it is not there."""
    if not path or not Path(path).exists():
        return None
    try:
        import anndata

        return anndata.read_h5ad(path)
    except Exception:  # noqa: BLE001 - a missing object is a stated absence
        return None


# --- the sections ---------------------------------------------------------------


def _section_funnel(art: dict[str, Any], figures: Path) -> Section:
    stages: list[tuple[str, int]] = []

    def add(label: str, value: Any) -> None:
        if isinstance(value, int) and value > 0:
            stages.append((label, value))

    review = art.get("cell_calling_review") or {}
    add("barcodes called", review.get("n_cells"))
    add("loaded", ((art.get("post_load_validate") or {}).get("metrics") or {}).get("n_cells"))
    qc_filter = (art.get("apply_cell_qc_filter") or {}).get("filter_summary") or {}
    add("before QC filter", qc_filter.get("n_before"))
    add("after QC filter", qc_filter.get("n_after"))
    doublets = (art.get("detect_doublets") or {}).get("doublet_summary") or {}
    add("after doublet step", doublets.get("n_cells_out"))

    # Duplicate consecutive counts say nothing; a stage that removed nothing is
    # already visible as a flat segment in the numbers below the figure.
    deduped: list[tuple[str, int]] = []
    for label, value in stages:
        if not deduped or deduped[-1][1] != value:
            deduped.append((label, value))

    if len(deduped) < 2:
        return Section("M1", "Cells retained", "main", False,
                       "no step reported a cell count to compare")
    path = plots.retention_funnel(deduped, figures / "m1_funnel.png")
    return Section(
        "M1", "Cells retained", "main", True,
        body=[f"{deduped[0][1]:,} cells entered the pipeline and {deduped[-1][1]:,} reached "
              f"the analysis ({deduped[-1][1] / deduped[0][1]:.0%})."],
        figures=[path] if path else [],
        tables=[Table("Cells at each stage", ["stage", "cells"],
                      [[label, value] for label, value in deduped])],
    )


def _section_qc(art: dict[str, Any], figures: Path) -> Section:
    before = _load((art.get("run_qc_metrics") or {}).get("adata_path"))
    after = _load((art.get("apply_cell_qc_filter") or {}).get("adata_path"))
    if before is None:
        return Section("M2", "Quality control", "main", False, "run_qc_metrics did not run")
    if after is None:
        after = before
    path = plots.qc_before_after(before, after, figures / "m2_qc.png")
    metrics = (art.get("run_qc_metrics") or {}).get("metrics") or {}
    rows = [[key, _fmt(value)] for key, value in sorted(metrics.items())]
    return Section(
        "M2", "Quality control", "main", True,
        figures=[path] if path else [],
        tables=[Table("Measured QC metrics", ["metric", "value"], rows)] if rows else [],
    )


def _section_embedding(final: Any, art: dict[str, Any], figures: Path) -> Section:
    if final is None:
        return Section("M3", "Embedding", "main", False, "no annotated object was produced")
    bases = [key for key in ("X_umap", "X_tsne") if key in final.obsm]
    if not bases:
        return Section("M3", "Embedding", "main", False, "run_umap did not run")
    colours = [c for c in ("cell_type", "leiden", "sample", "conf_score") if c in final.obs]
    paths = []
    for basis in bases:
        path = plots.embedding_panels(final, basis, colours,
                                      figures / f"m3_{basis.removeprefix('X_')}.png",
                                      title=basis.removeprefix("X_").upper())
        if path:
            paths.append(path)
    summary = (art.get("run_umap") or {}).get("embedding_summary") or {}
    return Section(
        "M3", "Embedding", "main", True,
        body=[f"Computed from `{summary.get('embedding_key', 'X_pca')}`, "
              f"seed {_fmt(summary.get('random_state'))}."],
        figures=paths,
    )


def _section_markers(final: Any, art: dict[str, Any], figures: Path) -> Section:
    summary = (art.get("find_markers") or {}).get("marker_summary") or {}
    if final is None or "rank_genes_groups" not in getattr(final, "uns", {}):
        return Section("M4", "Cluster markers", "main", False, "find_markers did not run")
    path = plots.marker_dotplot(final, figures / "m4_markers.png",
                                groupby=summary.get("cluster_key", "leiden"))
    top = (art.get("find_markers") or {}).get("top_markers") or {}
    rows = [
        [cluster, ", ".join(m["gene"] for m in genes[:5])]
        for cluster, genes in sorted(top.items(), key=lambda kv: (len(kv[0]), kv[0]))
    ]
    table_path = (art.get("find_markers") or {}).get("marker_table_path")
    body = [f"Ranked with {summary.get('method', 'wilcoxon')} across "
            f"{_fmt(summary.get('n_genes_tested'))} genes."]
    if table_path:
        body.append(f"Full ranking: `{table_path}`")
    return Section(
        "M4", "Cluster markers", "main", True, body=body,
        figures=[path] if path else [],
        tables=[Table("Top markers per cluster", ["cluster", "genes"], rows)] if rows else [],
    )


def _section_composition(final: Any, figures: Path) -> Section:
    if final is None or "cell_type" not in getattr(final, "obs", {}):
        return Section("M5", "Composition", "main", False, "cells were not annotated")
    if "sample" not in final.obs or final.obs["sample"].nunique() < 2:
        return Section("M5", "Composition", "main", False,
                       "a single library — there is nothing to compare composition against")
    path = plots.composition_by_sample(final, figures / "m5_composition.png")
    import pandas as pd

    table = pd.crosstab(final.obs["sample"].astype(str), final.obs["cell_type"].astype(str))
    rows = [[str(idx), *[int(v) for v in row]] for idx, row in table.iterrows()]
    return Section(
        "M5", "Composition", "main", True,
        figures=[path] if path else [],
        tables=[Table("Cells per type per library", ["sample", *table.columns.astype(str)], rows)],
    )


def _section_confidence(art: dict[str, Any], figures: Path) -> Section:
    annotate = art.get("annotate_cells") or {}
    per_cluster = annotate.get("per_cluster") or {}
    if annotate.get("annotation_state") != "annotated" or not per_cluster:
        return Section("M6", "Annotation confidence", "main", False,
                       annotate.get("warnings", ["cells were not annotated"])[0]
                       if annotate.get("warnings") else "cells were not annotated")
    path = plots.annotation_confidence(per_cluster, figures / "m6_confidence.png")
    rows = [
        [name, e.get("cell_type"), _fmt(e.get("n_cells")),
         _fmt(e.get("median_conf_score")), _fmt(e.get("per_cell_consensus")), e.get("runner_up")]
        for name, e in sorted(per_cluster.items(), key=lambda kv: (len(kv[0]), kv[0]))
    ]
    return Section(
        "M6", "Annotation confidence", "main", True,
        body=["Confidence is the model's own certainty; consensus is how many of the "
              "cluster's cells individually agreed. They come apart: high confidence with "
              "low consensus usually means the cluster merges two populations."],
        figures=[path] if path else [],
        tables=[Table("Per cluster",
                      ["cluster", "cell type", "cells", "median confidence",
                       "per-cell consensus", "runner-up"], rows)],
    )


def _clusters_the_judge_named(verdict: dict[str, Any], clusters: list[str]) -> list[str]:
    """Which clusters the judge's reasons refer to, by matching real cluster ids.

    The judge writes prose, so this is a read of its text rather than a field it
    filled in. Longer ids are matched first so `cluster 1` cannot claim a
    mention of `cluster 12`, and the full reasons are printed underneath either
    way — if this misses one, the evidence is still on the page.
    """
    text = " ".join(str(r) for r in (verdict.get("reasons") or []))
    named, remaining = [], text
    for cluster in sorted(clusters, key=len, reverse=True):
        for form in (f"cluster {cluster}", f"Cluster {cluster}"):
            if form in remaining:
                named.append(cluster)
                remaining = remaining.replace(form, "")
                break
    return sorted(named, key=lambda c: (len(c), c))


def _section_cross_check(art: dict[str, Any], audit: list[dict[str, Any]],
                         figures: Path) -> Section:
    """M7 — the two annotators side by side, and what the judge made of them."""
    cross = art.get("cross_check_annotation") or {}
    state = cross.get("cross_check_state")
    per_cluster = cross.get("per_cluster") or {}

    if state == "not_compared":
        tissues = (cross.get("evidence") or {}).get("available_tissues") or []
        return Section("M7", "Annotation cross-check", "main", False,
                       "no `scmayomap_tissue` was chosen, so the clusters were "
                       f"scored against nothing. {len(tissues)} tissues were offered; "
                       "the run stopped at the gate rather than guess.")
    if not per_cluster:
        return Section("M7", "Annotation cross-check", "main", False,
                       (cross.get("errors") or ["the cross-check did not run"])[0])

    clusters = sorted(per_cluster, key=lambda c: (len(c), c))
    verdict = next((r for r in reversed(audit)
                    if r.get("event") == "judge"
                    and r.get("step") == "cross_check_annotation"), {})
    named = _clusters_the_judge_named(verdict, clusters)

    rows = []
    for name in clusters:
        entry = per_cluster[name]
        candidates = entry.get("database_candidates") or []
        top = candidates[0] if candidates else {}
        rows.append([
            name,
            _fmt(entry.get("n_cells")),
            entry.get("celltypist_label") or "—",
            _fmt(entry.get("celltypist_confidence")),
            top.get("cell_type") or "—",
            _fmt(top.get("score")),
            _fmt(entry.get("n_matched_genes")),
            ", ".join(entry.get("flags") or []) or "—",
            "yes" if name in named else "",
        ])

    figure = plots.annotation_cross_check(
        cross.get("score_table_path") or "", per_cluster, figures / "m7_cross_check.png")

    summary = cross.get("cross_check_summary") or {}
    counts = summary.get("flag_counts") or {}
    body = [
        "Two annotators over the same clusters, by unrelated routes: CellTypist is "
        "a classifier trained on labelled reference cells, and the marker database "
        "was matched against this run's own differential expression. Neither saw "
        "the other's input, so where they agree something has been established, "
        "and where they differ is where to look.",

        (f"**How many agree.** The judge named {len(named)} of {len(clusters)} "
         f"clusters as carrying two different cell types — {', '.join(named)} — "
         f"and left the other {len(clusters) - len(named)} alone."
         if named else
         "**How many agree.** The judge's reasons name no cluster, so no count of "
         "agreeing clusters can be given here. That is what a verdict looks like "
         "when the judging was done by the rule-based stub, or by a model that "
         "reported on the flags without comparing the labels — read the table "
         "below yourself in that case.")
        + " The two vocabularies do not line up (`CD16+ NK cells` and `CD56-dim "
        "natural killer cell` are one population under two names), so agreement is "
        "not a string comparison and was not computed as one. The step reports both "
        "labels; the judge reconciles them, and its reasons are quoted below so the "
        "call can be checked against the table.",

        f"**What the numbers flagged, separately.** {summary.get('n_flagged', 0)} "
        f"clusters carry a numeric flag: {counts.get('low_marker_evidence', 0)} on "
        f"thin evidence, {counts.get('ambiguous', 0)} where the database did not "
        f"resolve to one type, {counts.get('confidence_conflict', 0)} where "
        "CellTypist was sure and the database was not. These test counts and "
        "margins — they never read a cell type's name, so a cluster can carry no "
        "flag and still be the clearest disagreement in the run.",
    ]

    if verdict:
        body.append(
            f"**Why the judge called this `{verdict.get('verdict')}` "
            f"(score {_fmt(verdict.get('score'))}).**")
        body.extend(f"- {r}" for r in (verdict.get("reasons") or []))

    body.append(
        "**Why a person still has to decide.** The pipeline can say the two methods "
        "disagree; it cannot say which is right, and the answer depends on the "
        "sample rather than on the data. On a PBMC preparation a `Neutrophil` call "
        "is a property of the reference — a Ficoll gradient leaves granulocytes in "
        "the pellet — and the database has no way to know how the cells were "
        "prepared. That is knowledge the operator has and neither annotator does, "
        "which is why this stops at a gate instead of resolving itself."
    )

    return Section(
        "M7", "Annotation cross-check", "main", True,
        body=body,
        figures=[figure] if figure else [],
        tables=[Table(
            f"CellTypist against the marker database ({cross.get('tissue')})",
            ["cluster", "cells", "CellTypist", "confidence",
             "database top-1", "score", "matched genes", "flags", "judge named"],
            rows)],
    )


def _section_barcode_rank(art: dict[str, Any], figures: Path) -> Section:
    review = art.get("cell_calling_review") or {}
    per_sample = review.get("per_sample") or {}
    if not per_sample:
        return Section("A1", "Barcode rank", "appendix", False,
                       "the input was a filtered matrix, so there is no raw barcode "
                       "distribution to plot")
    paths, rows = [], []
    for name, entry in sorted(per_sample.items()):
        evidence = entry.get("evidence") or {}
        curve = evidence.get("rank_curve_path")
        if not curve:
            continue
        path = plots.barcode_rank(curve, evidence, figures / f"a1_rank_{name}.png",
                                  selected_cells=entry.get("n_cells"))
        if path:
            paths.append(path)
        rows.append([name, _fmt(evidence.get("knee_rank")), _fmt(evidence.get("inflection_rank")),
                     _fmt(evidence.get("cliff_drop_ratio")), _fmt(entry.get("n_cells"))])
    if not paths:
        return Section("A1", "Barcode rank", "appendix", False,
                       "no rank curve was recorded for any library")
    return Section(
        "A1", "Barcode rank", "appendix", True, figures=paths,
        tables=[Table("Cutoffs considered",
                      ["sample", "knee rank", "inflection rank", "drop ratio", "cells kept"], rows)],
    )


def _section_qc_per_sample(art: dict[str, Any], figures: Path) -> Section:
    before = _load((art.get("run_qc_metrics") or {}).get("adata_path"))
    if before is None or "sample" not in getattr(before, "obs", {}):
        return Section("A2", "QC per library", "appendix", False,
                       "no per-library QC metrics were recorded")
    path = plots.qc_per_sample(before, figures / "a2_qc_per_sample.png")
    return Section("A2", "QC per library", "appendix", bool(path),
                   "" if path else "the per-library figure could not be drawn",
                   figures=[path] if path else [])


def _section_filter_reasons(art: dict[str, Any], figures: Path) -> Section:
    summary = (art.get("apply_cell_qc_filter") or {}).get("filter_summary") or {}
    flags_path = summary.get("cell_flags_path")
    if not flags_path or not Path(flags_path).exists():
        return Section("A3", "Why cells were removed", "appendix", False,
                       "no thresholds were applied, so nothing was removed")
    import pandas as pd

    frame = pd.read_csv(flags_path)
    path = plots.qc_filter_reasons(frame, figures / "a3_filter_reasons.png")
    attribution = summary.get("removed_by_criterion") or {}
    overlap = summary.get("n_removed_by_more_than_one")
    rows = [[name, _fmt(count)] for name, count in sorted(attribution.items())]
    body = [f"{_fmt(summary.get('n_removed'))} cells removed of "
            f"{_fmt(summary.get('n_before'))}."]
    if overlap:
        body.append(
            f"{_fmt(overlap)} of them failed more than one criterion, so the per-criterion "
            "counts below overlap and must not be added together."
        )
    return Section(
        "A3", "Why cells were removed", "appendix", True, body=body,
        figures=[path] if path else [],
        tables=[Table("Cells failing each criterion (overlapping)", ["criterion", "cells"], rows)],
    )


def _section_doublets(final: Any, art: dict[str, Any], figures: Path) -> Section:
    detect = art.get("detect_doublets") or {}
    summary = detect.get("doublet_summary") or {}
    if final is None or "doublet_score" not in getattr(final, "obs", {}):
        return Section("A4", "Doublets", "appendix", False, "detect_doublets did not run")
    path = plots.doublet_scores(final, detect.get("per_sample") or {}, figures / "a4_doublets.png")
    rows = [
        [name, _fmt(e.get("n_cells")), _fmt(e.get("n_doublets")), _fmt(e.get("pct_doublets")),
         _fmt(e.get("expected_rate")), e.get("expected_rate_source"), _fmt(e.get("threshold_used"))]
        for name, e in sorted((detect.get("per_sample") or {}).items())
    ]
    body = [f"{_fmt(summary.get('n_doublets'))} cells called doublets "
            f"({_fmt(summary.get('pct_doublets'))}%); "
            f"{'removed' if summary.get('removed') else 'kept and flagged'}."]
    return Section(
        "A4", "Doublets", "appendix", True, body=body,
        figures=[path] if path else [],
        tables=[Table("Per library",
                      ["sample", "cells", "doublets", "%", "expected rate", "rate source",
                       "threshold"], rows)],
    )


def _section_pca(final: Any, art: dict[str, Any], figures: Path) -> Section:
    if final is None:
        return Section("A5", "PCA and feature selection", "appendix", False,
                       "no object with a PCA was produced")
    path = plots.pca_and_hvg(final, figures / "a5_pca_hvg.png")
    if not path:
        return Section("A5", "PCA and feature selection", "appendix", False,
                       "run_pca and normalize_hvg_prepare left nothing to plot")
    pca = (art.get("run_pca") or {}).get("pca_summary") or {}
    hvg = (art.get("normalize_hvg_prepare") or {}).get("hvg_summary") or {}
    rows = [
        ["components kept", _fmt(pca.get("n_comps"))],
        ["variance explained", _fmt(pca.get("cumulative_variance_explained"))],
        ["genes used for the fit", _fmt(pca.get("n_genes_used"))],
        ["HVGs selected", _fmt(hvg.get("n_hvg"))],
        ["HVG flavor", f"{hvg.get('hvg_flavor')} (requested {hvg.get('hvg_flavor_requested')})"],
    ]
    return Section("A5", "PCA and feature selection", "appendix", True,
                   figures=[path], tables=[Table("Settings", ["setting", "value"], rows)])


def _section_integration(final: Any, art: dict[str, Any], figures: Path) -> Section:
    summary = (art.get("run_integration") or {}).get("integration_summary") or {}
    if not summary.get("integrated"):
        return Section("A6", "Integration diagnostic", "appendix", False,
                       "integration was not run, so there is no before-and-after to compare")
    if final is None or "X_umap_unintegrated" not in getattr(final, "obsm", {}):
        return Section("A6", "Integration diagnostic", "appendix", False,
                       "no pre-integration embedding was recorded")
    path = plots.integration_diagnostic(final, figures / "a6_integration.png",
                                        sample_key=summary.get("batch_key") or "sample")
    return Section(
        "A6", "Integration diagnostic", "appendix", bool(path),
        "" if path else "the diagnostic figure could not be drawn",
        body=["This is a diagnostic, not a proof. It shows whether libraries mix; it cannot "
              "separate a correction that worked from one that erased real differences "
              "between samples. That needs batch-mixing and biological-conservation metrics "
              "this pipeline does not compute."],
        figures=[path] if path else [],
    )


def _section_decisions(art: dict[str, Any]) -> Section:
    """Every value that could have been chosen differently, and who chose it."""
    rows: list[list[Any]] = []

    def add(parameter: str, value: Any, source: str) -> None:
        rows.append([parameter, _fmt(value), source])

    review = art.get("cell_calling_review") or {}
    if review.get("n_cells"):
        add("cells kept", review["n_cells"],
            (review.get("selection") or {}).get("chosen_by", "operator"))

    qc = art.get("apply_cell_qc_filter") or {}
    # `thresholds` carries a `chosen_by` stamp alongside the cuts themselves;
    # it is provenance about the row rather than a row of its own.
    chosen_by = (qc.get("thresholds") or {}).get("chosen_by", "operator (config)")
    for name, value in ((qc.get("thresholds") or {})).items():
        if value is not None and name != "chosen_by":
            add(name, value, chosen_by)
    if qc.get("filter_state") == "needs_review":
        add("QC thresholds", None, "not chosen — nothing was filtered")

    for sample, entry in ((art.get("detect_doublets") or {}).get("per_sample") or {}).items():
        if entry.get("assessed"):
            add(f"expected doublet rate ({sample})", entry.get("expected_rate"),
                entry.get("expected_rate_source", "—"))

    hvg = (art.get("normalize_hvg_prepare") or {}).get("hvg_summary") or {}
    prep = (art.get("normalize_hvg_prepare") or {}).get("prep_summary") or {}
    if hvg:
        add("HVGs selected", hvg.get("n_hvg"), f"n_top_genes={hvg.get('n_top_genes_requested')}")
        if hvg.get("hvg_flavor") != hvg.get("hvg_flavor_requested"):
            add("HVG flavor", hvg.get("hvg_flavor"),
                f"fell back from {hvg.get('hvg_flavor_requested')}")
    if prep:
        add("normalization target", prep.get("normalize_target_sum"),
            prep.get("normalize_target_sum_source", "—"))

    pca = (art.get("run_pca") or {}).get("pca_summary") or {}
    if pca:
        add("PCA components", pca.get("n_comps"),
            "default" if pca.get("n_comps") == pca.get("n_comps_requested") else "clamped to rank")

    integration = (art.get("run_integration") or {}).get("integration_summary") or {}
    if integration:
        add("integration", integration.get("method") or "skipped",
            f"{integration.get('n_batches') or 0} batches on `{integration.get('batch_key')}`")

    clustering = (art.get("run_clustering") or {}).get("clustering_summary") or {}
    if clustering:
        add("Leiden resolution", clustering.get("resolution"), "config or default")
        add("clusters found", clustering.get("n_clusters"), "result")

    annotate = (art.get("annotate_cells") or {}).get("annotation_summary") or {}
    if annotate.get("model"):
        add("CellTypist model", annotate.get("model"), "operator (config)")

    if not rows:
        return Section("P1", "Decisions", "audit", False, "no step recorded a decision")
    return Section("P1", "Decisions", "audit", True,
                   body=["Where a value came from matters as much as the value. "
                         "`operator` means a person chose it; anything else was derived "
                         "or is a documented default."],
                   tables=[Table("Every parameter that could have been different",
                                 ["parameter", "value", "source"], rows)])


def _section_verdicts(audit: list[dict[str, Any]]) -> Section:
    judged = [r for r in audit if r.get("event") == "judge"]
    if not judged:
        return Section("P2", "Judge verdicts", "audit", False, "no judge ran")
    rows = [
        [r.get("step"), r.get("verdict"), _fmt(r.get("score")),
         "; ".join((r.get("reasons") or [])[:2])[:300]]
        for r in judged
    ]
    flagged = [r["step"] for r in judged if r.get("verdict") in ("warn", "fail")]
    body = [f"{len(judged)} steps judged; {len(flagged)} raised something."]
    if flagged:
        body.append("Flagged: " + ", ".join(f"`{s}`" for s in flagged))

    tables = [Table("Per step", ["step", "verdict", "score", "reasons"], rows)]
    # Advice is recorded whether or not anyone took it. A run where the
    # operator set 15 and the model had suggested 20 is a more useful record
    # than one that only kept the number that won.
    advice_rows = [
        [r.get("step"), a.get("parameter"), _fmt(a.get("suggested_value")),
         a.get("confidence"), str(a.get("rationale") or "")[:300]]
        for r in judged for a in (r.get("advice") or [])
    ]
    if advice_rows:
        body.append(
            "The suggestions below were offered to the operator and are recorded "
            "whether or not they were followed. Nothing here was applied."
        )
        tables.append(Table("Suggested values",
                            ["step", "parameter", "suggested", "confidence", "rationale"],
                            advice_rows))
    return Section("P2", "Judge verdicts", "audit", True, body=body, tables=tables)


def _section_human(audit: list[dict[str, Any]]) -> Section:
    decisions = [r for r in audit if r.get("event") == "human_gate_close"]
    if not decisions:
        return Section("P3", "Human decisions", "audit", False,
                       "the run never stopped at a gate")
    rows = [[r.get("gate"), r.get("step"), r.get("decision"), r.get("rationale") or "—"]
            for r in decisions]
    return Section("P3", "Human decisions", "audit", True,
                   tables=[Table("Gates", ["gate", "step", "decision", "rationale"], rows)])


def _section_messages(art: dict[str, Any]) -> Section:
    rows: list[list[Any]] = []
    for step, output in art.items():
        if not isinstance(output, dict):
            continue
        for kind in ("warnings", "notes"):
            for message in output.get(kind) or []:
                rows.append([step, kind[:-1], str(message)[:400]])
    if not rows:
        return Section("P4", "Warnings and notes", "audit", True,
                       body=["No step raised a warning or a note."])
    return Section("P4", "Warnings and notes", "audit", True,
                   tables=[Table(f"{len(rows)} messages", ["step", "kind", "message"], rows)])


def _section_reproducibility(metadata: dict[str, Any], art: dict[str, Any]) -> Section:
    if not metadata:
        return Section("P5", "Reproducibility", "audit", False,
                       "run_metadata.json was not found beside the audit log")
    runtime = metadata.get("runtime") or {}
    source = metadata.get("source") or {}
    rows = [
        ["run id", metadata.get("run_id")],
        ["started", runtime.get("started_at")],
        ["python", runtime.get("python_version")],
        ["platform", runtime.get("platform")],
        ["host", runtime.get("hostname")],
        ["git commit", source.get("commit")],
        ["git dirty", _fmt(source.get("dirty"))],
        ["command", " ".join(source.get("command") or [])[:300]],
        ["config sha256", (source.get("config_sha256") or "")[:16] + "…"],
        ["random seed", _fmt((metadata.get("seeds") or {}).get("random_state"))],
    ]
    annotate = (art.get("annotate_cells") or {}).get("annotation_summary") or {}
    if annotate.get("model_sha256"):
        rows.append(["CellTypist model", f"{annotate.get('model')} "
                                         f"({annotate['model_sha256'][:16]}…)"])
    packages = [[name, version] for name, version in (metadata.get("packages") or {}).items()
                if version]
    body = []
    if source.get("dirty"):
        body.append("**The working tree had uncommitted changes.** The commit below does not "
                    "fully describe the code that ran.")
    return Section(
        "P5", "Reproducibility", "audit", True, body=body,
        tables=[Table("Run", ["field", "value"], [[k, _fmt(v)] for k, v in rows]),
                Table("Package versions", ["package", "version"], packages)],
    )


# --- assembling -------------------------------------------------------------------


def collect(payload: dict[str, Any]) -> tuple[ReportModel, Path]:
    """Turn artifacts, run metadata and the audit log into one model."""
    art = payload.get("artifacts") or {}
    run_dir = Path(payload.get("run_dir") or ".")
    out_dir = run_dir / TOOL_NAME
    figures = out_dir / "figures"

    metadata = _read_json(run_dir / "run_metadata.json")
    audit = _read_audit(run_dir / "audit.jsonl")

    annotate = art.get("annotate_cells") or {}
    final = _load(annotate.get("adata_path") or (art.get("find_markers") or {}).get("adata_path"))

    sections = [
        _section_funnel(art, figures),
        _section_qc(art, figures),
        _section_embedding(final, art, figures),
        _section_markers(final, art, figures),
        _section_composition(final, figures),
        _section_confidence(art, figures),
        _section_cross_check(art, audit, figures),
        _section_barcode_rank(art, figures),
        _section_qc_per_sample(art, figures),
        _section_filter_reasons(art, figures),
        _section_doublets(final, art, figures),
        _section_pca(final, art, figures),
        _section_integration(final, art, figures),
        _section_decisions(art),
        _section_verdicts(audit),
        _section_human(audit),
        _section_messages(art),
        _section_reproducibility(metadata, art),
    ]

    clustering = (art.get("run_clustering") or {}).get("clustering_summary") or {}
    annotation = annotate.get("annotation_summary") or {}
    headline = [
        ("cells", _fmt(getattr(final, "n_obs", None) if final is not None else None)),
        ("samples", _fmt(int(final.obs["sample"].nunique())
                         if final is not None and "sample" in final.obs else None)),
        ("clusters", _fmt(clustering.get("n_clusters"))),
        ("cell types", _fmt(annotation.get("n_cell_types"))),
    ]

    model = ReportModel(
        project=str(payload.get("project") or payload.get("run_id") or "scRNA-seq run"),
        run_id=str(metadata.get("run_id") or payload.get("run_id") or "unknown"),
        generated_at=datetime.now(timezone.utc).isoformat(timespec="seconds"),
        headline=headline,
        sections=sections,
    )
    return model, out_dir


# --- rendering ---------------------------------------------------------------------


def render_markdown(model: ReportModel, out_dir: Path) -> str:
    lines = [f"# {model.project}", "",
             f"Run `{model.run_id}` · generated {model.generated_at}", "",
             " · ".join(f"**{value}** {label}" for label, value in model.headline), ""]
    for tier, tier_title in TIERS:
        chosen = [s for s in model.sections if s.tier == tier]
        if not chosen:
            continue
        lines += [f"## {tier_title}", ""]
        for section in chosen:
            lines.append(f"### {section.key} · {section.title}")
            lines.append("")
            if not section.available:
                lines += [f"_Not available: {section.reason}._", ""]
                continue
            lines += [line for line in section.body] + ([""] if section.body else [])
            for figure in section.figures:
                rel = Path(figure).relative_to(out_dir) if str(figure).startswith(str(out_dir)) \
                    else Path(figure).name
                lines += [f"![{section.title}]({rel})", ""]
            for table in section.tables:
                lines += _markdown_table(table) + [""]
    path = out_dir / "report.md"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("\n".join(lines).rstrip() + "\n", encoding="utf-8")
    return str(path)


def _markdown_table(table: Table) -> list[str]:
    lines = [f"**{table.caption}**", "",
             "| " + " | ".join(str(c) for c in table.columns) + " |",
             "|" + "|".join(["---"] * len(table.columns)) + "|"]
    for row in table.rows:
        cells = [str(v).replace("|", "\\|") if v is not None else "—" for v in row]
        cells += [""] * (len(table.columns) - len(cells))
        lines.append("| " + " | ".join(cells) + " |")
    return lines


CSS = """
body{font:15px/1.6 -apple-system,BlinkMacSystemFont,'Segoe UI',Helvetica,Arial,sans-serif;
 max-width:1100px;margin:2rem auto;padding:0 1.5rem;color:#1a1a1a}
h1{border-bottom:2px solid #333;padding-bottom:.3rem}
h2{margin-top:2.5rem;border-bottom:1px solid #ddd;padding-bottom:.2rem}
h3{margin-top:1.8rem;color:#333}
.headline{display:flex;gap:1.5rem;flex-wrap:wrap;margin:1rem 0 2rem}
.headline div{background:#f4f6f8;padding:.6rem 1rem;border-radius:6px}
.headline b{display:block;font-size:1.5rem}
.unavailable{color:#666;background:#f7f7f7;border-left:3px solid #bbb;padding:.6rem 1rem;
 border-radius:0 4px 4px 0}
table{border-collapse:collapse;margin:1rem 0;font-size:13px;width:100%}
th,td{border:1px solid #ddd;padding:.35rem .6rem;text-align:left;vertical-align:top}
th{background:#f4f6f8}
img{max-width:100%;height:auto;margin:1rem 0;border:1px solid #eee;border-radius:4px}
caption{caption-side:top;text-align:left;font-weight:600;padding-bottom:.3rem}
code{background:#f4f6f8;padding:.1rem .3rem;border-radius:3px;font-size:90%}
"""


def render_html(model: ReportModel, out_dir: Path, *, inline: bool = True) -> str:
    def esc(value: Any) -> str:
        return html.escape("" if value is None else str(value))

    parts = [f"<!doctype html><html><head><meta charset='utf-8'>",
             f"<title>{esc(model.project)}</title><style>{CSS}</style></head><body>",
             f"<h1>{esc(model.project)}</h1>",
             f"<p>Run <code>{esc(model.run_id)}</code> · generated {esc(model.generated_at)}</p>",
             "<div class='headline'>"]
    parts += [f"<div><b>{esc(v)}</b>{esc(label)}</div>" for label, v in model.headline]
    parts.append("</div>")

    for tier, tier_title in TIERS:
        chosen = [s for s in model.sections if s.tier == tier]
        if not chosen:
            continue
        parts.append(f"<h2>{esc(tier_title)}</h2>")
        for section in chosen:
            parts.append(f"<h3>{esc(section.key)} · {esc(section.title)}</h3>")
            if not section.available:
                parts.append(f"<p class='unavailable'>Not available: {esc(section.reason)}</p>")
                continue
            for line in section.body:
                parts.append(f"<p>{esc(line)}</p>")
            for figure in section.figures:
                parts.append(f"<img alt='{esc(section.title)}' src='{_img_src(figure, out_dir, inline)}'>")
            for table in section.tables:
                parts.append(f"<table><caption>{esc(table.caption)}</caption><tr>"
                             + "".join(f"<th>{esc(c)}</th>" for c in table.columns) + "</tr>")
                for row in table.rows:
                    parts.append("<tr>" + "".join(f"<td>{esc(v)}</td>" for v in row) + "</tr>")
                parts.append("</table>")
    parts.append("</body></html>")

    path = out_dir / "report.html"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("\n".join(parts), encoding="utf-8")
    return str(path)


def _img_src(figure: str, out_dir: Path, inline: bool) -> str:
    """Inline as a data URI so one HTML file can be sent to someone."""
    if inline:
        try:
            data = base64.b64encode(Path(figure).read_bytes()).decode("ascii")
            return f"data:image/png;base64,{data}"
        except OSError:
            pass
    try:
        return str(Path(figure).relative_to(out_dir))
    except ValueError:
        return str(figure)


def run(payload: dict[str, Any]) -> dict[str, Any]:
    config = payload.get("config") or {}
    try:
        model, out_dir = collect(payload)
    except Exception as exc:  # noqa: BLE001 - a report must not be the thing that fails a run
        return _result(errors=[f"could not assemble the report: {type(exc).__name__}: {exc}"])

    out_dir.mkdir(parents=True, exist_ok=True)
    markdown_path = render_markdown(model, out_dir)
    html_path = render_html(model, out_dir, inline=bool(config.get("inline_figures", True)))

    model_path = out_dir / "report_model.json"
    model_path.write_text(
        json.dumps(
            {
                "project": model.project, "run_id": model.run_id,
                "generated_at": model.generated_at, "headline": model.headline,
                "sections": [
                    {"key": s.key, "title": s.title, "tier": s.tier, "available": s.available,
                     "reason": s.reason, "body": s.body, "figures": s.figures,
                     "tables": [{"caption": t.caption, "columns": t.columns, "rows": t.rows}
                                for t in s.tables]}
                    for s in model.sections
                ],
            },
            indent=2, ensure_ascii=False, default=str,
        ),
        encoding="utf-8",
    )

    figures = [f for s in model.sections for f in s.figures]
    missing = [f"{s.key}: {s.reason}" for s in model.sections if not s.available]
    return _result(
        markdown_path=markdown_path,
        html_path=html_path,
        model_path=str(model_path),
        figure_paths=figures,
        sections={s.key: s.available for s in model.sections},
        notes=[f"{len(missing)} section(s) not available: " + "; ".join(missing)] if missing else [],
        metrics={
            "n_sections": len(model.sections),
            "n_available": sum(1 for s in model.sections if s.available),
            "n_figures": len(figures),
        },
    )


def _result(
    *,
    markdown_path: str | None = None,
    html_path: str | None = None,
    model_path: str | None = None,
    figure_paths: list[str] | None = None,
    sections: dict[str, bool] | None = None,
    warnings: list[str] | None = None,
    notes: list[str] | None = None,
    errors: list[str] | None = None,
    metrics: dict[str, Any] | None = None,
) -> dict[str, Any]:
    return {
        "markdown_path": markdown_path,
        "html_path": html_path,
        "model_path": model_path,
        "figure_paths": figure_paths or [],
        "sections": sections or {},
        "recommended_next_tool": None,
        "metrics": metrics or {},
        "notes": notes or [],
        "warnings": warnings or [],
        "errors": errors or [],
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=TOOL_NAME)
    parser.add_argument("--run-dir", required=True,
                        help="a runs/<run_id> directory holding audit.jsonl and the step outputs")
    parser.add_argument("--project", default="scRNA-seq run")
    parser.add_argument("--no-inline-figures", action="store_true",
                        help="reference figures by path instead of embedding them")
    args = parser.parse_args(argv)

    run_dir = Path(args.run_dir)
    # Standalone: rebuild the artifact map from what each step left on disk.
    artifacts: dict[str, Any] = {}
    for record in _read_audit(run_dir / "audit.jsonl"):
        if record.get("event") == "step_end":
            artifacts.setdefault(record["step"], {})
    for step_dir in sorted(run_dir.iterdir()) if run_dir.exists() else []:
        if step_dir.is_dir() and (step_dir / "adata.h5ad").exists():
            artifacts.setdefault(step_dir.name, {})["adata_path"] = str(step_dir / "adata.h5ad")

    result = run({
        "artifacts": artifacts,
        "run_dir": str(run_dir),
        "project": args.project,
        "config": {"inline_figures": not args.no_inline_figures},
    })
    print(json.dumps(result, indent=2, ensure_ascii=False, default=str))
    return 1 if result["errors"] else 0


if __name__ == "__main__":
    raise SystemExit(main())
