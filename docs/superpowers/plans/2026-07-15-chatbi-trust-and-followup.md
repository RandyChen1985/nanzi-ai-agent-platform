# ChatBI Trust and Follow-up Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add truthful ChatBI data evidence and a compact “继续分析” action chooser without changing existing query execution behavior.

**Architecture:** Build a pure backend insight builder from `DataRunState` and successful SQL results, emit it as an additive SSE event, and merge it into a shared frontend message type. Render evidence and follow-up actions through focused shared Vue components used by both chat surfaces.

**Tech Stack:** Python 3, FastAPI/AgentScope streaming, pytest, Vue 3, TypeScript, existing frontend contract tests.

---

### Task 1: Backend insight builder

**Files:**
- Create: `app/services/ai/runners/chatbi/insight_meta.py`
- Modify: `app/services/ai/runners/chatbi/run_state.py`
- Test: `tests/ai/runners/test_chatbi_insight_meta.py`

- [ ] Write failing tests for safe evidence extraction, execution mode, row count, and deterministic actions.
- [ ] Run `REDIS_ENABLE=false PYTHONPATH=. venv/bin/python -m pytest tests/ai/runners/test_chatbi_insight_meta.py -q` and confirm missing-module failure.
- [ ] Implement typed pure helpers and minimal state fields.
- [ ] Re-run the focused tests and confirm they pass.

### Task 2: Additive SSE emission

**Files:**
- Modify: `app/services/ai/runners/chatbi/native_turn.py`
- Modify: `app/services/ai/runners/chatbi/react_stream.py`
- Modify: `app/services/ai/runners/chatbi/turn_handlers.py`
- Test: `tests/ai/runners/test_chatbi_insight_meta.py`

- [ ] Write a failing test proving one `chatbi_insight_meta` event is emitted after a successful result.
- [ ] Run the focused test and confirm the missing event failure.
- [ ] Emit the additive event once for native and federated success paths.
- [ ] Re-run the focused test and existing ChatBI module tests.

### Task 3: Shared frontend contract and reducer

**Files:**
- Create: `frontend/src/types/chatbiInsight.ts`
- Create: `frontend/src/utils/chatbiInsight.ts`
- Test: `tests/frontend/test_chatbi_insight_contract.py`

- [ ] Write failing source-contract tests for the event reducer, stable types, and four-action cap.
- [ ] Run `venv/bin/python -m pytest tests/frontend/test_chatbi_insight_contract.py -q` and confirm failure.
- [ ] Implement the types and event merge helper.
- [ ] Re-run the contract tests and confirm they pass.

### Task 4: Data evidence component

**Files:**
- Create: `frontend/src/components/chatbi/ChatBIDataEvidence.vue`
- Modify: `frontend/src/views/EmbedChat.vue`
- Modify: `frontend/src/views/AgentDebug.vue`
- Test: `tests/frontend/test_chatbi_insight_contract.py`

- [ ] Extend the failing contract test to require the shared component on both chat surfaces.
- [ ] Run the test and confirm failure.
- [ ] Implement the collapsed evidence card and connect the SSE event in both surfaces.
- [ ] Re-run the contract test.

### Task 5: “继续分析” chooser

**Files:**
- Create: `frontend/src/components/chatbi/ChatBIContinueAnalysis.vue`
- Modify: `frontend/src/views/EmbedChat.vue`
- Modify: `frontend/src/views/AgentDebug.vue`
- Test: `tests/frontend/test_chatbi_insight_contract.py`

- [ ] Extend the failing contract test for one visible trigger, desktop Popover, mobile Action Sheet, and emitted action query.
- [ ] Run the test and confirm failure.
- [ ] Implement the shared chooser and replace the fixed visual-analysis button.
- [ ] Re-run the contract test.

### Task 6: Verification

**Files:**
- Verify all changed files.

- [ ] Run focused backend and frontend contract tests.
- [ ] Run existing ChatBI runner/module tests proportional to the touched paths.
- [ ] Run Python compilation and `git diff --check`.
- [ ] Inspect `git diff` for accidental changes and report results without running `./dev.sh`.

