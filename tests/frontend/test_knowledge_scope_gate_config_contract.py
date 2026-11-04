"""检索范围确认的配置呈现契约（系统配置页 + 智能体版本配置）。

锁住四件事，任何一条退化都会让配置「看起来能改、实际没用」：
1. 三个配置项归在同一个色块组里（否则散落在知识库分组里，看不出是一套）；
2. 总开关必须走胶囊开关，不能退化成手填 true/false 的文本框；
3. 总开关关闭时，阈值与推荐数两个子项不再渲染（关着时填它们没有任何作用）；
4. 智能体侧四态互斥：每个选项都带一行用途说明，「仅用绑定库」是其中一个取值而不是独立开关；
5. 聊天页与调试页在该智能体上常驻一条「仅检索已绑定的知识库」说明（否则用户以为选库失灵）。
"""
import re
from pathlib import Path

import pytest

pytestmark = pytest.mark.no_infrastructure

ROOT = Path(__file__).resolve().parents[2]
SYSTEM_CONFIG = ROOT / "frontend/src/views/SystemConfig.vue"
AGENT_EDITOR = ROOT / "frontend/src/components/agent/AgentVersionEditorDrawer.vue"
EMBED_CHAT = ROOT / "frontend/src/views/EmbedChat.vue"
AGENT_DEBUG = ROOT / "frontend/src/views/AgentDebug.vue"
AGENT_MANAGEMENT = ROOT / "frontend/src/views/AgentManagement.vue"
SCOPE_BANNER = ROOT / "frontend/src/components/chat/ScopeBoundBanner.vue"
SCOPE_SELECT = ROOT / "frontend/src/components/agent/ScopeGateSelect.vue"

GROUP_KEYS = (
    "knowledge_scope_gate_enabled",
    "knowledge_scope_gate_threshold",
    "knowledge_scope_gate_ranked_limit",
)


@pytest.fixture(scope="module")
def system_config_source() -> str:
    return SYSTEM_CONFIG.read_text(encoding="utf-8")


@pytest.fixture(scope="module")
def agent_editor_source() -> str:
    return AGENT_EDITOR.read_text(encoding="utf-8")


def test_three_keys_share_one_color_block(system_config_source):
    for key in GROUP_KEYS:
        assert f"item.key === '{key}' ? 'scope-gate-config-group" in system_config_source, (
            f"{key} 没有加入 scope-gate-config-group 色块"
        )
    assert "rounded-t-xl" in system_config_source.split("knowledge_scope_gate_enabled' ? 'scope-gate-config-group", 1)[1][:200]
    assert "rounded-b-xl" in system_config_source.split("knowledge_scope_gate_ranked_limit' ? 'scope-gate-config-group", 1)[1][:200]


def test_group_has_a_heading(system_config_source):
    assert "知识库检索范围确认" in system_config_source


def test_enabled_key_registered_as_toggle(system_config_source):
    """与 miniprogram_enabled 同一类遗漏：布尔配置漏进白名单会变成文本框。"""
    groups = re.findall(r"\[([^\]]*?)\]\.includes\(item\.key\)", system_config_source)
    toggle_keys = set()
    for group in groups:
        toggle_keys.update(re.findall(r"'([a-z_0-9]+)'", group))
    assert "knowledge_scope_gate_enabled" in toggle_keys


def test_sub_keys_hidden_when_master_switch_off(system_config_source):
    knowledge_block = system_config_source.split("if (category === 'knowledge') {", 1)[1][:900]
    assert "knowledge_scope_gate_enabled" in knowledge_block
    assert "list = list.filter(x => !['knowledge_scope_gate_threshold', 'knowledge_scope_gate_ranked_limit'].includes(x.key))" in knowledge_block


def test_agent_options_carry_explanations(scope_select_source):
    """每个选项都要有一行用途说明：只写选项名，没人知道「跟随全局」跟的是什么。"""
    for label in ("跟随全局", "强制开启", "关闭", "仅用绑定库"):
        assert f"label: '{label}'" in scope_select_source, f"缺少「{label}」选项"
    for hint in ("按全局阈值判断要不要确认范围", "不跟随后续灰度", "绑定数超过阈值也不问"):
        assert hint in scope_select_source, f"缺少说明：{hint}"


