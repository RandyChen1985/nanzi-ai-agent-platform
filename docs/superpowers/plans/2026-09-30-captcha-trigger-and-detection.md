# 触发式验证码支持与检测细化 —— 实施计划与记录

- 日期：2026-09-30
- 设计文档：[2026-09-30-captcha-trigger-and-detection-design.md](../specs/2026-09-30-captcha-trigger-and-detection-design.md)
- 范围：方案 A（解算器学会点开触发按钮）+ 方案 B（检测细化）

## 任务分解与状态

### Task 1 — 失败测试先行（TDD）

新增到 `tests/services/ai/test_captcha_solver.py`：

1. `test_solver_clicks_trigger_button_then_recognizes_challenge`
2. `test_solver_stops_after_single_trigger_click`
3. `test_detect_captcha_script_separates_trigger_button_from_challenge`

验证 RED：

```bash
.venv/bin/python -m pytest tests/services/ai/test_captcha_solver.py -q -k "trigger or detect_captcha_script"
# 期望：3 failed
```

状态：✅ 已完成（3 failed 确认后进入实现）

### Task 2 — A：解算器支持 trigger

`app/services/ai/browser/captcha_solver.py`：

- `CAPTCHA_SOLVER_PROMPT` 新增第 3 类返回值 `trigger`（原 unsupported 顺延为第 4 类）；
- 新增常量 `CAPTCHA_ACTION_TRIGGER = "trigger"` 与 `CAPTCHA_TRIGGER_EXPAND_WAIT_SECONDS = 1.5`；
- `_execute_captcha_action` 新增 `trigger` 分支（平滑移动 + 点击）；
- `solve_captcha_detailed` 的"取截图 → 识别"改为 `while True` 循环：命中 `trigger` 时点击、等待展开、重新快照；页面已离开 captcha 直接返回成功，否则用新截图重新识别；`trigger_used` 保证每轮最多点一次。

状态：✅ 已完成

### Task 3 — B：检测细化

`app/services/ai/browser/browser_worker.py` 的 `_detect_captcha`：

- 文本标记扩充挑战文案（拖动滑块 / 按住滑块 / 向右滑动 / 请依次点击 / 滑动验证）；
- 节点判定由宽泛 `/captcha|geetest|nc_1|.../` 收窄为 `/captcha|geetest_(?:panel|popup|window|slider|widget|wrap|mask|cover|box_)|nc_1|.../`，排除 `geetest_btn` / `geetest_holder` 触发按钮；
- iframe 规则保持不变；
- `reason` 只在真正命中时返回，未命中返回 `null`。

状态：✅ 已完成

### Task 4 — 配套提示词

`app/services/ai/agent_prompts.py` 滑块条目补充：只有触发按钮时先点它，再 `browser_snapshot` 确认挑战是否出现。

状态：✅ 已完成

### Task 5 — 回归与登记

```bash
.venv/bin/python -m pytest tests/services/ai/test_captcha_solver.py \
  tests/services/ai/test_browser_runtime.py tests/services/ai/test_browser_events.py \
  tests/services/ai/test_browser_worker.py tests/frontend/test_browser_panel_contract.py -q
```

结果：**143 passed**（1 条与本改动无关的 `known_failures.txt` 陈旧条目告警）。

同步更新 `tests/CHECKLIST.md`。

状态：✅ 已完成

## 过程中发现与修正

- 新增用例 `test_solver_clicks_trigger_button_then_recognizes_challenge` 首次运行失败，原因是**测试自身的状态序列写错**：把"点击触发按钮后的校验快照"也设成了 `ready`，导致实现提前判定成功、未走到滑块识别。修正为 `["captcha", "captcha", "ready"]`（初始识别 → 点击后挑战展开仍 captcha → 拖动成功恢复）后通过。实现逻辑无需改动。

## 未执行项（边界）

- 未执行 `./dev.sh`、未执行任何部署或数据库操作、未执行 `git commit`；前端本轮无改动，无需重新构建（但仓库内含前几轮 `BrowserPanel.vue` 改动，仍建议整体 `./dev.sh`）。
- 极验 demo 的真机端到端验证需用户在控制台重启服务后进行。
