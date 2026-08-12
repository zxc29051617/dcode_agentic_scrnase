"""The study design, as data the pipeline can check rather than a name it parses.

Five things used to be one string. `ingest_validate` read a library name out of a
FASTQ filename, `cellranger_count` reused it as `library_id`, `merge_samples`
wrote it into `obs["sample"]`, and `run_integration` corrected on it. So which
library, which specimen, which subject, which biological group and which
technical batch were all the same value — and that value came from however the
files happened to be named.

This module is a table and a set of checks, the same shape as `species.py`: no
I/O beyond reading the file it is handed, no state, nothing that imports the
graph. Everything here is deterministic. A model may later be shown the summary
this produces, but nothing in this file asks one anything, and no verdict from
one can make an invalid manifest valid.

WHAT IT REFUSES TO DO. It never guesses. A blank cell is unknown, not a value to
fill from the row above or from the majority. A library whose id is not in the
manifest is an error, not a near-match to resolve. `PT001_disease_S1` is a
library id that happens to contain the word disease; the condition comes from
the manifest column or it does not exist.
"""

from __future__ import annotations

import csv
import hashlib
import io
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any

#: Bumped when the column contract changes in a way an older run could not be
#: read under. Recorded alongside the digest so a resume can tell "different
#: design" from "different schema".
SCHEMA_VERSION = "1"

#: One row per sequencing library. Every one of these must be present, though an
#: individual cell may be blank, which means unknown.
REQUIRED_COLUMNS: tuple[str, ...] = (
    "library_id",
    "sample_id",
    "donor_id",
    "condition",
    "technical_batch",
)

#: Accepted because they are documented, and biological unless stated otherwise.
#: None of them may become a batch key: `tissue` and `timepoint` in particular
#: often line up with when a library was built, which is exactly the confusion
#: `confounding()` exists to surface rather than to act on.
OPTIONAL_COLUMNS: tuple[str, ...] = (
    "tissue",
    "timepoint",
    "treatment",
    "sex",
    "biological_replicate",
)

#: The column contract is a whitelist, so an unrecognised name is refused rather
#: than carried. These are named separately only so the error can say *why* —
#: "this pipeline must not hold identifying data" reads differently from
#: "unknown column", and the person fixing the file needs the first one.
FORBIDDEN_COLUMNS: frozenset[str] = frozenset({
    "patient_name", "patient", "name", "full_name", "first_name", "last_name",
    "surname", "given_name", "mrn", "medical_record_number", "medical_record",
    "chart_number", "national_id", "id_number", "ssn", "nhi", "passport",
    "date_of_birth", "dob", "birth_date", "birthdate", "address", "phone",
    "telephone", "email", "initials",
})

#: A pseudonymous code: `D001`, `BATCH_A`, `LIB_003b`. Anything with a space in
#: it is refused, which is most of the way to refusing a person's name.
TOKEN = re.compile(r"^[A-Za-z0-9_.-]{1,64}$")

#: Value shapes that are identifying whatever column they arrive in. Checked
#: against the *value*, because a well-named column can still hold the wrong
#: thing. Deliberately narrow: `LIB001` and `S001` have to keep working.
_IDENTIFIER_SHAPES: tuple[tuple[re.Pattern[str], str], ...] = (
    (re.compile(r"\d{4}[-/]\d{1,2}[-/]\d{1,2}"), "looks like a date"),
    (re.compile(r"^[A-Za-z]\d{9}$"), "looks like an identity-card number"),
    (re.compile(r"\d{8,}"), "contains a long run of digits"),
)


@dataclass(frozen=True)
class Manifest:
    """A validated study design, normalized so equal designs compare equal."""

    rows: tuple[dict[str, str | None], ...]
    columns: tuple[str, ...]
    sha256: str
    schema_version: str = SCHEMA_VERSION

    @property
    def library_ids(self) -> tuple[str, ...]:
        return tuple(row["library_id"] for row in self.rows)  # type: ignore[misc]

    def column(self, name: str) -> dict[str, str | None]:
        """`{library_id: value}` for one column; missing values stay None."""
        return {row["library_id"]: row.get(name) for row in self.rows}  # type: ignore[index]