def test_agent_scope_options_are_mutually_exclusive(scope_select_source, agent_editor_source):
    """四态互斥，且只出现在智能体配置里。

    这条配置是智能体级 engine_config，不属于版本内容。曾经画在版本抽屉里，
    而版本保存只提交 versionForm——用户点了保存、重开还是旧值。
    """
    for mode in ("inherit", "on", "off", "bound_only"):
        assert f"value: '{mode}'" in scope_select_source, f"缺少 {mode} 选项"
    assert "仅用绑定库" in scope_select_source
    # 版本抽屉（新建向导）里也复用同一个选择器，不允许各自写一套选项
    assert "ScopeGateSelect" in agent_editor_source
    assert "scopeGateModes" not in agent_editor_source
    assert "setScopeBoundOnly" not in agent_editor_source


def test_agent_scope_gate_shown_only_for_knowledge_agents(agent_management_source):
    """只对知识库类型智能体展示：通用助手上放一个知识库检索开关没有意义。"""
    body = agent_management_source[
        agent_management_source.index("<!-- 知识库检索范围确认"):]
    body = body[: body.index("<!-- 推荐排版样式选择 -->")]
    assert "v-if=\"agentForm.agent_type === 'KNOWLEDGE_BASE'\"" in body


@pytest.fixture(scope="module")
def embed_chat_source() -> str:
    return EMBED_CHAT.read_text(encoding="utf-8")


@pytest.fixture(scope="module")
def agent_debug_source() -> str:
    return AGENT_DEBUG.read_text(encoding="utf-8")


@pytest.fixture(scope="module")
def scope_banner_source() -> str:
    return SCOPE_BANNER.read_text(encoding="utf-8")


def test_scope_bound_banner_exists_with_clear_copy(scope_banner_source):
    """banner 文案要说清「选了别的库也不会生效」，只写「仅绑定库」用户读不懂后果。"""
    assert "仅检索已绑定的知识库" in scope_banner_source
    assert "不会生效" in scope_banner_source
    # 绑定数量可选展示：拿到就写具体个数，拿不到退化为笼统描述
    assert "boundCount" in scope_banner_source


def test_banner_triggers_on_bound_only_mode(embed_chat_source):
    """只有 scope_gate=bound_only 才提示；off/inherit/on 都不该出现这条说明。"""
    assert "scope_gate" in embed_chat_source
    assert '=== "bound_only"' in embed_chat_source
    assert "ScopeBoundBanner" in embed_chat_source
    # 判定依据必须是当前生效智能体（与发消息时的 agent_id 同源）
    assert "effectiveChatAgent" in embed_chat_source


@pytest.mark.parametrize("page", [EMBED_CHAT, AGENT_DEBUG], ids=["embed_chat", "agent_debug"])
def test_banner_renders_above_chat_input(page):
    """提示要贴着输入框，用户选库的动作就在那里发生。"""
    source = page.read_text(encoding="utf-8")
    banner_pos = source.index("<ScopeBoundBanner")
    input_pos = source.index("</ChatInput>")
    assert banner_pos < input_pos


@pytest.mark.parametrize("page", [EMBED_CHAT, AGENT_DEBUG], ids=["embed_chat", "agent_debug"])
def test_banner_dismiss_is_per_agent(page):
    """按智能体记关闭状态：换一个智能体要重新提示，否则关过一次就再也看不到。"""
    source = page.read_text(encoding="utf-8")
    assert "scopeBoundDismissedAgentId" in source
    assert "scopeBoundAgentId.value !== scopeBoundDismissedAgentId.value" in source


def test_agent_debug_banner_skips_auto_router(agent_debug_source):
    """Auto Router 没有固定智能体，无法预判会路由到谁，不提示。"""
    assert "if (!id) return null;" in agent_debug_source
    assert '=== "bound_only"' in agent_debug_source


@pytest.fixture(scope="module")
def agent_management_source() -> str:
    return AGENT_MANAGEMENT.read_text(encoding="utf-8")




