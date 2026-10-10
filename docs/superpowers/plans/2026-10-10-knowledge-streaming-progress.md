# 知识库问答流式进度反馈 实现计划

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 让知识库问答在幻觉网关缓冲期间也持续向前端推送过程事件，并在预检索结束后给出「检索完成，正在组织回答」状态项，消除思考卡片停在「工具完成: search_knowledge_base」的卡死观感。

**Architecture:** 只改 `KnowledgeAgentRunner` 的网关重试循环：按 `"content" in chunk` 把事件分流——携带正文的仍进 `chunks_buffer`（网关判定前对用户不可见的安全语义不变），其余过程事件（`model_call` / `log` / `reasoning_content` / `meta` 等）实时 `yield`。error 事件改用循环内标志位识别（它可能携带 content）。预检索后额外发一个 `category: "knowledge"` 的 pending 状态项，在首个正文 chunk 到达时收尾。落库收尾把该类别一并纳入，避免历史回放永远显示「进行中」。

**Tech Stack:** Python 3.11 / FastAPI / pytest（`asyncio_mode = auto`）；Vue 3 + TS（本计划不改前端源码，只加防回退契约）。

**Spec:** [`docs/superpowers/specs/2026-10-10-knowledge-streaming-progress-design.md`](../specs/2026-10-10-knowledge-streaming-progress-design.md)

---

## 交付约定（本项目特有，优先于 skill 默认流程）

1. **不得执行 `git commit`**。每个任务末尾的「检查点」只做汇报与 commit message 草稿，提交时机由用户决定。
2. **不得执行 `./dev.sh`** 或任何服务启停脚本。
3. 测试命令一律用虚拟环境解释器：`.venv/bin/python -m pytest ...`（系统 `python` 没有 pytest）。
4. 前端契约测试必须加 `--confcutdir=tests/frontend`：`.venv/bin/python -m pytest --confcutdir=tests/frontend tests/frontend/<file> -v`。
5. 不得使用 Python 3.12+ 语法；新增注释与文案用中文。

## 与 spec 的两处实现层修正（已在此计划中定稿）

这两处是写计划时对 spec §5.1 代码片段的必要修正，评审本计划时请一并确认：

**修正 1：`saw_error` 判定必须前置到分流之前。**
spec §5.1 把 `if chunk.get("type") == "error"` 写在 `else`（非正文）分支内，但 error 事件**可能携带 `content`**（安全拦截走的就是这条），那样它会进正文分支、`saw_error` 永不置位 → 退回「扫 buffer 找 error」的失效状态，进而把安全拦截误判成幻觉并覆盖原始错误。因此判定放在 `"content" in chunk` 之前，两个分支都能置位。带 content 的 error chunk 仍进 buffer（与改动前 flush 行为一致），不带 content 的实时转发——两者都不重复。

**修正 2：用单个 `synthesis_pending` 代替 `synthesis_closed`。**
spec §5.1 的 `synthesis_closed` 只能表达「已经收尾过」，表达不了「是否发出过」。而循环前的分支还有两条**不发状态项**的路径（追问复用上下文 `is_followup`、未指定知识库 abort 前的其它路径），它们同样会走到循环；若只用 `synthesis_closed`，首个正文 chunk 会发出一个**没有对应 pending 的 success 事件**，前端凭空多出一条「检索完成，正在组织回答 · 已完成」。改用 `synthesis_pending`：发出时置 `True`，收尾时置 `False`，一个变量同时表达「有待收尾项」与「已收尾」，天然幂等且跨重试轮安全。

---

## 文件结构

| 文件 | 变更 | 职责 |
| --- | --- | --- |
| `app/services/ai/runners/knowledge_agent_runner.py` | 修改 3 处 + 新增 1 个方法 | ① `_knowledge_synthesis_event` 辅助方法（统一 id/title/category）；② 预检索后发出 pending 状态项；③ 网关重试循环按 content 分流 + `saw_error` 标志位 + 首个正文 chunk 收尾 |
| `app/services/ai/runtime/agentscope/process_timeline_snapshot.py` | 修改 1 行 | `finalize_process_timeline` 的 pending 收尾条件由 `== "model"` 扩为 `in ("model", "knowledge")` |
| `tests/test_knowledge_streaming_contract.py` | 新建 | 6 条后端源码契约（分 3 个任务增量追加） |
| `tests/frontend/test_knowledge_streaming_contract.py` | 新建 | 3 条前端防回退契约 |
| `tests/CHECKLIST.md` | 追加 1 行 | 本次交付记录 |

**为什么 `knowledge_agent_runner.py` 不改结构：** 该文件已 1072 行，但本次改动集中在 `:685`、`:765`、`:914-949` 三处，且都在既有函数内，不引入新的抽象层。拆分文件属于独立重构，不夹带在本次。

---

## Task 1: 知识库专属状态项（发出）

**Files:**
- Create: `tests/test_knowledge_streaming_contract.py`
- Modify: `app/services/ai/runners/knowledge_agent_runner.py`（新增方法在 `:507` 之后、`async def execute(` 之前；初始化在 `:685`；发出在 `:765` 之后）

- [x] **Step 1: 写失败测试**

创建 `tests/test_knowledge_streaming_contract.py`：

