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

    # 归属过滤必须真的按 created_by 收窄。
    # 加了业务分组页签后，原来的 filteredAgents 拆成两段：
    #   agentsMatchingFilters = 视图 + 关键词 + 全部筛选（不含分组）
    #   filteredAgents        = 上一段结果再套分组，最后排序
    # 归属过滤属于前者——顺带成为「页签数字 = 点进去的条数」的口径来源。
    filter_block = src[
        src.index("const agentsMatchingFilters") : src.index("const agentsMatchingFilters") + 900
    ]
    assert "workspaceTab.value === 'mine'" in filter_block
    assert "a.created_by === me" in filter_block
    # 分组过滤只能在过滤之后的第二段里
    group_block = src[src.index("const filteredAgents") : src.index("const filteredAgents") + 700]
    assert "agentGroupFilter.value" in group_block


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


def test_view_switch_tabs_are_outside_list_only_block():
    """视图切换条与分享面板不能被包进「只显示列表」的 v-if。

    曾经踩过一次：搜索框/筛选横幅的 <template v-if> 结束标签插晚了几行，
    把切换条和两个分享面板一起圈了进去。表现是切到「分享给我 / 我分享的」
    后整片空白——连切回去的按钮都没了。
    """
    src = _read(AGENT_MGMT)
    marker = '<template v-if="workspaceTab === \'mine\' || workspaceTab === \'all\'">'
    start = src.index(marker)
    end = src.index('</template>', start)
    inside = src[start:end]

    for label, needle in (
        ("视图切换条", 'v-for="tab in workspaceTabs"'),
        ("收到的分享面板", "<AgentShareReceivedPanel"),
        ("我分享的面板", "<AgentShareSentPanel"),
    ):
        assert needle not in inside, f"{label} 不能被包进列表专属的 v-if"

    # 搜索框必须对所有视图可见：两个分享面板自己没有搜索，藏掉就等于没法搜
    assert "搜索名称或标识符" not in inside
    assert "搜索智能体或分享人" not in inside

    # 反过来：批量 / 网格列表切换 只服务于智能体列表
    assert "toggleBatchMode" in inside
    # 「筛选」跟在批量之后（同在列表专属块内），但不在页头第一行；
    # 它仍必须只在列表视图出现，否则分享视图会多出一个无意义的入口
    filter_start = src.index("<!-- 高级筛选：状态/类型/引擎/就绪度/来源")
    # 从注释结束（-->）之后再找标签，否则会命中注释自己的 ">"
    after_comment = src.index("-->", filter_start) + 3
    filter_tag = src[after_comment : src.index(">", src.index("<div", after_comment)) + 1]
    assert 'workspaceTab === \'mine\' || workspaceTab === \'all\'' in filter_tag


def test_share_panels_receive_search_keyword():
    """分享视图的搜索框要真的过滤面板内容。

    只把搜索框留在界面上、不把关键词传进面板，等于给了个不生效的输入框——
    比藏起来更糟。
    """
    src = _read(AGENT_MGMT)
    for comp in ("AgentShareReceivedPanel", "AgentShareSentPanel"):
        idx = src.index(f"<{comp}")
        block = src[idx : idx + 300]
        assert ':keyword="searchKeyword"' in block, f"{comp} 没接搜索词"

    for panel_path in (RECEIVED_PANEL, SENT_PANEL):
        panel = _read(panel_path)
        assert "keyword" in panel, f"{panel_path.name} 未声明 keyword 属性"
        assert "visible" in panel, f"{panel_path.name} 未按关键词过滤"


def test_workspace_tabs_scroll_instead_of_wrapping_on_mobile():
    """视图页签条在移动端必须一行横向滚动，不能换行。

    换行会把「专家团」挤到第二行，而这些是并列的视图，分两行读起来像两组
    不同的东西。子项同时要 shrink-0 + whitespace-nowrap，否则 flex 会先压窄
    文字再折行，滚动就失效了。
    """
    page = Path("frontend/src/views/AgentManagement.vue").read_text()
    start = page.index('ref="workspaceTabsRef"')
    block = page[start : page.index('@click="switchWorkspaceTab', start)]

    assert "overflow-x-auto" in block
    assert "flex-wrap" not in block
    assert "shrink-0 whitespace-nowrap" in block
    # 滚动条隐藏，但保留滑动与键盘可达（不设 overflow-hidden）
    assert "[scrollbar-width:none]" in block
    assert "overflow-hidden" not in block


