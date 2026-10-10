"""回归：跨 event loop 的全局单例污染。

背景（2026-10 定位）：``no_infrastructure`` 标记的异步测试会跳过基础设施夹具，但它们
仍可能执行真实业务代码路径，从而惰性创建全局 Redis / HTTP 客户端——这些连接绑定当轮
event loop。若不清理，连接会带着「已关闭的 loop」进入后继测试，表现为::

    RuntimeError: Event loop is closed
    RuntimeError: Task ... got Future ... attached to a different loop

具体触发链：``test_workspace_prompt.py`` 整文件标了 ``no_infrastructure``，其中
``test_append_workspace_prompt_when_file_tools_and_conversation`` 会走真实代码路径并
惰性初始化 Redis；该文件不清理全局客户端，于是
``test_rag_executor.py``（它 mock 了 ``init_redis`` / ``close_redis`` 所以不会重建，
却没有 mock ``get_redis``）拿到了绑定已关闭 loop 的连接。

本用例用子进程真实复现「污染源 + 受害者」的组合，锁住测试隔离性。
"""

import subprocess
import sys
from pathlib import Path

import pytest

pytestmark = pytest.mark.no_infrastructure

REPO_ROOT = Path(__file__).resolve().parents[3]

POLLUTER = (
    "tests/ai/runtime/test_workspace_prompt.py"
    "::test_append_workspace_prompt_when_file_tools_and_conversation"
)
VICTIM = "tests/ai/executors/test_rag_executor.py::test_rag_executor_success"


def test_no_infrastructure_async_test_does_not_leak_loop_bound_redis():
    proc = subprocess.run(
        [
            sys.executable,
            "-m",
            "pytest",
            POLLUTER,
            VICTIM,
            "-q",
            "--tb=no",
            "-p",
            "no:cacheprovider",
        ],
        capture_output=True,
        text=True,
        cwd=REPO_ROOT,
    )

    assert proc.returncode == 0, (
        "检测到跨 event loop 污染：no_infrastructure 的异步测试泄漏了绑定旧 loop 的"
        "全局连接，导致后继测试失败。\n\n"
        f"{proc.stdout[-3000:]}"
    )
