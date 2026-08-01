#!/usr/bin/env python3
"""Append an anonymized run summary to the skill's ledger and refresh README stats.

Runs at the end of Phase 4 (see SKILL.md). Reads the run's loop_config.json and
results-<run_tag>.tsv from the TARGET project, extracts only aggregate numbers,
and writes to THIS skill checkout:

  runs/RUNS.tsv   one row per run (created on first use)
  README.md       the block between the autoloop-stats markers is re-rendered

Anonymized by design: the row carries the metric name, direction, round and
status counts, baseline, best, and improvement percent - never project names,
file paths, or candidate descriptions. The --label is chosen by the caller and
should name the task shape ("mobile web load time"), not the project.

Idempotent: re-logging an identical run (same label, date, baseline, best) is
a no-op for the ledger; the README block is re-rendered either way. The ledger
and README edits stay local until the user pushes the skill repo.

This script writes only inside the skill's own checkout. It is not part of the
frozen harness for the run being logged: it runs after the loop has stopped and
never reads or writes the target project beyond the two files it is given.
"""
import argparse
import csv
import datetime
import json
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
LEDGER = os.path.join(ROOT, "runs", "RUNS.tsv")
README = os.path.join(ROOT, "README.md")
START = "<!-- autoloop-stats:start -->"
END = "<!-- autoloop-stats:end -->"

COLUMNS = [
    "date", "label", "metric", "direction", "judged", "gates", "rounds",
    "candidates", "keeps", "discards", "gate_fails", "crashes",
    "baseline", "best", "improvement_pct", "stop",
]


def fmt_num(v):
    """Trim trailing .0 so integer-valued metrics read as integers."""
    if v is None:
        return "-"
    if float(v) == int(float(v)):
        return str(int(float(v)))
    return f"{float(v):g}"


def load_config(path):
    with open(path, encoding="utf-8") as f:
        cfg = json.load(f)
    primary = cfg.get("primary", {})
    metric = primary.get("name") or cfg.get("metric_name") or "metric"
    direction = primary.get("direction") or cfg.get("direction") or "min"
    counters = cfg.get("counter_metrics")
    if counters is None:
        counters = [cfg["counter_metric"]] if cfg.get("counter_metric") else []
    judged = bool(cfg.get("judge_metric"))
    return metric, direction, len(counters), judged


def load_results(path):
    rows = []
    with open(path, newline="", encoding="utf-8") as f:
        reader = csv.DictReader(f, delimiter="\t")
        has_round = reader.fieldnames is not None and "round" in reader.fieldnames
        for i, r in enumerate(reader):
            raw = r.get("primary", r.get("metric"))
            try:
                r["_primary"] = float(raw)
            except (TypeError, ValueError):
                r["_primary"] = None
            try:
                rv = float(str(r["round"]).strip()) if has_round else float(i)
                r["_round"] = int(rv) if rv.is_integer() else i
            except (TypeError, ValueError, KeyError):
                r["_round"] = i
            r["_status"] = (r.get("status") or "").strip().lower()
            rows.append(r)
    return rows


def summarize(rows, direction):
    if not rows:
        raise SystemExit("log_run: results file has no rows; nothing to log")
    baseline_row = next(
        (r for r in rows if r["_status"] == "keep" and r["_primary"] is not None), None)
    if baseline_row is None:
        raise SystemExit("log_run: no parseable keep row; cannot establish a baseline")
    baseline = baseline_row["_primary"]
    keeps = [r for r in rows if r["_status"] == "keep" and r["_primary"] is not None]
    best = min if direction == "min" else max
    best_val = best(r["_primary"] for r in keeps)
    non_baseline = [r for r in rows if r is not baseline_row]
    counts = {
        "rounds": max(r["_round"] for r in rows),
        "candidates": len(non_baseline),
        "keeps": sum(1 for r in non_baseline if r["_status"] == "keep"),
        "discards": sum(1 for r in non_baseline if r["_status"] == "discard"),
        "gate_fails": sum(1 for r in non_baseline if r["_status"] == "gate_fail"),
        "crashes": sum(1 for r in non_baseline if r["_status"] == "crash"),
    }
    if baseline == 0:
        pct = 0.0
    elif direction == "min":
        pct = (baseline - best_val) / abs(baseline) * 100.0
    else:
        pct = (best_val - baseline) / abs(baseline) * 100.0
    return baseline, best_val, round(pct, 1), counts


def read_ledger():
    if not os.path.exists(LEDGER):
        return []
    with open(LEDGER, newline="", encoding="utf-8") as f:
        return list(csv.DictReader(f, delimiter="\t"))


