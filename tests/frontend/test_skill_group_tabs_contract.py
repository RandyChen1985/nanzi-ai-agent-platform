"""技能工作台「业务分组」契约：SKILL.md frontmatter 存储 + 页签与筛选。

为什么这组测试值得存在：

* 技能和工具/数据集**不是一类东西**——列表由扫物理目录 + 解析 `SKILL.md` 得出，没有
  主表。分组因此写进 frontmatter 而不是建表，这一条一旦被改成建表，复制/分享/发布技能
  时分组就会悄悄丢，用户只会觉得「分组自己没了」；
* 技能页已有**四个一级视图**和「我的技能」内的**左侧二级菜单**，分组页签是第三层。
  位置放错（塞进左侧菜单或叠在二级菜单上）会把三个页面统一的交互拆掉；
* 技能的搜索必须能命中**技能 ID**（用户直接搜 `weather_travel_advice`），而共享分组模块
  的默认字段里没有 `id`——漏了就是「搜 ID 搜不到」，且这种退步不会有任何报错。
"""
from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
PROBE = ROOT / "tests" / "frontend" / "skill_group_tabs_probe.cjs"
VIEW = ROOT / "frontend" / "src" / "views" / "SkillsManagement.vue"
BACKEND_SKILLS = ROOT / "app" / "api" / "portal" / "endpoints" / "skills.py"
BACKEND_PERSONAL = ROOT / "app" / "api" / "portal" / "endpoints" / "personal_skills.py"
SKILL_METADATA = ROOT / "app" / "utils" / "skill_metadata.py"


@pytest.fixture(scope="module")
def view() -> str:
    return VIEW.read_text(encoding="utf-8")


@pytest.fixture(scope="module")
def probe() -> dict:
    completed = subprocess.run(
        ["node", str(PROBE)], capture_output=True, text=True, timeout=120
    )
    assert completed.returncode == 0, completed.stderr
    return json.loads(completed.stdout)


# --------------------------------------------------------------------------
# 页面行为（真跑 toolGroupTabs.ts）
# --------------------------------------------------------------------------


def test_tabs_order_and_counts(probe: dict) -> None:
    """全部 → 各组（按命中数降序）→ 未分组（固定末位）。"""
    assert probe["base_total"] == 12
    assert probe["base_ungrouped"] == 4
    assert probe["base_items"] == [
        ["全部", 12],
        ["数据检索", 3],
        ["开发工具", 2],
        ["文档写作", 2],
        ["天气出行", 1],
        ["未分组", 4],
    ]
    assert probe["base_first_key"] == "all"
    assert probe["base_last_key"] == "__ungrouped__"


def test_search_matches_skill_id(probe: dict) -> None:
    """技能页最高频的搜索方式是搜 ID，共享模块必须把 `id` 纳入搜索字段。"""
    assert probe["kw_id_total"] == 1
    assert probe["kw_id_labels"] == ["全部", "天气出行"]
    # 去掉 id 字段就搜不到 —— 证明 searchFields 里的 id 真的生效，不是被其它字段兜住的
    assert probe["kw_id_without_id_field_total"] == 0


def test_search_matches_name_within_group(probe: dict) -> None:
    """命中名称时页签只剩有命中的那一组（空组不留页签，避免点进去是空的）。"""
    assert probe["kw_name_total"] == 1
    assert probe["kw_name_items"] == [["全部", 1], ["数据检索", 1]]


def test_blank_null_missing_group_are_all_ungrouped(probe: dict) -> None:
    """空串 / 空格 / null / 缺失 四种写法都归「未分组」，不能各成一个页签。"""
    assert probe["blank_is_empty"] is True
    assert probe["null_is_empty"] is True
    assert probe["missing_is_empty"] is True
    assert probe["trimmed_group"] == "开发工具"


def test_enabled_string_maps_to_boolean(probe: dict) -> None:
    """技能 `enabled` 是 frontmatter 字符串，必须按真值白名单映射成布尔。"""
    assert probe["enabled_true"] is True
    assert probe["enabled_false"] is False
    assert probe["enabled_False_capital"] is False, "enabled: False（首字母大写）也是禁用"
    assert probe["enabled_missing"] is True, "没有 enabled 的技能默认启用"


def test_tab_count_equals_row_filter(probe: dict) -> None:
    """页签计数与列表过滤必须同源，否则会出现「页签说 1 个、点进去 0 条」。"""
    assert probe["consistency"] == probe["consistency_tab_total"] == 1


def test_ungrouped_tab_hidden_when_all_grouped(probe: dict) -> None:
    assert probe["all_grouped_has_ungrouped"] is False


