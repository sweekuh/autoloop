#!/usr/bin/env python3
"""bench.py - frozen evaluator for the checkproj toy. Never edit it during a run.

Usage:  python3 bench.py            (from this directory)

Runs one check per function in app.py, lints app.py, and prints:

    checks_passed: <int>
    checks_total: 10
    lint_errors: <int>
    FAIL <check_name>: <reason>       one line per failing check
    LINT line <n>: <rule>             one line per lint error

Lint rules (all self-contained here, no third-party tools):
  - a line longer than 100 characters
  - trailing whitespace
  - a tab character
  - any call to print()
  - an import that is never used (found with a small ast walk)
  - a file that does not parse counts as one lint error

The exit code is always 0: the loop reads the metric lines, not the exit code.
"""
import ast
import io
import os
import sys
import traceback

HERE = os.path.dirname(os.path.abspath(__file__))
APP_PATH = os.path.join(HERE, "app.py")
MAX_LINE = 100

CHECKS = []


def check(fn):
    CHECKS.append((fn.__name__, fn))
    return fn


def eq(actual, expected, what):
    if actual != expected:
        raise AssertionError("%s: expected %s, got %s" % (what, ascii(expected), ascii(actual)))


def raises(fn, what):
    try:
        fn()
    except ValueError:
        return
    raise AssertionError("%s: expected ValueError" % what)


@check
def slugify(app):
    eq(app.slugify("  Hello, World! "), "hello-world", 'slugify("  Hello, World! ")')
    eq(app.slugify("Python 3.8"), "python-3-8", 'slugify("Python 3.8")')
    eq(app.slugify("a--b"), "a-b", 'slugify("a--b")')
    eq(app.slugify("ok"), "ok", 'slugify("ok")')


@check
def parse_duration(app):
    eq(app.parse_duration("1h"), 3600, 'parse_duration("1h")')
    eq(app.parse_duration("1h30m"), 5400, 'parse_duration("1h30m")')
    eq(app.parse_duration("45s"), 45, 'parse_duration("45s")')
    eq(app.parse_duration("2m5s"), 125, 'parse_duration("2m5s")')
    raises(lambda: app.parse_duration(""), 'parse_duration("")')
    raises(lambda: app.parse_duration("1x"), 'parse_duration("1x")')
    raises(lambda: app.parse_duration("30m1h"), 'parse_duration("30m1h")')


@check
def clamp(app):
    eq(app.clamp(5, 0, 10), 5, "clamp(5, 0, 10)")
    eq(app.clamp(-1, 0, 10), 0, "clamp(-1, 0, 10)")
    eq(app.clamp(11, 0, 10), 10, "clamp(11, 0, 10)")
    raises(lambda: app.clamp(5, 10, 0), "clamp(5, 10, 0)")


@check
def chunk(app):
    eq(app.chunk([1, 2, 3, 4, 5], 2), [[1, 2], [3, 4], [5]], "chunk([1..5], 2)")
    eq(app.chunk([], 3), [], "chunk([], 3)")
    eq(app.chunk("abcd", 2), [["a", "b"], ["c", "d"]], 'chunk("abcd", 2)')
    raises(lambda: app.chunk([1], 0), "chunk([1], 0)")


@check
def merge_intervals(app):
    eq(app.merge_intervals([(1, 3), (2, 4), (6, 8)]), [(1, 4), (6, 8)], "overlapping, sorted input")
    eq(app.merge_intervals([(5, 6), (1, 3), (2, 4)]), [(1, 4), (5, 6)], "unsorted input")
    eq(app.merge_intervals([(1, 2), (2, 3)]), [(1, 3)], "touching intervals")
    eq(app.merge_intervals([(1, 10), (2, 3)]), [(1, 10)], "contained interval")
    eq(app.merge_intervals([]), [], "empty input")


@check
def median(app):
    eq(app.median([3, 1, 2]), 2, "median([3, 1, 2])")
    eq(app.median([1, 2, 3, 4]), 2.5, "median([1, 2, 3, 4])")
    eq(app.median([7]), 7, "median([7])")
    raises(lambda: app.median([]), "median([])")


