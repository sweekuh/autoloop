#!/usr/bin/env python3
"""Frozen stopping-rule arbiter for autoloop.

Reads loop_config.json and results-<run_tag>.tsv, prints a JSON verdict:
  {"stop": bool, "reason": str, "stats": {...}, "warnings": [...]}

Rounds, not candidates, are the unit of progress. With more than one candidate
per round, a single round contains several candidate rows and keeps at most
one, so counting raw rows would make patience fire once per candidate instead
of once per round.

Stop conditions, checked in this order, whichever fires first:
  1. max_rounds - hard cap
  2. target     - best-so-far has reached the declared goal value (optional)
  3. patience   - N consecutive rounds with no keep
  4. epsilon    - improvement in best-so-far over the last `epsilon_window`
                  rounds is below `epsilon` (metric units)

`target` is for a primary with a known bound - a pass count, a recall, a
percentage. Without it, a run that maxes out its metric still has to burn
`patience` rounds proposing candidates that provably cannot improve, because
none of the other three conditions can express "done". It is read from the
frozen config like `patience` and `max_rounds`, so it is exactly as far outside
the loop's reach as they are.

`epsilon` may be null (or absent). It then means the larger of 0.5% of the
baseline value and twice the noise floor at best-so-far, derived here and
reported as `epsilon_effective`, so a config written without a number still
gets a diminishing-returns stop and a single floor-sized keep inside the window
never reads as progress. An explicit number keeps its meaning; 0 disables the
condition.

max_rounds and patience are evaluated even when no keep row has ever parsed,
so a run that only crashes still terminates instead of looping unbounded.

The results file is audited, not trusted, and it is read as plain
tab-separated text with no CSV quoting, so no description can swallow the
rows after it. A `keep` row only counts as a keep
when it has a parseable primary, is the only keep in its round, violates no
declared counter-metric gate (the `counters` column is parsed as name=value
pairs and checked against `counter_metrics`), and beats the previous valid best
by at least the noise floor (strictly better when the floor is 0). Anything
else is listed in `warnings` and treated as no keep. That can only end a run
earlier, never later: an unearned keep would otherwise reset patience and hide
a stall. The baseline (the first valid keep) is exempt from the floor test.
A rejected keep still raises the bar a later candidate has to beat, because
dropping its value would make later rounds easier to keep - the one direction
this audit must never move.

Counter names are normalised the same way on both sides of the harness
(anything but letters, digits, `_`, `.` and `-` becomes `_`), so a name that
could not survive the `name=value` column can never make a gate unmatchable.

The noise floor is the larger of `min_delta` (metric units) and
`min_delta_pct` percent of the current best-so-far. The relative form exists
because an absolute floor grounded at the baseline stops working once the
metric has shrunk past it: a 90 ms floor measured at a 1300 ms baseline makes
every keep impossible once the artifact runs in 70 ms, and the run then
discards real wins until patience fires. When the floor is at or above
best-so-far on a `min` metric, a warning says so, because no candidate can be
kept from that point on.

A config whose primary has no direction is refused with stop=true, because a
guessed direction would keep the worst candidate instead of the best.

This script is part of the frozen harness. The looping agent must not edit it.
Backward compatible with a results.tsv that has no `round` column: each row is
then treated as its own round.
"""
import argparse
import csv
import json
import math
import re
import sys

COMPARATORS = {
    ">=": lambda v, t: v >= t,
    "<=": lambda v, t: v <= t,
    ">": lambda v, t: v > t,
    "<": lambda v, t: v < t,
    "==": lambda v, t: v == t,
}


