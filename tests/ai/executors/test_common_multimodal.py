import base64
import os

import pytest

from app.services.ai.executors.common import convert_history_to_messages


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
