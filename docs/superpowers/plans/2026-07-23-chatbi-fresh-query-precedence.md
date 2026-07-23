# ChatBI Fresh Query Precedence Implementation Plan

> **For agentic workers:** Execute this plan inline in the current session with test-first checkpoints. Do not stage or commit unrelated worktree changes.

**Goal:** Ensure a ChatBI request with a new data subject/time scope is queried before visualization or analysis, while preserving pure previous-result follow-ups.

**Architecture:** Keep the existing outer routing and grounding layers unchanged. Add the precedence check inside the shared ChatBI previous-result reuse predicate, using the existing explicit-new-query detector so the early shortcut, LLM correction, and rule fallback all share the same behavior; then cover the behavior at the classifier level with focused async tests.

**Tech Stack:** Python, pytest, pytest-asyncio, existing `DataQueryTurnType` classifier.

---

### Task 1: Add regression tests for fresh-query precedence

**Files:**
- Test: `tests/ai/test_turn_classifier.py`

- [x] **Step 1: Write the failing tests**

Add tests asserting that a new weekly agent-call request is classified as `NEW_DATA_QUERY` even when a previous result exists, and that a pure previous-result visualization remains `RESULT_ANALYSIS`.

- [x] **Step 2: Run the focused tests and verify the failure**

Run:

```bash
venv/bin/python -m pytest tests/ai/test_turn_classifier.py -k "fresh_query_precedes_previous_result_reuse or pure_previous_result_visualization_still_reuses" -q
```

Expected before the implementation: the new weekly agent-call test fails because the current classifier returns `RESULT_ANALYSIS`; the pure follow-up test remains green.

### Task 2: Make new data queries dominate every previous-result reuse path

**Files:**
- Modify: `app/services/ai/data_query_turn_classifier.py:796-817`

- [x] **Step 1: Implement the minimal precedence guard**

Change the shared reuse predicate so every caller rejects an explicit new data query before checking result-followup signals:

```python
if _looks_like_explicit_new_data_query(user_query):
    return False
```

This keeps “把刚才的结果画成柱状图” on the reuse path, while allowing “本周各智能体的调用情况” to continue to the existing classifier/LLM and fresh-query path. Placing the guard in `_can_reuse_previous_result` covers the direct and LLM-correction callers; the invalid-LLM rule fallback must also exclude explicit new queries before its pure-followup clarification branch.

- [x] **Step 2: Run the focused tests and verify they pass**

Run:

```bash
venv/bin/python -m pytest tests/ai/test_turn_classifier.py -k "fresh_query_precedes_previous_result_reuse or pure_previous_result_visualization_still_reuses" -q
```

Expected: both tests pass.

### Task 3: Run the scoped regression suite and repository checks

**Files:**
- No additional files.

- [x] **Step 1: Run all turn-classifier tests**

```bash
venv/bin/python -m pytest tests/ai/test_turn_classifier.py -q
```

- [x] **Step 2: Check the diff for whitespace errors**

```bash
git diff --check
```

- [x] **Step 3: Inspect the final diff**

```bash
git diff -- app/services/ai/data_query_turn_classifier.py tests/ai/test_turn_classifier.py
```

Confirm that only the classifier precedence and its regression tests changed for this fix; leave all pre-existing user modifications untouched.
