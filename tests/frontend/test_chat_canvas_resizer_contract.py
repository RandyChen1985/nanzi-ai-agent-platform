from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]


def test_chat_canvas_resizer_contract():
    canvas_file = ROOT / "frontend/src/components/embed/ChatCanvas.vue"
    assert canvas_file.exists(), "ChatCanvas.vue file must exist"

    content = canvas_file.read_text(encoding="utf-8")

    # Verify split-screen resizer storage key and state
    assert "nanzi_canvas_preferred_width" in content
    assert "customWidth" in content
    assert "isResizing" in content

    # Verify drag handlers
    assert "startResize" in content
    assert "handleResizing" in content
    assert "stopResize" in content
    assert "resetWidth" in content

    # Verify style and template handle
    assert "panelStyle" in content
    assert "cursor-col-resize" in content
    assert "@mousedown=\"startResize\"" in content
    assert "@dblclick=\"resetWidth\"" in content

    # Verify pinning support
    assert "defineModel<boolean>('pinned'" in content
    assert "pinned = !pinned" in content
    # 钉住优先于 overlay：工作区打开 auto-pin 后用 fixed，避免被工作区抽屉盖住
    assert "if (pinned.value)" in content
    assert "z-[145]" in content
    assert "adjacentDockWidth" in content
    assert "applyFillAvailableWidth" in content
    assert "max-w-[28rem]" not in content.split("if (pinned.value)")[1].split("if (props.overlay)")[0]

    embed_chat = (ROOT / "frontend/src/views/EmbedChat.vue").read_text(encoding="utf-8")
    assert "canvasPinnedWidthPx" in embed_chat

    for chat_surface in (
        embed_chat,
        (ROOT / "frontend/src/views/AgentDebug.vue").read_text(encoding="utf-8"),
    ):
        assert "canvasPinned" in chat_surface
        assert 'v-model:pinned="canvasPinned"' in chat_surface


def test_left_docked_canvas_offsets_chat_on_the_left():
    """工作空间预览的画布靠左停靠，让位必须落在左边，不能混进 marginRight。

    回归背景（已由代码证据链定位）：`totalPinnedDrawerPx` 无条件把画布宽度加进去，
    而 `pinnedDrawerMarginStyle` 只产出 `marginRight`。但 `canvasFromWorkspace` 为真时
    画布是 `fixed left-0` 的左停靠面板（EmbedChat 传 dock-side=left，ChatCanvas 用
    `fixed left-0 right-auto`），于是：
      - 对话列的左边界仍在 x=0，被画布从左切掉「画布宽」那一条；
      - 右侧同时空出等宽的死空白。
    把画布拖窄时最明显（拖宽到一半以上对话列宽度直接算成 0）。

    修法边界：右停靠画布必须保持原样（它确实需要 marginRight），所以两侧让位要拆成
    两个来源，而不是简单删掉画布那一项。
    """
    embed_chat = (ROOT / "frontend/src/views/EmbedChat.vue").read_text(encoding="utf-8")

    # 左右两份让位宽度都要存在，且按停靠侧二选一
    assert "const canvasRightDockWidthPx = computed(" in embed_chat
    assert "const canvasLeftDockWidthPx = computed(" in embed_chat
    assert "canvasFromWorkspace.value ? 0 : canvasPinnedWidthPx.value" in embed_chat
    assert "canvasFromWorkspace.value ? canvasPinnedWidthPx.value : 0" in embed_chat

    # 右侧总和只能加「右停靠」那份，绝不能再直接加画布宽度
    total_block = embed_chat.split("const totalPinnedDrawerPx = computed(")[1].split("});")[0]
    assert "canvasRightDockWidthPx.value" in total_block
    assert "canvasPinnedWidthPx.value" not in total_block, (
        "画布宽度又被无条件加进右侧总和了：左停靠时会让对话列左边界停在 x=0，"
        "被画布从左侧切掉一截，右侧还留等宽死空白"
    )

    # 左侧让位必须真的落到 `style.marginLeft` **赋值**上，且 marginRight 仍然保留。
    # ⚠️ 不能只断言 `"marginLeft" in style_block`：这段代码附近就有含 "marginLeft"
    # 字样的注释，删掉赋值、甚至把 marginLeft 写成 marginRight 都能蒙过去。
    # 变异验证（M2 删除赋值 / M4 改成 marginRight）实打实抓到过这个假绿，勿再退回弱断言。
    style_block = embed_chat.split("const pinnedDrawerMarginStyle = computed(")[1].split("});")[0]
    margin_left_lines = [
        line for line in style_block.splitlines() if "style.marginLeft" in line
    ]
    assert margin_left_lines, "左停靠让位没有写到 style.marginLeft 赋值上"
    assert any("leftPx" in line for line in margin_left_lines), (
        "style.marginLeft 必须用左停靠那份宽度（canvasLeftDockWidthPx）"
    )
    assert "style.marginRight = " in style_block, "右侧让位不能被删掉"
    assert "canvasLeftDockWidthPx.value" in style_block

    # 右停靠画布的行为不能被改坏：canvasPinnedWidthPx 仍是右停靠那份的来源
    right_block = embed_chat.split("const canvasRightDockWidthPx = computed(")[1].split(");")[0]
    assert "canvasPinnedWidthPx.value" in right_block


def test_canvas_pin_default_depends_on_open_source():
    """钉住的默认值按「打开来源」区分，而不是一个全局常量。

    用户需求原文：「是为了方便 ai 聊天消息中打开画布内容，如果我们从我的工作空间中
    打开画布，能不能这边不默认钉住画布呢」。所以：
      - 对话消息里打开（handleOpenCanvas：工具调用、对比、远程 HTML、直传 payload）
        → **保持默认钉住**，方便画布与对话并排；
      - 工作空间里打开（handleWorkspaceFilePreview）
        → **默认不钉住**，避免预览文件时把对话区占满。

    注意这跟 `test_workspace_canvas_mobile_keeps_workspace_and_skips_pin` 不冲突：
    移动端无论哪条路径都不钉住（全屏置顶），桌面端才按来源区分。
    """
    composable = (ROOT / "frontend/src/composables/chat/useWorkspaceCanvas.ts").read_text(
        encoding="utf-8"
    )

    # showCanvas 必须能按来源指定是否钉住，且移动端始终不钉住
    assert "const showCanvas = (pinByDefault = true)" in composable
    assert "canvasPinned.value = pinByDefault && !mobile" in composable

    # 工作空间预览：显式传 false
    workspace_block = composable.split("const handleWorkspaceFilePreview =")[1].split(
        "const handleOpenCanvas ="
    )[0]
    assert "showCanvas(false)" in workspace_block, "工作空间打开画布应默认不钉住"

    # 对话侧：保持默认钉住，绝不传 false
    open_canvas_block = composable.split("const handleOpenCanvas =")[1]
    assert "showCanvas(false)" not in open_canvas_block, (
        "对话侧打开画布必须保持默认钉住 —— 这是用户明确要保留的行为"
    )
    assert "showCanvas()" in open_canvas_block
