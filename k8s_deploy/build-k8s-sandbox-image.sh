#!/usr/bin/env bash
# ==============================================================================
# NanZi AI 开源智能体平台 · K8s 沙箱网关预置镜像构建脚本
# ==============================================================================
# 背景：
#   AgentScope K8sWorkspace 网关环境位于 Pod 内 /root/.agentscope（临时写层），
#   每次新 Pod 冷启动都要执行 bootstrap（apt + uv + venv + 安装依赖）——这是 K8s
#   沙箱比 Docker 冷启动慢的根本原因。且只要 /root/.agentscope/_mcp_gateway_app.py
#   存在，AgentScope 会整体跳过 bootstrap（含 Docker 镜像构建也走此快路径）。
#
#   本脚本构建一个“预置镜像”：把网关 venv（mcp/fastapi/uvicorn/httpx +
#   agentscope 工具链核心依赖）与 gateway 脚本模板直接打进镜像。配置
#   sandbox_k8s_image 指向该镜像后，新 Pod 起来直接可用，冷启动从数十秒降到秒级。
#   （agentscope 官方 _GATEWAY_BASE_REQUIREMENTS 遗漏工具链依赖，缺失会报
#   "HTTP 500: No module named 'xxx'"；补充清单与原因见 BASE_REQS 注释。）
#
# 用法（在可访问 Docker daemon 的构建机/节点执行）：
#   ./build-k8s-sandbox-image.sh                          # 默认 python:3.11-slim -> nanzi-sandbox-k8s:latest
#   ./build-k8s-sandbox-image.sh --version 1.0.0          # 指定产物版本 tag
#   ./build-k8s-sandbox-image.sh --base-image python:3.11-slim
#   ./build-k8s-sandbox-image.sh --proxy http://127.0.0.1:7890   # 构建机走代理
#   ./build-k8s-sandbox-image.sh --dry-run                # 只生成 Dockerfile/命令，不实际构建
#   ./build-k8s-sandbox-image.sh --no-import              # 构建+save 后不自动导入，打印导入命令
#   ./build-k8s-sandbox-image.sh --sync-template          # 从本机 agentscope 刷新 gateway 模板副本
# ==============================================================================
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
CONTEXT_DIR="$SCRIPT_DIR/sandbox-image"
TEMPLATE_FILE="$CONTEXT_DIR/_mcp_gateway_app.py"

# 颜色（POSIX 兼容，非 TTY 自动空）
if [ -t 1 ]; then
  C_GREEN='\033[32m'; C_CYAN='\033[36m'; C_YELLOW='\033[33m'
  C_RED='\033[31m'; C_BOLD='\033[1m'; C_RESET='\033[0m'
else
  C_GREEN=''; C_CYAN=''; C_YELLOW=''; C_RED=''; C_BOLD=''; C_RESET=''
fi

log_info()   { printf "%bℹ%b  %b\n" "${C_CYAN}" "${C_RESET}" "$*"; }
log_success(){ printf "%b✔%b  %b\n" "${C_GREEN}" "${C_RESET}" "$*"; }
log_warn()   { printf "%b⚠%b  %b\n" "${C_YELLOW}" "${C_RESET}" "$*"; }
log_error()  { printf "%b✖%b  %b\n" "${C_RED}" "${C_RESET}" "$*"; }

# ---- 常量（与 agentscope workspace._k8s/_utils 布局严格一致）----
GATEWAY_HOME="/root/.agentscope"
GATEWAY_VENV="$GATEWAY_HOME/.venv"
GATEWAY_SCRIPT_NAME="_mcp_gateway_app.py"
# agentscope.workspace._utils._GATEWAY_BASE_REQUIREMENTS + agentscope(--no-deps)
# 额外补充 agentscope 核心依赖（官方 _GATEWAY_BASE_REQUIREMENTS 清单遗漏）：
#   gateway 加载/调用 MCP 与 Bash 工具时会全量 import agentscope.tool
#   （tool/_types → _utils 需 docstring_parser；_toolkit 需 jinja2；_builtin 需
#   aiofiles/tree_sitter/tree_sitter_bash/python-frontmatter）。缺失会报
#   "HTTP 500: No module named 'xxx'"。以上为实测补全集（干净 venv 迭代验证到
#   import agentscope.mcp + agentscope.tool 全部通过）。
BASE_REQS=("mcp<2.0.0" "uvicorn" "fastapi" "httpx" "docstring_parser" "jinja2" "aiofiles" "tree_sitter" "tree_sitter_bash" "python-frontmatter")

