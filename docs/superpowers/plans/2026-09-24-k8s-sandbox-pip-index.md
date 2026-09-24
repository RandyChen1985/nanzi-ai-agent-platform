# K8s 沙箱预置镜像 pip 源支持 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 让 `k8s_deploy/build-k8s-sandbox-image.sh` 支持为镜像内 `uv pip install` 指定 pip 源，默认清华镜像、可用 `--pip-index` 覆盖回官方。

**Architecture:** 在构建脚本的参数层新增 `PIP_INDEX`（优先级：`--pip-index` > `PYPI_INDEX_URL` > 内置清华默认），经白名单校验后展开进 heredoc 生成的 Dockerfile，为两处 `uv pip install` 追加 `--default-index`，http 源额外追加 `--allow-insecure-host`。不触碰任何镜像 tag 计算逻辑。

**Tech Stack:** Bash 4+（`set -euo pipefail`）、uv（`--default-index` / `--allow-insecure-host`）、pytest + subprocess 契约测试、Markdown 文档。

---

## 文件结构

- Modify: `k8s_deploy/build-k8s-sandbox-image.sh`：新增 `--pip-index`、`PYPI_INDEX_URL` 默认值、`validate_pip_index` 校验、Dockerfile 注入、帮助与日志展示。这是本次唯一的功能改动文件。
- Create: `tests/test_k8s_sandbox_image_pip_index.py`：pip 源行为的契约与端到端（`--dry-run`）测试，独立成文件便于单独运行。
- Modify: `k8s_deploy/README.md`：参数说明与「默认已改为清华源、如何还原官方」提示。
- Modify: `sandbox/k8s/README.md`：常用命令示例补一条 `--pip-index`。
- Modify: `tests/CHECKLIST.md`：登记本次测试覆盖。

不改动：`sandbox/docker/*`、`app/services/ai/runtime/agentscope/docker_*.py`、
`app/services/ai/runtime/agentscope/docker_template_patch.py`、`docker/Dockerfile`。

---

## Task 1: 用失败测试锁定 pip 源行为

**Files:**

- Create: `tests/test_k8s_sandbox_image_pip_index.py`

- [ ] **Step 1: 写测试文件**

