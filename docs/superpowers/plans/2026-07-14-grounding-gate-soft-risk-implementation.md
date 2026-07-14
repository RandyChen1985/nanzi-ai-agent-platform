# Grounding Gate Soft Risk Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Replace Main assistant fact-grounding blocks with evidence-aware inline risk warnings while preserving trustworthy evidence receipts and unrelated security gates.

**Architecture:** Extend the grounding policy with a warning decision and risk metadata, expose the evidence types recorded in the per-turn ledger, and make `AssistantAgentRunner` append one server-generated Markdown warning after the original buffered response. Merge attachment evidence requirements instead of overriding route requirements, and apply the same behavior to first-generation and resumed streams.

**Tech Stack:** Python 3, asyncio, AgentScope runtime wrappers, pytest.

---

### Task 1: Grounding policy warning decisions

**Files:**
- Modify: `app/services/ai/grounding/ledger.py`
- Modify: `app/services/ai/grounding/policy.py`
- Test: `tests/ai/grounding/test_evidence_ledger.py`
- Test: `tests/ai/grounding/test_grounding_policy.py`

- [ ] **Step 1: Write failing policy and ledger tests**

Add tests proving that the ledger exposes all recorded evidence types, exact matches return `PASS`, internal-source mismatches return `PASS_WITH_WARNING`, missing evidence for a concrete factual answer returns a high-risk warning, and ordinary non-factual output does not receive a warning.

- [ ] **Step 2: Run the tests and verify RED**

Run:

```bash
REDIS_ENABLE=false PYTHONPATH=. venv/bin/python -m pytest tests/ai/grounding/test_evidence_ledger.py tests/ai/grounding/test_grounding_policy.py -q
```

Expected: failures because `PASS_WITH_WARNING`, risk metadata, and ledger evidence-type aggregation do not exist.

- [ ] **Step 3: Implement the minimal policy model**

Add `GroundingAction.PASS_WITH_WARNING`, a `GroundingRiskLevel` enum, risk and available-evidence fields on `GroundingDecision`, internal trusted-source compatibility, and warning decisions for unmatched factual answers. Keep failed results excluded by `EvidenceLedger.record_success()`.

- [ ] **Step 4: Run the policy tests and verify GREEN**

Run the command from Step 2. Expected: all policy and ledger tests pass.

### Task 2: Merge attachment evidence requirements

**Files:**
- Modify: `app/services/ai/runners/assistant_agent_runner.py`
- Test: `tests/ai/runners/test_assistant_agent_grounding_gate.py`

- [ ] **Step 1: Write failing attachment tests**

Add tests proving that a current-turn attachment adds `user_file` to an existing `internal_knowledge` requirement, an attachment continuation adds the same alternative, and a historical attachment unrelated to the current request adds nothing.

- [ ] **Step 2: Run the focused tests and verify RED**

```bash
REDIS_ENABLE=false PYTHONPATH=. venv/bin/python -m pytest tests/ai/runners/test_assistant_agent_grounding_gate.py -q
```

Expected: the new union assertion fails because the runner currently replaces accepted types with only `user_file`.

- [ ] **Step 3: Implement requirement merging**

Return a new `FactRequirement` whose `accepted_types` is the union of the route requirement and `user_file`; preserve `required=True` and the existing unknown-output scrutiny flag.

- [ ] **Step 4: Run the focused tests and verify GREEN**

Run the command from Step 2. Expected: all attachment requirement tests pass.

### Task 3: Preserve Main output and append one risk warning

**Files:**
- Modify: `app/services/ai/runners/assistant_agent_runner.py`
- Test: `tests/ai/runners/test_assistant_agent_grounding_gate.py`
- Test: `tests/ai/runners/test_assistant_agent_data_guard.py`

- [ ] **Step 1: Write failing stream tests**