def _clean(value: Any) -> str | None:
    text = "" if value is None else str(value).strip()
    return text or None


def _check_columns(header: list[str]) -> list[str]:
    errors: list[str] = []
    seen = [c.strip().lower() for c in header]

    duplicated = sorted({c for c in seen if seen.count(c) > 1})
    for name in duplicated:
        errors.append(f"column {name!r} appears more than once")

    for name in seen:
        if name in FORBIDDEN_COLUMNS:
            errors.append(
                f"column {name!r} is a direct identifier; this pipeline stores "
                f"pseudonymous codes only (D001, S001), never identifying data"
            )
        elif name not in REQUIRED_COLUMNS and name not in OPTIONAL_COLUMNS:
            errors.append(
                f"column {name!r} is not part of the manifest contract; the "
                f"accepted columns are {', '.join(REQUIRED_COLUMNS + OPTIONAL_COLUMNS)}"
            )

    for name in REQUIRED_COLUMNS:
        if name not in seen:
            errors.append(f"required column {name!r} is missing")
    return errors


def _check_value(column: str, value: str | None, line: int) -> str | None:
    """Why this cell cannot be stored — never quoting the cell back."""
    if value is None:
        return None
    if not TOKEN.match(value):
        return (
            f"row {line}: {column} is not a pseudonymous code (letters, digits, "
            f"'_', '.', '-', no spaces). Its value is not repeated here on purpose"
        )
    for pattern, why in _IDENTIFIER_SHAPES:
        if pattern.search(value):
            return (
                f"row {line}: {column} {why}, so it is refused as possibly "
                f"identifying. Use a pseudonymous code such as D001"
            )
    return None


def parse_manifest(text: str, *, source: str = "manifest") -> tuple[Manifest | None, list[str]]:
    """Validate CSV text into a `Manifest`, or return every reason it is not one.

    All errors are collected rather than raised one at a time: someone fixing a
    manifest should see the whole list, not discover the next problem on the
    next run.
    """
    reader = csv.reader(io.StringIO(text))
    try:
        rows = [row for row in reader if any(cell.strip() for cell in row)]
    except csv.Error as exc:
        return None, [f"{source}: cannot be read as CSV: {exc}"]

    if not rows:
        return None, [f"{source}: is empty"]

    header = [cell.strip() for cell in rows[0]]
    errors = _check_columns(header)
    if errors:
        return None, [f"{source}: {e}" for e in errors]

    lowered = [c.strip().lower() for c in header]
    parsed: list[dict[str, str | None]] = []
    seen_ids: dict[str, int] = {}

    for line, raw in enumerate(rows[1:], start=2):
        if len(raw) != len(lowered):
            errors.append(
                f"row {line}: has {len(raw)} field(s) but the header declares {len(lowered)}"
            )
            continue
        record = {name: _clean(cell) for name, cell in zip(lowered, raw)}

        for name, value in record.items():
            problem = _check_value(name, value, line)
            if problem:
                errors.append(problem)

        library_id = record.get("library_id")
        if not library_id:
            errors.append(f"row {line}: library_id is blank, so this row matches nothing")
        elif library_id in seen_ids:
            errors.append(
                f"row {line}: library_id {library_id!r} was already used on row "
                f"{seen_ids[library_id]}; a library id has to identify one library"
            )
        else:
            seen_ids[library_id] = line
            parsed.append(record)

    if errors:
        return None, [f"{source}: {e}" for e in errors]
    if not parsed:
        return None, [f"{source}: has a header but no libraries"]

    columns = tuple(c for c in REQUIRED_COLUMNS + OPTIONAL_COLUMNS if c in lowered)
    ordered = tuple(sorted(parsed, key=lambda r: str(r["library_id"])))
    digest = hashlib.sha256(
        _csv_text(ordered, columns).encode("utf-8")
    ).hexdigest()
    return Manifest(rows=ordered, columns=columns, sha256=digest), []


