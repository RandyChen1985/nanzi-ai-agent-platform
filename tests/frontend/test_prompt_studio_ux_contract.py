"""提示词工坊的前端契约。

这些点单看都很小，但都属于"用户会直接撞上"的那一类：

* 权限口径必须和后端一致，否则被授权的编辑者看不到按钮，会以为页面坏了；
* 系统级智能体的线上配置由管理员维护，工坊里只能存草稿，不能留一个点了才报错的按钮；
* 编辑器页面必须有未保存离开保护——改一大段提示词再手滑刷新就全没了；
* 历史抽屉的差异比对不能用字符级算法硬算几千字，会把主线程卡住。
"""
from pathlib import Path

STUDIO = "frontend/src/views/PromptStudio.vue"


def _src() -> str:
    return Path(STUDIO).read_text(encoding="utf-8")


def test_edit_permission_matches_backend_permission_point():
    """后端认 element:prompts:edit，前端不能只认 role === 'admin'。"""
    src = _src()

    assert 'perms.includes("element:prompts:edit")' in src, \
        "编辑权限必须按权限点判断，否则被授权的非管理员看不到保存/发布按钮"
    assert "const isAdmin = computed" in src

    # 不能退回"仅管理员可编辑"
    can_edit_block = src.split("const canEdit = computed", 1)[1].split("});", 1)[0]
    assert "u.role === \"admin\"" in can_edit_block, "管理员仍应直接放行"
    assert "element:prompts:edit" in can_edit_block, "非管理员必须能靠权限点拿到编辑权"


def test_category_tabs_are_admin_only():
    """分类 tab 只对管理员有意义：普通用户只看到自己智能体的提示词。"""
    src = _src()

    tab_block = src.split("v-if=\"isAdmin\" class=\"flex p-0.5 bg-gray-200/60", 1)
    assert len(tab_block) > 1, "非管理员应隐藏「全部 / 系统 / 智能体」的切换"

    # 「系统」tab 只放平台提示词，不能把系统级智能体混进来
    assert "tab === 'System' && p.source !== 'system_config'" in src, \
        "系统级智能体属于 Agent 分类，混进「系统」会让人误以为那是平台配置"


def test_system_agents_are_not_shown_in_studio():
    """系统级智能体整体不在工坊露出——迭代走智能体中心，避免绕过审核。"""
    src = _src()

    assert "publishBlockedBySystem" not in src, \
        "若系统级已不可见，就不该保留「保存草稿」这类死分支"
    assert "系统级智能体不在工坊露出" in src, "需要在代码里说明这条约束的原因"


def test_unsaved_changes_are_guarded_on_unload():
    """有未保存改动时，刷新/关标签要拦住。"""
    src = _src()

    assert "beforeunload" in src
    assert "addEventListener(\"beforeunload\"" in src
    assert "removeEventListener(\"beforeunload\"" in src, "监听必须在卸载时移除，避免泄漏"
    assert "event.preventDefault()" in src


def test_cmd_s_saves_instead_of_browser_dialog():
    """Cmd/Ctrl+S 不该弹出浏览器「保存网页」。"""
    src = _src()

    assert "metaKey" in src and "ctrlKey" in src
    assert "event.preventDefault()" in src
    assert 'event.key.toLowerCase() !== "s"' in src


def test_history_diff_is_line_level_and_capped():
    """历史差异比对用行级并把输入截断——字符级 diff 在长提示词上会卡主线程。"""
    src = _src()

    assert "Diff.diffLines" in src, "应使用行级 diff"
    assert "Diff.diffChars" not in src, \
        "字符级 diff 对几千字的系统提示词是 O(n²)，历史上导致抽屉展开卡顿"
    assert "DIFF_TEXT_LIMIT" in src, "超长文本必须截断，否则几万字会把页面拖死"


def test_empty_list_explains_why():
    """列表为空要说清原因：搜索无果、没有数据、还是看不到任何智能体。"""
    src = _src()

    assert "emptyListHint" in src
    hint_block = src.split("const emptyListHint", 1)[1].split("});", 1)[0]
    assert "searchQuery" in hint_block, "要区分「搜索没搜到」"
    assert "agent" in hint_block, "要解释智能体提示词按可见性过滤后为空的情况"