```python
"""K8s 沙箱预置镜像构建脚本的 pip 源契约与端到端测试。

覆盖：内置默认清华源、PYPI_INDEX_URL 覆盖、--pip-index 优先级、
http 源自动放行、非法值拒绝、文档同步登记。
"""

from __future__ import annotations

import os
import subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
K8S_DIR = ROOT / "k8s_deploy"
BUILD_SCRIPT = K8S_DIR / "build-k8s-sandbox-image.sh"
DEFAULT_PIP_INDEX = "https://pypi.tuna.tsinghua.edu.cn/simple"
TSINGHUA_HOST = "pypi.tuna.tsinghua.edu.cn"
MIRROR_URL = "https://mirror.example.com/pypi/simple"
OFFICIAL_URL = "https://pypi.org/simple"
INTERNAL_HTTP_URL = "http://nexus.internal:8081/repository/pypi/simple"


def _run_dry_run(*args: str, env_extra: dict[str, str] | None = None) -> subprocess.CompletedProcess:
    """执行 --dry-run，隔离 PYPI_INDEX_URL 干扰并只注入用例需要的环境变量。"""
    env = dict(os.environ)
    env.pop("PYPI_INDEX_URL", None)
    if env_extra:
        env.update(env_extra)
    return subprocess.run(
        ["bash", str(BUILD_SCRIPT), "--dry-run", *args],
        capture_output=True,
        text=True,
        env=env,
        cwd=str(K8S_DIR),
        timeout=180,
    )


def test_script_exposes_pip_index_option_and_tsinghua_default():
    source = BUILD_SCRIPT.read_text(encoding="utf-8")

    assert 'DEFAULT_PIP_INDEX="https://pypi.tuna.tsinghua.edu.cn/simple"' in source
    assert 'PIP_INDEX="${PYPI_INDEX_URL:-$DEFAULT_PIP_INDEX}"' in source
    assert "--pip-index)" in source
    assert "--default-index" in source


def test_dry_run_uses_tsinghua_mirror_by_default():
    result = _run_dry_run()

    assert result.returncode == 0, result.stderr
    assert f"--default-index {DEFAULT_PIP_INDEX}" in result.stdout


def test_dry_run_honours_pypi_index_url_env():
    result = _run_dry_run(env_extra={"PYPI_INDEX_URL": MIRROR_URL})

    assert result.returncode == 0, result.stderr
    assert f"--default-index {MIRROR_URL}" in result.stdout
    assert TSINGHUA_HOST not in result.stdout


def test_dry_run_pip_index_flag_overrides_env():
    result = _run_dry_run(
        "--pip-index",
        OFFICIAL_URL,
        env_extra={"PYPI_INDEX_URL": MIRROR_URL},
    )

    assert result.returncode == 0, result.stderr
    assert f"--default-index {OFFICIAL_URL}" in result.stdout
    assert MIRROR_URL not in result.stdout


def test_dry_run_applies_index_to_both_install_steps():
    """两条 uv pip install（BASE_REQS 与 agentscope --no-deps）都必须带源。"""
    result = _run_dry_run("--pip-index", OFFICIAL_URL)

    assert result.returncode == 0, result.stderr
    assert result.stdout.count(f"--default-index {OFFICIAL_URL}") >= 2
    assert "--no-deps" in result.stdout


def test_dry_run_allows_http_index_host_automatically():
    result = _run_dry_run("--pip-index", INTERNAL_HTTP_URL)

    assert result.returncode == 0, result.stderr
    assert f"--default-index {INTERNAL_HTTP_URL}" in result.stdout
    assert "--allow-insecure-host nexus.internal:8081" in result.stdout


def test_https_index_does_not_enable_insecure_host():
    result = _run_dry_run("--pip-index", OFFICIAL_URL)

    assert result.returncode == 0, result.stderr
    assert "--allow-insecure-host" not in result.stdout


def test_rejects_index_url_with_shell_metacharacters():
    result = _run_dry_run("--pip-index", "https://pypi.org/simple && echo pwned")

    assert result.returncode != 0
    combined = result.stdout + result.stderr
    assert "pip 源" in combined
    # 非法值必须在生成构建上下文之前被拦下
    assert "FROM " not in result.stdout


def test_rejects_index_url_without_http_scheme():
    result = _run_dry_run("--pip-index", "pypi.tuna.tsinghua.edu.cn/simple")

    assert result.returncode != 0
    assert "http:// 或 https://" in result.stdout + result.stderr


def test_help_and_docs_document_pip_index():
    help_result = subprocess.run(
        ["bash", str(BUILD_SCRIPT), "--help"],
        capture_output=True,
        text=True,
        cwd=str(K8S_DIR),
        timeout=60,
    )
    assert help_result.returncode == 0
    assert "--pip-index" in help_result.stdout

    k8s_readme = (K8S_DIR / "README.md").read_text(encoding="utf-8")
    assert "--pip-index" in k8s_readme
    assert "pypi.org/simple" in k8s_readme

    sandbox_readme = (ROOT / "sandbox" / "k8s" / "README.md").read_text(encoding="utf-8")
    assert "--pip-index" in sandbox_readme
```

- [ ] **Step 2: 运行测试确认全部失败**

Run: `.venv/bin/python -m pytest tests/test_k8s_sandbox_image_pip_index.py -v`

Expected: FAIL。静态用例报 `DEFAULT_PIP_INDEX` 断言失败；dry-run 用例报
`--default-index https://pypi.tuna.tsinghua.edu.cn/simple` 不在输出中；
`--pip-index` 相关用例因「未知参数: --pip-index」返回码非 0。

---

## Task 2: 脚本新增 pip 源参数与校验

**Files:**

- Modify: `k8s_deploy/build-k8s-sandbox-image.sh`（文件头注释、常量区、变量区、参数解析）

- [ ] **Step 1: 文件头用法注释补一条示例**

把第 21 行 `--proxy` 示例之后补一行：

```bash
#   ./build-k8s-sandbox-image.sh --pip-index https://pypi.org/simple     # 覆盖镜像内 pip 源（默认清华镜像）
```

- [ ] **Step 2: 常量区声明默认源**

