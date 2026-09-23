# EmbedChat 运行恢复器（run-status 驱动）Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 让 EmbedChat 在组件被卸载重建、切后台导致 SSE 失效等场景下，仍以服务端 `run-status` 为真相源把运行中的任务结果自动收敛到界面，并保证输入框不会永久锁在 busy。

**Architecture:** 不改后端。前端新增一个纯函数 `planHistorySync` 负责"如何把服务端最新一条助手结果合并进当前消息列表而不产生重复气泡"；EmbedChat 内部新增"运行恢复器"，在 `remoteRunActive === true` 时周期同步历史、转为 `false` 时补一次终态同步，并用 `streamInFlight` 区分"流仍在读"与"流已死但界面还挂着 busy"；组件卸载时 abort 掉幽灵流。

**Tech Stack:** Vue 3 + TypeScript + Vite；契约测试用 pytest（`tests/frontend/`，源码断言 + node 转译执行纯函数）。

---

## 背景（代码事实，供实施者对照）

- `/embed/chat` 与 `/dashboard/users` 是**顶层路由**，`App.vue` 只有裸 `<router-view>`，**无 keep-alive**（`frontend/src/router/index.ts:36-49`、`frontend/src/App.vue:15`）。
- 切到用户管理再切回 = EmbedChat **卸载重建**；`document.visibilityState` 不变，`visibilitychange` 不触发，因此现有恢复链（`EmbedChat.vue:9211-9220`）完全不执行。
- 服务端在客户端断开后仍会把任务跑完并落库（`app/api/v1/endpoints/chat.py:1782-1852`），`/conversation/{id}/run-status` 是权威运行状态（`chat.py:913-943`）。数据不丢，缺的是前端回收。
- EmbedChat 卸载时**不 abort** 流（`EmbedChat.vue:9227-9241`），会留下无人读取的幽灵流；AgentDebug 则 abort（`AgentDebug.vue:3967-3968`）。

## File Structure

- Create: `frontend/src/utils/runRecoverySync.ts` —— 纯函数 `planHistorySync`，只有"合并决策"一个职责，无 Vue 依赖，可被 node 直接单测。
- Modify: `frontend/src/views/EmbedChat.vue` —— 接入恢复决策、新增运行恢复器、卸载 abort。
- Create: `tests/frontend/test_embed_run_recovery_contract.py` —— 纯函数行为测试 + EmbedChat 源码契约。
- Modify: `tests/CHECKLIST.md` —— 新增验收项。

---

### Task 1: 新增 `planHistorySync` 纯函数

**Files:**
- Create: `frontend/src/utils/runRecoverySync.ts`
- Test: `tests/frontend/test_embed_run_recovery_contract.py`

- [ ] **Step 1: 写失败测试**

新建 `tests/frontend/test_embed_run_recovery_contract.py`：

