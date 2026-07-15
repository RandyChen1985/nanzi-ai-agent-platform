# Mobile Saved Report Digest Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Replace saved-report success reminders with mobile-first data digests containing deterministic results, optional grounded AI analysis, channel-specific Markdown, and auditable delivery records.

**Architecture:** Add a focused digest service that normalizes snapshots, builds deterministic mobile summaries, optionally enriches them through the existing platform LLM, and renders DingTalk/WeCom-safe Markdown. Persist digest and per-channel delivery audit separately from run snapshots, then let the scheduler orchestrate generation and delivery without changing report success state when AI or a channel fails.

**Tech Stack:** FastAPI, SQLAlchemy async ORM, MySQL JSON/TEXT, AgentScope-compatible platform LLM client, Vue 3, pytest source and unit contracts.

---

### Task 1: Persist digest settings and delivery audit

**Files:**
- Create: `db-prod/V98-create-saved-report-digest-deliveries.sql`
- Modify: `app/models/saved_report.py`
- Modify: `app/api/portal/endpoints/saved_reports.py`
- Test: `tests/test_saved_report_digest_migration_contract.py`

- [ ] **Step 1: Write the failing migration/model contract tests**

Assert V98 adds `ai_analysis_enabled` to subscriptions and creates `portal_saved_report_digest_deliveries` with `run_id`, `subscription_id`, `channel`, `digest_payload`, `title`, `content`, `status`, `error_message`, `ai_status`, and timestamps, all with Chinese comments.

- [ ] **Step 2: Run the contract test and verify RED**

Run: `venv/bin/python -m pytest tests/test_saved_report_digest_migration_contract.py -q`

Expected: FAIL because V98 and ORM fields do not exist.

- [ ] **Step 3: Add the migration and ORM model**

Use a nullable-safe, idempotent migration that defaults `ai_analysis_enabled` to `1`. Add `PortalSavedReportDigestDelivery` with JSON payload and one row per attempted channel delivery.

- [ ] **Step 4: Extend subscription request and response compatibility**

Add `ai_analysis_enabled: bool = True` to `SavedReportSubscriptionRequest`, store it on PUT, and return it from `_saved_report_subscription_data`.

- [ ] **Step 5: Run the contract tests and verify GREEN**

Run: `venv/bin/python -m pytest tests/test_saved_report_digest_migration_contract.py tests/frontend/test_saved_report_subscription_contract.py -q`

Expected: PASS.

### Task 2: Build deterministic mobile digest and channel renderers

**Files:**
- Create: `app/services/saved_report_digest_service.py`
- Create: `tests/services/test_saved_report_digest_service.py`

- [ ] **Step 1: Write failing tests for normalization and mobile limits**

Cover dict rows and column-array rows; assert no more than five records and five fields per record, compact numeric formatting, empty-result wording, and absence of Markdown table pipes.

- [ ] **Step 2: Run tests and verify RED**

Run: `venv/bin/python -m pytest tests/services/test_saved_report_digest_service.py -q`

Expected: FAIL because the digest service is missing.

- [ ] **Step 3: Implement deterministic digest generation**

Expose `build_deterministic_digest(report, run, params) -> dict` returning `title`, `scope`, `key_findings`, `records`, `analysis`, `risk_note`, and `generation_mode="fallback"`. Only claim aggregates supported directly by the snapshot.

- [ ] **Step 4: Implement mobile channel rendering**

Expose `render_mobile_markdown(digest, report_url, channel) -> tuple[str, str]`. Render vertical numbered records, short sections, paragraph-safe truncation, and a final complete-report link; never emit a Markdown table.

- [ ] **Step 5: Run tests and verify GREEN**

Run: `venv/bin/python -m pytest tests/services/test_saved_report_digest_service.py -q`

Expected: PASS.

### Task 3: Add grounded optional AI enrichment with fallback

**Files:**
- Modify: `app/services/saved_report_digest_service.py`
- Modify: `tests/services/test_saved_report_digest_service.py`

- [ ] **Step 1: Write failing AI success and failure tests**

Inject a fake generator returning JSON and assert validated findings replace fallback findings. Make the generator raise and assert the deterministic digest remains deliverable with `ai_status="fallback"`.

- [ ] **Step 2: Run tests and verify RED**

Run: `venv/bin/python -m pytest tests/services/test_saved_report_digest_service.py -q`

Expected: FAIL because AI enrichment is absent.

- [ ] **Step 3: Implement constrained AI enrichment**

Use `get_llm_async(streaming=False, temperature=0.1)` plus `chat_client_from_handle(...).generate_text(...)`. Send only report metadata, params, deterministic statistics, and at most ten snapshot rows. Parse fenced or plain JSON and validate list counts and per-item lengths.

- [ ] **Step 4: Implement safe fallback**

Catch model absence, timeout, invalid JSON and validation errors; log only the failure category and return the deterministic digest without raising.

- [ ] **Step 5: Run tests and verify GREEN**