def open_results(path):
    """The results file as a tab-separated reader with NO quoting semantics.

    csv's default QUOTE_MINIMAL treats a leading double quote as opening a
    quoted field, so one description starting with `"` would swallow every
    later row and hide it from the audit. Nothing writes quotes here:
    adjudicate.py strips them, and the columns are tab-separated.
    """
    # utf-8-sig: a BOM in the header would otherwise hide the `round` column
    # and turn every candidate row into its own round.
    f = open(path, newline="", encoding="utf-8-sig", errors="replace")
    return f, csv.DictReader(f, delimiter="\t", quoting=csv.QUOTE_NONE)


def parse_round(raw):
    """Round labels are integers, but tolerate integer-valued floats ('1.0').

    Anything else raises so the caller falls back to per-row rounds; silently
    accepting '1.5' as a round label would misgroup candidates.
    """
    v = float(str(raw).strip())
    if not v.is_integer():
        raise ValueError(raw)
    return int(v)


def clean_name(name):
    """A counter name as it can survive the counters column.

    The column is a `name=value` list split on commas, semicolons and
    whitespace, so a name containing any of those (or an `=`) could not be
    read back. Both sides of the harness normalise names the same way, so a
    gate is never silently unmatchable.
    """
    return "".join(ch if (ch.isalnum() or ch in "_.-") else "_" for ch in str(name).strip())


def parse_counters(raw):
    """'tests_passed=42, mem_mb=13' -> {'tests_passed': 42.0, 'mem_mb': 13.0}.

    Tokens without '=' or with a non-numeric value are dropped, so a gate whose
    value is missing is reported as not evaluable rather than as a violation.
    """
    out = {}
    if raw is None:
        return out
    for tok in re.split(r"[,;\s]+", str(raw).strip()):
        if "=" not in tok:
            continue
        name, _, val = tok.partition("=")
        try:
            v = float(val)
        except ValueError:
            continue
        if not math.isfinite(v):
            # `tests_passed=inf` would otherwise satisfy any >= gate.
            continue
        name = clean_name(name)
        if name in out and out[name] != v:
            # Two values for one gated counter: the gate cannot be evaluated,
            # so neither is used and the row fails the missing-counter rule.
            out[name] = None
            continue
        out.setdefault(name, v)
    return {k: v for k, v in out.items() if v is not None}


def load_gates(cfg):
    """[(name, comparator, threshold)] from counter_metrics.

    A missing comparator is derived from the counter's direction: a `max`
    counter must stay >= its threshold, a `min` counter <= it.
    """
    gates = []
    for cm in cfg.get("counter_metrics") or []:
        if not isinstance(cm, dict):
            continue
        name = cm.get("name")
        thr = cm.get("threshold")
        if name is None or thr is None:
            continue
        name = clean_name(name)
        cmp = cm.get("comparator")
        if cmp not in COMPARATORS:
            cmp = "<=" if cm.get("direction") == "min" else ">="
        try:
            gates.append((name, cmp, float(thr)))
        except (TypeError, ValueError):
            continue
    return gates


def gate_violations(counters, gates):
    """Strings describing each gate the given counters violate.

    A counter that is absent from `counters` is skipped: it cannot be shown to
    violate anything, and the caller decides whether to warn about that.
    """
    out = []
    for name, cmp, thr in gates:
        val = counters.get(name)
        if val is None:
            continue
        if not COMPARATORS[cmp](val, thr):
            out.append(f"{name}={val:.6g} fails {cmp} {thr:.6g}")
    return out


def improves(new, best, direction, floor):
    """True when `new` beats `best` in `direction` by at least `floor`.

    With a floor of 0 the candidate must still be strictly better: a tie is not
    an improvement, and treating it as one lets a run report jitter as progress.
    """
    gain = (best - new) if direction == "min" else (new - best)
    return gain > 0 and gain >= floor


def noise_floor(best, rules):
    """The margin a candidate must beat `best` by: max(min_delta, min_delta_pct% of |best|)."""
    rel = rules["min_delta_pct"] / 100.0 * abs(best) if best is not None else 0.0
    return max(rules["min_delta"], rel)


