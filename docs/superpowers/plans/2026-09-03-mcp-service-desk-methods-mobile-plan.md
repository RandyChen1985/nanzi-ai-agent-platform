# MCP 服务台能力与 Scope 移动端优化 Implementation Plan

> **For agentic workers:** Execute this plan inline with focused checkpoints; do not commit unless explicitly requested.

**Goal:** 让 MCP 服务台“能力与 Scope”在手机窄屏上以可读的能力卡片展示，同时保留桌面端完整表格。

**Architecture:** 在同一个 Vue 视图中维护两套语义一致的展示层：`md` 及以上显示表格，小于 `md` 显示卡片；tab 导航在小屏使用横向滚动且不换行。数据来源、权限判断和方法字段不变，只调整响应式呈现。

**Tech Stack:** Vue 3、TypeScript、Tailwind CSS 3、pytest 前端源代码契约测试、vue-tsc。

---

### Task 1: 锁定移动端展示契约

**Files:**
- Create: `tests/frontend/test_mcp_service_desk_methods_mobile_contract.py`
- Test: `frontend/src/views/McpServiceDesk.vue`

- [ ] **Step 1: 写失败的契约测试**

```python
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
pytestmark = pytest.mark.no_infrastructure


def _source() -> str:
    return (ROOT / "frontend/src/views/McpServiceDesk.vue").read_text(encoding="utf-8")


def test_methods_tab_has_responsive_table_and_mobile_cards():
    source = _source()
    methods_section = source.split("activeTab === 'methods'", 1)[1].split("activeTab === 'audit'", 1)[0]

    assert "md:block" in methods_section
    assert "md:hidden" in methods_section
    assert "method.name" in methods_section
    assert "method.scope" in methods_section
    assert "method.capability_group" in methods_section
    assert "必须用户授权" in methods_section
    assert "待接入" in methods_section


def test_service_desk_tabs_stay_single_line_and_scroll_on_mobile():
    source = _source()
    tab_bar = source.split("availableTabs.length", 1)[1].split("activeTab === 'guide'", 1)[0]

    assert "overflow-x-auto" in tab_bar
    assert "flex-nowrap" in tab_bar
    assert "whitespace-nowrap" in tab_bar
    assert "shrink-0" in tab_bar
```

- [ ] **Step 2: 运行定向测试确认当前实现不满足契约**

Run: `pytest --confcutdir=tests/frontend tests/frontend/test_mcp_service_desk_methods_mobile_contract.py -q`

Expected: FAIL because the current methods tab only contains one horizontally scrollable table and the tab bar has no explicit mobile overflow/non-wrapping contract.

### Task 2: 实现响应式能力展示

**Files:**
- Modify: `frontend/src/views/McpServiceDesk.vue:803-805,1122`

- [ ] **Step 1: 固定 tab 导航的移动端布局**

将 tab 容器改为 `flex-nowrap overflow-x-auto`，每个按钮增加 `shrink-0 whitespace-nowrap`，确保 tab 文本不折行且只在 tab 行内横向滚动。

- [ ] **Step 2: 增加桌面表格和移动卡片两套展示层**

保留现有五列字段与状态判断，将表格包裹在 `hidden md:block` 容器中；新增 `md:hidden` 卡片列表，每张卡片按“方法名 → Scope / 能力组标签 → 身份权限模式 / 状态”顺序展示，并复用 `method.name`、`method.scope`、`method.capability_group`、`method.implemented`、`method.enabled`。

- [ ] **Step 3: 运行契约测试确认实现通过**

Run: `pytest --confcutdir=tests/frontend tests/frontend/test_mcp_service_desk_methods_mobile_contract.py -q`

Expected: PASS.

### Task 3: 做静态回归验证

**Files:**
- Test: `frontend/src/views/McpServiceDesk.vue`

- [ ] **Step 1: 运行前端类型检查**

Run: `./node_modules/.bin/vue-tsc --noEmit` from `frontend`.

Expected: PASS with no new TypeScript/Vue template errors.

- [ ] **Step 2: 检查差异格式与工作区范围**

Run: `git diff --check` and `git status --short`

Expected: no whitespace errors; only the plan, focused contract test, and `McpServiceDesk.vue` are changed by this task.

- [ ] **Step 3: 记录验证边界**

报告静态契约与类型检查结果；不声称已完成真实登录、接口数据加载、浏览器真机、服务启动或部署验收，因为项目约定这些由用户在控制台执行。
