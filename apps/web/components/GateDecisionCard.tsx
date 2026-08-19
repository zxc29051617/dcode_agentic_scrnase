"use client";

import { useState } from "react";
import { useRouter } from "next/navigation";
import type { GateState } from "@/lib/controllerTypes";
import { candidatesFor, filterCandidates, type GateCandidate } from "@/lib/gateCandidates";

/**
 * The accept / revise / stop control for a run waiting at a human gate.
 *
 * This is the only mutating control in the application besides Confirm, and
 * the things it deliberately does *not* do are what keep it safe:
 *
 * **It does not decide which parameters may be set.** The inputs rendered are
 * exactly `pending_gate.revisable`, which the executor wrote into the question
 * when it opened the gate. A parameter that is not offered has no input, and
 * if one were somehow submitted anyway the controller refuses it through
 * `coerce_overrides`.
 *
 * **It does not convert a value.** `min_genes` is sent as the string typed.
 * The controller converts it with the same function the terminal uses, so a
 * threshold typed in a browser and one typed at a prompt mean the same thing.
 * A `Number()` here would be a second opinion, and the browser's would be the
 * one nobody audits.
 *
 * **It does not let the assistant answer.** There is no action, tool or prop
 * through which the model reaches this component's submit. A person clicks it.
 *
 * `expected_generation` travels with the decision. If somebody else answered
 * while this page was open, the controller refuses rather than applying this
 * answer to whatever the run is waiting on now, and the message says to reload.
 */

const DECISIONS = [
  { value: "accept", label: "Accept", hint: "take this result and carry on" },
  { value: "revise", label: "Revise", hint: "change a parameter and run this step again" },
  { value: "stop", label: "Stop", hint: "end the run here" },
] as const;

type Decision = (typeof DECISIONS)[number]["value"];

