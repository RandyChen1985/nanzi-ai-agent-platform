"""凭据来源三态的界面契约。

每个断言都对应一个「改回去就静默失效」的真实故障：

* 参数来源退回两态 → 界面配不出规则，后端能力等于没有入口；
* 规则面板复用 `bindingOfSlot` → 规则行没有 secret_key，界面显示空白；
* 试算走错地址 → 用户看到「试算不可用」，其实后端已经好了；
* 组件用裸 `axios` → 丢掉 `@/utils/axios` 的 baseURL 与拦截器，请求打到错误的主机。
"""
from pathlib import Path

import pytest


pytestmark = pytest.mark.no_infrastructure

ROOT = Path(__file__).parents[2]
EDITOR = ROOT / "frontend/src/components/secrets/SecretLocatorEditor.vue"
REGISTRY = ROOT / "frontend/src/components/system/McpServerRegistry.vue"


def test_editor_exists_and_covers_the_five_steps():
    src = EDITOR.read_text(encoding="utf-8")
    # 五步缺一不可：用哪个值 → 怎么变形 → 套成什么 → 匹配哪个字段 → 挑不到怎么办
    assert "① 用哪个值" in src
    assert "② 怎么变形" in src
    assert "③ 套成什么" in src
    assert "④ 匹配凭据的" in src
    assert "⑤ 挑不到时" in src


def test_editor_offers_all_three_on_miss_strategies():
    src = EDITOR.read_text(encoding="utf-8")
    assert 'value="fail"' in src
    assert 'value="fallback_fixed"' in src
    assert 'value="model"' in src


def test_editor_uses_packaged_axios_and_preview_endpoint():
    src = EDITOR.read_text(encoding="utf-8")
    assert "from '@/utils/axios'" in src
    assert "from 'axios'" not in src
    assert "/api/portal/secrets/locate-preview" in src


def test_editor_warns_that_preview_uses_current_user():
    """试算身份固定当前登录人（已拍板），界面上必须说出来。"""
    src = EDITOR.read_text(encoding="utf-8")
    assert "身份固定为当前登录人" in src


def test_editor_never_mentions_secret_id_field():
    """规则只描述怎么找。编辑器里不该出现「选一条凭据」的痕迹。"""
    src = EDITOR.read_text(encoding="utf-8")
    assert "secret_id" not in src


def test_param_source_select_has_three_states():
    src = REGISTRY.read_text(encoding="utf-8")
    assert '<option value="model">模型填写</option>' in src
    assert '<option value="cred">固定凭据</option>' in src
    assert '<option value="rule">按规则匹配</option>' in src
    # 旧两态写法必须彻底消失，否则 v-model 是字符串、选项是布尔，永远对不上
    assert '<option :value="false">' not in src
    assert '<option :value="true">' not in src


def test_rule_branch_reads_rule_draft_not_binding():
    """规则行没有 secret_key，用 bindingOfSlot 会显示空白。"""
    src = REGISTRY.read_text(encoding="utf-8")
    assert "paramRuleDraft[name]" in src
    assert "ruleBindingOfSlot" in src
    assert "fixedBindingOfSlot" in src


def test_saving_rule_binding_posts_strategy_rule():
    src = REGISTRY.read_text(encoding="utf-8")
    assert "strategy: 'rule'" in src
    # 从规则改回别的来源时必须把旧规则删掉，否则下次打开会被回填成「规则」
    assert "strategy: 'rule',\n            is_global" in src


def test_locator_editor_is_mounted_with_plain_params_only():
    """配置规则的键只能从「普通参数」里选：凭据参数自己不能当键。"""
    src = REGISTRY.read_text(encoding="utf-8")
    assert "<SecretLocatorEditor" in src
    assert "locatorArgOptions" in src
    fn = src[src.index("function locatorArgOptions"):]
    fn = fn[: fn.index("\n}\n")]
    assert "marked.has(name)" in fn
    assert "name !== self" in fn


