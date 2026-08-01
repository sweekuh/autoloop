---
name: Bug report
about: Something in the loop behaved wrong — a bad keep/discard, a broken stop, a crash
title: "[bug] "
labels: bug
---

**What happened**
<!-- One or two sentences. What did the loop do that it shouldn't have? -->

**What you expected instead**


**`loop_config.json`**
<!-- Paste the config for the run. Redact anything sensitive. -->
```json

```

**Relevant `results-<run_tag>.tsv` rows**
<!-- The last few rows around the problem (header + the rows that matter). -->
```
round	candidate	commit	primary	counters	status	description

```

**Stop verdict (if the stopping rule is involved)**
<!-- Output of: python scripts/check_stop.py --config loop_config.json --results results-<run_tag>.tsv -->
```json

```

**Environment**
- OS:
- Claude Code version:
- python3 version:
- autoloop commit (`git -C ~/.claude/skills/autoloop rev-parse --short HEAD`):
- Update-check verdict at run start (the `update_check.py` line), if you have it:

**Anything else**
<!-- Was the metric LLM-judged? Was `candidates_per_round` > 1 / worktree isolation on? Screenshots or a run.log tail help. -->
