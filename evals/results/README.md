# Recorded runs

Ground truth for the skill: complete autoloop runs, with their trial logs, kept
so that a change to SKILL.md or the harness can be judged against what a real
run did rather than against what the docs say a run should do. Each directory
holds the run's final `loop_config.json`, its `results-<run_tag>.tsv`, the last
`check_stop.py` verdict, the branch log, and a REPORT.md whose "Friction"
section lists everything the agent running the loop found ambiguous, wrong,
or missing. That section is why these runs exist.

| run | target | mode | baseline -> best | stop reason | harness at |
|---|---|---|---|---|---|
| `2026-09-17-sortproj-seq` | `tests/toy/sortproj` | 1 candidate per round, patience 4, max 12 | 1319.3 -> 73.3 ms (-94.4%), 42/42 tests throughout | patience exhausted (4 barren rounds) | b163683 |
| `2026-09-17-sortproj-par` | `tests/toy/sortproj` | 3 candidates per round in worktrees, patience 3, max 8 | 1335.8 -> 19.5 ms (-98.5%), 42/42 tests, one deliberate timeout crash that did not sink its round | max_rounds reached (8/8) | b163683 |

Both were driven by an agent following SKILL.md with no human present, on a
shared VM with roughly 10% timing noise, and both ended with zero
`check_stop.py` warnings. They are the first recorded runs where the stopping
rule fired on its own.

They also found the flaw that the commit after them fixes: `min_delta` was an
absolute floor grounded at the baseline (90 ms and 10 ms respectively), and
once the artifact had shrunk past it no candidate could ever be kept. The
sequential run discarded a 16.9 ms candidate against a 73 ms best; the
parallel run discarded six candidates 9-33% faster than its head. `min_delta_pct`
(a floor relative to best-so-far) and the `check_stop.py` warning for a floor
that blocks every keep came from these two reports. Their configs therefore
predate that key.

To add a run: copy a toy project into a scratch git repo, run the skill, then
copy the five files here under `<date>-<project>-<mode>/`. Never edit a
results file; it is the audit trail.
