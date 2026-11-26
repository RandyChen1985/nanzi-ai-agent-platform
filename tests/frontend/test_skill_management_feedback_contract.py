"""契约：技能管理的「失败必有可见反馈」与个人技能页（personalOnly）越界守卫。

## P1-3 反馈可见性

重命名与启用/禁用此前存在静默分支：失败只有 console、或信封不是 success 时什么都没发生，
用户看到的是「点了没反应」「开关自己弹回去了」。契约要求：成功给成功提示，失败给可见错误
且带上后端返回的原因（`e.response.data.detail`），本地前置校验（空 ID / 非法字符）也必须提示。

## P1-4/5/6 引用清理的可见性

技能 ID 同时是目录名、智能体技能白名单项、会话已挂载 chip 以及发布谱系的来源 ID。
重命名与删除都会同步这些引用，后端把成功条数放在 `synced`、把失败说明放在 `warnings`；
两者都必须出现在提示里——「技能还在但哪都挂不上」这种断链，静默比不支持更糟。

## personalOnly 越界

个人技能页（个人中心 / 嵌入式个人资源面板）下 tab bar 整体是 `v-if="!personalOnly"`，
「待审核」「平台技能」「已发布」三个平台视图都不渲染。而顶部引导横幅
（SkillFlowGuideBanner，`@action` → `handleSkillBannerAction`）与「?」帮助弹窗里的
「前往待审核 / 平台技能 / 智能体中心」是无条件切 tab / 跳路由的：在个人技能页点下去，
技能列表（`showSkillList`）与目标面板一起消失，页面只剩标题和横幅。这组契约守住：
越界入口在个人技能页隐藏 + 处理函数有兜底守卫 + 首屏与权限变化都不会落在不可见 tab。
"""
from __future__ import annotations

import json
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
SKILLS_VIEW = ROOT / "frontend" / "src" / "views" / "SkillsManagement.vue"
BANNER = ROOT / "frontend" / "src" / "components" / "skill" / "SkillFlowGuideBanner.vue"

pytestmark = pytest.mark.no_infrastructure


def _skills_source() -> str:
    return SKILLS_VIEW.read_text(encoding="utf-8")


def _banner_source() -> str:
    return BANNER.read_text(encoding="utf-8")


def _function_body(name: str, end_marker: str) -> str:
    source = _skills_source()
    start = source.index(f"const {name} = ")
    end = source.index(end_marker, start)
    return source[start:end]


# ---------------------------------------------------------------------------
# P1-3 失败反馈
# ---------------------------------------------------------------------------


def test_rename_and_toggle_report_failures_with_backend_reason():
    rename = _function_body("submitRename", "const deleteSkill = (skillId: string)")
    toggle = _function_body("toggleSkillStatus", "// 查看技能详情与抽屉挂载")

    # 失败 → 可见错误提示，且带上后端原因
    assert "showToast(e.response?.data?.detail || '重命名失败', 'error')" in rename
    assert "showToast(e.response?.data?.detail || '更新技能状态失败', 'error')" in toggle
    for body in (rename, toggle):
        assert "console.warn" not in body and "console.error" not in body

    # 成功 → 成功提示
    assert "showToast(`技能已${newStatus === 'true' ? '启用' : '禁用'}`, 'success')" in toggle
    assert "}, 'success',)" in rename or "'success'," in rename

    # 信封不是 success 时不得静默（此前 toggle 会直接吞掉）
    assert "showToast(response.data?.message || '更新技能状态失败', 'error')" in toggle
    assert "if (response.data?.status !== 'success')" in rename
    assert "showToast(response.data?.message || '重命名失败', 'error')" in rename
    # 信封失败时不能再报「已重命名」
    envelope_check = rename.index("if (response.data?.status !== 'success')")
    success_toast = rename.index("已重命名为 ${newSkillId}")
    assert envelope_check < success_toast


def test_rename_validates_empty_and_illegal_id_visibly():
    rename = _function_body("submitRename", "const deleteSkill = (skillId: string)")
    assert "showToast('技能 ID 不能为空', 'warning')" in rename
    assert "showToast('技能 ID 仅支持英文、数字、中划线和下划线', 'warning')" in rename
    # 弹窗保持打开才能补填：校验分支不得顺手把 renameDialog 置空
    empty_branch = rename[rename.index("if (!newSkillId) {") : rename.index("if (newSkillId === target.skillId)")]
    assert "renameDialog.value = null" not in empty_branch


# ---------------------------------------------------------------------------
# P1-4/5/6 引用同步结果可见
# ---------------------------------------------------------------------------