def test_single_group_still_renders_bar(probe: dict) -> None:
    """只有一个分组时页签是「全部 + 该组」，组件据此判断是否隐藏整条。"""
    assert probe["single_group_items_length"] == 2


def test_platform_and_personal_count_separately(probe: dict) -> None:
    """平台技能与我的技能各算各的页签，绝不混在一起。"""
    assert probe["personal_total"] == 2
    assert probe["personal_items"] == [["全部", 2], ["文档写作", 1], ["未分组", 1]]
    assert probe["personal_only_own_groups"] is True


# --------------------------------------------------------------------------
# 前端源码契约
# --------------------------------------------------------------------------


def test_view_reuses_shared_group_module(view: str) -> None:
    """页签顺序、计数口径、溢出方案必须复用共享模块，页面不许再写一份过滤。"""
    assert "computeGroupTabs" in view
    assert "matchesToolFilters" in view
    assert "ToolGroupTabs" in view
    assert "@/utils/toolGroupTabs" in view or "utils/toolGroupTabs" in view


def test_view_maps_skill_shape_once(view: str) -> None:
    """技能 → 共享形状的映射只允许有一处（`toGroupableSkill`）。

    `enabled` 字符串 → `is_active` 布尔、`group` → `group_name`，两处各写一份就会出现
    「页签按一套口径、列表按另一套口径」。
    """
    assert view.count("const toGroupableSkill") == 1
    assert view.count("is_active: isSkillEnabled(skill.enabled)") == 1
    assert view.count("group_name: skill.group ?? ''") == 1


def test_view_search_fields_include_id(view: str) -> None:
    """技能搜索必须包含 ID 字段（否则搜 `weather_travel_advice` 无结果）。"""
    assert "SKILL_SEARCH_FIELDS" in view
    assert "const SKILL_SEARCH_FIELDS: readonly ToolSearchField[] = ['id', 'name', 'description']" in view


def test_view_group_filter_is_shared_state(view: str) -> None:
    """页签与卡片徽标共用同一个 `skillGroupFilter`，不各存一份。"""
    assert view.count("const skillGroupFilter = ref(ALL_GROUP_KEY)") == 1
    assert view.count('v-model="skillGroupFilter"') == 1
    # 卡片徽标点击锁定同一分组
    assert 'skillGroupFilter = String(skill.group)' in view


def test_group_badge_hidden_when_locked(view: str) -> None:
    """锁定某组时卡片不再逐条显示分组徽标（每条都一样，纯噪音）。"""
    assert 'v-if="skillGroupFilter === ALL_GROUP_KEY"' in view


def test_group_filter_resets_on_view_switch(view: str) -> None:
    """切视图 / 切二级菜单后必须回到「全部」，否则停留在一个对不上的分组上看到空列表。"""
    assert "watch([activeScope, activeWorkspaceTab, personalMenu]" in view
    # 首屏加载期不重置（列表还空着，会把用户刚点的分组清掉）
    assert "if (!currentScopeSkills.value.length) return" in view


def test_create_form_and_group_dialog_present(view: str) -> None:
    """新建技能能填分组；已有技能可在「⋯ → 设置分组」里改。"""
    assert 'v-model="newSkill.group"' in view
    assert "group: newSkill.value.group.trim() || null" in view
    assert "runSkillAction('group')" in view
    assert "openGroupDialog" in view
    assert "saveSkillGroup" in view
    # 两个入口共用同一份候选，避免同一业务域被写成两个名字
    assert 'id="skill-group-options"' in view
    assert view.count('list="skill-group-options"') == 2


def test_group_write_uses_dedicated_endpoint(view: str) -> None:
    """改分组走专门的端点，不回传整份 SKILL.md（并发编辑时整文件覆盖会冲掉正文）。"""
    assert "/group`" in view
    assert "axios.put(url, { group: target.value.trim() || null })" in view


# --------------------------------------------------------------------------
# 后端契约
# --------------------------------------------------------------------------


def test_parser_outputs_group_field() -> None:
    """列表与详情都要带 `group`，否则前端只能看到「全部」一个页签。"""
    text = BACKEND_SKILLS.read_text(encoding="utf-8")
    assert '"group": get_skill_group(meta),' in text


def test_create_uses_shared_template() -> None:
    """平台与个人技能共用同一份 SKILL.md 模板（含分组行），不许各拼一份。"""
    for path in (BACKEND_SKILLS, BACKEND_PERSONAL):
        text = path.read_text(encoding="utf-8")
        assert "build_default_skill_md(req.name, req.description, req.group)" in text
        assert "HARD-GATE" not in text, "模板应只在 utils/skill_metadata.py 里维护一份"


