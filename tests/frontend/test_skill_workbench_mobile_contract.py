"""技能工作台的移动端可用性契约。

窄屏（<768px）既没有 hover，也没有足够横向空间：顶部四个 tab 会被压成逐字换行，
卡片上的「导出 / 重命名 / 删除」因为 ``opacity-0 + group-hover`` 在触屏上永远不可见。
"""
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
SKILLS_VIEW = ROOT / "frontend/src/views/SkillsManagement.vue"


def _read(path: Path) -> str:
    return path.read_text(encoding="utf-8")


def test_workspace_tabs_scroll_horizontally_on_mobile():
    source = _read(SKILLS_VIEW)
    tab_block = source.split("<!-- Scope Tab 切换 -->", 1)[1].split("<!-- 已发布 / 下架管理面板", 1)[0]
    assert "overflow-x-auto" in tab_block, "tab 容器必须可横向滚动"
    assert "whitespace-nowrap" in tab_block, "tab 文字不允许逐字换行"
    # 四个 tab 按钮都要 shrink-0，否则仍会被压缩换行
    assert tab_block.count("shrink-0") >= 4


def test_card_actions_are_visible_without_hover_on_touch():
    source = _read(SKILLS_VIEW)
    for color in ("emerald", "indigo", "red"):
        needle = (
            f"hover:bg-{color}-50 hover:text-{color}-600 "
            "md:opacity-0 md:group-hover:opacity-100"
        )
        assert needle in source, f"{color} 按钮在触屏上不可见"
