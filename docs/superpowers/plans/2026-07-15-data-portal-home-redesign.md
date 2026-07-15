# 数据门户首页重组 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 新增完整的“我的数据首页”，将真实报表运行、智能简报、最近分析和我的报表前置，同时复用现有推荐场景与数据目录。

**Architecture:** 后端提供一个按当前用户权限聚合的轻量首页接口，场景目录继续走现有独立接口以避免模型生成阻塞首屏。前端新增独立路由和小型分区组件，保留聊天抽屉并提供跳转完整页面的入口，不复制 ChatBI 或黄金报表执行逻辑。

**Tech Stack:** FastAPI、SQLAlchemy Async、Pydantic、pytest、Vue 3 Composition API、TypeScript、Tailwind CSS、Vue Router。

---

## 文件结构

- Create `app/services/data_portal_home_service.py`: 仅负责当前用户首页统计、最近活动和报表摘要聚合。
- Create `app/api/portal/endpoints/data_portal.py`: 首页响应模型和 HTTP 入口。
- Modify `app/api/portal/api.py`: 注册 `/data-portal` 路由。
- Create `tests/services/test_data_portal_home_service.py`: 聚合、日期边界、权限可见性测试。
- Create `tests/api/portal/test_data_portal_home_api.py`: API 认证和稳定契约测试。
- Create `frontend/src/types/dataPortal.ts`: 首页稳定类型。
- Create `frontend/src/composables/useDataPortalHome.ts`: 首页与场景的独立加载、刷新及局部失败状态。
- Create `frontend/src/views/DataPortalHome.vue`: 页面容器、桌面/移动导航和路由状态。
- Create `frontend/src/components/data-portal/DataPortalOverview.vue`: 今日关注、最近分析。
- Create `frontend/src/components/data-portal/DataPortalReportSection.vue`: 报表摘要和筛选。
- Create `frontend/src/components/data-portal/DataPortalSceneSection.vue`: 复用场景目录。
- Create `frontend/src/components/data-portal/DataPortalCatalogSection.vue`: 授权数据目录摘要。
- Modify `frontend/src/router/index.ts`: 注册 `/dashboard/data-portal`。
- Modify `frontend/src/components/chatbi/DatasetPortalDrawer.vue`: 增加打开完整页面事件。
- Modify `frontend/src/views/EmbedChat.vue` and `frontend/src/views/AgentDebug.vue`: 接收抽屉事件并路由。
- Create `tests/frontend/test_data_portal_home_contract.py`: 路由、组件复用、移动导航和跳转契约测试。

### Task 1: 后端首页聚合服务

**Files:**
- Create: `tests/services/test_data_portal_home_service.py`
- Create: `app/services/data_portal_home_service.py`

- [x] **Step 1: 写失败测试**

测试固定 `now` 下只统计当前用户今日的失败运行、成功简报和 active 订阅，并验证最近活动按时间倒序、最多 6 条。构造用户本人报表、角色共享报表和无权限报表，断言无权限报表不会进入结果。

```python
result = await DataPortalHomeService.build(db, user_id=7, role_ids=[3], now=fixed_now)
assert result["attention"]["failed_runs_today"] == 1
assert result["attention"]["digests_today"] == 2
assert result["attention"]["active_subscriptions"] == 3
assert all(item["report_id"] != "hidden" for item in result["recent_analysis"])
assert len(result["recent_analysis"]) <= 6
```

- [x] **Step 2: 运行测试确认失败**

Run: `REDIS_ENABLE=false PYTHONPATH=. venv/bin/python -m pytest tests/services/test_data_portal_home_service.py -q`

Expected: FAIL，提示 `app.services.data_portal_home_service` 不存在。

- [x] **Step 3: 实现最小聚合服务**

实现 `DataPortalHomeService.build()`、`_visible_report_condition()`、`_day_bounds()` 和 ISO 序列化。查询必须以 `owner_user_id == user_id`、用户分享或角色分享为可见条件；共享用户只读取自己产生的运行记录。首页报表项包含 `id/title/owner_name/is_owner/is_favorite/pinned/last_run_at/last_error/subscription_status`。

- [x] **Step 4: 运行测试确认通过**

