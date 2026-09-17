#!/usr/bin/env python3
"""bench.py - frozen evaluator for the sortproj toy. Never edit it during a run.

Usage:  python3 bench.py            (from this directory)

Builds a fixed, seeded input, times sorter.process() on it, then runs the
test suite against the same implementation. Prints, in this order:

    runtime_ms: <float, 1 decimal>    best of 3 timed calls after one warmup
    tests_passed: <int>
    tests_total: <int>
    FAIL <test_name>: <reason>        one line per failing test

If process() raises during the timed run there is no runtime_ms line; a
"crash: ..." line is printed instead and the traceback goes to stderr. The
exit code is always 0: the loop reads the metric lines, not the exit code.

Environment:
    AUTOLOOP_TOY_N     input size (default 10000). CI runs it small.
    AUTOLOOP_TOY_IMPL  module to import instead of `sorter` (used by the
                       repo's own checks to prove the plateau is real).
"""
import importlib
import os
import random
import sys
import time
import traceback

DEFAULT_N = 10000
SEED = 12345

_WORDS = ("alpha", "bravo", "charlie", "delta", "echo", "foxtrot", "golf",
          "hotel", "india", "juliet", "kilo", "lima", "mike", "november",
          "oscar", "papa", "quebec", "romeo", "sierra", "tango", "uniform")


