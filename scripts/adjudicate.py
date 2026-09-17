#!/usr/bin/env python3
"""Decide keep / discard / gate_fail / crash for one round of autoloop.

Part of the frozen harness (see SKILL.md Phase 3). The looping agent must not
edit it, and never assigns a status label itself: it runs run_trial.py for
each candidate, hands the results here, and appends the rows this prints:

  python3 ${CLAUDE_SKILL_DIR}/scripts/adjudicate.py --config loop_config.json \
      --results results-<run_tag>.tsv --round N --candidates candidates.json --append

With --append the rows go straight into the results file, so the agent never
touches that file at all. Without it the rows are only printed.

candidates.json is a list, one entry per candidate in the round:
  [{"candidate": "0", "commit": "abc1234", "description": "one line",
    "trial": <the JSON line run_trial.py printed>}]

It prints one JSON object:
  {"round": N, "rows": ["<tab-separated results row>", ...],
   "keep": "<candidate id>"|null, "keep_commit": "<sha>"|null,
   "best_so_far": float|null, "noise_floor": float, "appended": int,
   "reason": str, "warnings": [...]}

A `discard` is not always a dead end. With several candidates per round, a
survivor that beat best-so-far but lost to a better sibling gets its
description prefixed "lost to <id>: ", and when it lost by less than the noise
floor the prefix says so ("lost to <id> inside the noise floor: "), because
that ordering was a coin flip. The candidate generator should read those rows
as live ideas.

Candidate records come from the agent, so they are normalised before use: a
missing or duplicated id is renamed, a non-dict record or trial is a crash, and
a non-numeric or non-finite metric value is dropped (an unreadable primary is a
crash; an unreadable gated counter is a gate_fail). Descriptions and counter
names are stripped of anything that could corrupt the tab-separated log.

Rules, applied in this order, with the same functions check_stop.py uses so
the two scripts cannot disagree about what a keep is:
  1. A trial that timed out, crashed, or whose primary did not extract is
     `crash`.
  2. A trial that violates any counter-metric gate is `gate_fail`, whatever its
     primary looks like. The row's description records which gate and by how
     much, because those rows map the boundary of the search space.
  3. Among the survivors, the best primary is `keep` if it beats best-so-far
     (the best valid keep already in the results file) by at least the noise
     floor, max(min_delta, min_delta_pct% of best-so-far), strictly better when
     the floor is 0. Every other survivor is `discard`.
  4. Round 0 is the baseline: exactly one candidate, kept if it ran and passes
     every gate. A baseline that fails a gate or crashes produces no row and a
     reason telling the agent to resolve the harness with the user first.

best-so-far comes from the audited results file, so a mislabeled keep that
check_stop.py would ignore does not raise the bar here either.

Output is pure ASCII.
"""
import argparse
import json
import math
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import check_stop  # noqa: E402  (same directory; frozen alongside this script)

COLUMNS = ["round", "candidate", "commit", "primary", "counters", "status", "description"]


def ascii_only(s):
    return str(s).encode("ascii", "backslashreplace").decode("ascii")


def clean(text, limit=300):
    """One line, no tabs, quotes or control characters, ASCII, bounded.

    The results file is plain tab-separated text that check_stop.py reads with
    no quoting, so a stray double quote (or a tab) in a description would
    corrupt every row after it.
    """
    s = " ".join(str(text if text is not None else "").split())
    s = "".join(ch for ch in s if ch >= " " and ch not in "\x7f\"")
    return ascii_only(s)[:limit] or "-"


def number(v):
    """A finite float from a trial field, or None. Never raises."""
    if isinstance(v, bool) or not isinstance(v, (int, float)) or not math.isfinite(v):
        return None
    return float(v)


def finite_primary(trial):  # noqa: D401
    """The trial's primary as the value the results file will hold, or None.

    Rounded through fmt() so the comparison against best-so-far (which comes
    back from the file at that precision) uses the same number the row will
    carry. Non-finite values are None: nan and inf must never become a keep.
    """
    p = number(trial.get("primary"))
    return None if p is None else float(fmt(p))


def fmt(v):
    v = number(v)
    if v is None:
        return "-"
    return f"{v:.10g}"


def fmt_counters(counters):
    """`name=value` pairs, with names normalised exactly as check_stop.py reads them.

    Without clean_name a tab or a comma in a counter name would shift the TSV
    columns or split one pair into two, and a gate would silently stop matching.
    """
    if not isinstance(counters, dict):
        return "-"
    items = []
    for k, v in sorted(counters.items(), key=lambda kv: str(kv[0])):
        val = number(v)
        if val is not None:
            items.append(f"{check_stop.clean_name(k)}={fmt(val)}")
    return ",".join(items) if items else "-"