def test_save_agent_keeps_engine_config_keys_it_does_not_own(agent_management_source):
    """保存时 engine_config 是"局部重建"，必须带上原有键。

    这里踩过两次：整体赋值只声明本引擎关心的字段，``scope_gate`` 这类只挂在
    engine_config 上的设置就被静默丢掉——用户在表单里选了、点了保存、重开还是旧值。
    """
    body = agent_management_source[
        agent_management_source.index("const saveAgent = async"):]
    body = body[: body.index("const saveAgentVersion =") if "const saveAgentVersion =" in body else len(body)]
    # 只看多行重建（"= {\n"），跳过 "= {};" 这类空初始化
    rebuilds = [
        i for i in range(len(body))
        if body.startswith("agentForm.value.engine_config = {\n", i)
    ]
    assert rebuilds, "保存逻辑里应有 engine_config 重建"
    for pos in rebuilds:
        tail = body[pos : pos + 200]
        assert "...(agentForm.value.engine_config || {})," in tail, (
            "engine_config 重建必须展开原有键，否则会丢掉 scope_gate 等设置"
        )


def test_scope_gate_uses_double_line_dropdown(agent_management_source, scope_select_source):
    """四个选项平铺太占地方，改用下拉；但每项必须能放两行（标题 + 说明）且带图标。

    原生 ``<select>`` 的选项只能渲染单行纯文本，也放不下图标——而四种模式的差别
    （「强制开启」受不受全局总闸约束）恰恰只在说明里，所以必须自绘 listbox。
    """
    body = agent_management_source[
        agent_management_source.index("<!-- 知识库检索范围确认"):]
    body = body[: body.index("<!-- 推荐排版样式选择 -->")]
    assert "<ScopeGateSelect" in body
    assert 'type="radio"' not in body
    assert "<select" not in body, "原生 select 无法渲染双行与图标"

    # 组件侧：双行（label + hint）、每项有独立图标、浮层 Teleport 避免被弹窗裁切
    assert 'role="listbox"' in scope_select_source
    assert 'role="option"' in scope_select_source
    assert "option.hint" in scope_select_source
    assert "iconPath" in scope_select_source
    assert "<Teleport to=\"body\">" in scope_select_source
    for value in ("inherit", "on", "off", "bound_only"):
        assert f"value: '{value}'" in scope_select_source, f"缺少 {value} 选项"


@pytest.fixture(scope="module")
def scope_select_source() -> str:
    return SCOPE_SELECT.read_text(encoding="utf-8")


def test_agent_form_splits_sections_into_tabs(agent_management_source):
    """配置项多，按章节拆 tab：基本信息 / 专家团 / 能力标签 / 排版样式。

    折叠（含「全部折叠」批量按钮）已去掉：低频项各占一页，不再需要点开才看得见，
    页签也不该退化成「四个块还是一页、只是加了个锚点」。
    """
    assert 'role="tablist"' in agent_management_source
    for label in ("基本信息", "专家团", "能力标签", "排版样式"):
        assert f'label: "{label}"' in agent_management_source, f"缺少「{label}」页签"
    for key in ("basic", "team", "capabilities", "appearance"):
        assert f"v-show=\"agentFormTab === '{key}'\"" in agent_management_source, (
            f"「{key}」页签没有对应内容块"
        )
    # 折叠与批量折叠入口不再保留，避免两套隐藏机制并存
    assert "toggleSection" not in agent_management_source
    assert "全部折叠" not in agent_management_source
    assert "全部展开" not in agent_management_source
    # 页签头里的「?」必须 stop，否则看帮助会连带切页签
    assert '@click.stop="showCapabilityHelp = true"' in agent_management_source
    # 换页签必须走统一入口：直接改 agentFormTab 会丢掉「滚回顶部」，
    # 内容更短的一页会从半截开始显示（浏览器把 scrollTop 夹在中间）。
    assert '@click="switchAgentFormTab(tab.key)"' in agent_management_source
    assert "scrollIntoView" in agent_management_source


def test_team_intro_guide_opens_in_modal(agent_management_source):
    """「团队介绍」的写法指引要和能力标签的「?」一样弹层显示。

    内联展开会把「专家团」这一页撑长，收起后还得再点一次才看得到；两处「?」交互
    不一致时，用户会以为其中一个没反应。弹层还得保留「填入示例」这条路。
    """
    assert "const showTeamIntroHelp = ref(false)" in agent_management_source
    assert "const applyTeamIntroExample" in agent_management_source
    assert '@click.stop="showTeamIntroHelp = true"' in agent_management_source
    assert 'v-if="showTeamIntroHelp"' in agent_management_source
    # 内联展开版不许回来：同一个指引不能有两个入口
    assert "showTeamIntroGuide" not in agent_management_source
    # 弹层自带标题，组件不要再重复一遍标题和导语
    assert '<TeamIntroGuide hide-intro @apply="applyTeamIntroExample" />' in agent_management_source