def load_manifest(path: Any) -> tuple[Manifest | None, list[str]]:
    """Read and validate a manifest file. A missing file is an error, not None."""
    target = Path(str(path)).expanduser()
    if not target.is_file():
        return None, [f"sample manifest does not exist: {target}"]
    try:
        text = target.read_text(encoding="utf-8")
    except Exception as exc:  # noqa: BLE001 - an unreadable manifest is the finding
        return None, [f"cannot read {target}: {type(exc).__name__}: {exc}"]
    return parse_manifest(text, source=str(target))


def _csv_text(rows: tuple[dict[str, str | None], ...], columns: tuple[str, ...]) -> str:
    buffer = io.StringIO()
    writer = csv.writer(buffer, lineterminator="\n")
    writer.writerow(columns)
    for row in rows:
        writer.writerow(["" if row.get(c) is None else row[c] for c in columns])
    return buffer.getvalue()


def normalized_csv(parsed: Manifest) -> str:
    """The manifest as it is stored and hashed: sorted, trimmed, canonical order.

    This is what the digest is taken over, so reordering rows or padding a cell
    with spaces is not a change to the study design, while changing a condition
    is.
    """
    return _csv_text(parsed.rows, parsed.columns)


def match_libraries(parsed: Manifest, found: Any) -> list[str]:
    """Every reason the manifest and the libraries on disk are not the same set.

    Exact string equality, both directions. No case folding, no prefix matching,
    no edit distance — a manifest that nearly matches is a manifest that has not
    been checked, and quietly resolving the difference is how one sample ends up
    described by another sample's row.
    """
    actual = [str(name) for name in found]
    declared = set(parsed.library_ids)
    present = set(actual)
    errors: list[str] = []

    duplicated = sorted({name for name in actual if actual.count(name) > 1})
    if duplicated:
        errors.append(
            f"the run found the same library id more than once: {', '.join(duplicated)}"
        )

    unmatched = sorted(present - declared)
    if unmatched:
        errors.append(
            f"{len(unmatched)} library/libraries have no manifest row: "
            f"{', '.join(unmatched)} (the manifest declares "
            f"{', '.join(sorted(declared))})"
        )

    unused = sorted(declared - present)
    if unused:
        errors.append(
            f"{len(unused)} manifest row(s) match no library the run found: "
            f"{', '.join(unused)}"
        )
    return errors


def _components(pairs: set[tuple[str, str]]) -> int:
    """Connected components of the biological × technical bipartite graph.

    This is the identifiability question asked structurally. Put each condition
    and each batch on its own side, join them when some library has both. If the
    graph is connected, a batch difference can be told apart from a condition
    difference, because some batch holds more than one condition to compare
    within. If it falls into separate pieces, no comparison bridges them: the
    two effects enter the model the same way and removing one removes the other.

    A count, not a coefficient. There is no threshold to tune and nothing to
    calibrate, which is the point — a tuned cutoff would be a judgement about
    someone's experiment dressed up as a measurement.
    """
    parent: dict[str, str] = {}

    def find(node: str) -> str:
        parent.setdefault(node, node)
        while parent[node] != node:
            parent[node] = parent[parent[node]]
            node = parent[node]
        return node

    def union(a: str, b: str) -> None:
        ra, rb = find(a), find(b)
        if ra != rb:
            parent[ra] = rb

    for condition, batch in pairs:
        union(f"c:{condition}", f"b:{batch}")
    return len({find(node) for node in parent})