# ---- 参数 ----
BASE_IMAGE="python:3.11-slim"
IMAGE_NAME="nanzi-sandbox-k8s"
IMAGE_TAG="latest"
PROXY_URL=""
DO_IMPORT=true
DRY_RUN=false
SYNC_TEMPLATE=false
AGENTSCOPE_VERSION=""

usage() {
  cat <<'EOF'
用法: ./build-k8s-sandbox-image.sh [选项]

构建 K8s 沙箱“网关预置”镜像（新 Pod 冷启动跳过 AgentScope bootstrap）。

选项:
  --base-image <img>      基础镜像，默认 python:3.11-slim
  --image-name <name>     产物镜像名，默认 nanzi-sandbox-k8s
  --version <ver>         产物 Tag，默认 latest
  --proxy <url>           构建网络代理，如 http://127.0.0.1:7890
  --agentscope-version    覆盖安装的 agentscope 版本（默认跟随本机平台 agentscope，无则最新）
  --no-import             构建+save 后不自动导入节点（打印导入命令）
  --dry-run               只生成 Dockerfile 与命令清单，不实际构建/导入
  --sync-template         从本机 agentscope 刷新 sandbox-image/_mcp_gateway_app.py
  -h, --help              帮助
EOF
}

while [ $# -gt 0 ]; do
  case "$1" in
    --base-image) BASE_IMAGE="$2"; shift 2 ;;
    --image-name) IMAGE_NAME="$2"; shift 2 ;;
    --version)    IMAGE_TAG="$2"; shift 2 ;;
    --proxy)      PROXY_URL="$2"; shift 2 ;;
    --agentscope-version) AGENTSCOPE_VERSION="$2"; shift 2 ;;
    --no-import)  DO_IMPORT=false; shift ;;
    --dry-run)    DRY_RUN=true; shift ;;
    --sync-template) SYNC_TEMPLATE=true; shift ;;
    -h|--help)    usage; exit 0 ;;
    *) log_error "未知参数: $1（-h 查看帮助）"; exit 1 ;;
  esac
done

if [ "$SYNC_TEMPLATE" = "true" ]; then
  PY="${PYTHON:-}"
  if [ -z "$PY" ] && [ -x "$SCRIPT_DIR/../.venv/bin/python" ]; then
    PY="$SCRIPT_DIR/../.venv/bin/python"
  fi
  PY="${PY:-python3}"
  if ! "$PY" -c "import agentscope.workspace._mcp_gateway._mcp_gateway_app" 2>/dev/null; then
    log_error "无法在 [${PY}] import agentscope（请使用平台 venv：$SCRIPT_DIR/../.venv/bin/python，或 PYTHON=...）。"
    exit 1
  fi
  mkdir -p "$CONTEXT_DIR"
  "$PY" - <<PY
import agentscope.workspace._mcp_gateway._mcp_gateway_app as _src
import pathlib
src = pathlib.Path(_src.__file__)
dst = pathlib.Path("$TEMPLATE_FILE")
dst.write_bytes(src.read_bytes())
print(f"synced -> {dst}")
PY
  log_success "已从 agentscope 刷新模板副本：$TEMPLATE_FILE"
  exit 0
fi

# 尝试读取平台 agentscope 版本（未指定时优先跟随平台版本，避免协议漂移）
if [ -z "$AGENTSCOPE_VERSION" ]; then
  if [ -x "$SCRIPT_DIR/../.venv/bin/python" ]; then
    AGENTSCOPE_VERSION="$("$SCRIPT_DIR/../.venv/bin/python" -c "import agentscope; print(getattr(agentscope,'__version__',''))" 2>/dev/null || true)"
  fi
fi
if [ -z "$AGENTSCOPE_VERSION" ]; then
  log_warn "未读取到平台 agentscope 版本（当前不在平台代码目录/无 .venv）。将安装 PyPI 最新 agentscope，"
  log_warn "可能与平台运行版本不一致（网关协议漂移风险）。建议显式指定：--agentscope-version <平台版本>。"
fi

