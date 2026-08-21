/**
 * The body one human gate decision is submitted as.
 *
 * Three rules, and each of them is a boundary rather than a formatting choice:
 *
 * **A value is sent as typed.** `min_genes` goes over the wire as the string
 * somebody entered. `src/registry.py::coerce_overrides` converts it, and it is
 * the same function the terminal goes through, so a threshold typed in a
 * browser and one typed at a prompt mean the same thing. A `Number()` here
 * would be a second opinion about what `"200"` means, and the browser's is the
 * one nobody audits.
 *
 * **Overrides travel only with `revise`.** The controller returns 422 for
 * overrides on an `accept` or a `stop` — they are not meaningful there, and
 * accepting them silently would let a value be recorded that changed nothing.
 *
 * **A blank is an absence, not a value.** An untouched override keeps the
 * current setting, which is the rule the terminal follows; an untouched
 * rationale is a decision made without one, which is different from a decision
 * whose stated reason is the empty string.
 *
 * ## Why the rationale is here at all
 *
 * `docs/report_contract.md` P3 renders one row per human decision with a
 * rationale column, and `skills/build_report/build_report.py` prints `—` where
 * there is none. Without a control for it, every gate answered from a browser
 * produced a dash in that column while the same gate answered at a terminal
 * recorded a sentence — the audit tier that document calls "the reason this
 * pipeline exists" quietly emptied out for exactly the operators this app was
 * built for.
 *
 * It is optional, and it stays optional. Requiring it would produce the thing
 * a required free-text field always produces, which is `.` — and a mandatory
 * rationale nobody means is worse evidence than an honest absence.
 */

export type GateDecision = "accept" | "revise" | "stop";

export type GateDecisionInput = {
  decision: GateDecision;
  /** The gate generation this answer was made against. */
  generation: number;
  /** Raw, as typed. Blank entries are dropped, never sent as "". */
  overrides: Record<string, string>;
  rationale: string;
};

export type GateDecisionBody = {
  decision: GateDecision;
  expected_generation: number;
  overrides?: Record<string, string>;
  rationale?: string;
};

export function buildGateDecisionBody(input: GateDecisionInput): GateDecisionBody {
  const body: GateDecisionBody = {
    decision: input.decision,
    expected_generation: input.generation,
  };

  if (input.decision === "revise") {
    body.overrides = Object.fromEntries(
      Object.entries(input.overrides).filter(([, value]) => value.trim() !== ""),
    );
  }

  const rationale = input.rationale.trim();
  if (rationale) body.rationale = rationale;

  return body;
}
