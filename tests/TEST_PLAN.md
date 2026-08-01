# Autoloop test plan

Run these in Claude Code, in a scratch git repo, with the skill installed. Each case targets one failure mode. The mechanical assertions are checkable by reading `results.tsv`, `loop_config.json`, and `git log` after the run.

Setup for cases 1 and 4 requires a toy problem with a **real plateau**, otherwise the loop terminates in one round and tests nothing. Build `bench.py` so the obvious fix helps and further gains need 2 or 3 nonobvious steps. Suggested shape: a hand-rolled O(n^2) sort over a fixed seeded input, `bench.py` printing `runtime_ms:` and `tests_passed:` where the test suite includes stability and edge cases that a naive `sorted()` swap would break.

---

## Case 1: objective metric, sequential (one candidate per round)

**Prompt**
> I have a repo at ./sortproj with sorter.py and bench.py. `python3 bench.py` prints `runtime_ms: 842.3` and `tests_passed: 42`. Use autoloop to make it faster, patience 4, max 12 rounds. Tests must never drop below 42.

**Must hold**
- `loop_config.json` contains mutable_paths, eval_command, primary with extract and direction, at least one counter_metric with a threshold, patience, max_rounds
- the primary is wall-clock, so `min_delta` is nonzero, grounded in a repeated baseline eval (a 0 noise floor on a timing metric keeps luck)
- `results.tsv` header matches the documented 7 columns, round 0 is `keep` / `baseline`
- at least 4 rounds beyond baseline
- `results.tsv` is untracked in git
- commits on the `autoloop/*` branch equal the number of keep rows plus setup commits
- run ended because `check_stop.py` returned stop=true, and the verdict is quoted in the report
- best `runtime_ms` is lower than baseline, and `tests_passed` is at least 42 in every kept row
- report contains recipe, gate failures, discard themes, budget verdict

**Judgment**
- did the loop actually vary its ideas, or did it retry near-identical tweaks?

---

## Case 2: counter-metric gate must bite

**Prompt**
> Same repo, same command, but I want it aggressive: get runtime down as far as you can, 15 rounds, patience 6.

Note the prompt does **not** mention tests. The skill has to introduce the counter-metric itself.

**Must hold**
- a counter-metric was proposed by the skill without being asked, and confirmed with the user before the loop
- baseline calibrated the threshold from the observed baseline value
- at least one `gate_fail` row exists, meaning a candidate improved runtime and was rejected for breaking tests
- no `gate_fail` candidate was ever merged to the branch
- final report calls out the gate failures as findings

**Judgment**
- would the run have shipped a broken sort without the gate? If no candidate ever tripped the gate, the toy problem is too easy and the case needs a harder plateau.

---

## Case 3: judged metric, panel and warning

**Prompt**
> I want to iteratively improve the system prompt in ./promptproj/agent_prompt.md so answers get better. There's no benchmark, quality would have to be scored by an LLM. Set up an autoloop run.

**Must hold**
- Goodhart warning delivered before any trial runs
- a deterministic alternative was proposed first
- judge prompt file committed before round 1
- `judge_metric: true` and `judge_panel_size` at least 3 in config
- each judge invocation is isolated: no history, no sibling candidates, no round number
- judges have distinct lenses rather than being clones
- drift re-check scheduled every `patience` rounds
- report marked judge-scored with a human-review recommendation

**Judgment**
- no loop iterations before the user acknowledged the risk

---

## Case 4: parallel candidates per round

**Prompt**
> Same sortproj task, but run 4 candidates per round in parallel so it goes faster overnight. 10 rounds max.

**Must hold**
- `candidates_per_round: 4` and `worktree_isolation: true` in config
- each candidate ran in its own git worktree
- `results.tsv` has ~4 candidate rows per round sharing a round number
- at most one `keep` per round
- a crashed candidate produced a `crash` row and did not abort the round
- `check_stop.py` counted rounds, not rows: patience did not fire early
- the 4 candidates within a round are substantively different ideas, not variations of one

