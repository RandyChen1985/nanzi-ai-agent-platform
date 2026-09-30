# Langfuse 直调链路统一出口埋点实施计划

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 让全部直调调用点（handle 的 24 处 + 直接使用 chat client 的 26 处，合计 50 处）进入 Langfuse，业务代码零改动。

**Architecture:** 在唯一出口 `AgentScopeChatClient` 的 4 个方法上包裹 generation span。span 归属交给 OTel 自然语义：轮次内自动挂进该 trace，独立触发自成一条 trace。属性全部复用一期已实现但从未调用的 `generation_attributes()` / `extract_usage()`。

**Tech Stack:** Python 3.11 / OpenTelemetry API / langfuse SDK 4.15.6 / pytest。

**设计文档：** `docs/superpowers/specs/2026-09-30-langfuse-direct-llm-tracing-design.md`

**测试命令约定：** `PYTHONPATH=. .venv/bin/python -m pytest <path> -q`

**本计划不执行 `git commit`**（项目规范：提交须由用户明确要求）。

---

## 文件结构

| 文件 | 职责 | 动作 |
|---|---|---|
| `app/services/ai/observability/span_attributes.py` | 属性构造 | 修改：拆出 `identity_attributes()` |
| `app/services/ai/observability/generation_span.py` | 一次模型调用 → 一个 span | **新增** |
| `app/services/ai/runtime/agentscope/chat.py` | 唯一出口，4 个方法埋点 | 修改 |
| `app/core/llm/client.py` | `source` 标签的声明与透传 | 修改 |
| `tests/services/ai/observability/test_generation_span.py` | span 单元测试 | **新增** |
| `tests/ai/runtime/test_agentscope_chat_tracing.py` | 4 个方法的埋点与行为对照 | **新增** |

## 关键常量（后续任务统一引用）

| 常量 | 值 |
|---|---|
| `TRACER_NAME` | `"nanzi.llm"`（已在 `manager.ALLOWED_SCOPE_NAMES` 白名单内） |
| `SPAN_NAME` | `"nanzi.llm.call"` |
| `SOURCE_ATTRIBUTE` | `"nanzi.llm.source"` |
| `STREAMING_ATTRIBUTE` | `"nanzi.llm.streaming"` |
| `FIRST_CHUNK_ATTRIBUTE` | `"nanzi.llm.first_chunk_ms"` |
| `STRUCTURED_STATUS_ATTRIBUTE` | `"nanzi.llm.structured_status"` |
| `ERROR_TYPE_ATTRIBUTE` | `"error.type"` |

---

## Task 1: 拆出 `identity_attributes()`

目的：让直调 span 能取 `user.id` / `session.id`，**而不取** `langfuse.trace.name`（取它会改写整条轮次 trace 的名称）。

**Files:**
- Modify: `app/services/ai/observability/span_attributes.py:87-115`
- Test: `tests/services/ai/observability/test_span_attributes.py`（既有，仅追加）

- [ ] **Step 1: 写失败测试**

在 `tests/services/ai/observability/test_span_attributes.py` 末尾追加（该文件已用 `from app.services.ai.observability import span_attributes as sa` 导入，并已设 `pytestmark = pytest.mark.no_infrastructure`）：

```python
def test_identity_attributes_excludes_trace_name():
    """identity_attributes 只写身份与 metadata，绝不写 trace name。

    直调 span 在轮次内也会调用它；若这里带上 langfuse.trace.name，
    整条轮次 trace 的名字会被改写。
    """
    attrs = sa.identity_attributes(
        user_id=7,
        conversation_id="conv-1",
        extra={"llm_source": "chatbi.sql"},
    )
    assert attrs[sa.TRACE_USER_ID] == "7"
    assert attrs[sa.TRACE_SESSION_ID] == "conv-1"
    assert attrs["langfuse.trace.metadata.llm_source"] == "chatbi.sql"
    assert sa.TRACE_NAME not in attrs


def test_identity_attributes_keeps_trace_id_in_metadata():
    attrs = sa.identity_attributes(trace_id="t-9")
    assert attrs["langfuse.trace.metadata.trace_id"] == "t-9"
    assert sa.TRACE_NAME not in attrs


def test_trace_attributes_still_writes_trace_name():
    """行为回归：拆分后 trace_attributes 的输出必须与拆分前完全一致。"""
    attrs = sa.trace_attributes(
        user_id="u1", conversation_id="c1", agent_name="数据助手", trace_id="t1"
    )
    assert attrs[sa.TRACE_USER_ID] == "u1"
    assert attrs[sa.TRACE_SESSION_ID] == "c1"
    assert attrs[sa.TRACE_NAME] == "数据助手 对话"
    assert attrs["langfuse.trace.metadata.trace_id"] == "t1"

    plain = sa.trace_attributes()
    assert plain[sa.TRACE_NAME] == "对话"
```

- [ ] **Step 2: 跑测试确认失败**

Run: `PYTHONPATH=. .venv/bin/python -m pytest tests/services/ai/observability/test_span_attributes.py -q -k "identity_attributes or still_writes"`
Expected: FAIL —— `AttributeError: module ... has no attribute 'identity_attributes'`

- [ ] **Step 3: 实现**

把 `span_attributes.py` 中现有的 `trace_attributes` 函数整体替换为下面两个函数：

```python
def identity_attributes(
    *,
    user_id: Optional[str] = None,
    conversation_id: Optional[str] = None,
    trace_id: Optional[str] = None,
    extra: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    """构造身份属性（user / session + metadata），**不含** trace name。

    直调链路的 span 也用它：轮次内的直调若写 ``langfuse.trace.name``，
    会把整条轮次 trace 改名，因此 trace name 只由根 span 负责。
    """
    attributes: Dict[str, Any] = {}

    if user_id and str(user_id).strip():
        attributes[TRACE_USER_ID] = str(user_id).strip()
    if conversation_id and str(conversation_id).strip():
        attributes[TRACE_SESSION_ID] = str(conversation_id).strip()

    metadata: Dict[str, Any] = {}
    if trace_id and str(trace_id).strip():
        metadata["trace_id"] = str(trace_id).strip()
    for key, value in (extra or {}).items():
        if value is None or value == "":
            continue
        metadata[key] = value
    attributes.update(_metadata_attributes(metadata))

    return attributes


def trace_attributes(
    *,
    user_id: Optional[str] = None,
    conversation_id: Optional[str] = None,
    agent_name: Optional[str] = None,
    trace_id: Optional[str] = None,
    extra: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    """构造 trace 维度属性（写在一轮对话的根 span 上）。"""
    attributes = identity_attributes(
        user_id=user_id,
        conversation_id=conversation_id,
        trace_id=trace_id,
        extra=extra,
    )

    name = (agent_name or "").strip()
    attributes[TRACE_NAME] = f"{name} 对话" if name else "对话"

    return attributes
```

- [ ] **Step 4: 跑测试确认通过，并确认既有用例零回归**

Run: `PYTHONPATH=. .venv/bin/python -m pytest tests/services/ai/observability/test_span_attributes.py tests/services/ai/observability/ -q`
Expected: PASS，且既有 `test_manager.py` / `test_settings.py` 全绿（`turn_span` 依赖 `trace_attributes`）

---

## Task 2: `generation_span.py` 骨架与零开销路径