if [ ! -f "$TEMPLATE_FILE" ]; then
  log_warn "缺少 gateway 模板副本：$TEMPLATE_FILE"
  log_info "请先在平台代码目录执行：${0} --sync-template（需要可 import agentscope 的 Python）"
  exit 1
fi

FULL_IMAGE="$IMAGE_NAME:$IMAGE_TAG"
BUILD_DIR="$(mktemp -d "${TMPDIR:-/tmp}/k8s-sandbox-img.XXXXXX")"
trap 'rm -rf "$BUILD_DIR"' EXIT
cp "$TEMPLATE_FILE" "$BUILD_DIR/_mcp_gateway_app.py"

# ---- 生成 Dockerfile ----
UV_INSTALL='curl -LsSf https://astral.sh/uv/install.sh | env UV_INSTALL_DIR=/usr/local/bin INSTALLER_NO_MODIFY_PATH=1 sh'
if [ -n "$PROXY_URL" ]; then
  UV_INSTALL="curl -x $PROXY_URL -LsSf https://astral.sh/uv/install.sh | env UV_INSTALL_DIR=/usr/local/bin INSTALLER_NO_MODIFY_PATH=1 sh"
fi

AP="agentscope"
if [ -n "$AGENTSCOPE_VERSION" ]; then
  AP="agentscope==$AGENTSCOPE_VERSION"
fi
export AP

cat > "$BUILD_DIR/Dockerfile" <<EOF
FROM $BASE_IMAGE

