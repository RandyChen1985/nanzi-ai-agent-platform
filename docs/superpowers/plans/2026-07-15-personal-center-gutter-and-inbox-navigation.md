# PersonalCenter Gutter and Inbox Navigation Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add a small responsive horizontal gutter to PersonalCenter and make saved-report inbox notifications open their target even when Chat is already mounted or marking the notification read fails.

**Architecture:** Dashboard owns route-specific outer spacing. PortalNotificationBell creates a request-scoped target, expresses it through `/dashboard/chat` query parameters, and publishes a host-window event after navigation. Chat forwards runtime events as lightweight `OPEN_SAVED_REPORT` messages; EmbedChat deduplicates by `request_id` and passes a reactive target through DatasetPortalDrawer to the already-mounted DatasetCapabilityMenu.

**Tech Stack:** Vue 3 Composition API, Vue Router 4, TypeScript, Tailwind CSS, pytest source-contract tests.

---

### Task 1: Lock the regression contracts

**Files:**
- Modify: `tests/frontend/test_data_portal_home_contract.py`
- Modify: `tests/frontend/test_portal_notification_bell_contract.py`

- [ ] **Step 1: Replace the flush PersonalCenter expectation with a light-gutter expectation**

Update the PersonalCenter contract to require a dedicated Dashboard spacing computation, `px-3 sm:px-4` for PersonalCenter, unchanged `p-0` for AIChat, and continued `$route.path` component identity.

```python
def test_personal_center_has_light_horizontal_gutter_without_remounting_tabs():
    personal = _read("frontend/src/views/PersonalCenter.vue")
    dashboard = _read("frontend/src/views/Dashboard.vue")

    assert 'class="min-h-full bg-white"' in personal
    assert "rounded-lg shadow-sm border border-gray-200" not in personal
    assert "activeTab === 'data' ? '' : 'px-4 pb-4 sm:px-6 sm:pb-6'" in personal
    assert "dashboardContentSpacing" in dashboard
    assert "route.name === 'AIChat'" in dashboard
    assert "route.name === 'PersonalCenter'" in dashboard
    assert "px-3 sm:px-4" in dashboard
    assert ':key="$route.path"' in dashboard
    assert ':key="$route.fullPath"' not in dashboard
```

- [ ] **Step 2: Add notification navigation contracts**

Require Chat to track iframe readiness and forward a dedicated runtime-open message without reinitializing the chat. Require notification navigation to continue after a mark-read error.

```python
def test_saved_report_notification_uses_runtime_open_message_without_reinitializing_chat():
    chat = (ROOT / "frontend/src/views/Chat.vue").read_text(encoding="utf-8")
    embed = (ROOT / "frontend/src/views/EmbedChat.vue").read_text(encoding="utf-8")

    assert "widgetReady" in chat
    assert "SAVED_REPORT_OPEN_EVENT" in chat
    assert "createSavedReportOpenMessage" in chat
    assert 'case "OPEN_SAVED_REPORT":' in embed
    open_case = embed.split('case "OPEN_SAVED_REPORT":', 1)[1].split("break;", 1)[0]
    assert "initChat" not in open_case
    assert ':key="$route.fullPath"' not in chat


def test_mark_read_failure_does_not_block_saved_report_navigation():
    bell = (ROOT / "frontend/src/components/PortalNotificationBell.vue").read_text(encoding="utf-8")

    assert "notificationTarget" in bell
    assert "catch (error)" in bell
    assert "console.warn" in bell
    assert "closeNotifications()" in bell
    assert "await router.push(notificationTarget)" in bell
    assert "publishSavedReportOpenRequest" in bell
```

- [ ] **Step 3: Run the focused contracts and confirm RED**

Run:

```bash
venv/bin/python -m pytest tests/frontend/test_data_portal_home_contract.py tests/frontend/test_portal_notification_bell_contract.py -q
```

Expected: the new gutter, Chat watcher, and non-blocking navigation assertions fail against the current implementation.

Add `tests/frontend/test_saved_report_open_protocol_behavior.py` to execute the TypeScript protocol and prove that two identical targets produce two lightweight events.

