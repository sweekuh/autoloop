# autoloop

A [Claude Code](https://docs.claude.com/en/docs/claude-code) skill that optimizes anything you can score with a command. It changes an artifact, runs your evaluation, keeps what improves the number, reverts what doesn't, and stops on its own when progress flattens.

It generalizes [karpathy/autoresearch](https://github.com/karpathy/autoresearch), which runs that loop over single-GPU nanochat training. autoloop drops the ML-specific parts, so the artifact can be a sort function, a prompt, a config, a query, or anything else with a benchmark, test suite, or validator behind it.

## Field results

Aggregate stats from real runs, appended as they happen. Each row is one completed run; the trial-by-trial logs stay in their source projects.

<!-- autoloop-stats:start -->
3 run(s) logged - 14 kept, 2 discarded, 1 blocked by a counter-metric gate, 0 crashed. Median improvement in the primary metric: 56.5%.

| date | task | metric | baseline -> best | improvement | rounds | gate hits | stop |
|---|---|---|---|---|---|---|---|
| 2026-07-25 | mobile web load time | `load_ms` (min) | 18241 -> 3773 | +79.3% | 5 | 1 | interrupted by user (round 5 of 20) |
| 2026-07-25 | game level parity vs reference | `parity_passed` (max) | 23 -> 36 | +56.5% | 5 | 0 | primary hit its ceiling (36 of 36) |
| 2026-07-28 | long-form guide quality | `judge_median` (max) (judged) | 66 -> 94 | +42.4% | 7 | 0 | ended at round 7 of 8 |

Appended by `scripts/log_run.py` at the end of each run (SKILL.md Phase 4). Labels name the task shape, never the project; full trial logs stay in their source projects.
<!-- autoloop-stats:end -->

## The part that makes it work

A loop that tweaks and measures will happily report progress forever. Four properties stop that, and the skill checks all four before it starts:

1. **The evaluator is frozen.** Eval command, metric extraction, and stopping rule are fixed once the run begins. A loop that can edit its own grader will tell you every trial improved.
2. **One scalar metric, one direction.** `min` or `max` on a single number, not a weighted blend of three things you care about.
3. **Trials are cheap enough to run many times.** If one costs an hour or real money, you set a budget first.
4. **Mutations are isolated and revertible.** A named set of files under git, so any trial can be undone.

When a property is missing, the skill says which one and refuses to start. That is a real outcome, not a failure.

Every run also declares a counter-metric: something the loop is forbidden to make worse past a threshold. Test pass count, output validity, peak memory, cost per trial. These are gates rather than weights, because a search can trade away a weight and cannot trade away a gate. It is what keeps "make the sort faster" from turning into "make the sort faster by breaking it."

## How a run goes

- **Phase 0, qualify.** Goal, mutable paths, eval command, primary metric, counter-metrics, budget. This is where it refuses if the task doesn't fit.
- **Phase 1, contract.** Writes `loop_config.json`, creates the `autoloop/<run_tag>` branch and `results-<run_tag>.tsv`, waits for your sign-off.
- **Phase 2, baseline.** Evaluates the artifact untouched, and calibrates the counter-metric thresholds against what it measures.
- **Phase 3, loop.** Each round proposes candidates, evaluates them with the frozen harness, keeps at most one, and logs all of them including the failures.
- **Phase 4, report.** Baseline against best, the ordered list of kept commits so the win can be reproduced without rerunning the search, which gates blocked which candidates, and whether more search is worth paying for.

`scripts/check_stop.py` decides when to stop and the loop obeys it. Three conditions, first to fire wins: patience (N rounds with no keep), epsilon (gain across a trailing window drops below a threshold), and max_rounds. It lives in a separate script for the same reason the evaluator is frozen. Having just generated the ideas, the loop always feels one change away from a breakthrough.

One candidate per round is the default. Raise `candidates_per_round` above 1 and each candidate gets its own git worktree, which buys wall-clock time and costs sample efficiency, since candidates in the same round can't learn from each other's results.

## Requirements

- [Claude Code](https://docs.claude.com/en/docs/claude-code)
- `git`, because every trial is a commit and being able to revert is load-bearing
- `python3`, for `check_stop.py` and the update check. No third-party packages.

## Install

Either method works. They differ only in how updates reach you.

**With npx**, which also covers Cursor, Windsurf, Codex, and the other agents the `skills` CLI knows about:

```bash
npx skills add sweekuh/autoloop
```

That installs into the current project. Add `-g` to install for your user instead, and `npx skills update autoloop` to pull a newer version later.

**As a git checkout**, which is the version that keeps itself current:

```bash
git clone https://github.com/sweekuh/autoloop.git ~/.claude/skills/autoloop
```

On Windows that path is `%USERPROFILE%\.claude\skills\autoloop`. Claude Code then picks it up as the `autoloop` skill.

## Usage

Run `/autoloop`, or just describe what you want optimized. It triggers on phrasings like "optimize X until it plateaus", "hill-climb on this", or "try a bunch of variants and keep the best". It walks you through Phase 0, gets your sign-off on the config, then runs unattended until the stopping rule fires. The branch, `results-<run_tag>.tsv`, `run.log`, and `loop_config.json` stay where they are as the audit trail. The per-run filename means a later run in the same project can read what earlier ones tried.

## Self-updating

Before each run the skill runs `scripts/update_check.py`. If you installed it as a git checkout and that checkout is cleanly behind its tracking branch, it fast-forwards and carries on, so an overnight run doesn't start on a version missing a bugfix.

An `npx skills add` install is a copy rather than a checkout, so there is nothing for the check to fast-forward. It reports `not-git`, says so in a line, and continues. Update those installs with `npx skills update autoloop`.

Worth understanding what that means: it pulls, then follows the updated instructions. Whoever can push to the repo you cloned from can change what this skill does on your machine, and there is no signature check. If you would rather approve updates yourself, pin the checkout with `git checkout <sha>`. A pinned or detached checkout reports `no-upstream` and is never moved. Local edits and diverged history are left alone too, and the check never blocks a run on its own: offline, no git, or any error, it says so in a line and continues.

Update by hand any time:

```bash
git -C ~/.claude/skills/autoloop pull --ff-only
```

## Repo layout

```
SKILL.md                 the skill definition Claude Code loads
scripts/check_stop.py    frozen stopping-rule arbiter
scripts/update_check.py  update check that runs before Phase 0
scripts/log_run.py       appends an anonymized run summary to runs/RUNS.tsv
runs/RUNS.tsv            the run ledger behind the Field results table
tests/check.py           mechanical checks, same ones CI runs
tests/TEST_PLAN.md       cases for running the skill by hand
tests/fixtures/          sample config and results for check_stop.py
evals/evals.json         eval suite, skill-creator format
```

## Contributing

Issues are welcome, especially runs that went badly. If you have used it on something, the most useful thing to post is your `loop_config.json` plus the rows of `results-<run_tag>.tsv` around whatever went wrong. Runs where a counter-metric gate correctly blocked a "winning" candidate are worth sharing too, since they show the gates earning their place.

[CONTRIBUTING.md](.github/CONTRIBUTING.md) covers the dev loop, the pre-PR checks, and the one invariant that cannot break: the harness stays frozen.

## License

MIT, see [LICENSE](LICENSE).