Run: `REDIS_ENABLE=false PYTHONPATH=. venv/bin/python -m pytest tests/services/test_data_portal_home_service.py -q`

Expected: PASS。

### Task 2: 首页 API 契约

**Files:**
- Create: `tests/api/portal/test_data_portal_home_api.py`
- Create: `app/api/portal/endpoints/data_portal.py`
- Modify: `app/api/portal/api.py`

- [x] **Step 1: 写失败 API 测试**

覆盖认证用户 ID 传递、角色 ID 加载、响应含 `attention/recent_analysis/report_summary/generated_at`，并断言服务异常返回 500 而不是伪造零值。

```python
response = client.get("/api/portal/data-portal/home", headers=auth_headers)
assert response.status_code == 200
assert set(response.json()["data"]) == {
    "attention", "recent_analysis", "report_summary", "generated_at"
}
```

- [x] **Step 2: 运行测试确认失败**

Run: `REDIS_ENABLE=false PYTHONPATH=. venv/bin/python -m pytest tests/api/portal/test_data_portal_home_api.py -q`

Expected: FAIL，路由返回 404。

- [x] **Step 3: 注册接口**

新增 `GET /api/portal/data-portal/home`，复用黄金报表 `_get_user_role_ids()`，调用聚合服务并返回 `StandardResponse(data=payload)`；在 `portal/api.py` 注册 router。

- [x] **Step 4: 运行 API 与服务测试**

Run: `REDIS_ENABLE=false PYTHONPATH=. venv/bin/python -m pytest tests/services/test_data_portal_home_service.py tests/api/portal/test_data_portal_home_api.py -q`

Expected: PASS。

### Task 3: 前端数据类型与加载状态

**Files:**
- Create: `frontend/src/types/dataPortal.ts`
- Create: `frontend/src/composables/useDataPortalHome.ts`
- Create: `tests/frontend/test_data_portal_home_contract.py`

- [x] **Step 1: 写失败契约测试**

断言 composable 请求 `/api/portal/data-portal/home`，并行调用现有 `/api/v1/chat/dataset-menu`，分别维护 `homeError` 与 `sceneError`，刷新失败不清空已有 payload。

- [x] **Step 2: 运行测试确认失败**

Run: `venv/bin/python -m pytest tests/frontend/test_data_portal_home_contract.py -q`

Expected: FAIL，目标文件不存在。

- [x] **Step 3: 实现类型和 composable**

`useDataPortalHome()` 暴露 `homePayload/scenePayload/homeLoading/sceneLoading/homeError/sceneError/load/refresh`。`load()` 使用 `Promise.allSettled`；刷新只有成功时替换对应旧值。

- [x] **Step 4: 运行契约测试**

Run: `venv/bin/python -m pytest tests/frontend/test_data_portal_home_contract.py -q`

Expected: 类型和加载契约相关断言 PASS。

### Task 4: 首页视觉分区组件

**Files:**
- Create: `frontend/src/components/data-portal/DataPortalOverview.vue`
- Create: `frontend/src/components/data-portal/DataPortalReportSection.vue`
- Create: `frontend/src/components/data-portal/DataPortalSceneSection.vue`
- Create: `frontend/src/components/data-portal/DataPortalCatalogSection.vue`
- Modify: `tests/frontend/test_data_portal_home_contract.py`

- [x] **Step 1: 扩展失败契约测试**

断言首页只在真实数量非零或存在可行动空态时展示统计；最近分析动作发出 `open-report/open-digest/continue-analysis`；报表筛选包含 `subscribed/pinned/favorite/shared/recent`；场景组件复用 `DatasetCapabilityMenu` 的场景数据而不请求第二套接口。

- [x] **Step 2: 运行测试确认失败**

Run: `venv/bin/python -m pytest tests/frontend/test_data_portal_home_contract.py -q`

Expected: FAIL，四个组件不存在。

- [x] **Step 3: 实现四个组件**

组件只接受 props 并发出动作事件。`DataPortalOverview` 不生成假异常；`DataPortalReportSection` 使用宽屏三列、移动单列；`DataPortalSceneSection` 复用现有 payload 与 quick-question 事件；`DataPortalCatalogSection` 从 `related_data` 确定性汇总数据集、表和字段入口。

- [x] **Step 4: 运行契约测试**

