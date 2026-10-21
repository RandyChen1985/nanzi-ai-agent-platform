# 个人技能点对点分享 实现计划

> **For agentic workers:** 按任务顺序执行；每个任务先写失败测试，再写实现，最后跑测试。提交由用户决定，本计划不执行 git commit。

**Goal:** 在「我的技能」中加入点对点分享：分享人发快照邀请，接收人接受后复制一份独立个人技能。

**Architecture:** 新增 `skill_shares` 表与 `skill_share_service`，分享时固化快照，接受时原子复制到接收人个人技能目录；前端在「我的技能」内改为左菜单（我的技能 / 收到的分享 / 我的分享）。

**Tech Stack:** FastAPI + SQLAlchemy 2.x async + Vue 3 + Tailwind；pytest（no_infrastructure 隔离测试）。

---

## Task 1：模型与迁移

**Files:**
- Create: `app/models/skill_share.py`
- Create: `db-prod/V179-create_skill_shares.sql`
- Create: `db-prod-pg/V80-create_skill_shares.sql`
- Test: `tests/services/test_skill_share_service.py::test_share_model_uses_stable_table_and_columns`

- [ ] 写模型表/列断言测试，运行确认失败（ModuleNotFoundError）
- [ ] 实现 `SkillShare` 模型
- [ ] 编写两套迁移 SQL（MySQL 用 COMMENT 内联，PostgreSQL 用 COMMENT ON）
- [ ] 运行测试通过

## Task 2：快照与落地文件操作

**Files:**
- Create: `app/services/skill_share_service.py`
- Modify: `app/services/skill_publication_service.py`（`_copy_tree_contents` → 公共 `copy_tree_contents`，保留旧名别名）
- Test: `tests/services/test_skill_share_service.py`

测试：`test_create_share_snapshot_copies_tree`、`test_create_share_snapshot_rejects_hidden_file`、`test_materialize_share_appends_suffix_on_conflict`、`test_materialize_share_cleans_staged_dir_on_failure`。

## Task 3：分享编排（发送 / 列表 / 撤回）

**Files:** `app/services/skill_share_service.py`、测试同上

测试：`test_share_skill_rejects_self`、`test_share_skill_rejects_unknown_or_disabled_target`、`test_share_skill_refreshes_existing_pending`、`test_withdraw_share_only_owner`、`test_list_sent_shares_status_counts`。

## Task 4：接收与落地（accept / reject）

测试：`test_accept_share_copies_and_marks_accepted`、`test_accept_share_conflict_suffix`、`test_accept_share_rejects_non_target`、`test_accept_share_with_missing_snapshot`、`test_reject_share_marks_rejected`。

## Task 5：API 端点

**Files:**
- Modify: `app/api/portal/endpoints/personal_skills.py`（路由必须注册在 `/{skill_id}` 之前）
- Test: `tests/api/portal/test_personal_skill_shares_endpoint.py`（`no_infrastructure`，用 patch 隔离 service 或直接测路由表）

端点：`GET /share-targets`、`POST /{skill_id}/shares`、`GET /shares/sent`、`DELETE /shares/sent/{share_id}`、`GET /shares/received`、`POST /shares/received/{share_id}/accept`、`POST /shares/received/{share_id}/reject`、`GET /shares/summary`。

## Task 6：个人技能列表补充分享字段

`GET /api/portal/skills/personal` 返回 `shared_from` 与 `share_count`；`Skill` 类型补字段。

## Task 7：前端左菜单与面板

**Files:**
- Create: `frontend/src/components/skill/SkillPersonalMenu.vue`
- Create: `frontend/src/components/skill/SkillShareDialog.vue`
- Create: `frontend/src/components/skill/ReceivedSharesPanel.vue`
- Create: `frontend/src/components/skill/SentSharesPanel.vue`
- Create: `frontend/src/utils/skillShare.ts`（状态映射常量）
- Modify: `frontend/src/views/SkillsManagement.vue`（工具行移入内容区、aside、分享按钮、深链）
- Modify: `frontend/src/views/PersonalCenter.vue`（`skills_tab` 深链）
- Test: `tests/frontend/test_skill_share_contract.py`

## Task 8：文档与验证

- 更新 `tests/CHECKLIST.md`
- `pytest tests/services/test_skill_share_service.py tests/frontend/test_skill_share_contract.py`
- `pytest --confcutdir=tests/frontend tests/frontend`
- `vue-tsc --noEmit`（在 frontend 目录）