**Files:**
- Create: `app/services/ai/observability/generation_span.py`
- Test: `tests/services/ai/observability/test_generation_span.py`

- [ ] **Step 1: 写失败测试**

创建 `tests/services/ai/observability/test_generation_span.py`：

```python
"""直调 generation span 的单元测试：零开销、属性映射、累积、收尾。

不构造真实 Langfuse client（会注册全局 TracerProvider），全部用假 tracer 注入。
"""

import pkgutil
import types

import pytest

from app.services.ai.observability import generation_span, manager, settings

pytestmark = pytest.mark.no_infrastructure


@pytest.fixture(autouse=True)
def _reset():
    manager.reset_for_tests()
    settings.reset_for_tests()
    yield
    manager.reset_for_tests()
    settings.reset_for_tests()
    generation_span.reset_for_tests()


class _RecordingSpan:
    def __init__(self, name, attributes):
        self.name = name
        self.attributes = dict(attributes or {})
        self.ended = 0
        self.context = types.SimpleNamespace(trace_id=0xAB * 16)

    def set_attribute(self, key, value):
        self.attributes[key] = value

    def get_span_context(self):
        return self.context

    def end(self):
        self.ended += 1


class _RecordingTracer:
    def __init__(self):
        self.spans = []

    def start_span(self, name, attributes=None):
        span = _RecordingSpan(name, attributes)
        self.spans.append(span)
        return span


class _OccupiedSpan:
    """模拟"当前已有活跃 span"（即处于某个轮次内）。"""

    def get_span_context(self):
        return types.SimpleNamespace(is_valid=True)


def _enable_tracing(monkeypatch):
    monkeypatch.setattr(generation_span, "is_tracing_enabled", lambda: True)


async def test_no_span_when_tracing_disabled(monkeypatch):
    """追踪关闭时必须零 span 创建（冷启动零开销的依据）。"""
    tracer = _RecordingTracer()
    monkeypatch.setattr(generation_span, "_get_tracer", lambda: tracer)
    monkeypatch.setattr(generation_span, "is_tracing_enabled", lambda: False)

    async with generation_span.traced_generation(
        model="gpt-x", streaming=False, source=None, input_messages=[_user_message()]
    ) as span:
        assert span.active is False
        span.record_output("unused")
        span.end(error=RuntimeError("ignored"))

    assert tracer.spans == []
```

- [ ] **Step 2: 跑测试确认失败**

Run: `PYTHONPATH=. .venv/bin/python -m pytest tests/services/ai/observability/test_generation_span.py -q`
Expected: FAIL —— `ModuleNotFoundError: No module named 'app.services.ai.observability.generation_span'`

- [ ] **Step 3: 实现骨架**

创建 `app/services/ai/observability/generation_span.py`：

```python
"""直调链路的 generation span：把一次模型调用变成 Langfuse 可看的观测。

埋点位置是 ``AgentScopeChatClient`` 的四个方法——它是平台所有直调（含经
``AgentScopeLLMHandle`` 的与直接使用 chat client 的）的唯一出口。

与 ``turn_span`` 的关键差异：本模块只发 generation 级 span，**不 attach 到 OTel 上下文**
（单层 span 没有子节点，还能省掉跨 ``yield`` 的 detach 坑）。
"""

import contextlib
import logging
import time
from typing import Any, AsyncIterator, Dict, List, Optional, Tuple

from opentelemetry import trace as otel_trace

from app.services.ai.observability import manager, settings, span_attributes

logger = logging.getLogger(__name__)

TRACER_NAME = "nanzi.llm"
SPAN_NAME = "nanzi.llm.call"

SOURCE_ATTRIBUTE = "nanzi.llm.source"
STREAMING_ATTRIBUTE = "nanzi.llm.streaming"
FIRST_CHUNK_ATTRIBUTE = "nanzi.llm.first_chunk_ms"
STRUCTURED_STATUS_ATTRIBUTE = "nanzi.llm.structured_status"
ERROR_TYPE_ATTRIBUTE = "error.type"

# 输出文本入库前的截断长度，避免把超长响应塞进 span 属性。
MAX_OUTPUT_CHARS = 20000

_tracer = None


def _get_tracer():
    """平台自建 span 的 tracer；scope 必须是白名单里的 ``nanzi.llm``。"""
    global _tracer
    if _tracer is None:
        _tracer = otel_trace.get_tracer(TRACER_NAME)
    return _tracer


def is_tracing_enabled() -> bool:
    """追踪是否真正可用（开关 + 连接参数 + client 已建好），与 turn_span 同口径。"""
    try:
        return settings.is_tracing_ready() and manager.current_client() is not None
    except Exception:
        return False


def reset_for_tests() -> None:
    """仅测试使用：丢弃缓存的 tracer，避免上一用例的假 tracer 泄漏。"""
    global _tracer
    _tracer = None


class GenerationSpan:
    """一次模型调用的 span 句柄；追踪不可用时是安全空实现。"""

    __slots__ = ("_span", "_ended", "_started", "_chunk_count")

    def __init__(self, *, span: Any = None) -> None:
        self._span = span
        self._ended = False
        self._started = time.perf_counter()
        self._chunk_count = 0

    @property
    def active(self) -> bool:
        return self._span is not None

    def record_output(self, output: Any) -> None:
        """记录一次输出：非流式传最终结果，流式传单个 chunk。"""

    def record_status(self, status: str) -> None:
        """记录业务侧状态（如结构化输出的成功/失败分类）。"""

    def end(self, error: Optional[BaseException] = None) -> None:
        """结束 span；幂等、绝不抛异常。"""
        if self._ended:
            return
        self._ended = True


def start_generation_span(
    *,
    model: Optional[str],
    streaming: bool,
    source: Optional[str],
    input_messages: Any,
) -> GenerationSpan:
    """创建 generation span；不可用或出错时返回非活跃句柄。"""
    if not is_tracing_enabled():
        return GenerationSpan()

    attributes: Dict[str, Any] = {}
    try:
        span = _get_tracer().start_span(name=SPAN_NAME, attributes=attributes)
    except Exception as exc:
        logger.warning("Langfuse 直调 span 创建失败，本次不上报: %s", exc)
        return GenerationSpan()

    return GenerationSpan(span=span)


@contextlib.asynccontextmanager
async def traced_generation(
    *,
    model: Optional[str],
    streaming: bool,
    source: Optional[str],
    input_messages: Any,
) -> AsyncIterator[GenerationSpan]:
    """包裹一次模型调用，保证三条路径都收尾 span。

    ``GeneratorExit`` 是调用方提前 ``break`` / ``aclose()`` 的正常结果，不算错误。
    """
    span = start_generation_span(
        model=model, streaming=streaming, source=source, input_messages=input_messages
    )
    try:
        yield span
    except GeneratorExit:
        span.end()
        raise
    except BaseException as exc:
        span.end(error=exc)
        raise
    else:
        span.end()
```

- [ ] **Step 4: 跑测试确认通过**

Run: `PYTHONPATH=. .venv/bin/python -m pytest tests/services/ai/observability/test_generation_span.py -q`
Expected: PASS（1 passed）

---

## Task 3: 属性映射、`is_root`、`source` 与 trace name

**Files:**
- Modify: `app/services/ai/observability/generation_span.py`
- Test: `tests/services/ai/observability/test_generation_span.py`（追加）

- [ ] **Step 1: 写失败测试**

追加：

