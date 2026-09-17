"""naive_impl.py - the obvious fix, done hastily. NOT a mutable path.

Never list this file in a loop_config.json. It exists so the repo's own
checks (tests/check.py) can prove the sortproj plateau is real: bench.py
runs it via AUTOLOOP_TOY_IMPL=naive_impl and asserts that some tests fail.

It is sorter.py with the hand-rolled insertion sort swapped for sorted()
over plain (key, ident, payload) tuples - the first thing a hurried rewrite
reaches for. Runtime drops, but tuples with equal keys fall through to
comparing idents, so records that share a key come out in ident order
instead of input order and the stability tests fail. Sorting the Record
objects directly instead is worse: Record has no ordering, so that raises.
The only sort that passes is one keyed on the key alone.

Imports the untouched stages from sorter.py, so it is meaningful only
against the shipped sorter.py, not a mutated one.
"""
from sorter import Payload, Record, dedupe, format_records, parse_lines


def process(records):
    parsed = parse_lines(records)
    unique = dedupe(parsed)
    rows = sorted((r.key, r.ident, r.payload.text) for r in unique)
    ordered = [Record(key, ident, Payload(text)) for key, ident, text in rows]
    return format_records(ordered)
