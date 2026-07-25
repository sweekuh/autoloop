#!/usr/bin/env python3
"""Frozen stopping-rule arbiter for autoloop.

Reads loop_config.json and results.tsv, prints a JSON verdict:
  {"stop": bool, "reason": str, "stats": {...}}

Rounds, not candidates, are the unit of progress. With more than one candidate
per round, a single round contains several candidate rows and keeps at most
one, so counting raw rows would make patience fire once per candidate instead
of once per round.

Stop conditions, whichever fires first:
  1. patience   - N consecutive rounds with no keep
  2. epsilon    - improvement in best-so-far over the last `epsilon_window`
                  rounds is below `epsilon` (metric units)
  3. max_rounds - hard cap

This script is part of the frozen harness. The looping agent must not edit it.
Backward compatible with a results.tsv that has no `round` column: each row is
then treated as its own round.
"""
import argparse
import csv
import json
import sys


def load_rows(path):
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
                r["_round"] = int(r["round"]) if has_round else i
            except (TypeError, ValueError, KeyError):
                r["_round"] = i
            r["_status"] = (r.get("status") or "").strip().lower()
            rows.append(r)
    return rows


def group_rounds(rows):
    """Collapse candidate rows into per-round records, ordered by round."""
    buckets = {}
    for r in rows:
        buckets.setdefault(r["_round"], []).append(r)
    rounds = []
    for rnum in sorted(buckets):
        members = buckets[rnum]
        kept = [m for m in members if m["_status"] == "keep" and m["_primary"] is not None]
        rounds.append({
            "round": rnum,
            "kept": kept[0]["_primary"] if kept else None,
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

    primary_cfg = cfg.get("primary", {})
    direction = primary_cfg.get("direction", cfg.get("direction", "min"))
    patience = int(cfg.get("patience", 8))
    epsilon = float(cfg.get("epsilon", 0.0))
    window = int(cfg.get("epsilon_window", 10))
    max_rounds = int(cfg.get("max_rounds", cfg.get("max_trials", 40)))

    rows = load_rows(args.results)
    if not rows:
        print(json.dumps({"stop": False, "reason": "no trials yet; run baseline", "stats": {}}))
        return

    rounds = group_rounds(rows)
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

    if best is None:
        print(json.dumps({"stop": False, "reason": "no successful trial yet; establish a baseline", "stats": stats}))
        return

    if n_rounds >= max_rounds:
        print(json.dumps({"stop": True, "reason": f"max_rounds reached ({n_rounds}/{max_rounds})", "stats": stats}))
        return

    if barren >= patience:
        print(json.dumps({"stop": True, "reason": f"patience exhausted: {barren} consecutive rounds with no improvement (patience={patience})", "stats": stats}))
        return

    if epsilon > 0 and n_rounds > window:
        prev = series[-(window + 1)]
        if prev is not None:
            gain = (prev - best) if direction == "min" else (best - prev)
            stats["window_gain"] = gain
            if gain < epsilon:
                print(json.dumps({"stop": True, "reason": f"diminishing returns: gain over last {window} rounds is {gain:.6g}, below epsilon {epsilon:.6g}", "stats": stats}))
                return

    print(json.dumps({"stop": False, "reason": "continue", "stats": stats}))


if __name__ == "__main__":
    sys.exit(main())
