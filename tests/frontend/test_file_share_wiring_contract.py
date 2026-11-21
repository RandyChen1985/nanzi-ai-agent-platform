"""文件分享接线契约：漏接一处就会「点了分享没反应」，或者更糟——菜单里出现了点不开的项。

与 `test_chat_share_wiring_contract.py` 同一套路：只读源码比对关键字，不起浏览器、不连库。
"""

import re
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
pytestmark = pytest.mark.no_infrastructure


def _read(rel: str) -> str:
    return (ROOT / rel).read_text(encoding="utf-8")


# ---------------------------------------------------------------------------
# 菜单：两处入口共用 buildWorkspaceActionEntries，改一处必须两处同时出现
# ---------------------------------------------------------------------------


def test_share_key_is_declared_and_used_in_both_scenes():
    """普通项直接分享；公共项走「复制并分享」。

    公共目录不在分享支持的路径范围内（分享记录按 `(workspace_key, rel_path)` 定位文件），
    原来的 `entry('share', ...)` 会让用户在填完密码、有效期后才吃到「文件不在工作区目录内」。
    两个场景都必须给出分享入口，但公共项多出「先落到本人工作区」这一步。
    """
    source = _read("frontend/src/utils/workspaceActionEntries.ts")
    assert "| 'share'" in source
    assert "| 'copy-and-share'" in source
    assert source.count("entry('share', '分享文件', '🔗')") == 1
    assert source.count("entry('copy-and-share', '复制并分享', '🔗')") == 1

    # 公共项不能再出现「直接分享」：那正是这次要修掉的失败路径
    public_start = source.index("if (scene === 'public-item') {")
    public_block = source[public_start : source.index("// regular-item：用户自己的文件/目录")]
    assert "entry('share'" not in public_block
    assert "copy-and-share" in public_block


def test_share_sits_right_after_download():
    """位置契约：分享必须紧跟下载，都属于「把这份文件交给别人」。"""
    source = _read("frontend/src/utils/workspaceActionEntries.ts")
    regular = source.index("const fileEntries: WorkspaceActionEntry[] = [")
    block = source[regular : regular + 400]
    assert block.index("entry('download'") < block.index("entry('share'")


def test_trash_scene_has_no_share():
    """回收站里的东西不该能分享出去：路径已经不在原处，分享出去立刻就是死链。"""
    source = _read("frontend/src/utils/workspaceActionEntries.ts")
    trash_start = source.index("if (scene === 'trash-item') {")
    trash_end = source.index("if (scene === 'public-item') {")
    assert "share" not in source[trash_start:trash_end]


def test_directory_has_no_share():
    """目录不分享：当前只做单文件，目录要打包下载，是另一件事。"""
    source = _read("frontend/src/utils/workspaceActionEntries.ts")
    # 只看 regular-item 分支里的目录返回块：切到 public-item 会把它的文件分支一起框进来
    start = source.index("if (isDir) {\n    return [\n      entry('open-dir', '打开目录', '📂'),")
    # 切到目录 return 块的收尾，而不是「下一个变量声明」：文件分支前面会先算
    # `shareable`（判断归属），用变量名当边界会把文件分支的 share 一起框进来。
    end = source.index("\n    ]\n  }", start)
    block = source[start:end]
    # 断言精确到菜单项，而不是「块里不含 share 这个子串」——后者会被注释或变量名误伤
    assert "entry('share'" not in block
    assert 'entry("share"' not in block


def test_row_actions_emits_share():
    source = _read("frontend/src/components/workspace/WorkspaceRowActions.vue")
    assert '(e: "share", item: RowItem): void' in source
    assert 'case "share":' in source
    assert 'emit("share", props.item)' in source


def test_drawer_dispatches_share():
    source = _read("frontend/src/components/embed/WorkspaceBrowserDrawer.vue")
    assert "case 'share':" in source
    assert "openShareDialog(item)" in source
    assert "<ShareFileDialog" in source


# ---------------------------------------------------------------------------
# 两个宿主都真正挂上了弹窗，并把路径传进去
# ---------------------------------------------------------------------------


def test_workspace_manage_mounts_dialog_and_passes_path():
    source = _read("frontend/src/views/WorkspaceManage.vue")
    assert source.count('@share="openShare"') == 2  # 表格视图 + 卡片视图
    assert "ShareFileDialog" in source
    assert ':file-path="shareTarget?.path' in source
    assert "const openShare = (item: FsItem)" in source


def test_dialog_does_not_recompute_expiry():
    """有效期换算必须复用 shareExpiry 的纯函数，自己算一套就会出现「弹窗写 7 天、列表 6 天」。"""
    source = _read("frontend/src/components/ShareFileDialog.vue")
    assert "from '@/utils/shareExpiry'" in source
    assert "resolveExpiry(" in source
    assert "isExpiryPresetActive(" in source
    # 不允许在组件里手写毫秒换算
    assert "60 * 60 * 1000" not in source
    assert "24 * 60 * 60 * 1000" not in source