def test_platform_targets_exposes_rules_and_arg_options():
    """个人面板要能显示「这个槽位是一条规则」，也得知道能拿哪些入参当键。

    规则行没有 secret_id，`platform-targets` 里那条 JOIN 必须也是 outerjoin，
    否则个人面板看不到自己配的规则。
    """
    src = (ROOT / "app/api/portal/endpoints/secrets.py").read_text(encoding="utf-8")
    assert '"rules": {slot: _rule(' in src
    assert '"arg_options"' in src
    start = src.index("async def list_platform_credential_targets")
    seg = src[start:start + 6000]
    assert "outerjoin(UserSecret" in seg


def test_personal_panel_can_configure_and_remove_rules():
    src = (ROOT / "frontend/src/components/mcp/PlatformMcpCredentialPanel.vue").read_text(encoding="utf-8")
    assert "openLocatorFor" in src
    assert "<SecretLocatorEditor" in src
    assert "strategy: 'rule'" in src
    # 个人配的规则只对自己生效：请求体里不能出现 is_global 字段
    assert "is_global:" not in src
    assert "onRemoveRule" in src


def test_editor_can_remove_rule_only_when_asked():
    """参数入口不需要「删除规则」（改回「模型填写」即是删），个人入口需要。"""
    src = EDITOR.read_text(encoding="utf-8")
    assert "removable" in src
    assert "(e: 'remove'): void" in src


# ── 规则编辑器「点开没反应」的守卫 ────────────────────────────────────────
#
# 这三条是同一个错误的三种表现：把 `<SecretLocatorEditor>` 嵌进了「绑定凭据」
# 弹窗的 `v-if` 块里。点「规则」时只设置了自己的 locator 状态，父弹窗的
# `v-if` 仍是 false，于是整个子树不渲染——用户看到的是**点了完全没反应**，
# 没有任何报错、类型检查也不会红（模板结构本身是合法的）。

PANEL = ROOT / "frontend/src/components/mcp/PlatformMcpCredentialPanel.vue"
SYSPARAM = ROOT / "frontend/src/components/system/ToolSystemParamEditor.vue"
TOOLPARAM = ROOT / "frontend/src/components/system/ToolParamEditor.vue"


def _editor_index(src: str) -> int:
    return src.index("<SecretLocatorEditor")


def _vif_blocks_after(src: str, guard: str) -> list[int]:
    """``guard`` 这个 ``v-if`` 对应 div 的结束位置列表（简单配对，够用）。"""
    ends = []
    start = src.find(guard)
    while start != -1:
        opener = src.rfind("<div", 0, src.find(guard))
        depth = 0
        pos = opener
        while pos < len(src):
            nxt_open = src.find("<div", pos)
            nxt_close = src.find("</div>", pos)
            if nxt_close == -1:
                break
            if nxt_open != -1 and nxt_open < nxt_close:
                depth += 1
                pos = nxt_open + 4
            else:
                depth -= 1
                pos = nxt_close + 6
                if depth == 0:
                    ends.append(pos)
                    break
        start = src.find(guard, start + 1)
    return ends


@pytest.mark.parametrize(
    "path,guard",
    [
        (PANEL, 'v-if="editing"'),
        (SYSPARAM, 'v-if="pickingName"'),
    ],
)
def test_rule_editor_is_not_nested_inside_bind_modal(path, guard):
    """规则编辑器不能放在「绑定凭据」弹窗的 v-if 块内。"""
    src = path.read_text(encoding="utf-8")
    idx = _editor_index(src)
    for end in _vif_blocks_after(src, guard):
        assert not (src.index(guard) < idx < end), (
            f"{path.name}: <SecretLocatorEditor> 被嵌在 {guard} 的容器里，"
            "点「规则」时父弹窗不渲染，弹窗弹不出来"
        )


def test_rule_editor_sits_after_teleport_in_registry():
    """MCP 工具集里靠后打开，必须在参数设置弹窗之后（同层、更高 z）。"""
    src = REGISTRY.read_text(encoding="utf-8")
    assert src.index("<SecretLocatorEditor") > src.rindex("</Teleport>")


def test_rule_editor_z_index_above_parent_modals():
    """规则编辑器后打开，层级必须高于参数设置(100)/绑定凭据(110)。"""
    import re

    src = EDITOR.read_text(encoding="utf-8")
    match = re.search(r"fixed inset-0 z-\[(\d+)\]", src)
    assert match, "根节点没找到 z-index"
    assert int(match.group(1)) > 110, "规则编辑器会被父弹窗盖住"


