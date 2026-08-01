---
description: Run OpenCodeReview (delegation mode) on the current changes — no LLM API key needed
allowed-tools: Bash(ocr:*), Bash(git diff:*), Bash(git status:*), Read, Grep, Glob, Edit
---

# /ocr-review

Review the current code changes using OpenCodeReview (OCR) in **delegation mode**:
`ocr` does file selection and rule resolution deterministically; **you** do the actual review.
No LLM provider config or API key is required.

Arguments passed by the user: `$ARGUMENTS`

## Step 1 — Get the reviewable file list

```bash
ocr delegate preview $ARGUMENTS
```

- No arguments → workspace mode (staged + unstaged + untracked).
- Pass through `--commit <sha>`, or `--from <ref> --to <ref>`, verbatim if the user gave them.
- Optional: `--background "<requirement context>"` to check the change against intent.
- If `ocr` is not found: `npm i -g @alibaba-group/open-code-review`.

Read the output header for `mode` and refs. Entries wrapped in `~~strikethrough~~` are
**excluded** — never review those. Review only the plain bullet entries.

If 0 reviewable files: say so in one line and stop.

## Step 2 — Get the rules for those files

```bash
ocr delegate rule <path1> <path2> ...
```

Pass **all** reviewable paths in one call (rules come back grouped by content).
These rules are the review standard — apply them, do not substitute your own checklist.

## Step 3 — Get the diff

Build the git command from the `mode` reported in Step 1:

- `workspace` → `git diff HEAD -- <paths>` plus the full content of untracked files
- `commit` → `git show <sha> -- <paths>`
- `range` → `git diff <merge-base> <to> -- <paths>`

Read surrounding context with Read/Grep when a rule says to confirm before flagging.

## Step 4 — Review

Produce **line-level** comments as `path:line — <issue> — <fix>`.

Rate each finding, then filter:

- **High** — real bugs, security issues, clear rule violations with a precise fix. Report.
- **Medium** — context-dependent concerns, performance/style with a concrete suggestion. Report.
- **Low** — likely false positive, nitpick, insufficient context. **Discard silently.**

The rules explicitly favor precision over recall: stay silent when context is unclear.
A false alarm costs more than a missed minor issue.

## Step 5 — Fix

Apply the High findings and any Medium findings that are clearly correct and low-risk.
List what you fixed vs. what you left for the user to decide.
