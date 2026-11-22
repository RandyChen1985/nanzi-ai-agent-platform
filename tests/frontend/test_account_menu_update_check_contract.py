"""「关于」面板里的版本检测：前端只做展示，判定全在后端。

钉住四件事：
1. **不在前端重写版本比较**——四段版本与三段 tag 的补零比较只允许存在一份实现，
   两处各写一份，迟早出现「后端说有新版、前端说已最新」；
2. **白标可关**：仓库地址为空（`branding.hide_version_link`）时整块不出现；
3. **有新版才打扰**：窄面板只放一句摘要 + 三个动作（查看更新内容 / 稍后 / 忽略此版本），
   被忽略或稍后之后收成一行，不常驻占地方；
4. **展开才取数**：不展开「关于」就不打接口。
"""

from __future__ import annotations

from pathlib import Path

import pytest

pytestmark = pytest.mark.no_infrastructure

ROOT = Path(__file__).resolve().parents[2]


def _read(rel: str) -> str:
    return (ROOT / rel).read_text(encoding="utf-8")


@pytest.fixture(scope="module")
def menu() -> str:
    return _read("frontend/src/components/layout/AccountMenu.vue")


@pytest.fixture(scope="module")
def composable() -> str:
    return _read("frontend/src/composables/useUpdateCheck.ts")


@pytest.fixture(scope="module")
def service() -> str:
    return _read("app/services/update_check_service.py")


def test_frontend_does_not_reimplement_version_comparison(menu: str, composable: str, service: str) -> None:
    """比较器只有后端一份：前端组件与 composable 里都不许出现判定实现。"""

    assert "compare_versions" in service
    assert "compare_versions" not in composable
    # 前端不做「四段补零」这类计算，只把接口结论翻译成状态
    assert "segments" not in composable
    assert "compare_versions" not in menu


def test_component_delegates_to_composable(menu: str) -> None:
    assert 'from "@/composables/useUpdateCheck"' in menu
    for symbol in ("ensureUpdateChecked", "checkForUpdate", "ignoreUpdateVersion", "deferUpdateVersion"):
        assert symbol in menu


def test_status_block_hidden_when_version_link_disabled(menu: str) -> None:
    """白标部署关掉版本入口（repoUrl 为空）时，「有新版本」也跟着不出现。"""

    assert 'v-if="repoUrl && updateStatusText"' in menu
    # 「检查更新 / 重试」这一行同样以 repoUrl 为前提（常驻，检查中只置灰）
    assert 'v-if="repoUrl"' in menu


def test_outdated_block_offers_three_actions(menu: str) -> None:
    """有新版本时只给三个动作：看内容、稍后、忽略。不提供一键升级。"""

    assert 'v-if="updateState === \'outdated\' && updateData"' in menu
    assert "查看更新内容" in menu
    assert "稍后" in menu
    assert "忽略此版本" in menu
    assert "一键升级" not in menu
    assert "立即升级" not in menu


def test_changelog_opens_in_app_dialog_with_link_fallback(menu: str) -> None:
    """更新内容在平台内居中弹层里看；正文没缓存下来时才退回新标签打开仓库。"""

    assert '@click="notesOpen = true"' in menu
    assert "查看更新内容 ↗" in menu  # 回退分支仍保留外链
    assert "v-if=\"updateData.body\"" in menu
    assert "<UpdateNotesDialog" in menu
    assert ':visible="notesOpen"' in menu


def test_dialog_does_not_close_the_menu(menu: str) -> None:
    """弹层被 Teleport 到 body，不在菜单 rootRef 内：点它不该把菜单关掉。"""

    assert 'target.closest("[data-update-notes]")' in menu
    # Esc 先收弹层，用户再按一次才收菜单
    assert "if (notesOpen.value) {" in menu
    # 菜单关闭时弹层必须一起收，否则它会被落在屏幕上
    assert "notesOpen.value = false" in menu


