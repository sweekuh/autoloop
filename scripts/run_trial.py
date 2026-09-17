#!/usr/bin/env python3
"""Run one trial of the frozen eval and extract its metrics.

Part of the frozen harness (see SKILL.md Phase 3). The looping agent must not
edit it. It exists so that the numbers a round is judged on come out of a
script that reads loop_config.json, not out of the agent's reading of a log:

  python3 ${CLAUDE_SKILL_DIR}/scripts/run_trial.py --config loop_config.json [--cwd DIR]

It runs `eval_command` in --cwd (default: the current directory, which for a
parallel candidate is its worktree) under `trial_timeout_seconds`, then runs
the primary's and every counter-metric's `extract` command and takes the last
number on the last non-empty line each prints. It writes one JSON line:

  {"ok": bool, "primary": float|null, "counters": {name: float|null},
   "timed_out": bool, "exit_code": int|null, "elapsed_s": float, "tail": [...]}

`ok` is true only when the primary extracted. `tail` is the last lines of the
eval's own output (or of run.log when the command redirected there), enough
to diagnose a crash without pasting the whole log into context. Exit code is
0 whatever happened: a crashed trial is data, not a script failure, and the
adjudicator turns it into a `crash` row.

Output is pure ASCII so it cannot fail on a non-UTF-8 console.
"""
import argparse
import json
import os
import re
import signal
import subprocess
import sys
import time

TAIL_LINES = 20
NUMBER = re.compile(r"[-+]?(?:\d+\.\d*|\.\d+|\d+)(?:[eE][-+]?\d+)?")


def ascii_only(s):
    return str(s).encode("ascii", "backslashreplace").decode("ascii")


def last_number(text):
    """The last number on the last non-empty line of `text`, or None."""
    lines = [ln for ln in str(text).splitlines() if ln.strip()]
    if not lines:
        return None
    found = NUMBER.findall(lines[-1])
    if not found:
        return None
    try:
        return float(found[-1])
    except ValueError:
        return None


def kill_tree(proc):
    """Kill the shell and everything it started.

    shell=True means `proc` is the shell; the eval is its child. On POSIX the
    child sits in the session started below, so killing the process group gets
    it. On Windows proc.kill() would stop only cmd.exe: the eval keeps running,
    keeps run.log open, and every later trial fails with "file in use", which
    is exactly what happened in CI. taskkill /T walks the tree.
    """
    if os.name == "nt":
        try:
            subprocess.run(["taskkill", "/F", "/T", "/PID", str(proc.pid)],
                           stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, timeout=15)
            return
        except (OSError, subprocess.SubprocessError):
            pass
        proc.kill()
        return
    try:
        os.killpg(proc.pid, signal.SIGKILL)
    except OSError:
        proc.kill()


def run_shell(cmd, cwd, timeout):
    """Run `cmd` through the shell; kill the whole process tree on timeout.

    Returns (exit_code|None, combined_output, timed_out).
    """
    kwargs = {}
    if os.name != "nt":
        kwargs["start_new_session"] = True
    proc = subprocess.Popen(
        cmd, shell=True, cwd=cwd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
        stdin=subprocess.DEVNULL, **kwargs)
    try:
        out, _ = proc.communicate(timeout=timeout)
        return proc.returncode, out.decode("utf-8", "replace"), False
    except subprocess.TimeoutExpired:
        kill_tree(proc)
        try:
            out, _ = proc.communicate(timeout=5)
        except subprocess.TimeoutExpired:
            out = b""
        return None, out.decode("utf-8", "replace"), True


def extract(cmd, cwd):
    """Run an extract command and parse its output; None when nothing parses."""
    if not cmd:
        return None
    try:
        code, out, timed_out = run_shell(cmd, cwd, 60)
    except OSError:
        return None
    if timed_out:
        return None
    return last_number(out)


def tail_of(output, cwd):
    lines = [ln.rstrip() for ln in output.splitlines() if ln.strip()]
    if not lines:
        log_path = os.path.join(cwd, "run.log")
        if os.path.isfile(log_path):
            try:
                with open(log_path, encoding="utf-8", errors="replace") as f:
                    lines = [ln.rstrip() for ln in f.read().splitlines() if ln.strip()]
            except OSError:
                lines = []
    return [ascii_only(ln)[:200] for ln in lines[-TAIL_LINES:]]


def main():
    p = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    p.add_argument("--config", required=True, help="the run's loop_config.json")
    p.add_argument("--cwd", default=".", help="directory to run in (a worktree for a parallel candidate)")
    p.add_argument("--timeout", type=float, default=None,
                   help="override trial_timeout_seconds from the config")
    args = p.parse_args()

    with open(args.config, encoding="utf-8") as f:
        cfg = json.load(f)
    cwd = os.path.abspath(args.cwd)
    eval_cmd = cfg.get("eval_command")
    timeout = args.timeout
    if timeout is None:
        try:
            timeout = float(cfg.get("trial_timeout_seconds") or 0) or None
        except (TypeError, ValueError):
            timeout = None

    result = {"ok": False, "primary": None, "counters": {}, "timed_out": False,
              "exit_code": None, "elapsed_s": 0.0, "tail": []}
    if not eval_cmd:
        result["tail"] = ["run_trial: eval_command missing from config"]
        print(json.dumps(result))
        return 0

    started = time.perf_counter()
    try:
        code, out, timed_out = run_shell(eval_cmd, cwd, timeout)
    except OSError as e:
        result["tail"] = [ascii_only(f"run_trial: could not start eval_command: {e}")]
        print(json.dumps(result))
        return 0
    result["elapsed_s"] = round(time.perf_counter() - started, 3)
    result["exit_code"] = code
    result["timed_out"] = timed_out
    result["tail"] = tail_of(out, cwd)
    if timed_out:
        result["tail"].append(f"run_trial: eval_command exceeded trial_timeout_seconds ({timeout:g}s) and was killed")
        print(json.dumps(result))
        return 0

    primary_cfg = cfg.get("primary") or {}
    result["primary"] = extract(primary_cfg.get("extract"), cwd)
    for cm in cfg.get("counter_metrics") or []:
        name = cm.get("name")
        if not name:
            continue
        result["counters"][name] = extract(cm.get("extract"), cwd)
    result["ok"] = result["primary"] is not None
    if not result["ok"]:
        result["tail"].append("run_trial: primary metric did not extract")
    print(json.dumps(result))
    return 0


if __name__ == "__main__":
    sys.exit(main())
