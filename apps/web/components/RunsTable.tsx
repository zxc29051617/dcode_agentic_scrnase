"use client";

import { useMemo, useState } from "react";
import Link from "next/link";
import Badge from "@/components/Badge";
import { formatCount, formatTime, runTone } from "@/lib/verdict";
import type { RunSummary } from "@/lib/gatewayTypes";

/**
 * Filtering happens here rather than at the gateway because the run list is
 * a handful of rows read off local disk; a round trip per keystroke would be
 * slower and would put a user's typing into the server's log.
 */
export default function RunsTable({ runs }: { runs: RunSummary[] }) {
  const [query, setQuery] = useState("");
  const [status, setStatus] = useState("all");

  const statuses = useMemo(
    () => Array.from(new Set(runs.map((r) => r.status))).sort(),
    [runs],
  );

  const shown = useMemo(() => {
    const needle = query.trim().toLowerCase();
    return runs.filter(
      (r) =>
        (status === "all" || r.status === status) &&
        (needle === "" || r.scientific_run_id.toLowerCase().includes(needle)),
    );
  }, [runs, query, status]);

  if (runs.length === 0) {
    return (
      <div className="panel">
        <h2 style={{ marginTop: 0 }}>No runs found</h2>
        <p className="subtle">
          The gateway is reading its configured runs root and found no directory containing a{" "}
          <code>run_metadata.json</code>. Point <code>GATEWAY_RUNS_ROOT</code> at a directory of
          runs, or at <code>results/</code> for runs that were kept.
        </p>
      </div>
    );
  }

  return (
    <>
      <div className="controls">
        <input
          type="search"
          placeholder="Search run id…"
          value={query}
          onChange={(e) => setQuery(e.target.value)}
          aria-label="Search run id"
          style={{ minWidth: "16rem" }}
        />
        <select value={status} onChange={(e) => setStatus(e.target.value)} aria-label="Filter by status">
          <option value="all">All statuses</option>
          {statuses.map((s) => (
            <option key={s} value={s}>
              {s}
            </option>
          ))}
        </select>
        <span className="subtle">
          {shown.length} of {runs.length}
        </span>
      </div>

      {shown.length === 0 ? (
        <div className="panel">
          <p style={{ margin: 0 }}>Nothing matches that search.</p>
        </div>
      ) : (
        <div className="table-wrap">
          <table className="data">
            <thead>
              <tr>
                <th>Run</th>
                <th>Status</th>
                <th>Started</th>
                <th className="num">Steps</th>
                <th className="num">Cells</th>
                <th className="num">Clusters</th>
                <th className="num">Cell types</th>
              </tr>
            </thead>
            <tbody>
              {shown.map((r) => (
                <tr key={r.scientific_run_id}>
                  <td>
                    <Link href={`/runs/${encodeURIComponent(r.scientific_run_id)}`}>
                      {r.scientific_run_id}
                    </Link>
                  </td>
                  <td>
                    <Badge tone={runTone(r.status)}>{r.status}</Badge>
                  </td>
                  <td className="subtle">{formatTime(r.started_at)}</td>
                  <td className="num">{r.steps_recorded}</td>
                  <Cell value={r.cells} />
                  <Cell value={r.clusters} />
                  <Cell value={r.cell_types} />
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
    </>
  );
}

/** A number the run recorded, or a dash — never a substituted value. */
function Cell({ value }: { value: number | null }) {
  const text = formatCount(value);
  return (
    <td className="num" title={text === null ? "not recorded by any step in this run" : undefined}>
      {text ?? <span className="subtle">—</span>}
    </td>
  );
}
