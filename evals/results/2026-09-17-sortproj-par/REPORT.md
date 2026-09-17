# autoloop run e2e-par: sortproj, parallel mode (3 candidates per round)

Date 2026-09-17. Skill commit b163683; update check up-to-date (--check-only,
so the skill checkout was never modified). Target: fresh copy of
tests/toy/sortproj in /tmp/e2e-par/sortproj, branch autoloop/e2e-par. No human
present: the Phase 1 confirmation was taken from the task values. No Agent
tool: each round's three worktrees were evaluated one after another with
run_trial.py --cwd (same harness path, no wall-clock gain).

## Result

- runtime_ms (min): baseline 1335.8 -> best 19.5, delta -1316.3 ms (-98.5%)
- tests_passed (gate >= 42): baseline 42, best 42; never below 42 in any trial
  that produced output
- min_delta 10.0 ms, grounded in Phase 2 from two baseline runs (1335.8 and
  1344.7, spread 8.9 ms = 0.67% of baseline) and committed before round 1
- rounds 0-7, 22 rows: baseline + 4 keeps, 16 discards, 0 gate_fail, 1 crash
- Ledger row appended to runs/local/RUNS.tsv as "toy sort pipeline, 3 per
  round" (log_run.py says rounds=7, check_stop.py says rounds=8; Friction 8)

## Stop reason (verbatim from check_stop.py)

max_rounds reached (8/8)

Full verdict in verdict.json. rounds_since_keep was also 3 = patience at that
moment; max_rounds is checked first, so the run is filed under it.

## Recipe (kept commits in order; branch sha / sha logged in the results file)

1. b405b22 / 040dd75  stable built-in sorted() keyed on record.key instead of
   insertion sort                                         1335.8 -> 544.3
2. 3515104 / e9aec53  dedupe with a set instead of a list membership scan
                                                          544.3 -> 166.7
3. e5740ed / 3ee0627  format_records appends one built string instead of
   out = out + [line]                                     166.7 -> 72.0
4. e25bca9 / 847e549  split-based parse_line (str.split + precompiled key
   regex + str checks)                                    72.0 -> 19.5

Setup commits: 9d25ff2 (contract), 0ee4912 (min_delta). Full log: branch.txt.

## Gate failures

None. tests_passed stayed at 42 for all 20 candidates that produced output.
The only candidates that traded something away (5-3, 6-1, 7-1, 7-2: plain
tuples instead of the unorderable Payload class) traded an internal safety
property that no test pins, so the gate could not see it. None was kept.

## Discard themes

- Lost to a bigger sibling in the same round (7 of 16 discards: 1-2, 1-3, 2-2,
  3-1, 3-3, 4-1, 4-3). Valid improvements, not dead ends; re-proposing them
  later produced 3 of the 4 keeps.
- Real gains under the absolute noise floor once runtime reached ~20 ms
  (5-1 17.7, 5-2 17.5, 5-3 16.1, 6-1 15.0, 7-1 16.0, 7-2 13.0): each beats
  best-so-far 19.5 by 9-33% but not by 10 ms.
- No measurable effect (6-2 str-method key check 20.1, 6-3 fused pass 20.0,
  7-3 revert to the regex parser 18.3): inside noise of 19.5.
- Bulk one-pass regex over the joined input (7-1) was no faster than per-line
  str.split: per-line tuple/float construction dominates, not matching.
- Crash: 2-3 (hand-rolled scanner whose digit loop never advanced) hit the
  120 s timeout, was killed by run_trial.py and filed as crash; round 2 still
  kept 2-1, so the crash row did not sink its round.

## Budget verdict

Not under this config. min_delta 10 ms is now 51% of the best value, so no
single change can be credited; the best explored combination (7-2, 13.0 ms)
is 33% faster than the branch head and uncreditable. One short follow-up run
with min_delta re-grounded at the new scale (two runs of the current head,
expect ~1 ms) would bank 13.0 ms in its round 1; the floor is close after that.

## check_stop.py warnings

[]  (empty on every round; 0 warnings for the run)

## Friction (parallel mode)

