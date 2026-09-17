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
import tempfile

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
SCRIPTS = ("scripts/check_stop.py", "scripts/run_trial.py", "scripts/adjudicate.py",
           "scripts/update_check.py", "scripts/log_run.py")
for script in SCRIPTS:
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

# 2d. A bounded primary that has reached its declared target stops on `target`,
#     not on patience. Without it, a maxed-out run burns `patience` rounds
#     proposing candidates that provably cannot improve the metric.
r = subprocess.run(
    [PY, os.path.join(ROOT, "scripts", "check_stop.py"),
     "--config", os.path.join(FIXTURES, "loop_config-target.json"),
     "--results", os.path.join(FIXTURES, "results-target.tsv")],
    capture_output=True, text=True)
try:
    v = json.loads(r.stdout.strip().splitlines()[-1])
except Exception:
    v = {}
check("check_stop.py stops on a reached target",
      v.get("stop") is True and "target reached" in v.get("reason", ""), str(v))
check("check_stop.py reports the target in stats",
      v.get("stats", {}).get("target") == 16, str(v.get("stats")))

# 2e. `target` is opt-in. The same log without it must not stop, or adding the
#     condition would silently change the verdict for every existing config.
_cfg = json.loads(read("tests", "fixtures", "loop_config-target.json"))
_cfg.pop("target")
_tmp = os.path.join(tempfile.mkdtemp(), "loop_config.json")
with io.open(_tmp, "w", encoding="utf-8") as f:
    f.write(json.dumps(_cfg))
r = subprocess.run(
    [PY, os.path.join(ROOT, "scripts", "check_stop.py"),
     "--config", _tmp,
     "--results", os.path.join(FIXTURES, "results-target.tsv")],
    capture_output=True, text=True)
try:
    v = json.loads(r.stdout.strip().splitlines()[-1])
except Exception:
    v = {}
check("check_stop.py target is opt-in (absent -> no stop)",
      v.get("stop") is False, str(v))

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

# 4b. log_run.py computes a ledger row from the fixtures without writing.
r = subprocess.run(
    [PY, os.path.join(ROOT, "scripts", "log_run.py"),
     "--config", os.path.join(FIXTURES, "loop_config.json"),
     "--results", os.path.join(FIXTURES, "results.tsv"),
     "--label", "smoke", "--dry-run"],
    capture_output=True, text=True)
check("log_run.py --dry-run runs on fixtures", r.returncode == 0, r.stderr.strip())
fields = r.stdout.strip().split("\t")
check("log_run.py row has 16 fields", len(fields) == 16, str(fields))
check("log_run.py computes baseline and best", "100" in fields and "95" in fields, str(fields))

# 5. Script output stays pure ASCII (guards the non-UTF-8-console fix).
for script in SCRIPTS:
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
check("SKILL.md documents the target stop condition",
      "**target**" in _skill and '"target": null' in _skill)


def stop_verdict(config, results):
    r = subprocess.run(
        [PY, os.path.join(ROOT, "scripts", "check_stop.py"), "--config", config, "--results", results],
        capture_output=True, text=True)
    try:
        return json.loads(r.stdout.strip().splitlines()[-1])
    except Exception:
        return {"_stderr": r.stderr.strip()}


# 9. check_stop.py audits keep rows instead of trusting the status column. A
#    keep below the noise floor or one that violates a declared gate must be
#    warned about and treated as no keep, so it cannot reset patience.
v = stop_verdict(os.path.join(FIXTURES, "loop_config-audit.json"),
                 os.path.join(FIXTURES, "results-badkeep.tsv"))
check("check_stop.py verdict carries a warnings list", isinstance(v.get("warnings"), list), str(v))
check("check_stop.py warns on a sub-min_delta keep and a gate-violating keep",
      len(v.get("warnings", [])) == 2
      and "min_delta" in v["warnings"][0] and "violates gate" in v["warnings"][1], str(v.get("warnings")))
check("check_stop.py treats invalid keeps as no keep (patience fires)",
      v.get("stop") is True and "patience" in v.get("reason", "")
      and v.get("stats", {}).get("rounds_since_keep") == 8 and v.get("stats", {}).get("best") == 100.0, str(v))
for cfg_name, res_name in (("loop_config.json", "results.tsv"),
                           ("loop_config.json", "results-allcrash.tsv"),
                           ("loop_config.json", "results-floatrounds.tsv"),
                           ("loop_config-target.json", "results-target.tsv")):
    v = stop_verdict(os.path.join(FIXTURES, cfg_name), os.path.join(FIXTURES, res_name))
    check(f"check_stop.py audit is silent on a clean log ({res_name})", v.get("warnings") == [], str(v))