```python
"""契约：知识库问答在模型生成期间必须持续推送过程事件，并给出语义化状态项。

背景：`KnowledgeAgentRunner` 的幻觉网关重试循环此前把**所有** chunk 都塞进
`chunks_buffer`，直到网关判定通过才一次性 flush。于是模型生成期间前端收不到任何
事件，思考卡片停在最后一个直接 yield 的「工具完成: search_knowledge_base」上，
答案也整段跳出。这些断言锁住四处接入点不被后续重构改回去：

1. 过程事件（不含 content）实时转发，只有正文进缓冲区；
2. error 事件用独立标志位识别（它可能携带 content，只在非正文分支判断会漏）；
3. 预检索后立刻发出「检索完成，正在组织回答」pending 项，并在首个正文 chunk 收尾；
4. 落库收尾覆盖该状态项的类别，避免历史回放永远显示「进行中」。
"""
from __future__ import annotations

from pathlib import Path

import pytest

pytestmark = pytest.mark.no_infrastructure

ROOT = Path(__file__).resolve().parents[1]
KNOWLEDGE_RUNNER = ROOT / "app/services/ai/runners/knowledge_agent_runner.py"
TIMELINE_SNAPSHOT = ROOT / "app/services/ai/runtime/agentscope/process_timeline_snapshot.py"

SYNTHESIS_PENDING_CALL = 'yield self._knowledge_synthesis_event(status="pending"'
MODEL_EVENT_LOOP = "async for chunk in self._execute_with_agentscope_native_agent(**execute_kwargs):"


def _read(path: Path) -> str:
    return path.read_text(encoding="utf-8")


def _knowledge_synthesis_helper_source() -> str:
    """截取 helper 定义体，避免把调用处的字面量误当实现。"""
    source = _read(KNOWLEDGE_RUNNER)
    start = source.index("def _knowledge_synthesis_event(")
    return source[start:source.index("async def execute(", start)]


def _model_execution_loop_source() -> str:
    """截取网关重试循环内遍历模型事件的循环体。"""
    source = _read(KNOWLEDGE_RUNNER)
    start = source.index(MODEL_EVENT_LOOP)
    return source[start:source.index("if saw_error:", start)]


def test_knowledge_synthesis_helper_has_stable_id_title_and_category():
    helper = _knowledge_synthesis_helper_source()

    assert '"id": "knowledge_synthesis"' in helper, "前端按 id 归并，必须是稳定 id"
    assert '"title": "检索完成，正在组织回答"' in helper
    assert '"category": "knowledge"' in helper, "knowledge 类别才有秒表与超时保护"


def test_prefetch_emits_knowledge_synthesis_pending_after_service_check():
    source = _read(KNOWLEDGE_RUNNER)

    prefetch = source.index("async for chunk in self._auto_invoke_search_knowledge_base(")
    service_check = source.index("if knowledge_service_unavailable:", prefetch)
    emit = source.index(SYNTHESIS_PENDING_CALL, service_check)
    assert emit > service_check, "状态项必须在服务可用性检查之后发出（不可用时直接 abort）"


def test_knowledge_synthesis_details_never_claims_zero_citations():
    source = _read(KNOWLEDGE_RUNNER)
    block = source[
        source.index("knowledge_synthesis_details = (") : source.index(MODEL_EVENT_LOOP)
    ]

    assert "未检索到相关资料，正在组织回复…" in block, "0 条引用时不得谎报「已获取 0 条资料」"
    assert "prefetch_had_citations" in block, "文案必须按有无引用分流"
```

- [x] **Step 2: 运行测试确认失败**

Run:
```bash
.venv/bin/python -m pytest tests/test_knowledge_streaming_contract.py -v
```
Expected: `3 failed`，三条都是 `ValueError: substring not found`（实现尚不存在）。

- [x] **Step 3: 实现 `_knowledge_synthesis_event` 辅助方法**

在 `app/services/ai/runners/knowledge_agent_runner.py` 的 `_yield_knowledge_fatal_abort` 方法结束处（`"content": KnowledgeChatPrompts.KNOWLEDGE_SERVICE_UNAVAILABLE_CONTENT,` / `"status": "error",` / `}` 之后）与 `async def execute(` 之前插入：

```python
    def _knowledge_synthesis_event(self, status: str, details: str) -> Dict[str, Any]:
        """知识库问答的「检索完成，正在组织回答」过程项。

        发出（pending）与收尾（success）必须共用同一 id 与 title：前端按 id 归并，
        title 不一致会让两个状态各成一条。故集中在此，勿在调用处手写字面量。
        """
        return {
            "type": "log",
            "id": "knowledge_synthesis",
            "title": "检索完成，正在组织回答",
            "details": details,
            "status": status,
            "category": "knowledge",
        }
```

- [x] **Step 4: 初始化变量并发出 pending 状态项**

4a. 在 `_execute_raw` 中 `prefetch_had_citations = False` 的下一行（约 `:685`）加初始化：

```python
        prefetch_had_citations = False
        # 预检索后发出、首个正文 chunk 到达时收尾的状态项；跨重试轮只发/收一次。
        knowledge_synthesis_details = ""
        synthesis_pending = False
```

4b. 在 `else` 分支的 `self._knowledge_retrieval_succeeded = prefetch_had_citations`（约 `:765`）之后加发出：