```python
def _start(monkeypatch, tracer, *, source=None, model="gpt-x", streaming=False,
           input_messages=None, has_parent=False):
    _enable_tracing(monkeypatch)
    monkeypatch.setattr(generation_span, "_get_tracer", lambda: tracer)
    monkeypatch.setattr(
        generation_span,
        "_current_parent_is_valid",
        lambda: has_parent,
    )
    return generation_span.start_generation_span(
        model=model,
        streaming=streaming,
        source=source,
        input_messages=input_messages if input_messages is not None else [_user_message()],
    )


def test_span_name_and_generation_attributes(monkeypatch):
    tracer = _RecordingTracer()
    handle = _start(monkeypatch, tracer, streaming=True)
    handle.end()

    span = tracer.spans[0]
    assert span.name == generation_span.SPAN_NAME
    assert span.attributes[span_attributes.OBSERVATION_TYPE] == "generation"
    assert span.attributes[span_attributes.GEN_AI_OPERATION_NAME] == "chat"
    assert span.attributes[span_attributes.GEN_AI_REQUEST_MODEL] == "gpt-x"
    assert span.attributes[span_attributes.OBSERVATION_MODEL] == "gpt-x"
    assert span.attributes[generation_span.STREAMING_ATTRIBUTE] is True
    assert "hi" in span.attributes[span_attributes.GEN_AI_INPUT_MESSAGES]


def test_root_span_writes_trace_name_from_source(monkeypatch):
    tracer = _RecordingTracer()
    _start(monkeypatch, tracer, source="chatbi.sql", has_parent=False).end()
    assert tracer.spans[0].attributes[span_attributes.TRACE_NAME] == "chatbi.sql"


def test_root_span_without_source_uses_span_name(monkeypatch):
    tracer = _RecordingTracer()
    _start(monkeypatch, tracer, source=None, has_parent=False).end()
    assert tracer.spans[0].attributes[span_attributes.TRACE_NAME] == generation_span.SPAN_NAME


def test_child_span_never_writes_trace_name(monkeypatch):
    """轮次内的直调不能改写整条 trace 的名字。"""
    tracer = _RecordingTracer()
    _start(monkeypatch, tracer, source="chatbi.sql", has_parent=True).end()
    assert span_attributes.TRACE_NAME not in tracer.spans[0].attributes


def test_source_attribute_only_when_provided(monkeypatch):
    tracer = _RecordingTracer()
    _start(monkeypatch, tracer, source="  ").end()
    assert generation_span.SOURCE_ATTRIBUTE not in tracer.spans[0].attributes

    tracer2 = _RecordingTracer()
    _start(monkeypatch, tracer2, source="intent").end()
    assert tracer2.spans[0].attributes[generation_span.SOURCE_ATTRIBUTE] == "intent"


def test_string_input_is_wrapped_as_message(monkeypatch):
    """字符串入参必须被包成消息，否则 generation_attributes 会把它拆成字符列表。"""
    tracer = _RecordingTracer()
    _start(monkeypatch, tracer, input_messages="hello").end()
    raw = tracer.spans[0].attributes[span_attributes.GEN_AI_INPUT_MESSAGES]
    assert "hello" in raw
    assert '"h"' not in raw
```

同时在文件顶部导入区补上 `span_attributes`：

```python
from app.services.ai.observability import generation_span, manager, settings, span_attributes
```

- [ ] **Step 2: 跑测试确认失败**

Run: `PYTHONPATH=. .venv/bin/python -m pytest tests/services/ai/observability/test_generation_span.py -q`
Expected: FAIL —— `AttributeError: module ... has no attribute '_current_parent_is_valid'`

- [ ] **Step 3: 实现**

把 `generation_span.py` 的 `start_generation_span` 整体替换为下面内容，并在 `reset_for_tests` 之后新增三个辅助函数：

```python
def reset_for_tests() -> None:
    """仅测试使用：丢弃缓存的 tracer，避免上一用例的假 tracer 泄漏。"""
    global _tracer
    _tracer = None


def _current_parent_is_valid() -> bool:
    """当前是否已处于某个 trace 内（即有没有活跃父 span）。"""
    try:
        parent = otel_trace.get_current_span()
        return bool(parent.get_span_context().is_valid)
    except Exception:
        return False


def _normalize_messages(messages: Any) -> List[Any]:
    """把入参规范成消息列表。

    字符串必须包成一条消息：``generation_attributes`` 内部做 ``list(input_messages)``，
    直接传字符串会被拆成一个个字符。
    """
    if messages is None:
        return []
    if isinstance(messages, str):
        return [{"role": "user", "content": messages}]
    if isinstance(messages, (list, tuple)):
        return list(messages)
    return [messages]


def _identity_attributes() -> Dict[str, Any]:
    """从平台上下文取 user / session；取不到就返回空（不影响调用）。"""
    try:
        from app.core.context import get_current_agent_context

        ctx = get_current_agent_context()
    except Exception:
        return {}
    if ctx is None:
        return {}
    try:
        return span_attributes.identity_attributes(
            user_id=getattr(ctx, "user_id", None),
            conversation_id=getattr(ctx, "conversation_id", None),
        )
    except Exception:
        return {}


def start_generation_span(
    *,
    model: Optional[str],
    streaming: bool,
    source: Optional[str],
    input_messages: Any,
) -> GenerationSpan:
    """创建 generation span；不可用或出错时返回非活跃句柄。"""
    if not is_tracing_enabled():
        return GenerationSpan()

    try:
        clean_source = str(source).strip() if source else ""

        attributes = span_attributes.generation_attributes(
            model=model,
            input_messages=_normalize_messages(input_messages),
        )
        attributes[STREAMING_ATTRIBUTE] = bool(streaming)
        if clean_source:
            attributes[SOURCE_ATTRIBUTE] = clean_source
        attributes.update(_identity_attributes())

        # trace name 只能由「自建 trace」的 span 写：轮次内的直调写它会改写整条 trace 的名字。
        if not _current_parent_is_valid():
            attributes[span_attributes.TRACE_NAME] = clean_source or SPAN_NAME

        span = _get_tracer().start_span(name=SPAN_NAME, attributes=attributes)
    except Exception as exc:
        logger.warning("Langfuse 直调 span 创建失败，本次不上报: %s", exc)
        return GenerationSpan()

    return GenerationSpan(span=span)
```

- [ ] **Step 4: 跑测试确认通过**

Run: `PYTHONPATH=. .venv/bin/python -m pytest tests/services/ai/observability/test_generation_span.py -q`
Expected: PASS（7 passed）

---

## Task 4: 输出累积、用量、TTFT 与收尾

**Files:**
- Modify: `app/services/ai/observability/generation_span.py`
- Test: `tests/services/ai/observability/test_generation_span.py`（追加）

- [ ] **Step 1: 写失败测试**

追加：