在 `BASE_REQS=(...)` 之后新增：

```bash
# 镜像内 pip 源：默认清华镜像（与 dev.sh 的 PYPI_INDEX_URL 约定保持一致）
DEFAULT_PIP_INDEX="https://pypi.tuna.tsinghua.edu.cn/simple"
```

- [ ] **Step 3: 变量区声明运行期变量**

在 `LIST_MODE=false` 之后新增：

```bash
PIP_INDEX="${PYPI_INDEX_URL:-$DEFAULT_PIP_INDEX}"
PIP_INDEX_SCHEME=""
PIP_INDEX_HOST=""
PIP_INSECURE_ARG=""
```

- [ ] **Step 4: 新增校验函数**

在变量区之后、`resolve_node_container_tool` 之前新增：

```bash
# ---- pip 源校验与参数拼装 ----
# PIP_INDEX 会被展开进 Dockerfile 文本，必须先白名单校验，防止命令注入。
validate_pip_index() {
  local url="$1"

  if [ -z "$url" ]; then
    log_error "pip 源不能为空。请传 --pip-index <URL>，例如 https://pypi.org/simple"
    exit 1
  fi

  case "$url" in
    http://*|https://*) ;;
    *)
      log_error "pip 源必须以 http:// 或 https:// 开头：${url}"
      exit 1
      ;;
  esac

  # 单独拦换行：grep 按行匹配，换行本身不会命中 [:space:]
  case "$url" in
    *$'\n'*|*$'\r'*)
      log_error "pip 源不得包含换行符：${url}"
      exit 1
      ;;
  esac

  if printf '%s' "$url" | grep -q "[[:space:]\"'\`\$;\\\\&|<>(){}]"; then
    log_error "pip 源包含非法字符（不得含空白、引号、&、;、| 等）：${url}"
    exit 1
  fi

  PIP_INDEX_SCHEME="$(printf '%s' "$url" | sed -E 's#^(https?)://.*#\1#')"
  PIP_INDEX_HOST="$(printf '%s' "$url" | sed -E 's#^https?://([^/]+).*#\1#')"

  if [ "$PIP_INDEX_SCHEME" = "http" ]; then
    # uv 对 http 源要求显式放行（pip 对应参数是 --trusted-host）
    PIP_INSECURE_ARG="--allow-insecure-host ${PIP_INDEX_HOST}"
  fi
}
```

- [ ] **Step 5: 参数解析新增 `--pip-index`**

在 `while` 循环的 `--proxy` 分支之后新增：

```bash
    --pip-index)  PIP_INDEX="$2"; shift 2 ;;
```

- [ ] **Step 6: 在参数解析后执行校验**

在 `done`（`while` 循环结束）之后、`if [ "$LIST_MODE" = "true" ]; then` 之前新增：

```bash
# --list / --sync-template 不构建镜像，无需校验 pip 源
if [ "$LIST_MODE" != "true" ] && [ "$SYNC_TEMPLATE" != "true" ]; then
  validate_pip_index "$PIP_INDEX"
fi
```

- [ ] **Step 7: 运行静态与校验类测试**

Run: `.venv/bin/python -m pytest tests/test_k8s_sandbox_image_pip_index.py -v -k "exposes or rejects"`

Expected: `test_script_exposes_pip_index_option_and_tsinghua_default`、
`test_rejects_index_url_with_shell_metacharacters`、
`test_rejects_index_url_without_http_scheme` PASS；dry-run Dockerfile 类用例仍 FAIL。

---

## Task 3: Dockerfile 注入索引参数并展示生效值

**Files:**

- Modify: `k8s_deploy/build-k8s-sandbox-image.sh`（`usage()`、交互摘要、构建日志、heredoc Dockerfile、dry-run 分支）

- [ ] **Step 1: `usage()` 补参数说明**

在 `--proxy` 那一行 `printf` 之后新增：

```bash
  printf "  %b--pip-index%b %b<url>%b     镜像内依赖安装使用的 pip 源，默认 %b%s%b\n" "${C_CYAN}" "${C_RESET}" "${C_YELLOW}" "${C_RESET}" "${C_BOLD}" "$DEFAULT_PIP_INDEX" "${C_RESET}"
  printf "                            （也可用环境变量 %bPYPI_INDEX_URL%b 提供；还原官方源传 %bhttps://pypi.org/simple%b）\n" "${C_CYAN}" "${C_RESET}" "${C_CYAN}" "${C_RESET}"
```

