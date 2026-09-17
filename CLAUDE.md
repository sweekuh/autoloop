# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## What this repository is

This repo *is* a single Claude Code skill package named `autoloop` — it's not an application with its own build/test pipeline. `SKILL.md` is the skill definition Claude Code loads; everything else supports it.

```
SKILL.md                 skill definition (YAML frontmatter + instructions)
scripts/run_trial.py     frozen: runs one trial under trial_timeout_seconds and extracts the metrics (Phases 2-3)
scripts/adjudicate.py    frozen: turns a round's trial results into keep / discard / gate_fail / crash rows (Phases 2-3)
scripts/check_stop.py    frozen: stopping-rule arbiter; also audits the results file (Phase 3)
scripts/update_check.py  self-update check the skill runs before Phase 0 (fast-forwards a git checkout to its own upstream)
scripts/log_run.py       appends an anonymized run summary to the gitignored runs/local/RUNS.tsv (Phase 4); --publish targets runs/RUNS.tsv + README
runs/RUNS.tsv            published per-run ledger behind the README "Field results" table (maintainers, via --publish)
evals/evals.json         automated eval suite (skill-creator format)
tests/check.py           mechanical checks; what CI runs on Linux/macOS/Windows
tests/TEST_PLAN.md       human-run manual test plan (prose + a lightweight JSON index)
tests/fixtures/          sample configs and results for check_stop.py, plus trialproj/ (a tiny eval) for run_trial.py and adjudicate.py
tests/toy/               toy projects with a real plateau (sortproj, checkproj) for the test plan and the eval suite
.github/CONTRIBUTING.md  contributor guide (in .github/ because GitHub scans root, docs/, or .github/ only)
.github/workflows/       CI
```

Only four files sit at the repo root, each for a reason: `README.md` (what users read), `SKILL.md` (what Claude Code loads), `CLAUDE.md` (what Claude Code auto-loads when working in this repo), and `LICENSE` (GitHub detects the license from the root). Everything else lives under `scripts/`, `tests/`, `evals/`, `runs/`, or `.github/`.

## Commands

There's no build step or package manifest — the executable code is the five scripts under `scripts/`, all stdlib-only and pure ASCII.

