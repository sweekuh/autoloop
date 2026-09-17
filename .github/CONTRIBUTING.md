# Contributing to autoloop

Thanks for helping. This repo **is** a single Claude Code skill package — not an app with a build pipeline. `SKILL.md` is the skill Claude Code loads; everything else supports it.

## The one invariant: the harness is frozen

autoloop only works because the loop cannot influence its own grader. `scripts/run_trial.py` (runs the eval, extracts the numbers), `scripts/adjudicate.py` (keep / discard / gate_fail / crash), and `scripts/check_stop.py` (stop / continue, and an audit of the log) are **read-only ground truth** — those decisions have to be something the loop being evaluated can't edit or make for itself, or every run will report it's still improving. The agent never writes a status label; it appends what the scripts print.

So: **never make those three scripts (or the frozen-evaluator rules in `SKILL.md`) depend on loop state.** Change the stopping or keep *policy* deliberately and in the open, and only in the direction that can end a run earlier (the one accepted exception: a keep the audit rejects no longer satisfies `target`); never make it gameable. This is the change most likely to be rejected if it slips.

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

It compiles all five helpers, runs `check_stop.py` against the fixtures in `tests/fixtures/` (including the audit and epsilon cases), drives `run_trial.py` and `adjudicate.py` through a three-round log on the tiny `tests/fixtures/trialproj` eval, runs `update_check.py --check-only`, runs `log_run.py` for real against a throwaway snapshot and asserts the checkout stays clean, runs the toy benches under `tests/toy/`, verifies both machine-readable listings parse and stay in sync with the prose cases, and guards the pure-ASCII output invariant. Exit code 0 means everything passed. CI runs exactly this on Linux, macOS, and Windows (`.github/workflows/checks.yml`).

To run a piece by hand:

```bash
# check_stop.py the way a live loop invokes it (fixtures stand in for a real run)
python3 scripts/check_stop.py --config tests/fixtures/loop_config.json --results tests/fixtures/results.tsv
#    -> {"stop": bool, "reason": str, "stats": {...}, "warnings": [...]}

# run_trial.py on the tiny fixture eval (AUTOLOOP_FIXTURE_MODE=sleep|crash|gate for the other outcomes)
(cd tests/fixtures/trialproj && python3 ../../../scripts/run_trial.py --config loop_config.json)
#    -> {"ok": bool, "primary": ..., "counters": {...}, "timed_out": bool, "tail": [...]}

# update_check.py (works from any path — it derives its own skill dir)
python3 scripts/update_check.py --check-only
#    -> a human line, then {"status": ..., "action": ...}
```

`evals/evals.json` is **not** run by `tests/check.py` or CI — it needs an LLM grading harness (skill-creator). CI only validates that it parses and is internally consistent.

Script output stays **pure ASCII** so it can't `UnicodeEncodeError` on a non-UTF-8 console somewhere in the world.

## Testing behavior changes

Two eval listings, and they are not 1:1 — see [`CLAUDE.md`](../CLAUDE.md) for the details:

- **`evals/evals.json`** — the skill-creator automated suite (assertions per eval).
- **`tests/TEST_PLAN.md`** — prose cases for running the skill by hand in Claude Code.

If you change loop behavior, add or update a case in the one that fits, and say in your PR which you ran.

## Keep the docs honest

`SKILL.md`, `README.md`, and `CLAUDE.md` describe the same skill from three angles (the agent's instructions, the user's intro, the maintainer's map). If you change what the skill does, update all three so they don't drift.

## Pull requests

- Keep them focused — one behavior change per PR.
- In the description, say **what loop behavior changes** and **why**, not just what files moved.
- Small helper scripts stay stdlib-only and cross-platform (macOS / Linux / Windows).

## Reporting bugs & sharing runs

Use the [issue templates](ISSUE_TEMPLATE/). A shared `loop_config.json` + the relevant `results-<run_tag>.tsv` rows makes almost any report actionable. Runs where a **counter-metric gate correctly bit** are especially valuable — they show the discipline working.
