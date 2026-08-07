"""Score the clusters a second time from a marker database, and flag the gaps.

## Why a second annotator, when `annotate_cells` already answered
CellTypist is a logistic regression trained on labelled reference cells. It
reads the expression matrix and has no idea which genes it is keying on.
scMayoMap matches this run's own differential-expression table against a
curated marker database, and never sees the matrix.

They fail differently, and that is the whole value. On the PBMC test object
CellTypist calls clusters 0 and 1 classical monocytes with confidence 1.00 and
0.99; scMayoMap calls both Neutrophil. Neutrophils cannot be there — a Ficoll
gradient leaves granulocytes in the pellet — and the reason it says so is
recoverable: the database gives Neutrophil 84 markers and CD14 Monocyte 12, of
which 6 are shared, so pan-myeloid genes (ITGAM, ITGAX, CD33, TLR2, FCGR1A)
that both cell types express are counted for one and not the other. Agreement
between two methods with unrelated blind spots is evidence. Disagreement is a
place to look.

## This step does not decide who is right
The two vocabularies do not line up: "CD16+ NK cells" and "CD56-dim natural
killer cell" are one population under two names, "pDC" and "Dendritic cell"
differ in granularity. Matching them needs either a synonym table — a second
source of truth that drifts away from both — or a reader who knows the biology.

So the flags computed here are only the ones that need no vocabulary at all:
counts and comparisons of numbers. Both label strings go into the payload
untouched, and reconciling them belongs to a reader.

That reader had to be told. Handed this payload and asked to score the step,
the judge quoted the flag counts back and never looked at the names — 0 of 3
runs against the real endpoint, and still 0 of 3 with the pairs promoted to
their own field. `prompts/steps/cross_check_annotation.md` asks for the
comparison directly, and finds the disagreement 3 of 3.

## The tissue is a decision, not a default
Scored against all 28 tissues instead of `blood`, 14 of the PBMC object's 15
clusters change their top hit — to skin macrophages, pancreatic T cells, lung
club cells. The database has no way to know what tissue was sequenced, and
guessing wrong is not a degraded answer but a confident wrong one.

So with no `scmayomap_tissue` in config this step **compares nothing**, reports
the available tissues as evidence and stops at the human gate — the same shape
as `annotate_cells` refusing to guess a CellTypist model.

Run standalone:
    python skills/cross_check_annotation/cross_check_annotation.py \\
        <run_dir> --tissue blood
"""

from __future__ import annotations

import argparse
import csv
import json
import math
import sys
from collections import defaultdict
from pathlib import Path
from typing import Any

_PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent
if str(_PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(_PROJECT_ROOT))

TOOL_NAME = "cross_check_annotation"
INPUT_FIELDS = (
    "artifacts.find_markers",
    "artifacts.annotate_cells",
    "config.scmayomap_tissue",
    "run_dir",
)
OUTPUT_FIELDS = (
    "cross_check_state",
    "tissue",
    "per_cluster",
    "flagged",
    "cross_check_summary",
    "score_table_path",
    "evidence",
    "warnings",
    "errors",
    "recommended_next_tool",
)

#: Committed by `scripts/fetch_scmayomap_db.py`. Long form: tissue, cell_type, gene.
DATABASE_PATH = _PROJECT_ROOT / "marker_db" / "scmayomap" / "markers.csv"

#: scMayoMap's own filters, from scMayoMap.R:101. Not tuned here: changing them
#: would mean scoring something the published benchmark never measured.
PADJ_CUTOFF = 0.05
PCT_CUTOFF = 0.25

#: Below this many database-matched genes, a cluster's score rests on too little
#: to argue with. Cluster 6 of the PBMC object reaches 0.85 on ten genes.
MIN_MATCHED_GENES = 20

#: Relative gap between the top two cell types, (top1 - top2) / top1. Under this,
#: the database is not choosing between them so much as ranking noise.
MIN_RELATIVE_MARGIN = 0.10

#: A CellTypist median confidence at or above this is "sure". Paired with an
#: ambiguous database score, the two methods disagree about how hard the call is
#: even when they may agree on the label.
CONFIDENT_CELLTYPIST = 0.90


def _load_database(tissue: str | None) -> tuple[dict[str, set[str]], list[str]]:
    """Return {cell_type: {gene}} for one tissue, and every tissue on offer."""
    by_type: dict[str, set[str]] = defaultdict(set)
    tissues: set[str] = set()
    with DATABASE_PATH.open(newline="", encoding="utf-8") as handle:
        for row in csv.DictReader(handle):
            tissues.add(row["tissue"])
            if tissue is not None and row["tissue"] != tissue:
                continue
            by_type[row["cell_type"]].add(row["gene"])
    return dict(by_type), sorted(tissues)