Run: `venv/bin/python -m pytest tests/frontend/test_data_portal_home_contract.py -q`

Expected: PASS。

### Task 5: 完整页面、路由与动作跳转

**Files:**
- Create: `frontend/src/views/DataPortalHome.vue`
- Modify: `frontend/src/router/index.ts`
- Modify: `tests/frontend/test_data_portal_home_contract.py`

- [x] **Step 1: 写失败路由测试**

断言 `/dashboard/data-portal` 使用 `menu:ai_chat` 权限；页面具有 `home/reports/scenes/catalog` 四个 section，移动端底部导航；`report_id/run_id/conversation_id` 使用 query 参数传入现有 ChatBI 跳转。

- [x] **Step 2: 运行测试确认失败**

Run: `venv/bin/python -m pytest tests/frontend/test_data_portal_home_contract.py -q`

Expected: FAIL，页面和路由不存在。

- [x] **Step 3: 实现页面**

页面调用 `useDataPortalHome()`，桌面端渲染左侧导航，移动端渲染底部导航；首页组合四个分区。报表详情仍复用 `DatasetCapabilityMenu` 已有详情能力，继续分析路由到 `/dashboard/chat?dataset_portal=1&report_id=...&run_id=...`。

- [x] **Step 4: 运行契约测试**

Run: `venv/bin/python -m pytest tests/frontend/test_data_portal_home_contract.py -q`

Expected: PASS。

### Task 6: 抽屉进入完整页面

**Files:**
- Modify: `frontend/src/components/chatbi/DatasetPortalDrawer.vue`
- Modify: `frontend/src/views/EmbedChat.vue`
- Modify: `frontend/src/views/AgentDebug.vue`
- Modify: `tests/frontend/test_data_portal_home_contract.py`

- [x] **Step 1: 写失败集成契约测试**

断言抽屉头部存在“完整页面”按钮并发出 `open-full-page`；EmbedChat 和 AgentDebug 接收事件并路由 `/dashboard/data-portal`，嵌入式公开页使用当前窗口路由而不依赖 Dashboard 侧栏。

- [x] **Step 2: 运行测试确认失败**

Run: `venv/bin/python -m pytest tests/frontend/test_data_portal_home_contract.py -q`

Expected: FAIL，事件尚不存在。

- [x] **Step 3: 实现入口与跳转**

抽屉仅增加图标按钮和可访问名称，不改变关闭、钉住、移动端滚动与快捷提问行为。两个宿主统一实现 `openFullDataPortal()`。

- [x] **Step 4: 运行前端门户契约回归**

Run: `venv/bin/python -m pytest tests/frontend/test_data_portal_home_contract.py tests/frontend/test_dataset_menu_loading_contract.py -q`

Expected: PASS。

### Task 7: 综合验证与文档记录

**Files:**
- Modify: `tests/CHECKLIST.md`

- [x] **Step 1: 运行后端聚焦测试**

Run: `REDIS_ENABLE=false PYTHONPATH=. venv/bin/python -m pytest tests/services/test_data_portal_home_service.py tests/api/portal/test_data_portal_home_api.py tests/api/portal/test_saved_report_subscriptions.py -q`

Expected: PASS。

- [x] **Step 2: 运行前端契约测试**

Run: `venv/bin/python -m pytest tests/frontend/test_data_portal_home_contract.py tests/frontend/test_dataset_menu_loading_contract.py tests/frontend/test_saved_report_subscription_ui_contract.py -q`

Expected: PASS。

- [x] **Step 3: 运行静态校验**

Run: `venv/bin/python -m py_compile app/services/data_portal_home_service.py app/api/portal/endpoints/data_portal.py`

Expected: 无输出并退出 0。

Run: `git diff --check`

Expected: 无输出并退出 0。

- [x] **Step 4: 更新验收清单**

在 `tests/CHECKLIST.md` 记录完整页面、聚合接口、权限隔离、抽屉入口、移动端导航及实际通过的测试数量。不得记录未运行的构建或服务启动结果。

- [x] **Step 5: 检查工作区边界**

Run: `git status --short`

Expected: 本功能文件与用户已有 `frontend/src/views/AgentManagement.vue` 修改清晰分离；不运行 `./dev.sh`，不自动提交实现代码。