export default function GateDecisionCard({ state }: { state: GateState }) {
  const router = useRouter();
  const gate = state.pending_gate;
  const [decision, setDecision] = useState<Decision>("accept");
  const [rationale, setRationale] = useState("");
  const [overrides, setOverrides] = useState<Record<string, string>>({});
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [done, setDone] = useState<string | null>(null);

  if (!gate || !state.gate_id) return null;

  const offered = gate.revisable ?? [];

  async function submit() {
    if (busy) return;
    setBusy(true);
    setError(null);
    try {
      const body: Record<string, unknown> = {
        decision,
        rationale,
        expected_generation: state.generation,
      };
      if (decision === "revise") {
        // Only the parameters the operator actually typed into. A blank keeps
        // the current value, which is the same rule the terminal follows.
        const typed = Object.fromEntries(
          Object.entries(overrides).filter(([, value]) => value.trim() !== ""),
        );
        body.overrides = typed;
      }
      const response = await fetch(
        `/api/scientific-runs/${encodeURIComponent(state.scientific_run_id)}/gates/${encodeURIComponent(state.gate_id!)}/decision`,
        {
          method: "POST",
          headers: { "content-type": "application/json" },
          body: JSON.stringify(body),
        },
      );
      const parsed = await response.json();
      if (!response.ok) {
        const detail = parsed?.detail;
        setError(
          typeof detail === "string"
            ? detail
            : detail
              ? JSON.stringify(detail)
              : "the decision was not accepted",
        );
        return;
      }
      setDone(`${decision} recorded — the worker will continue this run`);
      // Re-render the server component so the page stops showing a gate that
      // has been answered.
      router.refresh();
    } catch (err) {
      setError(err instanceof Error ? err.message : "could not submit the decision");
    } finally {
      setBusy(false);
    }
  }

  return (
    <div className="panel" data-tone="warn" data-testid="gate-decision-card">
      <h2 style={{ marginTop: 0 }}>Waiting for your decision</h2>
      <p style={{ marginTop: 0 }}>
        <code>{gate.step}</code> ({gate.gate}) — verdict <strong>{gate.verdict}</strong>
        {gate.score !== null && gate.score !== undefined && ` · score ${gate.score}`} · gate{" "}
        {state.generation}
      </p>

      {gate.reasons?.length > 0 && (
        <ul data-testid="gate-reasons">
          {gate.reasons.map((reason, i) => (
            <li key={i}>{reason}</li>
          ))}
        </ul>
      )}

      {gate.suggested_action && (
        <p className="subtle">
          Suggested: {gate.suggested_action}{" "}
          <em>— a suggestion from the judge, not a decision. It is yours.</em>
        </p>
      )}

      {gate.advice?.length > 0 && (
        <div data-testid="gate-advice">
          <h3>The judge proposed</h3>
          <ul>
            {gate.advice.map((entry, i) => (
              <li key={i}>
                <code>{entry.parameter}</code> = <code>{JSON.stringify(entry.suggested_value)}</code>
                {entry.confidence ? ` [${entry.confidence}]` : ""} — {entry.rationale}
              </li>
            ))}
          </ul>
        </div>
      )}

      {gate.evidence && Object.keys(gate.evidence).length > 0 && (
        <details data-testid="gate-evidence">
          {/* Kept, and deliberately not the interface. Sixty-one models
              rendered as raw JSON above an empty text box left the reading,
              remembering and retyping to a person, for a decision the system
              had already enumerated every option for. The picker below is
              where the choice is made; this stays for checking what the
              executor actually recorded. */}
          <summary>Full recorded evidence (JSON, for checking)</summary>
          <pre style={{ overflowX: "auto", fontSize: "0.75rem" }}>
            {JSON.stringify(gate.evidence, null, 2)}
          </pre>
        </details>
      )}

      {gate.review && (
        <details data-testid="gate-review" open>
          <summary>Run review</summary>
          <pre style={{ overflowX: "auto", fontSize: "0.75rem" }}>
            {JSON.stringify(gate.review, null, 2)}
          </pre>
        </details>
      )}

      <fieldset style={{ marginTop: "1rem", border: "none", padding: 0 }}>
        <legend className="subtle">Your decision</legend>
        {DECISIONS.map((option) => (
          <label key={option.value} style={{ display: "block", padding: "0.15rem 0" }}>
            <input
              type="radio"
              name="decision"
              value={option.value}
              checked={decision === option.value}
              onChange={() => setDecision(option.value)}
            />{" "}
            <strong>{option.label}</strong> <span className="subtle">— {option.hint}</span>
          </label>
        ))}
      </fieldset>

      {decision === "revise" && (
        <div data-testid="revise-fields" style={{ marginTop: "0.6rem" }}>
          {offered.length === 0 ? (
            <p className="subtle">
              This gate offers no parameters, so <code>revise</code> means only &ldquo;run it
              again&rdquo;.
            </p>
          ) : (
            <>
              <p className="subtle" style={{ marginTop: 0 }}>
                Changing <code>{gate.revise_target}</code> onward. Blank keeps the current value.
              </p>
              {offered.map((name) => {
                // The executor listed the options for this parameter, so pick
                // from them. Where it did not, a text box is the honest
                // control — inventing a menu would be inventing choices.
                const enumerated = candidatesFor(name, gate.evidence);
                return enumerated ? (
                  <CandidatePicker
                    key={name}
                    group={enumerated}
                    value={overrides[name] ?? ""}
                    onChange={(next) =>
                      setOverrides((current) => ({ ...current, [name]: next }))
                    }
                  />
                ) : (
                  <label key={name} style={{ display: "block", padding: "0.15rem 0" }}>
                    <code>{name}</code>{" "}
                    <input
                      name={name}
                      data-testid={`override-${name}`}
                      value={overrides[name] ?? ""}
                      onChange={(event) =>
                        setOverrides((current) => ({ ...current, [name]: event.target.value }))
                      }
                    />
                  </label>
                );
              })}
            </>
          )}
        </div>
      )}

      <label style={{ display: "block", marginTop: "0.6rem" }}>
        Why <span className="subtle">(recorded in the run&rsquo;s audit log)</span>
        <input
          data-testid="gate-rationale"
          value={rationale}
          onChange={(event) => setRationale(event.target.value)}
        />
      </label>

      <div style={{ marginTop: "0.8rem", display: "flex", gap: "0.6rem", alignItems: "center" }}>
        <button type="button" onClick={submit} disabled={busy} data-testid="gate-submit">
          {busy ? "Submitting…" : `Submit ${decision}`}
        </button>
        {done && <span className="subtle" data-testid="gate-done">{done}</span>}
      </div>

      {error && (
        <p className="subtle" data-tone="fail" data-testid="gate-error">
          {error}
        </p>
      )}

      <p className="subtle" style={{ marginBottom: 0, marginTop: "0.8rem" }}>
        The assistant can explain this evidence. It cannot answer for you, and it cannot change a
        threshold — <code>accept</code>, <code>revise</code> and <code>stop</code> are recorded
        against a person.
      </p>
    </div>
  );
}

/**
 * Pick one option the executor enumerated, from the executor's own list.
 *
 * Three things this has to get right, and the third is the one that bites.
 *
 * **The descriptions survive.** Sixty-one filenames in a bare `<select>` would
 * replace "read JSON to find a name" with "guess from a name", which is not an
 * improvement for a decision whose whole difficulty is knowing what the options
 * mean. `Adult_Human_PBMC` and `Immune_All_Low` are distinguishable only by
 * what they were trained on, and that sentence is in the evidence.
 *
 * **Availability is stated before the choice, not after.** Two of the sixty-one
 * models are cached locally. Choosing one of the other fifty-nine is a decision
 * to wait for a download, and finding that out afterwards is the experience
 * this grouping exists to prevent. Nothing here downloads anything — the same
 * rule as everywhere else in this app: it reports, a person decides.
 *
 * **Selecting is not deciding.** This writes to the same `overrides` state the
 * text box wrote to. `accept` / `revise` / `stop` and the submit path are
 * untouched; a picked value is still only a proposal until somebody presses
 * Submit.
 */