```python
"""契约：EmbedChat 运行恢复器（run-status 驱动的会话续订）。

覆盖两层：
1. `planHistorySync` 纯函数行为（node 转译真实 TS 源码执行，非复刻）。
2. `EmbedChat.vue` 的接线契约（周期同步、终态收敛、busy 权威复位、卸载 abort）。
"""
import json
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
EMBED_CHAT = ROOT / "frontend/src/views/EmbedChat.vue"
pytestmark = pytest.mark.no_infrastructure


def _plan(messages, latest):
    module_path = "frontend/src/utils/runRecoverySync.ts"
    script = f"""
(async () => {{
const fs = require('fs');
const ts = require('./frontend/node_modules/typescript');
const source = fs.readFileSync({json.dumps(module_path)}, 'utf8');
const code = ts.transpileModule(source, {{
  compilerOptions: {{ module: ts.ModuleKind.CommonJS, target: ts.ScriptTarget.ES2022 }}
}}).outputText;
const moduleRef = {{ exports: {{}} }};
new Function('module', 'exports', require, code)(moduleRef, moduleRef.exports, require);
const result = moduleRef.exports.planHistorySync(
  {json.dumps(messages, ensure_ascii=False)},
  {json.dumps(latest, ensure_ascii=False)},
);
process.stdout.write(JSON.stringify(result));
}})().catch(error => {{ console.error(error); process.exit(1); }});
"""
    completed = subprocess.run(
        ["node", "-e", script], cwd=ROOT, check=True, capture_output=True, text=True
    )
    return json.loads(completed.stdout)


def test_updates_matching_trace_when_server_summary_is_longer():
    assert _plan(
        [{"role": "user", "trace_id": "t1", "content": "hi"},
         {"role": "agent", "trace_id": "t1", "content": "半", "isThinking": False}],
        {"trace_id": "t1", "summary": "完整回答"},
    ) == {"kind": "update", "index": 1}


def test_ignores_matching_trace_when_frontend_already_has_full_content():
    assert _plan(
        [{"role": "agent", "trace_id": "t1", "content": "完整回答", "isThinking": False}],
        {"trace_id": "t1", "summary": "完整回答"},
    ) == {"kind": "ignore"}


def test_fills_trace_less_streaming_placeholder_instead_of_duplicating():
    assert _plan(
        [{"role": "user", "trace_id": "t1", "content": "hi"},
         {"role": "agent", "content": "", "isThinking": True}],
        {"trace_id": "t1", "summary": "后台跑完的回答"},
    ) == {"kind": "fill", "index": 1}


def test_appends_when_only_the_user_turn_is_present():
    assert _plan(
        [{"role": "user", "trace_id": "t1", "content": "hi"}],
        {"trace_id": "t1", "summary": "后台跑完的回答"},
    ) == {"kind": "append"}


def test_ignores_when_trace_already_seen_without_matching_agent_message():
    """上一轮 trace 已出现时不得重复追加，避免恢复同步产生重复气泡。"""
    assert _plan(
        [{"role": "user", "trace_id": "t0", "content": "旧提问"},
         {"role": "agent", "trace_id": "t0", "content": "旧回答"},
         {"role": "user", "trace_id": "t1", "content": "新提问"}],
        {"trace_id": "t0", "summary": "旧回答"},
    ) == {"kind": "ignore"}


def test_ignores_when_latest_record_has_no_persisted_summary():
    assert _plan(
        [{"role": "user", "trace_id": "t1", "content": "hi"}],
        {"trace_id": "t1", "summary": ""},
    ) == {"kind": "ignore"}
```

- [ ] **Step 2: 跑测试确认失败**

Run: `pytest --confcutdir=tests/frontend tests/frontend/test_embed_run_recovery_contract.py -q`
Expected: FAIL（`frontend/src/utils/runRecoverySync.ts` 不存在，node 读取报错）

- [ ] **Step 3: 实现纯函数**

新建 `frontend/src/utils/runRecoverySync.ts`：

```ts
/**
 * 运行恢复同步决策。
 *
 * EmbedChat 是路由级页面：切到平台内其他页面再切回会卸载重建，SSE 与页面实例的绑定
 * 随之丢失，而服务端 producer 仍会把任务跑完并落库。恢复时必须在"不产生重复气泡、
 * 不覆盖更长正文"的前提下，把最新一条助手结果合并回当前消息列表。
 *
 * 只做决策、不碰响应式对象，便于直接单测。
 */

export interface HistorySyncMessage {
  role: string;
  trace_id?: string;
  content?: string;
  isThinking?: boolean;
}

export interface LatestAssistantRecord {
  trace_id?: string | null;
  summary?: string | null;
}

export type HistorySyncAction =
  | { kind: "update"; index: number }
  | { kind: "fill"; index: number }
  | { kind: "append" }
  | { kind: "ignore" };

export function planHistorySync(
  messages: HistorySyncMessage[],
  latest: LatestAssistantRecord,
): HistorySyncAction {
  const traceId = String(latest?.trace_id || "").trim();
  const summary = String(latest?.summary || "");
  // 服务端尚未落库终态内容（任务仍在跑）时不触碰界面。
  if (!traceId || !summary) return { kind: "ignore" };

  const matchedIndex = messages.findIndex(
    (message) => message?.role === "agent" && message?.trace_id === traceId,
  );
  if (matchedIndex !== -1) {
    const current = messages[matchedIndex];
    const currentContent = String(current?.content || "");
    const needsSync = current?.isThinking === true || currentContent.length < summary.length;
    return needsSync ? { kind: "update", index: matchedIndex } : { kind: "ignore" };
  }

  // 该 trace 已出现在列表里（例如本轮 user 已落库、助手消息尚未生成）：不重复追加。
  if (messages.some((message) => message?.trace_id === traceId)) {
    return { kind: "ignore" };
  }

  const lastIndex = messages.length - 1;
  if (lastIndex < 0) return { kind: "ignore" };
  const last = messages[lastIndex];

  // 流式占位消息还没拿到 trace_id：就地补全，避免出现两个助手气泡。
  if (last?.role === "agent" && !last?.trace_id && (last.isThinking === true || !last.content)) {
    return { kind: "fill", index: lastIndex };
  }
  // 用户刚提问、助手结果才落库：追加一条。
  if (last?.role === "user") return { kind: "append" };

  return { kind: "ignore" };
}
```