```python
            self._knowledge_retrieval_succeeded = prefetch_had_citations

            # 0 条引用时不得谎报「已获取 0 条资料」。文案在发出时定稿、收尾复用同一份：
            # 模型若在 ReAct 中二次检索会更新 _valid_citation_ids，收尾时重算会让
            # 同一条目前后文案不一致。
            knowledge_synthesis_details = (
                f"已获取 {len(self._valid_citation_ids)} 条资料，正在生成回答…"
                if prefetch_had_citations
                else "未检索到相关资料，正在组织回复…"
            )
            yield self._knowledge_synthesis_event(
                status="pending",
                details=knowledge_synthesis_details,
            )
            synthesis_pending = True
```

注意 `yield self._knowledge_synthesis_event(` 与 `status="pending"` 分了两行，而契约断言的是单行 `SYNTHESIS_PENDING_CALL`。因此此处必须写成契约断言期望的单行形式：

```python
            yield self._knowledge_synthesis_event(status="pending", details=knowledge_synthesis_details)
```

（该行约 100 字符，项目内既有代码存在同量级长行，无需换行。）

- [x] **Step 5: 运行测试确认通过**

Run:
```bash
.venv/bin/python -m pytest tests/test_knowledge_streaming_contract.py -v
```
Expected: `3 passed`

- [x] **Step 6: 语法与导入自检**

Run:
```bash
.venv/bin/python -c "import ast,pathlib; ast.parse(pathlib.Path('app/services/ai/runners/knowledge_agent_runner.py').read_text(encoding='utf-8')); print('syntax ok')"
```
Expected: `syntax ok`

- [x] **Step 7: 检查点（不提交）**

汇报：新增测试文件、helper 方法、两处变量/发出改动。建议 commit message 草稿：

```
feat(knowledge): 预检索后推送「检索完成，正在组织回答」状态项
```

---

## Task 2: 网关重试循环按 content 分流 + `saw_error` 标志位

**Files:**
- Modify: `tests/test_knowledge_streaming_contract.py`（追加 2 条契约）
- Modify: `app/services/ai/runners/knowledge_agent_runner.py:921`（加 `saw_error`）、`:934-949`（替换循环体）

- [x] **Step 1: 追加失败测试**

在 `tests/test_knowledge_streaming_contract.py` 末尾追加：

```python
def test_only_content_chunks_are_buffered_and_process_events_forwarded():
    loop = _model_execution_loop_source()

    content_branch = loop.index('if "content" in chunk:')
    else_branch = loop.index("else:", content_branch)
    assert loop.count("chunks_buffer.append(chunk)") == 1, "缓冲区只允许装正文"
    assert loop.index("chunks_buffer.append(chunk)") > content_branch
    assert "yield chunk" not in loop[content_branch:else_branch], (
        "正文不得直接 yield：网关判定前对用户不可见是安全语义"
    )
    assert "yield chunk" in loop[else_branch:], "过程事件必须实时转发"


def test_error_chunks_use_flag_instead_of_buffer_scan():
    source = _read(KNOWLEDGE_RUNNER)
    loop = _model_execution_loop_source()

    assert "saw_error = True" in loop
    assert "if saw_error:" in source
    # error 判定必须在分流之前：带 content 的错误事件（安全拦截）也要被识别
    assert loop.index("saw_error = True") < loop.index('if "content" in chunk:')
    assert 'any(chunk.get("type") == "error" for chunk in chunks_buffer)' not in source, (
        "error chunk 不再进缓冲区，扫缓冲区找 error 会永久失效"
    )
```

- [x] **Step 2: 运行测试确认失败**

Run:
```bash
.venv/bin/python -m pytest tests/test_knowledge_streaming_contract.py -v
```
Expected: `2 failed, 3 passed`。注意 `_model_execution_loop_source()` 现在会抛 `ValueError: substring not found`（`if saw_error:` 还不存在），两条新契约因此报错即失败。

- [x] **Step 3: 实现分流**

3a. 在 `while retry_count <= max_retries:` 循环体的 `chunks_buffer = []` 下一行（约 `:921`）加：

```python
        while retry_count <= max_retries:
            chunks_buffer = []
            full_text = ""
            # 终止信号标志位：error chunk 不再进缓冲区，无法靠扫缓冲区识别。
            saw_error = False
```

3b. 把 `:934-949` 整段（`async for chunk in ...` 到 `return`）替换为：

```python
            async for chunk in self._execute_with_agentscope_native_agent(**execute_kwargs):
                # 错误事件可能是安全拦截，且**携带 content**；若只在不带 content 的
                # 分支里识别，这类错误会被漏判而进入幻觉评估，把拦截误判成幻觉。
                if chunk.get("type") == "error":
                    saw_error = True
                if "content" in chunk:
                    content = str(chunk.get("content") or "")
                    if self._valid_citation_ids:
                        content = filter_invalid_citation_markers(content, self._valid_citation_ids)
                    full_text += content
                    chunk = dict(chunk)
                    chunk["content"] = content
                    chunks_buffer.append(chunk)
                else:
                    # 过程性事件（model_call / log / reasoning_content / meta 等）实时转发。
                    # 此前它们与正文一起进缓冲区，用户在模型生成期间收不到任何事件，
                    # 思考卡片停在「工具完成: search_knowledge_base」上，看起来像卡死。
                    yield chunk

            if saw_error:
                # 错误事件是终止信号，不能进入知识库事实评估/反思循环，
                # 否则会把安全拦截误判成幻觉并覆盖原始错误。
                for chunk in chunks_buffer:
                    yield chunk
                return
```

