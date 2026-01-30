# LLM Judge Rubric

> This document defines the rubric used by the LLM judge to evaluate developer
> bug-fix traces. It also specifies the required JSON output shape.

---

## Scoring Dimensions

Each dimension is scored **0.0–5.0** (float, one decimal place).

### 1. Root-Cause Identification (`root_cause_identification`)
- **5**: Developer correctly identifies the exact root cause with evidence (logs, stack traces, code references).
- **4**: Root cause is correct; evidence is present but incomplete.
- **3**: Root cause is approximately correct; some confusion or misdirection.
- **2**: Partially correct; significant time spent on wrong hypotheses.
- **1**: Root cause not clearly identified; fix may be coincidental.
- **0**: No evidence of root-cause analysis.

### 2. Plan Quality (`plan_quality`)
- **5**: Clear hypothesis-driven plan; systematic test-iterate loop; well-structured approach.
- **4**: Good plan with minor gaps in systematicity.
- **3**: Reasonable approach but reactive rather than planned.
- **2**: Ad-hoc debugging; no clear plan evident.
- **1**: Chaotic process; random changes.
- **0**: No discernible plan or methodology.

### 3. Experiment & Iterate Loop (`experiment_iterate_loop`)
- **5**: Each change is tested; results inform next step; clear feedback loop.
- **4**: Good iteration with occasional untested changes.
- **3**: Some iteration; gaps in testing intermediate states.
- **2**: Minimal iteration; large jumps between states.
- **1**: No meaningful iteration; single attempt.
- **0**: No experimentation visible.

### 4. Use of Signals — Tests & Logs (`use_of_signals_tests_logs`)
- **5**: Tests, logs, stack traces, and error messages are consistently used to guide decisions.
- **4**: Signals are mostly used; occasional missed signals.
- **3**: Some signal usage; important signals sometimes ignored.
- **2**: Minimal use of available signals.
- **1**: Signals largely ignored.
- **0**: No signal usage evident.

### 5. Minimality of Fix (`minimality_of_fix`)
- **5**: Fix is precisely targeted; no unrelated changes; minimal diff.
- **4**: Fix is targeted with minor unnecessary changes.
- **3**: Fix includes some unrelated cleanup or refactoring.
- **2**: Significant unrelated changes mixed with the fix.
- **1**: Overly broad changes; hard to isolate the actual fix.
- **0**: Changes are mostly unrelated to the bug.

### 6. Clarity (`clarity`)
- **5**: Reasoning is clear, well-documented, and directly grounded in code/evidence.
- **4**: Mostly clear; minor gaps in explanation.
- **3**: Understandable but could be clearer; some leaps in logic.
- **2**: Reasoning is hard to follow; significant gaps.
- **1**: Very unclear reasoning.
- **0**: No reasoning provided.

---

## Overall Score

`overall` = weighted average of all 6 dimensions (equal weight for MVP).

```
overall = (root_cause_identification + plan_quality + experiment_iterate_loop
           + use_of_signals_tests_logs + minimality_of_fix + clarity) / 6.0
```

Rounded to one decimal place.

---

## Flags

The judge may set zero or more flags from this fixed set:

- `hallucination_risk` — Judge suspects developer reasoning contains fabricated information.
- `missing_steps` — Significant debugging steps appear to be missing from the trace.
- `unsafe_suggestion` — Fix introduces potential security or reliability concerns.
- `incomplete_fix` — Fix may not fully resolve the reported bug.
- `exemplary_trace` — Trace is exceptionally high quality and suitable as a training example.

---

## Required JSON Output Shape

The judge **must** return exactly this JSON structure. No additional keys allowed at the top level.

```json
{
  "scores": {
    "root_cause_identification": 0.0,
    "plan_quality": 0.0,
    "experiment_iterate_loop": 0.0,
    "use_of_signals_tests_logs": 0.0,
    "minimality_of_fix": 0.0,
    "clarity": 0.0
  },
  "overall": 0.0,
  "rationale": "Free-text explanation of scores (1-3 paragraphs).",
  "flags": ["hallucination_risk"]
}
```

### Validation Rules
- All 6 score keys must be present.
- All scores must be floats in range [0.0, 5.0].
- `overall` must be a float in range [0.0, 5.0].
- `rationale` must be a non-empty string.
- `flags` must be an array (may be empty) containing only values from the fixed set above.

---

## Judge Prompt Template

The judge receives a "judge packet" containing:
1. Bug report summary (title, description, repro steps)
2. Ordered list of key events (thoughts, test outcomes, file edits with diffs)
3. Final diff summary
4. Test results (pass/fail, stdout/stderr excerpts)

The system prompt instructs the judge to:
- Evaluate using the rubric above
- Return strictly valid JSON matching the output shape
- Use `temperature: 0` for deterministic scoring
- Set flags only when clearly warranted

Model is configurable via `JUDGE_MODEL` environment variable.