- [ ] **Step 4: 跑测试确认通过**

Run: `pytest --confcutdir=tests/frontend tests/frontend/test_embed_run_recovery_contract.py -q`
Expected: 6 passed

---

### Task 2: EmbedChat 接入恢复决策

**Files:**
- Modify: `frontend/src/views/EmbedChat.vue`（import 区、`syncLatestSessionHistory` 内 `9139-1189` 区段）

- [ ] **Step 1: 写失败测试**

在 `tests/frontend/test_embed_run_recovery_contract.py` 追加：

```python
def _embed_source() -> str:
    return EMBED_CHAT.read_text(encoding="utf-8")


def test_embed_chat_routes_history_sync_through_decision_helper():
    source = _embed_source()
    assert 'from "@/utils/runRecoverySync"' in source
    assert "planHistorySync(" in source
    assert "action.kind ===" in source
```

- [ ] **Step 2: 跑测试确认失败**

Run: `pytest --confcutdir=tests/frontend tests/frontend/test_embed_run_recovery_contract.py -q -k decision_helper`
Expected: FAIL（未引入）

- [ ] **Step 3: 接入**

在 `frontend/src/views/EmbedChat.vue` 的 import 区加入：

```ts
import { planHistorySync } from "@/utils/runRecoverySync";
```

把 `syncLatestSessionHistory` 中「命中/追加」的分支替换为决策驱动（保留原有字段映射）：

```ts
      if (res.data?.data && Array.isArray(res.data.data.items) && res.data.data.items.length > 0) {
        const latestServerItem = res.data.data.items[0];
        const action = planHistorySync(messages.value, {
          trace_id: latestServerItem?.trace_id,
          summary: latestServerItem?.summary,
        });
        if (action.kind === "update" || action.kind === "fill") {
          const currentMsg = messages.value[action.index];
          if (currentMsg) {
            currentMsg.trace_id = latestServerItem.trace_id ?? currentMsg.trace_id;
            currentMsg.content = latestServerItem.summary;
            currentMsg.reasoningContent = latestServerItem.reasoning_content ?? currentMsg.reasoningContent;
            currentMsg.processTimeline = hydrateHistoryProcessTimeline(latestServerItem.process_timeline, latestServerItem.reasoning_content);
            currentMsg.isThinking = false;
            if (!currentMsg.agentName) {
              currentMsg.agentName = latestServerItem.agent_name ?? undefined;
              currentMsg.agentDisplayName = latestServerItem.agent_display_name || (String(latestServerItem.agent_name || '').startsWith('sys_') ? '系统助手' : undefined);
              currentMsg.agentType = latestServerItem.agent_type ?? undefined;
              currentMsg.agentAvatarUrl = latestServerItem.agent_avatar_url ?? undefined;
              currentMsg.prompt_tokens = latestServerItem.prompt_tokens ?? undefined;
              currentMsg.completion_tokens = latestServerItem.completion_tokens ?? undefined;
              currentMsg.total_tokens = latestServerItem.total_tokens ?? undefined;
            }
          }
        } else if (action.kind === "append") {
          messages.value.push({
            id: Date.now(),
            trace_id: latestServerItem.trace_id,
            role: 'agent',
            content: latestServerItem.summary,
            reasoningContent: latestServerItem.reasoning_content ?? undefined,
            processTimeline: hydrateHistoryProcessTimeline(latestServerItem.process_timeline, latestServerItem.reasoning_content),
            logs: [],
            isThinking: false,
            feedback: null,
            agentName: latestServerItem.agent_name ?? undefined,
            agentDisplayName: latestServerItem.agent_display_name || (String(latestServerItem.agent_name || '').startsWith('sys_') ? '系统助手' : undefined),
            agentType: latestServerItem.agent_type ?? undefined,
            agentAvatarUrl: latestServerItem.agent_avatar_url ?? undefined,
            prompt_tokens: latestServerItem.prompt_tokens ?? undefined,
            completion_tokens: latestServerItem.completion_tokens ?? undefined,
            total_tokens: latestServerItem.total_tokens ?? undefined,
            timestamp: latestServerItem.created_at,
          });
        }
        if (action.kind !== "ignore") {
          if (isProcessing.value) {
            isProcessing.value = false;
          }
          clearVisibilitySyncTimer();
          await nextTick();
          scrollToBottom();
          return;
        }
      }
```

