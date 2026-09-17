"""Tiny eval for exercising run_trial.py and adjudicate.py in tests/check.py.

AUTOLOOP_FIXTURE_MODE selects the scenario:
  ok     (default) prints a clean primary and counter
  sleep  hangs long enough to trip a short trial_timeout_seconds
  crash  exits before printing the primary (with a non-ASCII byte in the message)
  gate   prints a counter value that violates the fixture's gate
  fail   prints the metrics, then exits 3
"""
import os
import sys
import time

mode = os.environ.get("AUTOLOOP_FIXTURE_MODE", "ok")
if mode == "sleep":
    time.sleep(30)
if mode == "crash":
    sys.exit("boom: harness broke before printing caf\u00e9")
print("some noise line")
print("runtime_ms: 123.4")
print("tests_passed: 42")
if mode == "gate":
    print("tests_passed: 10")
if mode == "fail":
    sys.exit(3)