```python
class _Chunk:
    """模拟 AgentScope 的原始流式 chunk（带 is_last 终帧标记）。"""

    def __init__(self, text="", is_last=False, usage=None):
        self.content = [types.SimpleNamespace(type="text", text=text)]
        self.is_last = is_last
        self.usage = usage


class _Message:
    """模拟 AIMessage（无 is_last，带 usage_metadata）。"""

    def __init__(self, text="", usage=None):
        self.content = text
        self.usage_metadata = usage


def test_stream_chunks_final_frame_overrides_increments(monkeypatch):
    """终帧 content 是完整正文，必须覆盖增量，否则文本重复。"""
    tracer = _RecordingTracer()
    handle = _start(monkeypatch, tracer, streaming=True)

    handle.record_output(_Chunk("你好"))
    handle.record_output(_Chunk("，世界"))
    handle.record_output(_Chunk("你好，世界", is_last=True))
    handle.end()

    out = tracer.spans[0].attributes[span_attributes.GEN_AI_OUTPUT_MESSAGES]
    assert out.count("你好，世界") == 1
    assert "你好，世界你好，世界" not in out


def test_stream_usage_taken_from_final_frame(monkeypatch):
    tracer = _RecordingTracer()
    handle = _start(monkeypatch, tracer, streaming=True)

    handle.record_output(_Chunk("hi"))
    handle.record_output(
        _Chunk("hi there", is_last=True, usage={"input_tokens": 11, "output_tokens": 4})
    )
    handle.end()

    attrs = tracer.spans[0].attributes
    assert attrs[span_attributes.GEN_AI_USAGE_INPUT_TOKENS] == 11
    assert attrs[span_attributes.GEN_AI_USAGE_OUTPUT_TOKENS] == 4
    assert "input" in attrs[span_attributes.OBSERVATION_USAGE_DETAILS]


def test_non_stream_message_output_and_usage(monkeypatch):
    tracer = _RecordingTracer()
    handle = _start(monkeypatch, tracer, streaming=False)

    handle.record_output(
        _Message("答案", usage={"input_tokens": 3, "output_tokens": 2})
    )
    handle.end()

    attrs = tracer.spans[0].attributes
    assert "答案" in attrs[span_attributes.GEN_AI_OUTPUT_MESSAGES]
    assert attrs[span_attributes.GEN_AI_USAGE_INPUT_TOKENS] == 3


def test_first_chunk_records_ttft(monkeypatch):
    tracer = _RecordingTracer()
    handle = _start(monkeypatch, tracer, streaming=True)

    handle.record_output(_Chunk("a"))
    handle.record_output(_Chunk("ab", is_last=True))

    attrs = tracer.spans[0].attributes
    assert generation_span.FIRST_CHUNK_ATTRIBUTE in attrs
    assert attrs[generation_span.FIRST_CHUNK_ATTRIBUTE] >= 0


def test_end_records_error_type_and_is_idempotent(monkeypatch):
    tracer = _RecordingTracer()
    handle = _start(monkeypatch, tracer)

    handle.end(error=ValueError("boom"))
    handle.end()
    handle.end()

    span = tracer.spans[0]
    assert span.attributes[generation_span.ERROR_TYPE_ATTRIBUTE] == "ValueError"
    assert span.ended == 1


def test_record_status_writes_attribute(monkeypatch):
    tracer = _RecordingTracer()
    handle = _start(monkeypatch, tracer)
    handle.record_status("structured_output_error")
    handle.end()
    attrs = tracer.spans[0].attributes
    assert attrs[generation_span.STRUCTURED_STATUS_ATTRIBUTE] == "structured_output_error"


def test_span_json_is_truncated(monkeypatch):
    tracer = _RecordingTracer()
    handle = _start(monkeypatch, tracer)
    handle.record_output(_Message("x" * (generation_span.MAX_OUTPUT_CHARS + 500)))
    handle.end()
    out = tracer.spans[0].attributes[span_attributes.GEN_AI_OUTPUT_MESSAGES]
    assert len(out) <= generation_span.MAX_OUTPUT_CHARS + 200


async def test_generator_exit_does_not_record_error(monkeypatch):
    """调用方提前 break 属正常收尾，不该被记成错误。"""
    tracer = _RecordingTracer()
    _enable_tracing(monkeypatch)
    monkeypatch.setattr(generation_span, "_get_tracer", lambda: tracer)

    with pytest.raises(GeneratorExit):
        async with generation_span.traced_generation(
            model="m", streaming=True, source=None, input_messages=[]
        ):
            raise GeneratorExit

    span = tracer.spans[0]
    assert span.ended == 1
    assert generation_span.ERROR_TYPE_ATTRIBUTE not in span.attributes


async def test_exception_is_reraised_and_recorded(monkeypatch):
    tracer = _RecordingTracer()
    _enable_tracing(monkeypatch)
    monkeypatch.setattr(generation_span, "_get_tracer", lambda: tracer)

    with pytest.raises(ValueError):
        async with generation_span.traced_generation(
            model="m", streaming=False, source=None, input_messages=[]
        ):
            raise ValueError("kaboom")

    span = tracer.spans[0]
    assert span.ended == 1
    assert span.attributes[generation_span.ERROR_TYPE_ATTRIBUTE] == "ValueError"
```

- [ ] **Step 2: 跑测试确认失败**

Run: `PYTHONPATH=. .venv/bin/python -m pytest tests/services/ai/observability/test_generation_span.py -q`
Expected: FAIL —— `KeyError: 'gen_ai.output.messages'` 等

- [ ] **Step 3: 实现**

在 `generation_span.py` 的 `_normalize_messages` 之后新增两个解析辅助函数：

```python
def _output_text_and_is_last(output: Any) -> Tuple[str, bool]:
    """从一次输出里取 (文本, 是否终帧)，兼容 str / AIMessage / 原始 chunk。"""
    if isinstance(output, str):
        return output, False

    is_last = bool(getattr(output, "is_last", False))
    content = getattr(output, "content", None)

    if content is None and isinstance(output, dict):
        content = output.get("content")
        is_last = is_last or bool(output.get("is_last", False))

    if isinstance(content, str):
        return content, is_last

    # 原始 chunk 的 content 是 block 列表（AgentScope 形态）。
    parts: List[str] = []
    if isinstance(content, (list, tuple)):
        for block in content:
            text = getattr(block, "text", None)
            if text is None and isinstance(block, dict):
                text = block.get("text")
            if isinstance(text, str) and text:
                parts.append(text)
    return "".join(parts), is_last


def _output_usage(output: Any) -> Tuple[Optional[int], Optional[int]]:
    """从输出里取 (input_tokens, output_tokens)，兼容 usage_metadata / usage。"""
    for attr in ("usage_metadata", "usage"):
        usage = getattr(output, attr, None)
        if usage is None and isinstance(output, dict):
            usage = output.get(attr)
        tokens = span_attributes.extract_usage(usage)
        if tokens != (None, None):
            return tokens
    return (None, None)
```

把 `GenerationSpan` 类整体替换为：

