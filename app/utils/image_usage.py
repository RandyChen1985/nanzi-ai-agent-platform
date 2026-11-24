"""从工具输出里回捞图像用量（历史兼容）。

## 为什么需要回捞

2026-11-23 之前，`generate_image` / `edit_image` 走的是工具包装层的**非流式**返回
路径，而计量摘取只写在了流式那条路径上。结果是：用量没进 `ToolChunk` 元数据，整份
工具结果（含 `image_usage`）被 json 序列化进了执行轨迹的 `tool_output.raw`，而
`meta_info.image_usage` 是空的。

那段代码已经修了（两条路径共用同一套摘取），但**修之前产生的调用还在库里**。统计页
与历史接口如果只读 `meta_info`，那些已经花过钱的调用在页面上永远是 0——用户会以为
「修了还是没数」。

## 口径

只认实测到的两种形态：`{"raw": "<json 字符串>"}` 与直接给出的 dict。认不出就返回
`None`，不做猜测——宁可少算，也不能把别人的数字摊到这次调用上。
"""

from __future__ import annotations

import json
from typing import Any, Optional

#: 会产生计量的图像工具。回捞只在这几个工具上做：`tool_output` 是整段工具结果，
#: 对其它工具做无谓的 json 解析既慢又容易被同名键骗到。
IMAGE_USAGE_TOOL_NAMES = frozenset({"generate_image", "edit_image"})


def _json_load(value: Any) -> Any:
    """宽容地把 str/bytes 解析成对象；解析不了就原样返回。"""
    if isinstance(value, (bytes, bytearray)):
        try:
            value = value.decode("utf-8")
        except UnicodeDecodeError:
            return None
    if isinstance(value, str):
        try:
            return json.loads(value)
        except (TypeError, ValueError):
            return None
    return value


def image_usage_from_tool_output(tool_output: Any) -> Optional[dict]:
    """从落库的工具输出里取出 `image_usage`；没有就返回 None。"""
    payload = _json_load(tool_output)
    if isinstance(payload, dict) and "raw" in payload:
        # 轨迹落库时非 dict/list 结果会被包成 {"raw": "<文本>"}，量就在这层里面
        payload = _json_load(payload.get("raw"))
    if not isinstance(payload, dict):
        return None
    usage = payload.get("image_usage")
    return usage if isinstance(usage, dict) and usage else None
