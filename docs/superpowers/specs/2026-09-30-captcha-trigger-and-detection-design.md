# 触发式验证码支持与检测细化设计

- 日期：2026-09-30
- 状态：已实现
- 相关文件：`app/services/ai/browser/captcha_solver.py`、`app/services/ai/browser/browser_worker.py`、`app/services/ai/agent_prompts.py`

## 1. 背景与问题

用户实测极验 demo（`https://demos.geetest.co`）发现：页面上有一个「点击按钮进行验证」的按钮，**需要先点它才会展开滑块挑战**，但平台表现得"AI 一直在等待、什么都不做"。

代码定位得到三方死结：

1. **检测太早**：极验初始触发按钮本身带 `geetest_` 前缀类名（`geetest_btn` / `geetest_holder`），而 `BrowserWorker._detect_captcha` 的判定正则是 `/captcha|geetest|nc_1|slider-verify|verify-slider/` 命中任意节点即判 `captcha`。挑战还没出现，页面就进入验证码态。
2. **AI 被冻结**：进入验证码态后 runtime 会挂上人工接管状态，而 `BrowserRuntime._wait_for_ai_control` 只要 `_human_controls` 有状态就阻塞（**不论 `owner_id` 是否为 None**，AI 自触发的降级同样阻挡），直到 `HUMAN_CONTROL_TIMEOUT_SECONDS`（默认 45s）超时才抛 `BrowserHumanControlRequired`。AI 想点那个按钮也点不了。
3. **解算器不会点它**：`BrowserCaptchaSolver` 只处理**已展开**的滑块（`slider`）与点选（`click_sequence`），`CAPTCHA_SOLVER_PROMPT` 没有"先点开触发控件"这一类型；模型面对初始态只能返回 `unsupported` 或识别失败，试满额度后转人工。

结果：AI 动不了、解算器不认、人也要等 45 秒或看到降级提示才知道该自己上。

## 2. 目标与非目标

**目标**

- 「点击按钮进行验证」这类触发式验证码可以端到端自动完成：点开 → 识别 → 拖动/点选 → 校验。
- 挑战尚未展开时不要冻结 AI，让 AI 有机会自行点开触发按钮。

**非目标**

- 不放开 AI 在**真实挑战展开后**的操作权（保持"不在验证码页乱点"的边界）。
- 不引入第三方打码平台，不做任何对抗性绕过。
- 不扩展现有支持类型（仍只有滑块与点选两类）。

## 3. 设计

### 3.1 A：解算器支持 `trigger` 动作

`CAPTCHA_SOLVER_PROMPT` 新增返回类型：

```json
{
  "type": "trigger",
  "x": <触发按钮中心 X>,
  "y": <触发按钮中心 Y>,
  "reason": "<按钮文字，例如 点击按钮进行验证>"
}
```

`_execute_captcha_action` 新增 `trigger` 分支：平滑移动 + 点击该坐标。

`solve_captcha_detailed` 重构为循环：截图 → 识别 → 若为 `trigger` 则点击、等待 `CAPTCHA_TRIGGER_EXPAND_WAIT_SECONDS`（1.5s）、**重新获取快照**；若页面已离开 captcha 视为完成，否则用**展开后的新截图**重新识别（`slider` / `click_sequence` 进入原有动作流程）。

**额度策略（用户确认）**：`trigger` 只是"展开动作"，**不计入重试额度**；但每轮**最多点击一次**（`trigger_used` 标记），避免模型误判导致反复乱点——第二次仍返回 `trigger` 时按 `recognition_failed`（可重试）处理。

### 3.2 B：检测细化，区分触发按钮与已展开挑战

`_detect_captcha` 的页面脚本改为三类证据：

- **文本证据** `challengeTextMarkers`：`验证码 / 安全验证 / 人机验证 / 滑块验证 / 拖动滑块 / 按住滑块 / 向右滑动 / 请依次点击 / 滑动验证 / captcha / verify-human`。「点击按钮进行验证」这类触发文案**不列入**。
- **节点证据** `challengeNodePattern`：`captcha|geetest_(?:panel|popup|window|slider|widget|wrap|mask|cover|box_)|nc_1|slider-verify|verify-slider|verify-move-block|slider-btn|slide-verify`。关键点是把原先宽泛的 `geetest` 收窄为**挑战子串**——`geetest_btn` / `geetest_holder` 不再命中。
- **iframe 证据**：`src` 命中 `captcha|geetest|nc_1|slider-verify|verify-slider`（iframe 通常就是挑战本体，规则不变）。

三者任一命中即 `captcha`。

### 3.3 A 与 B 的关系（为什么不是重复）

- **B** 解决"纯触发按钮页"：页面不算验证码态 → AI 可自由点击 → 点开后挑战展开 → 下一次快照命中挑战证据 → 进入验证码流程。
- **A** 解决"已被判为验证码、但挑战还需要先点一下才展开"的兜底（例如挑战 iframe 已存在但内容未展开、或页面同时存在挑战容器与触发按钮）。
- 二者互补，覆盖不同 DOM 结构。

### 3.4 配套：AI 提示词

`agent_prompts.py` 的滑块条目补充：页面只有「点击按钮进行验证 / 点击开始验证」时**先点击它，再调用一次 `browser_snapshot` 确认挑战是否出现**，不要以为点击就等于完成验证。

### 3.5 明确不改的部分

- `_wait_for_ai_control` 保持原样：真实挑战展开后 AI 仍然让行，由解算器处理，失败转人工。
- 人工接管优先级、`requires_human` 提示链路、手动刷新入口均不变。

## 4. 风险与兜底

| 风险 | 兜底 |
|---|---|
| 收窄 `geetest` 后部分站点漏检 | iframe 规则未收窄（多数集成走 iframe）；文本标记保留 `验证码/安全验证` 等；任何时刻快照命中挑战证据即恢复判定 |
| 模型误判把普通按钮当 `trigger` | 每轮最多点一次；点击后若页面离开 captcha 才视为完成，否则重新识别并按正常失败流程处理 |
| trigger 点击与环境交互冲突 | 点击在解算轮内执行（`_captcha_round_events` 闸门生效期间），AI 的其他动作已让行 |

## 5. 测试策略

- `test_solver_clicks_trigger_button_then_recognizes_challenge`：trigger → 点击 → 重新截图识别 slider → solved，且 `mouse.click` 恰好一次、识别恰好两次。
- `test_solver_stops_after_single_trigger_click`：模型持续返回 trigger 时只点一次，结果为 `recognition_failed` 且 retryable。
- `test_detect_captcha_script_separates_trigger_button_from_challenge`：契约断言检测脚本包含挑战文案与 `geetest_(?:panel|popup...)` 收窄规则。