def test_team_editor_modal_uses_the_same_intro_guide_popup():
    """「专家团设置」弹窗里的「团队介绍 ?」也要走弹层，跟智能体编辑弹窗保持一致。

    这是另一个组件（AgentTeamEditorModal），容易只改了一边：用户在智能体中心看到
    的是弹层，点进「专家团设置」又变回内联展开。层级也要盯住——它自己的 Modal 是
    10010，帮助层必须更高，否则会被父弹窗盖住。
    """
    editor = (ROOT / "frontend/src/components/agent/AgentTeamEditorModal.vue").read_text(
        encoding="utf-8"
    )
    assert "const showIntroHelp = ref(false)" in editor
    assert "const applyIntroExample" in editor
    assert '@click.stop="showIntroHelp = true"' in editor
    assert 'v-if="showIntroHelp"' in editor
    # 内联展开版不许回来
    assert "showIntroGuide" not in editor
    assert '<TeamIntroGuide hide-intro @apply="applyIntroExample" />' in editor
    assert 'class="fixed inset-0 z-[10020]' in editor


def test_agent_form_window_stays_same_size_across_tabs(agent_management_source):
    """切页签时弹窗不许跳高度。

    原先高度由最长的「基本信息」决定，切到「排版样式」这种短页时窗口会缩下去，
    鼠标停在的位置跟着变、按钮也会移位。这里靠固定弹窗高度让它稳定：
    内容区（Modal 自带 overflow-y-auto）继续滚动，页签条 sticky 才有意义。
    """
    assert 'size="max-w-4xl sm:h-[min(84vh,820px)]"' in agent_management_source, (
        "编辑弹窗没有固定高度，切换页签窗口高度会跳"
    )
    # 页签条必须是内层容器的第一个子元素：多一个前置空 div，space-y-4 就会给它
    # 补 16px 上边距，顶部又出现一块空白（之前就是被这块空白顶下来的）
    assert '<div ref="agentFormTopAnchor" aria-hidden="true"></div>' not in agent_management_source
    assert 'ref="agentFormTopAnchor"' in agent_management_source
    # 吸顶必须用负 top。滚动容器带 p-4/p-6，`top-0` 只贴到内边距**下方**，
    # 滚动时内容会从页签条上方那圈 padding 里钻出来——实测就是这样永远悬着一条空白。
    assert "sticky -top-4" in agent_management_source
    assert "sm:-top-6" in agent_management_source
    # 底色必须不透明：半透明会把滚过页签条的文字透出来（截图里能看到半截字）。
    # 只看页签条那一行——文件别处的筛选条/下拉用半透明是正常的。
    tab_bar_line = next(
        line for line in agent_management_source.splitlines() if "sticky -top-4" in line
    )
    assert "bg-white " in tab_bar_line or tab_bar_line.rstrip().endswith("bg-white")
    assert "bg-white/95" not in tab_bar_line


def test_agent_form_tracks_unsaved_changes_per_tab(agent_management_source):
    """改动散在四页里时，必须能看出「改了几处、改在哪页」。

    tab 化本身把信息藏起来了：只剩一个「保存修改」，用户改完两页再回头，
    没法确认有没有漏掉的页。这条契约锁三件事：
    1. 打开弹窗取一次基线（否则一进来就满屏「有改动」）；
    2. 每个字段只归属一个页签（engine_config 里的键不许被两页重复计数）；
    3. 页签数字、底栏总数、一键跳转三个入口都在。
    """
    script = agent_management_source[: agent_management_source.index("</script>")]
    for name in (
        "takeAgentFormSnapshot",
        "dirtyFieldKeys",
        "dirtyCountByTab",
        "dirtyTotal",
        "jumpToNextDirtyTab",
    ):
        assert f"const {name}" in script, f"缺少未保存改动统计用的 {name}"

    open_body = script[script.index("const openAgentModal"):]
    open_body = open_body[: open_body.index("const agentPayloadWithoutSystemFlag")]
    assert "takeAgentFormSnapshot();" in open_body, "打开弹窗没有取基线，改动数会算错"

    fields = re.findall(r'\{ tab: "(basic|team|capabilities|appearance)", key: "([^"]+)"', script)
    keys = [key for _, key in fields]
    assert keys, "没有定义字段归属表"
    assert len(keys) == len(set(keys)), f"同一字段被多个页签重复计数：{keys}"
    assert {tab for tab, _ in fields} == {"basic", "team", "capabilities", "appearance"}

    assert "dirtyCountByTab[tab.key]" in agent_management_source, "页签上没有显示改动数"
    assert "处修改未保存" in agent_management_source, "底栏没有提示未保存改动"
    assert '@click="jumpToNextDirtyTab"' in agent_management_source, "底栏提示不可点击跳转"


