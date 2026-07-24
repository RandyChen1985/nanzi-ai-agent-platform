# Welcome Card Transition Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make fallback, generated, and cache-refreshed welcome-card sets switch smoothly without changing their data or click behavior.

**Architecture:** `WelcomeDashboard.vue` will render one content-keyed card-set surface instead of two mutually exclusive grids. A Vue transition will animate the set replacement, while card items use a small staggered reveal; the same key changes for fallback-to-recommendation and recommendation-to-recommendation updates.

**Tech Stack:** Vue 3, `<Transition>`, scoped CSS, existing pytest source-contract tests.

---

### Task 1: Lock down the replacement transition contract

**Files:**
- Modify: `tests/frontend/test_agent_welcome_cards_contract.py`

- [ ] **Step 1: Write the failing test**

```python
def test_welcome_cards_transition_between_fallback_and_refreshed_sets():
    dashboard = _source("frontend/src/components/embed/WelcomeDashboard.vue")

    assert '<Transition name="welcome-card-set"' in dashboard
    assert 'mode="out-in"' in dashboard
    assert 'welcomeCardSetKey' in dashboard
    assert 'welcome-card-item' in dashboard
    assert '@media (prefers-reduced-motion: reduce)' in dashboard
```

- [ ] **Step 2: Run test to verify it fails**

Run: `REDIS_ENABLE=false .venv/bin/python -m pytest tests/frontend/test_agent_welcome_cards_contract.py::test_welcome_cards_transition_between_fallback_and_refreshed_sets -q`

Expected: FAIL because the current dashboard has direct `v-if` / `v-else` grid replacement and no transition contract.

### Task 2: Replace hard grid mounting with a keyed transition surface

**Files:**
- Modify: `frontend/src/components/embed/WelcomeDashboard.vue`
- Test: `tests/frontend/test_agent_welcome_cards_contract.py`

- [ ] **Step 1: Implement the smallest transition surface**

```vue
<Transition name="welcome-card-set" mode="out-in">
  <div :key="welcomeCardSetKey" class="grid ...">
    <button v-for="(card, index) in welcomeCardItems" class="welcome-card-item" :style="{ '--welcome-card-index': index }">
```

Use a computed `welcomeCardSetKey` based on the three displayed cards, and retain the existing fallback actions and configured-card quick-question action.

- [ ] **Step 2: Add scoped enter/leave, stagger, and reduced-motion CSS**

```css
.welcome-card-set-enter-active { transition: opacity 260ms ease, transform 260ms ease; }
.welcome-card-set-leave-active { transition: opacity 140ms ease, transform 140ms ease; }
.welcome-card-item { animation: welcome-card-reveal 260ms both; }
@media (prefers-reduced-motion: reduce) { /* disable motion while retaining visibility */ }
```

- [ ] **Step 3: Run the focused contract test**

Run: `REDIS_ENABLE=false .venv/bin/python -m pytest tests/frontend/test_agent_welcome_cards_contract.py -q`

Expected: PASS.

### Task 3: Validate adjacent welcome behavior

**Files:**
- Test: `tests/frontend/test_agent_welcome_cards_contract.py`
- Test: `tests/services/ai/test_welcome_card_service.py`

- [ ] **Step 1: Run focused regression checks**

Run: `REDIS_ENABLE=false .venv/bin/python -m pytest tests/frontend/test_agent_welcome_cards_contract.py tests/services/ai/test_welcome_card_service.py -q`

Expected: PASS with the existing API/cache contracts unchanged.

- [ ] **Step 2: Check whitespace and unintended changes**

Run: `git diff --check`

Expected: no output.