def test_group_endpoints_exist() -> None:
    """两个 scope 各有一个只改分组行的端点，且都复用同一个写入实现。"""
    for path in (BACKEND_SKILLS, BACKEND_PERSONAL):
        text = path.read_text(encoding="utf-8")
        assert '@router.put("/{skill_id}/group"' in text
        assert "set_skill_group_in_file(" in text


def test_skill_metadata_group_helpers() -> None:
    """解析/写入层的三个入口必须都在，且键名与数据集口径一致（都用 `group_name` 语义的 group 键）。"""
    text = SKILL_METADATA.read_text(encoding="utf-8")
    assert 'SKILL_GROUP_KEY = "group"' in text
    for name in ("normalize_skill_group", "get_skill_group", "build_default_skill_md", "set_skill_group_in_file"):
        assert f"def {name}(" in text


# --------------------------------------------------------------------------
# 后端纯函数真跑：模板逐字一致、写入只动一行、正文不丢
# --------------------------------------------------------------------------

sys.path.insert(0, str(ROOT))


def test_default_template_matches_legacy_output() -> None:
    """不带分组时输出必须与历史模板逐字一致——存量技能不需要回填。"""
    from app.utils.skill_metadata import build_default_skill_md

    legacy = (
        "---\nname: 测试技能\ndescription: 描述\n---\n\n# 测试技能\n\n"
        "在此处编写该技能的具体战术规范、操作流程与 <HARD-GATE> 红线守则。\n"
    )
    assert build_default_skill_md("测试技能", "描述") == legacy


def test_group_roundtrip_in_file(tmp_path: Path) -> None:
    """写入 → 覆盖 → 清除，且正文与其它字段一个都不丢。"""
    from app.utils.skill_metadata import (
        build_default_skill_md,
        get_skill_group,
        parse_skill_frontmatter,
        set_skill_group_in_file,
    )

    skill_md = tmp_path / "SKILL.md"
    body_tail = "## 正文\n\n这里是用户的战术规范，绝不能被覆盖掉。\n"
    skill_md.write_text(build_default_skill_md("测试技能", "描述") + body_tail, encoding="utf-8")

    assert set_skill_group_in_file(str(skill_md), " 天气出行 ") is True
    assert get_skill_group(parse_skill_frontmatter("t", str(skill_md))) == "天气出行"

    # 覆盖写：只留一行 group，不能出现第二行
    assert set_skill_group_in_file(str(skill_md), "数据检索") is True
    text = skill_md.read_text(encoding="utf-8")
    assert text.count("group:") == 1
    assert "group: 数据检索" in text

    # 清除：连 `group:` 这个词都不该留（留空值会被解析成空串而非缺失）
    assert set_skill_group_in_file(str(skill_md), "   ") is True
    text = skill_md.read_text(encoding="utf-8")
    assert "group:" not in text
    assert "name: 测试技能" in text and body_tail.strip() in text


def test_group_write_on_file_without_frontmatter(tmp_path: Path) -> None:
    """纯 Markdown 技能补一份最小 frontmatter，正文原样保留。"""
    from app.utils.skill_metadata import set_skill_group_in_file

    skill_md = tmp_path / "plain.md"
    skill_md.write_text("# 只有正文\n\n内容\n", encoding="utf-8")
    assert set_skill_group_in_file(str(skill_md), "文档写作") is True
    text = skill_md.read_text(encoding="utf-8")
    assert text.startswith("---\ngroup: 文档写作\n---\n\n# 只有正文")


def test_group_write_tolerates_no_trailing_newline(tmp_path: Path) -> None:
    """闭合 `---` 在文件末尾（无尾换行）也要能改：手写 SKILL.md 的常见形态。"""
    from app.utils.skill_metadata import set_skill_group_in_file

    skill_md = tmp_path / "nofinal.md"
    skill_md.write_text("---\nname: x\ndescription: y\n---", encoding="utf-8")
    assert set_skill_group_in_file(str(skill_md), "运维支撑") is True
    assert "group: 运维支撑" in skill_md.read_text(encoding="utf-8")


def test_normalize_group_rejects_newline_and_truncates() -> None:
    """换行会把 frontmatter 写坏，必须折成空格；超长按 64 截断（与数据集列宽一致）。"""
    from app.utils.skill_metadata import SKILL_GROUP_MAX_LEN, normalize_skill_group

    assert normalize_skill_group("a\nb") == "a b"
    assert normalize_skill_group("   ") is None
    assert normalize_skill_group(None) is None
    assert len(normalize_skill_group("x" * 100)) == SKILL_GROUP_MAX_LEN == 64
