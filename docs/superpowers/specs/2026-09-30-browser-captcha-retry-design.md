# 浏览器验证码自动解算重试策略设计

## 目标

让 AI 对验证码的自动解算真正"试满几次再交人"，并把"为什么交给人工"暴露给用户：

1. 一轮自动解算内连续重试到上限（**3 次**，常量写死），每次之间做指数退避；
2. 未配置多模态模型时不消耗重试额度、不做无谓空转，并给出明确原因；
3. 解算过程与失败原因在面板可见；
4. 自动解算不得阻塞人工接管——用户任何时候点击画面都要立刻生效。

## 现状与问题

当前链路：

- AI 路径：`browser_snapshot` → `BrowserRuntime.snapshot()` → `BrowserWorker.snapshot()`（内部 `_detect_captcha` 判定 `page_state`）→ `BrowserRuntime.try_auto_solve_captcha()`。
- Viewer 路径：`BrowserPanel.vue` 每 2 秒发 `{"type":"snapshot"}` → `browser_viewer` WebSocket 处理器 → **同一个** `BrowserRuntime.snapshot()`。

存在的问题：

| # | 问题 | 证据 |
|---|------|------|
| 1 | 重试上限只有 2，且是**跨快照调用**累计的，一次 `browser_snapshot` 只试 1 次就降级 | `browser_runtime.py` `try_auto_solve_captcha()` |
| 2 | Viewer 的 2 秒轮询与 AI 共用同一计数器，验证码出现后几秒内额度被两边一起耗尽 | `browser.py` `event == "snapshot"` 分支调用 `runtime.snapshot()` |
| 3 | 未配多模态模型与"识别失败"压成同一个 `False`，前者毫秒级烧完额度 | `captcha_solver.py` `_resolve_vision_model()` 提前 `return False` |
| 4 | 失败原因被前端丢弃，界面只有固定文案 | `browser.py` 广播的 `reason` 字段未被 `BrowserPanel.vue` 的 `captcha` 分支读取 |
| 5 | 计数达到上限后，只有页面脱离 captcha 状态才会清零，AI 侧再无自动重试入口 | `_remember_snapshot_locked()` |

## 设计

### 1. 常量（写死，不做配置化）

在 `browser_runtime.py` 顶部与 `HUMAN_CONTROL_TIMEOUT_SECONDS` 并列：

```python
CAPTCHA_MAX_ATTEMPTS = 3                    # 一轮自动解算的最大尝试次数
CAPTCHA_RETRY_BACKOFF_BASE_SECONDS = 1.5    # 第 1 次失败后的退避基数
CAPTCHA_RETRY_BACKOFF_MAX_SECONDS = 4.0     # 单次退避上限
```

第 i 次失败（i 从 1 开始）后的退避为 `min(BASE * 2 ** (i - 1), MAX)`，即 1.5s、3.0s。

单轮耗时上界约 12~20 秒（含 `solve_captcha` 内部 1.5 秒的页面稳定等待），远低于工具默认 180 秒超时（`DEFAULT_AGENT_MAX_TOOLCALL_TIMEOUT`）。

### 2. 解算结果结构化：`CaptchaSolveOutcome`

`captcha_solver.py` 新增不可变结果对象与详细入口：

```python
@dataclass(frozen=True)
class CaptchaSolveOutcome:
    solved: bool
    retryable: bool
    reason_code: str      # no_vision_model | unsupported_type | recognition_failed | action_failed | still_captcha | error
    message: str
```

- 新增 `solve_captcha_detailed()`，承担现有 `solve_captcha()` 的全部逻辑并返回 `CaptchaSolveOutcome`；
- 原 `solve_captcha()` 保留为薄包装（返回 `.solved`），**不改动既有 3 个 solver 测试的契约**。

分类规则：

| reason_code | retryable | 是否消耗额度 | 说明 |
|---|---|---|---|
| `no_vision_model` | 否 | **否** | 配置缺失，重试无意义；下次快照仍会重新检查 |
| `unsupported_type` | 否 | 是 | 模型明确判定为短信/扫码/人脸等不可自动处理 |
| `recognition_failed` | 是 | 是 | 模型未返回可解析的坐标 |
| `action_failed` | 是 | 是 | 坐标有效但动作执行被拒（如滑块距离无效） |
| `still_captcha` | 是 | 是 | 动作已执行，页面仍处于验证码状态 |
| `error` | 是 | 是 | 解算过程抛异常 |

### 3. 单轮连续重试