def _read_markers(path: Path) -> tuple[dict[str, list[dict[str, float]]], list[str]]:
    """find_markers' table, filtered by scMayoMap's cutoffs and scored per gene.

    Score = (2^log2FC * pct.1) / pct.2, from scMayoMap.R:106. A pct.2 of zero —
    a gene detected in no other cluster — would divide by zero, so upstream it
    is replaced by the smallest non-zero pct.2 in the table, which requires one
    pass before scoring.
    """
    rows: list[dict[str, Any]] = []
    with path.open(newline="", encoding="utf-8") as handle:
        for row in csv.DictReader(handle):
            try:
                padj = float(row["pvals_adj"])
                pct1 = float(row["pct_nz_group"])
                pct2 = float(row["pct_nz_reference"])
                lfc = float(row["logfoldchanges"])
            except (KeyError, TypeError, ValueError):
                continue
            if math.isnan(padj) or padj > PADJ_CUTOFF or pct1 < PCT_CUTOFF:
                continue
            if math.isnan(lfc) or math.isinf(lfc):
                continue
            rows.append({"cluster": str(row["group"]), "gene": str(row["names"]).upper(),
                         "lfc": lfc, "pct1": pct1, "pct2": pct2})

    positive = [r["pct2"] for r in rows if r["pct2"] > 0]
    floor = min(positive) if positive else 1.0
    scored: dict[str, list[dict[str, float]]] = defaultdict(list)
    for row in rows:
        denominator = row["pct2"] if row["pct2"] > 0 else floor
        score = (2.0 ** row["lfc"] * row["pct1"]) / denominator
        scored[row["cluster"]].append({"gene": row["gene"], "score": score})
    return dict(scored), sorted(scored, key=_cluster_order)


def _cluster_order(name: str) -> tuple[int, Any]:
    return (0, int(name)) if name.isdigit() else (1, name)


def _cumulative_variance(values: list[float]) -> list[float]:
    """Running variance of the first n values, as scMayoMap's `cumvar` computes it.

    The R centres on a randomly chosen element first. Variance does not depend
    on that shift, so the choice cannot change the result; it only keeps
    `cumsum(x)**2` from losing precision. Centring on the mean does the same job
    and is deterministic.
    """
    if not values:
        return []
    mean = sum(values) / len(values)
    centred = [v - mean for v in values]
    out, total, total_sq = [], 0.0, 0.0
    for n, value in enumerate(centred, start=1):
        total += value
        total_sq += value * value
        out.append((total_sq - total * total / n) / (n - 1) if n > 1 else float("nan"))
    return out


def _top_candidates(ranked: list[tuple[str, float]]) -> int:
    """How many cell types scMayoMap reports, from the jump in cumulative variance.

    scMayoMap.R:130. Scores are already sorted descending; the biggest step in
    running variance is where the leaders stop resembling the tail.
    """
    if len(ranked) < 3:
        return len(ranked)
    variance = _cumulative_variance([score for _, score in ranked])
    variance[0] = 0.0
    steps = [b - a for a, b in zip(variance, variance[1:])]
    usable = [(value, i) for i, value in enumerate(steps) if not math.isnan(value)]
    return (max(usable)[1] + 1) if usable else 1


def _score_clusters(
    per_gene: dict[str, list[dict[str, float]]],
    database: dict[str, set[str]],
) -> dict[str, dict[str, Any]]:
    """scMayoMap.R:107-122 — sum each cell type's markers, divide by matched genes, normalize."""
    out: dict[str, dict[str, Any]] = {}
    for cluster, genes in per_gene.items():
        in_database = [g for g in genes if any(g["gene"] in m for m in database.values())]
        if not in_database:
            out[cluster] = {"n_matched_genes": 0, "ranked": []}
            continue

        # The denominator is every database-matched gene in the cluster, the same
        # for all cell types, so a cell type with a long marker list outscores one
        # with a short list even where both are equally supported.
        totals = {
            cell_type: sum(g["score"] for g in in_database if g["gene"] in markers)
            for cell_type, markers in database.items()
        }
        matched = len(in_database)
        means = {c: total / matched for c, total in totals.items() if total > 0}
        grand = sum(means.values())
        normalized = {c: v / grand for c, v in means.items()} if grand > 0 else {}
        ranked = sorted(normalized.items(), key=lambda kv: -kv[1])
        out[cluster] = {"n_matched_genes": matched, "ranked": ranked}
    return out


