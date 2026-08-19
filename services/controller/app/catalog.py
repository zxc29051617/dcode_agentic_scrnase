"""Turning something a person typed into a reference the worker may act on.

Every path in an analysis request arrives as untrusted text. It may have come
from a model that hallucinated it, from a browser field, or from a person who
meant well and typed `../../etc`. None of those is distinguishable from the
others by the time it reaches here, so none is trusted differently.

The rule is that **the worker never receives a path the caller supplied**. It
receives an `input_ref`, and only this module knows what a ref resolves to. Two
kinds exist:

    dataset:pbmc_1k_v3      a named entry in the server-side catalog file
    local:<16 hex>          a path that was validated against the allowlist and
                            recorded server-side; the token is the handle

The second exists because "the data is in data/counted/pbmc_1k_v3/outs" is a
sentence people actually say to an intake assistant, and refusing it outright
would push them to a worse workaround. What it is *not* is a path the request
then carries: it is checked once, here, and replaced with a token that means
nothing outside this service's own store.

## What "validated" means

Resolution is `Path.resolve()`, which collapses `..` and follows symlinks, and
the result must sit under one of the configured roots. That ordering is the
whole guarantee: a symlink inside an allowed root pointing outside it resolves
to its target and is then rejected by the containment check, so an escape has
to be a real path under a real root to survive. `Path.is_relative_to` is used
rather than string prefix matching, which says `/data/private` is inside
`/data/priv`.

A ref that resolves to nothing, or to a file type no route can read, is a
validation error and never reaches a worker.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any

#: Suffixes `ingest_validate` can route on, and directories are always allowed
#: (an MTX triplet or a Cell Ranger `outs/` is a directory). This is a coarse
#: gate — it stops a request naming a `.pdf` or a `.sqlite` — and deliberately
#: not a second detector. What the data *is* stays `ingest_validate`'s answer.
READABLE_SUFFIXES = (".h5", ".h5ad", ".mtx", ".gz", ".csv", ".tsv", ".fastq", ".fq")

DATASET_PREFIX = "dataset:"
LOCAL_PREFIX = "local:"
MANIFEST_PREFIX = "manifest:"

#: What a relative path in the catalog file is relative *to*.
#:
#: Not the current working directory, which is where this would otherwise
#: resolve — and which is `services/controller/` when the service is started
#: the documented way, so `"data/counted/..."` would silently point at
#: `services/controller/data/counted/...`, not exist, and the entry would
#: vanish from the catalog with no explanation. A person writing that path
#: means it relative to the project, so that is what it is resolved against.
REPO_ROOT = Path(__file__).resolve().parents[3]


@dataclass(frozen=True)
class CatalogEntry:
    """One dataset a request may name, as the browser is allowed to see it."""

    name: str
    input_ref: str
    display_name: str
    kind: str
    """What the catalog author says this is: `fastq`, `matrix`, `h5`, `h5ad`.

    A hint for the intake conversation and nothing more. `ingest_validate`
    detects the route from the filesystem on every run and its answer wins; this
    field never reaches the executor's config.
    """

    species_hint: str | None
    description: str | None
    path: Path
    """Server-side only. Never projected into an API response."""


@dataclass(frozen=True)
class ManifestEntry:
    name: str
    study_design_ref: str
    display_name: str
    description: str | None
    path: Path


class RefError(ValueError):
    """A reference that cannot be resolved, with a sentence for the operator."""


def _digest(path: Path) -> str:
    return hashlib.sha256(str(path).encode("utf-8")).hexdigest()[:16]


def _load_catalog_file(catalog_path: Path | None) -> dict[str, Any]:
    if catalog_path is None or not catalog_path.is_file():
        return {}
    try:
        loaded = json.loads(catalog_path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        raise RefError(f"the dataset catalog at {catalog_path.name} could not be read: {exc}") from exc
    return loaded if isinstance(loaded, dict) else {}


def _resolve_entry(raw_path: str) -> Path:
    """A catalog entry's path, absolute.

    An absolute path is taken as written. A relative one is resolved against
    the repository root rather than the process's working directory — see
    `REPO_ROOT`. Either way the result still has to pass the allowlist; this
    only decides what the text meant, not whether it is permitted.
    """
    candidate = Path(raw_path).expanduser()
    if not candidate.is_absolute():
        candidate = REPO_ROOT / candidate
    return candidate.resolve()


def _contained(candidate: Path, roots: tuple[Path, ...]) -> bool:
    """Is `candidate`, already resolved, inside any allowlisted root?"""
    for root in roots:
        try:
            if candidate.is_relative_to(root):
                return True
        except (ValueError, OSError):
            continue
    return False


class Catalog:
    """The allowlist, loaded per request so an edited catalog takes effect.

    Not cached across requests on purpose. The gateway rebuilds everything it
    serves per request for the same reason: a cache is a second copy of the
    truth, and this one decides what a worker is permitted to open.
    """

    def __init__(self, *, catalog_path: Path | None, data_roots: tuple[Path, ...]) -> None:
        self.data_roots = data_roots
        raw = _load_catalog_file(catalog_path)
        self.datasets: dict[str, CatalogEntry] = {}
        self.manifests: dict[str, ManifestEntry] = {}
        #: Entries the catalog offered and this object refused, with the reason.
        #: A dropped entry used to be silent, which meant a mistyped path
        #: produced an empty dataset list and no way to find out why — the
        #: single most likely thing to go wrong when setting this up.
        self.rejected: list[dict[str, str]] = []

        for name, entry in (raw.get("datasets") or {}).items():
            if not isinstance(entry, dict) or not entry.get("path"):
                self.rejected.append({"name": name, "reason": "the entry has no 'path'"})
                continue
            resolved = _resolve_entry(str(entry["path"]))
            # A catalog entry is not exempt from the allowlist. The catalog says
            # what is offered; the roots say what is reachable, and an entry
            # outside them is a misconfiguration rather than a widening.
            if data_roots and not _contained(resolved, data_roots):
                self.rejected.append({
                    "name": name,
                    "reason": "its path is outside every directory in CONTROLLER_DATA_ROOTS",
                })
                continue
            if not resolved.exists():
                self.rejected.append({
                    "name": name,
                    "reason": "there is nothing at that path on this machine",
                })
                continue
            self.datasets[name] = CatalogEntry(
                name=name,
                input_ref=f"{DATASET_PREFIX}{name}",
                display_name=str(entry.get("display_name") or name),
                kind=str(entry.get("kind") or "unknown"),
                species_hint=(str(entry["species"]) if entry.get("species") else None),
                description=(str(entry["description"]) if entry.get("description") else None),
                path=resolved,
            )

        for name, entry in (raw.get("manifests") or {}).items():
            if not isinstance(entry, dict) or not entry.get("path"):
                continue
            resolved = _resolve_entry(str(entry["path"]))
            if data_roots and not _contained(resolved, data_roots):
                self.rejected.append({
                    "name": f"manifest:{name}",
                    "reason": "its path is outside every directory in CONTROLLER_DATA_ROOTS",
                })
                continue
            if not resolved.is_file():
                self.rejected.append({
                    "name": f"manifest:{name}",
                    "reason": "there is no file at that path on this machine",
                })
                continue
            self.manifests[name] = ManifestEntry(
                name=name,
                study_design_ref=f"{MANIFEST_PREFIX}{name}",
                display_name=str(entry.get("display_name") or name),
                description=(str(entry["description"]) if entry.get("description") else None),
                path=resolved,
            )

    # --- projection ---------------------------------------------------------

    def list_datasets(self) -> list[dict[str, Any]]:
        """What an intake action may show. No absolute path appears here."""
        return [
            {
                "input_ref": entry.input_ref,
                "display_name": entry.display_name,
                "kind": entry.kind,
                "species_hint": entry.species_hint,
                "description": entry.description,
            }
            for entry in sorted(self.datasets.values(), key=lambda e: e.name)
        ]

    def list_manifests(self) -> list[dict[str, Any]]:
        return [
            {
                "study_design_ref": entry.study_design_ref,
                "display_name": entry.display_name,
                "description": entry.description,
            }
            for entry in sorted(self.manifests.values(), key=lambda e: e.name)
        ]

    # --- resolution ---------------------------------------------------------

    def resolve_input_ref(self, input_ref: str, *, known_local: dict[str, str]) -> Path:
        """The directory or file a validated `input_ref` names.

        `known_local` is the store's record of previously admitted paths. A
        `local:` token that is not in it resolves to nothing — a token invented
        by a caller is not a key to the filesystem.
        """
        text = str(input_ref or "").strip()
        if text.startswith(DATASET_PREFIX):
            entry = self.datasets.get(text[len(DATASET_PREFIX):])
            if entry is None:
                raise RefError(f"{text!r} is not a dataset this server offers")
            return entry.path
        if text.startswith(LOCAL_PREFIX):
            recorded = known_local.get(text)
            if not recorded:
                raise RefError(f"{text!r} is not a reference this server issued")
            # Re-checked on use, not only on admission: the allowlist may have
            # been narrowed, or the path replaced by a symlink, since the token
            # was minted. A token is a handle, never a standing permission.
            return self.admit_path(recorded).path
        raise RefError(
            f"{text!r} is not a usable input reference. Expected "
            f"'{DATASET_PREFIX}<name>' or a path inside an allowed data root."
        )

    def resolve_manifest_ref(self, study_design_ref: str) -> Path:
        text = str(study_design_ref or "").strip()
        if not text.startswith(MANIFEST_PREFIX):
            raise RefError(
                f"{text!r} is not a study design reference. Expected "
                f"'{MANIFEST_PREFIX}<name>' — a manifest path cannot be supplied directly."
            )
        entry = self.manifests.get(text[len(MANIFEST_PREFIX):])
        if entry is None:
            raise RefError(f"{text!r} is not a study design this server offers")
        return entry.path

    def admit_path(self, raw_path: str) -> CatalogEntry:
        """Check a caller-supplied path and mint the reference that replaces it.

        Everything a path can be wrong about is checked here, because after here
        it is a token and nobody looks at it again: outside the allowlist, not
        present, a file type no route reads, or no allowlist configured at all —
        which is refused rather than treated as "everything is allowed".
        """
        text = str(raw_path or "").strip()
        if not text:
            raise RefError("no path was given")
        if not self.data_roots:
            raise RefError(
                "this server has no allowed data roots configured, so a path cannot be "
                "accepted. Name a dataset from the catalog instead."
            )
        try:
            resolved = Path(text).expanduser().resolve()
        except (OSError, RuntimeError) as exc:
            raise RefError(f"{text!r} is not a usable path: {exc}") from exc

        if not _contained(resolved, self.data_roots):
            # The roots themselves are not named in the message. A caller
            # probing the filesystem learns only that this one was refused.
            raise RefError(
                "that path is outside the directories this server is allowed to read. "
                "Name a dataset from the catalog, or ask an administrator to allow the location."
            )
        if not resolved.exists():
            raise RefError("there is nothing at that path on the analysis machine")
        if resolved.is_file() and resolved.suffix.lower() not in READABLE_SUFFIXES:
            raise RefError(
                f"{resolved.name} is not a file type this pipeline reads "
                f"(expected one of: {', '.join(READABLE_SUFFIXES)}, or a directory)"
            )

        # A path that happens to be a catalog entry comes back as that entry,
        # so the two spellings of the same data produce one reference and one
        # config digest rather than two requests that look different.
        for entry in self.datasets.values():
            if entry.path == resolved:
                return entry

        return CatalogEntry(
            name=_digest(resolved),
            input_ref=f"{LOCAL_PREFIX}{_digest(resolved)}",
            display_name=resolved.name,
            kind="unknown",
            species_hint=None,
            description=None,
            path=resolved,
        )
