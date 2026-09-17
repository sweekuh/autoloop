#!/usr/bin/env python3
"""Mechanical checks for the autoloop skill package.

The eval suite in evals/evals.json needs an LLM grading harness, so CI cannot
run it. These are the checks that CAN run deterministically anywhere: the
helper scripts compile and behave, the machine-readable listings parse, and the
docs' hard invariants (pure-ASCII output, frozen-harness wording) still hold.

Run locally exactly as CI does:  python3 tests/check.py
Exit code 0 = all checks passed.
"""
import atexit
import io
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
FIXTURES = os.path.join(ROOT, "tests", "fixtures")
PY = sys.executable
os.environ["PYTHONDONTWRITEBYTECODE"] = "1"  # no scripts/__pycache__ in the checkout from the checks
failures = []


def check(name, ok, detail=""):
    print(("PASS  " if ok else "FAIL  ") + name + (f" - {detail}" if detail and not ok else ""))
    if not ok:
        failures.append(name)


def read(*parts):
    with io.open(os.path.join(ROOT, *parts), encoding="utf-8") as f:
        return f.read()


_TMPDIRS = []


def tmpdir():
    """A temp directory that is removed when the checks exit (so CI and dev boxes don't accumulate them)."""
    d = tempfile.mkdtemp(prefix="autoloop-check-")
    _TMPDIRS.append(d)
    return d


atexit.register(lambda: [shutil.rmtree(d, ignore_errors=True) for d in _TMPDIRS])


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
_tmp = os.path.join(tmpdir(), "loop_config.json")
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
      and "noise floor" in v["warnings"][0] and "violates gate" in v["warnings"][1], str(v.get("warnings")))
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
_nodir = os.path.join(tmpdir(), "loop_config.json")
with io.open(_nodir, "w", encoding="utf-8") as f:
    f.write(json.dumps(_cfg))
v = stop_verdict(_nodir, os.path.join(FIXTURES, "results.tsv"))
check("check_stop.py refuses a primary with no direction",
      v.get("stop") is True and "direction" in v.get("reason", ""), str(v))
v = stop_verdict(os.path.join(FIXTURES, "loop_config-audit.json"), os.path.join(FIXTURES, "results-dupkeep.tsv"))
check("check_stop.py rejects two keeps in a round, a keep without a primary, and a keep missing its gated counter",
      len(v.get("warnings", [])) == 3 and v.get("stats", {}).get("best") == 100.0
      and v.get("stats", {}).get("rounds_since_keep") == 3, str(v))
_cfg = json.loads(read("tests", "fixtures", "loop_config.json"))
_cfg["max_rounds"] = 3  # 2 round labels stay under the round cap; 4 rows exceed 3 x 1
_cap = os.path.join(tmpdir(), "loop_config.json")
with io.open(_cap, "w", encoding="utf-8") as f:
    f.write(json.dumps(_cfg))
v = stop_verdict(_cap, os.path.join(FIXTURES, "results-reusedround.tsv"))
check("check_stop.py caps candidate rows so a reused round label still terminates",
      v.get("stop") is True and "candidate rows" in v.get("reason", ""), str(v))
_cfg = json.loads(read("tests", "fixtures", "loop_config.json"))
_cfg["patience"] = "abc"
_cfg["epsilon_window"] = None
_bad = os.path.join(tmpdir(), "loop_config.json")
with io.open(_bad, "w", encoding="utf-8") as f:
    f.write(json.dumps(_cfg))
v = stop_verdict(_bad, os.path.join(FIXTURES, "results.tsv"))
check("check_stop.py answers a non-numeric config value with a verdict and a warning, not a traceback",
      "_stderr" not in v and isinstance(v.get("stop"), bool) and any("patience" in w for w in v.get("warnings", [])), str(v))
_bom = os.path.join(tmpdir(), "results-bom.tsv")
with io.open(_bom, "w", encoding="utf-8-sig", newline="\n") as f:
    f.write(read("tests", "fixtures", "results.tsv"))
v = stop_verdict(os.path.join(FIXTURES, "loop_config.json"), _bom)
check("check_stop.py tolerates a UTF-8 BOM in the results header",
      v.get("stats", {}).get("rounds") == 4 and v.get("warnings") == [], str(v))
v = stop_verdict(os.path.join(FIXTURES, "loop_config.json"), os.path.join(FIXTURES, "results-quoted.tsv"))
check("check_stop.py reads the log as plain TSV (a description opening with a quote hides nothing)",
      v.get("stats", {}).get("rounds") == 4 and v.get("stats", {}).get("candidates") == 4, str(v))