```python
class GenerationSpan:
    """一次模型调用的 span 句柄；追踪不可用时是安全空实现。"""

    __slots__ = ("_span", "_ended", "_started", "_chunk_count", "_text_parts", "_final_text", "_usage")

    def __init__(self, *, span: Any = None) -> None:
        self._span = span
        self._ended = False
        self._started = time.perf_counter()
        self._chunk_count = 0
        self._text_parts: List[str] = []
        self._final_text: Optional[str] = None
        self._usage: Tuple[Optional[int], Optional[int]] = (None, None)

    @property
    def active(self) -> bool:
        return self._span is not None

    def record_output(self, output: Any) -> None:
        """记录一次输出：非流式传最终结果，流式传单个 chunk。

        流式下终帧（``is_last=True``）的 content 是整段完整正文，必须**覆盖**已累积的
        增量，否则会得到"增量 + 完整正文"的重复内容。
        """
        if self._span is None or self._ended:
            return
        try:
            if self._chunk_count == 0:
                self._mark_first_chunk()
            self._chunk_count += 1

            text, is_last = _output_text_and_is_last(output)
            if is_last:
                if text:
                    self._final_text = text
            elif text:
                self._text_parts.append(text)

            tokens = _output_usage(output)
            if tokens != (None, None):
                self._usage = tokens
        except Exception as exc:
            logger.debug("Langfuse generation 输出记录失败（忽略）: %s", exc)

    def record_status(self, status: str) -> None:
        """记录业务侧状态（如结构化输出的成功/失败分类）。"""
        if self._span is None or self._ended:
            return
        try:
            self._span.set_attribute(STRUCTURED_STATUS_ATTRIBUTE, str(status))
        except Exception as exc:
            logger.debug("Langfuse generation 状态记录失败（忽略）: %s", exc)

    def _mark_first_chunk(self) -> None:
        try:
            elapsed_ms = (time.perf_counter() - self._started) * 1000
            self._span.set_attribute(FIRST_CHUNK_ATTRIBUTE, round(elapsed_ms, 2))
        except Exception as exc:
            logger.debug("Langfuse TTFT 记录失败（忽略）: %s", exc)

    def _resolved_text(self) -> str:
        if self._final_text is not None:
            return self._final_text
        return "".join(self._text_parts)

    def end(self, error: Optional[BaseException] = None) -> None:
        """结束 span；幂等、绝不抛异常。"""
        if self._ended:
            return
        self._ended = True
        if self._span is None:
            return

        if error is not None:
            try:
                self._span.set_attribute(ERROR_TYPE_ATTRIBUTE, type(error).__name__)
            except Exception as exc:
                logger.debug("Langfuse 错误状态记录失败（忽略）: %s", exc)

        try:
            text = self._resolved_text()
            attributes = span_attributes.generation_attributes(
                output_messages=(
                    [{"role": "assistant", "content": text[:MAX_OUTPUT_CHARS]}]
                    if text
                    else None
                ),
                usage=self._usage,
            )
            for key, value in attributes.items():
                self._span.set_attribute(key, value)
        except Exception as exc:
            logger.debug("Langfuse 输出属性写入失败（忽略）: %s", exc)

        try:
            self._span.end()
        except Exception as exc:
            logger.debug("Langfuse generation span 结束失败（忽略）: %s", exc)
```

- [ ] **Step 4: 跑测试确认通过**

Run: `PYTHONPATH=. .venv/bin/python -m pytest tests/services/ai/observability/test_generation_span.py -q`
Expected: PASS（15 passed）

---

## Task 5: `source` 标签与 client 元数据透传

**Files:**
- Modify: `app/services/ai/runtime/agentscope/chat.py:163-166` 与 `:299-301`
- Modify: `app/core/llm/client.py`（dataclass 字段、`bind_tools`、`get_chat_model`、`get_llm_async`）
- Test: `tests/ai/runtime/test_agentscope_chat_tracing.py`（新建）

- [ ] **Step 1: 写失败测试**

创建 `tests/ai/runtime/test_agentscope_chat_tracing.py`（入参必须是 `RuntimeMessage` 对象，不能是 dict——`to_agentscope_messages` 需要 `.role`；因此测试里统一用 `_user_message()` 辅助函数构造）：

```python
"""直调链路埋点的行为对照测试：属性正确，且返回值/异常语义完全不变。"""

import pytest

from app.core.llm.client import AgentScopeLLMHandle
from app.services.ai.runtime.agentscope import chat

pytestmark = pytest.mark.no_infrastructure


def test_chat_client_from_handle_carries_model_name_and_source():
    handle = AgentScopeLLMHandle(
        native_model=object(),
        model_name="gpt-x",
        temperature=0.7,
        streaming=False,
        source="chatbi.sql",
    )
    client = chat.chat_client_from_handle(handle)
    assert client.model_name == "gpt-x"
    assert client.source == "chatbi.sql"


def test_bind_tools_keeps_source():
    """bind_tools 返回新实例；漏传 source 会让"绑定工具后的调用"丢标签。"""
    handle = AgentScopeLLMHandle(
        native_model=object(),
        model_name="gpt-x",
        temperature=0.7,
        streaming=False,
        source="executor.tool",
    )
    rebound = handle.bind_tools([])
    assert rebound.source == "executor.tool"


def test_chat_client_from_bare_native_model_has_no_metadata():
    class _Native:
        pass

    client = chat.chat_client_from_handle(_Native())
    assert client.model_name is None
    assert client.source is None
```

- [ ] **Step 2: 跑测试确认失败**

Run: `PYTHONPATH=. .venv/bin/python -m pytest tests/ai/runtime/test_agentscope_chat_tracing.py -q`
Expected: FAIL —— `TypeError: __init__() got an unexpected keyword argument 'source'`

- [ ] **Step 3: 实现**

`app/services/ai/runtime/agentscope/chat.py` 的 `AgentScopeChatClient.__init__` 替换为：

```python
    def __init__(
        self,
        native_model: Any,
        *,
        model_name: str | None = None,
        source: str | None = None,
    ):
        self.native_model = native_model
        # 供 Langfuse span 记录模型名与业务来源（由 chat_client_from_handle 从 handle 上取）。
        self.model_name = model_name
        self.source = source
        self.last_structured_output_status = "unknown"
```

同文件 `chat_client_from_handle` 替换为：

```python
def chat_client_from_handle(llm_handle: Any) -> AgentScopeChatClient:
    native_model = getattr(llm_handle, "native_model", llm_handle)
    return AgentScopeChatClient(
        native_model,
        model_name=(
            getattr(llm_handle, "model_name", None)
            or getattr(native_model, "model_name", None)
        ),
        source=getattr(llm_handle, "source", None),
    )
```

`app/core/llm/client.py`：

1. `AgentScopeLLMHandle` 增加字段（放在 `tool_schemas` 之后）：

```python
    tool_schemas: list[dict[str, Any]] | None = None
    # 业务来源标签（如 "chatbi.sql"），仅用于 Langfuse 排障与聚合，不参与模型调用。
    source: str | None = None
```

2. `bind_tools` 的返回补上 `source=self.source`：

```python
        return AgentScopeLLMHandle(
            native_model=self.native_model,
            model_name=self.model_name,
            temperature=self.temperature,
            streaming=self.streaming,
            api_base_url=self.api_base_url,
            tool_schemas=legacy_tools_to_openai_schemas(tools),
            source=self.source,
        )
```

3. `LLMFactory.get_chat_model` 签名与构造补 `source`：

```python
    @staticmethod
    def get_chat_model(
        streaming: bool = False,
        api_key: str | None = None,
        base_url: str | None = None,
        model: str | None = None,
        provider: str | None = None,
        temperature: float | None = None,
        context_size: int | None = None,
        max_output_tokens: int | None = None,
        thinking_enable: bool = False,
        thinking_capable: bool = False,
        reasoning_effort: str | None = None,
        read_timeout: float | None = None,
        source: str | None = None,
    ) -> AgentScopeLLMHandle:
```

并把末尾的 `return AgentScopeLLMHandle(...)` 补上 `source=source,`：

