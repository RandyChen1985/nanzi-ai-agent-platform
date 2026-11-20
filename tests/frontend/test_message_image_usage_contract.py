"""消息「用量」里图片消耗的展示契约。

AI 消息右侧那个「用量 1.2k」按钮，口径一直是 LLM 的输入+输出 token（累加自
`model_call` 事件）。图像工具不走 model_call，所以生图/改图花掉的那部分**不在里面**——
用户点开只会看到 LLM 的账，容易以为生图没花钱。

这里把补充链路钉住：

```
图像工具 image_usage
  → 工具日志事件(SSE) → msg.image_usage（累加）
  → 用量 tooltip 多一行 / 调用明细弹窗多一块
```

**刻意不并入 `total_tokens`**：那个数字全站按 LLM token 口径统计，月度配额也按它扣，
把图片混进去会让「消息上显示的用量」和「配额实际消耗」变成两套算法。所以单独记一份。
"""

from pathlib import Path

import pytest


pytestmark = pytest.mark.no_infrastructure

ROOT = Path(__file__).resolve().parents[2]

RUNNER = "app/services/ai/runners/assistant_agent_runner.py"
SSE = "frontend/src/utils/agentscopeSseHandlers.ts"
TOKEN_FORMAT = "frontend/src/utils/tokenFormat.ts"
EMBED_CHAT = "frontend/src/views/EmbedChat.vue"
STATS_MODAL = "frontend/src/components/chat/ChatModelCallStatsModal.vue"


def _read(relative_path: str) -> str:
    return (ROOT / relative_path).read_text(encoding="utf-8")


def test_backend_reports_usage_on_the_tool_log_event():
    """后端必须把 image_usage 放进工具日志事件，否则前端根本收不到。"""
    source = _read(RUNNER)
    assert 'log_event["image_usage"] = image_usage' in source


def test_frontend_accumulates_per_message():
    """一条消息里可能生好几次图，必须累加；只留最后一次会少算钱。"""
    sse = _read(SSE)
    assert "accumulateImageUsage(msg, data.image_usage);" in sse
    assert "export function accumulateImageUsage" in sse
    # 累加而不是覆盖
    assert "prev.calls + 1" in sse
    assert "prev.images + succeeded" in sse
    assert "prev.tokens + Number(" in sse
    # 字段挂在消息上
    assert "image_usage?: MessageImageUsage;" in sse


def test_usage_is_not_folded_into_total_tokens():
    """总 token 的口径不能变：它驱动月度配额，混进图片就两套算法了。"""
    embed = _read(EMBED_CHAT)
    # 按钮上的数字仍只认 LLM token 三项
    assert (
        "const total = msg?.total_tokens ?? ((msg?.prompt_tokens || 0) + (msg?.completion_tokens || 0));"
        in embed
    )
    # 图片只走 tooltip 的第 4 个参数
    assert "formatImageUsageLine(msg?.image_usage)" in embed


def test_tooltip_gains_one_line_only_when_there_are_images():
    source = _read(TOKEN_FORMAT)
    assert "export interface MessageImageUsage" in source
    assert "export function formatImageUsageLine" in source
    # 没有图片时返回空串，tooltip 不多一行
    assert 'if (!usage || !usage.calls) return "";' in source
    # 部分出图要给出两个数
    assert "usage.requested > usage.images" in source
    # 上游没给 token 就不写 0
    assert "if (usage.tokens > 0)" in source
    # tooltip 用参数而不是字符串拼接 hack
    assert "imageLine?: string," in source
    assert "if (imageLine) lines.push(imageLine)" in source


def test_stats_modal_lists_image_spend_separately():
    """明细弹窗只列 /model_calls，图片必须单独一块，否则点开等于没看见。"""
    embed = _read(EMBED_CHAT)
    assert "currentImageUsage.value = msg?.image_usage ?? null;" in embed
    assert ':image-usage="currentImageUsage"' in embed

    modal = _read(STATS_MODAL)
    assert "imageUsage?: MessageImageUsage | null;" in modal
    assert 'v-if="!loading && imageUsage && imageUsage.calls"' in modal
    assert "图片消耗" in modal