def test_version_drawer_syncs_agent_engine_config_on_save(agent_management_source):
    """版本抽屉里的检索范围写的是智能体级 engine_config，而版本保存只提交 versionForm。

    给已有智能体建新版本时必须补一次同步，否则又是「保存成功、重开还是旧值」。
    """
    assert "syncAgentEngineConfigFromForm" in agent_management_source
    body = agent_management_source[
        agent_management_source.index("const syncAgentEngineConfigFromForm"):]
    body = body[: body.index("const saveVersion")]
    assert "if (!agent || isCreatingAgent.value) return;" in body, "新建流程随创建提交，不做同步"
    assert "if (nextMode === savedMode) return;" in body, "无变化不该多发一次 PATCH"
    assert "agentApi.updateAgent(" in body

    save_body = agent_management_source[
        agent_management_source.index("const saveVersion = async"):]
    save_body = save_body[: save_body.index("const publishVersionFromEditor")]
    assert "await syncAgentEngineConfigFromForm();" in save_body


def test_new_agent_wizard_exposes_scope_gate(agent_editor_source):
    """新建智能体走版本抽屉第 1 步，必须能配这个范围，且只对知识库类型显示。"""
    body = agent_editor_source[
        agent_editor_source.index("<!-- 知识库检索范围确认"):]
    body = body[: body.index("<!-- 推荐排版样式选择 -->")]
    assert "v-if=\"agentForm.agent_type === 'KNOWLEDGE_BASE'\"" in body
    assert "<ScopeGateSelect" in body


def test_template_helpers_are_declared_in_script(agent_management_source):
    """模板里用到的状态与辅助函数必须在 script 中真的定义。

    踩过的坑：一次区间替换把 `collapsedSections` / `isSectionOpen` /
    `toggleSection` / `toggleAllSections` 整段删掉，模板却还在用。
    Vue 渲染时抛 `isSectionOpen is not a function`，编辑弹窗直接不出现；
    而 `vue-tsc --noEmit` 走的是 solution-style 根 tsconfig（`files: []`），
    压根没检查任何文件，于是一直显示"通过"。这条契约不依赖 tsc。

    换成 tab 后风险点跟着换：模板依赖 `agentFormTab` / `agentFormTabs`，
    另需保证「换智能体回到首页」和「页签消失时回落」两处逻辑存在。
    """
    script_end = agent_management_source.index("</script>")
    script = agent_management_source[:script_end]
    for name in (
        "agentFormTab",
        "agentFormTabs",
        "agentFormTopAnchor",
        "switchAgentFormTab",
        "scrollAgentFormToTop",
        "revealBasicTab",
        "scopeGateMode",
        "scopeGateModeModel",
        "setScopeGateMode",
    ):
        assert f"const {name}" in script, f"模板用到的 {name} 没有在 script 中定义"
    # 打开另一个智能体时不能停在上次翻到的页签
    assert 'agentFormTab.value = "basic";' in script
    # 切类型/引擎导致某页签消失时，要回落而不是白屏
    assert "watch(agentFormTabs" in script
    # 保存校验失败必须把用户带回基本信息页，否则只弹一句 toast，人不知道该改哪个字段
    save_body = script[script.index("const saveAgent = async"):]
    save_body = save_body[: save_body.index("const saveOnboardingVersion")]
    assert save_body.count("revealBasicTab();") == 2, "缺标识符/标识符撞名两条校验都要回页"


