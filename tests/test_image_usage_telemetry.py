"""图像工具计量信息的传递链路：工具结果 → ToolChunk 元数据 → 执行轨迹 meta_info。

为什么单独一份契约：这条链横跨三个模块（图像工具 / AgentScope 包装层 / runner 轨迹写入），
任何一段断了都是**静默失效**——不报错、不影响出图，只是事后查不到这次花了多少。
真正花钱的是图像接口那次调用，平台原来对它一点痕迹都没有：`request_image_once` 不解析
上游 `usage`，而走 chat 协议的网关会把图片折算成大量 token 一并返回。

两条硬约束：

1. **计量不许进模型上下文**。工具结果会被 `json.dumps` 整段塞给模型，用量数字对模型
   没用，白花 token，还可能被它复述进回答；所以必须在格式化之前摘走。
2. **拿不到也别丢**。多数图像端点不返回 usage，此时张数、尺寸、耗时仍然要记下来，
   「生成了几张、多大」本身就是可核对的成本线索。
"""

from pathlib import Path

import pytest

from app.services.ai.runtime.agentscope import tool_result_context
from app.services.ai.runtime.agentscope.tool_result_context import (
    IMAGE_USAGE_METADATA_KEY,
    image_usage_from_metadata,
)
from app.services.ai.runtime.agentscope.tools import (
    _split_image_usage,
    _tool_chunk_metadata,
)

pytestmark = pytest.mark.no_infrastructure

ROOT = Path(__file__).resolve().parents[1]
RUNNER = ROOT / "app/services/ai/runners/assistant_agent_runner.py"

_USAGE = {
    "tool": "generate_image",
    "requested_count": 2,
    "succeeded_count": 2,
    "size": "1664x928",
    "model": "upstream-name",
    "upstream_tokens": {"prompt_tokens": 22, "completion_tokens": 44, "total_tokens": 66},
    "elapsed_ms": 8123,
}


class _FakeToolResult:
    """够用的工具结果替身：只需要 metadata。"""

    def __init__(self, metadata):
        self.metadata = metadata


def test_usage_is_stripped_before_the_model_sees_it():
    """摘要字段必须在格式化前摘掉，否则用量会跟着结果进模型上下文。"""
    clean, usage = _split_image_usage({"ok": True, "images": [{"a": 1}], "image_usage": _USAGE})

    assert usage == _USAGE
    assert IMAGE_USAGE_METADATA_KEY not in clean
    # 业务字段一个都不能少
    assert clean == {"ok": True, "images": [{"a": 1}]}

    # 原字典不能被就地改掉：工具返回值可能还被别处引用
    result = {"ok": True, "image_usage": _USAGE}
    _split_image_usage(result)
    assert IMAGE_USAGE_METADATA_KEY in result


def test_split_leaves_normal_results_untouched():
    """没有计量字段的工具（绝大多数）不该被复制一遍。"""
    result = {"ok": True, "rows": [1, 2]}
    clean, usage = _split_image_usage(result)

    assert clean is result
    assert usage is None
    # 非 dict 结果（字符串、None）原样返回
    assert _split_image_usage("done") == ("done", None)
    assert _split_image_usage(None) == (None, None)
    # 有键但是空字典：摘掉键，但不当作计量
    assert _split_image_usage({"ok": True, "image_usage": {}}) == ({"ok": True}, None)


def test_metadata_carries_usage_only_when_present():
    assert _tool_chunk_metadata("call-1", _USAGE) == {
        "nanzi_call_id": "call-1",
        "image_usage": _USAGE,
    }
    # 普通工具：元数据里只有凭证 ID，不多一个空键
    assert _tool_chunk_metadata("call-2") == {"nanzi_call_id": "call-2"}
    assert _tool_chunk_metadata("call-3", None) == {"nanzi_call_id": "call-3"}


