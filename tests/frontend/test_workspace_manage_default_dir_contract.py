"""Contract: 工作空间页默认落「我的目录」，公共目录走「公共资源」入口。"""

from pathlib import Path

import pytest


pytestmark = pytest.mark.no_infrastructure

ROOT = Path(__file__).resolve().parents[2]
PAGE = ROOT / "frontend" / "src" / "views" / "WorkspaceManage.vue"
FS_ENDPOINT = ROOT / "app" / "api" / "v1" / "endpoints" / "fs.py"


def _read(path: Path) -> str:
    return path.read_text(encoding="utf-8")


def test_page_mounts_into_private_workspace():
    text = _read(PAGE)
    assert 'void load("", { preferUserRoot: true })' in text, "首屏必须声明 preferUserRoot"
    assert 'if (options.preferUserRoot) params.prefer = "user_root"' in text


def test_page_keeps_public_resources_entry():
    text = _read(PAGE)
    assert '{ key: "public", label: "公共资源", path: "" }' in text, "公共资源入口不能少"
    # 空 path 是虚拟根的合法目标：navigate 不能再把空串当无效输入
    assert "if (path === null || path === undefined) return" in text


def test_virtual_root_stays_read_only_in_page():
    text = _read(PAGE)
    assert 'if (isVirtualRoot.value) return [{ label: "公共资源", path: "" }]' in text
    assert "&& writable.value" in text, "新建/上传必须看后端下发的 writable"
    assert "!isVirtualRoot.value && !SYSTEM_PROTECTED.has(item.name)" in text
    assert text.count('v-if="!isVirtualRoot"') == 2, "列表与卡片视图的行操作都要在虚拟根隐藏"


def test_backend_supports_prefer_user_root():
    text = _read(FS_ENDPOINT)
    assert 'prefer: Optional[Literal["user_root"]]' in text
    assert 'if prefer == "user_root" and is_fs_virtual_root(path, base_dir):' in text
    assert "preferred_root = get_user_private_workspace_root(user_info)" in text
