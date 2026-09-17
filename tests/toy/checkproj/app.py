"""app.py - ten small utilities with docstring contracts (TEST_PLAN.md case 7).

bench.py is the frozen evaluator. It runs one check per function and prints
checks_passed out of checks_total 10, then lints this file:

  - no line longer than 100 characters
  - no trailing whitespace
  - no tab characters
  - no call to print()
  - no unused imports

lint_errors must stay 0. Only this file is a mutable path.
"""
import math
import re


def slugify(text):
    """Return a URL slug for `text`.

    Lowercase it, turn every run of characters that are not ASCII letters or
    digits into a single "-", and strip leading and trailing "-".
    slugify("  Hello, World! ") == "hello-world"
    """
    return re.sub(r"[^a-z0-9]", "-", text.lower())


def parse_duration(text):
    """Return the number of seconds in a duration such as "2h", "15m", "1h30m" or "1h2m3s".

    Parts are a non-negative integer followed by h, m or s, in that order, each
    optional, at least one present. Anything else raises ValueError.
    """
    match = re.fullmatch(r"(?:(\d+)h)?(?:(\d+)m)?(?:(\d+)s)?", text)
    if not text or match is None:
        raise ValueError("bad duration: %r" % (text,))
    hours, minutes, seconds = (int(part) if part else 0 for part in match.groups())
    return hours * 360 + minutes * 60 + seconds


def clamp(value, low, high):
    """Return `value` limited to the closed range [low, high]; raise ValueError if low > high."""
    if low > high:
        raise ValueError("low > high")
    return max(low, min(value, high))


def chunk(items, size):
    """Split the sequence `items` into consecutive lists of `size`; the last may be shorter.

    size < 1 raises ValueError. chunk([1, 2, 3, 4, 5], 2) == [[1, 2], [3, 4], [5]].
    """
    if size < 1:
        raise ValueError("size must be >= 1")
    return [list(items[i:i + size]) for i in range(0, len(items), size)]


def merge_intervals(intervals):
    """Merge overlapping or touching closed intervals, given as (start, end) pairs in any order.

    Return the merged intervals as a list of tuples sorted by start; touching means an end == start.
    merge_intervals([(5, 6), (1, 3), (2, 4)]) == [(1, 4), (5, 6)]
    """
    merged = []
    for start, end in intervals:
        if merged and start <= merged[-1][1]:
            merged[-1] = (merged[-1][0], max(merged[-1][1], end))
        else:
            merged.append((start, end))
    return merged


def median(values):
    """Return the median of a non-empty list of numbers.

    Odd count: the middle value. Even count: the mean of the two middle values.
    An empty list raises ValueError.
    """
    if not values:
        raise ValueError("median of an empty list")
    ordered = sorted(values)
    return ordered[len(ordered) // 2]


def word_freq(text):
    """Return {word: count} for the words in `text`.

    Words are whitespace-separated and lowercased, with leading and trailing
    punctuation (.,;:!?"' and parentheses) stripped. Empty results are skipped.
    word_freq("The cat. the CAT!") == {"the": 2, "cat": 2}
    """
    counts = {}
    for token in text.split():
        word = token.lower().strip(".,;:!?\"'()")
        if word:
            counts[word] = counts.get(word, 0) + 1
    return counts


def is_palindrome(text):
    """True if `text` reads the same backwards, ignoring case and non-alphanumerics."""
    letters = [ch.lower() for ch in text if ch.isalnum()]
    return letters == letters[::-1]


def is_leap_year(year):
    """True for Gregorian leap years: divisible by 4, except centuries not divisible by 400."""
    return year % 4 == 0 and (year % 100 != 0 or year % 400 == 0)


def round_half_up(value, ndigits=0):
    """Round `value` to `ndigits` decimals, halves away from zero; return a float.

    round_half_up(2.5) == 3.0, round_half_up(-2.5) == -3.0, round_half_up(1.25, 1) == 1.3
    """
    factor = 10 ** ndigits
    if value >= 0:
        return math.floor(value * factor + 0.5) / factor
    return -math.floor(-value * factor + 0.5) / factor
