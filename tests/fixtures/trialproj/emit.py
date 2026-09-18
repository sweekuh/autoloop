"""Portable stand-in for an extract command in tests/check.py.

Nested quoting differs between /bin/sh and cmd.exe, so the checks point the
extract command at this file instead of at `python -c "..."`.
  emit.py two-lines | two-numbers | stderr-fail | stderr-ok | inf | ok
"""
import sys

mode = sys.argv[1] if len(sys.argv) > 1 else "ok"
if mode == "two-lines":
    print("runtime_ms: 1")
    print("runtime_ms: 2")
elif mode == "two-numbers":
    print("runtime_ms: 842.3 0.1")
elif mode == "stderr-fail":
    sys.stderr.write("error 42\n")
    sys.exit(1)
elif mode == "stderr-ok":
    sys.stderr.write("stderr noise 999\n")
    print("runtime_ms: 3.5")
elif mode == "inf":
    print("runtime_ms: 1e999")
else:
    print("runtime_ms: 7.5")