`try_auto_solve_captcha()` 由"最多一次"改为**一轮循环**：

1. 若页面已非 captcha → 直接返回；
2. 若用户已显式接管（`owner_id` 非空）→ 不打扰，直接返回；
3. 若已用尽额度（`_captcha_attempts >= CAPTCHA_MAX_ATTEMPTS`）→ 直接降级；
4. 循环 `CAPTCHA_MAX_ATTEMPTS` 次：
   - 每次开始前广播 `ai_action`：`第 N/3 次` （供面板显示进度）；
   - 调用 `solve_captcha_detailed()`；
   - 成功 → 清空计数、返回最新快照；
   - 失败且 `retryable=False` → 立即跳出循环；
   - 失败且可重试 → `await asyncio.sleep(backoff)`，**每轮开始前重新检查是否已被人工接管**，被接管则立即中止；
5. 循环结束写入 `_captcha_attempts = CAPTCHA_MAX_ATTEMPTS`（"已用尽"），设置人工控制并广播 `captcha` 事件。

失败原因文案映射后进入广播与 `ai_action`：

- `no_vision_model` → 「未配置支持多模态的默认模型，无法自动识别验证码」
- `unsupported_type` → 「验证码类型不支持自动识别」
- 其它 → 「自动识别未通过」

降级时不再立即 `clear_ai_action`，而是保留一条 `"AI 已自动尝试 N 次未通过（原因：…），请人工完成验证"`，让面板的 `currentAiAction` 区域可见（前端已渲染该字段，**无需前端改动**）。

### 4. Viewer 路径不阻塞（关键配套）

Viewer 的 WebSocket 消息是**串行**处理的，若其 `snapshot` 分支同步跑满一轮解算，用户在面板上的点击/拖拽会被排队十几秒——比现状更糟。因此：

- `BrowserRuntime.snapshot()` 增加关键字参数 `auto_solve: bool = True`，Viewer 路径一律传 `False`；
- `browser.py` 内新增 `_viewer_snapshot(session_id)`：取快照后若为 captcha，则**调度后台任务**执行一轮解算，并立刻返回当前快照；
- 后台任务完成后，通过既有事件通道广播 `{"type":"snapshot","snapshot": _viewer_snapshot_payload(...)}`，前端已有的 `payload.type === 'snapshot'` 分支会直接刷新画面（零前端改动）；
- 解算失败时沿用现有 `{"type":"captcha","detected":true,...}` 广播，面板进入人工接管提示。

### 5. 一轮解算去重

同一 session 同时只允许一轮解算在跑，避免 Viewer 后台任务与 AI 的 `browser_snapshot` 重复烧模型调用：

- `BrowserRuntime.auto_solve_captcha(session_id)` 内部持有 `dict[str, asyncio.Task]`；
- 已有运行中的任务则 `await asyncio.shield(task)` 复用同一轮，不再新建；
- `snapshot(auto_solve=True)` 与 Viewer 后台任务都经由该方法；
- `close()` / `clean_idle_sessions()` / `shutdown()` 时取消或忽略未完成任务，避免悬挂。

## 非目标

- 不新增前端交互（不加"让 AI 再试一次"按钮）；
- 不引入环境变量或系统配置项（本轮由用户明确要求写死常量）；
- 不改进验证码识别算法本身，不涉及绕过风控；
- 不改变人工接管的抢占语义与 45 秒等待超时。

## 测试策略

TDD 先写失败测试，覆盖：

1. 一轮内重试到上限：可重试失败 × 3 → `solve_captcha_detailed` 调用 3 次、最终 `owner=human`；
2. 中途成功即停止：第 2 次成功 → 调用 2 次、`owner=ai`、页面 `ready`；
3. `no_vision_model`：不消耗额度、立即降级、随后再次快照仍会重新尝试；
4. `unsupported_type`：只尝试 1 次即降级；
5. 退避可测：测试内把退避常量 monkeypatch 为 0；
6. Viewer 路径不阻塞：`snapshot(auto_solve=False)` 不同步执行解算；
7. 人工接管优先：解算循环中途检测到 `owner_id` 非空即中止。

存量 `test_captcha_solver.py` 中两个 runtime 用例因调用契约变化同步调整。

## 验证方式

- `pytest tests/services/ai/test_captcha_solver.py -q`
- 浏览器相关用例：`pytest tests/ -q -k "browser"`（不改前端契约，前端测试应保持不变）
- 按项目规范更新 `tests/CHECKLIST.md`
- **不执行 `./dev.sh`**，服务启停交由用户
