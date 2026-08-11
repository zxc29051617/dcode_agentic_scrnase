"""Write the registry-derived sections of the docs from `src/registry.py`.

    python scripts/export_registry_docs.py            # update the files
    python scripts/export_registry_docs.py --check    # fail if they are stale

The same argument `scripts/export_graph.py` makes, for the other half. That one
draws the topology from the compiled graph because a diagram of the wiring has
to *be* the wiring; this one writes the step tables from the registry because a
list of steps has to be the steps.

Both exist because the alternative was measured and it failed. `skills/README.md`
listed nineteen `judge_*` skills that had been deleted, omitted eight that
existed, called every skill a "scaffold" long after they were implemented, and
told the reader the orchestrator calls them over MCP, which it never has.
`docs/tool_registry.md` disagreed with itself in two places about whether judge
tools exist at all. Nobody was careless; the documents simply had no way to
fail.

## What is generated and what is not

Only what the registry actually knows: the step names, their kind, the judge
node each is scored by, whether `graph.py` owns their outgoing edges, and which
parameters a person may set at their gate.

Purpose, inputs and outputs are *not* generated. Those are human sentences about
why a step exists, and a generator would have to invent them. They stay in the
prose around the markers, and each step's real input/output contract lives in
its own `skills/<name>/SKILL.md`.

Topology is not generated here either. `docs/graph.mmd` comes from the compiled
graph via `scripts/export_graph.py` and is the precise answer; a "next step"
column in a table cannot express a conditional edge and would be a second,
worse answer to the same question.
"""

from __future__ import annotations

import argparse
import difflib
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from src.registry import REGISTRY, SKILLS_DIR  # noqa: E402

#: Delimiters. Everything between them is rewritten; everything outside is left
#: exactly as written, so a generated table can sit inside a page of prose
#: without the prose being at the generator's mercy.
BEGIN = "<!-- BEGIN GENERATED {name} — python scripts/export_registry_docs.py -->"
END = "<!-- END GENERATED {name} -->"

#: `StepSpec.kind` is the grouping, glossed rather than renamed: the heading a
#: reader sees is the value in the registry, so a step cannot appear under a
#: heading its spec does not claim.
KIND_TITLES: dict[str, str] = {
    "utility": "utility — intake, validation and reporting",
    "router": "router — chooses between routes",
    "upstream": "upstream — FASTQ to counts",
    "analysis": "analysis — the count matrix and everything after it",
    "gate": "gate — a person decides",
}


def skill_paths(step: str) -> tuple[Path, Path]:
    """Where a step's implementation and contract have to be."""
    return SKILLS_DIR / step / f"{step}.py", SKILLS_DIR / step / "SKILL.md"


def missing_skills() -> list[str]:
    """Registry steps with no implementation on disk."""
    return [step for step in REGISTRY if not skill_paths(step)[0].exists()]


def orphan_skills() -> list[str]:
    """Skill directories no registry step names."""
    if not SKILLS_DIR.is_dir():
        return []
    return sorted(
        child.name
        for child in SKILLS_DIR.iterdir()
        if child.is_dir() and (child / f"{child.name}.py").exists()
        and child.name not in REGISTRY
    )


def render_skill_list() -> str:
    """The skill inventory for `skills/README.md`, grouped by kind."""
    by_kind: dict[str, list[str]] = {}
    for name, spec in REGISTRY.items():
        by_kind.setdefault(spec.kind, []).append(name)

    lines = [
        f"**{len(REGISTRY)} skills**, one per registry step, in pipeline order. "
        f"Counted from `src/registry.py`, never typed.",
        "",
    ]
    for kind, steps in by_kind.items():
        lines.append(f"### {KIND_TITLES.get(kind, kind)}")
        lines.append("")
        for step in steps:
            implementation, contract = skill_paths(step)
            marks = []
            if not implementation.exists():
                marks.append("**no implementation**")
            if not contract.exists():
                marks.append("**no SKILL.md**")
            suffix = f" — {', '.join(marks)}" if marks else ""
            lines.append(f"- [`{step}`]({step}/SKILL.md){suffix}")
        lines.append("")
    return "\n".join(lines).rstrip()