def floor_blocks_all(best, rules):
    """True when the floor sits at or above best-so-far on a min metric: nothing can be kept."""
    return (best is not None and rules["direction"] == "min" and best > 0
            and noise_floor(best, rules) >= best)


def better(a, b, direction):
    """The better of two values in `direction`, tolerating None."""
    if a is None:
        return b
    if b is None:
        return a
    return min(a, b) if direction == "min" else max(a, b)


def _nonneg_float(v):
    try:
        f = float(v or 0.0)
    except (TypeError, ValueError):
        return 0.0
    if not math.isfinite(f):
        return 0.0
    return max(0.0, f)


def rules_from_config(cfg):
    """The subset of loop_config.json that decides what counts as a keep."""
    primary_cfg = cfg.get("primary", {})
    direction = primary_cfg.get("direction", cfg.get("direction"))
    return {
        "direction": direction,
        "min_delta": _nonneg_float(cfg.get("min_delta")),
        "min_delta_pct": _nonneg_float(cfg.get("min_delta_pct")),
        "gates": load_gates(cfg),
    }


def load_rows(path, warnings=None):
    rows = []
    # utf-8-sig: a BOM in the header would otherwise hide the `round` column
    # and turn every candidate row into its own round.
    f, reader = open_results(path)
    with f:
        has_round = reader.fieldnames is not None and "round" in reader.fieldnames
        # tolerate the older `metric` column name for the primary
        for i, r in enumerate(reader):
            raw = r.get("primary", r.get("metric"))
            try:
                r["_primary"] = float(raw)
            except (TypeError, ValueError):
                r["_primary"] = None
            if r["_primary"] is not None and not math.isfinite(r["_primary"]):
                if warnings is not None:
                    warnings.append(f"row {i + 1}: primary {raw!r} is not a finite number; treated as unparseable")
                r["_primary"] = None
            r["_bad_round"] = False
            try:
                r["_round"] = parse_round(r["round"]) if has_round else i
            except (TypeError, ValueError, KeyError):
                r["_round"] = i
                r["_bad_round"] = has_round
                if has_round and warnings is not None:
                    warnings.append(
                        f"row {i + 1}: round {r.get('round')!r} is not an integer; "
                        f"treated as its own round, and not as a keep")
            r["_status"] = (r.get("status") or "").strip().lower()
            r["_counters"] = parse_counters(r.get("counters"))
            rows.append(r)
    return rows


def group_rounds(rows, rules, warnings):
    """Collapse candidate rows into per-round records, ordered by round.

    Every keep row is audited on the way (see the module docstring); a keep
    that fails the audit is recorded in `warnings` and the round is treated as
    keepless.
    """
    direction = rules["direction"]
    gates = rules["gates"]
    buckets = {}
    for r in rows:
        buckets.setdefault(r["_round"], []).append(r)
    rounds = []
    best = None   # best VALID keep: what the run is credited with
    bar = None    # best keep row of any kind: the value a candidate must beat
    for rnum in sorted(buckets):
        members = buckets[rnum]
        kept = None
        keep_rows = [m for m in members if m["_status"] == "keep"]
        if len(keep_rows) > 1:
            # A round keeps at most one candidate. Two keep rows mean the log
            # was not written by adjudicate.py; neither is trusted.
            warnings.append(f"round {rnum}: {len(keep_rows)} keep rows; a round keeps at most one, so none counted")
            keep_rows = []
        for m in keep_rows:
            label = f"round {rnum} candidate {m.get('candidate') or '?'}"
            if m["_bad_round"]:
                warnings.append(f"{label}: keep row with a non-integer round label; treated as no keep")
                continue
            if m["_primary"] is None:
                warnings.append(f"{label}: keep row has no parseable primary; treated as no keep")
                continue
            viol = gate_violations(m["_counters"], gates)
            if viol:
                warnings.append(f"{label}: keep row violates gate ({'; '.join(viol)}); treated as no keep")
                continue
            missing = [name for name, _, _ in gates if m["_counters"].get(name) is None]
            if missing:
                # Same rule as adjudicate.py: a gate that cannot be evaluated is
                # not a passed gate, or a candidate could win by breaking the
                # counter's extraction.
                warnings.append(f"{label}: keep row has no value for gated counter(s) "
                                f"{', '.join(missing)}; treated as no keep")
                continue
            if bar is not None and not improves(
                    m["_primary"], bar, direction, noise_floor(bar, rules)):
                warnings.append(
                    f"{label}: keep row {m['_primary']:.6g} does not beat best-so-far "
                    f"{bar:.6g} by the noise floor {noise_floor(bar, rules):.6g}; treated as no keep")
                continue
            kept = m["_primary"]
            best = better(best, kept, direction)
        # A rejected keep still raises the bar for later rounds, never lowers
        # it: dropping its value would let a planted row make later rounds
        # easier to keep, the one direction this audit must never move.
        for m in members:
            if m["_status"] == "keep" and m["_primary"] is not None:
                bar = better(bar, m["_primary"], direction)
        rounds.append({
            "round": rnum,
            "kept": kept,
            "candidates": len(members),
        })
    return rounds