- [ ] **Step 2: 无参数交互摘要展示 pip 源**

在 `平台配置路径` 那行 `printf` 之后新增：

```bash
  printf "  • Pip 源:       %b%s%b\n" "${C_CYAN}" "$PIP_INDEX" "${C_RESET}"
```

- [ ] **Step 3: Dockerfile 两处安装命令注入索引**

把 heredoc 中原来的第 419-423 行：

```dockerfile
RUN uv venv $GATEWAY_VENV \\
 && uv pip install --python $GATEWAY_VENV/bin/python \\
      "mcp<2.0.0" uvicorn fastapi httpx docstring_parser jinja2 aiofiles tree_sitter tree_sitter_bash python-frontmatter \\
 && uv pip install --python $GATEWAY_VENV/bin/python --no-deps "$AP" \\
 && $GATEWAY_VENV/bin/python -c "import docstring_parser; import agentscope.mcp; import agentscope.tool"
```

替换为：

```dockerfile
RUN uv venv $GATEWAY_VENV \\
 && uv pip install --python $GATEWAY_VENV/bin/python \\
      --default-index $PIP_INDEX $PIP_INSECURE_ARG \\
      "mcp<2.0.0" uvicorn fastapi httpx docstring_parser jinja2 aiofiles tree_sitter tree_sitter_bash python-frontmatter \\
 && uv pip install --python $GATEWAY_VENV/bin/python --no-deps \\
      --default-index $PIP_INDEX $PIP_INSECURE_ARG "$AP" \\
 && $GATEWAY_VENV/bin/python -c "import docstring_parser; import agentscope.mcp; import agentscope.tool"
```

（`PIP_INSECURE_ARG` 在 https 源下为空串，展开后只多一个空格，不影响 shell 解析。）

- [ ] **Step 4: dry-run 与正式构建日志展示 pip 源**

在 `if [ "$DRY_RUN" = "true" ]; then` 之后紧接新增：

```bash
  log_info "镜像内 pip 源：$PIP_INDEX"
```

在 `log_info "开始构建 K8s 沙箱网关预置镜像：..."` 之后紧接新增：

```bash
log_info "镜像内 pip 源：${C_BOLD}${PIP_INDEX}${C_RESET}"
if [ -n "$PIP_INSECURE_ARG" ]; then
  log_info "检测到 http 源，已自动放行：--allow-insecure-host ${PIP_INDEX_HOST}"
fi
```

- [ ] **Step 5: 运行全部 pip 源测试**

Run: `.venv/bin/python -m pytest tests/test_k8s_sandbox_image_pip_index.py -v`

Expected: 除 `test_help_and_docs_document_pip_index`（文档尚未更新）外全部 PASS。

- [ ] **Step 6: 手工核对 dry-run 输出**

Run: `bash k8s_deploy/build-k8s-sandbox-image.sh --dry-run --pip-index http://nexus.internal:8081/repository/pypi/simple`

Expected: 打印的 Dockerfile 中两处 `uv pip install` 均含
`--default-index http://nexus.internal:8081/repository/pypi/simple --allow-insecure-host nexus.internal:8081`。

---

## Task 4: 文档同步

**Files:**

- Modify: `k8s_deploy/README.md:149` 与 `:214`（前置条件、常用参数）
- Modify: `sandbox/k8s/README.md:99` 附近的命令示例
- Modify: `tests/CHECKLIST.md`

- [ ] **Step 1: `k8s_deploy/README.md` 前置条件补充默认源说明**

把第 149 行的 PyPI 前置条件条目改为：