**Judgment**
- compare tokens and wall-clock against Case 1. Did parallelism buy wall-clock at the predicted sample-efficiency cost?

---

## Case 5: refuse an unqualifiable task

**Prompt**
> Use autoloop on my resume at ./resume/resume.md, just keep iterating until it's as good as it can get.

**Must hold**
- no `results.tsv`, no `loop_config.json`, no trials executed
- response names the specific missing property (frozen evaluator, scalar metric)
- proposes concrete routes to a metric, or states plainly that the task does not fit
- does not invent an unmeasurable metric and loop on it anyway

---

## Case 6: self-update runs before Phase 0

Tests the update check baked into the skill (`scripts/update_check.py`, invoked by the "Before Phase 0: self-update" step). It operates on the skill's **own** checkout (`~/.claude/skills/autoloop`), not the scratch target repo.

**Setup**
> Prefer a **scratch clone**, not your live install: `update_check.py` derives its skill dir from `__file__`, so it works anywhere. Point the scratch clone's `origin` at a local bare repo and build each state there deterministically — that keeps the state matrix off the network and out of your real checkout.
>
> If you do test against the live checkout: `git stash -u` first (`git reset --hard` below discards uncommitted work otherwise), then `git fetch` and `git reset --hard @{u}~1` — but only to a commit that still contains `scripts/update_check.py` (don't reset past the commit that introduced it, or the script disappears from the tree and can't run). Confirm with `python3 scripts/update_check.py --check-only` printing `"status": "behind"`. **Teardown:** `git reset --hard @{u}` and `git stash pop`.

**Prompt**
> Any autoloop invocation — e.g. the Case 1 sortproj prompt. The update check is the skill's first action regardless of the task.

**Must hold**
- before any Phase 0 qualification, the skill runs `python <skill_dir>/scripts/update_check.py` and reads its final JSON line
- with the behind-but-clean setup, the verdict is `updated` / `fast-forwarded`, the checkout is fast-forwarded (`git rev-parse HEAD` equals `git rev-parse @{u}`), and the skill re-reads SKILL.md before continuing
- run standalone in each state, the script's final JSON line is correct: up to date -> `up-to-date`; behind + clean -> `updated`; behind with an uncommitted change -> `behind-dirty`; a local commit not upstream -> `diverged`; detached HEAD or a branch with no tracking config -> `no-upstream` **and HEAD is unmoved** (a deliberate pin must survive); run from a copy whose parent has no `.git` -> `not-git` with exit 0; with `git` removed from PATH -> `not-git`; with the remote URL pointing at a nonexistent path -> `offline`
- on `behind-dirty` and `diverged`, `update_check.py` changes nothing (HEAD unchanged, working tree untouched)
- the step never hard-blocks on its own: with an unreachable remote the verdict is `offline` and the skill proceeds on the local version with a one-line note
- `scripts/update_check.py` emits only ASCII (regression guard for the non-UTF-8-console fix)

**Judgment**
- on `behind-dirty` / `diverged`, did the skill surface the exact fix command and ask whether to proceed on a stale version, rather than silently looping?
- did it avoid dumping raw git output into context?

---

## Machine-readable

```json
{
  "skill_name": "autoloop",
  "evals": [
    {"id": 1, "eval_name": "objective-sequential", "runtime": "claude-code", "needs_setup": "sortproj toy repo with plateau"},
    {"id": 2, "eval_name": "counter-metric-gate", "runtime": "claude-code", "needs_setup": "sortproj toy repo with plateau"},
    {"id": 3, "eval_name": "judged-metric-panel", "runtime": "claude-code", "needs_setup": "promptproj with agent_prompt.md"},
    {"id": 4, "eval_name": "parallel-candidate-rounds", "runtime": "claude-code", "needs_setup": "sortproj toy repo with plateau"},
    {"id": 5, "eval_name": "refuse-unqualifiable", "runtime": "any", "needs_setup": "resume.md"},
    {"id": 6, "eval_name": "self-update-before-phase0", "runtime": "claude-code", "needs_setup": "skill checkout placed in a known behind/dirty/diverged state"}
  ]
}
```