def confounding_from_columns(
    bio: dict[str, str | None],
    tech: dict[str, str | None],
    *,
    biological: str = "condition",
    technical: str = "technical_batch",
) -> dict[str, Any]:
    """The same check from two `{library_id: value}` mappings.

    Split out because `run_integration` asks this question of an AnnData object
    rather than of a manifest — the design has to be checked against what is
    actually in the object being corrected, not only against the file that
    described it.
    """
    pairs: set[tuple[str, str]] = set()
    table: dict[str, dict[str, int]] = {}
    unknown_bio = 0
    unknown_tech = 0

    for library_id in sorted(set(bio) | set(tech)):
        b, t = bio.get(library_id), tech.get(library_id)
        if b is None:
            unknown_bio += 1
            continue
        if t is None:
            unknown_tech += 1
            continue
        pairs.add((b, t))
        table.setdefault(b, {})
        table[b][t] = table[b].get(t, 0) + 1

    batches = sorted({t for _, t in pairs})
    conditions = sorted(table)
    for condition in conditions:
        for batch in batches:
            table[condition].setdefault(batch, 0)

    n_components = _components(pairs) if pairs else 0
    counts = [table[c][b] for c in conditions for b in batches]

    return {
        "biological_key": biological,
        "technical_key": technical,
        "n_conditions": len(conditions),
        "n_batches": len(batches),
        "n_components": n_components,
        # One component means every part of the design is linked to every other
        # by some shared batch or condition, so the effects are estimable apart.
        "separable": n_components <= 1,
        "fully_confounded": n_components > 1,
        "balanced": bool(counts) and len(set(counts)) == 1,
        "table": table,
        "n_unknown_condition": unknown_bio,
        "n_unknown_technical_batch": unknown_tech,
    }


def confounding(
    parsed: Manifest, biological: str = "condition", technical: str = "technical_batch"
) -> dict[str, Any]:
    """Whether the technical effect can be separated from the biological one.

    Returns counts only — no library, sample or donor id appears anywhere in the
    result, because this is the part that reaches the report and the run
    metadata.

    A blank `condition` is not a group. Those libraries are counted and named as
    unknown rather than pooled into a category that nobody wrote down.
    """
    return confounding_from_columns(
        parsed.column(biological), parsed.column(technical),
        biological=biological, technical=technical,
    )


def design_state(parsed: Manifest) -> dict[str, Any]:
    """The manifest in the shape `WorkflowState.study_design` carries.

    Both halves travel together on purpose. `by_library` is what `merge_samples`
    joins onto the cells and stays inside the run; `summary` is the aggregate
    that may be written to run metadata and shown in a report. Keeping them one
    field with two clearly different scopes is what stops a row-level value
    reaching a shareable file because somebody reached for the nearest dict.

    `snapshot` is the immutable copy of the design this run was started with. A
    checkpointed run resumes from it rather than re-reading a CSV that may have
    changed underneath, so one run can never mix two designs.
    """
    return {
        "by_library": {
            str(row["library_id"]): {
                column: row.get(column) for column in parsed.columns
                if column != "library_id"
            }
            for row in parsed.rows
        },
        "summary": public_summary(parsed),
        "snapshot": normalized_csv(parsed),
        "sha256": parsed.sha256,
        "schema_version": parsed.schema_version,
    }


def public_summary(parsed: Manifest) -> dict[str, Any]:
    """What may leave the run directory: shapes and a digest, never rows.

    Deliberately excludes the values themselves. In a study with a handful of
    subjects, a donor-to-condition listing identifies people even though every
    code in it is pseudonymous, so the report and the run metadata get counts.
    """
    def distinct(column: str) -> int:
        return len({v for v in parsed.column(column).values() if v is not None})

    return {
        "schema_version": parsed.schema_version,
        "sha256": parsed.sha256,
        "columns": list(parsed.columns),
        "n_libraries": len(parsed.rows),
        "n_samples": distinct("sample_id"),
        "n_donors": distinct("donor_id"),
        "n_conditions": distinct("condition"),
        "n_technical_batches": distinct("technical_batch"),
    }