def build_input(n, seed=SEED):
    """n raw lines: keys with many ties, ~9% duplicate idents, mixed spellings."""
    rng = random.Random(seed)
    span = max(n // 5, 4)
    lines = []
    for _ in range(n):
        base = rng.randint(-span, span)
        roll = rng.random()
        if roll < 0.7:
            key = str(base)
        elif roll < 0.85:
            key = "%d.%d" % (base, rng.randint(0, 9))
        else:
            key = "%d.%02d" % (base, rng.randint(0, 99))
        ident = "r%d" % rng.randrange(5 * n)
        words = [rng.choice(_WORDS) for _ in range(rng.randint(2, 5))]
        lines.append(key + " " + ident + " " + " ".join(words))
    return lines


def time_process(process, lines):
    """One warmup call (its output is kept for the tests), then best of 3."""
    warm = process(lines)
    best = None
    for _ in range(3):
        t0 = time.perf_counter()
        process(lines)
        dt = time.perf_counter() - t0
        if best is None or dt < best:
            best = dt
    return best * 1000.0, warm


# --------------------------------------------------------------------------
# Test suite. Each test is a function taking (process, ctx) and raising on
# failure. ctx carries the benchmark input and the warmup output so the big
# run is checked too, without paying for another call.
# --------------------------------------------------------------------------

TESTS = []


def test(fn):
    TESTS.append((fn.__name__, fn))
    return fn


def eq(actual, expected, what="result"):
    if actual != expected:
        raise AssertionError("%s: expected %s, got %s" % (what, ascii(expected), ascii(actual)))


def idents_of(out):
    return [line.split("\t")[2] for line in out]


FIXED_IN = [
    "2 a1 alpha",
    "1.5 b2 bravo",
    "2 c3 charlie",
    "-1 d4 delta",
    "1.5 a1 dup of a1",
    "10 e5 echo",
]
FIXED_OUT = [
    "1\t-1.00\td4\tdelta",
    "2\t1.50\tb2\tbravo",
    "3\t2.00\ta1\talpha",
    "4\t2.00\tc3\tcharlie",
    "5\t10.00\te5\techo",
]


@test
def empty_input_gives_empty_list(process, ctx):
    out = process([])
    if not isinstance(out, list):
        raise AssertionError("expected a list, got " + type(out).__name__)
    eq(out, [])


@test
def single_record_exact(process, ctx):
    out = process(["7 z9 just one"])
    eq(out, ["1\t7.00\tz9\tjust one"])
    if not all(isinstance(s, str) for s in out):
        raise AssertionError("output items are not str")


@test
def fixed_case_exact(process, ctx):
    eq(process(list(FIXED_IN)), FIXED_OUT)


@test
def numeric_order_not_lexicographic(process, ctx):
    out = process(["10 a x", "9 b x", "1000000000000 c x", "999999999999 d x", "100 e x"])
    eq(idents_of(out), ["b", "a", "e", "d", "c"])


@test
def negative_keys_sort_first(process, ctx):
    out = process(["0 a x", "-3.5 b x", "2 c x", "-10 d x", "-0.25 e x"])
    eq(idents_of(out), ["d", "b", "e", "a", "c"])


@test
def float_and_int_keys_interleave(process, ctx):
    out = process(["3 a x", "2.5 b x", "2 c x", "2.75 d x", "3.0001 e x"])
    eq(idents_of(out), ["c", "b", "d", "a", "e"])


@test
def equal_keys_keep_input_order(process, ctx):
    out = process(["5 zeta x", "5 alpha x", "5 mid x", "4 beta x", "5 aaa x"])
    eq(idents_of(out), ["beta", "zeta", "alpha", "mid", "aaa"])


@test
def equal_keys_keep_input_order_stress(process, ctx):
    lines = []
    expected = {0: [], 1: [], 2: []}
    for i in range(150):
        key = (i * 7) % 3
        ident = "id%03d" % (149 - i)
        lines.append("%d %s p%d" % (key, ident, i))
        expected[key].append(ident)
    out = process(lines)
    eq(idents_of(out), expected[0] + expected[1] + expected[2])


@test
def int_and_float_spellings_tie(process, ctx):
    out = process(["3.0 a x", "3 b x", "3.00 c x", "0 d x", "-0 e x", "0.0 f x"])
    eq(idents_of(out), ["d", "e", "f", "a", "b", "c"])


@test
def dedupe_keeps_first_occurrence_same_key(process, ctx):
    out = process(["1 a first", "1 a second", "1 a third"])
    eq(out, ["1\t1.00\ta\tfirst"])


@test
def dedupe_keeps_first_even_if_later_key_smaller(process, ctx):
    out = process(["9 a late big", "1 b x", "0 a early small"])
    eq(out, ["1\t1.00\tb\tx", "2\t9.00\ta\tlate big"])


@test
def dedupe_preserves_survivor_order(process, ctx):
    out = process(["1 c x", "1 a x", "1 c y", "1 b x", "1 a y", "1 d x"])
    eq(idents_of(out), ["c", "a", "b", "d"])


@test
def dedupe_by_ident_only(process, ctx):
    out = process(["1 a same", "1 b same", "1 c same"])
    eq(len(out), 3, "line count")


@test
def dedupe_ident_case_sensitive(process, ctx):
    out = process(["1 A1 x", "1 a1 y", "1 A1 z"])
    eq(out, ["1\t1.00\tA1\tx", "2\t1.00\ta1\ty"])


@test
def ranks_consecutive_after_dedupe(process, ctx):
    lines = ["%d k%d p" % (i % 4, i % 6) for i in range(30)]
    out = process(lines)
    eq([line.split("\t")[0] for line in out], [str(i) for i in range(1, 7)])


@test
def many_exact_duplicates_collapse(process, ctx):
    out = process(["2 dup payload"] * 100 + ["1 other p"])
    eq(out, ["1\t1.00\tother\tp", "2\t2.00\tdup\tpayload"])


@test
def input_not_mutated(process, ctx):
    lines = ["3 c x", "1 a x", "2 b x", "1 a y"]
    snapshot = list(lines)
    process(lines)
    eq(lines, snapshot, "input list after process()")


@test
def trailing_lf_stripped(process, ctx):
    out = process(["2 b two\n", "1 a one\n"])
    eq(out, ["1\t1.00\ta\tone", "2\t2.00\tb\ttwo"])


@test
def trailing_crlf_stripped(process, ctx):
    out = process(["1 a one\r\n"])
    eq(out, ["1\t1.00\ta\tone"])


@test
def payload_verbatim_spaces(process, ctx):
    out = process(["1 a  lead", "2 b trail ", "3 c in  side   here", "4 d  x \n"])
    eq(out, ["1\t1.00\ta\t lead", "2\t2.00\tb\ttrail ",
             "3\t3.00\tc\tin  side   here", "4\t4.00\td\t x "])


@test
def key_formatting_two_decimals(process, ctx):
    out = process(["007 a x", "3.14159 b x", "2 c x", "-0.5 d x", "-0 e x", "2.5 f x"])
    eq([line.split("\t")[1] for line in out],
       ["-0.50", "-0.00", "2.00", "2.50", "3.14", "7.00"])


@test
def malformed_line_raises_even_when_late(process, ctx):
    try:
        process(["1 a x", "2 b y", "3 c z", "oops", "4 d w"])
    except ValueError:
        return
    raise AssertionError("no ValueError for a malformed 4th line")


@test
def repeat_calls_are_independent(process, ctx):
    first = process(["1 a x", "2 b y"])
    second = process(["1 a x", "2 b y"])
    eq(second, first, "second call")
    third = process(["5 b z", "4 a w"])
    eq(third, ["1\t4.00\ta\tw", "2\t5.00\tb\tz"], "call with previously seen idents")


@test
def large_input_invariants(process, ctx):
    lines = ctx["lines"]
    out = ctx["warm_output"]
    if out is None:
        raise AssertionError("no output from the timed run")
    first = {}
    order = []
    for pos, raw in enumerate(lines):
        key_text, ident, payload = raw.split(" ", 2)
        if ident not in first:
            first[ident] = (pos, float(key_text), payload)
            order.append(ident)
    eq(len(out), len(order), "output line count")
    prev_key = None
    prev_pos = None
    seen = set()
    for i, line in enumerate(out):
        fields = line.split("\t")
        eq(len(fields), 4, "field count on line %d" % (i + 1))
        rank, key_text, ident, payload = fields
        eq(rank, str(i + 1), "rank on line %d" % (i + 1))
        if ident in seen:
            raise AssertionError("ident %s appears twice" % ident)
        seen.add(ident)
        if ident not in first:
            raise AssertionError("unknown ident %s" % ident)
        pos, key, expected_payload = first[ident]
        eq(key_text, format(key, ".2f"), "key text for %s" % ident)
        eq(payload, expected_payload, "payload for %s" % ident)
        if prev_key is not None:
            if key < prev_key:
                raise AssertionError("keys not ascending at line %d" % (i + 1))
            if key == prev_key and pos < prev_pos:
                raise AssertionError("equal keys out of input order at line %d" % (i + 1))
        prev_key, prev_pos = key, pos


MALFORMED = [
    ("rejects_empty_line", ""),
    ("rejects_missing_payload", "1 a1"),
    ("rejects_empty_payload_after_space", "1 a1 "),
    ("rejects_double_space_separator", "1  a1 x"),
    ("rejects_leading_space", " 1 a1 x"),
    ("rejects_nan_key", "nan a1 x"),
    ("rejects_inf_key", "inf a1 x"),
    ("rejects_exponent_key", "1e3 a1 x"),
    ("rejects_plus_sign_key", "+1 a1 x"),
    ("rejects_trailing_dot_key", "1. a1 x"),
    ("rejects_leading_dot_key", ".5 a1 x"),
    ("rejects_underscore_key", "1_000 a1 x"),
    ("rejects_unicode_digit_key", "\u0661 a1 x"),
    ("rejects_hyphen_in_ident", "1 a-1 x"),
    ("rejects_non_ascii_ident", "1 \u00e9 x"),
    ("rejects_tab_separator", "1\ta1 x"),
    ("rejects_tab_in_payload", "1 a1 x\ty"),
    ("rejects_double_trailing_newline", "1 a1 x\n\n"),
]


def _make_malformed_test(bad_line):
    def fn(process, ctx):
        try:
            out = process(["0 ok fine", bad_line])
        except ValueError:
            return
        except Exception as e:
            raise AssertionError("%s raised %s, not ValueError"
                                 % (ascii(bad_line), type(e).__name__))
        raise AssertionError("%s was accepted: %s" % (ascii(bad_line), ascii(out)))
    return fn


for _name, _line in MALFORMED:
    TESTS.append((_name, _make_malformed_test(_line)))


def run_tests(process, ctx):
    results = []
    for name, fn in TESTS:
        if process is None:
            results.append((name, False, "implementation did not import"))
            continue
        try:
            fn(process, ctx)
        except Exception as e:
            results.append((name, False, "%s: %s" % (type(e).__name__, _ascii(str(e)))))
        else:
            results.append((name, True, ""))
    return results


def _ascii(s):
    return s.encode("ascii", "backslashreplace").decode("ascii")


def main():
    impl_name = os.environ.get("AUTOLOOP_TOY_IMPL", "sorter")
    process = None
    try:
        process = importlib.import_module(impl_name).process
    except Exception as e:
        traceback.print_exc()
        print("crash: import of %s failed: %s: %s" % (impl_name, type(e).__name__, _ascii(str(e))))

    n = int(os.environ.get("AUTOLOOP_TOY_N", DEFAULT_N))
    lines = build_input(n)
    ctx = {"lines": lines, "warm_output": None}
    if process is not None:
        try:
            ms, warm = time_process(process, lines)
        except Exception as e:
            traceback.print_exc()
            print("crash: %s: %s" % (type(e).__name__, _ascii(str(e))))
        else:
            ctx["warm_output"] = warm
            print("runtime_ms: %.1f" % ms)

    results = run_tests(process, ctx)
    passed = sum(1 for _, ok, _ in results if ok)
    print("tests_passed: %d" % passed)
    print("tests_total: %d" % len(results))
    for name, ok, reason in results:
        if not ok:
            print("FAIL %s: %s" % (name, reason))
    sys.stdout.flush()
    return 0


if __name__ == "__main__":
    sys.exit(main())
