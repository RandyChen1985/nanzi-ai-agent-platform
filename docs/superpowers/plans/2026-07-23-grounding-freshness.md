# Grounding Freshness Implementation Plan

> **For agentic workers:** Implement the plan task-by-task with tests first. Do not run `./dev.sh`.

**Goal:** Extend the existing grounding gate with backward-compatible fact freshness requirements so dynamic facts require appropriate current evidence without changing static answers or existing ChatBI routing.

**Architecture:** Keep `EvidenceLedger` and `GroundingService` as the single evidence boundary. Add freshness metadata to evidence receipts, derive a freshness-aware `FactRequirement` from the canonical `RequestDecision`, and let unknown legacy decisions retain the current soft-warning behavior. ChatBI's existing `requires_fresh_data` remains authoritative for follow-up semantics and is mapped in a later integration step.

**Tech Stack:** Python dataclasses, Pydantic intent models, pytest, existing grounding and AgentScope runner infrastructure.

---

### Task 1: Add backward-compatible freshness and receipt metadata

**Files:**
- Modify: `app/services/ai/grounding/models.py`
- Modify: `app/services/ai/grounding/ledger.py`
- Test: `tests/ai/grounding/test_grounding_models.py`
- Test: `tests/ai/grounding/test_grounding_ledger.py`

- [ ] Add `FactFreshness` enum and optional receipt fields with defaults.
- [ ] Serialize and restore the new fields while accepting old snapshots.
- [ ] Add tests for new metadata and old snapshot compatibility.

### Task 2: Make grounding requirements freshness-aware

**Files:**
- Modify: `app/services/ai/grounding/policy.py`
- Test: `tests/ai/grounding/test_grounding_policy.py`

- [ ] Extend `FactRequirement` with freshness, age, source timestamp, reuse, and time-scope fields.
- [ ] Map runtime, local-file, ChatBI, web, docs, and conversation decisions to compatible defaults.
- [ ] Add freshness checks without changing the legacy `unknown` soft-warning path.

### Task 3: Propagate semantic freshness through intent and request decisions

**Files:**
- Modify: `app/services/ai/intent_service.py`
- Modify: `app/services/ai/request_decision.py`
- Test: `tests/ai/test_intent_service_agentscope.py`
- Test: `tests/ai/test_request_decision.py`

- [ ] Add defaulted structured fields for fact kind, freshness requirement, and time scope.
- [ ] Preserve existing source and ChatBI qualification behavior.
- [ ] Add tests proving machine/file queries do not become ChatBI because of aggregate wording.

### Task 4: Verify runner integration and compatibility

**Files:**
- Inspect/modify: `app/services/ai/runners/assistant_agent_runner.py`
- Inspect/modify: `app/services/ai/runners/data_agent_runner.py`
- Test: `tests/ai/runners/test_assistant_agent_grounding_gate.py`
- Test: `tests/ai/runners/test_data_agent_runner.py`

- [ ] Reuse the central requirement at tool preflight and final audit boundaries.
- [ ] Map ChatBI fresh-query versus follow-up reuse without changing existing tool behavior.
- [ ] Run focused grounding, routing, and ChatBI tests, plus `git diff --check` and Python compilation.
