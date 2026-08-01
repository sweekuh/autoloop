#!/usr/bin/env python3
"""Mechanical checks for the autoloop skill package.

The eval suite in evals/evals.json needs an LLM grading harness, so CI cannot
run it. These are the checks that CAN run deterministically anywhere: the
helper scripts compile and behave, the machine-readable listings parse, and the
docs' hard invariants (pure-ASCII output, frozen-harness wording) still hold.

Run locally exactly as CI does:  python3 tests/check.py
Exit code 0 = all checks passed.
"""
import io
import json
import os
import re
import subprocess
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
FIXTURES = os.path.join(ROOT, "tests", "fixtures")
PY = sys.executable
failures = []


def check(name, ok, detail=""):
    print(("PASS  " if ok else "FAIL  ") + name + (f" - {detail}" if detail and not ok else ""))
    if not ok:
        failures.append(name)


def read(*parts):
    with io.open(os.path.join(ROOT, *parts), encoding="utf-8") as f:
        return f.read()


# 1. The helpers compile.
for script in ("scripts/check_stop.py", "scripts/update_check.py", "scripts/log_run.py"):
    r = subprocess.run([PY, "-m", "py_compile", os.path.join(ROOT, script)],
                       capture_output=True, text=True)
    check(f"compiles: {script}", r.returncode == 0, r.stderr.strip())

# 2. check_stop.py runs against fixtures and returns a well-formed verdict.
r = subprocess.run(
    [PY, os.path.join(ROOT, "scripts", "check_stop.py"),
     "--config", os.path.join(FIXTURES, "loop_config.json"),
     "--results", os.path.join(FIXTURES, "results.tsv")],
    capture_output=True, text=True)
check("check_stop.py runs on fixtures", r.returncode == 0, r.stderr.strip())
try:
    verdict = json.loads(r.stdout.strip().splitlines()[-1])
except Exception as e:
    verdict = {}
    check("check_stop.py emits JSON", False, str(e))
else:
    check("check_stop.py emits JSON", True)
    check("check_stop.py verdict has stop/reason/stats",
          {"stop", "reason", "stats"} <= set(verdict), str(verdict))
    # The fixture has 2 consecutive keepless rounds and patience=2.
    check("check_stop.py honors patience (fixture expects stop=true)",
          verdict.get("stop") is True, str(verdict))
    check("check_stop.py groups rounds, not rows",
          verdict.get("stats", {}).get("rounds") == 4, str(verdict.get("stats")))

# 2b. Hard caps fire even when no keep row ever parsed. Before the ordering
#     fix, the "no successful trial yet" early return preceded max_rounds and
#     patience, so an all-crash run looped unbounded.
r = subprocess.run(
    [PY, os.path.join(ROOT, "scripts", "check_stop.py"),
     "--config", os.path.join(FIXTURES, "loop_config.json"),
     "--results", os.path.join(FIXTURES, "results-allcrash.tsv")],
    capture_output=True, text=True)
try:
    v = json.loads(r.stdout.strip().splitlines()[-1])
except Exception:
    v = {}
check("check_stop.py stops an all-crash run (patience over no-keep return)",
      v.get("stop") is True and "patience" in v.get("reason", ""), str(v))

# 2c. Integer-valued float round labels ('1.0') group as rounds, not rows.
#     Falling back to per-row rounds here re-created the candidate-vs-round
#     patience miscount the round grouping exists to prevent.
r = subprocess.run(
    [PY, os.path.join(ROOT, "scripts", "check_stop.py"),
     "--config", os.path.join(FIXTURES, "loop_config.json"),
     "--results", os.path.join(FIXTURES, "results-floatrounds.tsv")],
    capture_output=True, text=True)
try:
    v = json.loads(r.stdout.strip().splitlines()[-1])
except Exception:
    v = {}
check("check_stop.py groups float-labeled rounds correctly",
      v.get("stats", {}).get("rounds") == 2 and v.get("stop") is False, str(v))

# 3. update_check.py runs and emits a known status. In CI the checkout has no
#    tracking branch (detached HEAD), which must NOT fast-forward anything.
head_before = subprocess.run(["git", "-C", ROOT, "rev-parse", "HEAD"],
                             capture_output=True, text=True).stdout.strip()