- [ ] **Step 4: 跑测试确认通过**

Run: `pytest --confcutdir=tests/frontend tests/frontend/test_embed_run_recovery_contract.py -q`
Expected: 7 passed

---

### Task 3: 运行恢复器（周期同步 + 终态收敛 + busy 权威复位）

**Files:**
- Modify: `frontend/src/views/EmbedChat.vue`（`isProcessing` 声明区、`sendMessageInternal` 流生命周期、`onMounted` 内可见性区）

- [ ] **Step 1: 写失败测试**

追加：

```python
def test_embed_chat_runs_status_driven_recovery_sync():
    source = _embed_source()
    assert "let streamInFlight = false;" in source
    assert "const RUN_RECOVERY_SYNC_INTERVAL_MS = 3000;" in source
    assert "const scheduleRunRecoverySync = ()" in source
    assert "watch(remoteRunActive, (active) =>" in source
    assert "scheduleRunRecoverySync();" in source
    assert "void syncLatestSessionHistory(1, 5);" in source
    # busy 复位必须以 run-status 为准，且不能在有流在途时误复位
    assert "if (!streamInFlight && isProcessing.value && !hasPendingConfirmation())" in source


def test_embed_chat_clears_recovery_timer_on_hide():
    source = _embed_source()
    assert "clearRunRecoveryTimer();" in source
    assert "handlers?.clearRunRecoveryTimer" in source
```

- [ ] **Step 2: 跑测试确认失败**

Run: `pytest --confcutdir=tests/frontend tests/frontend/test_embed_run_recovery_contract.py -q -k recovery_sync`
Expected: FAIL

- [ ] **Step 3: 实现**

在 `isProcessing` 声明旁新增：

```ts
// 流式读取是否在途：用于区分「流仍在读」与「流已死但界面还挂着 busy」。
let streamInFlight = false;
// 组件是否已卸载：卸载后不再执行流收尾副作用（避免僵尸请求与轮询）。
let embedUnmounted = false;
```

在 `sendMessageInternal` 中 `abortController = new AbortController();` 之后置 `streamInFlight = true;`，并在 `finally` 首行置 `streamInFlight = false;`。

在 `onMounted` 内、`syncLatestSessionHistory` 之后新增恢复器：

```ts
  // --- 运行恢复器（run-status 驱动的会话续订）---
  // 组件可能因平台内路由切换被卸载重建，也可能因切后台导致 SSE 死掉；两种情况下
  // SSE 与页面的绑定都会丢失，而服务端 producer 仍会把任务跑完落库。只要 run-status
  // 仍为 active 就周期同步历史，active 转 false 时补一次终态同步。
  const RUN_RECOVERY_SYNC_INTERVAL_MS = 3000;
  let runRecoveryTimer: any = null;
  const clearRunRecoveryTimer = () => {
    if (runRecoveryTimer) {
      clearTimeout(runRecoveryTimer);
      runRecoveryTimer = null;
    }
  };
  const hasPendingConfirmation = () => {
    const lastMsg = messages.value[messages.value.length - 1];
    return Boolean(
      lastMsg?.pendingPermission?.status === "pending" ||
      lastMsg?.pendingExternalExecution?.status === "pending",
    );
  };
  const scheduleRunRecoverySync = () => {
    clearRunRecoveryTimer();
    if (!remoteRunActive.value || document.visibilityState !== "visible") return;
    // 流仍在正常推进时由 SSE 自己收敛界面：remoteRunActive 在每次正常发送时同样为
    // true，若不设这道闸门，普通对话也会多出每 3s 一次的 history 请求。
    if (streamInFlight && !showStalledPrompt.value) {
      runRecoveryTimer = setTimeout(scheduleRunRecoverySync, RUN_RECOVERY_SYNC_INTERVAL_MS);
      return;
    }
    runRecoveryTimer = setTimeout(async () => {
      runRecoveryTimer = null;
      await syncLatestSessionHistory(1, 1);
      if (remoteRunActive.value) scheduleRunRecoverySync();
    }, RUN_RECOVERY_SYNC_INTERVAL_MS);
  };
  watch(remoteRunActive, (active) => {
    if (active) {
      scheduleRunRecoverySync();
      return;
    }
    clearRunRecoveryTimer();
    if (!streamInFlight && isProcessing.value && !hasPendingConfirmation()) {
      // run-status 是权威真相源：SSE 已死时不能把输入框永久锁在 busy。
      isProcessing.value = false;
    }
    void syncLatestSessionHistory(1, 5);
  });
  // 挂载竞态兜底：若 run-status 在本组件挂载前就已解析为 active，watch 不会再触发。
  if (remoteRunActive.value) scheduleRunRecoverySync();
```

