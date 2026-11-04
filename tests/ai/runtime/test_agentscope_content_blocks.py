"""LangChain 风格消息 → RuntimeMessage 的内容块转换。

重点锁两件事：
1. 图片媒体类型必须来自 data URI 头，不能一律标成 png——上游按真实扩展名生成
   ``data:image/jpeg`` ／ ``data:image/webp``，标错后下游 ``Base64Source`` 会把
   jpeg 字节声明成 png，严格的多模态网关会直接判 Invalid image。
2. ``image_url`` 允许是裸字符串，不能因为按 dict 硬取而抛 ``AttributeError``。
"""

from __future__ import annotations

import pytest

from app.services.ai.runtime.agentscope.chat import (
    _compat_content_blocks,
    _image_mime_from_url,
    compat_to_runtime_messages,
    to_agentscope_messages,
)
from app.services.ai.runtime.agentscope.compat import HumanMessage

pytestmark = pytest.mark.no_infrastructure

JPEG_URL = "data:image/jpeg;base64,/9j/4AAQSkZJRg=="
WEBP_URL = "data:image/webp;base64,UklGRhIAAABXRUJQ"


def test_mime_comes_from_data_uri_header() -> None:
    assert _image_mime_from_url(JPEG_URL) == "image/jpeg"
    assert _image_mime_from_url(WEBP_URL) == "image/webp"
    assert _image_mime_from_url("data:image/png;base64,AAAA") == "image/png"


def test_mime_falls_back_for_remote_url_and_unknown_header() -> None:
    # 远程 URL 不带媒体类型，只能沿用历史默认值
    assert _image_mime_from_url("https://example.com/a.jpg") == "image/png"
    # 头部不是 image/* （脏数据）也回退，不能把非法值透给 Base64Source
    assert _image_mime_from_url("data:application/pdf;base64,AAAA") == "image/png"
    assert _image_mime_from_url("") == "image/png"


def test_image_block_keeps_source_url_and_real_mime() -> None:
    blocks = _compat_content_blocks(
        [
            {"type": "text", "text": "看图"},
            {"type": "image_url", "image_url": {"url": JPEG_URL}},
        ]
    )

    assert [block.type for block in blocks] == ["text", "image"]
    assert blocks[1].mime_type == "image/jpeg"
    assert blocks[1].data == JPEG_URL


def test_image_url_accepts_bare_string() -> None:
    blocks = _compat_content_blocks(
        [{"type": "image_url", "image_url": WEBP_URL}]
    )

    assert blocks[0].type == "image"
    assert blocks[0].mime_type == "image/webp"
    assert blocks[0].data == WEBP_URL


def test_empty_image_url_does_not_create_image_block() -> None:
    blocks = _compat_content_blocks(
        [
            {"type": "text", "text": "只有文字"},
            {"type": "image_url", "image_url": {"url": ""}},
            {"type": "image_url", "image_url": ""},
        ]
    )

    assert [block.type for block in blocks] == ["text"]


def test_non_dict_items_and_plain_string_content() -> None:
    assert _compat_content_blocks([{"type": "text", "text": "hi"}, "裸字符串"])[0].text == "hi"
    assert _compat_content_blocks([])[0].type == "text"
    assert _compat_content_blocks(None)[0].text == ""


def test_jpeg_survives_end_to_end_conversion() -> None:
    """整条链路：jpeg 附件不能被降级成 png 声明。"""
    messages = compat_to_runtime_messages(
        [HumanMessage(content=[{"type": "image_url", "image_url": {"url": JPEG_URL}}])]
    )
    assert messages[0].content[0].mime_type == "image/jpeg"

    converted = to_agentscope_messages(messages)
    source = converted[0].content[0].source
    assert source.media_type == "image/jpeg"
    # data: 前缀必须被剥掉，只留 base64 正文
    assert source.data == "/9j/4AAQSkZJRg=="


def test_truncated_data_uri_is_skipped_instead_of_raising() -> None:
    """残缺 data URI（没有逗号）不能抛 IndexError，直接丢掉这个块。"""
    messages = compat_to_runtime_messages(
        [HumanMessage(content=[{"type": "image_url", "image_url": {"url": "data:image/png;base64"}}])]
    )
    assert messages[0].content[0].type == "image"

    converted = to_agentscope_messages(messages)
    assert [block for block in converted[0].content if getattr(block, "source", None)] == []
