"""技能广场前端契约。

盯的是「用户能不能把这段提示词安全地交给智能体」这件事，而不是视觉细节：

1. tab 必须在「资源广场」**之后**，且 `?tab=gallery` 能深链、能刷新保持；
2. 安装提示词必须是**非交互**的（两个 `-y`、固定目录、超时兜底），前端不能自己拼一份
   少一个 `-y` 的版本——这是把会话拖成僵尸进程的根因；
3. 管理员才有的「安装到平台」入口用 `element:skills:admin` 判定，普通用户完全不渲染；
4. 已安装徽标只说明「库里已有同名技能」，别写成「已是最新」。
"""

from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
pytestmark = pytest.mark.no_infrastructure


def _read(path: str) -> str:
    return (ROOT / path).read_text(encoding="utf-8")


def test_gallery_tab_sits_after_resource_square_and_deeplinks():
    page = _read("frontend/src/views/PersonalWorkbench.vue")

    assert "技能广场" in page
    assert 'switchTab(\'gallery\')' in page
    assert "SkillGalleryPanel" in page

    # 顺序：工作台 → 资源广场 → 技能广场
    square_at = page.index("资源广场")
    gallery_at = page.index("技能广场")
    assert square_at < gallery_at, "技能广场必须排在资源广场之后"

    # 深链：?tab=gallery 要能被初始值和工作台 watch 同时识别
    assert '"square", "gallery"' in page or "'square', 'gallery'" in page
    assert "tabFromQuery" in page
    assert "query.tab = next" in page


def test_top_refresh_covers_gallery_tab():
    page = _read("frontend/src/views/PersonalWorkbench.vue")

    assert "galleryPanel" in page
    assert "galleryRefreshing" in page
    assert "刷新技能广场" in page
    # 刷新要真的调到面板的 reloadAll，而不是只 refresh() 工作台数据
    assert "galleryPanel.value?.reloadAll()" in page


def test_install_prompt_is_non_interactive():
    panel = _read("frontend/src/components/skill-gallery/SkillGalleryPanel.vue")
    drawer = _read("frontend/src/components/skill-gallery/SkillGalleryDrawer.vue")

    for source in (panel, drawer):
        # 前端不拼命令：一律用后端下发的 install_prompt / install_command，
        # 免得有人手滑拼出一版少一个 -y 的命令
        assert "npx -y skills add" not in source

    assert "install_prompt" in panel
    assert "install_command" in drawer
    assert "必须非交互执行" in drawer
    # 提示用户两个 -y 各管什么，否则容易被"顺手删掉一个"
    assert "跳过 npx 装包确认" in drawer
    assert "/workspace/.skill-tmp" in drawer
    # 兜底时长要与 catalog 提示词同口径（3 分钟），用户看详情就知道卡住会被中止
    assert "3 分钟" in drawer
    assert "import_skill_from_sandbox" in drawer or "install_prompt" in drawer


def test_admin_only_platform_install_button():
    panel = _read("frontend/src/components/skill-gallery/SkillGalleryPanel.vue")
    drawer = _read("frontend/src/components/skill-gallery/SkillGalleryDrawer.vue")

    assert "hasPermission('element:skills:admin')" in panel
    assert "canInstallPlatform" in panel
    assert "canInstallPlatform: boolean" in drawer
    assert "canInstallPlatform" in drawer
    # 平台入口靠 v-if 控制，不是 disabled——不给无权限的人一个必然失败的按钮
    assert 'v-if="canInstallPlatform"' in drawer
    assert "disabled" not in drawer.split("canInstallPlatform", 1)[1][:400]


