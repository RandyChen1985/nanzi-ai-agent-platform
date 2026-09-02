# MCP OAuth 默认 Redirect URI Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 允许创建 OAuth Authorization Code Client 时省略 Redirect URI，并统一补充 `https://localhost/oauth/callback`。

**Architecture:** 在后端请求模型的输入清洗边界统一处理空值，确保服务台和其他 API 调用方行为一致。OAuth 授权阶段继续使用已注册 URI 的精确匹配；前端仅同步说明默认行为。

**Tech Stack:** FastAPI/Pydantic 2、Vue 3、pytest 契约测试。

---

### Task 1: 为默认 URI 行为增加回归测试

**Files:**
- Modify: `tests/api/test_mcp_service_desk_contract.py`
- Modify: `tests/frontend/test_frontend_mcp_service_desk_contract.py`

- [x] **Step 1: 将后端空 URI 测试改为期望默认地址**

在现有 `test_client_registration_requires_user_authorization_code` 中保留不支持授权模式的异常断言，将第二段空 `redirect_uris=[]` 的异常断言改为创建模型并断言 `redirect_uris == ["https://localhost/oauth/callback"]`。

- [x] **Step 2: 增加前端默认行为契约断言**

在前端契约测试中断言页面源码包含默认 URI 和“未填写/默认回调”提示文本。

- [x] **Step 3: 运行测试确认 RED**

运行：

```bash
PYTHONPATH=. .venv/bin/pytest tests/api/test_mcp_service_desk_contract.py::test_client_registration_requires_user_authorization_code -q
pytest --confcutdir=tests/frontend tests/frontend/test_frontend_mcp_service_desk_contract.py -q
```

预期：后端测试因当前仍抛出缺少 `redirect_uri` 的 `ValueError` 失败；前端新断言因默认 URI/提示尚未存在而失败。

### Task 2: 实现后端默认 URI

**Files:**
- Modify: `app/api/portal/endpoints/mcp_service.py:124-130`

- [x] **Step 1: 在 `validate_redirect_uris` 清洗后补默认地址**

保持去空白、去重和通配符校验；当 `cleaned` 为空时返回 `["https://localhost/oauth/callback"]`，让后续 `validate_grant_requirements` 继续通过。

- [x] **Step 2: 运行后端回归测试确认 GREEN**

运行：

```bash
PYTHONPATH=. .venv/bin/pytest tests/api/test_mcp_service_desk_contract.py::test_client_registration_requires_user_authorization_code -q
```

预期：PASS。

### Task 3: 同步前端提示并完成验证

**Files:**
- Modify: `frontend/src/views/McpServiceDesk.vue:992`

- [x] **Step 1: 更新字段说明**

保留输入框可选，将说明改为明确告知空值会注册默认地址 `https://localhost/oauth/callback`，并说明真实 OAuth 回调仍需配置业务系统实际地址。

- [x] **Step 2: 运行相关测试和静态检查**

运行：

```bash
PYTHONPATH=. .venv/bin/pytest tests/api/test_mcp_service_desk_contract.py -q
pytest --confcutdir=tests/frontend tests/frontend/test_frontend_mcp_service_desk_contract.py -q
git diff --check
```

预期：相关测试全部 PASS，`git diff --check` 无输出。真实浏览器 OAuth、服务启动和部署验收不在本次范围内。
