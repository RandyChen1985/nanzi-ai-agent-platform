"""智能体分享的前端契约。

分享的界面边界比接口更容易被改坏：入口显示条件、语义文案、接受时的重命名、
以及「对方到底在哪看到」。最后这条踩过坑——最初做成「有待处理才出现的弹窗」，
接收方根本不知道去哪儿找，后来对齐成技能 / MCP 分享的页面级视图。
"""
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
AGENT_MGMT = REPO_ROOT / "frontend/src/views/AgentManagement.vue"
SHARE_MODAL = REPO_ROOT / "frontend/src/components/agent/AgentShareModal.vue"
RECEIVED_PANEL = REPO_ROOT / "frontend/src/components/agent/AgentShareReceivedPanel.vue"
SENT_PANEL = REPO_ROOT / "frontend/src/components/agent/AgentShareSentPanel.vue"
HISTORY_DRAWER = REPO_ROOT / "frontend/src/components/agent/AgentSystemHistoryDrawer.vue"
NOTIFICATION_BELL = REPO_ROOT / "frontend/src/components/PortalNotificationBell.vue"
AGENT_API = REPO_ROOT / "frontend/src/api/agent.ts"


def _read(path: Path) -> str:
    assert path.exists(), f"缺少文件：{path}"
    return path.read_text(encoding="utf-8")


def test_share_entry_hidden_for_system_agents():
    """系统级智能体不显示分享入口——可用范围只能由管理员授权治理。"""
    src = _read(AGENT_MGMT)
    assert "const canShareAgent" in src
    block = src[src.index("const canShareAgent") : src.index("const canShareAgent") + 800]
    assert "agent.is_system" in block
    assert "if (agent.is_system) return false" in block


def test_share_entry_limited_to_owner():
    """普通用户只能分享自己创建的智能体；管理员可代为分享。"""
    src = _read(AGENT_MGMT)
    block = src[src.index("const canShareAgent") : src.index("const canShareAgent") + 800]
    assert "created_by === userInfo.value?.user_name" in block
    assert "isMainAgent(agent)" in block


def test_share_is_page_level_view_not_modal():
    """分享必须是页面级视图，与技能 / MCP 分享一致。

    弹窗方案已经验证过不行：入口「有待处理才出现」，接收方找不到，
    而且发出去什么状态也查不到。
    """
    src = _read(AGENT_MGMT)
    assert "AgentShareReceivedPanel" in src
    assert "AgentShareSentPanel" in src
    assert "workspaceTab" in src
    # 已删除的弹窗不能回流
    assert "AgentShareInboxModal" not in src
    assert not (REPO_ROOT / "frontend/src/components/agent/AgentShareInboxModal.vue").exists()


def test_share_tabs_have_badges_and_url_entry():
    """三个平级视图 + 角标，且支持 ?agents_tab= 直接落位（站内信要用）。"""
    src = _read(AGENT_MGMT)
    assert "我的智能体" in src and "分享给我" in src and "我分享的" in src
    assert "received_pending" in src
    assert "sent_pending" in src
    assert "agents_tab" in src
    assert "resolveInitialTab" in src


def test_share_modal_states_copy_semantics():
    """弹窗必须说清「接受后得到的是副本」，否则会被理解成授权共用或转交。"""
    src = _read(SHARE_MODAL)
    assert "属于他自己的副本" in src
    assert "重新命名" in src
    assert "自动剔除" in src
    assert "recipient_user_names" in src
    assert "recipient_user_ids" not in src


def test_share_never_mutates_ownership_in_ui():
    """分享的 UI 不能出现「权属」「转交」这类词，避免与变更权属人混淆。"""
    modal = _read(SHARE_MODAL)
    sent = _read(SENT_PANEL)
    for src in (modal, sent):
        for word in ("变更权属", "权属人", "转交"):
            assert word not in src, f"分享界面不应该出现「{word}」"


def test_received_panel_requires_explicit_rename():
    """接受分享时要能改名；留空由服务端生成不冲突的名字。"""
    src = _read(RECEIVED_PANEL)
    assert "acceptShare" in src
    assert "suggestedName" in src
    assert "-copy" in src
    # 允许留空：服务端会给一个不冲突的名字，不要因为重名把人卡住
    assert "newName.value.trim() || undefined" in src


def test_sent_panel_supports_withdraw():
    """我发出的分享要能撤回——过期是自动的，但「发错了想收回」得给入口。"""
    src = _read(SENT_PANEL)
    assert "listSentShares" in src
    assert "withdrawShare" in src
    assert "撤回" in src


def test_notification_jumps_to_share_view():
    """站内信点击要直接落到「分享给我」，不能只给标题正文。"""
    src = _read(NOTIFICATION_BELL)
    assert "isAgentShareNotification" in src
    assert 'resource_type === "agent_share"' in src
    assert "/dashboard/agent-management" in src
    assert "agents_tab" in src
    assert "智能体分享" in src


def test_backend_notification_carries_jump_meta():
    """后端分享通知要带 resource_type / metadata，否则前端无从判断落点。"""
    notifier = (REPO_ROOT / "app/services/ai/agent_system_notifier.py").read_text(encoding="utf-8")
    assert 'resource_type="agent_share"' in notifier
    assert 'metadata={"open_tab": "received"}' in notifier