```python
        return AgentScopeLLMHandle(
            native_model=native_model,
            model_name=final_model,
            temperature=float(final_temp),
            streaming=streaming,
            api_base_url=final_base_url,
            source=source,
        )
```

4. `get_llm_async` 里显式取并透传（它用的是显式 `factory_kwargs` 白名单，不会自动带上新参数）：

在 `read_timeout = kwargs.get("read_timeout")` 之前插入：

```python
    source = kwargs.get("source")
```

在 `if reasoning_effort is not None:` 分支之后、`return LLMFactory.get_chat_model(**factory_kwargs)` 之前插入：

```python
    if source is not None:
        factory_kwargs["source"] = source
```

- [ ] **Step 4: 跑测试确认通过，并确认既有 chat 测试零回归**

Run: `PYTHONPATH=. .venv/bin/python -m pytest tests/ai/runtime/ tests/core/ -q`
Expected: PASS（新增 3 项 + 既有 `test_agentscope_chat_client.py` 全部通过）

---

## Task 6: `generate_message` 埋点

**Files:**
- Modify: `app/services/ai/runtime/agentscope/chat.py:247-280`
- Test: `tests/ai/runtime/test_agentscope_chat_tracing.py`（追加）

- [ ] **Step 1: 写失败测试**

先在文件顶部补导入与夹具：

```python
import types

from opentelemetry.sdk.trace.export import SpanExportResult

from app.services.ai.observability import generation_span


class _RecordingSpan:
    def __init__(self, name, attributes):
        self.name = name
        self.attributes = dict(attributes or {})
        self.ended = 0

    def set_attribute(self, key, value):
        self.attributes[key] = value

    def get_span_context(self):
        return types.SimpleNamespace(is_valid=True, trace_id=0xAB * 16)

    def end(self):
        self.ended += 1


class _RecordingTracer:
    def __init__(self):
        self.spans = []

    def start_span(self, name, attributes=None):
        span = _RecordingSpan(name, attributes)
        self.spans.append(span)
        return span


@pytest.fixture
def recording_spans(monkeypatch):
    """让 generation_span 走假 tracer 并认为追踪可用。"""
    tracer = _RecordingTracer()
    monkeypatch.setattr(generation_span, "is_tracing_enabled", lambda: True)
    monkeypatch.setattr(generation_span, "_get_tracer", lambda: tracer)
    monkeypatch.setattr(generation_span, "_current_parent_is_valid", lambda: False)
    return tracer


class _TextBlock:
    def __init__(self, text):
        self.type = "text"
        self.text = text


class _Response:
    """模拟 AgentScope 的 ChatResponse。"""

    def __init__(self, text, usage=None, is_last=True):
        self.content = [_TextBlock(text)]
        self.usage = usage
        self.is_last = is_last


class _PlainNative:
    """一次性返回响应（非流式）的模型。"""

    def __init__(self, text="你好"):
        self.text = text
        self.calls = 0

    def __call__(self, messages, **kwargs):
        self.calls += 1
        return _Response(self.text)


class _BoomNative:
    def __call__(self, messages, **kwargs):
        raise RuntimeError("模型炸了")


async def test_generate_message_creates_span_and_keeps_result(recording_spans):
    client = chat.AgentScopeChatClient(
        _PlainNative("你好"), model_name="gpt-x", source="unit.test"
    )
    result = await client.generate_message([_user_message()])

    assert result.content == "你好"
    assert len(recording_spans.spans) == 1
    span = recording_spans.spans[0]
    assert span.name == generation_span.SPAN_NAME
    assert span.attributes[generation_span.SOURCE_ATTRIBUTE] == "unit.test"
    assert span.ended == 1
    assert "你好" in span.attributes["gen_ai.output.messages"]


async def test_generate_message_reraises_and_records_error(recording_spans):
    client = chat.AgentScopeChatClient(_BoomNative(), model_name="gpt-x")

    with pytest.raises(RuntimeError, match="模型炸了"):
        await client.generate_message([_user_message()])

    span = recording_spans.spans[0]
    assert span.ended == 1
    assert span.attributes[generation_span.ERROR_TYPE_ATTRIBUTE] == "RuntimeError"
```

- [ ] **Step 2: 跑测试确认失败**

Run: `PYTHONPATH=. .venv/bin/python -m pytest tests/ai/runtime/test_agentscope_chat_tracing.py -q -k generate_message`
Expected: FAIL —— `assert len(recording_spans.spans) == 1` 实际为 0（尚未埋点）

- [ ] **Step 3: 实现**

在 `chat.py` 顶部导入区加入（放在既有 import 之后）：

```python
from app.services.ai.observability.generation_span import traced_generation
```

把 `generate_message` 整体替换为：

```python
    async def generate_message(
        self,
        messages: list[RuntimeMessage],
        *,
        tools: list[dict[str, Any]] | None = None,
        **kwargs: Any,
    ) -> AIMessage:
        async with traced_generation(
            model=self.model_name,
            streaming=False,
            source=self.source,
            input_messages=messages,
        ) as span:
            result = self.native_model(to_agentscope_messages(messages), tools=tools, **kwargs)
            if inspect.isawaitable(result):
                result = await result
            if _safe_getattr(result, "__aiter__"):
                # AgentScope 基类 __call__ 流式返回“增量块(is_last=False) + 完整终帧
                # (is_last=True)”，终帧 content 是整段累积后的完整正文、tool_calls 也
                # 只在该终帧完整给出。这里直接消费 result（而不是 stream_messages，
                # 以免对新会话重复发请求），逐块累加文本，遇 is_last=True 时以终帧的
                # 完整 AIMessage 覆盖累加的增量内容。
                content_parts: list[str] = []
                final_message: AIMessage | None = None
                async for chunk in result:  # type: ignore[union-attr]
                    message = _chat_response_to_message(chunk)
                    if getattr(chunk, "is_last", False):
                        final_message = message
                    else:
                        content_parts.append(message.content or "")
                if final_message is None:
                    resolved = AIMessage(content="".join(content_parts))
                elif not final_message.content and content_parts:
                    # 终帧 content 已是完整文本；仅当其为空（例如纯工具调用终帧）时回退到增量累积
                    resolved = replace(
                        final_message,
                        content="".join(content_parts),
                    )
                else:
                    resolved = final_message
            else:
                resolved = _chat_response_to_message(result)

            span.record_output(resolved)
            return resolved
```

- [ ] **Step 4: 跑测试确认通过**

Run: `PYTHONPATH=. .venv/bin/python -m pytest tests/ai/runtime/test_agentscope_chat_tracing.py -q -k generate_message`
Expected: PASS（2 passed）

---

## Task 7: `generate_structured_dict` 埋点（保持 fail-open）

该方法**吞掉异常并返回 `None`**，是调用方依赖的降级语义。埋点绝不能改动它。

**Files:**
- Modify: `app/services/ai/runtime/agentscope/chat.py:168-218`
- Test: `tests/ai/runtime/test_agentscope_chat_tracing.py`（追加）

- [ ] **Step 1: 写失败测试**

追加：