def test_reference_sync_summary_drives_both_toasts():
    source = _skills_source()
    assert "const summarizeReferenceSync" in source
    assert "data?.synced" in source and "data?.warnings" in source

    rename = _function_body("submitRename", "const deleteSkill = (skillId: string)")
    assert "const { syncedCount, warnings } = summarizeReferenceSync(response.data?.data)" in rename
    # 成功提示要反映「已同步 N 处引用；有 N 条警告」
    assert "已同步 ${syncedCount} 处引用" in rename
    assert "有 ${warnings.length} 条警告" in rename
    assert "warnings.join('；')" in rename
    # 有警告时改用 error 级别，不能报成一切正常
    assert "warnings.length ? 'error' : 'success'" in rename

    delete = _function_body("deleteSkill", "// 切换启用/禁用状态")
    assert "const { syncedCount, warnings } = summarizeReferenceSync(response.data?.data)" in delete
    assert "有 ${warnings.length} 条警告" in delete
    assert "warnings.join('；')" in delete
    assert "warnings.length ? 'error' : 'success'" in delete
    assert "showToast(response.data?.message || '注销技能失败', 'error')" in delete


def test_reference_sync_summary_aggregates_contract_shape():
    """行为验证：直接跑真实实现，锁住 synced 汇总与 warnings 归一化。"""
    source = _skills_source()
    start = source.index("const summarizeReferenceSync = ")
    end = source.index("/** 打开重命名弹窗", start)
    snippet = source[start:end].rstrip()

    expression = """
const cases = [
  api.summarizeReferenceSync({ synced: { agents: 2, publications: 1, conversations: 3 }, warnings: ['A 未同步', 'B 未同步'] }),
  api.summarizeReferenceSync({ synced: { conversations: 4 }, warnings: [] }),
  api.summarizeReferenceSync({}),
  api.summarizeReferenceSync(undefined),
  api.summarizeReferenceSync({ synced: { agents: null, conversations: '2' }, warnings: '不是数组' })
];
return cases;
"""
    script = f"""
const fs = require('fs');
const ts = require('./frontend/node_modules/typescript');
const snippet = {json.dumps(snippet)};
const code = ts.transpileModule(snippet + "\\nmodule.exports = {{ summarizeReferenceSync }};", {{
  compilerOptions: {{ module: ts.ModuleKind.CommonJS, target: ts.ScriptTarget.ES2022 }}
}}).outputText;
const moduleRef = {{ exports: {{}} }};
new Function('module', 'exports', code)(moduleRef, moduleRef.exports);
const api = moduleRef.exports;
const result = (() => {{ {expression} }})();
process.stdout.write(JSON.stringify(result));
"""
    completed = subprocess.run(
        ["node", "-e", script],
        cwd=ROOT,
        check=True,
        capture_output=True,
        text=True,
    )
    cases = json.loads(completed.stdout)
    assert cases[0] == {"syncedCount": 6, "warnings": ["A 未同步", "B 未同步"]}
    assert cases[1] == {"syncedCount": 4, "warnings": []}
    assert cases[2] == {"syncedCount": 0, "warnings": []}
    assert cases[3] == {"syncedCount": 0, "warnings": []}
    # 后端少字段 / 类型不对时不能抛错，也不能把非数组当警告列表渲染
    assert cases[4] == {"syncedCount": 2, "warnings": []}


# ---------------------------------------------------------------------------
# personalOnly 越界守卫
# ---------------------------------------------------------------------------


def test_banner_action_handler_guards_platform_tabs():
    # 结束 marker 只写到 `const confirmState = ref`：后来给 ref 加了显式类型参数
    # （`ref<{...}>`），写死花括号会让这条契约随之脆断。
    handler = _function_body("handleSkillBannerAction", "const confirmState = ref")
    # 「平台技能」对登录用户可见（无写权限时只读），因此只受个人技能页限制
    personal_guard = handler.index("if (props.personalOnly) {")
    global_tab = handler.index("activeWorkspaceTab.value = 'global'")
    assert personal_guard < global_tab
    assert "showToast(" in handler[personal_guard:global_tab]
    # 「待审核」仍是平台管理分区：无权限时给出可见反馈而不是静默失效
    guard = handler.index("if (!canAccessPlatformWorkspace.value) {")
    review_tab = handler.index("activeWorkspaceTab.value = 'review'")
    assert guard < review_tab
    assert "showToast(" in handler[guard:review_tab]
    # 只有 create（新建）在个人技能页是合法的，且不受平台管理闸限制
    assert handler.index("if (type === 'create')") < guard


