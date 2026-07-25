# Contributing to autoloop

Thanks for helping. This repo **is** a single Claude Code skill package — not an app with a build pipeline. `SKILL.md` is the skill Claude Code loads; everything else supports it.

## The one invariant: the harness is frozen

autoloop only works because the loop cannot influence its own grader. `scripts/check_stop.py` is **read-only ground truth** — the stop/continue decision has to be something the loop being evaluated can't edit, or every run will report it's still improving. The same discipline applies to the eval command and metric extraction described in `SKILL.md`.

So: **never make `check_stop.py` (or the frozen-evaluator rules in `SKILL.md`) depend on loop state.** Change the stopping *policy* deliberately and in the open; never make it gameable. This is the change most likely to be rejected if it slips.

## Dev setup

Work on it as a live checkout of the skill:

```bash
git clone https://github.com/sweekuh/autoloop.git ~/.claude/skills/autoloop
cd ~/.claude/skills/autoloop
```

Requirements: `git` and `python3` (no third-party packages — the scripts are stdlib only, on purpose).

## Checks before you open a PR

One command runs everything CI runs:

```bash
python3 tests/check.py
```

It compiles both helpers, runs `check_stop.py` against the fixtures in `tests/fixtures/`, runs `update_check.py --check-only`, verifies both machine-readable listings parse and stay in sync with the prose cases, and guards the pure-ASCII output invariant. Exit code 0 means everything passed. CI runs exactly this on Linux, macOS, and Windows (`.github/workflows/checks.yml`).

To run a piece by hand:

```bash
# check_stop.py the way a live loop invokes it (fixtures stand in for a real run)
python3 scripts/check_stop.py --config tests/fixtures/loop_config.json --results tests/fixtures/results.tsv
#    -> {"stop": bool, "reason": str, "stats": {...}}

# update_check.py (works from any path — it derives its own skill dir)
python3 scripts/update_check.py --check-only
#    -> a human line, then {"status": ..., "action": ...}
```

`evals/evals.json` is **not** run by `tests/check.py` or CI — it needs an LLM grading harness (skill-creator). CI only validates that it parses and is internally consistent.

Script output stays **pure ASCII** so it can't `UnicodeEncodeError` on a non-UTF-8 console somewhere in the world.

## Testing behavior changes

Two eval listings, and they are not 1:1 — see `CLAUDE.md` for the details:

- **`evals/evals.json`** — the skill-creator automated suite (assertions per eval).
- **`TEST_PLAN.md`** — prose cases for running the skill by hand in Claude Code.

If you change loop behavior, add or update a case in the one that fits, and say in your PR which you ran.

## Keep the docs honest

`SKILL.md`, `README.md`, and `CLAUDE.md` describe the same skill from three angles (the agent's instructions, the user's intro, the maintainer's map). If you change what the skill does, update all three so they don't drift.

## Pull requests

- Keep them focused — one behavior change per PR.
- In the description, say **what loop behavior changes** and **why**, not just what files moved.
- Small helper scripts stay stdlib-only and cross-platform (macOS / Linux / Windows).

## Reporting bugs & sharing runs

Use the [issue templates](.github/ISSUE_TEMPLATE/). A shared `loop_config.json` + the relevant `results.tsv` rows makes almost any report actionable. Runs where a **counter-metric gate correctly bit** are especially valuable — they show the discipline working.
