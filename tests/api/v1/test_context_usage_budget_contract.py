"""上下文预算契约：REST 上下文接口必须复用统一的预算计算，不得手搓公式。

背景：`GET /chat/conversation/{id}/context-usage` 曾自己拼装 `runtime_model_info`，
只传 `max_output_tokens`，而 `estimate_context_usage` 读取的键名是
`completion_reserve_tokens`。字段名错配让输出预留恒为 0，接口回落到"不扣输出预留"的
兜底公式，输入框水位线（自动压缩触发线 / 请求输入上限）各比真实值大一个
`max_output_tokens`，并与同一会话的调用统计弹框自相矛盾。

这类"接线"缺陷靠单元测试很难覆盖（函数本身是对的），因此这里用源码契约锁住：
端点必须走 `_runtime_context_metadata`，并且不再出现手搓的字段字典。
"""
from pathlib import Path

CHAT_ENDPOINT = (
    Path(__file__).resolve().parents[3] / "app/api/v1/endpoints/chat.py"
)


def _context_usage_endpoint_source() -> str:
    source = CHAT_ENDPOINT.read_text(encoding="utf-8")
    marker = "async def get_conversation_context_usage("
    start = source.index(marker)
    # 下一个顶层装饰器即为本函数结束
    end = source.index("\n@router.", start)
    return source[start:end]


def test_context_usage_endpoint_reuses_the_shared_budget_contract():
    body = _context_usage_endpoint_source()

    assert "_runtime_context_metadata(" in body, (
        "上下文用量接口必须复用 _runtime_context_metadata，而不是自己拼预算字段"
    )
    assert "RuntimeModelInfo(" in body


def test_context_usage_endpoint_does_not_hand_roll_budget_fields():
    body = _context_usage_endpoint_source()

    # 手搓字典的典型痕迹：直接构造 source/context_size/max_output_tokens 三件套
    assert '"max_output_tokens": model.max_output_tokens' not in body, (
        "不得手搓 runtime_model_info：字段名极易与 estimate_context_usage 的读取键名错配"
    )


def test_estimate_context_usage_consumes_the_contract_key_names():
    """契约双方必须对得上：端点产出的键名要覆盖消费方读取的每一个键。"""
    usage_source = (
        Path(__file__).resolve().parents[3] / "app/services/ai/context_usage.py"
    ).read_text(encoding="utf-8")
    agent_source = (
        Path(__file__).resolve().parents[3] / "app/services/ai/agent_service.py"
    ).read_text(encoding="utf-8")

    contract_keys = (
        "physical_window",
        "history_budget",
        "completion_reserve_tokens",
        "request_input_budget",
        "prompt_overhead_reservation_tokens",
        "overhead_reservation_tokens",
    )
    for key in contract_keys:
        assert f'"{key}"' in usage_source, f"消费方应读取 {key}"
        assert f'"{key}"' in agent_source, f"产出方应提供 {key}"
