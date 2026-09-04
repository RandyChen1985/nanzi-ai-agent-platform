# 温度通俗说明 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 在所有模型/智能体温度滑块下方，用当前温度值展示小白可理解的回答风格说明，并保留大于 1 的模型兼容性提醒。

**Architecture:** 新增一个无副作用的前端工具函数，根据温度值返回稳定、均衡、灵活、高创造性四档说明。模型管理、智能体版本抽屉和引导创建页面统一调用该函数，避免三处文案漂移；温度范围和后端接口保持现有 0～2 行为不变。

**Tech Stack:** Vue 3、TypeScript、Tailwind CSS、pytest 前端源码契约测试。

---

### Task 1: 为温度说明建立前端契约

**Files:**
- Modify: `tests/frontend/test_model_thinking_config_contract.py`
- Create: `frontend/src/utils/temperatureGuidance.ts`

- [ ] **Step 1: Write the failing test**

在 `test_temperature_controls_allow_two_and_warn_above_one` 后增加契约测试，要求三个页面引用统一工具，并包含四档面向用户的说明：

```python
def test_temperature_controls_explain_effects_in_plain_language():
    registry = MODEL_REGISTRY.read_text(encoding="utf-8")
    drawer = (ROOT / "frontend/src/components/agent/AgentVersionEditorDrawer.vue").read_text(encoding="utf-8")
    management = AGENT_MANAGEMENT.read_text(encoding="utf-8")
    guidance = (ROOT / "frontend/src/utils/temperatureGuidance.ts").read_text(encoding="utf-8")

    for source in (registry, drawer, management):
        assert "getTemperatureGuidance" in source
    for phrase in ("更稳定严谨", "适合日常对话", "表达变化更多", "可能偏离主题"):
        assert phrase in guidance
```

- [ ] **Step 2: Run test to verify it fails**

```bash
PYTHONPATH=. .venv/bin/pytest --confcutdir=tests/frontend -q tests/frontend/test_model_thinking_config_contract.py::test_temperature_controls_explain_effects_in_plain_language
```

Expected: FAIL，因为工具文件和页面引用尚不存在。

### Task 2: 实现统一的温度说明函数

**Files:**
- Create: `frontend/src/utils/temperatureGuidance.ts`

- [ ] **Step 1: Write minimal implementation**

新增纯函数，非法值回退为中间档，边界按 `<=0.3`、`<=0.8`、`<=1`、`>1` 处理：

```ts
export const getTemperatureGuidance = (value: unknown): string => {
  const temperature = Number(value)
  if (!Number.isFinite(temperature)) {
    return '准确性和表达多样性较均衡，适合日常对话。'
  }
  if (temperature <= 0.3) {
    return '回答更稳定严谨，适合查数、代码和规则问答。'
  }
  if (temperature <= 0.8) {
    return '准确性和表达多样性较均衡，适合日常对话。'
  }
  if (temperature <= 1) {
    return '回答更灵活，表达变化更多，但稳定性会有所降低。'
  }
  return '创造性和随机性更强，但可能偏离主题；请先确认模型官方文档支持。'
}
```

- [ ] **Step 2: Run the contract test to verify it still fails on missing page references**

运行 Task 1 的命令。Expected: FAIL only because the three Vue pages have not imported or called `getTemperatureGuidance`.

### Task 3: 将说明展示到三个温度控件下方

**Files:**
- Modify: `frontend/src/components/system/ModelRegistry.vue`
- Modify: `frontend/src/components/agent/AgentVersionEditorDrawer.vue`
- Modify: `frontend/src/views/AgentManagement.vue`

- [ ] **Step 1: Add imports**

三个 `<script setup>` 分别引入工具；`ModelRegistry.vue` 和抽屉使用 `../../utils/temperatureGuidance`，`AgentManagement.vue` 使用 `../utils/temperatureGuidance`。

- [ ] **Step 2: Add dynamic helper text below each slider**

模型管理滑块后加入：

```vue
<p class="mt-2 text-[11px] leading-4 text-gray-500">
  当前 {{ normalizeTemperature(modelForm.temperature).toFixed(2) }}：{{ getTemperatureGuidance(modelForm.temperature) }}
</p>
```

智能体版本抽屉的编排温度、合成温度和引导创建版本温度滑块后，分别使用对应的 `versionForm.temperature` 或 `versionForm.synthesis_temperature` 展示同样的动态说明。现有不一致提示和大于 1 提示保持原位置与逻辑；合成温度也补齐大于 1 提示。

- [ ] **Step 3: Run focused frontend contracts**

```bash
PYTHONPATH=. .venv/bin/pytest --confcutdir=tests/frontend -q tests/frontend/test_model_thinking_config_contract.py::test_temperature_controls_explain_effects_in_plain_language tests/frontend/test_model_thinking_config_contract.py::test_temperature_controls_allow_two_and_warn_above_one
```

Expected: PASS。

### Task 4: 做类型与回归检查

**Files:**
- No additional files.

- [ ] **Step 1: Run frontend type check**

```bash
./node_modules/.bin/vue-tsc --noEmit
```

Expected: PASS，无输出。

- [ ] **Step 2: Run the relevant frontend contract file**

```bash
PYTHONPATH=. .venv/bin/pytest --confcutdir=tests/frontend -q tests/frontend/test_model_thinking_config_contract.py
```

Expected: PASS；与本次温度文案无关的既有失败单独记录，不修改无关文件。

- [ ] **Step 3: Check patch formatting and scope**

```bash
git diff --check
git status --short
```

Expected: 无 whitespace 错误；不自动 stage 或 commit。