- [x] **Step 4: 运行测试确认通过**

Run:
```bash
.venv/bin/python -m pytest tests/test_knowledge_streaming_contract.py -v
```
Expected: `5 passed`

- [x] **Step 5: 确认 flush 逻辑未被破坏**

Run:
```bash
.venv/bin/python -m pytest tests/test_knowledge_streaming_contract.py -v -k "error or buffered"
grep -n "for chunk in chunks_buffer:" app/services/ai/runners/knowledge_agent_runner.py
```
Expected: 测试通过；`grep` 输出 3 处命中（`if saw_error:` 分支 1 处、`if passed_guard:` 1 处、`else:` 1 处），即 flush 仍是「只遍历缓冲区」的既有写法。

- [x] **Step 6: 检查点（不提交）**

建议 commit message 草稿：

```
fix(knowledge): 网关重试循环按 content 分流，过程事件实时转发
```

---

## Task 3: 首个正文 chunk 收尾状态项

**Files:**
- Modify: `tests/test_knowledge_streaming_contract.py`（追加 2 条契约）
- Modify: `app/services/ai/runners/knowledge_agent_runner.py`（正文分支内、`chunks_buffer.append(chunk)` 之后）

- [x] **Step 1: 追加失败测试**

在 `tests/test_knowledge_streaming_contract.py` 末尾追加：

```python
def test_first_content_chunk_closes_knowledge_synthesis_item():
    loop = _model_execution_loop_source()

    content_branch = loop.index('if "content" in chunk:')
    else_branch = loop.index("else:", content_branch)
    body = loop[content_branch:else_branch]
    assert "if synthesis_pending:" in body, "收尾必须在正文分支内（首个正文 chunk 处）"
    assert body.index("if synthesis_pending:") > body.index("chunks_buffer.append(chunk)")
    assert 'status="success"' in body
    assert "synthesis_pending = False" in body, "收尾后必须清标志，重试轮不得重复关闭"


def test_synthesis_closing_is_not_tied_to_flush():
    source = _read(KNOWLEDGE_RUNNER)

    flush = source.index("if passed_guard:")
    assert 'status="success"' not in source[flush:], (
        "flush 发生在网关判定之后，那时才收尾会让卡片再次停住"
    )
```

- [x] **Step 2: 运行测试确认失败**

Run:
```bash
.venv/bin/python -m pytest tests/test_knowledge_streaming_contract.py -v
```
Expected: `1 failed, 6 passed`（`test_first_content_chunk_closes_knowledge_synthesis_item` 失败；`test_synthesis_closing_is_not_tied_to_flush` 此时已通过——它锁的是「不要做什么」，属于防回归护栏，Task 6 的变异验证会证明它有效）。

- [x] **Step 3: 实现收尾**

在正文分支的 `chunks_buffer.append(chunk)` 之后（同一个 `if "content" in chunk:` 块内）加：

```python
                    if synthesis_pending:
                        # 首个正文 chunk 到达即收尾：此时 model_call 的 pending 项已经
                        # 实时转发出来，卡片能无缝接上「模型调用: <模型名> · 进行中」；
                        # 若等 flush 才收尾，中间会出现「状态项已完成 + 模型调用已完成
                        # + 正文未到达」的空档，卡片会再次停住。
                        synthesis_pending = False
                        yield self._knowledge_synthesis_event(
                            status="success",
                            details=knowledge_synthesis_details,
                        )
```

- [x] **Step 4: 运行测试确认通过**

Run:
```bash
.venv/bin/python -m pytest tests/test_knowledge_streaming_contract.py -v
```
Expected: `7 passed`

- [x] **Step 5: 检查点（不提交）**

建议 commit message 草稿：

```
feat(knowledge): 首个正文 chunk 到达时收尾「正在组织回答」状态项
```

---

## Task 4: 落库收尾覆盖 `knowledge` 类别

**Files:**
- Modify: `tests/test_knowledge_streaming_contract.py`（追加 1 条契约）
- Modify: `app/services/ai/runtime/agentscope/process_timeline_snapshot.py:612`

- [x] **Step 1: 追加失败测试**

在 `tests/test_knowledge_streaming_contract.py` 末尾追加：

```python
def test_persisted_timeline_closes_pending_for_model_and_knowledge_only():
    source = _read(TIMELINE_SNAPSHOT)
    finalize = source[source.index("def finalize_process_timeline(") :]

    assert 'copied.get("category") in ("model", "knowledge")' in source, (
        "knowledge 状态项若异常中断仍是 pending，历史回放会永远显示「进行中」"
    )
    assert '"permission"' not in finalize, "等待用户操作的类别必须保持待处理"
    assert '"external"' not in finalize, "等待外部执行的类别必须保持待处理"
```

- [x] **Step 2: 运行测试确认失败**

Run:
```bash
.venv/bin/python -m pytest tests/test_knowledge_streaming_contract.py -v
```
Expected: `1 failed, 7 passed`

- [x] **Step 3: 实现收尾扩展**

把 `app/services/ai/runtime/agentscope/process_timeline_snapshot.py:612` 的：

```python
            if copied.get("status") == "pending" and copied.get("category") == "model":
```

改为：