def test_active_workspace_tab_is_scrolled_into_view():
    """滚到看不见的页签被选中时，要自动滚进来——否则用户看不出选中了哪个。"""
    page = Path("frontend/src/views/AgentManagement.vue").read_text()
    assert "const ensureActiveTabVisible" in page
    assert 'inline: "nearest", block: "nearest"' in page
    # 两条进入路径都要覆盖：点击切换、站内信/URL 直达
    assert page.count("ensureActiveTabVisible()") >= 2


def test_group_toggle_stays_outside_the_scrolling_tab_strip():
    """「隐藏分组」不能被塞进页签的滚动容器。

    塞进去的后果是双重的：它会被滚到屏幕外，而且 `ml-auto` 在滚动容器里
    失效（没有剩余空间可分配），窄屏下会被压成一个字一行。
    """
    page = Path("frontend/src/views/AgentManagement.vue").read_text()
    strip = page.index('ref="workspaceTabsRef"')
    strip_close = page.index("</div>", page.index("@click=\"switchWorkspaceTab", strip))
    toggle = page.index("agentGroupTabsVisible ? '隐藏分组'", strip)
    assert toggle > strip_close, "「隐藏分组」仍在滚动容器内"
    # 它**自己的开标签**要有 shrink-0 + nowrap，否则被 flex 压窄后逐字换行。
    # 只看开标签：按钮内部 svg 也带 shrink-0，扫整段会让断言恒真。
    toggle_start = page.rindex("<button", strip_close, toggle)
    toggle_tag = page[toggle_start : page.index(">", toggle_start)]
    assert "shrink-0" in toggle_tag
    assert "whitespace-nowrap" in toggle_tag


def test_header_stacks_vertically_on_mobile():
    """窄屏下标题与操作按钮必须纵向堆叠。

    并排时 375px 宽度里按钮区（发布审核 + 新建智能体 + 下拉）抢走大半，
    标题被压断、状态摘要被挤成一个字一行。
    """
    page = Path("frontend/src/views/AgentManagement.vue").read_text()
    anchor = page.index("flex flex-col gap-3 sm:flex-row sm:items-start")
    head = page[anchor : anchor + 200]
    assert "justify-between" in head


def test_tab_strip_shows_clickable_scroll_arrows():
    """藏了滚动条就必须给可点的翻页箭头。

    否则用户只看到「分享给我」被切一半，第一反应是页面坏了，而不是「可以右滑」。
    箭头按滚动位置出现，滑到头就消失，不长期遮挡页签。
    """
    page = Path("frontend/src/views/AgentManagement.vue").read_text()

    assert 'aria-label="向右查看更多视图"' in page
    assert 'aria-label="向左查看更多视图"' in page
    assert 'v-if="canScrollRight"' in page
    assert 'v-if="canScrollLeft"' in page

    # 位置判断要跟着滚动实时更新，否则滑完箭头还在
    assert '@scroll.passive="updateScrollHints"' in page
    # 把比较运算钉住：只断言状态名存在，改坏边界也照样绿
    assert "el.scrollLeft < max - 4" in page
    assert "el.scrollLeft > 4" in page
    # 首次挂载就要算一次，否则箭头要等第一次滚动才出现
    assert "nextTick(updateScrollHints)" in page