_cfg = json.loads(read("tests", "fixtures", "loop_config.json"))
del _cfg["primary"]["direction"]
_nodir = os.path.join(tempfile.mkdtemp(), "loop_config.json")
with io.open(_nodir, "w", encoding="utf-8") as f:
    f.write(json.dumps(_cfg))
v = stop_verdict(_nodir, os.path.join(FIXTURES, "results.tsv"))
check("check_stop.py refuses a primary with no direction",
      v.get("stop") is True and "direction" in v.get("reason", ""), str(v))

# 10. epsilon: null means 0.5% of baseline, derived by check_stop.py. The
#     template used to ship epsilon 0.001, which with patience < epsilon_window
#     and any min_delta >= 0.001 could never fire.
v = stop_verdict(os.path.join(FIXTURES, "loop_config-epsilon.json"),
                 os.path.join(FIXTURES, "results-epsilon.tsv"))
check("check_stop.py derives epsilon from baseline when null",
      v.get("stop") is True and v.get("reason", "").startswith("diminishing returns")
      and abs(v.get("stats", {}).get("epsilon_effective", 0) - 4.21) < 1e-9, str(v))
_cfg = json.loads(read("tests", "fixtures", "loop_config-epsilon.json"))
_cfg["epsilon"] = 0.001
_eps = os.path.join(tempfile.mkdtemp(), "loop_config.json")
with io.open(_eps, "w", encoding="utf-8") as f:
    f.write(json.dumps(_cfg))
v = stop_verdict(_eps, os.path.join(FIXTURES, "results-epsilon.tsv"))
check("check_stop.py keeps an explicit epsilon as given",
      v.get("stop") is False and "epsilon_effective" not in v.get("stats", {}), str(v))
check("SKILL.md template ships epsilon null", '"epsilon": null' in _skill)

# 11. run_trial.py and adjudicate.py: the keep / discard / gate_fail / crash
#     decision comes out of the frozen scripts, on a tiny fixture project. The
#     interpreter path is substituted into the commands so the fixture runs on
#     Windows too (no grep, no python3 alias).
import shutil
_proj = os.path.join(tempfile.mkdtemp(), "proj")
shutil.copytree(os.path.join(FIXTURES, "trialproj"), _proj)
_cfg = json.loads(read("tests", "fixtures", "trialproj", "loop_config.json"))
_pyq = '"' + PY + '"'
_cfg["eval_command"] = _pyq + " bench.py > run.log 2>&1"
_cfg["primary"]["extract"] = _pyq + " extract.py runtime_ms"
_cfg["counter_metrics"][0]["extract"] = _pyq + " extract.py tests_passed"
_cfgp = os.path.join(_proj, "loop_config.json")
with io.open(_cfgp, "w", encoding="utf-8") as f:
    f.write(json.dumps(_cfg))


def run_trial(mode):
    env = dict(os.environ, AUTOLOOP_FIXTURE_MODE=mode)
    r = subprocess.run([PY, os.path.join(ROOT, "scripts", "run_trial.py"), "--config", _cfgp, "--cwd", _proj],
                       capture_output=True, text=True, env=env)
    try:
        return json.loads(r.stdout.strip().splitlines()[-1])
    except Exception:
        return {"_stderr": r.stderr.strip(), "_rc": r.returncode}


def adjudicate(results, rnum, cands):
    cpath = os.path.join(_proj, f"cands-{rnum}.json")
    with io.open(cpath, "w", encoding="utf-8") as f:
        f.write(json.dumps(cands))
    r = subprocess.run([PY, os.path.join(ROOT, "scripts", "adjudicate.py"), "--config", _cfgp,
                        "--results", results, "--round", str(rnum), "--candidates", cpath],
                       capture_output=True, text=True)
    try:
        return json.loads(r.stdout.strip().splitlines()[-1])
    except Exception:
        return {"_stderr": r.stderr.strip(), "_rc": r.returncode}


t_ok = run_trial("ok")
check("run_trial.py extracts the primary and the counter",
      t_ok.get("ok") is True and t_ok.get("primary") == 123.4
      and t_ok.get("counters", {}).get("tests_passed") == 42.0, str(t_ok))
check("run_trial.py output is pure ASCII", all(ord(c) < 128 for c in json.dumps(t_ok)))
t_to = run_trial("sleep")
check("run_trial.py enforces trial_timeout_seconds",
      t_to.get("ok") is False and t_to.get("timed_out") is True and t_to.get("elapsed_s", 99) < 15, str(t_to))