v = stop_verdict(os.path.join(FIXTURES, "loop_config-audit.json"), os.path.join(FIXTURES, "results-lowerbar.tsv"))
check("check_stop.py lets a rejected keep raise the bar but never lower it",
      v.get("stats", {}).get("best") == 100.0 and v.get("stats", {}).get("bar") == 50.0
      and v.get("stats", {}).get("rounds_since_keep") == 2, str(v))

# 10. epsilon: null means max(0.5% of baseline, 2x the noise floor at
#     best-so-far), derived by check_stop.py. The template used to ship
#     epsilon 0.001, which with patience < epsilon_window and any min_delta
#     >= 0.001 could never fire.
v = stop_verdict(os.path.join(FIXTURES, "loop_config-epsilon.json"),
                 os.path.join(FIXTURES, "results-epsilon.tsv"))
check("check_stop.py derives epsilon from baseline when null",
      v.get("stop") is True and v.get("reason", "").startswith("diminishing returns")
      and abs(v.get("stats", {}).get("epsilon_effective", 0) - 4.21) < 1e-9, str(v))
_cfg = json.loads(read("tests", "fixtures", "loop_config-epsilon.json"))
_cfg["epsilon"] = 0.001
_eps = os.path.join(tmpdir(), "loop_config.json")
with io.open(_eps, "w", encoding="utf-8") as f:
    f.write(json.dumps(_cfg))
v = stop_verdict(_eps, os.path.join(FIXTURES, "results-epsilon.tsv"))
check("check_stop.py keeps an explicit epsilon as given",
      v.get("stop") is False and "epsilon_effective" not in v.get("stats", {}), str(v))
check("SKILL.md template ships epsilon null", '"epsilon": null' in _skill)

# 10b. The noise floor can be relative. An absolute min_delta grounded at the
#      baseline made every keep impossible once the metric shrank past it (seen
#      on the first dogfood run: a 90 ms floor from a 1319 ms baseline discarded
#      a 16.9 ms candidate against a 73 ms best). min_delta_pct scales with
#      best-so-far; an absolute floor that blocks everything is warned about.
v = stop_verdict(os.path.join(FIXTURES, "loop_config-pct.json"),
                 os.path.join(FIXTURES, "results-pct.tsv"))
check("check_stop.py applies min_delta_pct relative to best-so-far",
      v.get("stats", {}).get("best") == 400.0 and len(v.get("warnings", [])) == 1
      and "noise floor 35" in v["warnings"][0], str(v))
check("check_stop.py reports the effective noise floor",
      abs(v.get("stats", {}).get("noise_floor", 0) - 28.0) < 1e-9, str(v.get("stats")))
check("check_stop.py derives epsilon as max(0.5% baseline, 2x floor)",
      abs(v.get("stats", {}).get("epsilon_effective", 0) - 56.0) < 1e-9, str(v.get("stats")))
v = stop_verdict(os.path.join(FIXTURES, "loop_config-floorblock.json"),
                 os.path.join(FIXTURES, "results-floorblock.tsv"))
check("check_stop.py warns when an absolute floor blocks every further keep",
      v.get("stats", {}).get("best") == 5.0 and any("no candidate can be kept" in w for w in v.get("warnings", [])), str(v))
check("SKILL.md template ships min_delta_pct", '"min_delta_pct": 0.0' in _skill)
check("SKILL.md Phase 2 grounds the floor as a percentage", "set `min_delta_pct` to at least" in _skill)

# 11. run_trial.py and adjudicate.py: the keep / discard / gate_fail / crash
#     decision comes out of the frozen scripts, on a tiny fixture project. The
#     interpreter path is substituted into the commands so the fixture runs on
#     Windows too (no grep, no python3 alias).
_proj = os.path.join(tmpdir(), "proj")
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
      t_to.get("ok") is False and t_to.get("timed_out") is True and t_to.get("elapsed_s", 99) < 30, str(t_to))
t_cr = run_trial("crash")
check("run_trial.py reports a crash with a tail",
      t_cr.get("ok") is False and t_cr.get("primary") is None
      and any("harness broke" in ln for ln in t_cr.get("tail", [])), str(t_cr))
t_gate = run_trial("gate")
check("run_trial.py reads a counter through the fixture extract (which picks the last matching line)",
      t_gate.get("ok") is True and t_gate.get("counters", {}).get("tests_passed") == 10.0, str(t_gate))
t_fail = run_trial("fail")
check("run_trial.py files a non-zero eval exit as a crash even when the metric printed",
      t_fail.get("ok") is False and t_fail.get("exit_code") == 3 and t_fail.get("primary") == 123.4, str(t_fail))