def test_dialog_renders_above_workspace_drawer():
    """从「浏览工作空间」抽屉点分享时，弹窗必须压在抽屉之上。

    抽屉自身 z-[125]、内部弹窗 z-[131]、拖拽遮罩 z-[300]；照抄会话分享弹窗的 z-[70]
    会被整块盖住（表现为弹窗只露出左边一截）。

    这里刻意**不对弹窗文件做全文 z 值扫描**：它的注释里写着「抽屉是 125 / 131」这类
    解释性数字，全文匹配会把注释当成真实层级（第一版就是这么判红的）。
    """
    dialog = _read("frontend/src/components/ShareFileDialog.vue")
    assert 'fixed inset-0 z-[1300]' in dialog, "遮罩层必须固定在 z-[1300]"

    drawer_z = {int(v) for v in re.findall(r"z-\[(\d+)\]", _read("frontend/src/components/embed/WorkspaceBrowserDrawer.vue"))}
    assert drawer_z
    # 1300 同时高于抽屉（131）、画布拖拽遮罩（300）与聊天输入浮层（1200），
    # 又低于 toast（9950+），成功提示不会被压在弹窗下面
    assert 1300 > max(drawer_z), f"分享弹窗必须高于工作区抽屉的 {sorted(drawer_z)}"


def test_both_share_dialogs_share_the_same_layer():
    """会话分享与文件分享弹窗必须同层。

    两者都能在「浏览工作空间」抽屉打开时出现（会话分享从侧栏点、文件分享从抽屉里点），
    层级一旦不一致，就会出现「一个盖住抽屉、另一个被抽屉盖住」的错位。
    """
    for rel in (
        "frontend/src/components/ShareConversationDialog.vue",
        "frontend/src/components/ShareFileDialog.vue",
    ):
        source = _read(rel)
        assert 'fixed inset-0 z-[1300]' in source, f"{rel} 的遮罩层必须是 z-[1300]"


def test_dialog_warns_about_content_fingerprint():
    """「分享的是此刻的副本」必须在点创建之前就看到，不能只写在文档里。"""
    source = _read("frontend/src/components/ShareFileDialog.vue")
    assert "此刻的文件副本" in source
    assert "isSensitiveName" in source


# ---------------------------------------------------------------------------
# 访客链路：路由免登录 + 下载走 HEAD 探测
# ---------------------------------------------------------------------------


def test_shared_file_route_is_public():
    source = _read("frontend/src/router/index.ts")
    assert "path: '/s/:shareId'" in source
    assert "SharedFile.vue" in source
    assert "meta: { public: true, title: '文件分享' }" in source


def test_visitor_client_avoids_global_401_redirect():
    """访客接口必须用独立 axios 实例：默认实例的 401 拦截器会把访客弹去登录页。"""
    source = _read("frontend/src/api/fileShare.ts")
    assert "axios.create(" in source
    assert "withCredentials: true" in source
    assert "/api/v1/share/file/" in source
    assert "/api/portal/file-shares" in source


def test_download_probes_with_head_before_downloading():
    """直接 <a download> 会把 410/401 的 JSON 错误页当文件存下来，必须先 HEAD 探测。"""
    source = _read("frontend/src/views/SharedFile.vue")
    assert "method: 'HEAD'" in source
    assert "probeReadable(" in source
    assert "downloadSharedFileUrl(" in source


# ---------------------------------------------------------------------------
# 落地页按 preview_kind 分流：HTML 能直接看，不是只能下载
# ---------------------------------------------------------------------------


def test_landing_page_renders_html_in_sandboxed_iframe():
    source = _read("frontend/src/views/SharedFile.vue")
    assert 'referrerpolicy="no-referrer"' in source
    assert "sharedFileRawUrl(" in source

    # 断言的是 sandbox **属性值**本身，不是全文：注释里会提到 allow-same-origin
    # （解释为什么不能加），拿全文做否定匹配会把注释也算成违规。
    attrs = re.findall(r'sandbox="([^"]*)"', source)
    assert attrs, "HTML 预览必须带 sandbox 属性"
    for attr in attrs:
        assert "allow-scripts" in attr, "不给 allow-scripts，生成的页面交互全废"
        assert "allow-same-origin" not in attr, (
            "allow-scripts 与 allow-same-origin 并存等于没有沙箱："
            "分享的 HTML 会拿到应用同源，能读浏览者的登录凭证"
        )


def test_landing_page_shows_images_and_svg_via_img_tag():
    """svg 用 <img>：img 里的 svg 脚本不执行，比 iframe 更安全。"""
    source = _read("frontend/src/views/SharedFile.vue")
    assert "kind === 'image' || kind === 'svg'" in source
    assert '<img' in source


def test_landing_page_renders_text_variants():
    source = _read("frontend/src/views/SharedFile.vue")
    assert "fetchFileShareText(" in source
    assert "MessageRenderer" in source          # markdown 复用消息渲染器
    assert "csvRows" in source                  # csv 转表格
    assert "truncated" in source                # 超 2MB 要给明确提示


def test_preview_kind_comes_from_backend():
    """呈现方式只由后端 classify_preview_kind 决定，前端不再维护一套扩展名清单。"""
    source = _read("frontend/src/api/fileShare.ts")
    assert "preview_kind: FilePreviewKind" in source
    assert "FilePreviewKind = 'html' | 'svg' | 'image' | 'pdf' | 'text' | 'download'" in source


def test_panel_separates_view_and_download_counts():
    source = _read("frontend/src/components/SharedLinksPanel.vue")
    assert "item.view_count" in source and "item.download_count" in source


def test_panel_has_file_tab():
    source = _read("frontend/src/components/SharedLinksPanel.vue")
    assert "文件链接" in source
    assert "listFileShares(" in source
    assert "revokeFileShare(" in source