# ── 入参候选必须来自「模型可见的普通参数」──────────────────────────────────

def test_system_param_editor_candidates_come_from_plain_params():
    """拿系统参数（本身是凭据位）当规则的键，会形成「凭据依赖凭据」的环，
    而真正该选的业务入参（applicant/to）反而选不到。"""
    src = SYSPARAM.read_text(encoding="utf-8")
    fn = src[src.index("function locatorArgOptions"):]
    fn = fn[: fn.index("\n}")]
    assert "plainParams" in fn
    assert "props.modelValue" not in fn, "候选又退回系统参数了"


def test_tool_param_editor_passes_plain_params_down():
    src = TOOLPARAM.read_text(encoding="utf-8")
    assert ":plain-params=" in src
    assert "visualParams" in src


# ── 同槽位「规则 + 固定」必须各存一份 ──────────────────────────────────
#
# 规则与固定凭据是两条独立记录（唯一索引 `uk_binding_slot_strategy` 含 strategy），
# 可以并存：规则负责按入参定位，固定那条是 `on_miss=fallback_fixed` 的回退目标。
# 曾经两者都写进 `headerBindings[slot]`：后遍历到的那条覆盖前一条，于是
#   · 规则被覆盖 → `ruleBindingOfSlot` 拿到 null，重新打开显示「配了却没配上」
#   · 固定被覆盖 → 「换凭据 / 解绑」找不到回退目标，显示「未选」
# 谁覆盖谁取决于数据库返回顺序，所以是那种「时好时坏」的故障。


class TestRuleAndFixedCoexist:
    def test_rule_rows_have_their_own_map(self):
        src = REGISTRY.read_text(encoding="utf-8")
        assert "const slotRules = ref<Record<string, HeaderBinding>>({})" in src
        # 规则行必须分流，不能再塞进 fixed 那张表
        assert "if ((row.strategy || 'fixed') === 'rule') rules[row.slot] = entry" in src
        assert "slotRules.value = rules" in src

    def test_slot_is_no_longer_single_key_overwrite(self):
        """`map[row.slot] = {...}` 是同槽位互相覆盖的根源。"""
        src = REGISTRY.read_text(encoding="utf-8")
        assert "map[row.slot] = {" not in src

    def test_rule_lookup_reads_its_own_map(self):
        src = REGISTRY.read_text(encoding="utf-8")
        fn = src[src.index("const ruleBindingOfSlot"):]
        fn = fn[: fn.index("\n}\n")]
        assert "slotRules.value[slot]" in fn

    def test_deleting_a_rule_clears_the_local_copy(self):
        """删完规则不回清本地，网络失败时会显示一条其实已经没了的规则。"""
        src = REGISTRY.read_text(encoding="utf-8")
        start = src.index("async function submitParamMark(")
        body = src[start:start + 4000]
        assert "delete slotRules.value[slot]" in body


def test_rule_miss_result_is_translated():
    """`rule_miss` 是动态凭据定位最常出现的结果，不能把内部常量摊给用户。"""
    labels = (ROOT / "frontend/src/utils/secretLabels.ts").read_text(encoding="utf-8")
    assert "rule_miss:" in labels
    assert "规则未命中" in labels


# ── 「让模型挑一条」的回传通道必须写在界面上 ─────────────────────────────
#
# 模型只能通过 `secret_choice_<参数名>` 回传编号，而且只有「工具参数」这一级
# 才有这条通道（Header 出站前没有入参可匹配，P2 已拍板不做）。界面不写清这点，
# 用户会把 `on_miss=model` 配到 Header 上，然后永远等不到模型来救。


def test_model_on_miss_hint_names_the_choice_channel():
    src = EDITOR.read_text(encoding="utf-8")
    assert "secret_choice_<参数名>" in src


def test_model_on_miss_hint_scopes_to_param_level():
    src = EDITOR.read_text(encoding="utf-8")
    hint = src[src.index("case 'model':"):]
    hint = hint[: hint.index("case 'fallback_fixed':")]
    assert "Header" in hint and "不支持" in hint

