"""历史消息的图片用量必须用**同一个**累加函数聚合。

## 背景

实时链路：SSE 的 `image_usage` 事件 → `accumulateImageUsage` 累加成
`msg.image_usage = {calls, images, requested, tokens, size}`。

历史链路（刷新后）：`GET /chat/history` 回传本轮图像调用的**原始记录数组**，
字段与轨迹 `meta_info.image_usage` 一致。它必须喂给**同一个** `accumulateImageUsage`。

## 为什么要这条契约

如果历史那侧自己写一遍聚合，就会出现「刚生成时显示出图 2 张、刷新后显示 1 张」
这种最难排查的 bug —— 两个数都"看起来对"，只是规则不一致。所以这里既锁住
「必须复用同一个函数」，也锁住后端白名单里有前端需要的键。
"""
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
EMBED_CHAT = ROOT / "frontend/src/views/EmbedChat.vue"
CHAT_API = ROOT / "app/api/v1/endpoints/chat.py"


def _source(path: Path) -> str:
    return path.read_text(encoding="utf-8")


def _normalized(path: Path) -> str:
    return re.sub(r"\s+", " ", _source(path))


def test_embed_chat_imports_the_shared_accumulator():
    """累加规则只有一份实现，历史链路必须导入实时链路那个函数。"""
    source = _normalized(EMBED_CHAT)

    assert "accumulateImageUsage" in source
    assert re.search(r"import\s*\{[^}]*accumulateImageUsage", source), (
        "应从 agentscopeSseHandlers 导入 accumulateImageUsage，而不是自己算"
    )


def test_history_restore_folds_backfilled_usage_through_it():
    """回填的原始记录要逐条喂给累加函数，而不是整体赋给消息。"""
    source = _normalized(EMBED_CHAT)

    assert re.search(r"item\.image_usage", source), "历史消息要读后端回填的 image_usage"
    assert re.search(r"for \(const usage of item\.image_usage\)", source)
    assert re.search(r"accumulateImageUsage\(restoredAgentMessage, usage\)", source)


def test_embed_chat_does_not_reimplement_the_aggregation():
    """前端不许出现第二套聚合规则。"""
    source = _normalized(EMBED_CHAT)

    for duplicated in (
        "images: prev.images +",
        "requested: prev.requested +",
        "tokens: prev.tokens +",
    ):
        assert duplicated not in source, (
            f"聚合规则重复实现：{duplicated}。历史与实时必须共用 accumulateImageUsage"
        )


def test_backend_whitelist_keeps_fields_the_frontend_reads():
    """后端白名单漏掉任何一个键，前端那一行就静默变成 0 张。"""
    source = _normalized(CHAT_API)

    for key in ('"succeeded_count"', '"requested_count"', '"upstream_tokens"', '"size"'):
        assert key in source, f"回填白名单缺少前端要读的键：{key}"
