# Personal Skills Experience Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Let every logged-in user manage full personal skills from PersonalCenter, and close the create → edit → mount loop including SKILL.md preload for personal scope.

**Architecture:** Reuse personal-skills management via a `personalOnly` mode on the skills workbench (extract later if needed). Fix `load_skill_md_content` and mount payloads to honor personal paths. Surface structured `create_skills` success actions in chat.

**Tech Stack:** Vue 3, TypeScript, FastAPI/Python, pytest frontend contracts.

---

### Task 1: Fix personal SKILL.md preload

**Files:**
- Modify: `app/services/ai/skill_resolver.py`
- Modify: `app/services/ai/agent_service.py` (call sites if signature changes)
- Test: `tests/services/ai/test_skill_resolver_personal_preload.py` (create)

- [x] Extend `load_skill_md_content` to accept optional `user_info`, `scope`, and/or `skill_md_path`
- [x] Prefer explicit `skill_md_path` when safe; else resolve personal dir for current user
- [x] Keep global `SKILLS_DIR` behavior for platform skills
- [x] Update `_inject_skills` callers to pass scope/path/user_info
- [x] Tests: personal path loads; path traversal rejected; global still works

### Task 2: Mount scope on chat skill select

**Files:**
- Modify: `frontend/src/views/EmbedChat.vue`
- Modify: `frontend/src/views/AgentDebug.vue`
- Test: `tests/frontend/test_personal_skills_experience_contract.py` (create)

- [x] `handleSelectSkill` includes `scope` from skill (`personal` | `global`) in attachment/`skillMeta`
- [x] Contract asserts both views pass scope

### Task 3: PersonalCenter skills tab + personalOnly workbench

**Files:**
- Modify: `frontend/src/views/SkillsManagement.vue` (add `personalOnly` prop)
- Modify: `frontend/src/views/PersonalCenter.vue`
- Test: same contract file

- [x] `personalOnly`: hide global tab, force `activeScope='personal'`, no platform write UI
- [x] PersonalCenter add tab `skills`; support `?tab=skills&skill_id=`
- [x] Deep-link opens the given personal skill detail when present

### Task 4: create_skills result card + drawer empty state

**Files:**
- Modify: `app/services/ai/tools/system_executive_tools.py` (structured success marker if needed)
- Modify: frontend chat handlers / message UI for tool result actions
- Modify: `frontend/src/components/embed/SkillBrowserDrawer.vue`

- [x] Detect personal skill create success; show 去编辑 / 立即挂载
- [x] Drawer empty state links to personal center skills + create hint
- [x] Contract coverage

### Task 5: Verify

- [x] Focused pytest green with `REDIS_ENABLE=false`
- [x] `git diff --check`
- [x] Do not run `./dev.sh`; hand browser verification to user