check("run_trial.py escapes non-ASCII eval output in the tail (backslashreplace)",
      any("caf\\xe9" in ln or "caf\\ufffd" in ln for ln in t_cr.get("tail", [])), str(t_cr.get("tail")))


def trial_with_extract(extract_cmd, **cfg_overrides):
    cfg = json.loads(json.dumps(_cfg))
    cfg["primary"]["extract"] = extract_cmd
    cfg.update(cfg_overrides)
    path = os.path.join(_proj, "loop_config-extract.json")
    with io.open(path, "w", encoding="utf-8") as f:
        f.write(json.dumps(cfg))
    r = subprocess.run([PY, os.path.join(ROOT, "scripts", "run_trial.py"), "--config", path, "--cwd", _proj],
                       capture_output=True, text=True, env=dict(os.environ, AUTOLOOP_FIXTURE_MODE="ok"))
    try:
        return json.loads(r.stdout.strip().splitlines()[-1])
    except Exception:
        return {"_stderr": r.stderr.strip(), "_rc": r.returncode}


_emit = _pyq + " emit.py "
_two = trial_with_extract(_emit + "two-lines")
check("run_trial.py refuses an extract that prints two lines (an artifact printing its own metric line)",
      _two.get("primary") is None and _two.get("ok") is False, str(_two))
_twonum = trial_with_extract(_emit + "two-numbers")
check("run_trial.py refuses an extract line holding two numbers (a number appended to the metric line)",
      _twonum.get("primary") is None and _twonum.get("ok") is False, str(_twonum))
_err = trial_with_extract(_emit + "stderr-fail")
check("run_trial.py never scrapes digits from an extract's stderr or a failed extract",
      _err.get("primary") is None, str(_err))
_inf = trial_with_extract(_emit + "inf")
check("run_trial.py treats a non-finite metric as unreadable", _inf.get("primary") is None, str(_inf))
_lbl = trial_with_extract(_emit + "ok")
check("run_trial.py reads a labelled metric line (the label's own digits do not count)",
      _lbl.get("primary") == 7.5 and _lbl.get("ok") is True, str(_lbl))
_nto = trial_with_extract(_cfg["primary"]["extract"], trial_timeout_seconds=None)
check("run_trial.py falls back to a 600 s timeout when the config has none",
      _nto.get("ok") is True and any("using 600s" in ln for ln in _nto.get("tail", [])), str(_nto))

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
check("adjudicate.py marks an improvement that lost to a sibling as a live idea",
      a1.get("rows", [""])[0].endswith("\tdiscard\tlost to 4: tighter loop"), str(a1.get("rows", [""])[0]))
check("adjudicate.py rows have 7 tab-separated fields and no embedded tabs in descriptions",
      all(len(ln.split("\t")) == 7 for ln in a1.get("rows", [])), str(a1.get("rows")))
append_rows(a1)
a2 = adjudicate(_results, 2, [{"candidate": "0", "commit": "fff6666", "description": "noise", "trial": dict(t_ok, primary=49.7)}])
check("adjudicate.py discards a sub-min_delta improvement",
      a2.get("keep") is None and [ln.split("\t")[5] for ln in a2.get("rows", [])] == ["discard"], str(a2))
append_rows(a2)
_cfg_pct = dict(_cfg, min_delta=0.0, min_delta_pct=10.0)
_cfgp_pct = os.path.join(_proj, "loop_config-pct.json")
with io.open(_cfgp_pct, "w", encoding="utf-8") as f:
    f.write(json.dumps(_cfg_pct))
_saved_cfgp = _cfgp
_cfgp = _cfgp_pct
a3 = adjudicate(_results, 3, [
    {"candidate": "0", "commit": "abc0001", "description": "8 percent, inside a 10 percent floor", "trial": dict(t_ok, primary=46.0)},
    {"candidate": "1", "commit": "abc0002", "description": "12 percent, a keep", "trial": dict(t_ok, primary=44.0)},
])
_cfgp = _saved_cfgp
check("adjudicate.py applies min_delta_pct against best-so-far",
      a3.get("keep") == "1" and abs(a3.get("noise_floor", 0) - 5.0) < 1e-9
      and [ln.split("\t")[5] for ln in a3.get("rows", [])] == ["discard", "keep"], str(a3))
v = stop_verdict(_cfgp, _results)
check("check_stop.py accepts what adjudicate.py wrote with zero warnings",
      v.get("stop") is False and v.get("warnings") == [] and v.get("stats", {}).get("best") == 50.0, str(v))