def row(rnum, cand, status, description):
    trial = cand.get("trial") or {}
    return "\t".join([
        str(rnum),
        clean(cand.get("candidate"), 40),
        clean(cand.get("commit"), 64),
        fmt(trial.get("primary") if isinstance(trial, dict) else None),
        fmt_counters(trial.get("counters")),
        status,
        clean(description),
    ])


def crash_reason(trial):
    if trial.get("timed_out"):
        return "timed out"
    if trial.get("primary") is None:
        code = trial.get("exit_code")
        return "primary did not extract" + (f" (exit {code})" if code not in (None, 0) else "")
    return "no result"


def empty(rnum, reason, warnings=None):
    return {"round": rnum, "rows": [], "keep": None, "keep_commit": None,
            "best_so_far": None, "appended": 0, "reason": reason, "warnings": warnings or []}


def run(args):
    with open(args.config, encoding="utf-8-sig") as f:
        cfg = json.load(f)
    with open(args.candidates, encoding="utf-8-sig") as f:
        cands = json.load(f)
    if not isinstance(cands, list) or not cands:
        return empty(args.round, "no candidates given")

    warnings = []
    # Candidate records are the agent's; normalise them so a missing, duplicate
    # or non-string id cannot mislabel a row, and a non-dict trial is a crash.
    seen_ids = set()
    for i, cand in enumerate(cands):
        if not isinstance(cand, dict):
            cands[i] = cand = {"description": clean(cand)}
            warnings.append(f"candidate {i}: not an object; treated as a crash")
        if not isinstance(cand.get("trial"), dict):
            cand["trial"] = {"ok": False}
        cid = cand.get("candidate")
        cid = clean(cid, 40) if cid is not None and cid != "" else None
        if cid is None or cid in seen_ids:
            new_id = f"{i}" if cid is None else f"{cid}-{i}"
            warnings.append(f"candidate {i}: id {cid!r} is missing or duplicated; renamed {new_id!r}")
            cid = new_id
        cand["candidate"] = cid
        seen_ids.add(cid)
        trial = cand["trial"]
        # Metric values are the agent's too: a string or a non-dict counters
        # map must make one candidate a crash, never abort the whole round.
        if not isinstance(trial.get("counters"), dict):
            trial["counters"] = {}
        trial["counters"] = {k: number(v) for k, v in trial["counters"].items() if number(v) is not None}
        if number(trial.get("primary")) is None:
            trial["primary"] = None
            trial["ok"] = False
    rules = check_stop.rules_from_config(cfg)
    direction = rules["direction"]
    if direction not in ("min", "max"):
        print(json.dumps({"round": args.round, "rows": [], "keep": None, "keep_commit": None,
                          "best_so_far": None,
                          "reason": "invalid config: primary.direction must be 'min' or 'max'",
                          "warnings": warnings}))
        return 0
    gates = rules["gates"]

    rows_seen = check_stop.load_rows(args.results, warnings) if os.path.exists(args.results) else []
    rounds = check_stop.group_rounds(rows_seen, rules, warnings)
    series = check_stop.best_series(rounds, direction)
    best = series[-1] if series else None
    if any(r["_round"] == args.round for r in rows_seen):
        # Reusing a round number would fold new candidates into an old round,
        # which patience and max_rounds count once. Re-running a crashed
        # candidate happens before adjudication, never after.
        return empty(args.round, f"round {args.round} already has rows in "
                                 f"{os.path.basename(args.results)}; use the next round number", warnings)
    if rows_seen and args.round > max(r["_round"] for r in rows_seen) + 1:
        warnings.append(f"round {args.round} skips ahead of the last logged round "
                        f"{max(r['_round'] for r in rows_seen)}")

    out = {"round": args.round, "rows": [], "keep": None, "keep_commit": None,
           "best_so_far": best, "appended": 0, "reason": "", "warnings": warnings}

    def finish():
        if args.append and out["rows"]:
            exists = os.path.exists(args.results) and os.path.getsize(args.results) > 0
            needs_nl = False
            if exists:
                with open(args.results, "rb") as f:
                    f.seek(-1, os.SEEK_END)
                    # A file whose last line has no terminator would otherwise
                    # swallow the first new row onto the end of it.
                    needs_nl = f.read(1) not in (b"\n", b"\r")
            with open(args.results, "a", encoding="utf-8", newline="\n") as f:
                if not exists:
                    f.write("\t".join(COLUMNS) + "\n")
                elif needs_nl:
                    f.write("\n")
                for line in out["rows"]:
                    f.write(line + "\n")
            out["appended"] = len(out["rows"])
        print(json.dumps(out, allow_nan=False))
        return 0

    # Round 0: the baseline, one candidate, no comparison.
    if args.round == 0:
        cand = cands[0]
        trial = cand.get("trial") or {}
        if len(cands) > 1:
            warnings.append("baseline round has more than one candidate; only the first is used")
        if best is not None:
            warnings.append("results file already holds a baseline; re-baselining is unusual")
        if not trial.get("ok") or finite_primary(trial) is None:
            out["reason"] = f"baseline crashed ({crash_reason(trial)}); fix the harness with the user before looping"
            return finish()
        viol = check_stop.gate_violations(trial.get("counters") or {}, gates)
        if viol:
            out["reason"] = ("baseline violates a counter-metric gate (" + "; ".join(viol) +
                             "); resolve the threshold with the user before looping")
            return finish()
        missing = [name for name, _, _ in gates if (trial.get("counters") or {}).get(name) is None]
        if missing:
            out["reason"] = ("baseline did not extract counter-metric(s) " + ", ".join(missing) +
                             "; fix the extract pattern with the user before looping")
            return finish()
        out["rows"] = [row(0, cand, "keep", cand.get("description") or "baseline")]
        out["keep"] = clean(cand.get("candidate"), 40)
        out["keep_commit"] = clean(cand.get("commit"), 64)
        out["reason"] = "baseline recorded"
        return finish()

    if best is None:
        out["reason"] = "no valid baseline in the results file; run round 0 first"
        return finish()
    floor = check_stop.noise_floor(best, rules)
    out["noise_floor"] = floor
    if check_stop.floor_blocks_all(best, rules):
        warnings.append(
            f"noise floor {floor:.6g} is at or above best-so-far {best:.6g}: no candidate can be "
            f"kept from here; set min_delta_pct instead of an absolute min_delta for a metric that shrinks")

    survivors = []
    labelled = []
    for cand in cands:
        trial = cand.get("trial") or {}
        desc = cand.get("description") or "-"
        cand["_p"] = finite_primary(trial)
        if not trial.get("ok") or cand["_p"] is None:
            labelled.append((cand, "crash", f"crash ({crash_reason(trial)}): {desc}"))
            continue
        counters = trial.get("counters") or {}
        viol = check_stop.gate_violations(counters, gates)
        if viol:
            labelled.append((cand, "gate_fail", "gate_fail (" + "; ".join(viol) + f"): {desc}"))
            continue
        missing = [name for name, _, _ in gates if counters.get(name) is None]
        if missing:
            # A gate that cannot be evaluated is not passed. Treat it like a
            # violation so a candidate cannot win by breaking the counter's
            # extraction.
            labelled.append((cand, "gate_fail", "gate_fail (" + ", ".join(missing) +
                             f" did not extract): {desc}"))
            continue
        survivors.append(cand)
        labelled.append((cand, None, desc))

    keep = None
    top = None
    if survivors:
        key = (lambda c: c["_p"])
        top = min(survivors, key=key) if direction == "min" else max(survivors, key=key)
        if check_stop.improves(top["_p"], best, direction, floor):
            keep = top
            out["reason"] = (f"keep candidate {top['candidate']}: {top['_p']:.6g} beats "
                             f"best-so-far {best:.6g} by at least the noise floor {floor:.6g}")
        else:
            out["reason"] = (f"no keep: best survivor {top['_p']:.6g} does not beat "
                             f"best-so-far {best:.6g} by the noise floor {floor:.6g}")
    else:
        out["reason"] = "no keep: every candidate crashed or failed a gate"

    for cand, status, desc in labelled:
        if status is None:
            if cand is keep:
                status = "keep"
            else:
                status = "discard"
                if keep is not None and check_stop.improves(cand["_p"], best, direction, floor):
                    # An improvement that lost to a sibling is a live idea, not a dead end.
                    gap = abs(cand["_p"] - top["_p"])
                    inside = " inside the noise floor" if gap < floor else ""
                    desc = f"lost to {keep['candidate']}{inside}: {desc}"
        out["rows"].append(row(args.round, cand, status, desc))
    out["keep"] = keep["candidate"] if keep is not None else None
    out["keep_commit"] = clean(keep.get("commit"), 64) if keep is not None else None
    return finish()


def main():
    p = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    p.add_argument("--config", required=True)
    p.add_argument("--results", required=True)
    p.add_argument("--round", required=True, type=int)
    p.add_argument("--candidates", required=True, help="JSON file: list of candidate records")
    p.add_argument("--append", action="store_true",
                   help="append the rows to --results (creating it with the header if missing)")
    args = p.parse_args()
    try:
        res = run(args)
        if isinstance(res, dict):
            print(json.dumps(res, allow_nan=False))
        return 0
    except Exception as e:  # a traceback is not a JSON line the agent can act on
        print(json.dumps(empty(args.round, f"adjudicate.py failed: {type(e).__name__}: {e}"[:300]),
                         allow_nan=False))
        return 0


if __name__ == "__main__":
    sys.exit(main())
