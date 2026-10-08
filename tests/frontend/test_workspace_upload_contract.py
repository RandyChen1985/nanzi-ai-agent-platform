"""工作空间上传进度与状态反馈的契约。

用户诉求：上传（尤其是大文件）不能没有任何进度反馈。
实测结论：`onUploadProgress` 的事件粒度不可依赖——5MB 文件只触发 1 次（0%→100%）、
60MB 只触发 2 次且 114ms 就结束。因此界面节奏必须由定时器驱动，并显式区分
「传输中」与「服务端写入中」，否则进度条会在 100% 处无解释地滞留。
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
def test_upload_queue_behaviour_via_node():
    """并发上限、速率窗口、取消释放槽位等时序语义只能靠运行时回放验证。"""
    script = Path("tests/frontend/workspace_upload_queue.mjs")
    assert script.exists(), "上传队列行为测试脚本缺失"
    proc = subprocess.run(
        ["node", "--experimental-strip-types", "--no-warnings", str(script)],
        capture_output=True,
        text=True,
    )
    assert proc.returncode == 0, proc.stdout + proc.stderr
    assert "工作空间上传队列行为测试全部通过" in proc.stdout


def test_queue_engine_is_dependency_free():
    """队列引擎必须保持零运行时依赖，否则 Node 无法直接加载它做行为测试。"""
    source = _source("frontend/src/utils/workspaceUploadQueue.ts")

    assert "from 'axios'" not in source
    assert "@/utils/axios" not in source
    assert ".vue" not in source
    # committing 是「服务端写入中」阶段的载体，缺了它就只能停在 100%
    for status in ("'queued'", "'transferring'", "'committing'", "'done'", "'failed'", "'canceled'"):
        assert status in source, f"上传状态机缺少 {status}"
    assert "DEFAULT_UPLOAD_CONCURRENCY = 3" in source
    assert "UPLOAD_PROGRESS_REFRESH_MS = 400" in source


def test_upload_transport_passes_progress_and_signal():
    """传输层必须接上进度回调与取消信号，否则引擎拿不到任何进度来源。"""
    source = _source("frontend/src/utils/workspaceFilePreview.ts")

    assert "onUploadProgress" in source, "上传未接进度回调"
    assert "signal" in source, "上传未接取消信号"
    assert "onCommit" in source, "传输层需在请求体发完后通知引擎切换到「服务端写入中」"
    # 大文件落盘可能远超默认 60s，传输层必须显式解除超时，否则大文件必然被中断
    assert "timeout: 0" in source, "上传仍受默认 60s 超时限制"


def test_queue_reads_stored_name_from_response():
    """落盘名由队列引擎从响应中读取并回填，UI 才能解释重名后缀。"""
    source = _source("frontend/src/utils/workspaceUploadQueue.ts")

    assert "stored_name" in source, "队列未读取服务端返回的落盘名"
    assert "storedName" in source, "队列未把落盘名落到任务上"


def test_drawer_wires_queue_and_renders_committing_state():
    drawer = _source("frontend/src/components/embed/WorkspaceBrowserDrawer.vue")

    assert "createWorkspaceUploadQueue" not in drawer, "队列实例应由 composable 持有，不宜在抽屉内直接创建"
    assert "useWorkspaceUploadQueue" in drawer, "抽屉未接上传队列"
    assert "服务端写入中" in drawer, "缺少服务端写入阶段的可见文案"
    # 批量上传不能每个文件都刷一次目录，否则会互相打断滚动位置
    assert "aggregate.active" in drawer, "未按队列活动状态收敛目录刷新"
    # 串行等待式上传必须已被队列取代
    assert "await uploadToWorkspaceDir(" not in drawer, "仍存在串行 await 上传，未接入队列"


def test_upload_queue_composable_bridges_vue_and_engine():
    source = _source("frontend/src/composables/useWorkspaceUploadQueue.ts")

    assert "createWorkspaceUploadQueue" in source
    assert "uploadToWorkspaceDir" in source
    assert "onBeforeUnmount" in source, "队列实例必须随组件卸载销毁，否则定时器与请求会泄漏"
    assert "destroy()" in source


def test_backend_streams_chunks_and_reads_limit_from_config():
    """后端必须分块落盘，并且上限来自配置而非硬编码常量。"""
    source = _source("app/api/v1/endpoints/fs.py")

    assert "UPLOAD_CHUNK_BYTES" in source, "上传未分块，大文件会造成等量内存峰值"
    assert "while True:" in source and "await file.read(UPLOAD_CHUNK_BYTES)" in source
    # 一次性读满整个文件的老实现必须消失
    assert "await file.read(MAX_UPLOAD_BYTES + 1)" not in source
    assert "WORKSPACE_UPLOAD_MAX_MB" in source, "上传上限未接入配置"
    assert "413" in source, "超限应返回 413（413 语义为载荷过大），而非笼统的 400"
    # 超限即中止并清理半截文件
    assert "os.remove(target)" in source, "超限/失败未清理半截文件"


def test_upload_limit_default_is_configurable():
    config = _source("app/core/config.py")
    assert "WORKSPACE_UPLOAD_MAX_MB: int = 200" in config, "上传上限默认值未按设计设为 200MB"