_before = len(io.open(_results, encoding="utf-8").read().splitlines())
_cpath = os.path.join(_proj, "cands-append.json")
with io.open(_cpath, "w", encoding="utf-8") as f:
    f.write(json.dumps([
        {"candidate": "0", "commit": "abc0004", "description": "a keep, appended by the script", "trial": dict(t_ok, primary=45.0)},
        {"candidate": "1", "commit": "abc0005", "description": "lost by 0.2, inside the floor", "trial": dict(t_ok, primary=45.2)},
    ]))
r = subprocess.run([PY, os.path.join(ROOT, "scripts", "adjudicate.py"), "--config", _cfgp,
                    "--results", _results, "--round", "4", "--candidates", _cpath, "--append"],
                   capture_output=True, text=True)
try:
    a4 = json.loads(r.stdout.strip().splitlines()[-1])
except Exception:
    a4 = {"_stderr": r.stderr.strip()}
_after = io.open(_results, encoding="utf-8").read().splitlines()
check("adjudicate.py --append writes its rows into the results file",
      a4.get("appended") == 2 and len(_after) == _before + 2 and _after[-2].endswith("\tkeep\ta keep, appended by the script"), str(a4))
check("adjudicate.py flags a sibling that lost inside the noise floor",
      _after[-1].endswith("\tdiscard\tlost to 0 inside the noise floor: lost by 0.2, inside the floor"), _after[-1])
v = stop_verdict(_cfgp, _results)
check("check_stop.py reads the appended round with zero warnings",
      v.get("warnings") == [] and v.get("stats", {}).get("best") == 45.0 and v.get("stats", {}).get("rounds") == 4, str(v))
check("SKILL.md uses adjudicate.py --append", "--candidates candidates.json --append" in _skill)

# 11b. adjudicate.py is only as frozen as its inputs: best-so-far must come
#      from the audited log, an unevaluable gate is not a passed gate, a round
#      number is used once, agent-written candidate ids cannot mislabel rows,
#      and garbage input yields a JSON line rather than a traceback.
_bogus = os.path.join(_proj, "results-bogus.tsv")
with io.open(_bogus, "w", encoding="utf-8", newline="\n") as f:
    f.write("round\tcandidate\tcommit\tprimary\tcounters\tstatus\tdescription\n"
            "0\t0\t-\t100\ttests_passed=42\tkeep\tbaseline\n"
            "1\t0\tx\t5\ttests_passed=10\tkeep\tmislabeled: violates the gate\n")
ab = adjudicate(_bogus, 2, [{"candidate": "0", "commit": "y", "description": "real", "trial": dict(t_ok, primary=90.0)}])
check("adjudicate.py takes best-so-far from the audited log, not from raw keep rows",
      ab.get("best_so_far") == 100.0 and ab.get("keep") == "0", str(ab))
am = adjudicate(_bogus, 3, [{"candidate": "0", "commit": "z", "description": "broke the counter",
                            "trial": dict(t_ok, primary=1.0, counters={})}])
check("adjudicate.py files a candidate whose gated counter did not extract as gate_fail",
      [ln.split("\t")[5] for ln in am.get("rows", [])] == ["gate_fail"] and "did not extract" in am.get("rows", [""])[0], str(am))
ad = adjudicate(_results, 4, [{"candidate": "0", "commit": "w", "description": "again", "trial": dict(t_ok, primary=10.0)}])
check("adjudicate.py refuses to write into a round that already has rows",
      ad.get("rows") == [] and "already has rows" in ad.get("reason", ""), str(ad))
ai = adjudicate(_bogus, 5, [{"candidate": "0", "commit": "p", "description": "first", "trial": dict(t_ok, primary=80.0)},
                            {"candidate": "0", "commit": "q", "description": "second", "trial": dict(t_ok, primary=70.0)}])
check("adjudicate.py renames a duplicated candidate id and keeps exactly one",
      [ln.split("\t")[5] for ln in ai.get("rows", [])].count("keep") == 1
      and any("duplicated" in w for w in ai.get("warnings", [])) and ai.get("keep") == "0-1", str(ai))
ag = adjudicate(_bogus, 6, "garbage")
check("adjudicate.py answers garbage candidates with a JSON line, not a traceback",
      ag.get("rows") == [] and "no candidates" in ag.get("reason", ""), str(ag))
ag = adjudicate(_bogus, 6, ["not an object"])
check("adjudicate.py files a malformed candidate record as a crash",
      [ln.split("\t")[5] for ln in ag.get("rows", [])] == ["crash"], str(ag))
ag = adjudicate(_bogus, 7, [
    {"candidate": "0", "commit": "s", "description": "string metrics", "trial": dict(t_ok, primary="fast")},
    {"candidate": "1", "commit": "t", "description": "a real improvement", "trial": dict(t_ok, primary=90.0)},
])
check("adjudicate.py files a non-numeric metric as one crash without sinking the round",
      [ln.split("\t")[5] for ln in ag.get("rows", [])] == ["crash", "keep"], str(ag))
