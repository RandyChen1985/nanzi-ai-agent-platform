# ChatBI Non-Data Routing Split Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Separate non-data requests from genuine data-query clarification so ChatBI redirects unrelated requests without showing data-gap prompts.

**Architecture:** Extend the ChatBI turn classification contract with distinct `NON_DATA_REQUEST` and `CLARIFICATION_REQUIRED` turn types plus structured `missing_fields`. Route the two types to separate response assembly paths, while retaining compatibility with the legacy combined enum value and preserving all existing data-query paths.

**Tech Stack:** Python 3, asyncio, pytest, AgentScope-compatible chat client, SSE runner handlers.

---

### Task 1: Split the classification contract

**Files:**
- Modify: `app/services/ai/data_query_turn_classifier.py`
- Test: `tests/ai/test_turn_classifier.py`

- [ ] **Step 1: Write failing classification tests**

Add tests asserting that model/capability/writing questions return `NON_DATA_REQUEST` without a classifier LLM call; incomplete but explicit data requests return `CLARIFICATION_REQUIRED` with structured missing fields; and complete queries remain `NEW_DATA_QUERY`.

- [ ] **Step 2: Verify the tests fail**

Run: `venv/bin/python -m pytest tests/ai/test_turn_classifier.py -q`

Expected: failures because the new enum values and `missing_fields` contract do not exist.

- [ ] **Step 3: Implement the classification contract**

Add `NON_DATA_REQUEST` and `CLARIFICATION_REQUIRED`, add `missing_fields: tuple[str, ...]`, introduce high-confidence non-data detection, and update the LLM JSON schema/parser to return structured missing fields. Preserve `CLARIFICATION_OR_NON_DATA` as a compatibility-only value.

- [ ] **Step 4: Verify classification tests pass**

Run: `venv/bin/python -m pytest tests/ai/test_turn_classifier.py -q`

Expected: all classification tests pass.

### Task 2: Separate response assembly and runner dispatch

**Files:**
- Modify: `app/services/ai/executors/prompts.py`
- Modify: `app/services/ai/runners/chatbi/clarification.py`
- Modify: `app/services/ai/runners/chatbi/turn_handlers.py`
- Test: `tests/ai/test_data_query_prompts.py`
- Test: `tests/ai/runners/test_data_agent_runner.py`
- Test: `tests/ai/runners/test_chatbi_modules.py`

- [ ] **Step 1: Write failing response and dispatch tests**

Add tests asserting non-data responses contain the switch action and never contain data-gap buttons, while clarification responses generate buttons only for their explicit `missing_fields`. Assert both new types are early-exit turns.

- [ ] **Step 2: Verify the tests fail**

Run: `venv/bin/python -m pytest tests/ai/test_data_query_prompts.py tests/ai/runners/test_data_agent_runner.py tests/ai/runners/test_chatbi_modules.py -q`

Expected: failures because the independent handlers and structured-gap response API do not exist.

- [ ] **Step 3: Implement independent handlers**

Add deterministic non-data guidance, pass `missing_fields` through the clarification runner, and dispatch `NON_DATA_REQUEST` separately from `CLARIFICATION_REQUIRED`. Stop defaulting an unknown clarification to the time/metric/object trio.

- [ ] **Step 4: Verify response and runner tests pass**

Run: `venv/bin/python -m pytest tests/ai/test_data_query_prompts.py tests/ai/runners/test_data_agent_runner.py tests/ai/runners/test_chatbi_modules.py -q`

Expected: all selected tests pass.

### Task 3: Regression verification

**Files:**
- Modify only if a regression exposes an in-scope compatibility issue.

- [ ] **Step 1: Run the focused ChatBI suite**

Run: `venv/bin/python -m pytest tests/ai/test_turn_classifier.py tests/ai/test_data_query_prompts.py tests/ai/runners/test_chatbi_modules.py tests/ai/runners/test_data_agent_runner.py -q`

Expected: all tests pass.

- [ ] **Step 2: Inspect the final diff**

Run: `git diff --check` and `git status --short`.

Expected: no whitespace errors; only the plan, targeted production files, and targeted tests are changed.

- [ ] **Step 3: Request code review**

Review the implementation against `docs/superpowers/specs/2026-07-14-chatbi-non-data-routing-design.md`, then address all critical and important findings before handoff.

### Task 4: Contextual clarification recommendations

**Files:**
- Modify: `app/services/ai/executors/prompts.py`
- Modify: `app/services/ai/runners/chatbi/clarification.py`
- Test: `tests/ai/test_data_query_prompts.py`

- [ ] **Step 1: Write failing recommendation tests**

Assert that validated semantic recommendations replace mechanical gap templates, preserve the original query topic, and reject unrelated or pseudo-query candidates.

- [ ] **Step 2: Verify the tests fail**

Run: `venv/bin/python -m pytest tests/ai/test_data_query_prompts.py -q`

Expected: failures because contextual recommendation parsing and response injection do not exist.

- [ ] **Step 3: Implement semantic recommendation generation**

Ask the configured model for two or three complete candidate queries using the original question, recent history, and structured gaps. Validate candidates in code and fall back to deterministic gap-specific prompts only when no candidate survives.

- [ ] **Step 4: Verify prompt and runner regressions**

Run: `venv/bin/python -m pytest tests/ai/test_data_query_prompts.py tests/ai/test_turn_classifier.py -q`

Expected: all selected tests pass.
