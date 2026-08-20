"""The controller's own durable state: requests, jobs, and admitted paths.

SQLite and nothing else — no ORM, no driver, no server — so this module imports
in both environments that need it: the API's own venv, and the scientific
environment the worker runs in. A shared queue that only one of the two could
import would have to be reached over HTTP, and a worker that learns what to do
over HTTP loses its job record the moment the request times out.

Local-development MVP. A production deployment replaces this with Postgres and
a real queue; what it must not replace is the two invariants the schema encodes,
because they are what stop one confirmation becoming two analyses:

**One start job per request.** `UNIQUE(request_id, kind)` with `kind='start'`.
A second confirm of the same request finds the row already there and returns it,
which is what makes confirmation idempotent rather than merely fast.

**One continuation per gate generation.** `UNIQUE(scientific_run_id, generation)`
on gate jobs. A decision carries the generation it was made against; a second
decision for a generation already answered is a duplicate, and a decision for an
older generation is stale. Both are refused by the same index.
"""

from __future__ import annotations

import json
import sqlite3
from pathlib import Path
from typing import Any

from .domain import utcnow

SCHEMA = """
CREATE TABLE IF NOT EXISTS analysis_requests (
    request_id        TEXT PRIMARY KEY,
    conversation_id   TEXT,
    document          TEXT NOT NULL,
    status            TEXT NOT NULL,
    config_digest     TEXT,
    scientific_run_id TEXT,
    created_at        TEXT NOT NULL,
    updated_at        TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS jobs (
    job_id            TEXT PRIMARY KEY,
    request_id        TEXT NOT NULL,
    kind              TEXT NOT NULL,
    status            TEXT NOT NULL,
    scientific_run_id TEXT,
    generation        INTEGER,
    payload           TEXT NOT NULL,
    error             TEXT,
    created_at        TEXT NOT NULL,
    updated_at        TEXT NOT NULL
);

-- One start per request: this is the idempotency of confirm, in the schema
-- rather than in a handler that could be raced by two clicks.
CREATE UNIQUE INDEX IF NOT EXISTS jobs_one_start_per_request
    ON jobs (request_id) WHERE kind = 'start';

-- One continuation per pending gate. A stale or repeated decision collides here.
CREATE UNIQUE INDEX IF NOT EXISTS jobs_one_continue_per_generation
    ON jobs (scientific_run_id, generation) WHERE kind = 'continue';

CREATE INDEX IF NOT EXISTS jobs_queue ON jobs (status, created_at);

-- Paths admitted by the allowlist, and the opaque token that replaced each.
-- The token is what travels; this table is the only thing that can turn one
-- back into a path, and it lives server-side.
CREATE TABLE IF NOT EXISTS admitted_paths (
    input_ref     TEXT PRIMARY KEY,
    absolute_path TEXT NOT NULL,
    created_at    TEXT NOT NULL
);

-- Every gate decision an operator submitted, whether or not it was applied.
-- Separate from `jobs` because a refused decision has no job and still has to
-- be answerable for.
CREATE TABLE IF NOT EXISTS gate_decisions (
    decision_id       TEXT PRIMARY KEY,
    scientific_run_id TEXT NOT NULL,
    gate_id           TEXT NOT NULL,
    generation        INTEGER NOT NULL,
    decision          TEXT NOT NULL,
    rationale         TEXT,
    overrides         TEXT NOT NULL,
    operator_id       TEXT NOT NULL,
    outcome           TEXT NOT NULL,
    created_at        TEXT NOT NULL
);
"""


def connect(db_path: str | Path) -> sqlite3.Connection:
    path = Path(db_path)
    path.parent.mkdir(parents=True, exist_ok=True)
    connection = sqlite3.connect(str(path), timeout=30.0, isolation_level=None)
    connection.row_factory = sqlite3.Row
    # WAL so the worker polling and the API writing do not lock each other out;
    # the two are separate processes by design.
    connection.execute("PRAGMA journal_mode=WAL")
    connection.execute("PRAGMA foreign_keys=ON")
    connection.executescript(SCHEMA)
    return connection