def test_ignored_state_is_recoverable(menu: str) -> None:
    """忽略之后仍要留一条恢复入口，否则用户会以为提示坏了。"""

    assert "的提示" in menu
    assert "恢复提示" in menu


def test_checks_when_menu_opens_not_on_every_about_toggle(menu: str) -> None:
    """打开账户菜单就连带取一次结果（折叠态也要能亮小点），但只读缓存不 force。

    取数时机从「展开关于」放宽到「打开菜单」，是为了让折叠着的「关于」行
    也能提示有新版本；expand/collapse 反复触发由 ensureUpdateChecked 自己幂等挡住。
    """

    assert "watch(\n  () => props.visible," in menu
    assert "void ensureUpdateChecked(props.version)" in menu
    # 只有用户点「检查更新」才 force，打开菜单不主动打外部接口
    assert "void checkForUpdate(props.version)" in menu


def test_collapsed_row_shows_a_pending_update_dot(menu: str) -> None:
    """折叠时「关于」右侧只有版本号会被用户略过，加一个小橙点做最低限度提示。"""

    assert 'v-if="hasPendingUpdate" class="update-badge-dot"' in menu
    assert ".update-badge-dot {" in menu


def test_dev_build_has_no_latest_verdict(menu: str) -> None:
    """dev 构建没有可比版本，不能显示「已是最新」这种没有意义的结论。"""

    assert "normalizeVersion(props.version) !== \"\"" in menu


def test_api_endpoints(composable: str) -> None:
    assert 'const CHECK_URL = "/api/portal/system/update-check"' in composable
    # GET 读缓存、POST 强制刷新
    assert "axios.post(CHECK_URL" in composable
    assert "axios.get(CHECK_URL" in composable
    # 「忽略此版本」走账号偏好，和 ui_mode / 主题同一套
    assert "/api/portal/portal-prefs/ignored-update-version" in composable


def test_defer_is_session_only_and_ignore_is_persisted(composable: str) -> None:
    """「稍后」= 本次会话；「忽略」= 写账号偏好。两者语义不能混。"""

    assert 'const DEFERRED_KEY = "nanzi_update_deferred"' in composable
    assert "sessionStorage.setItem(DEFERRED_KEY" in composable
    assert "axios.put(\"/api/portal/portal-prefs/ignored-update-version\", { version: target })" in composable


def test_normalize_version_drops_build_segment(composable: str) -> None:
    """前端归一化只用于比对与忽略值，规则与后端一致：剥 v 前缀、取三段。"""

    assert 'replace(/^[vV]/, "")' in composable
    assert "parts.slice(0, 3).join(\".\")" in composable


@pytest.fixture(scope="module")
def dialog() -> str:
    return _read("frontend/src/components/layout/UpdateNotesDialog.vue")


def test_dialog_renders_cached_markdown_safely(dialog: str) -> None:
    assert 'import { renderMarkdown } from "@/utils/markdown"' in dialog
    assert "v-html=\"renderedHtml\"" in dialog
    # 正文来自上游，必须走带 sanitize 的那条渲染路径
    assert "renderMarkdown(props.body)" in dialog


def test_dialog_is_a_modal_with_its_own_scroll(dialog: str) -> None:
    assert "<Teleport to=\"body\">" in dialog
    assert 'role="dialog"' in dialog
    assert 'aria-modal="true"' in dialog
    # 正文区独立滚动，长文不会把底部按钮顶出屏幕
    assert "overflow-y: auto" in dialog


def test_dialog_closes_on_escape_and_backdrop(dialog: str) -> None:
    assert 'event.key === "Escape"' in dialog
    assert "event.stopPropagation()" in dialog
    assert '@click.self="emit(\'close\')"' in dialog


