# -*- coding: utf-8 -*-
"""Sandbox container thin MCP server (Inline).

This module builds the MCP client configuration handed to the
AgentScope :class:`DockerWorkspace` / :class:`E2BWorkspace` via their
``default_mcps`` argument.

Constraint recap (see the platform design notes):

* The platform cannot copy arbitrary modules into the runtime image —
  ``extra_pip`` only pulls real PyPI packages and the gateway app is
  owned by AgentScope. The gateway always ships ``mcp``, ``fastapi``
  and ``uvicorn`` inside the container venv.
* STDIO MCP servers are spawned by the in-container gateway as plain
  subprocesses (``command`` + ``args`` as a list — no shell involved),
  so we expose the container-side bash/file tools through an inline
  ``python -c <script>`` FastMCP stdio server. ``BaseWorkspace`` uses
  ``model_dump(mode="json")`` to push the configuration into the
  gateway, which keeps this fully deterministic with zero image
  changes.

The script is kept as a single Python ``str`` so it can be compiled
and unit-tested on the host while also being executable inside the
container by ``python``.

Interpreter resolution matters: the gateway always ships ``mcp`` inside
its own virtualenv (``/root/.agentscope/.venv``), but a stock
``python:3.11-slim`` sandbox image does NOT install ``mcp`` into the
system Python. If the MCP subprocess is spawned as plain ``python`` and
``PATH`` does not point at the gateway venv first, the ``sandbox`` MCP
registration fails with ``ModuleNotFoundError: No module named 'mcp'``
inside the gateway. Kubernetes sandboxes therefore pin the absolute
gateway-venv interpreter; Docker/E2B sandboxes keep the default
``python`` resolution.
"""

from __future__ import annotations

from typing import Any

from agentscope.mcp import MCPClient  # type: ignore[attr-defined]
from agentscope.mcp import StdioMCPConfig  # type: ignore[attr-defined]

# Name used for the injected container tool server. Stored under
# ``<workdir>/.mcp`` and exposed to the model as ``<name>::<tool>``.
CONTAINER_MCP_NAME = "sandbox"

# Working directory inside the container/sandbox. Matches
# AgentScope's ``CONTAINER_WORKDIR``.
CONTAINER_WORKDIR = "/workspace"

# Absolute interpreter of the AgentScope gateway virtualenv inside the
# sandbox container. The gateway always installs ``mcp`` (plus fastapi /
# uvicorn) there, while the stock ``python:3.11-slim`` system Python does
# not. Kubernetes sandbox pods must spawn the inline ``sandbox`` MCP server
# with this interpreter so ``from mcp.server.fastmcp import FastMCP``
# resolves; a bare ``python`` command can silently resolve to the system
# interpreter and make the whole MCP registration fail (HTTP 500 in the
# gateway, and no usable Bash tool downstream).
K8S_GATEWAY_VENV_PYTHON = "/root/.agentscope/.venv/bin/python"

# AgentScope 网关运行时还需这些工具链核心依赖（官方 _GATEWAY_BASE_REQUIREMENTS
# 只含 mcp/uvicorn/fastapi/httpx，而 gateway 加载/调用 MCP 与 Bash 工具时会全量
# import agentscope.tool：tool/_types→_utils 需 docstring_parser；_toolkit 需
# jinja2；_builtin 需 aiofiles/tree_sitter/tree_sitter_bash/python-frontmatter）。
# 缺失表现为沙箱 Bash 报 "HTTP 500: No module named 'xxx'"。
# 清单用于两处：① K8sWorkspace 的 ``extra_pip`` —— K8s 冷启动 bootstrap 的
# ``uv pip install <_GATEWAY_BASE_REQUIREMENTS + extra_pip>`` 一并安装（未配
# 预置镜像的 Pod 也覆盖）；② k8s_deploy/build-k8s-sandbox-image.sh 的 BASE_REQS
# （预置镜像）。已按干净 venv 实测：装齐后 import agentscope.mcp + agentscope.tool
# （含 Bash/Read/Write/Edit/Glob/Grep 等内置工具）全部通过；docker 预构建镜像因
# ``uv pip install agentscope`` 不带 --no-deps 天然完整，不受影响。
K8S_GATEWAY_EXTRA_PIP: tuple[str, ...] = (
    "docstring_parser",
    "jinja2",
    "aiofiles",
    "tree_sitter",
    "tree_sitter_bash",
    "python-frontmatter",
)

