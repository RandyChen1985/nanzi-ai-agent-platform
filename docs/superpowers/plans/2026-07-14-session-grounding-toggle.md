# Session Grounding Toggle Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:test-driven-development and execute each checkbox in order. This workspace does not auto-commit or run service restart scripts.

**Goal:** Replace the duplicate settings model selector with a default-off session grounding toggle that fully bypasses anti-hallucination audits when disabled.

**Architecture:** Pass strict `debug_options.grounding_enabled` from EmbedChat into the existing runner debug-options channel. Centralize the boolean interpretation in `BaseExecutor`, then bypass Main buffering, ChatBI audit warnings, and Knowledge reflection when disabled.

**Tech Stack:** Vue 3, TypeScript, Python 3, pytest.

---

### Task 1: Lock backend switch behavior

**Files:**
- Modify: `tests/ai/runners/test_assistant_agent_grounding_gate.py`
- Modify: `tests/ai/runners/test_data_agent_runner.py`
- Modify: `tests/ai/runners/test_knowledge_hallucination_guard.py`

- [x] Add tests proving missing/false `grounding_enabled` bypasses warnings and retries.
- [x] Add tests proving explicit `true` retains existing audits.
- [x] Run focused tests and confirm expected RED failures.

### Task 2: Implement shared backend bypass

**Files:**
- Modify: `app/services/ai/executors/base.py`
- Modify: `app/services/ai/runners/assistant_agent_runner.py`
- Modify: `app/services/ai/runners/data_agent_runner.py`
- Modify: `app/services/ai/runners/knowledge_agent_runner.py`

- [x] Add strict `_grounding_enabled()` to `BaseExecutor`.
- [x] Disable Main output buffering and resume auditing when false.
- [x] Pass disabled state through ChatBI audit.
- [x] Skip Knowledge evaluation and retry when false.
- [x] Run focused tests and confirm GREEN.

### Task 3: Replace the settings control

**Files:**
- Modify: `frontend/src/components/embed/ChatSettings.vue`
- Modify: `frontend/src/views/EmbedChat.vue`
- Create: `frontend/scripts/groundingToggle.test.ts`

- [x] Write a failing source-contract test for placement, default, payload, and reset behavior.
- [x] Replace the model selector with the grounding toggle.
- [x] Send an explicit boolean on every request and reset it for new conversations.
- [x] Run the frontend contract test. TypeScript/service builds remain user-operated per workspace instructions.

### Task 4: Documentation and regression

**Files:**
- Modify: `docs/platform-grounding-gate.md`
- Modify: `docs/md/ai_agent_gating_contract.md`
- Modify: `tests/CHECKLIST.md`

- [x] Document default-off session behavior and legacy-client compatibility.
- [x] Run grounding/Main/ChatBI/Knowledge focused regressions.
- [x] Run `py_compile` and `git diff --check`.
- [x] Do not run `./dev.sh`; leave service restart to the user.

### Task 5: Align AgentDebug settings

**Files:**
- Modify: `frontend/src/components/DebugConfigPanel.vue`
- Modify: `frontend/src/views/AgentDebug.vue`
- Modify: `frontend/scripts/groundingToggle.test.ts`

- [x] Add failing assertions that AgentDebug removes the duplicate model override, exposes the grounding toggle, sends an explicit boolean, and resets it for a new conversation.
- [x] Add `enableGrounding: false` to the shared debug config contract and replace the right-panel model override with a checkbox.
- [x] Keep the ChatInput model selector bound to `debugConfig.model` and pass `grounding_enabled` in `debug_options`.
- [x] Run the frontend contract test, `git diff --check`, and leave service compilation to the user.

### Task 6: Add shared grounding help examples

**Files:**
- Create: `frontend/src/components/GroundingHelpPopover.vue`
- Modify: `frontend/src/components/embed/ChatSettings.vue`
- Modify: `frontend/src/components/DebugConfigPanel.vue`
- Modify: `frontend/scripts/groundingToggle.test.ts`
- Modify: `docs/platform-grounding-gate.md`
- Modify: `tests/CHECKLIST.md`

- [x] Add failing source-contract assertions for the shared component, both entry points, the three result examples, and close interactions.
- [x] Run `node --experimental-strip-types scripts/groundingToggle.test.ts` and confirm it fails because `GroundingHelpPopover.vue` does not exist.
- [x] Implement the compact `?` popover with hover/click opening plus outside-click and `Esc` closing.
- [x] Mount the shared component beside both grounding labels without nesting its button inside a checkbox label.
- [x] Document the help example and run the frontend contract test plus `git diff --check`; do not run build or service scripts.

### Task 7: Show grounding toggle Toast feedback

**Files:**
- Modify: `frontend/scripts/groundingToggle.test.ts`
- Modify: `frontend/src/components/embed/ChatSettings.vue`
- Modify: `frontend/src/components/DebugConfigPanel.vue`
- Modify: `docs/platform-grounding-gate.md`
- Modify: `tests/CHECKLIST.md`

- [x] **Step 1: Add failing Toast contract assertions**

Assert that both components import `useToast`, contain “反幻觉校验已开启” with `success` and “反幻觉校验已关闭” with `info`; also assert that ChatSettings compares the requested state with the current state and AgentDebug binds `@change="handleGroundingChange"`.

- [x] **Step 2: Run the contract test and verify RED**

Run: `cd frontend && node --experimental-strip-types scripts/groundingToggle.test.ts`

Expected: FAIL because `ChatSettings.vue` does not import `useToast`.

- [x] **Step 3: Implement minimal Toast feedback**

Use the existing global composable in both components:

```ts
const { showToast } = useToast();

showToast(
  enabled ? "反幻觉校验已开启" : "反幻觉校验已关闭",
  enabled ? "success" : "info",
);
```

ChatSettings returns without showing a Toast when `config.enableGrounding === enabled`, while preserving its existing save-and-close behavior. AgentDebug invokes a `handleGroundingChange()` handler from the checkbox `change` event and reads the already-updated `config.enableGrounding` value.

- [x] **Step 4: Verify GREEN and static validity**

Run the frontend contract test, parse `ChatSettings.vue` and `DebugConfigPanel.vue` with `@vue/compiler-sfc`, and run `git diff --check`.

Expected: all checks pass. Do not run frontend build, `./dev.sh`, or service restart commands.