`onVisibilityChange` 改为：

```ts
  const onVisibilityChange = () => {
    if (document.visibilityState === "visible") {
      clearVisibilitySyncTimer();
      void refreshCurrentRunStatus();
      syncLatestSessionHistory(1, 15);
      scheduleRunRecoverySync();
    } else {
      stopRemoteRunPolling();
      clearVisibilitySyncTimer();
      clearRunRecoveryTimer();
    }
  };
```

并把清理函数登记到卸载钩子：

```ts
  (onUnmountHandlers as any).value = { onMessage, onOnline, onOffline, onVisibilityChange, clearVisibilitySyncTimer, clearRunRecoveryTimer };
```

- [ ] **Step 4: 跑测试确认通过**

Run: `pytest --confcutdir=tests/frontend tests/frontend/test_embed_run_recovery_contract.py -q`
Expected: 9 passed

---

### Task 4: 卸载时 abort 幽灵流

**Files:**
- Modify: `frontend/src/views/EmbedChat.vue`（`onUnmounted`、`sendMessageInternal` 的 catch/finally）

- [ ] **Step 1: 写失败测试**

追加：

```python
def test_embed_chat_aborts_in_flight_stream_on_unmount():
    source = _embed_source()
    assert "embedUnmounted = true;" in source
    assert "if (abortController) abortController.abort();" in source
    # 卸载后的收尾副作用必须被守卫
    assert "if (!embedUnmounted) {" in source
```

- [ ] **Step 2: 跑测试确认失败**

Run: `pytest --confcutdir=tests/frontend tests/frontend/test_embed_run_recovery_contract.py -q -k unmount`
Expected: FAIL

- [ ] **Step 3: 实现**

`onUnmounted` 开头：

```ts
onUnmounted(() => {
  embedUnmounted = true;
  if (abortController) abortController.abort();
  if (abortController) abortController = null;
  cancelPendingUrlTokenInitialization();
  ...
  if (handlers?.clearRunRecoveryTimer) handlers.clearRunRecoveryTimer();
```

`sendMessageInternal` 的 catch / finally 加守卫：

```ts
  } catch (e: any) {
    flushContentBuffer();
    if (e.name === "AbortError") {
      if (!embedUnmounted) agentMsg.value.content += "\n[用户终止]";
    } else if (document.visibilityState === "hidden") {
      console.log("[Stream] Client disconnected in background; awaiting background producer sync on resume.");
    } else if (!embedUnmounted) {
      agentMsg.value.content += `\n[错误: ${e.message}]`;
    }
  } finally {
    streamInFlight = false;
    if (!embedUnmounted) {
      flushContentBuffer();
      isProcessing.value = agentMsg.value.pendingPermission?.status === "pending" || agentMsg.value.pendingExternalExecution?.status === "pending";
      void refreshCurrentRunStatus();
      ...原有收尾...
      scrollToBottom();
    }
  }
```

- [ ] **Step 4: 跑测试确认通过**

Run: `pytest --confcutdir=tests/frontend tests/frontend/test_embed_run_recovery_contract.py -q`
Expected: 10 passed

---

### Task 5: 全量验证与清单

**Files:**
- Modify: `tests/CHECKLIST.md`

- [ ] **Step 1: 前端契约测试全量**

Run: `pytest --confcutdir=tests/frontend tests/frontend -q`
Expected: 全部通过（含既有 `test_conversation_run_status_contract.py`、`test_chat_send_dedup_contract.py`）

- [ ] **Step 2: 类型检查**

Run: `cd frontend && npx vue-tsc --noEmit`
Expected: 无新增类型错误

- [ ] **Step 3: 更新 `tests/CHECKLIST.md`**

新增验收项：EmbedChat 切到平台内其他页面再切回后，运行中的任务会自动续订结果；输入框不会永久停留在 busy；恢复同步不产生重复气泡。

