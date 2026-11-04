"""智能体中心：筛选面板要有「专家团主专家 / 子专家 / 所属团」三个团队条件。

为什么需要：「这个团到底有谁」是用户最常问的问题，但答案分散在两处——团卡片上只有
成员数量，名单要进详情抽屉才看得到。这次把团队关系做成可筛选的维度，并且：

- 前两个条件是「是 / 否」，第三个按**团主 id** 过滤（而不是团名，团改名不会让已生效
  的筛选悄悄失效）；
- 「所属团」同时收**团主本人和它的成员**——问「这个团有谁」应该包含带队的人；
- 卡片上的子专家徽标可点：单团直接筛，多团先弹出团列表让用户选；
- **术语统一用「子专家」**：产品里 `team_member_count` 的文案就是「N 位子专家」，
  代码里不允许再出现「子代理」这种同义不同名的说法。
"""

from pathlib import Path

import pytest

pytestmark = pytest.mark.no_infrastructure

ROOT = Path(__file__).resolve().parents[2]
PAGE = ROOT / "frontend/src/views/AgentManagement.vue"


def _read() -> str:
    assert PAGE.exists(), f"文件不存在：{PAGE}"
    return PAGE.read_text(encoding="utf-8")


def test_three_team_filters_are_declared():
    page = _read()
    assert 'const teamLeadFilter = ref<"all" | "yes" | "no">("all")' in page
    assert 'const subAgentFilter = ref<"all" | "yes" | "no">("all")' in page
    # 第三个存团主 id：用名字做 key，团改名后筛选会静默失效
    assert 'const teamFilter = ref<"all" | string>("all")' in page


def test_team_filters_join_count_chips_and_reset():
    """新条件必须与老 6 个条件同一套机制，否则会出现「筛了但角标不涨 / chip 撤不掉」。"""
    page = _read()
    assert page.count("teamLeadFilter.value") >= 3
    assert page.count("subAgentFilter.value") >= 3
    assert page.count("teamFilter.value") >= 3
    # 角标计数
    assert "teamLeadFilter.value,\n      subAgentFilter.value,\n      teamFilter.value," in page
    # chip 文案：所属团要显示团名而不是 id
    assert "所属团：${team?.name || \"已失效的团\"}" in page
    # 重置要清空，否则「重置」后旧条件还在，用户以为没生效
    assert "teamLeadFilter.value = \"all\";\n  subAgentFilter.value = \"all\";\n  teamFilter.value = \"all\";" in page


def test_team_filters_count_as_active_filters():
    """hasActiveAgentFilters 是独立枚举的，漏掉新条件会让空状态文案说错、拖拽仍然可用。"""
    page = _read()
    start = page.index("const hasActiveAgentFilters = computed(")
    end = page.index("/** 「筛选」按钮上的角标：有几个条件在生效 */")
    block = page[start:end]
    assert "teamLeadFilter.value !== \"all\"" in block
    assert "subAgentFilter.value !== \"all\"" in block
    assert "teamFilter.value !== \"all\"" in block


def test_team_role_filter_semantics():
    page = _read()
    # 团主用 is_expert_team_lead 判定，但主助手恒按「非团主」处理
    assert "(Boolean(a.is_expert_team_lead) && !isMainAgent(a)) === wantLead" in page
    # 子专家 = 出现在某个团主专家的调度范围里，不是「非团主」
    assert "(agentTeamsOf(a).length > 0) === wantSub" in page
    # 所属团同时收团主本人与成员
    assert "String(a.id) === target || agentTeamsOf(a).some((team) => team.id === target)" in page


def test_sub_agent_teams_built_from_member_ids_without_new_api():
    """反查表靠 team_member_ids 在前端展开，不新增接口请求。"""
    page = _read()
    assert "const subAgentTeams = computed(() => {" in page
    assert "for (const rawId of team.team_member_ids || [])" in page
    # 团主自己的 id 混在成员里时要跳过，否则团主会被标成「它自己的子专家」
    assert "if (memberId === teamId) continue" in page
    # 同一个团不能重复计入（同一 id 出现两次时去重）
    assert "if (!list.some((item) => item.id === teamId)) list.push" in page


def test_badge_shows_name_for_one_team_and_count_for_many():
    page = _read()
    assert "子专家 · ${only.name}" in page
    assert "子专家 · ${teams.length} 个团" in page
    # 多团不展开团名，完整名单放进 title
    assert "所属专家团：${teams.map((team) => team.name).join('、')}" in page


def test_badge_is_clickable_and_picker_lists_every_team():
    page = _read()
    # 徽标可点（网格卡片 + 列表行两处，都 .stop 防止触发卡片的选中/打开）
    assert page.count('@click.stop="openTeamPicker($event, agent)"') == 2
    # 单团不弹浮层，直接筛——多一步选择是多余的
    assert "if (teams.length === 1) {\n    applyTeamFilter(only.id)" in page
    # 多团浮层要说明「属于几个团」，否则用户不知道还有别的团
    assert "这个子专家属于 {{ teamPicker.teams.length }} 个团" in page
    # 浮层用 Teleport 挂在 body：卡片在滚动容器里，absolute 会被裁
    assert '<div v-if="teamPicker" class="fixed inset-0 z-[10050]" @click="closeTeamPicker">' in page