def _flags(entry: dict[str, Any], celltypist_conf: float | None) -> list[str]:
    """Only tests that compare numbers. Nothing here reads a cell type's name."""
    found: list[str] = []
    ranked = entry["ranked"]
    if entry["n_matched_genes"] < MIN_MATCHED_GENES:
        found.append("low_marker_evidence")

    margin = entry.get("relative_margin")
    ambiguous = bool(ranked) and (entry["n_candidates"] > 1
                                  or (margin is not None and margin < MIN_RELATIVE_MARGIN))
    if ambiguous:
        found.append("ambiguous")
    if ambiguous and celltypist_conf is not None and celltypist_conf >= CONFIDENT_CELLTYPIST:
        found.append("confidence_conflict")
    return found


def run(payload: dict[str, Any]) -> dict[str, Any]:
    artifacts = payload.get("artifacts") or {}
    config = payload.get("config") or {}
    warnings: list[str] = []
    notes: list[str] = []

    if not DATABASE_PATH.exists():
        return _result(errors=[
            f"marker database missing: {DATABASE_PATH}. "
            "Run scripts/fetch_scmayomap_db.py to write it."
        ])

    markers_path = (artifacts.get("find_markers") or {}).get("marker_table_path")
    if not markers_path:
        return _result(errors=["no marker table; find_markers must run first"])
    markers_path = Path(str(markers_path)).expanduser()
    if not markers_path.exists():
        return _result(errors=[f"marker table does not exist: {markers_path}"])

    annotation = artifacts.get("annotate_cells") or {}
    celltypist = annotation.get("per_cluster") or {}
    if not celltypist:
        notes.append("annotate_cells produced no per-cluster labels; "
                     "the database scores are reported without a comparison")

    tissue = config.get("scmayomap_tissue")
    _, available = _load_database(None)

    if not tissue:
        # Same refusal as annotate_cells with no model: the evidence needed to
        # choose goes back, and the run stops at the gate rather than guessing.
        return _result(
            state="not_compared",
            evidence={
                "available_tissues": available,
                "why_it_matters": (
                    "Scored against every tissue instead of one, 14 of 15 clusters "
                    "on the PBMC test object change their top hit. The database "
                    "cannot infer the tissue, and the wrong one returns confident "
                    "wrong labels rather than an error."
                ),
                "clusters_awaiting_comparison": sorted(celltypist, key=_cluster_order),
            },
            warnings=["no scmayomap_tissue in config; nothing was cross-checked"],
            next_tool="human_review_decision",
        )

    tissue = str(tissue)
    if tissue not in available:
        return _result(errors=[f"unknown tissue {tissue!r}; expected one of {available}"])

    database, _ = _load_database(tissue)
    per_gene, clusters = _read_markers(markers_path)
    if not clusters:
        return _result(errors=[
            f"no marker rows survived padj<={PADJ_CUTOFF} and pct>={PCT_CUTOFF}"
        ])

    scored = _score_clusters(per_gene, database)

    per_cluster: dict[str, Any] = {}
    flagged: dict[str, list[str]] = {}
    for cluster in clusters:
        entry = scored[cluster]
        ranked = entry["ranked"]
        entry["n_candidates"] = _top_candidates(ranked) if ranked else 0
        top = ranked[: entry["n_candidates"]]
        entry["relative_margin"] = (
            (ranked[0][1] - ranked[1][1]) / ranked[0][1]
            if len(ranked) > 1 and ranked[0][1] > 0 else None
        )

        label = celltypist.get(cluster) or {}
        conf = label.get("median_conf_score")
        found = _flags(entry, float(conf) if conf is not None else None)
        if found:
            flagged[cluster] = found

        per_cluster[cluster] = {
            "n_matched_genes": entry["n_matched_genes"],
            "database_candidates": [
                {"cell_type": name, "score": round(score, 4)} for name, score in top
            ],
            "relative_margin": (round(entry["relative_margin"], 4)
                                if entry["relative_margin"] is not None else None),
            # Both label strings, unresolved on purpose: deciding whether they
            # name the same cell type is the judge's job, not a string compare.
            "celltypist_label": label.get("cell_type"),
            "celltypist_confidence": conf,
            "n_cells": label.get("n_cells"),
            "flags": found,
        }

    run_dir = payload.get("run_dir")
    table_path = _write_score_table(run_dir, scored) if run_dir else None
    if table_path:
        notes.append(f"full cell-type scores per cluster: {table_path}")

    thin = sum(1 for f in flagged.values() if "low_marker_evidence" in f)
    if thin:
        warnings.append(f"{thin} cluster(s) matched fewer than {MIN_MATCHED_GENES} "
                        "database genes; their scores rest on little evidence")
    conflicts = sum(1 for f in flagged.values() if "confidence_conflict" in f)
    if conflicts:
        warnings.append(f"{conflicts} cluster(s) where CellTypist was confident "
                        "but the marker database was not")

    notes.append("cell-type names come from two vocabularies and are reported as "
                 "given; no attempt is made here to decide which pairs are synonyms")

    return _result(
        state="compared",
        tissue=tissue,
        per_cluster=per_cluster,
        flagged=flagged,
        summary={
            "n_clusters": len(per_cluster),
            "n_flagged": len(flagged),
            "n_database_cell_types": len(database),
            "flag_counts": {
                name: sum(1 for f in flagged.values() if name in f)
                for name in ("low_marker_evidence", "ambiguous", "confidence_conflict")
            },
        },
        score_table_path=table_path,
        metrics={
            "clusters_scored": len(per_cluster),
            "clusters_flagged": len(flagged),
            "median_matched_genes": _median(
                [v["n_matched_genes"] for v in per_cluster.values()]
            ),
        },
        warnings=warnings,
        notes=notes,
        next_tool="human_review_decision",
    )


