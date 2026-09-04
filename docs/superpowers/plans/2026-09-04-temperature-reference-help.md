# 模型温度参考弹框 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 在模型管理的温度配置旁提供可关闭的供应商参考弹框，帮助用户理解 OpenAI、DeepSeek、GLM、Kimi 和 Qwen 的温度范围与参考值。

**Architecture:** 将供应商说明、范围、参考值、场景和官方文档链接作为前端只读数据集中在温度参考工具中。`ModelRegistry.vue` 负责弹框开关、当前供应商高亮和展示；不新增后端接口，也不改变温度保存/调用逻辑。

**Tech Stack:** Vue 3、TypeScript、Tailwind CSS、pytest 前端源码契约测试。

---

### Task 1: 建立供应商参考数据和页面契约

**Files:**
- Modify: `tests/frontend/test_model_thinking_config_contract.py`
- Create: `frontend/src/utils/temperatureReference.ts`

- [ ] **Step 1: Write the failing test**

新增契约测试，要求模型温度区域存在帮助入口、弹框开关、五家供应商名称和官方文档链接，并要求数据文件包含范围/参考字段。

```python
def test_model_temperature_has_provider_reference_help():
    source = MODEL_REGISTRY.read_text(encoding="utf-8")
    reference_path = ROOT / "frontend/src/utils/temperatureReference.ts"

    assert "showTemperatureGuide" in source
    assert "温度参考" in source
    assert "temperatureReference" in source
    assert reference_path.exists()
    reference = reference_path.read_text(encoding="utf-8")
    for provider in ("OpenAI", "DeepSeek", "GLM", "Kimi", "Qwen"):
        assert provider in reference
    for field in ("range", "recommendation", "scenarios", "officialUrl"):
        assert field in reference
```

- [ ] **Step 2: Run the test to verify it fails**

```bash
PYTHONPATH=. .venv/bin/pytest --confcutdir=tests/frontend -q tests/frontend/test_model_thinking_config_contract.py::test_model_temperature_has_provider_reference_help
```

Expected: FAIL because the help state, modal and data file do not yet exist.

### Task 2: 实现供应商温度参考数据

**Files:**
- Create: `frontend/src/utils/temperatureReference.ts`

- [ ] **Step 1: Add static reference data**

使用带有 `provider`、`range`、`recommendation`、`scenarios` 和 `officialUrl` 的只读类型，明确区分“官方范围/限制”和“官方示例/参考”，避免把单一温度值宣传成所有模型的统一标准。Kimi 记录模型固定温度限制，GLM 记录官方 `0～1` 范围。

- [ ] **Step 2: Run the test to verify it still fails on the page implementation**

运行 Task 1 的命令。Expected: FAIL only because `ModelRegistry.vue` has not yet wired the reference data or modal.

### Task 3: 在模型温度控件旁接入帮助弹框

**Files:**
- Modify: `frontend/src/components/system/ModelRegistry.vue`

- [ ] **Step 1: Add the modal state and current-provider selection**

新增 `showTemperatureGuide` 状态，导入 `temperatureReference` 和类型；根据 `modelForm.provider` 找到当前供应商，找不到时显示“其他兼容服务”说明。

- [ ] **Step 2: Add an accessible `?` button**

在“模型温度”标题旁加入带 `aria-label="查看温度参考"` 的按钮，点击切换弹框；按钮不触发表单提交。

- [ ] **Step 3: Render the modal**

新增固定层弹框，包含：温度越低/越高的通俗解释、当前供应商高亮、五家供应商的范围/参考值/场景、官方文档链接，以及“不同模型可能存在差异，建议以具体模型官方文档为准”的说明。支持点击遮罩关闭和显式关闭按钮，不修改温度值。

- [ ] **Step 4: Run the focused contract tests**

```bash
PYTHONPATH=. .venv/bin/pytest --confcutdir=tests/frontend -q tests/frontend/test_model_thinking_config_contract.py::test_model_temperature_has_provider_reference_help tests/frontend/test_model_thinking_config_contract.py::test_temperature_controls_allow_two_and_warn_above_one
```

Expected: PASS。

### Task 4: 做类型和回归检查

**Files:**
- No additional files.

- [ ] **Step 1: Run the frontend type check**

```bash
./node_modules/.bin/vue-tsc --noEmit
```

Expected: PASS，无输出。

- [ ] **Step 2: Run the relevant frontend contract file**

```bash
PYTHONPATH=. .venv/bin/pytest --confcutdir=tests/frontend -q tests/frontend/test_model_thinking_config_contract.py
```

Expected: 本次相关测试通过；与温度弹框无关的既有失败单独记录，不修改无关文件。

- [ ] **Step 3: Check formatting and worktree scope**

```bash
git diff --check
git status --short
```

Expected: 无 whitespace 错误；不自动 stage 或 commit。
