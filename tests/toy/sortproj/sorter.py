"""sorter.py - the artifact an autoloop run mutates (TEST_PLAN.md cases 1, 2, 4).

bench.py is the frozen evaluator: it times process() and runs the tests
below. Only this file is a mutable path. bench.py never changes.

Public contract (bench.py pins every clause of it):

    process(records) -> list of str

`records` is a list of raw input lines. Each line has the form

    <key> <ident> <payload>

with exactly one ASCII space between fields, where

  key      a decimal literal: an optional "-", one or more ASCII digits,
           optionally "." followed by one or more ASCII digits. Nothing
           else: no "+", no exponent, no "nan" or "inf", no "_", no leading
           or trailing ".", no non-ASCII digits. Converted with float().
  ident    one or more of [A-Za-z0-9_]. The record's identity. Case matters.
  payload  one or more characters other than tab, CR and LF, kept verbatim
           (leading, trailing and repeated spaces included).

One trailing "\\n" or "\\r\\n" is removed from a line before matching. A
line that does not match raises ValueError - never skipped silently. A key
that would be NaN raises ValueError.

Pipeline: parse -> dedupe -> sort -> format.

  dedupe   keeps the FIRST occurrence (in input order) of each ident and
           drops every later one, whatever its key. Survivors keep their
           relative order.
  sort     ascending by key only. Records with equal keys keep their input
           order (stable). Payloads are never compared, and comparing one
           raises TypeError.
  format   one output line per surviving record:

               "<rank>\\t<key>\\t<ident>\\t<payload>"

           rank is the 1-based position in the output, key is
           format(key, ".2f").

process() never mutates `records` and keeps no state between calls.
"""
import re

_KEY_RE = r"-?[0-9]+(?:\.[0-9]+)?"
_IDENT_RE = r"[A-Za-z0-9_]+"
_PAYLOAD_RE = r"[^\t\r\n]+"
_IDENT_CHARS = "ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789_"


class Payload(object):
    """Opaque record body. Ordering one is always a bug, so it raises."""

    __slots__ = ("text",)

    def __init__(self, text):
        self.text = text

    def _unorderable(self, other):
        raise TypeError("payloads cannot be ordered")

    __lt__ = _unorderable
    __le__ = _unorderable
    __gt__ = _unorderable
    __ge__ = _unorderable


class Record(object):
    """A parsed line. Deliberately has no ordering of its own."""

    __slots__ = ("key", "ident", "payload")

    def __init__(self, key, ident, payload):
        self.key = key
        self.ident = ident
        self.payload = payload


def _strip_newline(line):
    if line.endswith("\r\n"):
        return line[:-2]
    if line.endswith("\n"):
        return line[:-1]
    return line


def parse_line(line):
    """Parse one raw line into a Record, or raise ValueError."""
    if not isinstance(line, str):
        raise ValueError("record is not a string: " + ascii(line))
    text = _strip_newline(line)
    pattern = re.compile("(" + _KEY_RE + ") (" + _IDENT_RE + ") (" + _PAYLOAD_RE + ")")
    match = pattern.fullmatch(text)
    if match is None:
        raise ValueError("malformed line: " + ascii(line))
    key_text = match.group(1)
    ident_text = match.group(2)
    payload_text = match.group(3)
    # Re-validate every field on its own before trusting it.
    if re.compile(_KEY_RE).fullmatch(key_text) is None:
        raise ValueError("bad key: " + ascii(key_text))
    if re.compile(_IDENT_RE).fullmatch(ident_text) is None:
        raise ValueError("bad ident: " + ascii(ident_text))
    if re.compile(_PAYLOAD_RE).fullmatch(payload_text) is None:
        raise ValueError("bad payload: " + ascii(payload_text))
    allowed = list(_IDENT_CHARS)
    ident = ""
    for ch in ident_text:
        if ch not in allowed:
            raise ValueError("bad ident: " + ascii(ident_text))
        ident = ident + ch
    key = float(key_text)
    if key != key:
        raise ValueError("NaN key: " + ascii(key_text))
    return Record(key, ident, Payload(payload_text))


def parse_lines(records):
    parsed = []
    for line in records:
        parsed.append(parse_line(line))
    return parsed


def dedupe(records):
    """Keep the first record for each ident, in input order."""
    seen = []
    out = []
    for record in records:
        if record.ident not in seen:
            seen.append(record.ident)
            out.append(record)
    return out


def insertion_sort(records):
    """Stable ascending sort by key."""
    out = list(records)
    for i in range(1, len(out)):
        current = out[i]
        j = i - 1
        while j >= 0 and out[j].key > current.key:
            out[j + 1] = out[j]
            j -= 1
        out[j + 1] = current
    return out


def format_records(records):
    out = []
    rank = 0
    for record in records:
        rank += 1
        line = ""
        line = line + str(rank)
        line = line + "\t" + format(record.key, ".2f")
        line = line + "\t" + record.ident
        line = line + "\t" + record.payload.text
        out = out + [line]
    return out


def process(records):
    parsed = parse_lines(records)
    unique = dedupe(parsed)
    ordered = insertion_sort(unique)
    return format_records(ordered)
