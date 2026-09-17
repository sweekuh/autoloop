# autoloop dogfood run: sortproj, run_tag e2e-seq (2026-09-17)

Skill checkout: /home/user/autoloop @ b163683. Target: /tmp/e2e-seq/sortproj
(copy of tests/toy/sortproj, spoilers deleted). Self-update check before
Phase 0: `autoloop update-check: up to date.` / status `up-to-date`, continued.
No human was present: the task's config values served as Phase 1 confirmation.

## Result

- primary `runtime_ms` (min): baseline 1319.3 -> best 73.3, delta -1246.0 ms
  (-94.4%). Best is the round-3 keep at commit f76c6f9.
- counter-metric `tests_passed` (>= 42): baseline 42, best 42. Every one of the
  8 trials in the run reported 42/42.
- min_delta used: 90.0 ms (two baseline runs through run_trial.py measured
  1319.3 and 1403.1, spread 83.8 ms = 6.35%; rounded up, committed as d9bd491
  before round 1).
- trial cost: 5.7 s wall-clock at baseline, 0.15-0.4 s once fast; no money.
- rounds: 8 (0..7) per check_stop.py; 3 keeps, 4 discards, 0 gate_fail,
  0 crash after baseline.

## Stop reason (verbatim from check_stop.py)

patience exhausted: 4 consecutive rounds with no improvement (patience=4)

## Recipe (kept commits on autoloop/e2e-seq, in order)

1. 3763497  replace O(n^2) insertion sort with stable sorted() keyed on key
            (1319.3 -> 491.4)
2. f1cfabd  dedupe: track seen idents in a set instead of a list (-> 181.0)
3. f76c6f9  format_records: append lines instead of quadratic out = out + [line]
            (-> 73.3)

## Gate failures

None. tests_passed never dropped below 42, so the counter-metric gate never
fired; no candidate tried to trade tests away.

## Discard themes

All four discards were genuine improvements rejected only by the noise floor:

- r4 aab5434  18.3 ms  parse_line: precompiled regex, drop revalidation loop
- r5 a86c3c4  60.2 ms  sort key via operator.attrgetter
- r6 d8421d3  67.5 ms  format_records as a comprehension
- r7 deb9008  16.9 ms  explore: r4+r5+r6 combined

After round 3 best-so-far (73.3) was already below min_delta (90.0), so no
candidate could satisfy `gain >= min_delta` no matter what it measured. The
theme is therefore not "these ideas fail"; it is "an absolute noise floor
grounded at a 1319 ms baseline was applied to a 73 ms artifact". The single
biggest win of the run (parse_line, a 4x step) is not on the branch.

## Budget verdict

With this config: no, provably; every further round is a discard by
arithmetic. With a relative min_delta (say 10% of best-so-far): yes, clearly.
The 16.9 ms 42/42 version exists only as dangling commit deb9008 in the scratch
repo (unreachable after the reset; gc will delete it). Start a follow-up there.

## check_stop.py warnings

warnings: [] (zero warnings on every round including the final verdict).

## Ledger

log_run.py appended one row to /home/user/autoloop/runs/local/RUNS.tsv:
2026-09-17 / toy sort pipeline / runtime_ms min / rounds 7 / candidates 7 /
keeps 3 / discards 4 / gate_fails 0 / crashes 0 / 1319.3 -> 73.3 / +94.4%.

## Friction