def test_narrow_row_holds_search_review_and_create():
    """窄屏第二行就是主操作条：搜索框 + 审核 + 新建，同一行。

    搜索框不再折叠进页头的放大镜——收起后这一行只剩两颗按钮，
    等于把「搜一下」这个最常用的动作藏起来。
    「筛选」在窄屏不出现：它有 9 项条件，是低频动作，这一行要留给三颗高频入口；
    原来筛选占的那个位置改由「发布审核」顶上（文案收成「审核」）。
    """
    page = Path("frontend/src/views/AgentManagement.vue").read_text()

    assert "mobileSearchOpen" not in page, "搜索框不再有折叠态"
    assert "toggleMobileSearch" not in page

    # 搜索框常显：不能在窄屏挂隐藏
    anchor = page.index('id="agent-search-row"')
    tag = page[page.rindex("<div", 0, anchor) : page.index(">", anchor)]
    assert "hidden" not in tag, "窄屏也要直接显示搜索框"
    assert "flex-1" in tag, "搜索框吃掉剩余宽度，审核与新建才排得进一行"

    # 筛选只在桌面渲染
    filter_note = page.index("<!-- 窄屏不放筛选")
    filter_tag_at = page.index("<div", filter_note)
    filter_tag = page[filter_tag_at : page.index(">", filter_tag_at)]
    assert "!isMobile" in filter_tag, "窄屏不显示筛选"

    # 顺序：搜索 → 批量/视图（桌面）→ 筛选（桌面）→ 审核（窄屏）→ 新建（窄屏）
    row_start = page.index("<!-- 第 2 行：搜索独占一栏")
    row = page[row_start : page.index("<!-- 智能体 5 步全流程指引", row_start)]
    positions = [
        row.index('id="agent-search-row"'),
        row.index("toggleBatchMode"),
        row.index('aria-label="筛选"'),
        row.index('v-if="canReviewSystem && isMobile"'),
        row.index('<AgentCreateButton\n        v-if="isMobile"'),
    ]
    assert positions == sorted(positions), "顺序应为 搜索 → 批量/视图 → 筛选 → 审核 → 新建"

    # 窄屏那颗审核只留图标：多两个字就把搜索框压窄一截
    review_at = row.index('v-if="canReviewSystem && isMobile"')
    review_chunk = row[review_at : row.index("</button>", review_at)]
    assert 'aria-label="审核"' in review_chunk, "纯图标按钮必须有可读名称"
    assert "<svg" in review_chunk, "图标要在"
    # 去掉无障碍名称与 title 后，按钮里不该再有可见的「审核」文本
    visible = review_chunk.replace('aria-label="审核"', "").replace(
        "查看待审核的「发布为系统级」申请", ""
    )
    assert "审核" not in visible, "窄屏不显示文字，只留图标"
    # 待审数量用角标，不占按钮宽度
    assert "absolute -right-1 -top-1" in review_chunk


def test_desktop_header_keeps_create_but_narrow_hides_it():
    """页头动作区（桌面）：显示指引 / 发布审核 / 新建。

    窄屏只留「发布审核」这类低频入口，新建让位到搜索行末尾（见上一条用例）。
    动作区仍然不能成为滚动容器——新建下拉是 absolute 定位，会被裁掉。
    """
    page = Path("frontend/src/views/AgentManagement.vue").read_text()

    bar_start = page.index("flex flex-wrap items-center gap-1.5")
    bar = page[bar_start : page.index("<!-- 搜索、筛选与两个横幅", bar_start)]
    bar_tag = bar.split(">")[0]
    assert "overflow-x-auto" not in bar_tag, "动作区不能成为滚动容器（会裁掉 absolute 面板）"
    assert "flex-wrap" in bar_tag, "放不下时靠换行兜底"
    assert "sm:flex-nowrap" in bar_tag, "桌面端不换行"

    positions = [
        bar.index("显示指引"),
        bar.index("发布审核"),
        bar.index('<AgentCreateButton\n          v-if="!isMobile"'),
    ]
    assert positions == sorted(positions), "顺序应为 显示指引 → 发布审核 → 新建"

    # 搜索与筛选都已经搬到第二行，不该再出现在页头
    assert 'aria-controls="agent-search-row"' not in bar
    assert 'aria-label="筛选"' not in bar

    # 短文案只作用于移动端，桌面保留全称（新建那半句已随组件搬走）
    create_btn = Path("frontend/src/components/agent/AgentCreateButton.vue").read_text()
    assert '新建<span class="hidden sm:inline">智能体</span>' in create_btn
    assert '<span class="hidden sm:inline">筛选</span>' in page