```python
class _StructuredBoomNative:
    async def generate_structured_output(self, **kwargs):
        raise RuntimeError("structured failed")


class _StructuredOkNative:
    async def generate_structured_output(self, **kwargs):
        # generate_structured_dict 直接接受 dict 形态的 content
        return types.SimpleNamespace(content={"a": 1})


async def test_generate_structured_dict_keeps_fail_open(recording_spans):
    """埋点不得改变 fail-open：异常仍被吞掉并返回 None。"""
    client = chat.AgentScopeChatClient(_StructuredBoomNative(), model_name="gpt-x")

    result = await client.generate_structured_dict(
        [_user_message()], structured_model=object()
    )

    assert result is None
    span = recording_spans.spans[0]
    assert span.ended == 1
    # 失败由状态属性表达，而不是异常
    assert span.attributes[generation_span.STRUCTURED_STATUS_ATTRIBUTE] in {
        "error",
        "structured_output_error",
        "invalid",
    }
    assert generation_span.ERROR_TYPE_ATTRIBUTE not in span.attributes


async def test_generate_structured_dict_records_success_status(recording_spans):
    client = chat.AgentScopeChatClient(_StructuredOkNative(), model_name="gpt-x")

    result = await client.generate_structured_dict(
        [_user_message()], structured_model=object()
    )

    assert result == {"a": 1}
    span = recording_spans.spans[0]
    assert span.attributes[generation_span.STRUCTURED_STATUS_ATTRIBUTE] == "success"
```

- [ ] **Step 2: 跑测试确认失败**

Run: `PYTHONPATH=. .venv/bin/python -m pytest tests/ai/runtime/test_agentscope_chat_tracing.py -q -k structured`
Expected: FAIL —— `IndexError: list index out of range`（`recording_spans.spans` 为空）

- [ ] **Step 3: 实现**

把现有 `generate_structured_dict` **整体重命名为** `_generate_structured_dict_impl`（方法体一行不动），并在它之前新增包装方法：

```python
    async def generate_structured_dict(
        self,
        messages: list[RuntimeMessage],
        structured_model: Any,
    ) -> dict[str, Any] | None:
        async with traced_generation(
            model=self.model_name,
            streaming=False,
            source=self.source,
            input_messages=messages,
        ) as span:
            try:
                result = await self._generate_structured_dict_impl(messages, structured_model)
            finally:
                # 本方法是 fail-open 的：失败不抛异常，只能靠状态属性表达。
                span.record_status(self.last_structured_output_status)
            if result is not None:
                span.record_output(json.dumps(result, ensure_ascii=False))
            return result

    async def _generate_structured_dict_impl(
        self,
        messages: list[RuntimeMessage],
        structured_model: Any,
    ) -> dict[str, Any] | None:
```

- [ ] **Step 4: 跑测试确认通过**

Run: `PYTHONPATH=. .venv/bin/python -m pytest tests/ai/runtime/test_agentscope_chat_tracing.py -q -k structured`
Expected: PASS（2 passed）

---

## Task 8: `generate_text` 埋点

注意该方法有三个 `return` 分支（流式迭代器、`AsyncIterator`、普通响应），**每个分支都要记录输出**。

**Files:**
- Modify: `app/services/ai/runtime/agentscope/chat.py`（`generate_text`）
- Test: `tests/ai/runtime/test_agentscope_chat_tracing.py`（追加）

- [ ] **Step 1: 写失败测试**

追加：

```python
class _StreamingTextNative:
    """流式返回：增量块 + 完整终帧。"""

    def __call__(self, messages, **kwargs):
        async def _gen():
            yield _Response("你", is_last=False)
            yield _Response("你好", is_last=True)

        return _gen()


async def test_generate_text_keeps_result_and_records_output(recording_spans):
    client = chat.AgentScopeChatClient(_StreamingTextNative(), model_name="gpt-x")

    text = await client.generate_text([_user_message()])

    assert text == "你好"
    span = recording_spans.spans[0]
    assert span.ended == 1
    assert "你好" in span.attributes["gen_ai.output.messages"]


async def test_generate_text_reraises_error(recording_spans):
    client = chat.AgentScopeChatClient(_BoomNative(), model_name="gpt-x")

    with pytest.raises(RuntimeError):
        await client.generate_text([_user_message()])

    assert recording_spans.spans[0].ended == 1
```

- [ ] **Step 2: 跑测试确认失败**

Run: `PYTHONPATH=. .venv/bin/python -m pytest tests/ai/runtime/test_agentscope_chat_tracing.py -q -k generate_text`
Expected: FAIL —— span 数为 0

- [ ] **Step 3: 实现**

把 `generate_text` 整体替换为：

```python
    async def generate_text(self, messages: list[RuntimeMessage], **kwargs: Any) -> str:
        async with traced_generation(
            model=self.model_name,
            streaming=False,
            source=self.source,
            input_messages=messages,
        ) as span:
            result = self.native_model(to_agentscope_messages(messages), **kwargs)
            if inspect.isawaitable(result):
                result = await result

            if _safe_getattr(result, "__aiter__"):
                # AgentScope 基类 ChatModelBase.__call__（_stream/累积器）保证流式返回
                # 形如“增量块(is_last=False) + 完整终帧(is_last=True)”，末帧 content 是
                # 整段累积后的完整正文。因此前面这些增量块逐段累加，遇 is_last=True 时
                # 以完整正文覆盖已累积内容，避免“增量汇总 + 完整末帧”的重复拼接。
                final_text = ""
                async for chunk in result:  # type: ignore[union-attr]
                    text = _response_text(chunk)
                    if getattr(chunk, "is_last", False):
                        final_text = text or final_text
                    else:
                        final_text += text
                span.record_output(final_text)
                return final_text

            if isinstance(result, AsyncIterator):
                final_text = ""
                async for chunk in result:
                    final_text += _response_text(chunk)
                span.record_output(final_text)
                return final_text

            text = _response_text(result)
            span.record_output(text)
            return text
```

- [ ] **Step 4: 跑测试确认通过**

Run: `PYTHONPATH=. .venv/bin/python -m pytest tests/ai/runtime/test_agentscope_chat_tracing.py -q -k generate_text`
Expected: PASS（2 passed）

---

## Task 9: `stream_messages` 埋点（终帧 + 提前中断）

流式方法必须记录**原始 chunk**（而非转换后的 `AIMessage`），因为 `is_last` 终帧标记只在原始 chunk 上。span 在首次迭代时才创建——生成器没被消费就等于调用没发生。

**Files:**
- Modify: `app/services/ai/runtime/agentscope/chat.py`（`stream_messages`）
- Test: `tests/ai/runtime/test_agentscope_chat_tracing.py`（追加）

- [ ] **Step 1: 写失败测试**

追加：