- Syntax-check them: `python3 -m py_compile scripts/*.py`
- Run everything CI runs (about five seconds): `python3 tests/check.py`
- Run check_stop the way a live loop invokes it (a live run passes its own `--results results-<run_tag>.tsv`; here, point it at the fixtures): `python3 scripts/check_stop.py --config tests/fixtures/loop_config.json --results tests/fixtures/results.tsv` — prints a JSON verdict `{"stop": bool, "reason": str, "stats": {...}, "warnings": [...]}` to stdout.
- Run one trial the way Phase 3 does, against the tiny fixture eval: `cd tests/fixtures/trialproj && python3 ../../../scripts/run_trial.py --config loop_config.json` — prints one JSON line with `primary`, `counters`, `timed_out`, and a `tail`. Set `AUTOLOOP_FIXTURE_MODE=sleep|crash|gate` to see the other outcomes. (The fixture's commands say `python3` and `grep`; `tests/check.py` substitutes the running interpreter so the same fixture works on Windows.)
- Adjudicate a round: `python3 scripts/adjudicate.py --config loop_config.json --results results-<tag>.tsv --round N --candidates candidates.json` — prints the rows to append and which candidate, if any, to keep. `tests/check.py` section 11 shows a full three-round example.
- Run the update check the way SKILL.md invokes it: `python3 scripts/update_check.py` (add `--check-only` to report without fast-forwarding) — prints a human line then a JSON verdict `{"status": ..., "action": ...}`. It derives its own skill dir from `__file__`, so it is path-independent across installs. SKILL.md invokes every script as `python3 ${CLAUDE_SKILL_DIR}/scripts/<name>`; on Windows the interpreter is usually `python`.
- Validate the eval suite parses: `python3 -c "import json; json.load(open('evals/evals.json'))"`
- Dry-run the ledger logger against the fixtures (prints the row, writes nothing): `python3 scripts/log_run.py --config tests/fixtures/loop_config.json --results tests/fixtures/results.tsv --label smoke --dry-run`. Without `--dry-run` it appends to the gitignored `runs/local/RUNS.tsv`; only `--publish` touches `runs/RUNS.tsv` and the README, because a modified tracked file makes `update_check.py` report `behind-dirty` and stop auto-updating.

## Architecture

### The loop this skill drives runs elsewhere, not in this repo

`SKILL.md` implements a generalized keep/discard hill-climbing loop (Phase 0 qualify → Phase 1 setup contract → Phase 2 baseline → Phase 3 loop → Phase 4 report) over an artifact in whatever *other* project the user is working in. When invoked, it writes `loop_config.json` and `results-<run_tag>.tsv` into that target project and creates an `autoloop/<run_tag>` branch there — this repo only ships the skill definition and its frozen helper scripts.

### The frozen harness is three scripts — a running loop must never edit them

This is the load-bearing invariant of the whole skill (stated explicitly in each script's own docstring): every decision about a trial has to be read-only ground truth the loop being evaluated cannot influence, or every run will report that it's still improving. The agent never writes a status label; it only runs these and appends what they print.

- `scripts/run_trial.py` runs `eval_command` under `trial_timeout_seconds` (killing the process group on timeout), applies every `extract` pattern, and prints one JSON line with the primary, the counters, and a bounded `tail` for crash diagnosis. `ok` is false when the primary did not extract.
- `scripts/adjudicate.py` takes one round's trial JSON lines and prints the exact results rows: `crash` (timed out / did not extract), `gate_fail` (any counter-metric violation, or a counter that did not extract — an unevaluable gate is not a passed gate), then `keep` for the best survivor only if it beats best-so-far by the noise floor, `max(min_delta, min_delta_pct% of best-so-far)` (strictly better at a floor of 0), `discard` for the rest. Round 0 is the baseline and produces no row (and a reason) if it crashes or already violates a gate. It imports `check_stop.py` for the gate and min_delta rules and for best-so-far, so the two cannot disagree.
- `scripts/check_stop.py` decides stop/continue, and audits the log it reads: a `keep` row with no parseable primary, a second keep in a round, a gate violation, or a gain below the noise floor is listed in `warnings` and treated as no keep. The floor is relative on purpose (`min_delta_pct`): the first dogfood run grounded an absolute 90 ms floor at a 1319 ms baseline and then discarded a 16.9 ms candidate against a 73 ms best, because nothing could ever clear 90 ms again; an absolute floor that blocks every keep on a `min` metric is now warned about. That can only end a run earlier (an unearned keep would reset patience), which is the same argument that let `target` in. A primary without a direction is refused with `stop: true` rather than defaulting to `min`.

Implementation details that matter if you touch `check_stop.py`:

- It groups results-file rows by the `round` column, not raw candidate rows — with `candidates_per_round > 1` a round has several candidate rows but at most one `keep`, so counting raw rows would make `patience` fire once per candidate instead of once per round.
- `epsilon: null` (the template default) means the larger of 0.5% of the baseline value and twice the noise floor at best-so-far, derived at verdict time and reported as `stats.epsilon_effective`. The old template value of 0.001 in metric units could never fire: with `patience` 8 below the 10-round window and any `min_delta` >= 0.001, a window gain under 0.001 needs zero keeps, which patience catches first. An explicit number is used as given; 0 disables.
- The four stop conditions are checked in this order, first to fire wins: `max_rounds` (hard cap) → `target` (best-so-far has reached the declared goal value; optional, `null` by default) → `patience` (consecutive keepless rounds) → `epsilon`/`epsilon_window` (diminishing returns over the trailing window). `target` sits before `patience` on purpose: once a bounded metric is at its goal, no further round can improve it, and a finished run must not be filed under the same stop reason as a stalled one. `max_rounds` and `patience` are also checked **before** the no-parseable-keep early return, so a run that only crashes (or whose primary column is malformed) still terminates instead of looping unbounded.
- It's backward compatible on purpose: falls back to the legacy `metric` column name if `primary` is absent, and treats each row as its own round if the results file has no `round` column.

### Two eval listings that look redundant but aren't

- `evals/evals.json` — the skill-creator-format automated suite: contiguous 0-indexed `id`s, each eval with a non-empty `assertions` array (`mechanical`, `judgment`, or `manual` type); `tests/check.py` enforces both. This is what an automated grading run consumes. It needs an LLM grading harness, so CI cannot run it; CI runs the mechanical checks instead (see `.github/workflows/checks.yml`).
- `tests/TEST_PLAN.md` — 1-indexed prose cases (`Case 1`, `Case 2`, ...) for a human running the skill manually in Claude Code, plus its own trailing "Machine-readable" JSON block — a different, lighter schema (no assertions) that indexes the prose cases one-to-one, not the same data as `evals/evals.json`.

Three behaviors are deliberately covered by both listings, one prose case mirroring one eval: self-update (`TEST_PLAN.md` Case 6 ↔ `evals.json` id 4), the counter-metric gate biting unprompted (Case 2 ↔ id 5), and a bounded primary reaching its `target` (Case 7 ↔ id 6). Edit both sides when one of those behaviors changes.

The two files are still not 1:1, so don't assume they reconcile, e.g. when running skill-creator's benchmarking workflow against this skill: eval id 3 (parallel mode) is manual-only with no files, and Case 1 and Case 4 assert more than eval id 0 does (a nonzero `min_delta` grounded in a repeated baseline, worktree isolation, crash rows that don't sink a round). A case that exists in only one listing needs a new entry written before it can be run from the other.

### Toy problems live in `tests/toy/` and need a real plateau

`tests/toy/sortproj` (cases 1, 2, and 4: `runtime_ms` min with a `tests_passed` gate) and `tests/toy/checkproj` (case 7: a bounded `checks_passed` with `target` 10 and a `lint_errors` gate) are the toy projects `TEST_PLAN.md` and the eval suite assume; copy one into a scratch git repo and run the case prompt against it. sortproj's plateau is deliberate: four inefficiencies of decreasing cost, each guarded by tests a naive fix breaks (`naive_impl.py` proves it: faster, and 38 of 42 tests). `tests/check.py` runs both benches small on every CI platform. The spoilers are in `tests/toy/README.md`; never show that section to the agent running the loop. A toy problem that resolves in one round tests nothing.