def test_dedicated_share_candidates_endpoint():
    """分享候选走专用接口：转交候选要求管理员权限，普通用户会拿到空列表。"""
    api_src = _read(AGENT_API)
    assert "shareCandidates" in api_src
    assert "/api/portal/agents/shares/candidates" in api_src

    mgmt_src = _read(AGENT_MGMT)
    block = mgmt_src[
        mgmt_src.index("const loadShareCandidates") : mgmt_src.index("const loadShareCandidates") + 400
    ]
    assert "shareCandidates" in block


def test_share_events_visible_in_history():
    """分享动作要进发布历史，各状态都要有可读标签。"""
    src = _read(HISTORY_DRAWER)
    for action in ("SHARE_SEND", "SHARE_ACCEPT", "SHARE_REJECT", "SHARE_REVOKE"):
        assert action in src, f"发布历史缺少 {action} 的展示配置"
    assert "分享给同事" in src


def test_received_panel_shows_snapshot_summary():
    """列表要能看出「这个智能体是干嘛的」。

    只显示名字和分享时间时，名字叫 dddd 的话接收方根本判断不了要不要接受。
    模型、工具数、提示词开头都比名字有信息量。
    """
    src = _read(RECEIVED_PANEL)
    assert "record.summary" in src
    for field in ("model_name", "tool_count", "prompt_length", "system_prompt", "tool_names"):
        assert field in src, f"收件列表缺少摘要字段 {field}"


def test_accept_goes_through_onboarding_wizard():
    """接受分享后要走进新建向导（从重命名那一步开始），不是直接丢一个可用副本。"""
    mgmt = _read(AGENT_MGMT)
    assert "continueAgentOnboarding" in mgmt
    # 三步向导都要能落位，BASIC 不能被当成 RESOURCE
    block = mgmt[mgmt.index("const continueAgentOnboarding") : mgmt.index("const continueAgentOnboarding") + 1600]
    assert 'agent.onboarding_step === "BASIC"' in block
    assert "onboardingStep.value" in block


def test_admin_has_all_agents_tab():
    """管理员要有「全部智能体」，「我的智能体」只看自己名下。"""
    src = _read(AGENT_MGMT)
    assert "workspaceTabs" in src
    assert "全部智能体" in src
    # tab 只在管理员时出现
    tabs_block = src[src.index("const workspaceTabs") : src.index("const workspaceTabs") + 700]
    assert "isAdminViewer.value" in tabs_block

    # 归属过滤必须真的按 created_by 收窄
    filter_block = src[src.index("const filteredAgents"): src.index("const filteredAgents") + 700]
    assert "workspaceTab.value === 'mine'" in filter_block
    assert "a.created_by === me" in filter_block


def test_admin_default_tab_does_not_shrink_scope():
    """管理员默认落在「全部」，加视图不能让他看到的范围突然变小。"""
    src = _read(AGENT_MGMT)
    block = src[src.index("const resolveInitialTab") : src.index("const resolveInitialTab") + 500]
    assert "isAdminViewer.value ? 'all' : 'mine'" in block


def test_review_drawer_tolerates_deleted_agent():
    """孤儿申请单（智能体已删）不能让审核抽屉崩掉，要能直接驳回关闭。

    线上出现过「角标 1 条、点进去空」，根因是 INNER JOIN 把孤儿单从列表里滤掉了。
    """
    src = _read(REPO_ROOT / "frontend/src/components/agent/AgentSystemReviewDrawer.vue")
    assert "agent_missing" in src
    assert "智能体已删除" in src
    # 搜索不能对 null 调 toLowerCase
    assert "(item.agent_identifier || '')" in src


def test_review_drawer_supports_closing_orphan_request():
    """孤儿申请单点进去要能关掉：驳回按钮必须出现，通过按钮要隐藏。

    只修列表不修详情是不够的——按钮区挂 ``v-if="detail"``，详情 404 就整块不渲染，
    用户会看到一张有标题但没有操作按钮的卡片。
    """
    src = _read(REPO_ROOT / "frontend/src/components/agent/AgentSystemReviewDrawer.vue")
    assert "detail.agent_missing" in src
    assert "驳回并关闭" in src
    # 孤儿单不该给出「通过并发布」
    assert 'v-if="!detail.agent_missing"' in src


def test_editing_returns_current_is_system():
    """更新智能体时必须回传当前 is_system，否则管理员改系统级必然 400。

    后端 AIAgentBase.is_system 缺省是 False；省略该字段就等于「想把它改成非系统级」，
    与系统级(True) 不等直接 400。表现是管理员对除主助手外的任何系统级智能体
    改个描述都保存失败。
    """
    src = _read(AGENT_MGMT)
    block = src[src.index("if (isEditingAgent.value && selectedAgent.value)"):]
    block = block[: block.index("} else if")]
    assert "is_system: selectedAgent.value.is_system" in block


def test_external_engine_agents_cannot_be_shared():
    """RAGFlow / OpenClaw 不支持分享。

    这两类引擎的 engine_config 指向同一个远端 app/dataset，复制出去的副本与源
    共用一个远程实例，「副本互不影响」不成立——接收方改知识库配置会动到分享者的
    数据面。所以界面要隐藏入口，后端也要拦。
    """
    src = _read(AGENT_MGMT)
    block = src[src.index("const canShareAgent") : src.index("const canShareAgent") + 800]
    assert 'agent.engine_type || "LOCAL"' in block
    assert "!== \"LOCAL\"" in block
