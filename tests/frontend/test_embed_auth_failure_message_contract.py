"""嵌入页认证失败提示必须与真实原因一致、且给出可行动的下一步。

## 背景

原先 `EmbedChat.vue` 的「无访问权限」遮罩是**硬编码单一文案**：

    认证失败，请检查您的账号是否有权限访问！

无论失败原因是什么（Ticket 已核销 / 缺少凭证 / 密钥无效 / 会话过期）都显示这一句。
对最常见的 **Ticket 一次性核销**场景（用户只是按了 F5）这是**误导**：ticket 已被
`GETDEL` 核销（`embed_service.py`），用户去检查账号权限、改角色配置都**不可能解决**，
而真正该做的是「回到宿主系统重新打开页面」。

## 要求

1. 存在按原因区分的状态（`authFailureReason`）与展示映射（`authFailureView`）。
2. `invalid_ticket` 的文案必须点明**一次性 / 不可重复使用**，并给出下一步，
   且**不得**再让用户去检查账号权限。
3. 每个上报 `INIT_FAILURE` 的失败分支都必须同时设置原因，避免回落成笼统文案。
4. 模板不得再硬编码那句权限文案，须改由映射提供。
"""
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
EMBED_CHAT = ROOT / "frontend/src/views/EmbedChat.vue"

# 旧文案：对 Ticket 场景是误导，不应再作为唯一提示
MISLEADING_TEXT = "认证失败，请检查您的账号是否有权限访问！"


def _source() -> str:
    return EMBED_CHAT.read_text(encoding="utf-8")


def test_auth_failure_reason_state_and_view_mapping_exist():
    """必须存在失败原因状态与展示映射，而非单一硬编码文案。"""
    source = _source()
    assert "authFailureReason" in source, "缺少按原因区分的失败状态"
    assert "authFailureView" in source, "缺少失败提示的展示映射"


def test_invalid_ticket_message_is_actionable_and_not_permission_blame():
    """Ticket 失败应说明「一次性、不可重复使用」，而不是让用户查账号权限。"""
    source = _source()
    # 锚定定义本身：模板中的 `{{ authFailureView.title }}`（行 ~319）出现在
    # <script> 里的定义（行 ~6424）之前，用 index("authFailureView") 会取到模板用法。
    view_start = source.index("const authFailureView")
    view_block = source[view_start : view_start + 1600]

    assert "invalid_ticket" in view_block, "映射中缺少 invalid_ticket 分支"
    assert "一次性" in view_block or "不可重复" in view_block, (
        "invalid_ticket 文案应点明凭证为一次性、不可重复使用"
    )

    # 该分支自身不得把责任推给「账号权限」
    ticket_branch = view_block[view_block.index("invalid_ticket") :]
    ticket_branch = ticket_branch[: ticket_branch.index("}") if "}" in ticket_branch else 600]
    assert "检查您的账号" not in ticket_branch, (
        "invalid_ticket 不应再提示用户去检查账号权限（改权限无法解决核销问题）"
    )


def test_every_init_failure_reports_a_reason():
    """每个 INIT_FAILURE 上报点都必须设置 authFailureReason。"""
    source = _source()
    missing = []
    idx = 0
    while True:
        idx = source.find('reason: "', idx)
        if idx == -1:
            break
        # 取该上报点前 400 字符，检查是否设置了原因
        window_start = max(0, idx - 400)
        window = source[window_start:idx]
        if "authFailureReason.value" not in window:
            line_no = source[:idx].count("\n") + 1
            missing.append(line_no)
        idx += 1

    assert not missing, (
        f"以下 INIT_FAILURE 上报点未设置 authFailureReason，会回落成笼统文案：行 {missing}"
    )


def test_template_uses_mapping_instead_of_hardcoded_permission_text():
    """模板须改由映射提供文案，而非硬编码权限提示。"""
    source = _source()
    # 根模板内含多个内联 <template>（v-if / 具名插槽），首个 </template> 是内联块的
    # 结束（行 ~30），会误截根模板；根模板的结束是最后一个 </template>。
    template_start = source.index("<template>")
    template_end = source.rindex("</template>")
    template = source[template_start:template_end]

    assert MISLEADING_TEXT not in template, (
        "模板仍硬编码误导性的权限文案，应改由 authFailureView 提供"
    )
    assert "authFailureView" in template, "模板未使用 authFailureView 渲染失败提示"