Add tests proving that unmatched evidence preserves the generated answer, appends exactly one Markdown risk warning, emits no `grounding_blocked` event, a knowledge receipt can satisfy the internal compatibility path, exact evidence adds no warning, and the legacy fabricated-data guard also preserves text with a warning.

- [ ] **Step 2: Run the focused tests and verify RED**

```bash
REDIS_ENABLE=false PYTHONPATH=. venv/bin/python -m pytest tests/ai/runners/test_assistant_agent_grounding_gate.py tests/ai/runners/test_assistant_agent_data_guard.py -q
```

Expected: failures because current strict branches replace content and emit a block/switch response.

- [ ] **Step 3: Implement inline warning output**

Add a single helper that converts policy risk metadata into the approved low/medium/high Markdown notice. In `_execute_raw`, yield the original buffered chunks first and then one warning chunk for `PASS_WITH_WARNING` or legacy data-guard detection. Keep exact matches unchanged and stop emitting `grounding_blocked` from the default Main path.

- [ ] **Step 4: Run the focused tests and verify GREEN**

Run the command from Step 2. Expected: all Main stream tests pass.

### Task 4: Apply the same rule to resumed streams

**Files:**
- Modify: `app/services/ai/runners/assistant_agent_runner.py`
- Test: `tests/ai/runners/test_assistant_agent_grounding_gate.py`

- [ ] **Step 1: Write a failing resume-path test**

Exercise the post-interrupt audit boundary and assert that an ungrounded resumed answer is preserved, receives one warning, and emits no `grounding_blocked` event.

- [ ] **Step 2: Run the resume test and verify RED**

```bash
REDIS_ENABLE=false PYTHONPATH=. venv/bin/python -m pytest tests/ai/runners/test_assistant_agent_grounding_gate.py -q
```

Expected: failure because the resume path still replaces the buffered answer with a block event and fallback content.

- [ ] **Step 3: Reuse the warning helper in the resume path**

Yield the original buffered chunks and append the same single server-generated warning used by the initial stream.

- [ ] **Step 4: Run the resume tests and verify GREEN**

Run the command from Step 2. Expected: all grounding runner tests pass.

### Task 5: Regression verification and documentation alignment

**Files:**
- Modify: `docs/platform-grounding-gate.md`
- Modify: `docs/md/ai_agent_gating_contract.md`
- Verify: grounding, runtime evidence, delegation, ChatBI reuse, and knowledge guard tests

- [ ] **Step 1: Update runtime documentation**

Document that Main grounding is warning-first, internal sources can be compatible with an explicit source notice, and user-visible block cards are no longer emitted by the default Main path. Keep knowledge-runner and security-gate boundaries explicit.

- [ ] **Step 2: Run the complete targeted regression slice**

```bash
REDIS_ENABLE=false PYTHONPATH=. venv/bin/python -m pytest \
  tests/ai/grounding/test_evidence_ledger.py \
  tests/ai/grounding/test_grounding_policy.py \
  tests/ai/runtime/test_agentscope_tool_evidence.py \
  tests/ai/runners/test_assistant_agent_grounding_gate.py \
  tests/ai/runners/test_assistant_agent_data_guard.py \
  tests/ai/runners/test_knowledge_hallucination_guard.py \
  tests/ai/test_sub_agent_delegation.py \
  tests/ai/runners/test_data_agent_runner.py -q
```

Expected: all selected tests pass.

- [ ] **Step 3: Run static checks**

```bash
PYTHONPATH=. venv/bin/python -m py_compile \
  app/services/ai/grounding/ledger.py \
  app/services/ai/grounding/policy.py \
  app/services/ai/runners/assistant_agent_runner.py
git diff --check
```

Expected: no output from `py_compile` or `git diff --check`.

- [ ] **Step 4: Review the final diff**

Confirm that the change does not modify frontend card compatibility, permissions, SQL gates, knowledge-runner NLI behavior, or service startup. Do not stage, commit, or run `./dev.sh` unless the user explicitly requests it.

