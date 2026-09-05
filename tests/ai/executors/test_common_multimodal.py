import base64
import os

import pytest
from app.services.ai.runtime.agentscope.compat import AIMessage, HumanMessage, SystemMessage

from app.services.ai.executors.common import (
    append_system_instruction,
    convert_history_to_messages,
    normalize_messages_for_llm,
    _plain_user_text,
)

pytestmark = pytest.mark.no_infrastructure


@pytest.fixture
def sample_png(tmp_path, monkeypatch):
    uploads = tmp_path / "uploads"
    uploads.mkdir()
    img_path = uploads / "chart.png"
    img_path.write_bytes(b"\x89PNG fake image bytes")

    server_img = tmp_path / "photos" / "scan.jpg"
    server_img.parent.mkdir()
    server_img.write_bytes(b"\xff\xd8\xff fake jpeg")

    monkeypatch.setattr("app.services.ai.executors.common.get_data_base_dir", lambda: str(tmp_path))
    return {
        "upload_url": "/static/uploads/chart.png",
        "server_path": str(server_img),
    }


def test_uploaded_image_uses_multimodal_with_normalized_ext(sample_png, tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    os.makedirs("data/uploads", exist_ok=True)
    with open("data/uploads/chart.png", "wb") as f:
        f.write(b"\x89PNG fake image bytes")

    history = [
        {
            "role": "user",
            "content": "describe this",
            "files": [
                {
                    "url": sample_png["upload_url"],
                    "filename": "chart.png",
                    "size": 128,
                    "ext": "png",
                }
            ],
        }
    ]

    messages = convert_history_to_messages(history)
    assert len(messages) == 1
    content = messages[0].content
    assert isinstance(content, list)
    assert content[0]["type"] == "text"
    assert content[1]["type"] == "image_url"
    assert content[1]["image_url"]["url"].startswith("data:image/png;base64,")


def test_server_local_file_image_uses_multimodal(sample_png):
    history = [
        {
            "role": "user",
            "content": "what is in this image",
            "files": [
                {
                    "type": "local_file",
                    "url": sample_png["server_path"],
                    "filename": "scan.jpg",
                    "size": 256,
                    "ext": ".jpg",
                }
            ],
        }
    ]

    messages = convert_history_to_messages(history)
    content = messages[0].content
    assert isinstance(content, list)
    assert len(content) == 2
    data_url = content[1]["image_url"]["url"]
    assert data_url.startswith("data:image/jpeg;base64,")
    decoded = base64.b64decode(data_url.split(",", 1)[1])
    assert decoded.startswith(b"\xff\xd8\xff")


def test_server_non_image_stays_text_only(sample_png):
    doc_path = os.path.join(os.path.dirname(sample_png["server_path"]), "readme.txt")
    with open(doc_path, "w", encoding="utf-8") as f:
        f.write("hello")

    history = [
        {
            "role": "user",
            "content": "summarize",
            "files": [
                {
                    "type": "local_file",
                    "url": doc_path,
                    "filename": "readme.txt",
                    "size": 5,
                    "ext": ".txt",
                }
            ],
        }
    ]

    messages = convert_history_to_messages(history)
    content = messages[0].content
    assert isinstance(content, str)
    assert doc_path in content
    assert "image_url" not in content


def test_historical_image_not_resubmitted_as_multimodal(sample_png, tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    os.makedirs("data/uploads", exist_ok=True)
    with open("data/uploads/chart.png", "wb") as f:
        f.write(b"\x89PNG fake")

    history = [
        {
            "role": "user",
            "content": "describe image\n\n---\n\n系统指令块",
            "files": [
                {
                    "url": "/static/uploads/chart.png",
                    "filename": "chart.png",
                    "size": 128,
                    "ext": "png",
                }
            ],
        },
        {"role": "assistant", "content": "model error"},
        {"role": "user", "content": "just a follow up question"},
    ]

    messages = convert_history_to_messages(history)
    assert len(messages) == 3
    assert messages[0].content == "describe image"
    assert isinstance(messages[2].content, str)
    assert messages[2].content == "just a follow up question"


def test_vision_sidecar_skips_image_url_on_current_turn(sample_png, tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    os.makedirs("data/uploads", exist_ok=True)
    with open("data/uploads/chart.png", "wb") as f:
        f.write(b"\x89PNG fake")

    history = [
        {
            "role": "user",
            "content": (
                "describe this\n\n"
                "<vision_sidecar model=\"qwen-vl\">图表标题是销售额</vision_sidecar>"
            ),
            "files": [
                {
                    "url": "/static/uploads/chart.png",
                    "filename": "chart.png",
                    "size": 128,
                    "ext": "png",
                }
            ],
        }
    ]

    messages = convert_history_to_messages(history)
    content = messages[0].content
    assert isinstance(content, str)
    assert "销售额" in content
    assert "image_url" not in content


def test_historical_vision_sidecar_kept_for_followup():
    history = [
        {
            "role": "user",
            "content": (
                "看这张图\n\n---\n\n用户本轮已上传图片：a.png\n\n"
                "<vision_sidecar model=\"qwen-vl\">第三行是合计 128</vision_sidecar>"
            ),
            "files": [{"url": "/static/uploads/a.png", "filename": "a.png", "ext": "png"}],
        },
        {"role": "assistant", "content": "收到"},
        {"role": "user", "content": "第三行是什么"},
    ]

    messages = convert_history_to_messages(history)
    assert "看这张图" in messages[0].content
    assert "第三行是合计 128" in messages[0].content
    assert "vision_sidecar" in messages[0].content
    assert "用户本轮已上传图片" not in messages[0].content
    assert _plain_user_text(history[0]["content"]).startswith("看这张图")


def test_history_injects_final_tool_results_as_separate_internal_context():
    messages = convert_history_to_messages(
        [
            {
                "role": "assistant",
                "content": "已处理",
                "tool_run_text": "search_knowledge_base: {} -> 权限申请流程",
                "tool_run_text_version": "final_tool_result_v2",
            }
        ]
    )

    assert isinstance(messages[0], AIMessage)
    assert "<backend_tool_result_context>" in messages[0].content
    assert "权限申请流程" in messages[0].content
    assert "不包含思考、执行日志或失败调用" in messages[0].content
    assert "<backend_tool_run_summary>" not in messages[0].content


def test_legacy_unversioned_tool_run_text_is_not_injected_into_llm_context():
    messages = convert_history_to_messages(
        [
            {
                "role": "assistant",
                "content": "已处理",
                "tool_run_text": "backend_tool_run_summary: -> 订单总数 999",
            }
        ]
    )

    assert "backend_tool_run_summary" not in messages[0].content
    assert "订单总数 999" not in messages[0].content


def test_normalize_messages_for_llm_merges_system_messages_at_beginning():
    messages = [
        SystemMessage(content="primary system"),
        HumanMessage(content="hello"),
        SystemMessage(content="runtime context"),
        HumanMessage(content="follow up"),
    ]

    normalized = normalize_messages_for_llm(messages)

    assert isinstance(normalized[0], SystemMessage)
    assert "primary system" in normalized[0].content
    assert "runtime context" in normalized[0].content
    assert [type(m) for m in normalized[1:]] == [HumanMessage, HumanMessage]


def test_append_system_instruction_merges_into_first_system_message():
    messages = [
        SystemMessage(content="primary system"),
        HumanMessage(content="hello"),
    ]

    append_system_instruction(messages, "must use search")

    assert len(messages) == 2
    assert isinstance(messages[0], SystemMessage)
    assert "primary system" in messages[0].content
    assert "must use search" in messages[0].content