```
- 构建机可访问 PyPI（拉取 `mcp/uvicorn/fastapi/httpx` 网关基础依赖与 agentscope 工具链依赖 `docstring_parser/jinja2/aiofiles/tree_sitter/tree_sitter_bash/python-frontmatter`，清单见 `build-k8s-sandbox-image.sh` 的 `BASE_REQS`）。镜像内依赖安装默认使用清华镜像 `https://pypi.tuna.tsinghua.edu.cn/simple`，需要官方源时传 `--pip-index https://pypi.org/simple`；网络受限请加 `--proxy`；
```

- [ ] **Step 2: `k8s_deploy/README.md` 常用参数补充 `--pip-index`**

把「常用参数」段落中的参数清单补上 `--pip-index <URL>`（默认清华镜像，可用
`PYPI_INDEX_URL` 环境变量提供，http 源会自动加 `--allow-insecure-host`）。

- [ ] **Step 3: `sandbox/k8s/README.md` 示例补充**

在第 99 行 `--proxy` 示例之后新增：

```bash
# 指定镜像内依赖安装使用的 pip 源（默认清华镜像；还原官方源传 https://pypi.org/simple）
./sandbox/k8s/build-k8s-sandbox-image.sh --pip-index https://pypi.org/simple
```

- [ ] **Step 4: `tests/CHECKLIST.md` 登记覆盖**

在表格末尾新增一行，沿用现有列（功能/变更文件/说明/验证结果）：

```
| K8s 沙箱预置镜像构建脚本 pip 源支持 (`--pip-index`, 默认清华镜像, http 源自动 allow-insecure-host) | `k8s_deploy/build-k8s-sandbox-image.sh`, `k8s_deploy/README.md`, `sandbox/k8s/README.md`, `tests/test_k8s_sandbox_image_pip_index.py`, `tests/CHECKLIST.md` | 构建脚本新增 `PIP_INDEX`（优先级 `--pip-index` > `PYPI_INDEX_URL` > 清华默认），白名单校验后展开进 Dockerfile，为 `BASE_REQS` 与 `agentscope --no-deps` 两处 `uv pip install` 追加 `--default-index`，http 源自动追加 `--allow-insecure-host`；help/交互摘要/构建日志均展示生效源；不触碰 Docker 沙箱 tag 计算 | ✅ 契约与 dry-run 测试全绿 |
```

- [ ] **Step 5: 运行文档契约测试**

Run: `.venv/bin/python -m pytest tests/test_k8s_sandbox_image_pip_index.py tests/test_k8s_deploy_docs_contract.py -v`

Expected: 全部 PASS。

---

## Task 5: 全量回归

**Files:** 无新增。

- [ ] **Step 1: 运行相关测试集合**

Run: `.venv/bin/python -m pytest tests/test_k8s_sandbox_image_pip_index.py tests/test_k8s_deploy_docs_contract.py tests/ai/runtime/test_docker_prebuild_cli.py tests/ai/runtime/test_docker_template_patch.py -v`

Expected: 全部 PASS，且 Docker 沙箱相关测试未受影响。

- [ ] **Step 2: 语法与 shellcheck 检查**

Run: `bash -n k8s_deploy/build-k8s-sandbox-image.sh && echo SYNTAX_OK`

Expected: 输出 `SYNTAX_OK`。

- [ ] **Step 3: 确认未误改范围外文件**

Run: `git status --short`

Expected: 仅出现 `k8s_deploy/build-k8s-sandbox-image.sh`、`k8s_deploy/README.md`、
`sandbox/k8s/README.md`、`tests/CHECKLIST.md`、`tests/test_k8s_sandbox_image_pip_index.py`
与两份 spec/plan 文档。**不执行 git commit**（按项目约定由用户决定提交）。

---

## Self-Review

- **Spec 覆盖**：目标 1→Task 2/3；目标 2→Task 2 Step 2/3；目标 3→Task 3（`--pip-index` 传官方 URL）；目标 4→Task 3 Step 1/2/4；目标 5→Task 2 Step 4/6；目标 6→Task 2 Step 4 + Task 3 Step 3；文档→Task 4；测试→Task 1 与 Task 5。无遗漏。
- **占位符**：无 TBD/TODO，每个改动步骤均给出完整代码或完整替换文本。
- **命名一致性**：`PIP_INDEX`、`PIP_INDEX_HOST`、`PIP_INDEX_SCHEME`、`PIP_INSECURE_ARG`、`DEFAULT_PIP_INDEX`、`validate_pip_index` 在 Task 1（测试断言）与 Task 2/3（实现）中完全一致。