ai = adjudicate(_bogus, 8, [{"candidate": "0", "commit": "u", "description": "tabbed counter name",
                             "trial": dict(t_ok, primary=80.0, counters={"tests\tpassed": 42, "tests_passed": 42})}])
check("adjudicate.py normalises counter names so none can shift the TSV columns",
      all(len(ln.split("\t")) == 7 for ln in ai.get("rows", []))
      and ai.get("rows", [""])[0].split("\t")[4] == "tests_passed=42", str(ai))
ai = adjudicate(_bogus, 9, [{"candidate": "0", "commit": "v", "description": "two names, one gate",
                             "trial": dict(t_ok, primary=80.0, counters={"tests passed": 10, "tests_passed": 42})}])
check("adjudicate.py refuses a gate whose counter arrives under two colliding names",
      [ln.split("\t")[5] for ln in ai.get("rows", [])] == ["gate_fail"]
      and "did not extract" in ai.get("rows", [""])[0], str(ai))
_dup = os.path.join(_proj, "results-dupcounter.tsv")
with io.open(_dup, "w", encoding="utf-8", newline="\n") as f:
    f.write("round\tcandidate\tcommit\tprimary\tcounters\tstatus\tdescription\n"
            "0\t0\t-\t100\ttests_passed=42\tkeep\tbaseline\n"
            "1\t0\tx\t50\ttests_passed=10,tests_passed=42\tkeep\ttwo values for one gate\n")
v = stop_verdict(_cfgp, _dup)
check("check_stop.py refuses a keep row carrying two values for one gated counter",
      v.get("stats", {}).get("best") == 100.0
      and any("no value for gated counter" in w for w in v.get("warnings", [])), str(v))
_noeol = os.path.join(_proj, "results-noeol.tsv")
with io.open(_noeol, "w", encoding="utf-8", newline="\n") as f:
    f.write("round\tcandidate\tcommit\tprimary\tcounters\tstatus\tdescription\n"
            "0\t0\t-\t100\ttests_passed=42\tkeep\tbaseline")  # no trailing newline
r = subprocess.run([PY, os.path.join(ROOT, "scripts", "adjudicate.py"), "--config", _cfgp,
                    "--results", _noeol, "--round", "1", "--candidates", _cpath, "--append"],
                   capture_output=True, text=True)
_lines = io.open(_noeol, encoding="utf-8").read().splitlines()
check("adjudicate.py --append never concatenates a row onto an unterminated last line",
      len(_lines) == 4 and _lines[1].endswith("baseline"), str(_lines))
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

# 12. log_run.py must not dirty the skill checkout by default. A modified
#     tracked file makes update_check.py return behind-dirty, which silently
#     disabled self-update for every user after their first logged run.
_clone = os.path.join(tmpdir(), "skill")
_ls = subprocess.run(["git", "-C", ROOT, "ls-files", "-z"], capture_output=True)
_tracked = [p for p in _ls.stdout.decode("utf-8", "replace").split("\0") if p] if _ls.returncode == 0 else []
if _tracked:  # tracked files only: never a .venv, a worktree, or a big runs/ export
    for _rel in _tracked:
        _src = os.path.join(ROOT, _rel)
        if not os.path.isfile(_src):
            continue
        _dst = os.path.join(_clone, _rel)
        os.makedirs(os.path.dirname(_dst), exist_ok=True)
        shutil.copy2(_src, _dst)
else:  # not a git checkout (a tarball install, or no git): copy what matters
    for _rel in ("scripts", "runs", "README.md", "SKILL.md"):
        _src = os.path.join(ROOT, _rel)
        if os.path.isdir(_src):
            shutil.copytree(_src, os.path.join(_clone, _rel), ignore=shutil.ignore_patterns("__pycache__", "local"))
        elif os.path.isfile(_src):
            shutil.copy2(_src, os.path.join(_clone, _rel))
_git = ["git", "-C", _clone, "-c", "user.name=t", "-c", "user.email=t@t"]
subprocess.run(_git + ["init", "-q"], capture_output=True)
subprocess.run(_git + ["add", "-A"], capture_output=True)
subprocess.run(_git + ["commit", "-q", "-m", "snapshot"], capture_output=True)
r = subprocess.run(
    [PY, os.path.join(_clone, "scripts", "log_run.py"),
     "--config", os.path.join(FIXTURES, "loop_config.json"),
     "--results", os.path.join(FIXTURES, "results.tsv"),
     "--label", "smoke", "--stop", "patience"],
    capture_output=True, text=True)