# 与 AgentScope K8s bootstrap 相同的系统依赖（curl/ca-certificates 供 uv 安装，ripgrep 供内置 Grep）
RUN apt-get update -qq \\
 && apt-get install -y --no-install-recommends curl ca-certificates ripgrep \\
 && rm -rf /var/lib/apt/lists/*

# uv（放入 PATH）
RUN $UV_INSTALL

# venv 已存在时允许幂等 clear（兜底，正常预置后不再重复创建）
ENV UV_VENV_CLEAR=1

# 预置网关 venv（agentscope _GATEWAY_BASE_REQUIREMENTS + 工具链所需核心依赖，见 BASE_REQS 注释）
RUN uv venv $GATEWAY_VENV \\
 && uv pip install --python $GATEWAY_VENV/bin/python \\
      "mcp<2.0.0" uvicorn fastapi httpx docstring_parser jinja2 aiofiles tree_sitter tree_sitter_bash python-frontmatter \\
 && uv pip install --python $GATEWAY_VENV/bin/python --no-deps "$AP" \\
 && $GATEWAY_VENV/bin/python -c "import docstring_parser; import agentscope.mcp; import agentscope.tool"

# 预置 gateway 脚本 → AgentScope 判定已初始化，新 Pod 冷启动跳过整个 bootstrap
COPY _mcp_gateway_app.py $GATEWAY_HOME/_mcp_gateway_app.py
EOF

if [ "$DRY_RUN" = "true" ]; then
  log_info "【演练模式】已生成构建上下文：$BUILD_DIR"
  log_info "Dockerfile:"
  sed 's/^/    /' "$BUILD_DIR/Dockerfile"
  log_info "接下来会执行的命令："
  printf "  %bdocker build -t %s %s%b\n" "${C_CYAN}" "$FULL_IMAGE" "$BUILD_DIR" "${C_RESET}"
  printf "  %bdocker save %s -o nanzi-sandbox-k8s_%s.tar%s\n" "${C_CYAN}" "$FULL_IMAGE" "$IMAGE_TAG" "${C_RESET}"
  if [ "$DO_IMPORT" = "true" ]; then
    printf "  %bctr -n k8s.io images import nanzi-sandbox-k8s_%s.tar   # 非 K3s（普通 containerd）%s\n" "${C_CYAN}" "$IMAGE_TAG" "${C_RESET}"
    printf "  %bk3s ctr images import nanzi-sandbox-k8s_%s.tar          # K3s%s\n" "${C_CYAN}" "$IMAGE_TAG" "${C_RESET}"
  fi
  log_success "演练完成，未实际构建/导入。"
  exit 0
fi

if ! command -v docker >/dev/null 2>&1; then
  log_error "未找到 docker 命令。本脚本需要在能访问 Docker daemon 的构建机/节点执行。"
  exit 1
fi

# 防误用：若当前身处容器 / K8s Pod 内（例如误在 NanZi 平台 Pod 里执行），提前提示
if [ -f "/.dockerenv" ] || [ -n "${KUBERNETES_SERVICE_HOST:-}" ]; then
  log_error "检测到当前环境可能是容器 / K8s Pod（存在 /.dockerenv 或 KUBERNETES_SERVICE_HOST）。"
  log_warn "本脚本需要在【能访问 Docker daemon 的节点/构建机（宿主机）】执行，不要在 NanZi 平台 Pod 内构建。"
  log_info "若节点没有 Docker：请在任意有 Docker 的开发机执行本脚本得到 <tar>，再把 tar 拷到节点用 ./install.sh import <tar> 导入。"
  exit 1
fi

log_info "开始构建 K8s 沙箱网关预置镜像：${C_BOLD}${FULL_IMAGE}${C_RESET}（基础镜像 ${BASE_IMAGE}）"
docker build -t "$FULL_IMAGE" "$BUILD_DIR"
log_success "镜像构建完成：$FULL_IMAGE"

TAR_FILE="nanzi-sandbox-k8s_${IMAGE_TAG}.tar"
log_info "导出镜像为 tar（文件式，非管道）..."
docker save "$FULL_IMAGE" -o "$TAR_FILE"
log_success "已导出：$TAR_FILE"

if [ "$DO_IMPORT" = "true" ]; then
  IMPORTED=false
  # 运行时选择：K3s（命令或 socket）优先——K3s 自带 containerd 是 kubelet 读取的那套；
  # 没有 K3s 时用系统 containerd（普通 K8s 节点）。
  if command -v k3s >/dev/null 2>&1; then
    log_info "正在导入 K3s containerd（k3s ctr）..."
    if sudo -n k3s ctr images import "$TAR_FILE" 2>/dev/null || k3s ctr images import "$TAR_FILE" 2>/dev/null; then
      IMPORTED=true
    fi
  elif [ -S "/run/k3s/containerd/containerd.sock" ]; then
    log_info "正在导入 K3s containerd（ctr -a socket）..."
    if sudo -n ctr -a /run/k3s/containerd/containerd.sock -n k8s.io images import "$TAR_FILE" 2>/dev/null \
      || ctr -a /run/k3s/containerd/containerd.sock -n k8s.io images import "$TAR_FILE" 2>/dev/null; then
      IMPORTED=true
    fi
  elif command -v ctr >/dev/null 2>&1; then
    log_info "正在导入节点 containerd（ctr -n k8s.io）..."
    if sudo -n ctr -n k8s.io images import "$TAR_FILE" 2>/dev/null || ctr -n k8s.io images import "$TAR_FILE" 2>/dev/null; then
      IMPORTED=true
    fi
  fi
  if [ "$IMPORTED" = "true" ]; then
    log_success "已导入节点容器运行时：$FULL_IMAGE"
  else
    log_warn "自动导入未成功（可能当前主机不是节点或权限不足）。请在节点手动执行："
    printf "  %bctr -n k8s.io images import %s   # 非 K3s（普通 containerd）%b\n" "${C_CYAN}" "$TAR_FILE" "${C_RESET}"
    printf "  %bk3s ctr images import %s          # K3s%b\n" "${C_CYAN}" "$TAR_FILE" "${C_RESET}"
  fi
else
  log_info "已跳过自动导入。请将 tar 拷贝到节点后手动导入："
  printf "  %bctr -n k8s.io images import %s   # 非 K3s（普通 containerd）%b\n" "${C_CYAN}" "$TAR_FILE" "${C_RESET}"
  printf "  %bk3s ctr images import %s          # K3s%b\n" "${C_CYAN}" "$TAR_FILE" "${C_RESET}"
fi

cat <<EOF

────────────────────────────────────────────────────────────────────
✅ 构建完成。接下来让沙箱使用该预置镜像：

1. 在节点/管理端确认镜像已导入：
   crictl images | grep nanzi-sandbox-k8s   （或 ./install.sh --images nanzi-sandbox-k8s）

2. 将系统配置 sandbox_k8s_image 设为: $FULL_IMAGE
   （配置 -> 沙箱 -> sandbox_k8s_image）

3. 之后新建/重启的沙箱 Pod 将直接使用预置网关环境，冷启动大幅加速。
────────────────────────────────────────────────────────────────────
EOF
