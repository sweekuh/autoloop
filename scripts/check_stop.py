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

`epsilon` may be null (or absent). It then means 0.5% of the baseline value,
derived here from the baseline row and reported as `epsilon_effective`, so a
config written without a number still gets a diminishing-returns stop. An
explicit number keeps its meaning; 0 disables the condition.

max_rounds and patience are evaluated even when no keep row has ever parsed,
so a run that only crashes still terminates instead of looping unbounded.

The results file is audited, not trusted. A `keep` row only counts as a keep
when it has a parseable primary, is the only keep in its round, violates no
declared counter-metric gate (the `counters` column is parsed as name=value
pairs and checked against `counter_metrics`), and beats the previous valid best
by at least `min_delta` (strictly better when min_delta is 0). Anything else is
listed in `warnings` and treated as no keep. That can only end a run earlier,
never later: an unearned keep would otherwise reset patience and hide a stall.
The baseline (the first valid keep) is exempt from the min_delta test.

A config whose primary has no direction is refused with stop=true, because a
guessed direction would keep the worst candidate instead of the best.

This script is part of the frozen harness. The looping agent must not edit it.
Backward compatible with a results.tsv that has no `round` column: each row is
then treated as its own round.
"""
import argparse
import csv
import json
import re
import sys

COMPARATORS = {
    ">=": lambda v, t: v >= t,
    "<=": lambda v, t: v <= t,
    ">": lambda v, t: v > t,
    "<": lambda v, t: v < t,
    "==": lambda v, t: v == t,
}


def parse_round(raw):
    """Round labels are integers, but tolerate integer-valued floats ('1.0').

    Anything else raises so the caller falls back to per-row rounds; silently
    accepting '1.5' as a round label would misgroup candidates.
    """
    v = float(str(raw).strip())
    if not v.is_integer():
        raise ValueError(raw)
    return int(v)


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
            out[name.strip()] = float(val)
        except ValueError:
            continue
    return out


def load_gates(cfg):
    """[(name, comparator, threshold)] from counter_metrics.

    A missing comparator is derived from the counter's direction: a `max`
    counter must stay >= its threshold, a `min` counter <= it.
    """
    gates = []
    for cm in cfg.get("counter_metrics") or []:
        name = cm.get("name")
        thr = cm.get("threshold")
        if name is None or thr is None:
            continue
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


def improves(new, best, direction, min_delta):
    """True when `new` beats `best` in `direction` by at least min_delta.

    With min_delta 0 the candidate must still be strictly better: a tie is not
    an improvement, and treating it as one lets a run report jitter as progress.
    """
    gain = (best - new) if direction == "min" else (new - best)
    return gain > 0 and gain >= min_delta


def rules_from_config(cfg):
    """The subset of loop_config.json that decides what counts as a keep."""
    primary_cfg = cfg.get("primary", {})
    direction = primary_cfg.get("direction", cfg.get("direction"))
    try:
        min_delta = float(cfg.get("min_delta") or 0.0)
    except (TypeError, ValueError):
        min_delta = 0.0
    return {"direction": direction, "min_delta": min_delta, "gates": load_gates(cfg)}


def load_rows(path, warnings=None):
    rows = []
    with open(path, newline="") as f:
        reader = csv.DictReader(f, delimiter="\t")
        has_round = reader.fieldnames is not None and "round" in reader.fieldnames
        # tolerate the older `metric` column name for the primary
        for i, r in enumerate(reader):
            raw = r.get("primary", r.get("metric"))
            try:
                r["_primary"] = float(raw)
            except (TypeError, ValueError):
                r["_primary"] = None
            try:
                r["_round"] = parse_round(r["round"]) if has_round else i
            except (TypeError, ValueError, KeyError):
                r["_round"] = i
                if has_round and warnings is not None:
                    warnings.append(
                        f"row {i + 1}: round {r.get('round')!r} is not an integer; "
                        f"treated as its own round")
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
    min_delta = rules["min_delta"]
    gates = rules["gates"]
    buckets = {}
    for r in rows:
        buckets.setdefault(r["_round"], []).append(r)
    rounds = []
    best = None
    for rnum in sorted(buckets):
        members = buckets[rnum]
        kept = None
        for m in members:
            if m["_status"] != "keep":
                continue
            label = f"round {rnum} candidate {m.get('candidate') or '?'}"
            if m["_primary"] is None:
                warnings.append(f"{label}: keep row has no parseable primary; treated as no keep")
                continue
            if kept is not None:
                warnings.append(f"{label}: second keep in one round; treated as no keep")
                continue
            viol = gate_violations(m["_counters"], gates)
            if viol:
                warnings.append(f"{label}: keep row violates gate ({'; '.join(viol)}); treated as no keep")
                continue
            if best is not None and not improves(m["_primary"], best, direction, min_delta):
                warnings.append(
                    f"{label}: keep row {m['_primary']:.6g} does not beat best-so-far "
                    f"{best:.6g} by min_delta {min_delta:.6g}; treated as no keep")
                continue
            kept = m["_primary"]
            if best is None:
                best = kept
            elif direction == "min":
                best = min(best, kept)
            else:
                best = max(best, kept)
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


def main():
    p = argparse.ArgumentParser()
    p.add_argument("--config", required=True)
    p.add_argument("--results", required=True)
    args = p.parse_args()

    with open(args.config) as f:
        cfg = json.load(f)

    warnings = []
    rules = rules_from_config(cfg)
    direction = rules["direction"]
    if direction not in ("min", "max"):
        print(json.dumps({
            "stop": True,
            "reason": "invalid config: primary.direction must be 'min' or 'max'",
            "stats": {}, "warnings": warnings}))
        return
    patience = int(cfg.get("patience", 8))
    raw_epsilon = cfg.get("epsilon")
    epsilon = None if raw_epsilon is None else float(raw_epsilon)
    window = int(cfg.get("epsilon_window", 10))
    max_rounds = int(cfg.get("max_rounds", cfg.get("max_trials", 40)))
    raw_target = cfg.get("target")
    try:
        target = None if raw_target is None else float(raw_target)
    except (TypeError, ValueError):
        target = None

    rows = load_rows(args.results, warnings)
    if not rows:
        print(json.dumps({"stop": False, "reason": "no trials yet; run baseline", "stats": {}, "warnings": warnings}))
        return

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
    stats = {
        "rounds": n_rounds,
        "candidates": len(rows),
        "baseline": baseline,
        "best": best,
        "rounds_since_keep": barren,
    }
    if target is not None:
        stats["target"] = target
    if epsilon is None:
        # null means 0.5% of the baseline value. A baseline of 0 (or none yet)
        # leaves the condition disabled rather than inventing a floor.
        epsilon = 0.005 * abs(baseline) if baseline else 0.0
        stats["epsilon_effective"] = epsilon
    if rules["min_delta"]:
        stats["min_delta"] = rules["min_delta"]

    def verdict(stop, reason):
        print(json.dumps({"stop": stop, "reason": reason, "stats": stats, "warnings": warnings}))

    # Hard caps come before the no-keep early return: a run whose keeps never
    # parse (all crashes, or a malformed primary column) must still terminate.
    if n_rounds >= max_rounds:
        verdict(True, f"max_rounds reached ({n_rounds}/{max_rounds})")
        return

    # A declared target beats patience: once the metric is at its goal, further
    # rounds cannot improve it, and reporting "patience exhausted" would file a
    # finished run under the same reason as a stalled one.
    if target is not None and best is not None:
        if (best <= target) if direction == "min" else (best >= target):
            verdict(True, f"target reached: best {best:.6g} meets target {target:.6g} (direction={direction})")
            return

    if barren >= patience:
        verdict(True, f"patience exhausted: {barren} consecutive rounds with no improvement (patience={patience})")
        return

    if best is None:
        verdict(False, "no successful trial yet; establish a baseline")
        return

    if epsilon > 0 and n_rounds > window:
        prev = series[-(window + 1)]
        if prev is not None:
            gain = (prev - best) if direction == "min" else (best - prev)
            stats["window_gain"] = gain
            if gain < epsilon:
                verdict(True, f"diminishing returns: gain over last {window} rounds is {gain:.6g}, below epsilon {epsilon:.6g}")
                return

    verdict(False, "continue")


if __name__ == "__main__":
    sys.exit(main())