```python
            # knowledge 是「检索完成、正在组织回答」的过程项，异常中断时若仍留 pending，
            # 历史回放会永远显示「进行中」。permission / external 是「等待用户操作、
            # 机器没在跑」的类别（对应前端 NON_LIVE_TIMER_CATEGORIES），必须保持待处理，
            # 故只按需扩展，不写成"收尾所有 pending"。
            if copied.get("status") == "pending" and copied.get("category") in ("model", "knowledge"):
```

- [x] **Step 4: 运行测试确认通过**

Run:
```bash
.venv/bin/python -m pytest tests/test_knowledge_streaming_contract.py -v
```
Expected: `8 passed`

- [x] **Step 5: 跑落库快照的既有测试防回归**

Run:
```bash
.venv/bin/python -m pytest tests -k "process_timeline or timeline_snapshot" -q
```
Expected: 全部 passed（无 failed / error）。若该 `-k` 未匹配到任何用例，改用：
```bash
grep -rl "finalize_process_timeline" tests/ | head
```
并对列出的文件逐个运行。

- [x] **Step 6: 检查点（不提交）**

建议 commit message 草稿：

```
fix(knowledge): 落库收尾覆盖 knowledge 类别的 pending 项
```

---

## Task 5: 前端防回退契约

后端改动依赖前端三项既有能力，本任务用契约锁住它们，防止后续重构把 `knowledge` 类别摘掉导致现象回退。**前端源码零改动。**

**Files:**
- Create: `tests/frontend/test_knowledge_streaming_contract.py`

- [x] **Step 1: 写契约测试**

创建 `tests/frontend/test_knowledge_streaming_contract.py`：

```python
"""契约：知识库问答的过程状态项必须能显示为「进行中」并带实时秒表。

背景：后端新增 `category: "knowledge"` 的 pending 状态项「检索完成，正在组织回答」，
用于填补「检索已结束、模型正文尚未到达」的空档。若前端把该类别从超时保护名单摘掉，
异常中断的条目会永远显示「进行中」；若把它放进 NON_LIVE_TIMER 名单，则会失去秒表
（用户正需要「已经等了多久」这个信号）。本文件不测新功能，只防止这三项既有能力回退。
"""

from pathlib import Path

import pytest

pytestmark = pytest.mark.no_infrastructure

ROOT = Path(__file__).resolve().parents[2]
PROCESS_TIMELINE = ROOT / "frontend/src/utils/processTimeline.ts"
SSE_HANDLERS = ROOT / "frontend/src/utils/agentscopeSseHandlers.ts"
EMBED = ROOT / "frontend/src/views/EmbedChat.vue"


def _read(path: Path) -> str:
    return path.read_text(encoding="utf-8")


def test_knowledge_category_is_protected_from_stale_pending():
    source = _read(SSE_HANDLERS)

    line = next(
        (row for row in source.splitlines() if "STALE_PENDING_CATEGORIES" in row and "new Set(" in row),
        "",
    )
    assert line, "必须保留 STALE_PENDING_CATEGORIES 定义"
    assert '"knowledge"' in line, "knowledge 挂起项必须有超时保护，否则永远转圈"


def test_knowledge_category_shows_live_timer():
    """knowledge 是「机器正在跑」的类别，必须显示实时秒表。"""
    source = _read(PROCESS_TIMELINE)
    block = source[source.index("NON_LIVE_TIMER_CATEGORIES"):]
    block = block[: block.index("]);")]

    assert '"knowledge"' not in block, "knowledge 不得进入「等待用户操作」名单"
    assert '"permission"' in block and '"external"' in block, "该名单只装等待用户操作的类别"


def test_embed_dispatches_agentscope_events_for_process_items():
    """过程事件实时化后，「模型调用: <模型名>」pending 项由该分发器渲染。"""
    embed = _read(EMBED)
    handlers = _read(SSE_HANDLERS)

    assert embed.count("dispatchAgentscopeStreamEvent(") >= 3, "EmbedChat 的三处流式入口都要分发"
    assert 'case "model_call":' in handlers
    assert "handleModelCallEvent" in handlers
```

- [x] **Step 2: 运行测试确认通过**

这三个能力**当前都已存在**，因此本任务是一次「先确认契约成立」的动作，预期直接全绿。

Run:
```bash
.venv/bin/python -m pytest --confcutdir=tests/frontend tests/frontend/test_knowledge_streaming_contract.py -v
```
Expected: `3 passed`

- [x] **Step 3: 做一次定向变异，证明契约不是假绿**

把 `frontend/src/utils/agentscopeSseHandlers.ts:388` 的
`const STALE_PENDING_CATEGORIES = new Set(["model", "agent", "tool", "sql", "knowledge", "default"]);`
临时改为去掉 `"knowledge"` 的版本（先备份原行）：

Run:
```bash
cp frontend/src/utils/agentscopeSseHandlers.ts /tmp/sse_handlers_backup.ts
python3 - <<'PY'
import pathlib
p = pathlib.Path("frontend/src/utils/agentscopeSseHandlers.ts")
s = p.read_text(encoding="utf-8")
old = 'const STALE_PENDING_CATEGORIES = new Set(["model", "agent", "tool", "sql", "knowledge", "default"]);'
new = 'const STALE_PENDING_CATEGORIES = new Set(["model", "agent", "tool", "sql", "default"]);'
assert old in s, "未找到目标行，先核对文件是否已变"
p.write_text(s.replace(old, new), encoding="utf-8")
PY
.venv/bin/python -m pytest --confcutdir=tests/frontend tests/frontend/test_knowledge_streaming_contract.py -v
cp /tmp/sse_handlers_backup.ts frontend/src/utils/agentscopeSseHandlers.ts
```
Expected: 变异后被 `test_knowledge_category_is_protected_from_stale_pending` 捕获（`1 failed, 2 passed`）；还原后再跑一次恢复 `3 passed`。