function CandidatePicker({
  group,
  value,
  onChange,
}: {
  group: NonNullable<ReturnType<typeof candidatesFor>>;
  value: string;
  onChange: (next: string) => void;
}) {
  const [query, setQuery] = useState("");
  const matches = filterCandidates(group.candidates, query);
  const local = matches.filter((c) => c.local === true);
  const remote = matches.filter((c) => c.local === false);
  const plain = matches.filter((c) => c.local === null);
  const chosen = group.candidates.find((c) => c.value === value) ?? null;

  return (
    <div data-testid={`picker-${group.parameter}`} style={{ marginTop: "0.5rem" }}>
      <div style={{ display: "flex", gap: "0.6rem", alignItems: "baseline", flexWrap: "wrap" }}>
        <code>{group.parameter}</code>
        <span className="subtle">
          {group.candidates.length} option{group.candidates.length === 1 ? "" : "s"} recorded by{" "}
          this step
        </span>
      </div>

      <input
        type="search"
        placeholder="Filter by name or description…"
        value={query}
        data-testid={`picker-search-${group.parameter}`}
        onChange={(event) => setQuery(event.target.value)}
        style={{ width: "100%", margin: "0.4rem 0 0.5rem", padding: "0.4rem 0.5rem" }}
      />

      {/* The value actually being submitted, restated. The list scrolls, and a
          choice made and then scrolled past is a choice a person cannot check
          before pressing Submit. */}
      <p className="subtle" style={{ margin: "0 0 0.5rem" }} data-testid={`picker-chosen-${group.parameter}`}>
        {chosen ? (
          <>
            selected <code>{chosen.value}</code>
            {chosen.local === false && (
              <strong> — not downloaded on this machine</strong>
            )}
          </>
        ) : (
          <>nothing selected — the current value is kept</>
        )}
      </p>

      <div
        style={{
          maxHeight: "22rem",
          overflowY: "auto",
          border: "1px solid var(--line)",
          borderRadius: "6px",
          padding: "0.35rem",
        }}
      >
        {matches.length === 0 && (
          <p className="subtle" style={{ margin: "0.5rem" }}>
            Nothing matches “{query}”.
          </p>
        )}
        {local.length > 0 && (
          <CandidateGroup
            heading="Available now"
            note="already downloaded on this machine"
            items={local}
            parameter={group.parameter}
            value={value}
            onChange={onChange}
          />
        )}
        {remote.length > 0 && (
          <CandidateGroup
            heading="Needs downloading first"
            note="CellTypist fetches these on use; this page does not download anything"
            items={remote}
            parameter={group.parameter}
            value={value}
            onChange={onChange}
          />
        )}
        {plain.length > 0 && (
          <CandidateGroup
            heading={null}
            note={null}
            items={plain}
            parameter={group.parameter}
            value={value}
            onChange={onChange}
          />
        )}
      </div>
    </div>
  );
}

function CandidateGroup({
  heading,
  note,
  items,
  parameter,
  value,
  onChange,
}: {
  heading: string | null;
  note: string | null;
  items: GateCandidate[];
  parameter: string;
  value: string;
  onChange: (next: string) => void;
}) {
  return (
    <div style={{ marginBottom: "0.5rem" }}>
      {heading && (
        <p
          className="subtle"
          style={{ margin: "0.35rem 0.4rem 0.3rem", fontSize: "0.75rem", letterSpacing: "0.04em" }}
        >
          <strong>{heading.toUpperCase()}</strong>
          {note ? ` · ${note}` : ""}
        </p>
      )}
      {items.map((item) => {
        const selected = value === item.value;
        return (
          <label
            key={item.value}
            data-testid={`option-${item.value}`}
            style={{
              display: "grid",
              gridTemplateColumns: "1.1rem 1fr",
              gap: "0.5rem",
              alignItems: "start",
              padding: "0.4rem 0.45rem",
              borderRadius: "5px",
              cursor: "pointer",
              background: selected ? "var(--reused-bg)" : undefined,
            }}
          >
            <input
              type="radio"
              name={`candidate-${parameter}`}
              checked={selected}
              onChange={() => onChange(item.value)}
              style={{ marginTop: "0.25rem" }}
            />
            <span>
              <code>{item.value}</code>
              {item.local === false && (
                <span className="subtle"> · not downloaded</span>
              )}
              {item.description && (
                <span
                  className="subtle"
                  style={{ display: "block", fontSize: "0.85rem", lineHeight: 1.45 }}
                >
                  {item.description}
                </span>
              )}
            </span>
          </label>
        );
      })}
    </div>
  );
}