def append_ledger(row):
    existing = read_ledger()
    for r in existing:
        if (r.get("label"), r.get("date"), r.get("baseline"), r.get("best")) == (
                row["label"], row["date"], row["baseline"], row["best"]):
            return existing, False
    os.makedirs(os.path.dirname(LEDGER), exist_ok=True)
    is_new = not os.path.exists(LEDGER)
    with open(LEDGER, "a", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=COLUMNS, delimiter="\t")
        if is_new:
            w.writeheader()
        w.writerow(row)
    return existing + [row], True


def median(vals):
    s = sorted(vals)
    n = len(s)
    if n == 0:
        return 0.0
    mid = n // 2
    return s[mid] if n % 2 else (s[mid - 1] + s[mid]) / 2.0


def render_block(ledger):
    total = len(ledger)
    keeps = sum(int(r["keeps"]) for r in ledger)
    discards = sum(int(r["discards"]) for r in ledger)
    gate_fails = sum(int(r["gate_fails"]) for r in ledger)
    crashes = sum(int(r["crashes"]) for r in ledger)
    med = median([float(r["improvement_pct"]) for r in ledger])
    lines = [
        f"{total} run(s) logged - {keeps} kept, {discards} discarded, "
        f"{gate_fails} blocked by a counter-metric gate, {crashes} crashed. "
        f"Median improvement in the primary metric: {med:g}%.",
        "",
        "| date | task | metric | baseline -> best | improvement | rounds | gate hits | stop |",
        "|---|---|---|---|---|---|---|---|",
    ]
    for r in ledger:
        judged = " (judged)" if r.get("judged") == "yes" else ""
        lines.append(
            f"| {r['date']} | {r['label']} | `{r['metric']}` ({r['direction']}){judged} "
            f"| {r['baseline']} -> {r['best']} | {float(r['improvement_pct']):+g}% "
            f"| {r['rounds']} | {r['gate_fails']} | {r['stop']} |")
    lines += [
        "",
        "Appended by `scripts/log_run.py` at the end of each run (SKILL.md Phase 4). "
        "Labels name the task shape, never the project; full trial logs stay in their "
        "source projects.",
    ]
    return "\n".join(lines)


def update_readme(ledger):
    with open(README, encoding="utf-8") as f:
        text = f.read()
    if START not in text or END not in text:
        raise SystemExit(
            f"log_run: README.md is missing the {START} / {END} markers; "
            "ledger row was written but the stats block was not rendered")
    head, rest = text.split(START, 1)
    _, tail = rest.split(END, 1)
    text = head + START + "\n" + render_block(ledger) + "\n" + END + tail
    with open(README, "w", encoding="utf-8", newline="\n") as f:
        f.write(text)


def main():
    p = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    p.add_argument("--config", required=True, help="the run's loop_config.json")
    p.add_argument("--results", required=True, help="the run's results-<run_tag>.tsv")
    p.add_argument("--label", required=True,
                   help="2-4 generic words naming the task shape, not the project")
    p.add_argument("--stop", default="-", help="stop reason, one short phrase")
    p.add_argument("--date", default=None, help="run date YYYY-MM-DD (default: today)")
    p.add_argument("--dry-run", action="store_true",
                   help="print the row; write nothing")
    args = p.parse_args()

    metric, direction, gates, judged = load_config(args.config)
    rows = load_results(args.results)
    baseline, best_val, pct, counts = summarize(rows, direction)

    row = {
        "date": args.date or datetime.date.today().isoformat(),
        "label": args.label,
        "metric": metric,
        "direction": direction,
        "judged": "yes" if judged else "no",
        "gates": str(gates),
        "rounds": str(counts["rounds"]),
        "candidates": str(counts["candidates"]),
        "keeps": str(counts["keeps"]),
        "discards": str(counts["discards"]),
        "gate_fails": str(counts["gate_fails"]),
        "crashes": str(counts["crashes"]),
        "baseline": fmt_num(baseline),
        "best": fmt_num(best_val),
        "improvement_pct": f"{pct:g}",
        "stop": args.stop,
    }

    if args.dry_run:
        print("\t".join(row[c] for c in COLUMNS))
        return 0

    ledger, added = append_ledger(row)
    update_readme(ledger)
    verb = "logged" if added else "already logged (ledger unchanged)"
    print(f"log_run: {verb} - {row['label']}: {row['baseline']} -> {row['best']} "
          f"({float(row['improvement_pct']):+g}%), README stats re-rendered")
    return 0


if __name__ == "__main__":
    sys.exit(main())
