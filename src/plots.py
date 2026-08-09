"""Figures for the report. Drawing only — no analysis, no artifact assembly.

Every function here takes data that some step already computed and returns a
path, or `None` when it could not draw. None of them raise: a report missing
one panel is worth far more than no report, and the section that asked for the
figure records why it is absent.

The division matters and is set by `docs/report_contract.md`. Anything with a
seed, a neighbour count or a package version behind it belongs to the step that
owns it — recomputing an embedding at report time would produce a picture of a
run that never happened. What is left for this module is genuinely only
drawing: reading recorded numbers and putting them on axes.

The one subtlety is that some scanpy plotting functions are not purely
drawing. `sc.pl.rank_genes_groups_dotplot` will silently run
`sc.tl.dendrogram` — an analysis step, writing to `uns` — unless told not to,
so it is always called with `dendrogram=False` here.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any, Sequence

#: Enough to read on a screen without being enormous in an HTML page.
DPI = 130

#: Marker genes shown per cluster in the dotplot. More than a handful and the
#: axis is unreadable at any figure width.
MARKERS_PER_CLUSTER = 3

#: QC metrics, in the order a reader looks at them, with display names.
QC_METRICS = (
    ("n_genes_by_counts", "genes per cell"),
    ("total_counts", "UMI per cell"),
    ("pct_counts_mt", "% mitochondrial"),
    ("pct_counts_erythroid", "% haemoglobin"),
)


def _pyplot():
    """Import matplotlib with a non-interactive backend, once and late."""
    import matplotlib

    matplotlib.use("Agg")  # a pipeline has no display
    import matplotlib.pyplot as plt

    return plt


def _save(figure: Any, path: Path) -> str:
    path.parent.mkdir(parents=True, exist_ok=True)
    figure.savefig(path, dpi=DPI, bbox_inches="tight")
    return str(path)


def _safe(draw):
    """Run a drawing function, returning None instead of raising.

    Wrapped rather than repeated in every function: the failure mode is always
    the same, and always the same response — no figure, report continues, the
    section says it is missing.
    """

    def wrapped(*args: Any, **kwargs: Any) -> str | None:
        plt = _pyplot()
        try:
            return draw(*args, **kwargs)
        except Exception:  # noqa: BLE001 - a lost panel must not cost the report
            return None
        finally:
            plt.close("all")

    wrapped.__name__ = draw.__name__
    wrapped.__doc__ = draw.__doc__
    return wrapped


# --- M1: what happened to the cells -------------------------------------------


@_safe
def retention_funnel(stages: Sequence[tuple[str, int]], path: Path) -> str | None:
    """Cells surviving each stage, with the loss at each step labelled.

    The single most useful orientation figure: it answers "how much of the data
    reached the conclusions" before any of the biology is discussed.
    """
    if len(stages) < 2:
        return None
    plt = _pyplot()

    labels = [name for name, _ in stages]
    values = [count for _, count in stages]
    figure, axis = plt.subplots(figsize=(7, 0.55 * len(stages) + 1.2))
    axis.barh(range(len(values)), values, color="#4C78A8")
    axis.set_yticks(range(len(labels)), labels)
    axis.invert_yaxis()
    axis.set_xlabel("cells")

    start = values[0] or 1
    for index, value in enumerate(values):
        lost = values[index - 1] - value if index else 0
        suffix = f"   -{lost:,}" if lost > 0 else ""
        axis.text(value, index, f"  {value:,} ({value / start:.0%}){suffix}", va="center", fontsize=9)
    axis.set_xlim(0, start * 1.35)
    axis.spines[["top", "right"]].set_visible(False)
    axis.set_title("cells retained at each stage")
    return _save(figure, path)


# --- M2 / A2: quality control --------------------------------------------------


@_safe
def qc_before_after(before: Any, after: Any, path: Path) -> str | None:
    """Each QC metric before and after filtering, on shared axes.

    Shown as paired violins rather than two separate figures: the question is
    what the cut changed, and that is only legible when both distributions sit
    on the same scale.
    """
    import numpy as np

    plt = _pyplot()
    metrics = [(col, label) for col, label in QC_METRICS if col in before.obs]
    if not metrics:
        return None

    figure, axes = plt.subplots(1, len(metrics), figsize=(3.1 * len(metrics), 3.4))
    axes = np.atleast_1d(axes)
    for axis, (column, label) in zip(axes, metrics):
        series = [
            np.asarray(before.obs[column], dtype=float),
            np.asarray(after.obs[column], dtype=float) if column in after.obs else np.array([]),
        ]
        series = [s[np.isfinite(s)] for s in series]
        parts = axis.violinplot([s for s in series if s.size], showmedians=True)
        for index, body in enumerate(parts["bodies"]):
            body.set_facecolor("#E45756" if index == 0 else "#54A24B")
            body.set_alpha(0.65)
        axis.set_xticks(range(1, len([s for s in series if s.size]) + 1),
                        ["before", "after"][: len([s for s in series if s.size])])
        axis.set_title(label, fontsize=10)
        axis.spines[["top", "right"]].set_visible(False)
    figure.suptitle(f"QC metrics: {before.n_obs:,} cells before, {after.n_obs:,} after", fontsize=11)
    figure.tight_layout()
    return _save(figure, path)


@_safe
def qc_per_sample(adata: Any, path: Path, sample_key: str = "sample") -> str | None:
    """The same metrics split by library, where an uneven cut becomes visible."""
    import numpy as np

    plt = _pyplot()
    if sample_key not in adata.obs:
        return None
    samples = sorted(str(s) for s in adata.obs[sample_key].unique())
    metrics = [(col, label) for col, label in QC_METRICS if col in adata.obs]
    if not metrics or len(samples) < 1:
        return None

    figure, axes = plt.subplots(1, len(metrics), figsize=(3.1 * len(metrics), 3.4))
    axes = np.atleast_1d(axes)
    for axis, (column, label) in zip(axes, metrics):
        data = [
            np.asarray(adata.obs.loc[adata.obs[sample_key].astype(str) == s, column], dtype=float)
            for s in samples
        ]
        data = [d[np.isfinite(d)] for d in data]
        axis.violinplot([d for d in data if d.size], showmedians=True)
        axis.set_xticks(range(1, len(samples) + 1), samples, rotation=30, ha="right", fontsize=8)
        axis.set_title(label, fontsize=10)
        axis.spines[["top", "right"]].set_visible(False)
    figure.suptitle("QC metrics per library", fontsize=11)
    figure.tight_layout()
    return _save(figure, path)


# --- A3: why cells were removed -------------------------------------------------


@_safe
def qc_filter_reasons(flags_frame: Any, path: Path) -> str | None:
    """Which criterion removed what, how much they overlapped, and per library.

    The overlap panel is the reason the per-cell flags are recorded at all: the
    per-criterion totals cannot be taken apart again, and without it a reader
    double-counts cells that failed two cuts.
    """
    import numpy as np

    plt = _pyplot()
    criteria = [c for c in flags_frame.columns if c.startswith("fail_")]
    if not criteria:
        return None
    failed = ~flags_frame["qc_pass"].to_numpy(dtype=bool)

    has_sample = "sample" in flags_frame.columns
    figure, axes = plt.subplots(1, 3 if has_sample else 2, figsize=(11 if has_sample else 7.5, 3.4))

    counts = [int(flags_frame[c].sum()) for c in criteria]
    axes[0].bar([c.removeprefix("fail_") for c in criteria], counts, color="#E45756")
    axes[0].set_title("cells failing each criterion", fontsize=10)
    axes[0].tick_params(axis="x", rotation=30, labelsize=8)
    for index, value in enumerate(counts):
        axes[0].text(index, value, f"{value:,}", ha="center", va="bottom", fontsize=8)

    n_failed_by = flags_frame[criteria].to_numpy(dtype=bool).sum(axis=1)[failed]
    if n_failed_by.size:
        buckets = np.bincount(n_failed_by, minlength=len(criteria) + 1)[1:]
        axes[1].bar(range(1, len(buckets) + 1), buckets, color="#F58518")
        axes[1].set_xticks(range(1, len(buckets) + 1))
        axes[1].set_xlabel("criteria failed by one cell")
    axes[1].set_title("overlap between criteria", fontsize=10)

    if has_sample:
        grouped = flags_frame.groupby("sample")["qc_pass"]
        rates = (1 - grouped.mean()) * 100
        axes[2].bar(rates.index.astype(str), rates.to_numpy(), color="#4C78A8")
        axes[2].set_ylabel("% removed")
        axes[2].set_title("removal rate per library", fontsize=10)
        axes[2].tick_params(axis="x", rotation=30, labelsize=8)

    for axis in axes:
        axis.spines[["top", "right"]].set_visible(False)
    figure.tight_layout()
    return _save(figure, path)


# --- A1: the barcode-rank curve --------------------------------------------------


@_safe
def barcode_rank(curve_path: str | Path, evidence: dict[str, Any], path: Path,
                 selected_cells: int | None = None) -> str | None:
    """The cliff-and-knee curve, with every cutoff that was considered marked.

    Drawn from the vector `cell_calling_review` saved rather than from the raw
    matrix, which is why that vector is saved at all.
    """
    import numpy as np

    plt = _pyplot()
    counts = np.load(curve_path)["sorted_umi_counts"]
    positive = counts[counts > 0]
    if positive.size < 2:
        return None

    figure, axis = plt.subplots(figsize=(6.4, 4.2))
    axis.loglog(np.arange(1, positive.size + 1), positive, color="#4C78A8", lw=1.6)
    for key, label, colour in (
        ("knee_rank", "knee", "#54A24B"),
        ("inflection_rank", "inflection", "#F58518"),
    ):
        rank = evidence.get(key)
        if rank:
            axis.axvline(rank, color=colour, ls="--", lw=1.2, label=f"{label} ({rank:,})")
    if selected_cells:
        axis.axvline(selected_cells, color="#E45756", lw=1.6,
                     label=f"selected ({selected_cells:,})")
    axis.set_xlabel("barcode rank")
    axis.set_ylabel("UMI count")
    axis.set_title("barcode rank")
    axis.legend(fontsize=8, frameon=False)
    axis.spines[["top", "right"]].set_visible(False)
    return _save(figure, path)


# --- A4: doublets -----------------------------------------------------------------


@_safe
def doublet_scores(adata: Any, per_sample: dict[str, Any], path: Path,
                   sample_key: str = "sample") -> str | None:
    """Score distribution per library with the threshold that was applied."""
    import numpy as np

    plt = _pyplot()
    if "doublet_score" not in adata.obs:
        return None
    samples = (
        sorted(str(s) for s in adata.obs[sample_key].unique())
        if sample_key in adata.obs else ["all cells"]
    )
    figure, axes = plt.subplots(1, len(samples), figsize=(4.0 * len(samples), 3.3), squeeze=False)
    for axis, sample in zip(axes[0], samples):
        mask = (
            adata.obs[sample_key].astype(str) == sample
            if sample_key in adata.obs else np.ones(adata.n_obs, dtype=bool)
        )
        scores = np.asarray(adata.obs.loc[mask, "doublet_score"], dtype=float)
        scores = scores[np.isfinite(scores)]
        if not scores.size:
            continue
        axis.hist(scores, bins=50, color="#4C78A8")
        entry = per_sample.get(sample) or {}
        threshold = entry.get("threshold_used")
        if threshold is not None:
            axis.axvline(threshold, color="#E45756", ls="--", lw=1.3,
                         label=f"threshold {threshold:g}")
            axis.legend(fontsize=8, frameon=False)
        called = entry.get("n_doublets")
        axis.set_title(
            f"{sample}" + (f" — {called:,} called" if called is not None else ""), fontsize=10
        )
        axis.set_yscale("log")
        axis.set_xlabel("doublet score")
        axis.spines[["top", "right"]].set_visible(False)
    figure.tight_layout()
    return _save(figure, path)


# --- A5: PCA and feature selection --------------------------------------------------


@_safe
def pca_and_hvg(adata: Any, path: Path) -> str | None:
    """Variance explained per component, and the mean-variance fit HVGs came from."""
    import numpy as np

    plt = _pyplot()
    ratio = (adata.uns.get("pca") or {}).get("variance_ratio")
    has_hvg = {"means", "variances_norm", "highly_variable"} <= set(adata.var.columns)
    if ratio is None and not has_hvg:
        return None

    panels = int(ratio is not None) + int(has_hvg)
    figure, axes = plt.subplots(1, panels, figsize=(4.6 * panels, 3.6), squeeze=False)
    index = 0
    if ratio is not None:
        ratio = np.asarray(ratio, dtype=float)
        axis = axes[0][index]
        axis.plot(range(1, ratio.size + 1), ratio * 100, marker="o", ms=3, color="#4C78A8")
        axis.set_xlabel("principal component")
        axis.set_ylabel("% variance")
        axis.set_title(f"variance explained ({ratio.sum():.0%} total)", fontsize=10)
        axis.spines[["top", "right"]].set_visible(False)
        index += 1
    if has_hvg:
        axis = axes[0][index]
        flags = adata.var["highly_variable"].to_numpy(dtype=bool)
        means = adata.var["means"].to_numpy(dtype=float)
        norm = adata.var["variances_norm"].to_numpy(dtype=float)
        axis.scatter(means[~flags], norm[~flags], s=3, c="#BAB0AC", label="other", rasterized=True)
        axis.scatter(means[flags], norm[flags], s=3, c="#E45756",
                     label=f"HVG ({int(flags.sum()):,})", rasterized=True)
        axis.set_xscale("log")
        axis.set_xlabel("mean expression")
        axis.set_ylabel("normalized variance")
        axis.set_title("feature selection", fontsize=10)
        axis.legend(fontsize=8, frameon=False, markerscale=3)
        axis.spines[["top", "right"]].set_visible(False)
    figure.tight_layout()
    return _save(figure, path)


# --- M3 / A6: embeddings ---------------------------------------------------------


@_safe
def embedding_panels(adata: Any, basis: str, colours: Sequence[str], path: Path,
                     title: str | None = None) -> str | None:
    """One panel per `obs` column, on a basis some step already computed."""
    import scanpy as sc

    plt = _pyplot()
    if basis not in adata.obsm:
        return None
    usable = [c for c in colours if c in adata.obs]
    if not usable:
        return None
    axes = sc.pl.embedding(adata, basis=basis, color=usable, show=False,
                           wspace=0.35, ncols=min(len(usable), 4))
    figure = (axes[0] if isinstance(axes, list) else axes).get_figure()
    if title:
        figure.suptitle(title, fontsize=12)
    return _save(figure, path)


@_safe
def integration_diagnostic(adata: Any, path: Path, sample_key: str = "sample",
                           before_key: str = "X_umap_unintegrated",
                           after_key: str = "X_umap") -> str | None:
    """Libraries before and after correction, on the same colouring.

    A diagnostic, not a proof. It shows whether libraries mix; it cannot
    separate a correction that worked from one that erased real differences
    between samples. Saying more than that needs batch-mixing and
    biological-conservation metrics this pipeline does not compute.
    """
    import scanpy as sc

    plt = _pyplot()
    if before_key not in adata.obsm or after_key not in adata.obsm:
        return None
    if sample_key not in adata.obs:
        return None

    figure, axes = plt.subplots(1, 2, figsize=(11, 4.4))
    for axis, key, label in (
        (axes[0], before_key, "before integration"),
        (axes[1], after_key, "after integration"),
    ):
        sc.pl.embedding(adata, basis=key, color=sample_key, show=False, ax=axis,
                        legend_loc="right margin" if key == after_key else None)
        axis.set_title(label, fontsize=11)
    figure.suptitle("integration diagnostic — do libraries mix?", fontsize=12)
    figure.tight_layout()
    return _save(figure, path)


# --- M4: markers ------------------------------------------------------------------


@_safe
def marker_dotplot(adata: Any, path: Path, groupby: str = "leiden",
                   n_genes: int = MARKERS_PER_CLUSTER) -> str | None:
    """Top markers per cluster: dot size is the fraction expressing, colour the mean.

    `dendrogram=False` is not cosmetic. Left to its default this call runs
    `sc.tl.dendrogram`, which is an analysis step that writes to `uns` — the
    report is not allowed to do that.
    """
    import scanpy as sc

    _pyplot()
    if "rank_genes_groups" not in adata.uns or groupby not in adata.obs:
        return None
    plot = sc.pl.rank_genes_groups_dotplot(
        adata, n_genes=n_genes, groupby=groupby, dendrogram=False,
        show=False, return_fig=True,
    )
    path.parent.mkdir(parents=True, exist_ok=True)
    plot.savefig(path, dpi=DPI, bbox_inches="tight")
    return str(path)


# --- M5: composition ---------------------------------------------------------------


@_safe
def composition_by_sample(adata: Any, path: Path, sample_key: str = "sample",
                          type_key: str = "cell_type") -> str | None:
    """Cell-type proportions per library, as a stacked bar.

    Proportions rather than counts: libraries differ in size, and the question
    this answers is whether the composition differs, not the depth.
    """
    import pandas as pd

    plt = _pyplot()
    if sample_key not in adata.obs or type_key not in adata.obs:
        return None
    table = pd.crosstab(adata.obs[sample_key].astype(str), adata.obs[type_key].astype(str))
    if table.empty:
        return None
    fractions = table.div(table.sum(axis=1), axis=0)

    figure, axis = plt.subplots(figsize=(max(6.0, 1.6 * len(fractions)), 4.6))
    bottom = None
    for column in fractions.columns:
        values = fractions[column].to_numpy()
        axis.bar(fractions.index, values, bottom=bottom, label=column)
        bottom = values if bottom is None else bottom + values
    axis.set_ylabel("fraction of cells")
    axis.set_title("cell-type composition per library")
    axis.legend(fontsize=7, frameon=False, bbox_to_anchor=(1.01, 1), loc="upper left")
    axis.spines[["top", "right"]].set_visible(False)
    figure.tight_layout()
    return _save(figure, path)


# --- M6: how much to trust each label -------------------------------------------------


@_safe
def annotation_confidence(per_cluster: dict[str, Any], path: Path) -> str | None:
    """Confidence against consensus, per cluster.

    Two different doubts, and they come apart: a cluster can be labelled with
    high confidence while its own cells disagree with each other, which means
    the cluster merges populations rather than that the label is wrong.
    """
    plt = _pyplot()
    entries = [(name, e) for name, e in per_cluster.items() if e.get("cell_type")]
    if not entries:
        return None
    entries.sort(key=lambda kv: (len(kv[0]), kv[0]))

    names = [name for name, _ in entries]
    confidence = [float(e.get("median_conf_score") or 0) for _, e in entries]
    consensus = [float(e.get("per_cell_consensus") or 0) for _, e in entries]
    sizes = [int(e.get("n_cells") or 0) for _, e in entries]

    figure, axes = plt.subplots(1, 2, figsize=(11.5, 4.0))
    positions = range(len(names))
    axes[0].bar([p - 0.2 for p in positions], confidence, width=0.4,
                label="median confidence", color="#4C78A8")
    axes[0].bar([p + 0.2 for p in positions], consensus, width=0.4,
                label="per-cell consensus", color="#F58518")
    axes[0].set_xticks(list(positions), names, fontsize=8)
    axes[0].set_ylim(0, 1.05)
    axes[0].set_xlabel("cluster")
    axes[0].legend(fontsize=8, frameon=False)
    axes[0].set_title("confidence and consensus per cluster", fontsize=10)

    scatter = axes[1].scatter(confidence, consensus, s=[max(20, min(400, n)) for n in sizes],
                              c="#54A24B", alpha=0.7)
    for name, x, y in zip(names, confidence, consensus):
        axes[1].annotate(name, (x, y), fontsize=7, xytext=(3, 3), textcoords="offset points")
    axes[1].set_xlabel("median confidence")
    axes[1].set_ylabel("per-cell consensus")
    axes[1].set_title("point size is cluster size", fontsize=10)
    for axis in axes:
        axis.spines[["top", "right"]].set_visible(False)
    figure.tight_layout()
    return _save(figure, path)


# --- M7: annotation cross-check ---------------------------------------------------


#: Cell types drawn on the cross-check dot plot. The database scores all 55 in
#: the blood panel, and a 15 x 55 grid is unreadable; these are the ones any
#: cluster actually ranked highly.
CROSS_CHECK_TYPES = 14


@_safe
def annotation_cross_check(score_table_path: str | Path, per_cluster: dict[str, Any],
                           path: Path) -> str | None:
    """Marker-database scores per cluster, with CellTypist's own call marked.

    Reads the CSV `cross_check_annotation` already wrote. Nothing is scored
    here — the report renders, it does not analyse — and the file exists
    precisely so a 55-column table never has to travel in the state.

    The shape follows scMayoMap's own figure: clusters across, cell types down,
    dot size and colour the normalised score. What is added is the red ring on
    whatever CellTypist chose, because the question this figure exists to answer
    is not "what did the database say" but "did the two methods land in the same
    place".
    """
    import csv

    plt = _pyplot()
    source = Path(score_table_path)
    if not source.exists() or not per_cluster:
        return None

    scores: dict[tuple[str, str], float] = {}
    with source.open(newline="", encoding="utf-8") as handle:
        for row in csv.DictReader(handle):
            try:
                scores[(row["cluster"], row["cell_type"])] = float(row["score"])
            except (KeyError, TypeError, ValueError):
                continue
    if not scores:
        return None

    clusters = sorted(per_cluster, key=lambda c: (len(c), c))
    # Rank cell types by the best score any cluster gave them, so the rows are
    # the ones with something to show rather than the alphabetical first few.
    best: dict[str, float] = {}
    for (_cluster, cell_type), value in scores.items():
        best[cell_type] = max(best.get(cell_type, 0.0), value)
    types = [t for t, _ in sorted(best.items(), key=lambda kv: -kv[1])[:CROSS_CHECK_TYPES]]
    types.reverse()

    figure, axis = plt.subplots(figsize=(2.2 + 0.66 * len(clusters), 3.0 + 0.33 * len(types)))
    xs, ys, sizes, colours = [], [], [], []
    for x, cluster in enumerate(clusters):
        for y, cell_type in enumerate(types):
            value = scores.get((cluster, cell_type), 0.0)
            if value <= 0:
                continue
            xs.append(x)
            ys.append(y)
            sizes.append(20 + 620 * value)
            colours.append(value)

    dots = axis.scatter(xs, ys, s=sizes, c=colours, cmap="viridis",
                        vmin=0, vmax=max(colours) if colours else 1,
                        alpha=0.85, edgecolors="none")

    # Ring the database's own top hit. An earlier version ringed CellTypist's
    # label instead, and drew nothing at all: the two vocabularies share almost
    # no strings, so the ring never fired. CellTypist's call is printed under
    # the axis instead, where no name matching is needed to read it.
    for x, cluster in enumerate(clusters):
        best = max(((t, scores.get((cluster, t), 0.0)) for t in types),
                   key=lambda kv: kv[1], default=(None, 0.0))
        if best[0] is not None and best[1] > 0:
            axis.scatter([x], [types.index(best[0])], s=780, facecolors="none",
                         edgecolors="#D62728", linewidths=1.5, zorder=3)

    ticks = []
    for cluster in clusters:
        label = (per_cluster.get(cluster) or {}).get("celltypist_label") or "—"
        ticks.append(f"{cluster}\n{label[:22]}")
    axis.set_xticks(range(len(clusters)), ticks, fontsize=7, rotation=45, ha="right")
    axis.set_yticks(range(len(types)), types, fontsize=8)
    axis.set_xlabel("cluster, and the cell type CellTypist assigned it", fontsize=9)
    axis.set_xlim(-0.6, len(clusters) - 0.4)
    axis.set_ylim(-0.6, len(types) - 0.4)
    axis.grid(True, which="major", color="#EEEEEE", linewidth=0.6, zorder=0)
    axis.set_axisbelow(True)
    axis.spines[["top", "right"]].set_visible(False)
    axis.set_title("marker-database score per cluster; red ring is the database's own "
                   "top hit\nread each column against the label beneath it — where "
                   "they name different cells, look",
                   fontsize=10)
    figure.colorbar(dots, ax=axis, shrink=0.6, label="normalised score")
    figure.tight_layout()
    return _save(figure, path)
