# autoloop

A [Claude Code](https://docs.claude.com/en/docs/claude-code) skill that optimizes anything you can score with a command. It changes an artifact, runs your evaluation, keeps what improves the number, reverts what doesn't, and stops on its own when progress flattens.

It generalizes [karpathy/autoresearch](https://github.com/karpathy/autoresearch), which runs that loop over single-GPU nanochat training. autoloop drops the ML-specific parts, so the artifact can be a sort function, a prompt, a config, a query, or anything else with a benchmark, test suite, or validator behind it.

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
- **Phase 1, contract.** Writes `loop_config.json`, creates the `autoloop/<run_tag>` branch and `results.tsv`, waits for your sign-off.
- **Phase 2, baseline.** Evaluates the artifact untouched, and calibrates the counter-metric thresholds against what it measures.
- **Phase 3, loop.** Each round proposes candidates, evaluates them with the frozen harness, keeps at most one, and logs all of them including the failures.
- **Phase 4, report.** Baseline against best, the ordered list of kept commits so the win can be reproduced without rerunning the search, which gates blocked which candidates, and whether more search is worth paying for.

`scripts/check_stop.py` decides when to stop and the loop obeys it. Three conditions, first to fire wins: patience (N rounds with no keep), epsilon (gain across a trailing window drops below a threshold), and max_rounds. It lives in a separate script for the same reason the evaluator is frozen. Having just generated the ideas, the loop always feels one change away from a breakthrough.

One candidate per round is the default. Set `lambda` above 1 and each candidate gets its own git worktree, which buys wall-clock time and costs sample efficiency, since candidates in the same round can't learn from each other's results.

## Requirements

- [Claude Code](https://docs.claude.com/en/docs/claude-code)
- `git`, because every trial is a commit and being able to revert is load-bearing
- `python3`, for `check_stop.py` and the update check. No third-party packages.

## Install

```bash
git clone https://github.com/sweekuh/autoloop.git ~/.claude/skills/autoloop
```

On Windows that path is `%USERPROFILE%\.claude\skills\autoloop`.

Claude Code then picks it up as the `autoloop` skill. Clone it rather than copying the files in, because the skill updates itself through that checkout.

## Usage

Run `/autoloop`, or just describe what you want optimized. It triggers on phrasings like "optimize X until it plateaus", "hill-climb on this", or "try a bunch of variants and keep the best". It walks you through Phase 0, gets your sign-off on the config, then runs unattended until the stopping rule fires. The branch, `results.tsv`, `run.log`, and `loop_config.json` stay where they are as the audit trail.

## Self-updating

Before each run the skill runs `scripts/update_check.py`. If your checkout is cleanly behind its tracking branch, it fast-forwards and carries on, so an overnight run doesn't start on a version missing a bugfix.

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
tests/check.py           mechanical checks, same ones CI runs
tests/fixtures/          sample config and results for check_stop.py
evals/evals.json         eval suite, skill-creator format
TEST_PLAN.md             cases for running the skill by hand
```

## Contributing

Issues are welcome, especially runs that went badly. If you have used it on something, the most useful thing to post is your `loop_config.json` plus the rows of `results.tsv` around whatever went wrong. Runs where a counter-metric gate correctly blocked a "winning" candidate are worth sharing too, since they show the gates earning their place.

[CONTRIBUTING.md](CONTRIBUTING.md) covers the dev loop, the pre-PR checks, and the one invariant that cannot break: the harness stays frozen.

## License

MIT, see [LICENSE](LICENSE).