def test_installed_badge_does_not_claim_latest_version():
    panel = _read("frontend/src/components/skill-gallery/SkillGalleryPanel.vue")
    drawer = _read("frontend/src/components/skill-gallery/SkillGalleryDrawer.vue")
    shared = _read("frontend/src/components/skill-gallery/installedLabel.ts")
    types = _read("frontend/src/types/skillGallery.ts")

    # 徽标文案收敛到一处：面板与抽屉之前各写一套，同一状态两种说法
    assert "installedLabel" in panel
    assert "installedLabel" in drawer
    assert "已在你的个人技能库" in shared
    assert "平台技能库已收录" in shared
    # 旧的两套自定义文案不得回流
    assert "已在你的技能库" not in panel
    assert "平台已收录" not in panel
    # 只判定「有没有」，不在徽标文案里声称是最新版
    assert "已是最新版" not in panel
    assert "已是最新版" not in drawer
    assert "up to date" not in panel.lower()
    assert "installed" in types
    assert "'personal' | 'global' | null" in types


def test_gallery_api_only_exposes_read_endpoints():
    """广场没有安装/写入接口。

    唯一的 POST 是行为事件上报（``/skills/gallery/events``），它只写结构化日志，
    不落库、不改任何技能状态——不能因为埋点就把「只读」这条边界放开。
    """
    api = _read("frontend/src/api/skillGallery.ts")

    assert "/skills/gallery" in api
    assert "axios.get" in api
    for verb in ("axios.put", "axios.delete", "axios.patch"):
        assert verb not in api, f"技能广场是只读的，不该出现 {verb}"
    assert api.count("axios.post") == 1, "只允许事件上报这一处 POST"
    post_block = api.split("axios.post", 1)[1][:200]
    assert "/skills/gallery/events" in post_block
    for forbidden in ("/install", "/upload", "/publish"):
        assert forbidden not in api


def test_intro_is_rendered_as_escaped_markdown_not_raw_html():
    drawer = _read("frontend/src/components/skill-gallery/SkillGalleryDrawer.vue")

    # 正文用 v-html 渲染我们自己写的 Markdown，渲染前必须先转义
    assert "v-html" in drawer
    assert "replace(/&/g, '&amp;')" in drawer
    assert "replace(/</g, '&lt;')" in drawer
    assert "replace(/>/g, '&gt;')" in drawer


def test_catalog_ships_own_intro_docs_not_upstream_skill_md():
    """目录里只有我们自己写的介绍，没有第三方 SKILL.md 原文。

    ``anthropics/skills`` 是 Proprietary 许可，禁止把材料复制到服务之外再分发。
    """
    import json

    catalog = json.loads(
        (ROOT / "app/services/ai/skill_gallery/catalog.json").read_text(encoding="utf-8")
    )
    assert catalog["skills"]
    for item in catalog["skills"]:
        assert item["install_command"].count("npx -y skills add") == 1
        assert item["install_command"].rstrip().endswith("-y")
        assert "import_skill_from_sandbox" in item["install_prompt"]
        doc = ROOT / "app/services/ai/skill_gallery" / item["intro_doc"]
        assert doc.is_file(), f"{item['id']} 缺少介绍文档"
        body = doc.read_text(encoding="utf-8")
        assert not body.startswith("---"), "介绍文档不该是上游 SKILL.md 的拷贝"


def test_more_skills_link_points_to_external_hub():
    """广场只放精选，全量入口外链到 ModelScope 技能广场。

    外链必须带 `target="_blank"` 与 `rel="noreferrer noopener"`：前者避免用户
    在当前 tag 里走掉（回来还在工作台），后者掐掉 `window.opener` 与 Referer。
    """
    panel = _read("frontend/src/components/skill-gallery/SkillGalleryPanel.vue")

    assert "https://modelscope.cn/skills" in panel
    assert "EXTERNAL_SKILL_HUB_URL" in panel
    assert "更多技能" in panel

    # 链接落在副标题那一行，不是另起一块
    header = panel.split("精选可直接抄用的智能体技能", 1)[1][:600]
    assert "更多技能" in header

    # 新窗口 + 隔离（常量定义在 script 里，这里只看模板中的那次渲染）
    template = panel.split("<template>", 1)[1]
    link = template.split("EXTERNAL_SKILL_HUB_URL", 1)[1][:400]
    assert 'target="_blank"' in link
    assert 'rel="noreferrer noopener"' in link


