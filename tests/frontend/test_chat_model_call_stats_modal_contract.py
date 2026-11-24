"""「大模型调用明细指标」弹窗：图片消耗是附加信息，不能把 token 明细挤掉。

## 真实故障（2026-11-23）

弹窗里新加的「图片消耗」块写成了 `v-if`，而紧随其后的空态是 `v-else-if`、明细整块
是 `v-else` —— 三者在 Vue 里构成**同一条互斥链**。于是只要这条消息带图片，明细就不
渲染：用户点开弹窗只看得到「图片消耗 出图 1 张 · 1 次调用」一行，原本的调用次数 /
总耗时 / 输入 / 输出 / 缓存命中全都不见了，看起来像「图片消息不再统计 token」。

## 契约

一条消息**既可能有模型调用、又有生图调用**，这两块信息是并列的，必须各自判定、
同时可见。图片块不许参与明细的 `v-if` / `v-else` 链。
"""
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
MODAL = ROOT / "frontend/src/components/chat/ChatModelCallStatsModal.vue"


def _source() -> str:
    assert MODAL.exists(), f"缺少弹窗组件：{MODAL}"
    return re.sub(r"\s+", " ", MODAL.read_text(encoding="utf-8"))


def test_token_breakdown_is_not_mutually_exclusive_with_image_usage():
    """空态与明细要用显式 v-if 判定，不能挂在图片块的 v-else 链上。"""
    source = _source()

    assert 'v-else-if="stats.length === 0"' not in source, (
        "空态仍是 v-else-if：会继续与图片块互斥，带图片的消息看不到明细"
    )
    assert 'v-if="!loading && stats.length === 0"' in source, "空态要独立判定且避开 loading"
    assert 'v-if="!loading && stats.length > 0"' in source, "明细要独立判定且避开 loading"


def test_image_usage_block_still_renders():
    """修互斥不能顺手把图片那一行删了——它才是「这张图花了多少」的唯一线索。"""
    source = _source()

    assert "图片消耗" in source
    assert 'v-if="!loading && imageUsage && imageUsage.calls"' in source
    # 出图张数、调用次数、尺寸都得留着
    assert "出图 {{ imageUsage.images }} 张" in source
    assert "{{ imageUsage.calls }} 次调用" in source
    assert "imageUsage.size" in source