r = subprocess.run([PY, os.path.join(ROOT, "scripts", "update_check.py"), "--check-only"],
                   capture_output=True, text=True)
check("update_check.py exits 0 (fails open)", r.returncode == 0, r.stderr.strip())
lines = [ln for ln in r.stdout.strip().splitlines() if ln.strip()]
try:
    uc = json.loads(lines[-1])
except Exception as e:
    uc = {}
    check("update_check.py emits JSON verdict", False, str(e))
else:
    check("update_check.py emits JSON verdict", True)
    known = {"updated", "up-to-date", "behind", "behind-dirty", "diverged",
             "no-upstream", "not-git", "offline", "error"}
    check("update_check.py status is a documented value",
          uc.get("status") in known, str(uc))
head_after = subprocess.run(["git", "-C", ROOT, "rev-parse", "HEAD"],
                            capture_output=True, text=True).stdout.strip()
check("update_check.py --check-only never moves HEAD", head_before == head_after)

# 4. update_check.py rejects unknown flags rather than silently updating.
r = subprocess.run([PY, os.path.join(ROOT, "scripts", "update_check.py"), "--dry-run"],
                   capture_output=True, text=True)
check("update_check.py rejects unknown flags", r.returncode != 0)

# 5. Script output stays pure ASCII (guards the non-UTF-8-console fix).
for script in ("scripts/check_stop.py", "scripts/update_check.py", "scripts/log_run.py"):
    src = read(script)
    bad = [c for c in src if ord(c) > 127]
    check(f"pure ASCII source: {script}", not bad, f"{len(bad)} non-ascii chars")

# 6. Machine-readable listings parse, and their ids are contiguous.
try:
    evals = json.loads(read("evals", "evals.json"))
    ids = [e["id"] for e in evals["evals"]]
    check("evals/evals.json parses", True)
    check("evals ids are 0-indexed and contiguous", ids == list(range(len(ids))), str(ids))
    check("every eval has assertions",
          all(e.get("assertions") for e in evals["evals"]))
except Exception as e:
    check("evals/evals.json parses", False, str(e))

try:
    tp = read("tests", "TEST_PLAN.md")
    block = re.search(r"```json\s*(\{.*?\})\s*```", tp, re.S).group(1)
    tj = json.loads(block)
    tids = [e["id"] for e in tj["evals"]]
    check("TEST_PLAN.md machine-readable block parses", True)
    check("TEST_PLAN ids are 1-indexed and contiguous",
          tids == list(range(1, len(tids) + 1)), str(tids))
    cases = re.findall(r"^## Case (\d+)", tp, re.M)
    check("TEST_PLAN prose cases match its index",
          [int(c) for c in cases] == tids, f"prose={cases} index={tids}")
except Exception as e:
    check("TEST_PLAN.md machine-readable block parses", False, str(e))

# 7. The frozen-harness invariant is still stated where contributors will see it.
check("CONTRIBUTING documents the frozen harness",
      "frozen" in read(".github", "CONTRIBUTING.md").lower())
check("SKILL.md still shells out to check_stop.py",
      "scripts/check_stop.py" in read("SKILL.md"))

# 8. Per-run results filename. A single shared results.tsv means the next run in
#    the same project truncates the previous run's log, which is the one piece of
#    the trajectory git does not already keep (loop_config.json is committed).
_skill = read("SKILL.md")
check("SKILL.md creates a per-run results file",
      "results-<run_tag>.tsv" in _skill)
check("SKILL.md does not create a shared results.tsv",
      "Create `results.tsv`" not in _skill)
check("SKILL.md check_stop invocation uses the per-run filename",
      "--results results-<run_tag>.tsv" in _skill)
check("SKILL.md Phase 0 reads prior runs",
      "results-*.tsv" in _skill)
check("SKILL.md documents the min_delta noise floor",
      "min_delta" in _skill)

print()
if failures:
    print(f"{len(failures)} check(s) failed: " + ", ".join(failures))
    sys.exit(1)
print("All checks passed.")
