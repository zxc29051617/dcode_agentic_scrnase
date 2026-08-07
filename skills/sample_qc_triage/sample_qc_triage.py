"""Decide which libraries enter the run, before any of them are counted.

An optional pre-route step: it runs after `ingest_validate` and before the
FASTQ/matrix split, which is the last moment a sample can be left out cheaply.
Counting one library takes Cell Ranger 20–40 minutes, and a dead library that
gets through is worse than slow — it merges into the analysis and every number
downstream quietly includes it.

## It never excludes a sample on its own
This pipeline has already shipped one bug where a two-sample run silently
analysed one library and reported on it as the whole study. A triage step that
dropped samples on its own judgement would be that bug with a rationale.

So the shape is `apply_cell_qc_filter`'s: measure, report what each candidate
cutoff would exclude, and act only on an explicit `exclude_samples`. A flagged
sample raises a warning, which the judge and the human gate turn into a stop —
the existing machinery, not a special case here.

## Operational, not clinical
It asks whether a library can be analysed, not whether the biology is
interesting: is it in the table twice, does the table describe this run at all,
does it clear the bounds the operator set. Nothing here is a judgement about
the sample's science.

## No default thresholds
Same reasoning as `apply_cell_qc_filter`. There is no universal minimum read
count or saturation, and a number that is right for one assay quietly discards
good libraries from another. Bounds come from config, and appear in the audit
log because they do.

Run standalone:
    python skills/sample_qc_triage/sample_qc_triage.py --metrics metrics.csv
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any

_PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent
if str(_PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(_PROJECT_ROOT))

TOOL_NAME = "sample_qc_triage"
INPUT_FIELDS = (
    "artifacts.ingest_validate",
    "sample_metadata",
    "config.qc_metrics_csv",
    "config.sample_column",
    "config.sample_thresholds",
    "config.exclude_samples",
)
OUTPUT_FIELDS = (
    "triage_state",
    "included_samples",
    "excluded_samples",
    "per_sample",
    "evidence",
    "matrix_paths",
    "warnings",
    "errors",
    "recommended_next_tool",
)

#: Column names that plausibly hold the library name. Checked in order, so a
#: table carrying several of them resolves the same way every time.
SAMPLE_COLUMN_ALIASES = (
    "sample", "sample_id", "sample id", "sampleid",
    "library", "library_id", "library id", "libraryid",
    "name", "id",
)

#: Percentiles reported for each numeric column, so an operator choosing a
#: bound can see the spread they are cutting into.
PERCENTILES = (0, 10, 25, 50, 75, 90, 100)


def _load_table(payload: dict[str, Any]) -> tuple[Any, str | None]:
    """The triage table, from a CSV path or from `sample_metadata`."""
    import pandas as pd

    config = payload.get("config") or {}
    csv_path = config.get("qc_metrics_csv")
    if csv_path:
        path = Path(str(csv_path)).expanduser()
        if not path.exists():
            return None, f"qc_metrics_csv does not exist: {path}"
        try:
            return pd.read_csv(path), None
        except Exception as exc:  # noqa: BLE001 - an unreadable table is the finding
            return None, f"cannot read {path}: {type(exc).__name__}: {exc}"

    metadata = payload.get("sample_metadata") or {}
    if metadata:
        # `{sample: {metric: value}}` is the shape state carries it in.
        try:
            return pd.DataFrame.from_dict(metadata, orient="index").reset_index(
                names="sample"
            ), None
        except Exception as exc:  # noqa: BLE001
            return None, f"sample_metadata is not a table: {type(exc).__name__}: {exc}"
    return None, None


def _sample_column(frame: Any, requested: str | None) -> str | None:
    if requested:
        return requested if requested in frame.columns else None
    lowered = {str(c).strip().lower(): c for c in frame.columns}
    for alias in SAMPLE_COLUMN_ALIASES:
        if alias in lowered:
            return lowered[alias]
    return None


def _distributions(frame: Any, sample_column: str) -> dict[str, Any]:
    """Percentiles per numeric column — the spread a bound would cut into."""
    import pandas as pd

    spread: dict[str, Any] = {}
    for column in frame.columns:
        if column == sample_column:
            continue
        values = pd.to_numeric(frame[column], errors="coerce").dropna()
        if values.empty:
            continue
        spread[str(column)] = {
            "percentiles": {str(p): round(float(values.quantile(p / 100)), 4) for p in PERCENTILES},
            "n_values": int(values.size),
        }
    return spread


def _preview(frame: Any, sample_column: str, spread: dict[str, Any]) -> dict[str, Any]:
    """Which samples each candidate bound would exclude, before any is chosen."""
    import pandas as pd

    preview: dict[str, Any] = {}
    for column, summary in spread.items():
        percentiles = summary["percentiles"]
        values = pd.to_numeric(frame[column], errors="coerce")
        rows = []
        for label in ("10", "25", "50"):
            bound = percentiles[label]
            failing = frame.loc[values < bound, sample_column].astype(str).tolist()
            rows.append({"min": bound, "at_percentile": int(label), "would_exclude": failing})
        preview[column] = rows
    return preview


def _evaluate(frame: Any, sample_column: str, thresholds: dict[str, Any]) -> dict[str, list[str]]:
    """`{sample: [reasons it was flagged]}` for the bounds the operator set."""
    import pandas as pd

    flags: dict[str, list[str]] = {}
    for column, bound in (thresholds or {}).items():
        if column not in frame.columns or not isinstance(bound, dict):
            continue
        values = pd.to_numeric(frame[column], errors="coerce")
        for kind, limit in bound.items():
            if limit is None or kind not in ("min", "max"):
                continue
            failing = values < limit if kind == "min" else values > limit
            for sample, value in zip(frame[sample_column].astype(str), values):
                index = frame.index[frame[sample_column].astype(str) == sample]
                if bool(failing.loc[index].any()):
                    flags.setdefault(sample, []).append(
                        f"{column}={value:g} fails {kind} {limit:g}"
                    )
    return flags


def _run_samples(payload: dict[str, Any]) -> dict[str, str]:
    """`{sample: path}` for the libraries this run actually has."""
    ingest = (payload.get("artifacts") or {}).get("ingest_validate") or {}
    paths = ingest.get("matrix_paths") or {}
    if paths:
        return {str(k): str(v) for k, v in paths.items()}
    return {str(name): "" for name in (ingest.get("fastq_layout") or {})}


def run(payload: dict[str, Any]) -> dict[str, Any]:
    config = payload.get("config") or {}
    warnings: list[str] = []
    notes: list[str] = []

    frame, problem = _load_table(payload)
    if problem:
        return _result(errors=[problem])
    known = _run_samples(payload)

    if frame is None or frame.empty:
        # Asked for explicitly, with nothing to act on. A note would be too
        # quiet: the operator believes triage happened.
        warnings.append(
            "sample QC triage was requested but no metrics table was supplied "
            "(config.qc_metrics_csv or sample_metadata); no sample was assessed"
        )
        return _result(triage_state="no_action", included_samples=sorted(known),
                       warnings=warnings)

    sample_column = _sample_column(frame, config.get("sample_column"))
    if sample_column is None:
        return _result(errors=[
            "cannot tell which column names the sample; set config.sample_column "
            f"(the table has: {', '.join(str(c) for c in frame.columns)})"
        ])

    listed = frame[sample_column].astype(str).tolist()
    duplicates = sorted({name for name in listed if listed.count(name) > 1})
    if duplicates:
        # Two rows under one name is how two libraries become one silently.
        return _result(errors=[
            f"the metrics table names {', '.join(duplicates)} more than once; "
            "a sample cannot be triaged under an identity it shares"
        ])

    # --- does the table describe this run at all? ---------------------------
    in_table, in_run = set(listed), set(known)
    if in_run:
        missing = sorted(in_run - in_table)
        extra = sorted(in_table - in_run)
        if missing:
            warnings.append(
                f"{len(missing)} librar(y/ies) in this run are absent from the metrics "
                f"table and were not assessed: {', '.join(missing)}"
            )
        if extra:
            warnings.append(
                f"{len(extra)} row(s) in the metrics table are not libraries in this "
                f"run: {', '.join(extra)}"
            )

    spread = _distributions(frame, sample_column)
    evidence = {
        "sample_column": sample_column,
        "n_rows": int(len(frame)),
        "columns": [str(c) for c in frame.columns],
        "distributions": spread,
        "preview": _preview(frame, sample_column, spread),
    }

    flags = _evaluate(frame, sample_column, config.get("sample_thresholds") or {})
    per_sample = {
        name: {"flagged": name in flags, "reasons": flags.get(name, [])}
        for name in sorted(in_table)
    }

    # --- act only on an explicit list ----------------------------------------
    requested = config.get("exclude_samples")
    if requested:
        excluded = sorted({str(s) for s in requested})
        unknown = [s for s in excluded if s not in in_table and s not in in_run]
        if unknown:
            return _result(errors=[
                f"exclude_samples names {', '.join(unknown)}, which is not a library "
                "in this run or a row in the metrics table"
            ], evidence=evidence)
        included = sorted(in_run - set(excluded)) if in_run else sorted(in_table - set(excluded))
        if not included:
            return _result(errors=[
                "excluding those samples would leave the run with nothing to analyse"
            ], evidence=evidence)
        notes.append(f"excluded {len(excluded)} of {len(in_run or in_table)} libraries by request")
        return _result(
            triage_state="applied",
            included_samples=included,
            excluded_samples=excluded,
            per_sample=per_sample,
            evidence=evidence,
            matrix_paths={name: path for name, path in known.items()
                          if name in included and path},
            warnings=warnings,
            notes=notes,
            metrics={"n_included": len(included), "n_excluded": len(excluded)},
        )

    if flags:
        warnings.append(
            f"{len(flags)} librar(y/ies) fail the bounds set for them "
            f"({', '.join(sorted(flags))}); nothing was excluded. Set "
            "config.exclude_samples to leave them out, or continue with them in"
        )
        state = "needs_review"
    else:
        state = "no_action"

    return _result(
        triage_state=state,
        included_samples=sorted(in_run or in_table),
        per_sample=per_sample,
        evidence=evidence,
        warnings=warnings,
        notes=notes,
        metrics={"n_included": len(in_run or in_table), "n_flagged": len(flags)},
    )


def _result(
    *,
    triage_state: str = "no_action",
    included_samples: list[str] | None = None,
    excluded_samples: list[str] | None = None,
    per_sample: dict[str, Any] | None = None,
    evidence: dict[str, Any] | None = None,
    matrix_paths: dict[str, str] | None = None,
    warnings: list[str] | None = None,
    notes: list[str] | None = None,
    errors: list[str] | None = None,
    metrics: dict[str, Any] | None = None,
) -> dict[str, Any]:
    return {
        "triage_state": triage_state,
        "included_samples": included_samples or [],
        "excluded_samples": excluded_samples or [],
        "per_sample": per_sample or {},
        "evidence": evidence or {},
        # Only set when samples were actually excluded, so downstream keeps
        # reading `ingest_validate` in the ordinary case.
        "matrix_paths": matrix_paths or {},
        "recommended_next_tool": None,
        "metrics": metrics or {},
        "notes": notes or [],
        "warnings": warnings or [],
        "errors": errors or [],
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=TOOL_NAME)
    parser.add_argument("--metrics", required=True, help="one row per library")
    parser.add_argument("--sample-column")
    parser.add_argument("--exclude", nargs="*", default=None)
    args = parser.parse_args(argv)

    config: dict[str, Any] = {"qc_metrics_csv": args.metrics}
    if args.sample_column:
        config["sample_column"] = args.sample_column
    if args.exclude:
        config["exclude_samples"] = args.exclude

    result = run({"config": config, "artifacts": {}, "sample_metadata": {}})
    print(json.dumps(result, indent=2, ensure_ascii=False, default=str))
    return 1 if result["errors"] else 0


if __name__ == "__main__":
    raise SystemExit(main())
