# Runtime Forbidden Tools Hardening Implementation Plan

> **For agentic workers:** Execute inline with test-driven development. Do not stage or commit without user approval.

**Goal:** Make user and role forbidden-tool policies reliably cover AgentScope runtime and remove the identified frontend and compatibility regressions.

**Architecture:** Keep permission persistence unchanged. Centralize runtime policy lookup and decision creation in `agentscope/tools.py`, call it consistently from runtime and native wrappers, preserve native wrapping whenever a spec carries `native_tool`, and parse shell command tokens before applying command-name rules.

**Tech Stack:** Python, AgentScope, SQLAlchemy, pytest, Vue 3, TypeScript.

---

### Task 1: Lock runtime behavior with tests

**Files:**
- Modify: `tests/ai/runtime/test_agentscope_tooling.py`
- Modify: `tests/test_skills_management.py`

- [ ] Add tests proving native specs remain native-wrapped, native tools receive forbidden-tool and command checks with explicit `user_id`, aliases are honored, command names match token boundaries, and policy lookup failures deny controlled execution.
- [ ] Run the focused tests and confirm the new cases fail for the reviewed reasons.

### Task 2: Harden AgentScope runtime enforcement

**Files:**
- Modify: `app/services/ai/runtime/agentscope/tools.py`

- [ ] Restore `spec.native_tool is not None` as the native-wrapper condition.
- [ ] Apply forbidden-tool and command checks to both wrapper types with explicit `user_id`.
- [ ] Remove debug logging, consolidate policy loading, use shell token parsing for command matching, and deny when a user-scoped policy cannot be loaded.
- [ ] Run focused runtime tests until green.

### Task 3: Correct frontend types and input boundaries

**Files:**
- Modify: `frontend/src/views/Users.vue`
- Modify: `frontend/src/views/Roles.vue`
- Modify: `app/schemas/permission.py`

- [ ] Include `forbidden_configs` in the user page tab type without treating it as a permission-data resource key.
- [ ] Normalize and de-duplicate command entries and constrain stored values to the existing `resource_id` limit.
- [ ] Keep frontend controls compatible with the backend validation.

### Task 4: Regression verification and cleanup

**Files:**
- Modify: touched files only for formatting cleanup.

- [ ] Run permission API and AgentScope runtime test slices.
- [ ] Run `git diff --check` and remove trailing whitespace.
- [ ] Inspect the final diff and report remaining validation limits; do not compile or restart services.