### Task 2: Implement route-specific spacing and resilient notification navigation

**Files:**
- Modify: `frontend/src/views/Dashboard.vue`
- Modify: `frontend/src/views/Chat.vue`
- Modify: `frontend/src/views/EmbedChat.vue`
- Modify: `frontend/src/components/PortalNotificationBell.vue`
- Modify: `frontend/src/components/chatbi/DatasetPortalDrawer.vue`
- Modify: `frontend/src/components/chatbi/DatasetCapabilityMenu.vue`
- Create: `frontend/src/utils/savedReportOpenProtocol.ts`
- Create: `frontend/src/utils/savedReportFocus.ts`

- [ ] **Step 1: Give Dashboard an explicit spacing computation**

Add:

```ts
const dashboardContentSpacing = computed(() => {
  if (route.name === "AIChat") return "p-0";
  if (route.name === "PersonalCenter") return "px-3 sm:px-4";
  return "p-0 sm:p-4 md:p-8";
});
```

Bind the main content container with `:class="dashboardContentSpacing"` and leave the routed component key as `$route.path`.

- [ ] **Step 2: Send a dedicated saved-report request to an already-ready iframe**

Add `widgetReady`, set it when `NANZI_WIDGET_READY` arrives, listen for the host event, and send only the lightweight message:

```ts
const widgetReady = ref(false);
const sendSavedReportOpenRequest = (target: SavedReportOpenTarget) => {
  if (!widgetReady.value || !target.report_id || !chatFrame.value?.contentWindow) return;
  chatFrame.value.contentWindow.postMessage(createSavedReportOpenMessage(target), "*");
};
```

EmbedChat handles `OPEN_SAVED_REPORT` without invoking `initChat()`, deduplicates the same click by `request_id`, and updates a reactive focus request. DatasetPortalDrawer passes that request to DatasetCapabilityMenu, whose watcher opens the report detail, runs tab, and requested run even when the list and drawer are already mounted. PortalNotificationBell generates a new request id per click, so repeated clicks on an identical target remain observable.

- [ ] **Step 3: Decouple mark-read errors from navigation**

Resolve `notificationTarget` before calling the API, wrap only the mark-read operation in `try/catch`, update local unread state only on success, then close the panel and push the target regardless of that API result.

```ts
const notificationTarget = meta.report_id
  ? { path: "/dashboard/chat", query: { dataset_portal: "1", report_id: meta.report_id, run_id: item.resource_id || "" } }
  : null;

try {
  // mark unread notification as read
} catch (error) {
  console.warn("Failed to mark portal notification as read", error);
}

if (notificationTarget) {
  closeNotifications();
  await router.push(notificationTarget);
}
```

- [ ] **Step 4: Run the focused contracts and confirm GREEN**

Run:

```bash
venv/bin/python -m pytest tests/frontend/test_data_portal_home_contract.py tests/frontend/test_portal_notification_bell_contract.py -q
```

Expected: all focused tests pass.

### Task 3: Regression verification and handoff

**Files:**
- Verify only; do not start, build, or restart services.

- [ ] **Step 1: Run adjacent frontend contract tests**

Run:

```bash
venv/bin/python -m pytest tests/frontend/test_data_portal_home_contract.py tests/frontend/test_portal_notification_bell_contract.py tests/frontend/test_dataset_menu_loading_contract.py -q
```

Expected: all selected tests pass.

- [ ] **Step 2: Inspect the exact diff**

Run:

```bash
git diff -- frontend/src/views/Dashboard.vue frontend/src/views/Chat.vue frontend/src/components/PortalNotificationBell.vue tests/frontend/test_data_portal_home_contract.py tests/frontend/test_portal_notification_bell_contract.py
```

Expected: only the confirmed gutter, notification routing, and regression-test changes are present.

- [ ] **Step 3: Hand off without committing or restarting**

Report changed files, test evidence, and manual verification steps. Do not run `./dev.sh`, do not build the frontend, and do not commit unless the user explicitly requests it.