def test_platform_workspace_gate_is_single_source_of_truth():
    source = _skills_source()
    assert "const canAccessPlatformWorkspace = computed(() => !props.personalOnly && canManagePlatformSkills.value)" in source
    # 首屏落点已与「平台管理分区」解耦：平台技能对登录用户可读
    assert "const resolveInitialWorkspaceTab = (): 'global' | 'personal' =>" in source
    assert "!props.personalOnly ? 'global' : 'personal'" in source
    assert "watch(canAccessPlatformWorkspace, (available) => {" in source
    watcher = source[
        source.index("watch(canAccessPlatformWorkspace, (available) => {") : source.index("/** 列表类请求的并发计数")
    ]
    # 不可用时只把管理分区回落到「平台技能」，不能把用户踢回「我的技能」
    assert "activeWorkspaceTab.value === 'review' || activeWorkspaceTab.value === 'publications'" in watcher
    assert "activeWorkspaceTab.value = 'global'" in watcher
    assert "activeScope.value = 'personal'" not in watcher
    assert "activeWorkspaceTab.value = 'personal'" not in watcher
    # 平台视图渲染、列表渲染都挂在这道闸上
    assert "const showSkillList = computed(() => activeWorkspaceTab.value === 'global' || activeWorkspaceTab.value === 'personal')" in source
    assert 'activeWorkspaceTab === \'review\' && canAccessPlatformWorkspace' in source
    assert "<template v-if=\"showSkillList\">" in source
    assert "v-if=\"activeWorkspaceTab !== 'review'\"" not in source
    # 其他平台管理入口同样加闸
    review_open = _function_body("openPublicationReview", "const closePublicationReview = () => {")
    assert "if (!canAccessPlatformWorkspace.value) return" in review_open
    assert "const openPublicationReview" in source
    # 绑定反查 / 审核队列 / 已发布列表只在有写权限时预取；平台技能列表按 personalOnly 加载
    assert "if (!props.personalOnly && canManagePlatformSkills.value)" in source
    assert "if (!props.personalOnly) {" in source


def test_banner_receives_gate_and_hides_admin_steps():
    source = _skills_source()
    assert ':platform-workspace-available="canAccessPlatformWorkspace"' in source

    banner = _banner_source()
    # 组件声明该 prop，并在按钮渲染与事件派发两处生效
    assert "platformWorkspaceAvailable?: boolean" in banner
    assert "const isAdminOnlyStep = (item: { requiresPlatformAdmin?: boolean }) =>" in banner
    # 横幅已抽取共享外壳 FlowGuideStrip：不可用入口由 visibleSteps 统一降级为「无动作 + 说明文案」，
    # 外壳只在 item.actionType 存在时渲染按钮，因此这里同时校验降级逻辑与外壳的渲染条件。
    assert "const visibleSteps = computed(() =>" in banner
    assert "{ ...item, actionType: null, hintText: '仅平台技能管理员可用' }" in banner
    strip = (ROOT / "frontend/src/components/common/FlowGuideStrip.vue").read_text(encoding="utf-8")
    assert 'v-if="item.actionType"' in strip
    assert "'仅平台技能管理员可用'" in banner
    # 第 3/5 步（待审核 / 智能体中心）标记为平台管理入口；第 4 步「平台技能」对登录用户可见
    assert banner.count("requiresPlatformAdmin: true") == 2
    assert "if (type !== 'create' && type !== 'global' && !props.platformWorkspaceAvailable) return" in banner
    # 原有文案与路由不变（既有流程指引契约仍要成立）
    assert "前往待审核" in banner and "前往智能体中心" in banner
    assert "/dashboard/agent-management" in banner


def test_help_modal_admin_entries_are_guarded():
    source = _skills_source()
    # 帮助弹窗不能绕过 banner 的守卫自己写 tab / 跳路由
    assert "showHelpModal = false; handleSkillBannerAction('review')" in source
    assert "showHelpModal = false; handleSkillBannerAction('global')" in source
    assert "showHelpModal = false; activeWorkspaceTab = 'review'" not in source
    assert "showHelpModal = false; activeWorkspaceTab = 'global'; activeScope = 'global'" not in source
    # 两个仍然受限的入口（待审核 / 智能体中心）要有 v-if 守卫；「平台技能」已对登录用户开放
    assert source.count('v-if="canAccessPlatformWorkspace"') >= 2
    help_block = source[source.index('v-if="showHelpModal"') : source.index('<!-- 新建技能 Modal -->')]
    assert help_block.count('v-if="canAccessPlatformWorkspace"') >= 2
    assert "仅平台技能管理员可访问「待审核」" in help_block
    assert "个人技能页不提供该入口" in help_block
    assert "智能体中心需要平台管理权限" in help_block