check("log_run.py runs for real against a snapshot checkout", r.returncode == 0, r.stderr.strip())
_porcelain = subprocess.run(_git + ["status", "--porcelain"], capture_output=True, text=True).stdout.strip()
check("log_run.py leaves the checkout clean by default", _porcelain == "", _porcelain)
_local = os.path.join(_clone, "runs", "local", "RUNS.tsv")
check("log_run.py wrote the gitignored local ledger",
      os.path.exists(_local) and len(io.open(_local, encoding="utf-8").read().strip().splitlines()) == 2)
r = subprocess.run(
    [PY, os.path.join(_clone, "scripts", "log_run.py"),
     "--config", os.path.join(FIXTURES, "loop_config.json"),
     "--results", os.path.join(FIXTURES, "results.tsv"),
     "--label", "smoke", "--stop", "patience", "--publish"],
    capture_output=True, text=True)
_porcelain = subprocess.run(_git + ["status", "--porcelain"], capture_output=True, text=True).stdout.strip()
check("log_run.py --publish updates the tracked ledger and README",
      r.returncode == 0 and "README.md" in _porcelain and "runs/RUNS.tsv" in _porcelain, _porcelain or r.stderr)
check("SKILL.md Phase 4 logs to the local ledger", "runs/local/RUNS.tsv" in _skill)
check(".gitignore excludes runs/local/", "runs/local/" in read(".gitignore"))

# 13. windows-which: find_git() never consults the current directory. GIT_BIN
#    used to come from shutil.which("git"), which on Windows under Python < 3.12
#    prepends cwd to the search - and the skill runs with cwd set to the project
#    being optimized, so a git.exe planted there would have won. Importing the
#    module is safe: main() sits under the __name__ guard.
import importlib.util
_uc_spec = importlib.util.spec_from_file_location(
    "autoloop_update_check", os.path.join(ROOT, "scripts", "update_check.py"))
_uc = importlib.util.module_from_spec(_uc_spec)
try:
    _uc_spec.loader.exec_module(_uc)
except Exception as e:
    _uc = None
    check("update_check.py imports as a module", False, str(e))
else:
    check("update_check.py imports as a module", True)
    check("update_check.py defines find_git()", callable(getattr(_uc, "find_git", None)))
    _uc_src = read("scripts", "update_check.py")
    check("update_check.py binds GIT_BIN via find_git()", "GIT_BIN = find_git()" in _uc_src)
    # The import line, not the call: the docstring legitimately names shutil.which
    # in prose to explain why it is avoided, and no import means no call.
    check("update_check.py no longer imports shutil",
          re.search(r"^\s*(import shutil|from shutil import)", _uc_src, re.M) is None)

if _uc is not None and callable(getattr(_uc, "find_git", None)):
    _base = tmpdir()
    _cwd_dir = os.path.join(_base, "cwd_dir")      # holds a fake git, NOT on PATH
    _path_dir = os.path.join(_base, "path_dir")    # holds a fake git, put on PATH
    _empty_dir = os.path.join(_base, "empty_dir")  # a PATH with no git at all
    for _d in (_cwd_dir, _path_dir, _empty_dir):
        os.mkdir(_d)
    for _d in (_cwd_dir, _path_dir):
        for _name in ("git", "git.exe"):
            _fake = os.path.join(_d, _name)
            with io.open(_fake, "w", encoding="utf-8") as f:
                f.write("#!/bin/sh\nexit 0\n")
            os.chmod(_fake, 0o755)  # on Windows os.access(X_OK) is true for any existing file
    _old_cwd = os.getcwd()
    _old_path = os.environ.get("PATH")
    try:
        os.chdir(_cwd_dir)
        os.environ["PATH"] = _empty_dir
        _found = _uc.find_git()
        check("find_git() ignores cwd when PATH has no git", _found is None, str(_found))
        os.environ["PATH"] = _path_dir
        _found = _uc.find_git()
        check("find_git() finds git on PATH",
              _found is not None and _found.startswith(_path_dir), str(_found))
        os.environ["PATH"] = "." + os.pathsep + _path_dir
        _found = _uc.find_git()
        check("find_git() skips a '.' PATH entry ahead of the real one",
              _found is not None and _found.startswith(_path_dir)
              and not _found.startswith(_cwd_dir), str(_found))
    finally:
        os.chdir(_old_cwd)
        if _old_path is None:
            os.environ.pop("PATH", None)
        else:
            os.environ["PATH"] = _old_path

