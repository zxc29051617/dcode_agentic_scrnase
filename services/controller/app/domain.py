"""The analysis request, its lifecycle, and the mapping to executor config.

The public field names in here are the contract. They are the same strings in
`schemas/analysis_request.schema.json`, in the controller's responses, in the
web app's types and in the intake conversation, because a field that is called
one thing in three places is three chances for them to disagree about which one
is authoritative.

## Why the request schema is not the executor's config

`analysis.embedding_method` becomes `config["method"]`. `analysis.resolution`
becomes `config["resolution"]`, unchanged. The names differ where the public
one is clearer and match where it is not, and `to_executor_config` is the one
place that knows which is which.

The point of the split is not naming. It is that a request is an *allowlist*:
`to_executor_config` can only emit keys it has an explicit rule for, so no
amount of extra JSON in a request body reaches the executor. A request schema
that was passed straight through as config would make every documented CLI flag
— and every undocumented config key any skill happens to read — settable by
whoever can post a request.
"""

from __future__ import annotations

import hashlib
import json
from datetime import datetime, timezone
from typing import Any

#: Every state a request can be in. `draft` and `validated` are internal
#: bookkeeping around one preview; `awaiting_confirmation` is the only state a
#: confirm is accepted from; `rejected` is a request that failed validation
#: badly enough that re-previewing is the way forward.
STATUSES: tuple[str, ...] = (
    "draft",
    "validated",
    "awaiting_confirmation",
    "queued",
    "running",
    "needs_review",
    "completed",
    "failed",
    "cancelled",
    "rejected",
)

#: Statuses that mean the scientific side is finished with this request, so a
#: worker must not pick it up again and a gate decision has nothing to answer.
TERMINAL_STATUSES: frozenset[str] = frozenset({"completed", "failed", "cancelled", "rejected"})

#: The `analysis` block's allowlist: public name -> executor config key.
#: A public name absent from here cannot reach the executor at all.
ANALYSIS_TO_CONFIG: dict[str, str] = {
    "embedding_method": "method",
    "embedding_dimensions": "dimensions",
    "embedding_max_cells": "embedding_max_cells",
    "integration_mode": "integration_mode",
    "resolution": "resolution",
    "celltypist_model": "celltypist_model",
    "scmayomap_tissue": "scmayomap_tissue",
    "random_state": "random_state",
    "min_genes": "min_genes",
    "min_counts": "min_counts",
    "max_pct_mito": "max_pct_mito",
    "remove_doublets": "remove_doublets",
}

#: Public switches that describe *whether a step happens*, which this pipeline
#: does not make optional. Both steps are on `registry.MAINLINE` and every route
#: that finishes runs them. `true` is therefore a description of what will
#: happen; `false` is a request the executor cannot honour, and is reported as
#: unsupported rather than accepted and ignored.
ALWAYS_ON_SWITCHES: dict[str, str] = {
    "annotation": "annotate_cells",
    "doublet_detection": "detect_doublets",
}

#: Analyses people ask for that this workflow has no step for. Named so the
#: intake can say "not supported, and here is what is" instead of producing a
#: plausible request that silently omits what was actually wanted.
UNSUPPORTED_ANALYSES: dict[str, str] = {
    "trajectory": "trajectory inference / pseudotime",
    "pseudotime": "trajectory inference / pseudotime",
    "rna_velocity": "RNA velocity",
    "velocity": "RNA velocity",
    "differential_expression": "differential expression between conditions",
    "de": "differential expression between conditions",
    "cell_cell_communication": "cell-cell communication / ligand-receptor",
    "cnv": "copy-number inference",
}

EMBEDDING_METHODS = ("umap", "tsne", "both")
INTEGRATION_MODES = ("none", "harmony")


def utcnow() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def config_digest(payload: dict[str, Any]) -> str:
    """A stable digest over everything that decides what will be executed.

    Confirmation carries this value back, and a mismatch is refused. That is
    what stops a draft being previewed, edited in another tab, and confirmed
    under the digest of the version somebody read.

    `sort_keys` makes it independent of dict ordering, which JSON round-trips
    through a browser do not preserve.
    """
    body = json.dumps(payload, sort_keys=True, ensure_ascii=False, separators=(",", ":"))
    return "sha256:" + hashlib.sha256(body.encode("utf-8")).hexdigest()


def digest_source(request: dict[str, Any]) -> dict[str, Any]:
    """The subset of a request the digest is taken over.

    Deliberately not the whole record: `updated_at`, `status` and the request id
    move for reasons that do not change what would be computed, and including
    them would make every re-preview a "stale digest".
    """
    return {
        "input_ref": request.get("input_ref"),
        "species": request.get("species"),
        "study_design_ref": request.get("study_design_ref"),
        "analysis": request.get("analysis") or {},
        "research_question": request.get("research_question"),
        "project": request.get("project"),
    }


def to_executor_config(analysis: dict[str, Any], *, species: str) -> dict[str, Any]:
    """The executor config an accepted request becomes.

    Only keys in `ANALYSIS_TO_CONFIG` are emitted, and a `None` is dropped
    rather than passed on — `src/run.py` builds its config the same way, for the
    same reason: several skills read `config.get(key, DEFAULT)`, which returns
    the explicit `None` instead of the default and then fails converting it.
    """
    config: dict[str, Any] = {"species": species}
    for public, key in ANALYSIS_TO_CONFIG.items():
        value = analysis.get(public)
        if value is None:
            continue
        config[key] = value
    return config