class Store:
    """Every read and write the controller and the worker make, in one place."""

    def __init__(self, db_path: str | Path) -> None:
        self.db_path = str(db_path)
        self.connection = connect(db_path)

    def close(self) -> None:
        try:
            self.connection.close()
        except sqlite3.Error:
            pass

    # --- requests -----------------------------------------------------------

    def put_request(self, document: dict[str, Any]) -> None:
        now = utcnow()
        self.connection.execute(
            """
            INSERT INTO analysis_requests
                (request_id, conversation_id, document, status, config_digest,
                 scientific_run_id, created_at, updated_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(request_id) DO UPDATE SET
                conversation_id   = excluded.conversation_id,
                document          = excluded.document,
                status            = excluded.status,
                config_digest     = excluded.config_digest,
                scientific_run_id = COALESCE(excluded.scientific_run_id,
                                             analysis_requests.scientific_run_id),
                updated_at        = excluded.updated_at
            """,
            (
                document["request_id"],
                document.get("conversation_id"),
                json.dumps(document, ensure_ascii=False),
                document["status"],
                document.get("config_digest"),
                document.get("scientific_run_id"),
                document.get("created_at") or now,
                now,
            ),
        )

    def get_request(self, request_id: str) -> dict[str, Any] | None:
        row = self.connection.execute(
            "SELECT document FROM analysis_requests WHERE request_id = ?", (request_id,)
        ).fetchone()
        return json.loads(row["document"]) if row else None

    def request_for_run(self, scientific_run_id: str) -> dict[str, Any] | None:
        row = self.connection.execute(
            "SELECT document FROM analysis_requests WHERE scientific_run_id = ?",
            (scientific_run_id,),
        ).fetchone()
        return json.loads(row["document"]) if row else None

    def latest_job_for_run(self, scientific_run_id: str) -> dict[str, Any] | None:
        """The most recently updated job that touched this run, or None.

        This is what a `needs_review` gate closing and the run going quiet
        with no report actually means: `gate_state` alone cannot distinguish
        "still working" from "the executor refused to proceed and said why" —
        both look like "no pending gate, no report" from the audit log. The
        reason lives here, in `error`, because it is the worker's own account
        of what happened, not an inference from files it wrote.
        """
        row = self.connection.execute(
            # `rowid DESC` breaks ties `updated_at` cannot: two jobs finished in
            # the same second sort equally by timestamp, and insertion order —
            # which rowid gives for free — is what actually happened second.
            "SELECT job_id, kind, status, scientific_run_id, error, updated_at "
            "FROM jobs WHERE scientific_run_id = ? ORDER BY updated_at DESC, rowid DESC LIMIT 1",
            (scientific_run_id,),
        ).fetchone()
        return dict(row) if row else None

    def set_request_status(
        self, request_id: str, status: str, *, scientific_run_id: str | None = None
    ) -> dict[str, Any] | None:
        document = self.get_request(request_id)
        if document is None:
            return None
        document["status"] = status
        document["updated_at"] = utcnow()
        if scientific_run_id:
            document["scientific_run_id"] = scientific_run_id
        self.put_request(document)
        return document

    # --- admitted paths -----------------------------------------------------

    def admit_path(self, input_ref: str, absolute_path: str) -> None:
        self.connection.execute(
            "INSERT OR IGNORE INTO admitted_paths (input_ref, absolute_path, created_at) "
            "VALUES (?, ?, ?)",
            (input_ref, absolute_path, utcnow()),
        )

    def known_local(self) -> dict[str, str]:
        return {
            row["input_ref"]: row["absolute_path"]
            for row in self.connection.execute(
                "SELECT input_ref, absolute_path FROM admitted_paths"
            )
        }

    # --- jobs ---------------------------------------------------------------

    def enqueue_start(self, *, job_id: str, request_id: str, scientific_run_id: str,
                      payload: dict[str, Any]) -> dict[str, Any]:
        """Queue the one start job this request may ever have.

        A second call returns the existing row untouched. That is the whole of
        confirm idempotency: two clicks, two tabs, or a retried POST all reach
        here and all leave with the same job.
        """
        existing = self.start_job(request_id)
        if existing is not None:
            return existing
        now = utcnow()
        try:
            self.connection.execute(
                "INSERT INTO jobs (job_id, request_id, kind, status, scientific_run_id, "
                "generation, payload, error, created_at, updated_at) "
                "VALUES (?, ?, 'start', 'queued', ?, NULL, ?, NULL, ?, ?)",
                (job_id, request_id, scientific_run_id,
                 json.dumps(payload, ensure_ascii=False), now, now),
            )
        except sqlite3.IntegrityError:
            # Lost a race with another request for the same confirmation. The
            # winner's row is the answer; there is no second job to create.
            existing = self.start_job(request_id)
            if existing is None:
                raise
            return existing
        return self.get_job(job_id)  # type: ignore[return-value]

    def enqueue_continue(self, *, job_id: str, request_id: str, scientific_run_id: str,
                         generation: int, payload: dict[str, Any]) -> dict[str, Any] | None:
        """Queue one continuation for one gate generation, or None if taken.

        `None` means this generation already has a continuation — a duplicate or
        a stale decision — and the caller reports that rather than queuing a
        second answer to one question.
        """
        now = utcnow()
        try:
            self.connection.execute(
                "INSERT INTO jobs (job_id, request_id, kind, status, scientific_run_id, "
                "generation, payload, error, created_at, updated_at) "
                "VALUES (?, ?, 'continue', 'queued', ?, ?, ?, NULL, ?, ?)",
                (job_id, request_id, scientific_run_id, generation,
                 json.dumps(payload, ensure_ascii=False), now, now),
            )
        except sqlite3.IntegrityError:
            return None
        return self.get_job(job_id)

    def get_job(self, job_id: str) -> dict[str, Any] | None:
        row = self.connection.execute("SELECT * FROM jobs WHERE job_id = ?", (job_id,)).fetchone()
        return _job_row(row) if row else None

    def start_job(self, request_id: str) -> dict[str, Any] | None:
        row = self.connection.execute(
            "SELECT * FROM jobs WHERE request_id = ? AND kind = 'start'", (request_id,)
        ).fetchone()
        return _job_row(row) if row else None

    def jobs_for_request(self, request_id: str) -> list[dict[str, Any]]:
        return [
            _job_row(row)
            for row in self.connection.execute(
                "SELECT * FROM jobs WHERE request_id = ? ORDER BY created_at", (request_id,)
            )
        ]

    def claim_next_job(self, *, worker_id: str) -> dict[str, Any] | None:
        """Take one queued job, atomically, so two workers cannot take the same one.

        The `WHERE status = 'queued'` in the UPDATE is what makes it atomic: a
        second worker's UPDATE matches no row and it moves on.
        """
        while True:
            row = self.connection.execute(
                "SELECT job_id FROM jobs WHERE status = 'queued' ORDER BY created_at LIMIT 1"
            ).fetchone()
            if row is None:
                return None
            cursor = self.connection.execute(
                "UPDATE jobs SET status = 'running', error = ?, updated_at = ? "
                "WHERE job_id = ? AND status = 'queued'",
                (f"claimed by {worker_id}", utcnow(), row["job_id"]),
            )
            if cursor.rowcount:
                return self.get_job(row["job_id"])

    def finish_job(self, job_id: str, status: str, *, error: str | None = None) -> None:
        self.connection.execute(
            "UPDATE jobs SET status = ?, error = ?, updated_at = ? WHERE job_id = ?",
            (status, error, utcnow(), job_id),
        )

    def running_jobs(self) -> list[dict[str, Any]]:
        return [
            _job_row(row)
            for row in self.connection.execute("SELECT * FROM jobs WHERE status = 'running'")
        ]

    # --- gate decisions -----------------------------------------------------

    def record_decision(self, entry: dict[str, Any]) -> None:
        self.connection.execute(
            "INSERT INTO gate_decisions (decision_id, scientific_run_id, gate_id, generation, "
            "decision, rationale, overrides, operator_id, outcome, created_at) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (
                entry["decision_id"], entry["scientific_run_id"], entry["gate_id"],
                entry["generation"], entry["decision"], entry.get("rationale"),
                json.dumps(entry.get("overrides") or {}, ensure_ascii=False),
                entry["operator_id"], entry["outcome"], utcnow(),
            ),
        )

    def decisions_for_run(self, scientific_run_id: str) -> list[dict[str, Any]]:
        return [
            {**dict(row), "overrides": json.loads(row["overrides"])}
            for row in self.connection.execute(
                "SELECT * FROM gate_decisions WHERE scientific_run_id = ? ORDER BY created_at",
                (scientific_run_id,),
            )
        ]


def _job_row(row: sqlite3.Row) -> dict[str, Any]:
    job = dict(row)
    job["payload"] = json.loads(job["payload"])
    return job