- [x] **Step 4: 确认源码已完整还原**

Run:
```bash
git diff --stat frontend/src/utils/agentscopeSseHandlers.ts
.venv/bin/python -m pytest --confcutdir=tests/frontend tests/frontend/test_knowledge_streaming_contract.py -q
```
Expected: `git diff --stat` 对该文件**无输出**（已还原）；测试 `3 passed`。

- [x] **Step 5: 检查点（不提交）**

建议 commit message 草稿：

```
test(knowledge): 加前端防回退契约，锁住 knowledge 类别的秒表与超时保护
```

---

## Task 6: 变异验证、CHECKLIST 与全量回归

**Files:**
- Modify: `tests/CHECKLIST.md`（表格第 6 行之前插入新行）
- Verify only: 全部改动文件

- [x] **Step 1: 备份两个源文件（变异验证用）**

Run:
```bash
cp app/services/ai/runners/knowledge_agent_runner.py /tmp/knowledge_runner_backup.py
cp app/services/ai/runtime/agentscope/process_timeline_snapshot.py /tmp/timeline_snapshot_backup.py
ls -l /tmp/knowledge_runner_backup.py /tmp/timeline_snapshot_backup.py
```
Expected: 两个文件都存在。

> 用 `cp` 备份而**不用 `git checkout --`**：工作区含本次未提交改动，`git checkout` 会一并丢弃。

- [x] **Step 2: 逐条执行 6 项变异，确认每项都被捕获**

每条变异后运行 `bash .venv/bin/python -m pytest tests/test_knowledge_streaming_contract.py -q`，记录结果，然后立刻从备份还原。

| # | 变异 | 捕获它的契约 | 期望结果 |
| --- | --- | --- | --- |
| M1 | 把 `else:` 分支的 `yield chunk` 改成 `chunks_buffer.append(chunk)` | `test_only_content_chunks_are_buffered_and_process_events_forwarded` | failed |
| M2 | 删掉 `saw_error = True`，把 `if saw_error:` 换成 `if any(chunk.get("type") == "error" for chunk in chunks_buffer):` | `test_error_chunks_use_flag_instead_of_buffer_scan` | failed |
| M3 | 删掉 `yield self._knowledge_synthesis_event(status="pending", details=knowledge_synthesis_details)` 一行 | `test_prefetch_emits_knowledge_synthesis_pending_after_service_check` | failed |
| M4 | 删掉正文分支里的整个 `if synthesis_pending:` 块 | `test_first_content_chunk_closes_knowledge_synthesis_item` | failed |
| M5 | 把 `if synthesis_pending:` 块从正文分支移到 `if passed_guard:` 之后 | `test_first_content_chunk_closes_knowledge_synthesis_item` + `test_synthesis_closing_is_not_tied_to_flush` | failed（至少一条） |
| M6 | 把 `copied.get("category") in ("model", "knowledge")` 改回 `== "model"` | `test_persisted_timeline_closes_pending_for_model_and_knowledge_only` | failed |

M1 示例命令（其余按同法替换 `old` / `new`）：
```bash
python3 - <<'PY'
import pathlib
p = pathlib.Path("app/services/ai/runners/knowledge_agent_runner.py")
s = p.read_text(encoding="utf-8")
old = "                    yield chunk\n\n            if saw_error:"
new = "                    chunks_buffer.append(chunk)\n\n            if saw_error:"
assert old in s, "未找到目标片段"
p.write_text(s.replace(old, new), encoding="utf-8")
PY
.venv/bin/python -m pytest tests/test_knowledge_streaming_contract.py -q
cp /tmp/knowledge_runner_backup.py app/services/ai/runners/knowledge_agent_runner.py
```

M5 的变异做法：先删正文分支内的 `if synthesis_pending:` 块，再在 `if passed_guard:` 之后插入同块（缩进改为 8 空格）。

Expected: 6 项全部被捕获；每项还原后 `bash .venv/bin/python -m pytest tests/test_knowledge_streaming_contract.py -q` 恢复 `8 passed`。

- [x] **Step 3: 确认两个源文件与备份逐字节一致**

Run:
```bash
diff /tmp/knowledge_runner_backup.py app/services/ai/runners/knowledge_agent_runner.py && echo "runner 已还原"
diff /tmp/timeline_snapshot_backup.py app/services/ai/runtime/agentscope/process_timeline_snapshot.py && echo "snapshot 已还原"
```
Expected: 两行 `已还原`，`diff` 无输出。

- [x] **Step 4: 更新 `tests/CHECKLIST.md`**

在 `tests/CHECKLIST.md` 的表格分隔行（`| 特性 / 修复项 | 涉及核心文件 | ...` 的下一行 `| --- | ... |`）之后、现有第一行数据行（「知识库引用详情刷新后丢失」）**之前**，插入新行：

