from app.services.ai.multimodal_support import (
    format_execution_error,
    history_contains_images,
    is_multimodal_api_error,
    last_user_message_has_images,
)


def test_history_contains_images():
    history = [
        {
            "role": "user",
            "content": "看看这张图",
            "files": [
                {
                    "type": "local_file",
                    "url": "/app/data/uploads/a.png",
                    "filename": "a.png",
                    "ext": ".png",
                }
            ],
        }
    ]
    assert history_contains_images(history) is True
    assert history_contains_images([{"role": "user", "content": "hi"}]) is False


def test_last_user_message_has_images_ignores_history():
    history = [
        {
            "role": "user",
            "content": "old image",
            "files": [{"url": "/static/uploads/a.png", "filename": "a.png", "ext": "png"}],
        },
        {"role": "assistant", "content": "failed"},
        {"role": "user", "content": "follow up text only"},
    ]
    assert history_contains_images(history) is True
    assert last_user_message_has_images(history) is False


def test_is_multimodal_api_error():
    raw = (
        "Error code: 400 - {'error': {'message': "
        "'DeepSeek-V4-Flash is not a multimodal model', 'type': 'BadRequestError'}}"
    )
    assert is_multimodal_api_error(raw) is True
    assert is_multimodal_api_error("connection timeout") is False


def test_format_execution_error_multimodal():
    raw = "'DeepSeek-V4-Flash' is not a multimodal model"
    msg = format_execution_error(raw)
    assert "不支持图片理解" in msg
    assert "DeepSeek-V4-Flash" in msg
    assert "[系统错误]" not in msg


def test_format_execution_error_generic():
    msg = format_execution_error("connection reset")
    assert "[系统错误]" in msg
    assert "connection reset" in msg
