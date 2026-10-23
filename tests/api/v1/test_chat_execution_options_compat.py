"""`/chat/completions` 请求字段更名兼容：``debug_options`` → ``execution_options``。

旧客户端（含历史 Embed/第三方集成）仍会传 ``debug_options``，后端必须继续接受，
且两者同时出现时以新字段为准，避免同一请求出现两套覆盖值。
"""

from app.api.v1.endpoints.chat import ChatCompletionRequest


def _payload(**extra) -> dict:
    return {"messages": [{"role": "user", "content": "hi"}], **extra}


def test_request_accepts_execution_options_field():
    request = ChatCompletionRequest(**_payload(execution_options={"model": "new-model"}))

    assert request.execution_options == {"model": "new-model"}


def test_request_accepts_legacy_debug_options_field():
    request = ChatCompletionRequest(**_payload(debug_options={"model": "legacy-model"}))

    assert request.execution_options == {"model": "legacy-model"}


def test_request_prefers_execution_options_over_legacy_field():
    request = ChatCompletionRequest(
        **_payload(
            execution_options={"model": "new-model"},
            debug_options={"model": "legacy-model"},
        )
    )

    assert request.execution_options == {"model": "new-model"}