# 14. install-hardening: SKILL.md reaches its bundled scripts through the path
#    Claude Code documents (${CLAUDE_SKILL_DIR}), pre-approves them in the
#    frontmatter, invokes python3 like README and CLAUDE.md do, and tells the
#    loop what to do when an installer copied SKILL.md without scripts/ (seen
#    in the field: no scripts/ dir means no frozen stopping rule at all).
_skill = read("SKILL.md")
_skill_lines = _skill.splitlines()
check("SKILL.md has no <skill_dir> placeholder left",
      "<skill_dir>" not in _skill)
for _name in ("update_check.py", "check_stop.py", "log_run.py"):
    check(f"SKILL.md invokes {_name} as python3 ${{CLAUDE_SKILL_DIR}}/scripts/{_name}",
          "python3 ${CLAUDE_SKILL_DIR}/scripts/" + _name in _skill)
check("SKILL.md addresses check_stop.py via ${CLAUDE_SKILL_DIR}",
      "${CLAUDE_SKILL_DIR}/scripts/check_stop.py" in _skill)

# Frontmatter = the lines between the first two "---" lines. Plain string
# handling on purpose: this file stays stdlib-only, so no yaml module.
_fm = []
_fences = 0
for _ln in _skill_lines:
    if _ln.strip() == "---":
        _fences += 1
        if _fences == 2:
            break
        continue
    if _fences == 1:
        _fm.append(_ln)
check("SKILL.md frontmatter is delimited by two --- lines", _fences == 2)
_allowed = [ln for ln in _fm if ln.startswith("allowed-tools:")]
check("SKILL.md frontmatter pre-approves the bundled scripts (allowed-tools)",
      any("scripts/" in ln for ln in _allowed), str(_allowed))
check("SKILL.md pre-approves check_stop.py and adjudicate.py by name, with the documented ' *' suffix",
      any("scripts/check_stop.py *" in ln and "scripts/adjudicate.py *" in ln for ln in _allowed), str(_allowed))
check("SKILL.md does not pre-approve run_trial.py (it executes the eval command)",
      not any("run_trial" in ln or "scripts/*" in ln for ln in _allowed), str(_allowed))
_meta_ok = False
_meta_detail = "no metadata: line in frontmatter"
for _i, _ln in enumerate(_fm):
    if _ln.rstrip() == "metadata:":
        _nxt = _fm[_i + 1] if _i + 1 < len(_fm) else ""
        _meta_ok = _nxt.startswith(" ") and _nxt.strip().startswith("version:")
        _meta_detail = f"line after metadata: is {_nxt!r}"
check("SKILL.md frontmatter carries metadata.version", _meta_ok, _meta_detail)

# The description is what the skill listing shows; the documented cap is 1536.
_desc = []
_in_desc = False
for _ln in _fm:
    if _ln.startswith("description:"):
        _in_desc = True
        _rest = _ln[len("description:"):].strip()
        if _rest and _rest not in (">", ">-", "|", "|-"):
            _desc.append(_rest)
        continue
    if _in_desc:
        if _ln.startswith(" "):
            _desc.append(_ln.strip())
        else:
            break
_desc_text = " ".join(_desc)
check("SKILL.md description stays under the 1536-char listing limit",
      0 < len(_desc_text) < 1536, f"{len(_desc_text)} chars")

check("SKILL.md tells the loop what to do when scripts/ is missing",
      "the frozen harness is missing" in _skill)
_bare = [ln for ln in _skill_lines if not ln.startswith("allowed-tools:")
         and re.search(r"(^|[\s`(])python\s+(\$|<|\S*scripts/)", ln)]
check("SKILL.md command lines use python3, never bare python",
      not _bare, str(_bare))

# 15. toy-problems: the toy projects behind TEST_PLAN.md cases 1, 2, 4 and 7
#    exist, run, and have a real plateau. A toy that the obvious fix solves
#    in one round tests nothing, so the hasty fix (sortproj/naive_impl.py)
#    must run yet fail a test, the checkproj baseline must be neither 0 nor
#    10 of 10 with a clean lint, and the example configs must be ones
#    check_stop.py accepts as-is.
TOY = os.path.join(ROOT, "tests", "toy")


def run_toy_bench(subdir, **extra_env):
    env = dict(os.environ)
    env["AUTOLOOP_TOY_N"] = "400"
    env["PYTHONDONTWRITEBYTECODE"] = "1"
    env.update(extra_env)
    return subprocess.run([PY, "bench.py"], cwd=os.path.join(TOY, subdir),
                          capture_output=True, text=True, env=env)


def metric(stdout, name):
    """The int after '^name: ' if exactly one such line exists, else None."""
    found = re.findall(r"^" + name + r": ([0-9]+)$", stdout, re.M)
    return int(found[0]) if len(found) == 1 else None


