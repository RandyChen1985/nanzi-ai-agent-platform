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
    # 团主用 is_expert_team_lead 判定
    assert "Boolean(a.is_expert_team_lead) === wantLead" in page
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
