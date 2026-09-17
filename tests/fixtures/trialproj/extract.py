"""Portable stand-in for `grep '^name:' run.log | tail -1` (Windows has no grep)."""
import sys

name = sys.argv[1]
last = None
with open("run.log", encoding="utf-8", errors="replace") as f:
    for line in f:
        if line.startswith(name + ":"):
            last = line.rstrip("\n")
if last is not None:
    print(last)
