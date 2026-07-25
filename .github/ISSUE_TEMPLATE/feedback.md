---
name: Share a run / feedback
about: Tell us what you optimized, whether it worked, and what could be better
title: "[feedback] "
labels: feedback
---

**What did you point autoloop at?**
<!-- The task in a sentence: what artifact, what "better" meant. -->

**Did it work?**
<!-- Baseline -> best on the primary metric. Absolute and percent if you have it. -->
- Primary metric:
- Baseline:
- Best:
- Rounds run / stop reason:

**Counter-metrics — did a gate bite?**
<!-- Did the loop find a "win" that a counter-metric correctly blocked? Those stories are the most useful. -->

**What surprised you?**
<!-- Good or bad. Where did the loop waste budget? Where did it find something you wouldn't have? -->

**What would make it better?**
<!-- Rough is fine. Missing knobs, confusing steps, a stopping rule that quit too early or too late. -->

**Setup (optional)**
- OS / python3 version:
- Was the metric deterministic or LLM-judged?