```markdown
| 知识库问答「检索完成后空档」修复：网关缓冲期间实时转发过程事件 + 语义化状态项 (Knowledge Streaming Progress) | `app/services/ai/runners/knowledge_agent_runner.py`、`app/services/ai/runtime/agentscope/process_timeline_snapshot.py`、`tests/test_knowledge_streaming_contract.py`（新增）、`tests/frontend/test_knowledge_streaming_contract.py`（新增）、`docs/superpowers/specs/2026-10-10-knowledge-streaming-progress-design.md`（新增）、`docs/superpowers/plans/2026-10-10-knowledge-streaming-progress.md`（新增）、`tests/CHECKLIST.md` | **用户反馈**：知识库提问后思考卡片停在「工具完成: search_knowledge_base」，之后数秒到十几秒没有任何进展信号，随后答案整段一次性出现，问「检索已完成、AI 还没输出之前这段时间在干嘛」。**根因（已核实）**：`knowledge_agent_runner.py:934-942` 的网关重试循环把**所有** chunk 都 `append` 进 `chunks_buffer` 且不 `yield`，直到幻觉网关判定通过才在 `:1051` 一次性 flush——模型生成全程前端零事件；而「工具完成: search_knowledge_base」是预检索阶段直接 `yield` 的（`:445-457`），因此成了前端收到的最后一个事件。之所以要缓冲，是因为网关需要完整回答文本才能判定幻觉，正文提前露出就收不回来。**方案（用户选定「A + 知识库专属文案」）**：不改安全语义（正文仍在网关通过后才可见），只让**过程事件**重新流动——循环内按 `"content" in chunk` 分流，携带正文的进缓冲区，其余（`model_call` / `log` / `reasoning_content` / `meta` / `context_*`）实时 `yield`；切分依据用「是否携带 content」而非 type 白名单，因为白名单需随上游事件演进维护、容易漏，而 content 是稳定的结构性判据；缓冲区语义随之收窄为「只装正文」，与实时转发部分互斥不重叠，`:1051` 的 flush 逻辑本身无需改动。**连带必修一处**：`:944` 原本靠 `any(chunk.get("type") == "error" for chunk in chunks_buffer)` 识别终止信号，而 error chunk 不带 content、改为实时转发后不再进缓冲区，该判断会**永久失效**——代码注释明确写了错误事件若进入事实评估会被「把安全拦截误判成幻觉并覆盖原始错误」。改为循环内 `saw_error` 标志位；且判定**前置到分流之前**（error 事件可能携带 content，安全拦截走的正是这条），保证两个分支都能置位。**语义化状态项**：预检索完成、服务可用性检查通过后发出 `id: "knowledge_synthesis"` / `title: "检索完成，正在组织回答"` / `category: "knowledge"` / `status: "pending"`，details 按有无引用分流（`已获取 N 条资料，正在生成回答…` / `未检索到相关资料，正在组织回复…`，0 条时不得谎报「已获取 0 条资料」）；在**首个正文 chunk 到达时**收尾——选这个时机而非 flush 之后，是因为 flush 在网关判定结束后才发生，那时才收尾会让卡片再次停住；收尾用单个 `synthesis_pending` 标志（发出置 `True`、收尾置 `False`）而非 spec 初稿的 `synthesis_closed`，因为追问复用上下文等路径**不发**状态项却同样走到循环，只用「已收尾」标志会凭空发出没有对应 pending 的 success 事件。**落库收尾**：`finalize_process_timeline`（`process_timeline_snapshot.py:610-615`）的 pending 收尾条件由 `== "model"` 扩为 `in ("model", "knowledge")`，否则异常中断的状态项会让历史回放永远显示「进行中」；**明确不写成"收尾所有 pending"**——`permission` / `external` 是「等待用户操作、机器没在跑」的类别（对应前端 `NON_LIVE_TIMER_CATEGORIES`），必须保持待处理。**前端零改动**：`model_call` 事件的分发（`EmbedChat.vue:2473`、调用点 3 处、`agentscopeSseHandlers.ts:909`）、`log` 事件的按类别写入、卡片头部反向优先取 pending 项（`processTimeline.ts`）、流结束 `finalizeAllPendingStreamLogs` 全部既有且可用——这套能力本来就通，只是被缓冲整体挡住了。**已知行为变化**：每轮重试开始时 `chunks_buffer = []` 会丢弃上一轮 chunk，因此现状下重试轮的 token 不计入；改为实时转发后重试轮 token 会照常累加，页面 token 数会比过去**偏大**——这是更准确的口径（重试确实消耗了 token），属有意接受的行为变化。**验证**：后端契约 **8 项**（`tests/test_knowledge_streaming_contract.py`，分 3 次 TDD 增量追加：先 RED 3 failed → 实现 → GREEN，再 RED 2 failed → 实现 → GREEN，再 RED 1 failed → 实现 → GREEN，落库契约 RED 1 failed → GREEN）；前端契约 **3 项**（`tests/frontend/test_knowledge_streaming_contract.py`，锁 knowledge 类别的超时保护、实时秒表、以及 EmbedChat 三处流式入口仍分发 AgentsScope 事件）；**变异验证 6/6 全部被捕获**（过程事件改回缓冲、error 判定改回扫缓冲区、去掉状态项发出、去掉收尾块、收尾时机挪到 flush 之后、落库收尾改回只认 model），每项还原后 8 passed，并用 `diff` 证明两个源文件与备份逐字节一致；未跑 `./dev.sh`、未执行 git commit。**人工验证（需用户在控制台跑 `./dev.sh` 后执行）**：① 知识库提问 → 检索后立刻出现「检索完成，正在组织回答 · 进行中 Ns」；② 紧接着出现「模型调用: \<模型名\> · 进行中」（证明过程事件已实时流动）；③ 答案出现后两项均已收尾、无残留转圈；④ 问一个知识库无相关资料的问题 → 状态项文案为「未检索到相关资料，正在组织回复…」；⑤ 刷新页面看历史回放，上述条目都在且**没有任何一项停在「进行中」**。 | ✅ 过程事件实时转发（网关安全语义不变）、error 改标志位且前置判定、预检索后发出知识库专属状态项并在首个正文 chunk 收尾、落库收尾覆盖 knowledge；后端契约 8 项 + 前端契约 3 项，变异 6/6 捕获 | 2026-10-10 |
```