def render_registry_table() -> str:
    """The step table for `docs/tool_registry.md`."""
    lines = [
        f"{len(REGISTRY)} steps, in the order `src/registry.py` declares them — which is",
        "also a valid topological order for both routes, and is what",
        "`registry.steps_invalidated_by` reads to decide what a config change stales.",
        "",
        "| # | step | kind | judge node | branches | revisable at its gate |",
        "|---|---|---|---|---|---|",
    ]
    for index, (name, spec) in enumerate(REGISTRY.items(), start=1):
        judge = f"`{spec.judge}`" if spec.judge else "—"
        branches = "yes" if spec.branches else "—"
        revisable = ", ".join(f"`{key}`" for key in spec.revisable) if spec.revisable else "—"
        lines.append(
            f"| {index} | `{name}` | {spec.kind} | {judge} | {branches} | {revisable} |"
        )

    judged = sum(1 for spec in REGISTRY.values() if spec.judge)
    branching = sum(1 for spec in REGISTRY.values() if spec.branches)
    revisable_steps = sum(1 for spec in REGISTRY.values() if spec.revisable)
    lines += [
        "",
        f"{judged} of {len(REGISTRY)} steps are judged; `human_review_decision` is a gate,",
        f"not a scored step. {branching} own their outgoing edges in `graph.py` rather than",
        f"having a single successor. {revisable_steps} accept a value from a person at their",
        "gate — the four that stop rather than guess.",
        "",
        "`judge node` is a **node name in the graph and a label in the audit log**, not a",
        "module: there is one judge implementation in `src/judge.py` and every step hands",
        "it a different payload. Inputs, outputs and failure modes are per step and live in",
        "`skills/<step>/SKILL.md`; the exact topology is `docs/graph.mmd`, generated from",
        "the compiled graph by `scripts/export_graph.py`.",
    ]
    return "\n".join(lines)


#: file -> {section name: renderer}
SECTIONS: dict[str, dict[str, callable]] = {
    "skills/README.md": {"skill-list": render_skill_list},
    "docs/tool_registry.md": {"registry-table": render_registry_table},
}


def replace_section(text: str, name: str, body: str) -> str:
    """Swap one delimited section's contents, leaving everything else alone."""
    begin, end = BEGIN.format(name=name), END.format(name=name)
    if begin not in text or end not in text:
        raise SystemExit(
            f"missing markers for section {name!r}; expected these two lines:\n"
            f"  {begin}\n  {end}"
        )
    head, rest = text.split(begin, 1)
    _stale, tail = rest.split(end, 1)
    return f"{head}{begin}\n\n{body}\n\n{end}{tail}"


def render_file(path: Path) -> str:
    """What `path` should contain."""
    text = path.read_text(encoding="utf-8")
    for name, renderer in SECTIONS[str(path.relative_to(PROJECT_ROOT))].items():
        text = replace_section(text, name, renderer())
    return text


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--check", action="store_true",
                        help="exit non-zero if a generated section is stale, and write nothing")
    args = parser.parse_args(argv)

    # A registry step with no skill is a broken pipeline, not a stale document,
    # so it fails in both modes rather than being written into a table.
    absent = missing_skills()
    if absent:
        print(f"registry steps with no skills/<name>/<name>.py: {absent}", file=sys.stderr)
        return 2
    orphans = orphan_skills()
    if orphans:
        print(f"skill directories no registry step names: {orphans}", file=sys.stderr)
        return 2

    stale: list[str] = []
    for relative in SECTIONS:
        path = PROJECT_ROOT / relative
        current = path.read_text(encoding="utf-8")
        wanted = render_file(path)
        if current == wanted:
            continue
        if args.check:
            stale.append(relative)
            diff = difflib.unified_diff(
                current.splitlines(), wanted.splitlines(),
                fromfile=f"{relative} (committed)", tofile=f"{relative} (from the registry)",
                lineterm="", n=2,
            )
            print("\n".join(diff), file=sys.stderr)
        else:
            path.write_text(wanted, encoding="utf-8")
            print(f"updated {relative}")

    if args.check:
        if stale:
            print(f"\nstale: {', '.join(stale)}\n"
                  f"run: python scripts/export_registry_docs.py", file=sys.stderr)
            return 1
        print(f"generated sections are current ({len(REGISTRY)} steps)")
        return 0

    if not stale:
        print(f"generated sections already current ({len(REGISTRY)} steps)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
