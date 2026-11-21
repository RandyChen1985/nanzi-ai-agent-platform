"""契约：产物文件名只在**展示**处去掉 UUID 前缀，实际名与逻辑判断一律用真实名。

背景（用户反馈）：`generated/` 里每张生成的图片都叫
`26399a7a80ed41e58b2431a2ca949a34_generated-image-20261121-004916-1-7f3ecd.png`，
卡片标题被截成 `26399a7a80ed41e58b2431a2ca949...`，一排卡片认不出谁是谁。
那层 32 位 uuid 前缀是 `publish()` 加的内部去重手段，对用户没有意义。

关键在于「只在展示处去」：
- 重命名输入框必须仍是真实名，否则用户一改名就把 uuid 前缀写进真实文件；
- 搜索、扩展名判定、系统保护名单也必须用真实名，否则筛选和平安逻辑会错位；
- 下载保存名与分享弹窗显示名用干净名是合理的（后端仍按 `path` 定位）。

最后一条用 Node 探针真跑边界——剥错或不剥都会让界面与磁盘对不上。
"""

from __future__ import annotations

import subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
PAGE = ROOT / "frontend/src/views/WorkspaceManage.vue"
HELPER = ROOT / "frontend/src/utils/workspaceFileName.ts"


def _read() -> str:
    return PAGE.read_text(encoding="utf-8")


def test_display_helper_exists_and_only_strips_uuid_prefix():
    source = HELPER.read_text(encoding="utf-8")
    assert "export function displayFileName(" in source
    assert "/^[0-9a-f]{32}_/i" in source
    # 剥完为空要退回原名，不能在界面上显示空白
    assert "return stripped || raw;" in source


def test_grid_and_list_titles_use_display_name():
    source = _read()
    assert 'import { displayFileName } from "@/utils/workspaceFileName"' in source
    # 卡片视图标题
    assert '{{ displayFileName(item.name) }}</p>' in source
    # 列表视图文件名（带 :title="item.path" 的那一行）
    assert ':title="item.path">{{ displayFileName(item.name) }}</span>' in source
    # 列表视图文件夹名
    assert 'truncate font-medium text-gray-800 hover:text-primary dark:text-gray-200">{{ displayFileName(item.name) }}' in source


def test_rename_dialog_keeps_the_real_name():
    """这条是防误改的核心：改名框里出现干净名，用户一提交就会把 uuid 前缀抹掉。"""
    source = _read()
    assert "dialogDraft.value = item.name" in source
    assert "dialogDraft.value = displayFileName" not in source


def test_logic_still_uses_the_real_name():
    """搜索、扩展名、系统保护、归档判定都不能被展示层改写。"""
    source = _read()
    assert "SYSTEM_PROTECTED.has(item.name)" in source
    assert "extOf(item.name)" in source
    assert "canPreviewWorkspaceFile(item.name)" in source
    assert "isExtractableArchive(item.name)" in source
    assert "item.name.toLowerCase().includes(kw)" in source
    # 路径永远原样交给后端
    assert "path: item.path" in source


def test_download_and_share_show_the_clean_name():
    """下载保存名与分享弹窗显示名用干净名；后端仍按 path 定位，不受影响。"""
    source = _read()
    assert "name: displayFileName(item.name)," in source  # downloadWorkspaceFile
    assert "shareTarget.value = { path: item.path, name: displayFileName(item.name)" in source
    assert "buildWorkspaceCanvasPayload(item.path, displayFileName(item.name))" in source


def test_display_name_behaviour_via_node():
    """16 条边界：该去的去、不该动的别动（31/33 位 hex、缺下划线、前缀不在开头等）。"""
    proc = subprocess.run(
        ["node", "tests/frontend/workspace_file_name.mjs"],
        cwd=ROOT,
        capture_output=True,
        text=True,
    )
    assert proc.returncode == 0, proc.stdout + proc.stderr
    assert "产物文件名展示测试全部通过" in proc.stdout
