"""Human gate policy.

`docs/langgraph_scRNA_workflow.md` section 8:
    pass -> continue automatically
    warn -> continue only if policy allows, but record the warning
    fail -> stop and require human confirmation or parameter revision
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

from .judge import JudgeResult

GateRoute = Literal["continue", "human_gate"]


@dataclass(frozen=True)
class GatePolicy:
    """Decides whether a verdict continues the mainline or stops for a human."""

    autocontinue_on_warn: bool = False
    """If True, `warn` proceeds and the warning is recorded rather than blocking."""

    honor_needs_human_review: bool = True
    """If True, a judge asking for review wins even when the verdict is `pass`."""

    interactive: bool = False
    """If True, the human gate blocks via LangGraph `interrupt`; otherwise it halts."""

    headless_decision: Literal["stop", "accept"] = "stop"
    """What a non-interactive run assumes at a gate. Defaults to `stop` so nothing
    is ever waved through by accident; set to `accept` only to walk the wiring."""

    max_revisions_per_step: int = 10
    """How many times one step may be revised before the gate stops instead.

    Not a scientific threshold — a guard rail. `recursion_limit` does not bound
    the revise loop, because it counts supersteps per `invoke` and every
    `Command(resume=...)` starts a fresh one; answering `revise` forever
    therefore re-runs real analysis forever. A person at a terminal stops on
    their own, but a programmatic decider returning `revise` on every warn does
    not, and it is the same code path.

    Ten is high enough that nobody working through thresholds by hand will meet
    it and low enough to end a runaway inside one step's runtime. Exceeding it
    is recorded as a `stop` with the reason, never as a silent `accept`."""

    def route(self, judge: JudgeResult) -> GateRoute:
        if judge.verdict == "fail":
            return "human_gate"
        if judge.needs_human_review and self.honor_needs_human_review:
            return "human_gate"
        if judge.verdict == "warn" and not self.autocontinue_on_warn:
            return "human_gate"
        return "continue"


DEFAULT_POLICY = GatePolicy()
