# Toy problems for the test plan

`tests/TEST_PLAN.md` cases 1, 2, 4 and 7 (and `evals/evals.json` evals 0, 5 and 6) run
the skill against two small projects. These are those projects. They exist so
that the test plan is runnable, and so the repo has ground truth about whether
a loop stops for the right reason.

```
sortproj/    cases 1, 2, 4   make sorter.process() faster; the tests must keep passing
checkproj/   case 7          fix app.py until all 10 checks pass; lint must stay clean
```

Both are stdlib-only, pure ASCII, Python 3.8+. `tests/check.py` runs both
benches (small) on every CI platform, so they cannot silently rot.

## Using one

1. Copy the project directory, and nothing else, into a scratch git repo.
   Do not copy this README (the spoilers at the bottom are for you, not the
   agent) and delete `naive_impl.py` from the copy; its docstring gives a
   hint away.

   ```bash
   cp -r tests/toy/sortproj /tmp/sortproj
   cd /tmp/sortproj && rm naive_impl.py && git init -q && git add -A && git commit -qm "toy baseline"
   ```

2. Run the bench once by hand to see the baseline metric lines
   (`python3 bench.py`). The numbers quoted in the TEST_PLAN prompts are
   illustrative; say the real ones.

3. Start Claude Code in the scratch repo and paste the case's prompt.
   `loop_config.example.json` is a ready-to-copy `loop_config.json` for the
   case, if you would rather hand the skill a config than have it derive
   one. Rename it; the skill writes `loop_config.json`, not the example.

4. Afterwards, read `results-<run_tag>.tsv`, `loop_config.json` and
   `git log` against the case's "Must hold" list.

## What each bench prints

Every bench exits 0 whenever it printed its metric lines, because `run_trial.py`
files a non-zero exit as a crash: a failing check is reported through the metric
line and the counter-metric gate, never through the exit code. A missing primary
line is a crash too.

`sortproj/bench.py`:

```
runtime_ms: 1308.1        best of 3 timed process() calls, after one warmup
tests_passed: 42
tests_total: 42
FAIL <test_name>: <reason>   one per failing test (none at baseline)
crash: <error>               replaces runtime_ms when process() raises
```

`checkproj/bench.py`:

```
checks_passed: 6
checks_total: 10
lint_errors: 0
FAIL <check_name>: <reason>  one per failing check (four at baseline)
LINT line <n>: <rule>        one per lint error (none at baseline)
```

Environment variables, both for `sortproj/bench.py`:

- `AUTOLOOP_TOY_N` - input size. Default 10000, which takes roughly 1 to 4
  seconds per `process()` call on a laptop (about 1.3 s on the development
  box), so one eval is around 5 to 15 s. `tests/check.py` sets it to 400.
- `AUTOLOOP_TOY_IMPL` - module to import instead of `sorter`. The repo's
  checks use it to run `naive_impl` and prove the plateau is real. Never set
  it during a loop.

The wall-clock primary is noisy: on a shared VM three baseline runs spread
by about 10%. Calibrate `min_delta_pct` (never an absolute `min_delta`, which stops working once the metric has shrunk past it) from repeated baseline runs as SKILL.md
says; on a noisy machine the last one or two improvements will sit inside
the noise floor, and the loop stopping on `patience` there is the correct
outcome, not a failure.

## Contracts the agent is given

The agent sees only the project directory. `sorter.py`'s docstring is the
full contract for `process()`; `bench.py` holds the 42 tests that pin it and
is not a mutable path. `app.py`'s docstrings are the contracts for its ten
functions; `checkproj/bench.py` holds the checks and the lint rules. Both
benches are frozen for the run: only `sorter.py` or `app.py` may change.

---

## Spoilers (maintainers only; do not show this to the agent running a loop)

### sortproj: the plateau

`process()` is parse -> dedupe -> sort -> format. Every stage has an
independent inefficiency, and the costs drop by 2x or more at each step, so
a hill-climb gets one big win, then three smaller ones it has to profile
for. Measured at N=10000 on the development box (baseline about 1.3 s):

| stage  | inefficiency                                                          | cost   |
|--------|-----------------------------------------------------------------------|--------|
| sort   | hand-rolled insertion sort, O(n^2), on ~9100 unique records           | ~0.86 s |
| dedupe | `ident not in seen` against a growing *list*, O(n^2)                  | ~0.37 s |
| format | `out = out + [line]` rebuilds the output list per record, O(n^2)      | ~0.12 s |
| parse  | regex recompiled per line, each field re-validated with a fresh compile, ident rebuilt char by char against a per-line `list()` of allowed characters | ~0.07 s |

What a naive fix breaks, and which test catches it:

- `sorted(records)`: `Record` has no ordering, so it raises `TypeError`.
- `sorted()` over `(key, ident, payload)` tuples (this is `naive_impl.py`):
  equal keys fall through to ident order. Fails `equal_keys_keep_input_order`,
  `..._stress`, `dedupe_preserves_survivor_order`, `large_input_invariants`.
  Runtime drops to about 0.55 s and 38 of 42 tests pass.
- a key that includes the payload: `Payload` raises `TypeError` on any
  ordering comparison.
- set-based dedupe that keeps the last occurrence, or dedupes after sorting:
  `dedupe_keeps_first_even_if_later_key_smaller`, `dedupe_preserves_survivor_order`.
- a module-level `seen` set kept across calls: `repeat_calls_are_independent`.
- `line.split()` or `split(" ")` parsing: accepts double spaces, tabs, a
  trailing space with an empty payload, and `float()` accepts `nan`, `inf`,
  `1e3`, `+1`, `1.`, `.5`, `1_000`. Each has its own `rejects_*` test.
- `\d` instead of `[0-9]`: matches Unicode digits (`rejects_unicode_digit_key`);
  `\w` for the ident matches non-ASCII letters (`rejects_non_ascii_ident`).
- `re.match(...$)` instead of `fullmatch`: `$` matches before a trailing
  newline, so `"1 a1 x\n\n"` is accepted (`rejects_double_trailing_newline`).
- `.strip()` on the payload: `payload_verbatim_spaces`.
- parsing ints as `int` for speed: `"-0"` formats as `0.00` instead of
  `-0.00` (`key_formatting_two_decimals`).
- sorting the input list in place: `input_not_mutated`.

The correct end state (Python `sorted(key=...)`, dict or set dedupe keeping
the first, one precompiled regex with `fullmatch`, list append and join)
runs in a few tens of milliseconds at N=10000.

### checkproj: the bugs

Four of the ten functions in `app.py` fail their check at baseline:

- `slugify`: replaces each bad character with its own `-`; does not collapse
  runs or strip the ends. Fix: `[^a-z0-9]+` and `.strip("-")`.
- `parse_duration`: hours are multiplied by 360, not 3600.
- `merge_intervals`: never sorts the input, so unsorted intervals do not merge.
- `median`: even counts return the upper middle value instead of the mean.

The other six (`clamp`, `chunk`, `word_freq`, `is_palindrome`,
`is_leap_year`, `round_half_up`) are correct. A run therefore starts at
`checks_passed: 6` and should end the round it reaches 10 with
`check_stop.py` saying `target reached`, not `patience` rounds later.

Lint bait, all clean as shipped (`lint_errors: 0`):

- `re` is used only by `slugify` and `parse_duration`, both buggy: rewrite
  both without a regex and `re` becomes an unused import.
- one docstring line in `merge_intervals` is exactly 100 characters, the
  limit; widening it by one character trips "line too long".
- the obvious debugging move, a `print()`, is a lint error anywhere in the
  file, even inside a function that is never called.
