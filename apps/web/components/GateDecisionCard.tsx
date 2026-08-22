"use client";

import { useState } from "react";
import ThresholdPreview from "@/components/run/ThresholdPreview";
import { useRouter } from "next/navigation";
import type { GateState } from "@/lib/controllerTypes";
import { buildGateDecisionBody } from "@/lib/gateDecision";
import { candidatesFor, filterCandidates, type GateCandidate } from "@/lib/gateCandidates";
import { presetsFor, type GatePreset, type PresetFact } from "@/lib/gatePresets";
import GateAdvisor from "@/components/GateAdvisor";

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
  { value: "accept", label: "接受", hint: "採用這個結果，繼續往下跑" },
  { value: "revise", label: "改參數重跑", hint: "改一個參數，把這一步重跑一次" },
  { value: "stop", label: "停止", hint: "在這裡結束這次執行" },
] as const;

type Decision = (typeof DECISIONS)[number]["value"];

export default function GateDecisionCard({
  state,
  advisorInstructions,
  modelConfigured = false,
  modelReason = null,
}: {
  state: GateState;
  /** The advisor's brief. Passed in from the server so this component never
   *  reads an environment variable. */
  advisorInstructions?: string;
  modelConfigured?: boolean;
  modelReason?: string | null;
}) {
  const router = useRouter();
  const gate = state.pending_gate;
  // Deterministic from this step's own contract: `apply_cell_qc_filter`
  // records `filter_state: "needs_review"` if and only if no threshold was
  // requested, and emits this exact phrase alongside it every time.
  const acceptWouldHalt =
    gate?.step === "apply_cell_qc_filter" &&
    (gate?.reasons ?? []).some((r) => r.includes("no QC thresholds chosen"));
  const [decision, setDecision] = useState<Decision>(acceptWouldHalt ? "revise" : "accept");
  const [rationale, setRationale] = useState("");
  const [overrides, setOverrides] = useState<Record<string, string>>({});
  // Which named set is showing. `"custom"` is a real choice, not the
  // absence of one: it means the person looked at the sets and wants the
  // boxes, which is different from a gate that offered no sets at all.
  const [preset, setPreset] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [done, setDone] = useState<string | null>(null);

  if (!gate || !state.gate_id) return null;

  const offered = gate.revisable ?? [];
  const presets = presetsFor({
    step: gate.step,
    revisable: gate.revisable,
    advice: gate.advice as unknown[] | null,
    evidence: gate.evidence as Record<string, unknown> | null,
  });

  async function submit() {
    if (busy) return;
    setBusy(true);
    setError(null);
    try {
      // What is sent, and what is deliberately left out of it, is
      // `lib/gateDecision.ts` — the blank-is-an-absence rule and the
      // send-the-string-as-typed rule are both testable there without a DOM.
      const body = buildGateDecisionBody({
        decision,
        generation: state.generation,
        overrides,
        rationale,
      });
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
              : "這個決定沒有被接受",
        );
        return;
      }
      setDone(`已記錄「${DECISIONS.find((d) => d.value === decision)?.label ?? decision}」—— worker 會接手繼續這次執行`);
      // Re-render the server component so the page stops showing a gate that
      // has been answered.
      router.refresh();
    } catch (err) {
      setError(err instanceof Error ? err.message : "送不出這個決定");
    } finally {
      setBusy(false);
    }
  }

  return (
    <div className="panel" data-tone="warn" data-testid="gate-decision-card">
      <h2 style={{ marginTop: 0 }}>等你決定</h2>
      <p style={{ marginTop: 0 }}>
        <code>{gate.step}</code>（{gate.gate}）— 判斷 <strong>{gate.verdict}</strong>
        {gate.score !== null && gate.score !== undefined && ` · 分數 ${gate.score}`} · 第{" "}
        {state.generation} 次
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
          建議的做法：{gate.suggested_action}{" "}
          <em>—— 這是模型的建議，不是決定。決定在你。</em>
        </p>
      )}

      {/* An empty `advice` used to render nothing at all, so a gate where the
          reviewer proposed no value looked identical to one where the block had
          simply not loaded. "It did not propose one" is a fact about this
          gate and belongs on the page. */}
      {gate.revisable?.length > 0 && !(gate.advice?.length > 0) && (
        <p className="subtle" data-testid="gate-no-advice">
          模型在這裡沒有提出任何數值 —— 它只回報了量到的東西，把選擇留著。下面就是它量到的。
        </p>
      )}

      {gate.advice?.length > 0 && (
        <div data-testid="gate-advice">
          <h3>模型提出的建議值</h3>
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

      {/* The evidence, translated. It was already in the payload and already
          on the page — as a two-hundred-line JSON blob behind a summary that
          called itself "for checking". It is the entire answer to the question
          this gate asks, so it is now a table. */}
      <ThresholdPreview
        preview={(gate.evidence as Record<string, unknown> | undefined)?.preview as
          | Record<string, unknown>
          | undefined}
        distributions={(gate.evidence as Record<string, unknown> | undefined)?.distributions as
          | Record<string, unknown>
          | undefined}
        nCells={(gate.evidence as Record<string, unknown> | undefined)?.n_cells as number | undefined}
      />

      {gate.evidence && Object.keys(gate.evidence).length > 0 && (
        <details data-testid="gate-evidence">
          {/* Kept, and deliberately not the interface. Sixty-one models
              rendered as raw JSON above an empty text box left the reading,
              remembering and retyping to a person, for a decision the system
              had already enumerated every option for. The picker below is
              where the choice is made; this stays for checking what the
              executor actually recorded. */}
          <summary>完整的原始證據（JSON，供核對）</summary>
          <pre style={{ overflowX: "auto", fontSize: "0.75rem" }}>
            {JSON.stringify(gate.evidence, null, 2)}
          </pre>
        </details>
      )}

      {gate.review && (
        <details data-testid="gate-review" open>
          <summary>本次執行的檢視</summary>
          <pre style={{ overflowX: "auto", fontSize: "0.75rem" }}>
            {JSON.stringify(gate.review, null, 2)}
          </pre>
        </details>
      )}

      {/* `apply_cell_qc_filter` demonstrated the gap this closes: Accept was
          offered as an equal option here, and two seconds after somebody
          pressed it the run halted — `filter_state` stays `needs_review`
          whenever no threshold was set, and the executor refuses to build a
          report on a step that never resolved. The reason text is part of
          that step's contract (`apply_cell_qc_filter.py` emits it exactly
          when, and only when, this is true), so it can be checked here
          rather than discovered after the fact a second time. */}
      {acceptWouldHalt && (
        <p className="subtle" data-tone="warn" data-testid="accept-would-halt" style={{ marginTop: "0.8rem" }}>
          <strong>接受不會讓這一步完成。</strong>還沒有設定任何閾值，所以接受之後這次執行會立刻停住
          —— 沒有過濾後的物件可以往下傳。請選<strong>改參數重跑</strong>，並在下面至少給一個值。
        </p>
      )}

      <fieldset style={{ marginTop: "1rem", border: "none", padding: 0 }}>
        <legend className="subtle">你的決定</legend>
        {DECISIONS.map((option) => (
          <label key={option.value} style={{ display: "block", padding: "0.15rem 0" }}>
            <input
              type="radio"
              name="decision"
              value={option.value}
              checked={decision === option.value}
              disabled={option.value === "accept" && acceptWouldHalt}
              onChange={() => setDecision(option.value)}
            />{" "}
            <strong>{option.label}</strong>{" "}
            <span className="subtle">
              — {option.value === "accept" && acceptWouldHalt ? "會讓這次執行停住，見上方說明" : option.hint}
            </span>
          </label>
        ))}
      </fieldset>

      {decision === "revise" && (
        <div data-testid="revise-fields" style={{ marginTop: "0.6rem" }}>
          {offered.length === 0 ? (
            <p className="subtle">
              這一關沒有可以改的參數，所以「改參數重跑」在這裡就只是「再跑一次」。
            </p>
          ) : (
            <>
              <p className="subtle" style={{ marginTop: 0 }}>
                會從 <code>{gate.revise_target}</code> 開始重跑。留白表示沿用目前的值。
              </p>
              {/* A named set, or three empty boxes. The decision at a QC gate
                  is not three independent numbers, it is one posture — cut
                  the tail, keep everything, be strict — and rebuilding that
                  from a percentile table is work the evidence already did.
                  `lib/gatePresets.ts` returns nothing where it cannot build a
                  set, and then this is the plain form it always was. */}
              {presets.length > 0 && (
                <PresetPicker
                  presets={presets}
                  selected={preset}
                  onSelect={(next) => {
                    setPreset(next);
                    const chosen = presets.find((p) => p.key === next);
                    if (chosen) setOverrides({ ...chosen.overrides });
                  }}
                />
              )}
              {(presets.length === 0 || preset === "custom") &&
                offered.map((name) => {
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


      {/* Optional, and it stays optional — a required free-text box produces
          "." and a mandatory rationale nobody means is worse evidence than an
          honest absence. But it has to exist: `docs/report_contract.md` P3
          renders a rationale for every human decision, and without a control
          here every gate answered in a browser printed a dash in that column
          while the same gate answered at a terminal recorded a sentence. The
          audit tier is the reason this pipeline exists; it should not empty
          out for the operators the web app was built for. */}
      <label style={{ display: "block", marginTop: "0.9rem" }}>
        為什麼 <span className="subtle">（選填 —— 會寫進這次執行的稽核紀錄，以及報告的決策表；
        那裡是唯一留下你當時在想什麼的地方）</span>
        <textarea
          data-testid="gate-rationale"
          value={rationale}
          rows={2}
          onChange={(event) => setRationale(event.target.value)}
          placeholder={
            decision === "revise"
              ? "例如：粒線體中位數 4.73%，15 切的是尾巴，不會砍進分布主體"
              : decision === "stop"
                ? "例如：reference 選錯了，要從 FASTQ 重來"
                : "例如：警告講的是第 12 群，只有 8 顆細胞，這個組織本來就會這樣"
          }
          style={{ display: "block", width: "100%", marginTop: "0.25rem", padding: "0.4rem 0.5rem" }}
        />
      </label>

      <div style={{ marginTop: "0.8rem", display: "flex", gap: "0.6rem", alignItems: "center" }}>
        {/* "Submit stop" is not a sentence anybody says, and it gave the
            destructive option the same weight and wording as the other two.
            Each decision now names what it does, and stop is marked. */}
        <button
          type="button"
          onClick={submit}
          disabled={busy}
          data-testid="gate-submit"
          data-tone={decision === "stop" ? "fail" : undefined}
        >
          {busy
            ? "送出中…"
            : decision === "accept"
              ? "接受並繼續"
              : decision === "revise"
                ? "用這些值重跑這一步"
                : "停止這次執行"}
        </button>
        {done && <span className="subtle" data-testid="gate-done">{done}</span>}
      </div>

      {error && (
        <p className="subtle" data-tone="fail" data-testid="gate-error">
          {error}
        </p>
      )}

      {advisorInstructions && (
        <GateAdvisor
          runId={state.scientific_run_id}
          step={gate.step}
          parameters={offered}
          instructions={advisorInstructions}
          modelConfigured={modelConfigured}
          modelReason={modelReason}
        />
      )}

      <p className="subtle" style={{ marginBottom: 0, marginTop: "0.8rem" }}>
        助理可以解釋這些證據、替某個選項辯護，但它<strong>不能替你回答</strong>
        —— <code>accept</code>、<code>revise</code>、<code>stop</code> 都是記在一個人名下的。
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
        placeholder="用名稱或說明過濾…"
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
            heading="本機已有，可以直接用"
            note="already downloaded on this machine"
            items={local}
            parameter={group.parameter}
            value={value}
            onChange={onChange}
          />
        )}
        {remote.length > 0 && (
          <CandidateGroup
            heading="要先下載"
note="CellTypist 會在使用時自己抓；這個頁面不會下載任何東西"
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

/** `4618.5` -> `4,618.5`. Absent stays absent rather than becoming a zero. */
function nfmt(value: number | null): string | null {
  return value === null ? null : value.toLocaleString("en-US");
}

const PRESET_NAMES: Record<string, string> = {
  advised: "模型建議",
  looser: "寬鬆",
  stricter: "嚴格",
};

/**
 * One line of a preset: what this criterion alone would cost, and where this
 * run's own distribution sits.
 *
 * Both halves are needed and neither is enough. "removes 108 cells" does not
 * say whether 15 is a tail or the middle of the data; "the median is 4.73"
 * does not say what acting on it costs. The pairing is the whole reason the
 * step writes `preview` and `distributions` as two blocks.
 */
function FactLine({ fact }: { fact: PresetFact }) {
  const removed = nfmt(fact.cellsRemoved);
  const kept = nfmt(fact.cellsKept);
  return (
    <li style={{ padding: "0.1rem 0" }}>
      <code>
        {fact.parameter} {fact.threshold}
      </code>{" "}
      {removed !== null ? (
        <>
          — 單獨移除 {removed} 顆
          {fact.pctRemoved !== null && `（${fact.pctRemoved}%）`}
          {kept !== null && `，保留 ${kept}`}
        </>
      ) : (
        <span className="subtle">— 這個值不在預覽表裡，代價未知</span>
      )}
      {fact.median !== null && (
        <span className="subtle">
          {" "}
          · 本次中位數 {fact.median}
          {fact.p90 !== null && `，p90 ${fact.p90}`}
          {fact.p95 !== null && `，p95 ${fact.p95}`}
        </span>
      )}
    </li>
  );
}

/**
 * Pick a whole threshold set, or ask for the boxes.
 *
 * ## "Recommended" has to point at something
 *
 * Only the `judge` preset is marked, and what marks it is not this component's
 * opinion: it is the set the run's own judge call advised, shown with the
 * confidence it reported and the sentence it wrote. A page that labelled a
 * derived set "recommended" would be the interface making a scientific choice
 * with nothing behind it, which is the failure the whole gate exists to avoid.
 *
 * ## No total, ever
 *
 * Each line is that criterion applied alone and the cuts overlap — 87 + 105 +
 * 108 read as 300 on the run this was built against, where 167 cells were
 * actually removed. There is no combined figure here because there is no
 * honest one to show before the filter runs; `filter_summary` reports it
 * afterwards, in `n_removed_by_more_than_one`.
 */
function PresetPicker({
  presets,
  selected,
  onSelect,
}: {
  presets: GatePreset[];
  selected: string | null;
  onSelect: (key: string) => void;
}) {
  return (
    <fieldset
      data-testid="gate-presets"
      style={{ border: "none", padding: 0, margin: "0 0 0.8rem 0" }}
    >
      <legend className="subtle">選一組閾值</legend>
      {presets.map((preset, i) => (
        <label
          key={preset.key}
          data-testid={`preset-${preset.key}`}
          style={{ display: "block", padding: "0.35rem 0" }}
        >
          <input
            type="radio"
            name="preset"
            value={preset.key}
            checked={selected === preset.key}
            onChange={() => onSelect(preset.key)}
          />{" "}
          <strong>
            {i + 1}. {PRESET_NAMES[preset.key] ?? preset.key}
          </strong>
          {preset.source === "judge" && (
            <span className="subtle" data-testid="preset-recommended">
              {" "}
              — 模型建議
              {preset.confidence ? `，信心 ${preset.confidence}` : ""}
            </span>
          )}
          <ul style={{ margin: "0.2rem 0 0 1.4rem", fontSize: "0.9rem" }}>
            {preset.facts.map((fact) => (
              <FactLine key={fact.parameter} fact={fact} />
            ))}
          </ul>
          {/* The judge's own words, not a paraphrase. It was written during
              this run's judge call and is already in the audit log, so the
              page is quoting the record rather than adding to it. */}
          {preset.rationale && (
            <p className="subtle" style={{ margin: "0.2rem 0 0 1.4rem", fontSize: "0.85rem" }}>
              {preset.rationale}
            </p>
          )}
        </label>
      ))}
      <label style={{ display: "block", padding: "0.35rem 0" }}>
        <input
          type="radio"
          name="preset"
          value="custom"
          checked={selected === "custom"}
          onChange={() => onSelect("custom")}
        />{" "}
        <strong>{presets.length + 1}. 自己填</strong>
        <span className="subtle"> — 每個參數分別輸入</span>
      </label>
      <p className="subtle" style={{ margin: "0.4rem 0 0", fontSize: "0.85rem" }}>
        每一行都是那個條件<strong>單獨</strong>作用的結果，條件之間會重疊，
        <strong>移除數不能相加</strong>。實際合計要等過濾跑完才知道。
      </p>
    </fieldset>
  );
}
