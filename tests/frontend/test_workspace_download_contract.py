"""工作区「下载」与「预览」双入口的契约测试。

下载原先复用 ``/api/v1/chat/fs/preview``，被预览的扩展名白名单连坐：
zip 在工作区列表里可见，点下载却得到 400。这里锁定修复后的分工：
  - 画布预览继续用 resolveFsPreviewUrl（白名单语义不变）；
  - 下载改用独立的 /api/v1/chat/fs/download（不限格式）。

行为验证见 tests/frontend/workspace_download_url.mjs（Node 真跑纯函数）。
"""

import shutil
import subprocess
from pathlib import Path

import pytest


ROOT = Path(__file__).resolve().parents[2]

pytestmark = pytest.mark.no_infrastructure


def _source(path: str) -> str:
    return (ROOT / path).read_text(encoding="utf-8")


@pytest.mark.skipif(shutil.which("node") is None, reason="需要 node 才能执行纯函数行为测试")
def test_workspace_download_url_behaviour_via_node():
    script = Path("tests/frontend/workspace_download_url.mjs")
    assert script.exists(), "行为测试脚本缺失"
    proc = subprocess.run(
        ["node", "--experimental-strip-types", "--no-warnings", str(script)],
        capture_output=True,
        text=True,
    )
    assert proc.returncode == 0, proc.stdout + proc.stderr
    assert "工作区下载 URL 构造测试全部通过" in proc.stdout


def test_download_url_module_is_dependency_free():
    """零依赖模块能被 Node 直接加载；引入 axios 会让行为测试静默失去覆盖。"""
    source = _source("frontend/src/utils/workspaceDownloadUrl.ts")

    assert "from 'axios'" not in source
    assert "from '@/utils/axios'" not in source
    assert "from '@/utils/" not in source
    assert "export const WORKSPACE_DOWNLOAD_ENDPOINT = '/api/v1/chat/fs/download'" in source
    assert "export function buildWorkspaceDownloadUrl(" in source


def test_download_uses_dedicated_endpoint_while_preview_keeps_whitelist_semantics():
    source = _source("frontend/src/utils/workspaceFilePreview.ts")

    assert "import { buildWorkspaceDownloadUrl } from './workspaceDownloadUrl'" in source
    # 预览入口保持不变：仍是白名单驱动的 fs/preview
    assert "export function resolveFsPreviewUrl(" in source
    assert "/api/v1/chat/fs/preview?path=" in source

    # 下载入口不得再请求预览地址
    download_start = source.index("export async function downloadWorkspaceFile(")
    download_body = source[download_start:]
    assert "resolveFsPreviewUrl" not in download_body
    assert "buildWorkspaceDownloadUrl" in download_body
    # 出错提示不能再把 400「不支持预览」透出来误导用户
    assert "不支持预览该类型的文件" not in download_body


def test_download_reads_backend_detail_from_blob_errors():
    source = _source("frontend/src/utils/workspaceFilePreview.ts")
    download_body = source[source.index("export async function downloadWorkspaceFile("):]
    assert "readErrorDetail(err.response?.data)" in download_body
    # responseType=blob 时 data 是 Blob，直接读 .detail 永远是 undefined
    assert ".data?.detail" not in download_body

    helper = _source("frontend/src/utils/httpErrorDetail.ts")
    assert "instanceof Blob" in helper
    assert "await payload.text()" in helper


@pytest.mark.skipif(shutil.which("node") is None, reason="需要 node 才能执行 Blob 行为测试")
def test_blob_error_detail_behaviour_via_node():
    proc = subprocess.run(
        ["node", "--experimental-strip-types", "--no-warnings", "tests/frontend/http_error_detail.mjs"],
        capture_output=True,
        text=True,
    )
    assert proc.returncode == 0, proc.stdout + proc.stderr
    assert "HTTP 错误详情提取测试全部通过" in proc.stdout