def test_agent_tab_state_is_declared_after_form_refs(agent_management_source):
    """页签状态的声明顺序必须晚于它读到的 ref。

    `watch(agentFormTabs, …)` 建立时会**立即求值一次** source（不是 only-when-changed），
    而 `agentFormTabs` 的 getter 里读 `agentForm.value`。一旦 watch 早于 `const agentForm`
    声明，setup 期就抛 `Cannot access 'agentForm' before initialization`（TDZ），
    `<script setup>` 整体失败——整个智能体中心白屏。

    这条契约存在的原因：`compile_vue_templates.cjs`（语法）与 `vue-tsc`（类型）都发现不了
    这类顺序错误，当时的白屏就是一路"全绿"之后实机才炸出来的。只锁顺序，不锁位置。
    """
    script = agent_management_source[: agent_management_source.index("</script>")]
    form_decl = script.index("const agentForm = ref<AIAgentBase>(")
    tab_decl = script.index("const agentFormTab = ref<AgentFormTab>")
    tabs_decl = script.index("const agentFormTabs = computed")
    # 只认行首：注释里也会出现 `watch(agentFormTabs, …)` 这段文字
    watch_match = re.search(r"^watch\(agentFormTabs", script, re.M)
    assert watch_match is not None, "watch(agentFormTabs) 没有定义在顶层"
    watch_decl = watch_match.start()
    assert form_decl < tab_decl < tabs_decl < watch_decl, (
        "agentForm → agentFormTab → agentFormTabs → watch(agentFormTabs) 必须依次声明，"
        "否则 setup 期 TDZ 会让页面白屏"
    )
    # 滚动复位依赖模板里的锚点，ref 名写错时只会静默失效，这里一起锁住
    assert 'ref="agentFormTopAnchor"' in agent_management_source


def test_knowledge_portal_toggle_is_locked_under_bound_only():
    """「仅用绑定库」时知识库中心的挂载开关要禁用。

    只提示不禁用会让人以为挂上了就生效，实际后端会拒绝——用户既白操作一次，
    也读不懂为什么搜不到。
    """
    drawer = (ROOT / "frontend/src/components/knowledge/KnowledgePortalDrawer.vue").read_text(
        encoding="utf-8"
    )
    assert "scopeLocked?: boolean" in drawer
    toggle = drawer[drawer.index('<!-- Toggle switch -->'):]
    toggle = toggle[: toggle.index("</button>")]
    assert ':disabled="toggleLocked(ds.id)"' in toggle
    assert "!toggleLocked(ds.id) && emit('toggle-active', ds.id)" in toggle
    assert "只检索已绑定的知识库" in toggle
    # 只挡新增、放行取消：已挂载的库必须还能点掉，否则锁定态成了死锁
    assert "const toggleLocked = (id: string) => !!props.scopeLocked && !props.activeDatasetIds.includes(id);" in drawer

    for page in (EMBED_CHAT, AGENT_DEBUG):
        source = page.read_text(encoding="utf-8")
        start = source.index("<KnowledgePortalDrawer")
        portal = source[start : source.index("/>", start)]
        assert ':scope-locked="!!scopeBoundOnlyAgent"' in portal, f"{page.name} 未锁定挂载开关"


def test_editing_hides_read_only_type_and_engine_pickers(agent_management_source):
    """编辑态下「智能体类型」「执行引擎」都不可改，不该继续占版面。

    注意不能把整块删掉就完事：类型卡片的蓝色提示仍要留（说明当前类型的运行行为），
    引擎参数区（RAGFlow / OpenClaw）也必须保留，否则等于把可编辑项一起藏了。
    """
    # 引擎：编辑 + LOCAL 时整块收起，但引擎参数字段仍在
    assert 'v-if="!isEditingAgent || agentForm.engine_type !== \'LOCAL\'"' in agent_management_source
    assert 'v-if="agentForm.engine_type !== \'LOCAL\'"' in agent_management_source
    # 类型：三张卡片包在 !isEditingAgent 里
    type_block = agent_management_source[
        agent_management_source.index("<!-- 编辑时类型不可改"):
    ]
    type_block = type_block[: type_block.index("<!-- 推荐排版样式选择 -->")]
    assert "<template v-if=\"!isEditingAgent\">" in type_block
    assert "创建保存后不可修改" not in type_block
    # 当前类型的行为说明保留
    assert "发布前需要绑定知识库和检索工具" in type_block
