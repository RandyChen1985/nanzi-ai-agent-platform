"""嵌入页 URL 直传 Key 升级提示契约（源码级）。

背景：存量业务仍在使用 `/embed/chat?token=<API Key>` 的兼容入口。该方式会把长期
密钥暴露在浏览器地址栏（历史记录、Referer、代理日志），因此需要在 embed 页顶部
提示宿主尽早迁移到 Ticket 模式（`?ticket=emt_*`）。

本契约锁定：
1. **判定入口唯一**——只有 `else if (query.get("token"))` 分支标记为 token 方式；
   ticket 分支不得标记（ticket 优先，且是推荐方式，不该被提示）。
2. **判定与校验结果解耦**——即使 Key 无效也应提示，因为提示的是「接入方式」。
3. **忽略键三级回退**——instance_id → agent_id → 全局，顺序不可颠倒。
4. **关闭与忽略目标不同**——关闭只写内存（刷新重现），忽略才写 localStorage（永久）。
5. **文案指向 Ticket 模式**——必须给出 `POST /api/v1/embed/tickets` 作为迁移目标。
"""
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
EMBED_CHAT = ROOT / "frontend/src/views/EmbedChat.vue"

pytestmark = pytest.mark.no_infrastructure

TOKEN_BRANCH_MARKER = '} else if (query.get("token")) {'
AGENT_ID_MARKER = 'if (query.get("agent_id"))'
TICKET_BRANCH_MARKER = "if (ticketFromUrl) {"


def _source() -> str:
    return EMBED_CHAT.read_text(encoding="utf-8")


def _token_branch(source: str) -> str:
    """截取 `else if (query.get("token"))` 分支体。"""
    start = source.index(TOKEN_BRANCH_MARKER)
    end = source.index(AGENT_ID_MARKER, start)
    return source[start:end]


def _ticket_branch(source: str) -> str:
    """截取 `if (ticketFromUrl)` 分支体（到 token 分支为止）。"""
    start = source.index(TICKET_BRANCH_MARKER)
    end = source.index(TOKEN_BRANCH_MARKER, start)
    return source[start:end]


def _function_body(source: str, name: str) -> str:
    """截取以 `const <name> =` 或 `function <name>(` 开头的函数体。"""
    for opener in (f"const {name} =", f"function {name}("):
        if opener in source:
            start = source.index(opener)
            # 粗略截到下一个顶层 const/function 声明
            rest = source[start:]
            for terminator in ("\nconst ", "\nfunction ", "\n};"):
                idx = rest.find(terminator, len(opener))
                if idx != -1:
                    return rest[:idx]
            return rest
    raise AssertionError(f"未找到函数 {name}")


# --- ① 判定入口唯一 ---


def test_token_branch_marks_legacy_url_token():
    """token 分支必须打上「本次是 URL 直传方式」的标记。"""
    branch = _token_branch(_source())

    assert "usesLegacyUrlToken.value = true" in branch


def test_ticket_branch_does_not_mark_legacy_url_token():
    """ticket 是推荐方式，绝不能触发升级提示。"""
    branch = _ticket_branch(_source())

    assert "usesLegacyUrlToken" not in branch


def test_marking_is_independent_of_validation_outcome():
    """标记必须发生在校验之前，与 Key 是否有效无关。

    `config.token = token` 只是把凭据放进内存参与后续校验；标记不应出现在任何
    `if (ok)` / `if (!ok)` 之类的校验结果分支里。
    """
    branch = _token_branch(_source())

    assert "usesLegacyUrlToken.value = true" in branch
    # 标记行不应被包在响应式校验结果里
    assert "if (ok) usesLegacyUrlToken" not in branch


# --- ② 忽略键三级回退 ---


def test_ignore_key_fallback_order_is_instance_then_agent_then_global():
    """忽略键回退顺序：instance_id → agent_id → 全局，不可颠倒。

    instance_id 隔离性最好；agent_id 次之；两者都缺才退化为全局。
    """
    body = _function_body(_source(), "resolveLegacyTokenHintStorageKey")

    assert "instanceId" in body
    assert "agentId" in body
    assert body.index("instanceId") < body.index("agentId")


def test_ignore_key_is_namespaced_under_yovole_prefix():
    """存储键沿用项目现有 `yovole_` 前缀惯例。"""
    source = _source()

    assert "yovole_embed_legacy_token_hint_ignored" in source


# --- ③ 关闭与忽略目标不同 ---


def test_dismiss_only_writes_memory():
    """「关闭」只影响本次会话，不得写 localStorage。"""
    body = _function_body(_source(), "dismissLegacyTokenHint")

    assert "Dismissed" in body or "dismissed" in body
    assert "localStorage" not in body


def test_ignore_persists_to_localstorage():
    """「不再提示」必须写 localStorage 才能永久生效。"""
    body = _function_body(_source(), "ignoreLegacyTokenHint")

    assert "localStorage.setItem" in body
    assert "Ignored" in body or "ignored" in body


def test_banner_visibility_requires_all_three_conditions():
    """可见性 = 是 token 方式 且 未关闭 且 未忽略。"""
    source = _source()
    body = _function_body(source, "showLegacyTokenHint")

    assert "usesLegacyUrlToken" in body
    assert "Dismissed" in body
    assert "Ignored" in body


# --- ④ 文案指向 Ticket 模式 ---


def test_banner_copy_points_to_ticket_mode():
    """提示必须明确迁移目标：Ticket 模式及其签发接口。"""
    source = _source()
    banner_start = source.index("showLegacyTokenHint")
    banner_section = source[banner_start : banner_start + 3000]

    assert "/api/v1/embed/tickets" in banner_section
    assert "ticket" in banner_section.lower()


def test_banner_copy_mentions_api_key_in_url():
    """提示需要说明当前用的是「URL 直传 API Key」，否则用户不知道在说什么。"""
    source = _source()
    banner_start = source.index("showLegacyTokenHint")

    assert "API Key" in source[banner_start : banner_start + 3000]