F1. Absolute min_delta on a proportional-noise metric (the run's defining
    problem). Phase 2: "set `min_delta` in `loop_config.json` to at least the
    spread between the two runs, then commit that change before round 1."
    Timing noise scales with runtime; the floor does not. Once best-so-far fell
    below 90 ms a keep became impossible, and rounds 4-7 discarded 18.3, 60.2,
    67.5 and 16.9 ms candidates with 42/42 tests. Neither adjudicate.py nor
    check_stop.py warns when min_delta >= best-so-far, and SKILL.md ("The loop
    reads this file and never deviates from it") gives no escape, so I did not
    deviate. Fix: allow min_delta as a fraction of best-so-far, or at minimum
    have check_stop.py emit a warning "min_delta 90 >= best 73.3: no keep is
    possible" so the report cannot hide it.

F2. Two samples is a poor noise estimate. Same sentence as F1. Two draws from
    ~10% jitter can land 0-20% apart; I got 6.35% and had to guess how far
    above "at least the spread" to go (chose 90). SKILL.md says nothing about
    sample count, relative vs absolute, or where to record the second run:
    it produces no results row, so the only record of the spread is my commit
    message. What I did: recorded both numbers in the commit message.

F3. The derived epsilon contradicts SKILL.md's own rule. Phase 1: "A number
    you do set must be at least 2x `min_delta`, or ... the condition never
    fires." With epsilon null, check_stop derived epsilon_effective = 6.5965
    (0.5% of baseline) against min_delta 90: every keep clears it, so it can
    only fire after 10 keepless rounds, which patience=4 always pre-empts.
    Dead rule, no warning. The null-derivation should respect the 2x rule or
    check_stop should say the condition is inert.

F4. Prior-run scan is broken (verified). Phase 0: `git log --oneline
    autoloop/* 2>/dev/null`. With branch autoloop/e2e-seq present this printed
    nothing and exited 0 (git treats the unexpanded glob as a pathspec).
    `git log --oneline --branches='autoloop/*'` lists it. As written the skill
    always concludes there are no prior runs. I ran the scan as written (empty
    project, so the result happened to be correct) and then probed it.

F5. Two round counts for one run. check_stop.py reports `"rounds": 8` (round 0
    counted); log_run.py logs `rounds 7` (max round label). The user's "max 12
    rounds" became max_rounds 12, which is 11 mutating rounds because the
    baseline counts. SKILL.md Phase 0 never says whether round 0 is a round.

F6. Discards become dangling commits. Phase 3 step 2: "Otherwise reset the
    branch to the last kept commit." After `git reset --hard` the four discard
    shas in results-e2e-seq.tsv (including the best code of the run, deb9008)
    are unreachable and will be gc'd, so the audit trail's commit column rots.
    Suggest a ref per candidate (refs/autoloop/<tag>/r<N>) or at least a note.

F7. Baseline's commit is "-". Phase 2 prescribes `"commit": "-"` for round 0,
    but the first discard must "reset the branch to the last kept commit",
    which the log cannot name. I tracked HEAD before each candidate commit
    (d9bd491, the min_delta commit) out of band.

F8. "Read its `tail` only when a trial crashed." run_trial.py always prints
    `tail` inside the one JSON line the agent must read, so the tail is read
    every trial (harmless here: 3 lines); the instruction cannot be followed.

F9. Ledger ordering contradiction. "After delivering the report, append ..."
    and, three sentences later, "Mention the row in the final report". The row
    cannot be mentioned in a report delivered before it exists. I logged first
    and then wrote this report.

F10. `--stop "<stop reason, short>"` vs Phase 4 "verbatim from check_stop.py".
    Used the 76-char verbatim reason per the task; wide in the README table.

F11. Explore gets exactly one shot at patience 4. "last patience / 2 rounds
    (floor, minimum 3)" = 3, so the explore round is round 7, the same round
    whose discard exhausts patience. It ran (deb9008, 16.9 ms) and could not be
    built on. SKILL.md only warns about patience <= 3; at 4 the explore round
    is also the final round.

F12. Phase 0 wants "Trial cost: wall-clock" and Phase 1 needs a timeout, but
    Phase 2 says round 0 runs "through the harness, never by hand", so nothing
    sanctioned measures cost before the contract. I estimated from the code.

F13. allowed-tools pre-approves only `python3 ${CLAUDE_SKILL_DIR}/scripts/*`;
    every git commit/reset and results append would prompt outside auto mode.

No script crashed, hung, or produced a malformed row; F1, F3 and F5 are
harness design gaps, F4 is a SKILL.md bug, the rest are text ambiguities.