def test_platform_prompt_is_optional_in_types_and_never_falls_back_to_personal():
    """平台提示词按权限下发，前端类型必须允许缺失，且不能静默拿个人版顶替。

    后端对无 ``element:skills:admin`` 的用户不返回 ``install_prompt_global``。若前端用
    ``install_prompt_global || install_prompt`` 兜底，管理员在权限查询失败（fail-closed）
    时会复制到**个人版**提示词，却提示「已复制安装到平台提示词」——用户以为装到平台，
    实际只装进自己库。宁可提示失败。
    """
    types = _read("frontend/src/types/skillGallery.ts")
    drawer = _read("frontend/src/components/skill-gallery/SkillGalleryDrawer.vue")

    assert "install_prompt_global?: string" in types

    platform_fn = drawer.split("function copyPlatform", 1)[1]
    assert "install_prompt_global ||" not in platform_fn
    assert "未获取到平台安装提示词" in platform_fn


def test_gallery_panel_recovers_from_failures():
    """错误态要给重试入口，空态要区分「目录空」与「筛没了」。"""
    panel = _read("frontend/src/components/skill-gallery/SkillGalleryPanel.vue")

    assert "重试" in panel
    assert '@click="reloadAll"' in panel
    # 之前目录为空时也提示「换个关键词试试」，把故障说成用户搜错
    assert "emptyHint" in panel
    assert "技能目录暂时是空的" in panel


def test_gallery_cards_are_keyboard_reachable():
    """卡片只挂 @click 时键盘用户打不开详情。"""
    panel = _read("frontend/src/components/skill-gallery/SkillGalleryPanel.vue")

    assert 'tabindex="0"' in panel
    assert "@keydown.enter.self.prevent" in panel
    assert "@keydown.space.self.prevent" in panel
    assert "aria-label" in panel


def test_installed_skill_points_to_skill_workbench():
    """装完之后要有去处：技能工作台能看内容、启用状态与卸载。"""
    panel = _read("frontend/src/components/skill-gallery/SkillGalleryPanel.vue")
    drawer = _read("frontend/src/components/skill-gallery/SkillGalleryDrawer.vue")

    assert "menu:skills_management" in panel
    assert "/dashboard/skills" in panel
    assert "/dashboard/skills" in drawer
    # 没有菜单权限的账号不显示入口，免得点进去 403
    assert "canOpenSkillAdmin" in panel
    assert "canOpenSkillAdmin: boolean" in drawer
    assert 'v-if="detail.installed && canOpenSkillAdmin"' in drawer


def test_drawer_shows_catalog_and_installed_versions_separately():
    """目录版本 vs 库内版本要分开：只判断「有没有」，不知道「是不是最新」。"""
    drawer = _read("frontend/src/components/skill-gallery/SkillGalleryDrawer.vue")
    types = _read("frontend/src/types/skillGallery.ts")

    assert "目录版本" in drawer
    assert "库内版本" in drawer
    assert "detail.installed_version || '未知'" in drawer
    assert "installed_version" in types


def test_gallery_reports_three_behavior_events():
    """行为事件只在复制成功 / 详情加载成功后上报，失败不上报。"""
    panel = _read("frontend/src/components/skill-gallery/SkillGalleryPanel.vue")
    drawer = _read("frontend/src/components/skill-gallery/SkillGalleryDrawer.vue")
    api = _read("frontend/src/api/skillGallery.ts")

    assert "reportSkillGalleryEvent" in panel
    assert "reportSkillGalleryEvent" in drawer
    assert "'prompt_copy'" in panel
    assert "'platform_prompt_copy'" in drawer
    assert "'detail_open'" in drawer
    assert "/skills/gallery/events" in api
    # 复制失败也上报会把「复制量」抬虚
    assert "if (copied) void reportSkillGalleryEvent" in panel