t_cr = run_trial("crash")
check("run_trial.py reports a crash with a tail",
      t_cr.get("ok") is False and t_cr.get("primary") is None
      and any("harness broke" in ln for ln in t_cr.get("tail", [])), str(t_cr))
t_gate = run_trial("gate")
check("run_trial.py takes the last matching line for a counter",
      t_gate.get("ok") is True and t_gate.get("counters", {}).get("tests_passed") == 10.0, str(t_gate))

_results = os.path.join(_proj, "results-fixture-trial.tsv")
with io.open(_results, "w", encoding="utf-8", newline="\n") as f:
    f.write("round\tcandidate\tcommit\tprimary\tcounters\tstatus\tdescription\n")


def append_rows(verdict):
    with io.open(_results, "a", encoding="utf-8", newline="\n") as f:
        for line in verdict.get("rows", []):
            f.write(line + "\n")


a0 = adjudicate(_results, 0, [{"candidate": "0", "commit": "-", "description": "baseline", "trial": t_ok}])
check("adjudicate.py records the baseline as keep",
      a0.get("keep") == "0" and len(a0.get("rows", [])) == 1 and a0["rows"][0].endswith("\tkeep\tbaseline"), str(a0))
append_rows(a0)
better = dict(t_ok, primary=100.0)
much = dict(t_ok, primary=50.0)
a1 = adjudicate(_results, 1, [
    {"candidate": "0", "commit": "aaa1111", "description": "tighter loop", "trial": better},
    {"candidate": "1", "commit": "bbb2222", "description": "drop the checks\tfor speed", "trial": dict(t_gate, primary=40.0)},
    {"candidate": "2", "commit": "ccc3333", "description": "typo", "trial": t_cr},
    {"candidate": "3", "commit": "ddd4444", "description": "hung", "trial": t_to},
    {"candidate": "4", "commit": "eee5555", "description": "memoize", "trial": much},
])
_statuses = [ln.split("\t")[5] for ln in a1.get("rows", [])]
check("adjudicate.py labels crash, gate_fail, discard and keep",
      _statuses == ["discard", "gate_fail", "crash", "crash", "keep"] and a1.get("keep") == "4"
      and a1.get("keep_commit") == "eee5555", str(a1))
check("adjudicate.py refuses a gate-violating winner",
      "gate_fail (tests_passed=10 fails >= 42)" in a1.get("rows", ["", ""])[1], str(a1.get("rows")))
check("adjudicate.py rows have 7 tab-separated fields and no embedded tabs in descriptions",
      all(len(ln.split("\t")) == 7 for ln in a1.get("rows", [])), str(a1.get("rows")))
append_rows(a1)
a2 = adjudicate(_results, 2, [{"candidate": "0", "commit": "fff6666", "description": "noise", "trial": dict(t_ok, primary=49.7)}])
check("adjudicate.py discards a sub-min_delta improvement",
      a2.get("keep") is None and [ln.split("\t")[5] for ln in a2.get("rows", [])] == ["discard"], str(a2))
append_rows(a2)
v = stop_verdict(_cfgp, _results)
check("check_stop.py accepts what adjudicate.py wrote with zero warnings",
      v.get("stop") is False and v.get("warnings") == [] and v.get("stats", {}).get("best") == 50.0, str(v))
_bad = os.path.join(_proj, "results-bad.tsv")
with io.open(_bad, "w", encoding="utf-8", newline="\n") as f:
    f.write("round\tcandidate\tcommit\tprimary\tcounters\tstatus\tdescription\n")
ab = adjudicate(_bad, 0, [{"candidate": "0", "commit": "-", "description": "baseline", "trial": t_gate}])
check("adjudicate.py refuses a baseline that violates a gate",
      ab.get("rows") == [] and "gate" in ab.get("reason", ""), str(ab))
ab = adjudicate(_bad, 1, [{"candidate": "0", "commit": "x", "description": "x", "trial": t_ok}])
check("adjudicate.py refuses to adjudicate a round with no baseline",
      ab.get("rows") == [] and "baseline" in ab.get("reason", ""), str(ab))
check("SKILL.md shells out to run_trial.py and adjudicate.py",
      "scripts/run_trial.py" in _skill and "scripts/adjudicate.py" in _skill)
check("SKILL.md says the agent never writes a status label",
      "never write a status label" in _skill)


print()
if failures:
    print(f"{len(failures)} check(s) failed: " + ", ".join(failures))
    sys.exit(1)
print("All checks passed.")