```python
async def test_stream_messages_records_original_chunks_without_duplication(recording_spans):
    client = chat.AgentScopeChatClient(_StreamingTextNative(), model_name="gpt-x")

    chunks = [m.content async for m in client.stream_messages([_user_message()])]

    assert chunks == ["你", "你好"]  # 对外 chunk 序列不变
    span = recording_spans.spans[0]
    assert span.ended == 1
    out = span.attributes["gen_ai.output.messages"]
    assert out.count("你好") == 1, "终帧必须覆盖增量，不能重复拼接"
    assert generation_span.FIRST_CHUNK_ATTRIBUTE in span.attributes


async def test_stream_messages_closes_span_on_early_break(recording_spans):
    """调用方提前 break 时 span 必须收尾，且不记为错误。"""
    client = chat.AgentScopeChatClient(_StreamingTextNative(), model_name="gpt-x")

    stream = client.stream_messages([_user_message()])
    async for _ in stream:
        break
    await stream.aclose()  # 显式关闭，不依赖 GC 时机

    span = recording_spans.spans[0]
    assert span.ended == 1
    assert generation_span.ERROR_TYPE_ATTRIBUTE not in span.attributes


class _MidStreamBoomNative:
    def __call__(self, messages, **kwargs):
        async def _gen():
            yield _Response("a", is_last=False)
            raise RuntimeError("流中断了")

        return _gen()


async def test_stream_messages_reraises_midstream_error(recording_spans):
    client = chat.AgentScopeChatClient(_MidStreamBoomNative(), model_name="gpt-x")

    with pytest.raises(RuntimeError, match="流中断了"):
        async for _ in client.stream_messages([_user_message()]):
            pass

    span = recording_spans.spans[0]
    assert span.ended == 1
    assert span.attributes[generation_span.ERROR_TYPE_ATTRIBUTE] == "RuntimeError"


async def test_stream_messages_creates_no_span_when_unused(recording_spans):
    """生成器未被消费 = 调用没发生，不应产生 span。"""
    client = chat.AgentScopeChatClient(_StreamingTextNative(), model_name="gpt-x")
    client.stream_messages([_user_message()])
    assert recording_spans.spans == []
```

- [ ] **Step 2: 跑测试确认失败**

Run: `PYTHONPATH=. .venv/bin/python -m pytest tests/ai/runtime/test_agentscope_chat_tracing.py -q -k stream_messages`
Expected: FAIL —— span 数为 0

- [ ] **Step 3: 实现**

把 `stream_messages` 整体替换为：

```python
    async def stream_messages(
        self,
        messages: list[RuntimeMessage],
        *,
        tools: list[dict[str, Any]] | None = None,
        **kwargs: Any,
    ) -> AsyncIterator[AIMessage]:
        async with traced_generation(
            model=self.model_name,
            streaming=True,
            source=self.source,
            input_messages=messages,
        ) as span:
            result = self.native_model(to_agentscope_messages(messages), tools=tools, **kwargs)
            if inspect.isawaitable(result):
                result = await result
            if _safe_getattr(result, "__aiter__"):
                async for chunk in result:
                    # 记录原始 chunk：is_last 终帧标记只在原始 chunk 上，
                    # 转成 AIMessage 后会丢失，导致文本与用量无法正确累积。
                    span.record_output(chunk)
                    yield _chat_response_to_message(chunk)
                return
            span.record_output(result)
            yield _chat_response_to_message(result)
```

- [ ] **Step 4: 跑测试确认通过**

Run: `PYTHONPATH=. .venv/bin/python -m pytest tests/ai/runtime/test_agentscope_chat_tracing.py -q`
Expected: PASS（全部 13 项）

---

## Task 10: 回归验证、性能对比与交付清单

**Files:**
- Test: `tests/ai/runtime/test_agentscope_chat_tracing.py`（追加回归用例）
- Modify: `tests/CHECKLIST.md`

- [ ] **Step 1: 写轮次内不污染 trace name 的回归测试**

追加：

```python
async def test_child_call_does_not_rewrite_trace_name(monkeypatch):
    """轮次内的直调必须挂进同一 trace，且不能改写轮次 trace 的名称。

    这里模拟「已有活跃父 span」：span 归属交给 OTel，我们只断言不写 trace name。
    """
    tracer = _RecordingTracer()
    monkeypatch.setattr(generation_span, "is_tracing_enabled", lambda: True)
    monkeypatch.setattr(generation_span, "_get_tracer", lambda: tracer)
    monkeypatch.setattr(generation_span, "_current_parent_is_valid", lambda: True)

    client = chat.AgentScopeChatClient(
        _PlainNative("ok"), model_name="gpt-x", source="executor.tool"
    )
    await client.generate_message([_user_message()])

    span = tracer.spans[0]
    assert "langfuse.trace.name" not in span.attributes
    assert span.attributes[generation_span.SOURCE_ATTRIBUTE] == "executor.tool"


async def test_no_span_when_tracing_disabled(monkeypatch):
    """追踪关闭时四个方法的行为与无埋点版本一致（这里以 generate_message 为代表）。"""
    from app.services.ai.observability import manager, settings

    manager.reset_for_tests()
    settings.reset_for_tests()

    client = chat.AgentScopeChatClient(_PlainNative("ok"), model_name="gpt-x")
    result = await client.generate_message([_user_message()])
    assert result.content == "ok"
```

- [ ] **Step 2: 跑全套相关测试**

Run:

```bash
PYTHONPATH=. .venv/bin/python -m pytest \
  tests/services/ai/observability/ \
  tests/ai/runtime/ \
  tests/services/ai/ \
  --confcutdir=tests -q \
  --ignore=tests/api/portal/test_chatbi_examples_sql.py
```

Expected: 新增用例全绿；既有失败项与改动前**逐条一致**（用 `git stash` 前后对比，或与本文档记录前的基线比对）

- [ ] **Step 3: 性能对比**

把下面脚本存为 `/tmp/bench_direct_llm.py` 并运行（各 300 次调用，比较单次微秒开销）：

```python
import asyncio
import time

from app.services.ai.observability import generation_span
from app.services.ai.runtime.agentscope import chat


class _Block:
    def __init__(self, text):
        self.type = "text"
        self.text = text


class _Native:
    def __call__(self, messages, **kwargs):
        resp = type("R", (), {})()
        resp.content = [_Block("ok")]
        resp.usage = None
        resp.is_last = True
        return resp


async def measure(enabled: bool) -> float:
    generation_span.is_tracing_enabled = lambda: enabled  # 仅测量用
    client = chat.AgentScopeChatClient(_Native(), model_name="m", source="bench")
    start = time.perf_counter()
    for _ in range(300):
        await client.generate_message([_user_message()])
    return (time.perf_counter() - start) / 300 * 1_000_000


print("追踪关闭:", round(asyncio.run(measure(False)), 2), "µs/次")
print("追踪开启:", round(asyncio.run(measure(True)), 2), "µs/次")
```

Run: `PYTHONPATH=. .venv/bin/python /tmp/bench_direct_llm.py`
Expected: 两个数字都远小于 1 ms；两者差值即埋点净开销（预期数十微秒量级，与一期 26.5 µs/轮 同量级）。把数字记入 CHECKLIST。

- [ ] **Step 4: 更新 `tests/CHECKLIST.md`**

追加一行交付条目，格式与既有条目一致（交付项 / 涉及文件 / 背景与实现 / 验证结论 / 日期 2026-09-30），其中必须写明：埋点位置为何定在 `AgentScopeChatClient` 而非 handle、终帧覆盖策略、fail-open 语义保持不变、以及真实 Langfuse 实例验收待用户执行。

---

## 完成标准

- [ ] `traced_generation` 在追踪关闭时零 span 创建；
- [ ] 四个方法在追踪关闭/开启下返回值与异常语义完全一致；
- [ ] `generate_structured_dict` 的 fail-open 行为未变；
- [ ] 流式终帧不产生重复文本，提前 `break` 也能收尾；
- [ ] 轮次内的直调不改写 trace name；
- [ ] `bind_tools()` 后 `source` 仍保留；
- [ ] 既有测试零回归；
- [ ] 真实 Langfuse 实例上能看到直调 span（**由用户执行**：`./dev.sh` 后触发任一 ChatBI / 报告分析功能）。