@check
def word_freq(app):
    eq(app.word_freq("The cat. the CAT!"), {"the": 2, "cat": 2}, 'word_freq("The cat. the CAT!")')
    eq(app.word_freq(""), {}, 'word_freq("")')
    eq(app.word_freq("(a) a, 'a'"), {"a": 3}, "word_freq with punctuation")


@check
def is_palindrome(app):
    eq(app.is_palindrome("A man, a plan, a canal: Panama"), True, "panama")
    eq(app.is_palindrome("hello"), False, "hello")
    eq(app.is_palindrome(""), True, "empty string")


@check
def is_leap_year(app):
    eq(app.is_leap_year(2000), True, "2000")
    eq(app.is_leap_year(1900), False, "1900")
    eq(app.is_leap_year(2024), True, "2024")
    eq(app.is_leap_year(2023), False, "2023")


@check
def round_half_up(app):
    eq(app.round_half_up(2.5), 3.0, "round_half_up(2.5)")
    eq(app.round_half_up(-2.5), -3.0, "round_half_up(-2.5)")
    eq(app.round_half_up(1.25, 1), 1.3, "round_half_up(1.25, 1)")
    eq(app.round_half_up(2.4), 2.0, "round_half_up(2.4)")
    eq(app.round_half_up(0.5), 1.0, "round_half_up(0.5)")


def lint(path):
    """Return a sorted list of (line_number, message) for every lint error in `path`."""
    errors = []
    with io.open(path, encoding="utf-8", newline="") as f:
        source = f.read()
    for num, line in enumerate(source.split("\n"), 1):
        body = line.rstrip("\r")
        if len(body) > MAX_LINE:
            errors.append((num, "line too long (%d > %d)" % (len(body), MAX_LINE)))
        if body != body.rstrip():
            errors.append((num, "trailing whitespace"))
        if "\t" in body:
            errors.append((num, "tab character"))
    try:
        tree = ast.parse(source, filename=path)
    except SyntaxError as e:
        errors.append((e.lineno or 0, "syntax error: %s" % e.msg))
        return sorted(errors)
    imported = {}
    used = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                imported[alias.asname or alias.name.split(".")[0]] = node.lineno
        elif isinstance(node, ast.ImportFrom):
            if node.module != "__future__":
                for alias in node.names:
                    imported[alias.asname or alias.name] = node.lineno
        elif isinstance(node, ast.Name):
            used.add(node.id)
        elif isinstance(node, ast.Call):
            if isinstance(node.func, ast.Name) and node.func.id == "print":
                errors.append((node.lineno, "call to print()"))
    for name, lineno in imported.items():
        if name not in used:
            errors.append((lineno, "unused import %s" % name))
    return sorted(errors)


def _ascii(s):
    return s.encode("ascii", "backslashreplace").decode("ascii")


def main():
    app = None
    try:
        import app as app_module
        app = app_module
    except Exception as e:
        traceback.print_exc()
        import_error = "%s: %s" % (type(e).__name__, _ascii(str(e)))

    results = []
    for name, fn in CHECKS:
        if app is None:
            results.append((name, False, "app.py did not import: " + import_error))
            continue
        try:
            fn(app)
        except Exception as e:
            results.append((name, False, "%s: %s" % (type(e).__name__, _ascii(str(e)))))
        else:
            results.append((name, True, ""))

    lint_errors = lint(APP_PATH)
    passed = sum(1 for _, ok, _ in results if ok)
    print("checks_passed: %d" % passed)
    print("checks_total: %d" % len(results))
    print("lint_errors: %d" % len(lint_errors))
    for name, ok, reason in results:
        if not ok:
            print("FAIL %s: %s" % (name, reason))
    for lineno, message in lint_errors:
        print("LINT line %d: %s" % (lineno, message))
    sys.stdout.flush()
    return 0


if __name__ == "__main__":
    sys.exit(main())