---

## 手动验收（由用户执行，不由 Agent 跑）

1. 在 EmbedChat 发起一个长任务（>1 分钟）。
2. 任务执行中切到「用户管理」，停留 10 秒以上，再切回 EmbedChat。
3. 期望：输入框解锁；任务跑完后回答自动出现在界面上（不需要新建会话）；不出现重复气泡。
4. 反向对照：任务执行中不要在切走后等待结束，而是切回后等其自然结束，同样应自动收敛。

---

### Task 6: 流式草稿快照（还原已输出的思考与正文）

**Files:**
- Create: `frontend/src/utils/embedStreamSnapshot.ts`
- Modify: `frontend/src/views/EmbedChat.vue`
- Test: `tests/frontend/test_embed_stream_snapshot_contract.py`

**为什么需要：** 本轮内容只存在于流式内存，尚未落库。切页面使 EmbedChat 卸载重建，
重建后 `messages` 为空 → 命中 `v-else-if="messages.length === 0"` 退回欢迎页，
`reasoning_content` 与 `processTimeline` 随之消失；而 `visibilitychange` 在平台内
路由切换时根本不触发，所以既有恢复链帮不上忙。

**关键设计：**

1. `buildEmbedStreamSnapshot({ conversationId, traceId, user, draft, now })` 构建快照，
   并做体积硬约束（正文/思考各 20000 字符；时间线 120 条 / 递归 2 层 / 长文本截断 /
   剔除 `file_metadata.changes`；总体 400KB，超限先丢时间线再丢思考，仍超限则返回 null）。
2. `isEmbedStreamSnapshotUsable(snapshot, conversationId, now)` 校验版本、会话归属、
   12h TTL 与"是否有可见内容"。
3. 存储按「`instance_id` + `conversation_id`」分桶写 sessionStorage；任何存储异常静默降级。
4. 写入：`flushContentBuffer` 内节流 1.5s（`maybePersistStreamSnapshot()`），
   卸载前强制写一次（`maybePersistStreamSnapshot(true)`）。
5. 还原：`fetchConversationHistory` 完成后调用 `restoreEmbedStreamSnapshot(initGeneration)`；
   同 trace 终态已可见则丢弃草稿。草稿带 `trace_id`，落库终态到达时由 `planHistorySync`
   命中并覆盖，随后 `clearStreamSnapshot(conversationId.value)`。
6. 配套修正：`planHistorySync` 在 `messages` 为空且服务端已有终态时返回 `reload`，
   组件重拉整段历史（修复"任务跑完回答也不出现"）。

**边界（如实记录）：** 只覆盖同 tab 内的切页面与刷新；跨 tab / 跨设备恢复需要后端
Redis 快照方案（本次未做）。快照含对话内容，落在 sessionStorage（本 tab、TTL 12h、
关闭标签即清），合规判断由业务侧确认。

---

### Task 7: 实时续显（事件日志 + 增量轮询）

本文件记录的 Task 1-6 解决的是「切回后**结果**能否收敛」；切回后的**过程实时性**
由独立计划承接：`docs/superpowers/plans/2026-09-23-embed-stream-resume.md`。

两条链路的分工与边界：
- 本文件的流式快照（`embedStreamSnapshot.ts`）负责**切回瞬间**立即还原已输出的思考与正文；
- 事件日志计划新增的续显轮询负责其后的**持续推进**（思考、工具时间线、正文增量，约 1s 粒度）；
- 终态一律以落库历史为准（`planHistorySync` 走 update/fill/append/reload），两条链路都不改变这一点。

已知取舍与降级：
- 事件日志是 Redis 旁路：Redis 不可用、日志被 `LTRIM` 截断或 TTL 过期时，自动退回本文件的
  「快照 + 终态同步」行为，只是过程显示不完整，终态仍正确。
- 轮询粒度约 1s，非严格流式。若需要真正的逐帧续接，需改走后端会话级广播 hub +
  SSE 断点续订（`Last-Event-ID` 式回放），改动与回归面显著更大，本次未做。
- 活流 SSE 事件本身不带 `_seq`，因此活流进行中刷新页面时，快照里的游标可能短暂落后于快照正文，
  重放会重复叠加少量正文/时间线；已由「每批后续显立即强制落盘」把窗口压到约一个轮询周期，
  并由 `appendAssistantBodyDelta` 的去重与终态覆盖兜底。彻底消除需后端在活流事件里也带 `_seq`。