def test_dialog_offers_both_repo_links(dialog: str) -> None:
    """弹层正文是我们缓存的副本（超长还会截断），要留外链核对原文。

    Gitee 与 GitHub 两个地址都给：内网环境里常常只有其中一个能通。
    """

    assert "在 Gitee 打开 ↗" in dialog
    assert "在 GitHub 打开 ↗" in dialog
    assert ':href="releaseUrl"' in dialog
    assert ':href="githubUrl"' in dialog


def test_clicking_check_does_not_close_the_menu(menu: str) -> None:
    """回归：点「检查更新」后状态切到「检查中」，模板换掉按钮节点。

    事件冒泡到 document 时 target 已脱离 DOM，`rootRef.contains()` 必然为 false，
    于是被当成「点了菜单外面」把整个菜单关掉——用户连「检查中」都看不到。
    已经脱离文档的点击一律不算点外面。
    """

    assert "if (!document.body.contains(target)) return" in menu
    # 顺序很重要：先判定「是否已脱离文档」，再判定「是否在菜单内」
    assert menu.index("if (!document.body.contains(target)) return") < menu.index(
        "if (rootRef.value?.contains(target)) return"
    )


def test_button_stays_while_checking(menu: str) -> None:
    """检查中按钮只置灰、不移除：节点不消失，布局与焦点都不会跳。"""

    assert ':disabled="updateState === \'checking\'"' in menu
    # 动作区不再把 checking 排除在渲染条件外
    assert "v-else-if=\"repoUrl && updateState !== 'checking'\"" not in menu


def test_throttle_is_explained_but_not_shown_as_failure(menu: str, composable: str) -> None:
    """60 秒节流不是失败：灰字说明一句，不占「检查失败」的红色位置。"""

    assert "throttleHint" in menu
    assert "刚刚检查过，请 1 分钟后再试" in composable
    # 节流态不判成 failed
    assert "if (data.value.error && !data.value.throttled) return \"failed\"" in composable


def test_dev_build_still_reports_upstream_version(menu: str) -> None:
    """dev 构建判不出「是不是最新」，但「上游现在是多少」仍要说出来。"""

    assert "开发版 · 最新" in menu


def test_about_panel_has_no_duplicate_repo_link(menu: str) -> None:
    """「代码仓库」与「更新日志」指向同一个仓库，只留后者（用户要求去掉重复入口）。

    更新日志那条会落在具体 tag 上，信息更完整；两条并排只是重复。
    """

    assert ">代码仓库</a>" not in menu
    assert "更新日志：" in menu


def test_dev_build_shows_an_upstream_hint_block(menu: str) -> None:
    """开发版也要有和「有新版本」同形态的提示：告诉用户最新是多少，由他自己判断。

    版本号比较不出结论，不等于用户不需要这条信息——否则 dev 环境点半天只看到
    一行版本号，判断不了「该不该拉代码」。
    """

    assert "updateUncomparable && updateData" in menu
    assert "上游最新 {{ updateData.latest }}" in menu
    assert "当前是开发版，判断不了是否落后，请自行确认" in menu
    # 同一块里也要能展开更新内容
    assert 'v-else-if="updateUncomparable && updateData"' in menu


def test_check_button_is_always_visible(menu: str) -> None:
    """结果块与动作行互不挤占：任何状态都能看到「检查更新」。"""

    assert 'v-if="repoUrl" class="mt-1 flex items-center gap-2"' in menu


def test_changelog_opens_dialog_only_when_already_latest(menu: str) -> None:
    """已是最新时「更新日志」就地弹层；落后时保留外链。

    后端只缓存最新那次发行说明的正文，所以只有「当前版本 == 上游最新 tag」时，
    弹层里的内容才是这个版本对应的；落后时这个入口指向历史版本，
    拿最新版说明冒充旧版说明比跳一次浏览器糟糕得多。
    """

    assert "changelogOpensNotes" in menu
    assert "normalizeVersion(updateData.value.latest" in menu
    # 回落分支仍在：落后 / 开发版时指向历史 tag 或 Releases 列表
    assert ':href="changelogUrl"' in menu