1. No worktree command is given and the obvious one fails. SKILL.md: "give
   every candidate its own `git worktree`". `git worktree add ../wt
   autoloop/e2e-par` -> "fatal: 'autoloop/e2e-par' is already used by
   worktree at '/tmp/e2e-par/sortproj'". Did: `git worktree add --detach
   ../wt-R-i HEAD`. SKILL.md should spell that out.
2. `git worktree remove` refuses every time. SKILL.md: "Remove the round's
   worktrees (`git worktree remove`)". Actual: "fatal: '../wt-1-1' contains
   modified or untracked files, use --force to delete it", because the eval
   writes run.log (and __pycache__) into the worktree. Did: --force, 21 times.
3. Logged commit shas are not on the branch. SKILL.md: "merge that worktree's
   diff onto the branch and commit". The results file records the worktree sha
   (040dd75); the branch has the cherry-pick (b405b22). After remove + prune
   the logged shas dangle and will be gc'd, so the Recipe cannot be rebuilt
   from the log alone. Did: cherry-pick, list both shas above. Fix: `git merge
   --ff-only <keep_commit>` (the candidate commit is a child of HEAD) keeps
   the sha; or keep refs/autoloop/<tag>/<round>-<i> for every candidate.
4. Combination candidates have no durable source. SKILL.md suggests "a
   combination of two prior near-misses" but also "only kept diffs live on the
   branch". 7-1 and 7-2 were built with `git cherry-pick --no-commit aacb1bd`,
   a dangling commit of a removed worktree; that works only until gc. Same
   fix as 3.
5. "Otherwise reset the branch to the last kept commit" has nothing to reset
   in parallel mode: candidate commits sit on detached worktrees and the
   branch never moves during a round. Did: verified sorter.py clean vs HEAD.
   Mark it sequential-only.
6. An absolute min_delta does not survive a 68x improvement. Phase 2: "set
   `min_delta` ... to at least the spread between the two runs". 8.9 ms was
   0.67% of baseline; at 19.5 ms the same floor is 51%, and rounds 5-7
   discarded six candidates that were 9-33% faster than the head. The frozen
   config cannot be re-grounded mid-run. Did: obeyed. Needs a relative option
   (e.g. min_delta_pct) or a documented re-baseline step. Biggest finding.
7. The derived epsilon breaks the skill's own rule. Phase 1: "A number you do
   set must be at least 2x `min_delta`", yet epsilon null derives
   epsilon_effective = 0.5% of baseline = 6.679 ms < 2 x 10 ms (true whenever
   min_delta > 0.25% of baseline). Also epsilon_window 10 with max_rounds 8
   can never fire (needs rounds > window). Harmless here, still wrong.
8. max_rounds counts the baseline. "max 8 rounds" gave 7 search rounds;
   check_stop counts round 0 ("max_rounds reached (8/8)"), while log_run.py
   logs rounds=7 (highest round label) for the same run. Pick one meaning.
9. `discard` conflates "worse" with "lost to a sibling". Phase 0: "Candidate
   descriptions with `discard` ... are things already tried here. Do not
   spend budget rediscovering them." Read literally that discards set-dedupe,
   format-append and the split parser, which became 3 of the 4 keeps. Did:
   re-proposed anything whose primary beat best-so-far. adjudicate.py should
   annotate such rows, e.g. "discard (beat best-so-far; lost to 1-1)".
10. Sibling choice has no noise floor. Round 4 kept 4-2 (19.5) over 4-1
   (20.8), a 1.3 ms gap under min_delta: the parser choice was a coin flip
   made by the harness. Noted; a tie rule would need a design decision.
11. A timeout crash carries no diagnostics. Rule 3: "For a `crash`, read the
   `tail` in its trial JSON. Fix trivial breakage". For 2-3 the tail was only
   "run_trial: eval_command exceeded trial_timeout_seconds (120s) and was
   killed": bench had redirected to run.log and printed nothing before the
   kill. That trial cost 120 s, more than all other trials combined (~30 s).
   Did: let the row stand. Suggest sizing the timeout at ~10x baseline elapsed.
12. No fallback when subagents do not exist. "Spawn one subagent per
   candidate" is the only evaluation path described. Did: three sequential
   run_trial.py --cwd calls per round, so parallelism bought nothing, as the
   sub-second warning predicts. Unsaid: truly concurrent candidates on one VM
   contend for CPU, widening noise past a min_delta grounded sequentially.
13. `run_trial.py --config loop_config.json --cwd <worktree>` reads the config
   from the invoking directory, not the worktree. Identical here because the
   min_delta change was committed before round 1; an uncommitted edit would
   diverge silently. Say the config must be committed before worktrees exist.
14. No glue for the two mechanical steps of every round (candidates.json from
   N trial JSON lines; appending adjudicate.py's rows). I wrote two 10-line
   helpers. An `adjudicate.py --append` flag would remove the one place an
   agent touches the results file by hand.
15. Two concurrent runs from one skill checkout: the default update_check.py
   would fast-forward it while the other agent's loop is mid-run, against
   "the skill text is part of the frozen harness". Did: --check-only.
16. At patience 3 the explore threshold coincides with the stop, as SKILL.md
   admits; explores were run one barren round early (5-3, 6-1, 7-1..7-3).