def test_usage_is_read_back_from_metadata():
    assert image_usage_from_metadata(_FakeToolResult({"image_usage": _USAGE})) == _USAGE
    # 没有元数据 / 元数据里没有 / 值是脏的：一律 None，不做猜测
    assert image_usage_from_metadata(object()) is None
    assert image_usage_from_metadata(_FakeToolResult({})) is None
    assert image_usage_from_metadata(_FakeToolResult({"image_usage": "oops"})) is None
    assert image_usage_from_metadata(_FakeToolResult({"image_usage": {}})) is None


def test_usage_key_is_shared_by_both_ends():
    """写端与读端必须是同一个键：各写各的字符串，改名一次就静默断链。"""
    tools_source = (
        ROOT / "app/services/ai/runtime/agentscope/tools.py"
    ).read_text(encoding="utf-8")
    assert 'IMAGE_USAGE_METADATA_KEY = "image_usage"' not in tools_source, (
        "键只能在 tool_result_context 里定义一次，tools.py 要 import 而不是自己写一遍"
    )
    assert IMAGE_USAGE_METADATA_KEY == "image_usage"
    assert tool_result_context.IMAGE_USAGE_METADATA_KEY == "image_usage"


def test_runner_writes_usage_into_trace_meta_info():
    """最后一段：轨迹的 meta_info 要同时容纳超时来源与用量。

    这两项都是「事后解释一次调用」的关键，写在同一条 meta_info 里才能一起查。
    """
    source = RUNNER.read_text(encoding="utf-8")

    # 从工具结果元数据取值并传下去
    assert "image_usage=image_usage_from_metadata(event)" in source
    # 方法签名接得住
    assert "image_usage: Dict[str, Any] | None = None" in source
    # 落进 meta_info（与 timeout 合并，且两者互不覆盖）
    step_at = source.index('event_type="tool_call"')
    window = source[step_at : step_at + 1400]
    assert 'meta_info=' in window
    assert '"timeout_seconds": t_timeout' in window
    assert '"image_usage": image_usage' in window


@pytest.mark.asyncio
async def test_non_streaming_tool_call_carries_usage_in_metadata():
    """非流式工具（generate_image / edit_image 走的就是这条）同样要把计量挂到元数据上。

    真实故障（2026-11-23 14:10 那次生图）：轨迹的 `tool_output.raw` 里明明有
    `image_usage`，`meta_info` 却只有 timeout —— 因为 `AgentScopeRuntimeTool.__call__`
    有两条返回路径，只有「流式生成器」那条摘了计量，普通 async 工具这条直接
    `_format_runtime_tool_result(res)` 就返回了。后果是三重静默失效：用量跟着结果进了
    模型上下文（白花 token）、轨迹里查不到、Token 统计页的图片用量恒为 0。
    """
    from app.services.ai.runtime.agentscope.tools import (
        AgentScopeRuntimeTool,
        RuntimeToolSpec,
    )

    async def _generate_image(**_kwargs):
        return {
            "ok": True,
            "images": [{"filename": "generated-image-1.png"}],
            "image_usage": _USAGE,
            "hint": "用 markdown 图片语法引用 download_url",
        }

    spec = RuntimeToolSpec(
        name="generate_image",
        description="画一张图",
        parameters_schema={"type": "object", "properties": {}},
        source_type="system",
        callable=_generate_image,
        permission_scope="read",
    )
    tool = AgentScopeRuntimeTool(spec, approval_mode="allow")

    chunk = await tool()

    # 1) 计量必须挂在元数据上，才能一路带到执行轨迹的 meta_info
    assert chunk.metadata[IMAGE_USAGE_METADATA_KEY] == _USAGE
    # 2) 凭证 ID 不能因为这次改动丢掉
    assert chunk.metadata["nanzi_call_id"]

    text = "".join(str(getattr(block, "text", "")) for block in chunk.content)
    # 3) 计量不许进模型上下文
    assert "image_usage" not in text
    assert "succeeded_count" not in text
    # 4) 业务字段一个都不能少
    assert "generated-image-1.png" in text
    assert "download_url" in text