Run: `venv/bin/python -m pytest tests/services/test_saved_report_digest_service.py -q`

Expected: PASS.

### Task 4: Orchestrate digest delivery and audit in the scheduler

**Files:**
- Modify: `app/services/ai/scheduler_service.py`
- Create: `tests/services/test_saved_report_digest_scheduler.py`

- [ ] **Step 1: Write failing scheduler tests**

Assert a successful subscribed run builds a digest and sends rendered content instead of the old row-count reminder; AI/channel failure must not change the run status; every attempted channel creates an audit row.

- [ ] **Step 2: Run tests and verify RED**

Run: `venv/bin/python -m pytest tests/services/test_saved_report_digest_scheduler.py -q`

Expected: FAIL because the wrapper still sends `本次查询返回 N 行数据`.

- [ ] **Step 3: Replace success reminder orchestration**

After loading the latest successful run, call the digest service, render one body per selected channel plus station inbox, send each channel, and add a delivery audit row for success or error. Preserve failure notification throttling.

- [ ] **Step 4: Keep manual and scheduled behavior consistent**

Manual subscription runs continue to honor `notify_on_success`; both trigger labels appear as metadata in the digest, not as the primary message.

- [ ] **Step 5: Run scheduler and existing notification tests**

Run: `venv/bin/python -m pytest tests/services/test_saved_report_digest_scheduler.py tests/frontend/test_portal_notification_bell_contract.py tests/services/test_saved_report_subscription_service.py -q`

Expected: PASS.

### Task 5: Expose actual delivery content in run history

**Files:**
- Modify: `app/api/portal/endpoints/saved_reports.py`
- Modify: `frontend/src/components/chatbi/DatasetCapabilityMenu.vue`
- Modify: `frontend/src/views/EmbedChat.vue`
- Modify: `frontend/src/views/AgentDebug.vue`
- Modify: `tests/api/portal/test_saved_reports.py`
- Modify: `tests/frontend/test_saved_report_run_history_contract.py`

- [ ] **Step 1: Write failing API and frontend contracts**

Assert run detail includes `deliveries` with channel, status, title, content, AI status and sent time. Assert all three report-detail surfaces render a “推送内容” section with channel and send-state badges.

- [ ] **Step 2: Run tests and verify RED**

Run: `venv/bin/python -m pytest tests/api/portal/test_saved_reports.py tests/frontend/test_saved_report_run_history_contract.py -q`

Expected: FAIL because run details do not expose deliveries.

- [ ] **Step 3: Load delivery audit with run detail**

Query delivery rows only after the existing report/run permission check and serialize safe fields; do not expose Webhooks or configuration.

- [ ] **Step 4: Render delivery audit in all report-detail surfaces**

Below the result snapshot, render the final mobile Markdown in a wrapping text block, channel badge, success/failure badge, AI/fallback badge, and error text where present.

- [ ] **Step 5: Run API/frontend contracts and verify GREEN**

Run: `venv/bin/python -m pytest tests/api/portal/test_saved_reports.py tests/frontend/test_saved_report_run_history_contract.py -q`

Expected: PASS.

### Task 6: Add subscription UI control and regression verification

**Files:**
- Modify: `frontend/src/components/chatbi/DatasetCapabilityMenu.vue`
- Modify: `frontend/src/views/EmbedChat.vue`
- Modify: `frontend/src/views/AgentDebug.vue`
- Modify: `tests/frontend/test_saved_report_subscription_contract.py`
- Modify: `tests/CHECKLIST.md`

- [ ] **Step 1: Write failing UI contract**

Assert the subscription form carries `ai_analysis_enabled`, defaults it to true, and labels successful notification as “发送报表简报” rather than “成功通知”.

- [ ] **Step 2: Run and verify RED**

Run: `venv/bin/python -m pytest tests/frontend/test_saved_report_subscription_contract.py -q`

Expected: FAIL because the control is missing.

- [ ] **Step 3: Add the compatible controls across all report surfaces**

Add an enabled-by-default “AI 智能分析” switch and clarify that disabling it still sends a data摘要. Rename the success option to “运行成功后发送报表简报”.

- [ ] **Step 4: Run targeted regression tests**

Run: `venv/bin/python -m pytest tests/test_saved_report_digest_migration_contract.py tests/services/test_saved_report_digest_service.py tests/services/test_saved_report_digest_scheduler.py tests/api/portal/test_saved_reports.py tests/frontend/test_saved_report_run_history_contract.py tests/frontend/test_saved_report_subscription_contract.py tests/frontend/test_portal_notification_bell_contract.py tests/api/test_task_center_saved_report_contract.py -q`

Expected: PASS.

- [ ] **Step 5: Run static repository validation**

Run: `venv/bin/python -m py_compile app/models/saved_report.py app/services/saved_report_digest_service.py app/services/ai/scheduler_service.py app/api/portal/endpoints/saved_reports.py`

Run: `git diff --check`

Expected: both commands exit 0. Do not run `./dev.sh`, frontend builds, service starts, git add, or git commit.