# The inline FastMCP stdio server. It must be syntactically valid
# Python and use only the stdlib + ``mcp`` (already present in the
# gateway venv). ``run(transport="stdio")`` blocks serving requests.
_INLINE_SERVER = '''\
import json
import os
import subprocess

from mcp.server.fastmcp import FastMCP

_ROOT = os.environ.get("SANDBOX_WORKDIR", "/workspace")
mcp = FastMCP("sandbox")


def _abspath(path: str) -> str:
    p = os.path.abspath(os.path.join(_ROOT, os.path.expanduser(path)))
    if not p.startswith(_ROOT):
        raise ValueError("path escapes workspace: " + path)
    return p


@mcp.tool()
def bash(command: str, cwd: str = ".") -> str:
    """Run a shell command inside the sandbox and return its output.

    Args:
        command: The shell command line to execute.
        cwd: Relative directory under the configured sandbox workdir to run in.
    """
    workdir = _ROOT if cwd in ("", ".") else _abspath(cwd)
    try:
        res = subprocess.run(
            ["bash", "-lc", command],
            cwd=workdir,
            capture_output=True,
            text=True,
            timeout=600,
        )
    except subprocess.TimeoutExpired:
        return json.dumps({"ok": False, "error": "command timed out"})
    return json.dumps({
        "ok": res.returncode == 0,
        "exit_code": res.returncode,
        "stdout": res.stdout[-100000:],
        "stderr": res.stderr[-100000:],
    })


@mcp.tool()
def read(path: str, offset: int = 0, limit: int = 2000) -> str:
    """Read a text file inside the sandbox.

    Args:
        path: File path relative to the configured sandbox workdir.
        offset: Zero-based starting line.
        limit: Max number of lines to return.
    """
    p = _abspath(path)
    if not os.path.isfile(p):
        return json.dumps({"ok": False, "error": "no such file: " + path})
    try:
        with open(p, "r", encoding="utf-8", errors="replace") as fh:
            lines = fh.readlines()
    except Exception as exc:  # noqa: BLE001
        return json.dumps({"ok": False, "error": str(exc)})
    chunk = lines[offset: offset + limit]
    return json.dumps({
        "ok": True,
        "content": "".join(chunk),
        "total_lines": len(lines),
        "start_line": offset,
    })


@mcp.tool()
def write(path: str, content: str) -> str:
    """Create or overwrite a text file inside the sandbox.

    Args:
        path: File path relative to the configured sandbox workdir. Parent dirs created.
        content: Full file content to write.
    """
    p = _abspath(path)
    try:
        os.makedirs(os.path.dirname(p), exist_ok=True)
        with open(p, "w", encoding="utf-8") as fh:
            fh.write(content)
    except Exception as exc:  # noqa: BLE001
        return json.dumps({"ok": False, "error": str(exc)})
    return json.dumps({"ok": True, "path": path})


@mcp.tool()
def glob(pattern: str, cwd: str = ".") -> str:
    """List files under the configured sandbox workdir matching a glob pattern.

    Args:
        pattern: Glob pattern relative to the configured sandbox workdir, e.g. **/*.py.
        cwd: Subdirectory to search in.
    """
    import glob as _glob

    base = _ROOT if cwd in ("", ".") else _abspath(cwd)
    matches = _glob.glob(pattern, root_dir=base, recursive=True)
    return json.dumps({"ok": True, "files": sorted(matches)[:500]})


mcp.run(transport="stdio")
'''


def build_container_tool_mcp(
    workdir: str = CONTAINER_WORKDIR,
    *,
    interpreter: str = "python",
) -> MCPClient:
    """Return the stateful STDIO MCP handshake for the sandbox tools.

    The gateway (inside the container) spawns ``<interpreter> -c <script>``
    with its default inherited environment; we pin a container-side
    working directory via ``cwd`` and expose it under
    :data:`CONTAINER_MCP_NAME`. Docker sandboxes bind the user's host
    workspace to this logical path; the platform's host-side file tools
    translate it back to the real user workspace for previews and artifacts.

    ``interpreter`` defaults to ``python`` (resolved through ``PATH``),
    which is correct for Docker/E2B sandboxes whose gateway venv is already
    first on ``PATH``. Kubernetes sandbox pods must pass
    :data:`K8S_GATEWAY_VENV_PYTHON` so the MCP child process uses the venv
    that actually ships the ``mcp`` package (see module docstring).
    """
    resolved_workdir = str(workdir or CONTAINER_WORKDIR).strip()
    if not resolved_workdir.startswith("/"):
        raise ValueError("sandbox workdir must be an absolute path")
    interpreter_cmd = (interpreter or "python").strip()
    if not interpreter_cmd:
        raise ValueError("sandbox MCP interpreter must not be empty")
    return MCPClient(
        name=CONTAINER_MCP_NAME,
        is_stateful=True,
        mcp_config=StdioMCPConfig(
            command=interpreter_cmd,
            args=["-c", _INLINE_SERVER],
            cwd=resolved_workdir,
            env={"SANDBOX_WORKDIR": resolved_workdir},
        ),
    )


def container_tool_mcp_spec(workdir: str = CONTAINER_WORKDIR) -> dict[str, Any]:
    """Return the gateway-consumed JSON for the sandbox tool server."""
    return build_container_tool_mcp(workdir=workdir).model_dump(mode="json")
