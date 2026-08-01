# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## What this repository is

This repo *is* a single Claude Code skill package named `autoloop` — it's not an application with its own build/test pipeline. `SKILL.md` is the skill definition Claude Code loads; everything else supports it.

```
SKILL.md                 skill definition (YAML frontmatter + instructions)
scripts/check_stop.py    frozen stopping-rule arbiter the skill shells out to
scripts/update_check.py  self-update check the skill runs before Phase 0 (fast-forwards a git checkout to its own upstream)
evals/evals.json         automated eval suite (skill-creator format)
tests/check.py           mechanical checks; what CI runs on Linux/macOS/Windows
tests/TEST_PLAN.md       human-run manual test plan (prose + a lightweight JSON index)
tests/fixtures/          sample loop_config.json + results.tsv so check_stop.py is runnable
.github/CONTRIBUTING.md  contributor guide (in .github/ because GitHub scans root, docs/, or .github/ only)
.github/workflows/       CI
```

Only four files sit at the repo root, each for a reason: `README.md` (what users read), `SKILL.md` (what Claude Code loads), `CLAUDE.md` (what Claude Code auto-loads when working in this repo), and `LICENSE` (GitHub detects the license from the root). Everything else lives under `scripts/`, `tests/`, `evals/`, or `.github/`.

## Commands

There's no build step or package manifest — the executable code is `scripts/check_stop.py` and `scripts/update_check.py`.

- Syntax-check them: `python3 -m py_compile scripts/check_stop.py scripts/update_check.py`
- Run check_stop the way a live loop invokes it (a live run passes its own `--results results-<run_tag>.tsv`; here, point it at the fixtures): `python3 scripts/check_stop.py --config tests/fixtures/loop_config.json --results tests/fixtures/results.tsv` — prints a JSON verdict `{"stop": bool, "reason": str, "stats": {...}}` to stdout.
- Run the update check the way SKILL.md invokes it (SKILL.md says `python`, matching its pre-existing `check_stop.py` line; use whichever name exists on your machine): `python3 scripts/update_check.py` (add `--check-only` to report without fast-forwarding) — prints a human line then a JSON verdict `{"status": ..., "action": ...}`. It derives its own skill dir from `__file__`, so it is path-independent across installs.
- Validate the eval suite parses: `python3 -c "import json; json.load(open('evals/evals.json'))"`

## Architecture

### The loop this skill drives runs elsewhere, not in this repo

`SKILL.md` implements a generalized keep/discard hill-climbing loop (Phase 0 qualify → Phase 1 setup contract → Phase 2 baseline → Phase 3 loop → Phase 4 report) over an artifact in whatever *other* project the user is working in. When invoked, it writes `loop_config.json` and `results-<run_tag>.tsv` into that target project and creates an `autoloop/<run_tag>` branch there — this repo only ships the skill definition and its frozen helper script.

### `scripts/check_stop.py` is frozen — a running loop must never edit it

This is the load-bearing invariant of the whole skill (stated explicitly in the script's own docstring): the stop/continue decision has to be read-only ground truth the loop being evaluated cannot influence, or every run will report that it's still improving. Implementation details that matter if you touch this file:

- It groups results-file rows by the `round` column, not raw candidate rows — with `candidates_per_round > 1` a round has several candidate rows but at most one `keep`, so counting raw rows would make `patience` fire once per candidate instead of once per round.
- The three stop conditions are checked in this order, first to fire wins: `max_rounds` (hard cap) → `patience` (consecutive keepless rounds) → `epsilon`/`epsilon_window` (diminishing returns over the trailing window). Both hard caps are checked **before** the no-parseable-keep early return, so a run that only crashes (or whose primary column is malformed) still terminates instead of looping unbounded.
- It's backward compatible on purpose: falls back to the legacy `metric` column name if `primary` is absent, and treats each row as its own round if the results file has no `round` column.

### Two eval listings that look redundant but aren't

- `evals/evals.json` — the skill-creator-format automated suite: 5 evals, 0-indexed `id`s (0-4), each with a full `assertions` array (`mechanical`, `judgment`, or `manual` type). This is what an automated grading run consumes. It needs an LLM grading harness, so CI cannot run it; CI runs the mechanical checks instead (see `.github/workflows/checks.yml`).
- `tests/TEST_PLAN.md` — 6 prose cases (`Case 1`-`Case 6`, 1-indexed) for a human running the skill manually in Claude Code, plus its own trailing "Machine-readable" JSON block — a different, lighter schema (no assertions) that indexes the 6 prose cases, not the same data as `evals/evals.json`.

The self-update feature is the one place the two listings deliberately mirror each other (`TEST_PLAN.md` Case 6 and `evals.json` id 4 cover the same states). Edit both when that behavior changes.

`TEST_PLAN.md`'s Case 2 ("counter-metric gate must bite" — the skill must introduce a counter-metric unprompted) has no standalone counterpart in `evals/evals.json`; the closest thing is one assertion nested inside eval id 0. Don't assume the two files are 1:1 reconcilable, e.g. when running skill-creator's benchmarking workflow against this skill — Case 2 would need a new eval written first.

### Toy problems for test cases 1 and 4 need a real plateau

Per `TEST_PLAN.md`: the sort benchmark used to exercise the loop must be built so the obvious fix helps and further gains need 2-3 nonobvious steps (e.g. an O(n²) sort with a test suite covering stability/edge cases that a naive `sorted()` swap would break). A toy problem that resolves in one round tests nothing.