def _median(values: list[int]) -> float | None:
    if not values:
        return None
    ordered = sorted(values)
    middle = len(ordered) // 2
    if len(ordered) % 2:
        return float(ordered[middle])
    return (ordered[middle - 1] + ordered[middle]) / 2


def _write_score_table(run_dir: Any, scored: dict[str, dict[str, Any]]) -> str | None:
    """Every cell type's score for every cluster, linked rather than carried in state."""
    directory = Path(str(run_dir)).expanduser() / TOOL_NAME
    try:
        directory.mkdir(parents=True, exist_ok=True)
        path = directory / "scmayomap_scores.csv"
        with path.open("w", newline="", encoding="utf-8") as handle:
            writer = csv.writer(handle)
            writer.writerow(["cluster", "cell_type", "score", "rank"])
            for cluster in sorted(scored, key=_cluster_order):
                for rank, (cell_type, score) in enumerate(scored[cluster]["ranked"], start=1):
                    writer.writerow([cluster, cell_type, f"{score:.6f}", rank])
        return str(path)
    except OSError:
        return None


def _result(
    *,
    state: str = "unavailable",
    tissue: str | None = None,
    per_cluster: dict[str, Any] | None = None,
    flagged: dict[str, list[str]] | None = None,
    summary: dict[str, Any] | None = None,
    score_table_path: str | None = None,
    evidence: dict[str, Any] | None = None,
    metrics: dict[str, Any] | None = None,
    warnings: list[str] | None = None,
    notes: list[str] | None = None,
    errors: list[str] | None = None,
    next_tool: str | None = None,
) -> dict[str, Any]:
    return {
        "cross_check_state": state,
        "tissue": tissue,
        "per_cluster": per_cluster or {},
        "flagged": flagged or {},
        "cross_check_summary": summary or {},
        "score_table_path": score_table_path,
        "evidence": evidence or {},
        "recommended_next_tool": next_tool,
        "metrics": metrics or {},
        "notes": notes or [],
        "warnings": warnings or [],
        "errors": errors or [],
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=TOOL_NAME)
    parser.add_argument("run_dir", help="a completed run directory")
    parser.add_argument("--tissue", dest="scmayomap_tissue",
                        help="tissue in the marker database, e.g. blood")
    parser.add_argument("--list-tissues", action="store_true")
    args = parser.parse_args(argv)

    if args.list_tissues:
        _, available = _load_database(None)
        print("\n".join(available))
        return 0

    root = Path(args.run_dir).expanduser()
    artifacts: dict[str, Any] = {}
    for step in ("find_markers", "annotate_cells"):
        output = root / step / "output.json"
        if output.exists():
            artifacts[step] = json.loads(output.read_text())

    result = run({
        "artifacts": artifacts,
        "config": {"scmayomap_tissue": args.scmayomap_tissue},
        "run_dir": str(root),
    })
    print(json.dumps(result, indent=2))
    return 1 if result["errors"] else 0


if __name__ == "__main__":
    raise SystemExit(main())