def test_badge_only_for_agents_inside_a_team():
    page = _read()
    # 普通智能体不挂徽标：每张卡都写「非子专家」等于噪音
    assert 'v-if="subAgentBadge(agent)"' in page
    assert "const [only] = teams\n  if (!only) return null" in page


def test_terminology_is_zi_zhuan_jia_only():
    """团队相关的文案一律叫「子专家」。

    只收本次新增的代码段：时间线、会话导出里既有的「调用子代理」是后端日志格式
    （processTimeline 按这个字符串解析），不属于这次术语统一的范围，不能一起改。
    """
    page = _read()
    start = page.index("const teamLeadFilter")
    end = page.index("const subAgentBadgeShort")
    block = page[start:end]
    assert "子代理" not in block, "新增的团队筛选/徽标代码里混入了「子代理」"
    assert "子专家" in block

    # 用户可见文案
    assert "只看子专家" in page and "排除子专家" in page
    assert "{{ subAgentBadge(agent)?.label }}" in page
    assert "{{ subAgentBadgeShort(agent) }}" in page


def test_team_section_is_a_flat_group_not_a_collapsed_panel():
    """三个条件并排一行，展开在同一个面板里，不再套一层折叠。"""
    page = _read()
    assert '<span>团队</span>' in page
    assert 'data-key' not in page  # Vue 实现走 v-model，不是原型的演示属性
    assert page.count('v-model="teamLeadFilter"') == 1
    assert page.count('v-model="subAgentFilter"') == 1
    assert page.count('v-model="teamFilter"') == 1
    # 同时选「是」会空，面板里要提前交代，别让用户以为筛坏了
    assert "两个条件都选「是」时结果为空" in page


# ---------------------------------------------------------------------------
# 同日优化：筛选面板变成 9 个条件之后暴露出来的一批小问题
# ---------------------------------------------------------------------------


def test_search_covers_description_and_team_names():
    """列表搜索只匹配 name/display_name，而「专家团」视图匹配团名 + 介绍。

    同一页两套规则的结果是：搜只写在描述里的词，在列表里搜不到，切个视图反而能搜到。
    """
    page = _read()
    start = page.index("// 关键词：名称、标识、描述")
    block = page[start:start + 900]
    assert "a.description" in block
    assert "resolveTeamName(a).toLowerCase().includes(k)" in block
    assert "agentTeamsOf(a).some((team) => team.name.toLowerCase().includes(k))" in block


def test_panel_is_scrollable_and_shows_match_count():
    """9 个条件后面板约 450px 高，没有 max-h 时「应用」要滚页面才点得到。"""
    page = _read()
    assert "max-h-[calc(100vh-9rem)]" in page
    assert "overflow-y-auto" in page
    # 底部操作条贴底，滚动时始终可点
    assert "sticky -bottom-4" in page
    # 应用前就能看到会剩几个
    assert "匹配 {{ filteredAgents.length }} 个" in page


def test_card_title_can_shrink():
    """flex item 的默认最小宽度是内容宽度，缺 min-w-0 时 line-clamp 不生效，
    长名称会把后面的徽标顶出卡片。"""
    page = _read()
    assert 'class="min-w-0 truncate font-bold text-gray-900"' in page


def test_empty_state_explains_team_filter():
    """按团筛完为空 ≠ 没有数据：成员可能未发布、已删除，或当前账号看不到。"""
    page = _read()
    assert "这个团在当前列表里没有可见成员" in page


def test_short_badge_reads_as_team_count():
    """「子专家 · 2」会被读成 2 个成员，补一个「团」字。"""
    page = _read()
    assert "`子专家 · ${teams.length} 团`" in page


def test_scope_configurator_opens_the_drawer_without_a_toast():
    """点「配置调度范围」直接把「配置与发布」抽屉打开即可，别再盖一条警告。

    按钮的 title 已经写着「在工具里给 sub_agent_call 勾选可委派的子智能体」，
    抽屉本身也带版本列表；每点一次都弹 toast，反而挡住刚打开的界面。
    """
    page = _read()
    start = page.index("const openTeamScopeConfigurator")
    # 函数体只有一行调用，结尾是不带分号的 `}`；用 `\n};` 会一路切到很后面的函数
    body = page[start : page.index("\n}", start)]
    assert "openDrawer(team)" in body
    assert "showToast" not in body
    # 按钮上的说明要留着，否则用户就真不知道下一步点哪了
    assert 'title="打开「配置与发布」，在工具里给 sub_agent_call 勾选可委派的子智能体"' in page
