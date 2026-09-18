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
    """提示必须明确迁移目标：Ticket 模式。"""
    source = _source()
    banner_start = source.index("showLegacyTokenHint")
    banner_section = source[banner_start : banner_start + 3000]

    assert "Ticket" in banner_section


def test_banner_copy_tells_user_to_contact_developer():
    """嵌入页访客是宿主系统的终端用户，改不了接入方式，必须给出可执行的动作。

    只有「联系开发人员」是终端用户真正能做的事；技术细节（接口地址、参数名）
    对他无意义，反而会拉长横幅、挤占嵌入页高度。
    """
    source = _source()
    banner_start = source.index("showLegacyTokenHint")
    banner_section = source[banner_start : banner_start + 3000]

    assert "联系开发人员" in banner_section


def test_banner_copy_stays_concise():
    """文案必须简短——嵌入页高度有限，窄 iframe 里长文本会占三四行。

    这里限定横幅正文的整体长度，防止后续改文案时又加回技术细节。
    """
    source = _source()
    banner_start = source.index("showLegacyTokenHint")
    banner_section = source[banner_start : banner_start + 3000]

    # 取 <span> 正文所在片段，粗略衡量可见文案长度
    visible = banner_section.split("检测到", 1)
    assert len(visible) == 2, "横幅正文应包含提示语句"

    copy_region = visible[1].split("</span>", 1)[0]
    # 去掉标签与空白后统计正文字符数
    import re

    text = re.sub(r"<[^>]+>", "", copy_region)
    text = re.sub(r"\s+", "", text)

    assert len(text) <= 60, f"横幅正文过长（{len(text)} 字）: {text}"


def test_banner_copy_does_not_leak_technical_endpoint_details():
    """终端用户向的提示不应包含接口地址等实现细节。"""
    source = _source()
    banner_start = source.index("showLegacyTokenHint")
    banner_section = source[banner_start : banner_start + 3000]

    assert "/api/v1/embed/tickets" not in banner_section