r = _r_base = run_toy_bench("sortproj")
check("sortproj bench.py exits 0", r.returncode == 0, r.stderr.strip()[-300:])
check("sortproj bench.py prints exactly one runtime_ms line",
      len(re.findall(r"^runtime_ms: [0-9]+\.[0-9]$", r.stdout, re.M)) == 1, r.stdout[:300])
_passed, _total = metric(r.stdout, "tests_passed"), metric(r.stdout, "tests_total")
check("sortproj bench.py prints tests_passed and tests_total",
      _passed is not None and _total is not None and _total > 0, r.stdout[:300])
check("sortproj baseline passes every test",
      _passed is not None and _passed == _total,
      "\n".join(ln for ln in r.stdout.splitlines() if ln.startswith("FAIL"))[:600])

r = _r_naive = run_toy_bench("sortproj", AUTOLOOP_TOY_IMPL="naive_impl")
_passed, _total = metric(r.stdout, "tests_passed"), metric(r.stdout, "tests_total")
check("sortproj plateau is real: the obvious fix breaks a test",
      r.returncode == 0 and _passed is not None and _total is not None and _passed < _total,
      f"passed={_passed} total={_total}")


def runtime_ms(stdout):
    found = re.findall(r"^runtime_ms: ([0-9.]+)$", stdout, re.M)
    return float(found[0]) if len(found) == 1 else None


check("sortproj baseline still has headroom (the hasty fix is faster than the shipped sorter)",
      runtime_ms(_r_base.stdout) is not None and runtime_ms(_r_naive.stdout) is not None
      and runtime_ms(_r_base.stdout) > runtime_ms(_r_naive.stdout),
      f"baseline={runtime_ms(_r_base.stdout)} naive={runtime_ms(_r_naive.stdout)}")

r = run_toy_bench("checkproj")
check("checkproj bench.py exits 0", r.returncode == 0, r.stderr.strip()[-300:])
_cp = metric(r.stdout, "checks_passed")
check("checkproj baseline is partly broken (0 < checks_passed < 10)",
      _cp is not None and 0 < _cp < 10, r.stdout[:300])
check("checkproj bench.py prints checks_total: 10", metric(r.stdout, "checks_total") == 10)
check("checkproj baseline is lint-clean (lint_errors: 0)",
      metric(r.stdout, "lint_errors") == 0,
      "\n".join(ln for ln in r.stdout.splitlines() if ln.startswith("LINT"))[:600])
_cp_copy = os.path.join(tmpdir(), "checkproj")
shutil.copytree(os.path.join(TOY, "checkproj"), _cp_copy, ignore=shutil.ignore_patterns("__pycache__"))
with io.open(os.path.join(_cp_copy, "app.py"), "a", encoding="utf-8") as f:
    f.write("\nprint('lint bait')   \n")
r = subprocess.run([PY, "bench.py"], cwd=_cp_copy, capture_output=True, text=True,
                   env=dict(os.environ, PYTHONDONTWRITEBYTECODE="1"))
check("checkproj lint actually bites (a planted print and trailing whitespace are counted)",
      r.returncode == 0 and (metric(r.stdout, "lint_errors") or 0) >= 1, r.stdout[:300])

_empty = os.path.join(tmpdir(), "results-empty.tsv")
with io.open(_empty, "w", encoding="utf-8", newline="\n") as f:
    f.write("round\tcandidate\tcommit\tprimary\tcounters\tstatus\tdescription\n")
for _sub in ("sortproj", "checkproj"):
    _cfg_path = os.path.join(TOY, _sub, "loop_config.example.json")
    try:
        _cfg = json.loads(read("tests", "toy", _sub, "loop_config.example.json"))
    except Exception as e:
        _cfg = None
        check(f"{_sub} loop_config.example.json parses", False, str(e))
    else:
        check(f"{_sub} loop_config.example.json parses",
              bool(_cfg.get("mutable_paths") and _cfg.get("primary") and _cfg.get("counter_metrics")))
    r = subprocess.run(
        [PY, os.path.join(ROOT, "scripts", "check_stop.py"),
         "--config", _cfg_path, "--results", _empty],
        capture_output=True, text=True)
    try:
        v = json.loads(r.stdout.strip().splitlines()[-1])
    except Exception:
        v = {}
    check(f"check_stop.py accepts {_sub} example config (empty run -> stop=false)",
          r.returncode == 0 and v.get("stop") is False, (r.stderr.strip() or str(v))[-300:])

print()
if failures:
    print(f"{len(failures)} check(s) failed: " + ", ".join(failures))
    sys.exit(1)
print("All checks passed.")