def best_series(rounds, direction):
    """Running best-so-far after each round."""
    best = None
    series = []
    for rd in rounds:
        val = rd["kept"]
        if val is not None:
            if best is None:
                best = val
            elif direction == "min":
                best = min(best, val)
            else:
                best = max(best, val)
        series.append(best)
    return series


def _setting(cfg, key, default, warnings, kind=float, allow_none=False):
    """A numeric config value, or `default` (with a warning) when it is unusable.

    A traceback here would leave the loop without a verdict; a documented
    default with a warning keeps the run terminating.
    """
    raw = cfg.get(key, default)
    if raw is None and allow_none:
        return None
    try:
        val = kind(raw)
    except (TypeError, ValueError):
        warnings.append(f"config: {key}={raw!r} is not a number; using {default!r}")
        return default
    if isinstance(val, float) and not math.isfinite(val):
        warnings.append(f"config: {key}={raw!r} is not finite; using {default!r}")
        return default
    return val


def run(args):
    with open(args.config, encoding="utf-8-sig") as f:
        cfg = json.load(f)

    warnings = []
    rules = rules_from_config(cfg)
    direction = rules["direction"]
    if direction not in ("min", "max"):
        return {"stop": True,
                "reason": "invalid config: primary.direction must be 'min' or 'max'",
                "stats": {}, "warnings": warnings}
    patience = _setting(cfg, "patience", 8, warnings, int)
    if patience < 1:
        warnings.append(f"config: patience={patience} is below 1; using 1")
        patience = 1
    epsilon = _setting(cfg, "epsilon", None, warnings, float, allow_none=True)
    window = _setting(cfg, "epsilon_window", 10, warnings, int)
    if window < 1:
        warnings.append(f"config: epsilon_window={window} is below 1; using 1")
        window = 1
    if "max_rounds" not in cfg and "max_trials" in cfg:
        cfg = dict(cfg, max_rounds=cfg["max_trials"])
    max_rounds = _setting(cfg, "max_rounds", 40, warnings, int)
    if max_rounds < 1:
        warnings.append(f"config: max_rounds={max_rounds} is below 1; using 1")
        max_rounds = 1
    per_round = max(1, _setting(cfg, "candidates_per_round", 1, warnings, int))
    target = _setting(cfg, "target", None, warnings, float, allow_none=True)

    rows = load_rows(args.results, warnings)
    if not rows:
        return {"stop": False, "reason": "no trials yet; run baseline", "stats": {}, "warnings": warnings}

    rounds = group_rounds(rows, rules, warnings)
    series = best_series(rounds, direction)
    baseline = next((rd["kept"] for rd in rounds if rd["kept"] is not None), None)
    best = series[-1]

    # consecutive rounds with no keep, counted from the end
    barren = 0
    for rd in reversed(rounds):
        if rd["kept"] is not None:
            break
        barren += 1

    n_rounds = len(rounds)
    has_baseline_round = any(rd["round"] == 0 for rd in rounds)
    stats = {
        "rounds": n_rounds,
        "rounds_after_baseline": n_rounds - 1 if has_baseline_round else n_rounds,
        "candidates": len(rows),
        "baseline": baseline,
        "best": best,
        "rounds_since_keep": barren,
    }
    if target is not None:
        stats["target"] = target
    bar = None
    for r in rows:
        if r["_status"] == "keep" and r["_primary"] is not None:
            bar = better(bar, r["_primary"], direction)
    if bar is not None and best is not None and bar != best:
        stats["bar"] = bar
    floor = noise_floor(best, rules) if best is not None else rules["min_delta"]
    if floor:
        stats["noise_floor"] = floor
    if floor_blocks_all(best, rules):
        warnings.append(
            f"noise floor {floor:.6g} is at or above best-so-far {best:.6g}: no candidate can be "
            f"kept from here; set min_delta_pct instead of an absolute min_delta for a metric that shrinks")
    if epsilon is None:
        # null means the larger of 0.5% of baseline and twice the noise floor at
        # best-so-far, so one floor-sized keep in the window never reads as
        # progress. A baseline of 0 (or none yet) leaves the condition disabled
        # rather than inventing a value.
        epsilon = max(0.005 * abs(baseline), 2 * floor) if baseline else 0.0
        stats["epsilon_effective"] = epsilon

    def verdict(stop, reason):
        return {"stop": stop, "reason": reason, "stats": stats, "warnings": warnings}

    # Hard caps come before the no-keep early return: a run whose keeps never
    # parse (all crashes, or a malformed primary column) must still terminate.
    if n_rounds >= max_rounds:
        return verdict(True, f"max_rounds reached ({n_rounds}/{max_rounds})")
    # The same cap counted in candidate rows, so a log that reuses one round
    # label (which the round-based conditions cannot see) still terminates.
    if len(rows) > max_rounds * per_round:
        return verdict(True, f"max_rounds reached: {len(rows)} candidate rows exceed "
                             f"max_rounds x candidates_per_round ({max_rounds} x {per_round})")

    # A declared target beats patience: once the metric is at its goal, further
    # rounds cannot improve it, and reporting "patience exhausted" would file a
    # finished run under the same reason as a stalled one.
    if target is not None and best is not None:
        if (best <= target) if direction == "min" else (best >= target):
            return verdict(True, f"target reached: best {best:.6g} meets target {target:.6g} (direction={direction})")

    if barren >= patience:
        return verdict(True, f"patience exhausted: {barren} consecutive rounds with no improvement (patience={patience})")

    if best is None:
        return verdict(False, "no successful trial yet; establish a baseline")

    if epsilon > 0 and n_rounds > window:
        prev = series[-(window + 1)]
        if prev is not None:
            gain = (prev - best) if direction == "min" else (best - prev)
            stats["window_gain"] = gain
            if gain < epsilon:
                return verdict(True, f"diminishing returns: gain over last {window} rounds is {gain:.6g}, below epsilon {epsilon:.6g}")

    return verdict(False, "continue")


def main():
    p = argparse.ArgumentParser()
    p.add_argument("--config", required=True)
    p.add_argument("--results", required=True)
    args = p.parse_args()
    try:
        out = run(args)
        line = json.dumps(out, allow_nan=False)
    except Exception as e:  # no verdict at all would leave the loop to decide for itself
        line = json.dumps({"stop": True,
                           "reason": f"check_stop.py failed: {type(e).__name__}: {e}"[:300],
                           "stats": {}, "warnings": []})
    print(line)
    return 0


if __name__ == "__main__":
    sys.exit(main())