- [x] **Step 5: 跑新增契约全量**

Run:
```bash
.venv/bin/python -m pytest tests/test_knowledge_streaming_contract.py tests/test_knowledge_citation_store.py tests/test_knowledge_citation_history_contract.py -q
.venv/bin/python -m pytest --confcutdir=tests/frontend tests/frontend/test_knowledge_streaming_contract.py tests/frontend/test_knowledge_citation_history_contract.py -q
```
Expected: 后端 `19 passed`（8 + 7 + 4，引用相关既有 11 项）与前端 `11 passed`（3 + 8）。具体数字以实际输出为准，**要求 0 failed / 0 error**。

- [x] **Step 6: 跑知识库链路的既有测试防回归**

Run:
```bash
.venv/bin/python -m pytest tests -k "knowledge" -q
```
Expected: 0 failed / 0 error。若有既有失败，先确认是否为改动引入：
```bash
git stash list
git diff --stat app/services/ai/runners/knowledge_agent_runner.py app/services/ai/runtime/agentscope/process_timeline_snapshot.py
```
并对失败用例逐个判断（本改动只影响 `KnowledgeAgentRunner` 与 timeline 落库，其余失败应能在 `main` 上复现）。

- [x] **Step 7: 前端类型检查（无源码改动，仅确认未误伤）**

Run:
```bash
cd frontend && NODE_OPTIONS="--max-old-space-size=4096" ./node_modules/.bin/vue-tsc -b --pretty false 2>&1 | tail -5
```
Expected: 与基线一致（基线 62 条），不得新增。

- [x] **Step 8: 检查点（不提交）**

汇报：改动文件清单、8 + 3 条契约、变异 6/6 结果、CHECKLIST 已更新、全量测试结果。建议 commit message：

```
feat(knowledge): 网关缓冲期间实时转发过程事件并加「正在组织回答」状态项
```

---

## Self-Review

**1. Spec 覆盖检查**

| spec 节 | 落点 |
| --- | --- |
| §5.1 按 content 分流 | Task 2 Step 3（含修正 1：error 判定前置） |
| §5.2 buffer 收窄与 `saw_error` | Task 2 Step 1 契约、Step 3 实现 |
| §5.3 状态项 id/title/details/category | Task 1 Step 3、Step 4 |
| §5.3 首个正文 chunk 收尾 | Task 3（含修正 2：`synthesis_pending`） |
| §5.4 落库收尾 `("model", "knowledge")` 且不含 permission/external | Task 4 |
| §5.5 前端零改动 | Task 5（只加防回退契约，Step 1 明确「前端源码零改动」） |
| §6 错误更早可见 / 跨重试 / 0 引用文案 / token 计数变化 | Task 1 Step 5 契约、Task 2 实现、Task 6 Step 4 CHECKLIST |
| §7 后端 6 条契约 | Task 1(3) + Task 2(2) + Task 3(2) + Task 4(1) = 8 条（spec 列的 6 条要项全覆盖，另拆出文案与收尾时机 2 条） |
| §7 变异 6 项 | Task 6 Step 2 |
| §7 前端契约 1 条 | Task 5（拆为 3 条：超时保护 / 秒表 / 分发入口） |
| §7 人工验证 5 步 | Task 6 Step 4 CHECKLIST 末尾 |
| §8 无迁移/无依赖/可回滚 | 全程未涉及迁移与依赖；回滚即还原两个源文件 |
| §9 文件清单 | 「文件结构」表逐项对应 |

**2. 占位符扫描**：无 TBD / TODO / 「类似 Task N」/「适当处理」；每个改代码的步骤都给出了完整可粘贴代码与精确插入位置。

**3. 类型与命名一致性**：`_knowledge_synthesis_event(status, details)` 在 Task 1 定义、Task 1/3 调用；`synthesis_pending` 在 Task 1 Step 4a 定义、Task 3 使用；`knowledge_synthesis_details` 在 Task 1 Step 4a 定义、Task 1/3 使用；`saw_error` 在 Task 2 定义与判断；契约中的 `MODEL_EVENT_LOOP`、`SYNTHESIS_PENDING_CALL` 常量在 Task 1 定义、Task 2/3/4 复用，且与实现代码逐字一致（`SYNTHESIS_PENDING_CALL` 要求实现写成单行）。
