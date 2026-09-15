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
AUTO_CONFIRM=false
LIST_MODE=false

# ---- 探测节点容器运行时命令（支持 K3s / 标准 containerd / crictl）----
resolve_node_container_tool() {
  local sudo_cmd=""
  if [ "$(id -u)" -ne 0 ] && command -v sudo >/dev/null 2>&1; then
    sudo_cmd="sudo"
  fi

  NODE_CTR_CMD=""
  NODE_RUNTIME_TYPE=""

  if command -v k3s >/dev/null 2>&1; then
    NODE_CTR_CMD="${sudo_cmd:+$sudo_cmd }k3s ctr"
    NODE_RUNTIME_TYPE="K3s containerd"
  elif [ -S "/run/k3s/containerd/containerd.sock" ]; then
    NODE_CTR_CMD="${sudo_cmd:+$sudo_cmd }ctr -a /run/k3s/containerd/containerd.sock -n k8s.io"
    NODE_RUNTIME_TYPE="K3s containerd (socket)"
  elif command -v ctr >/dev/null 2>&1; then
    NODE_CTR_CMD="${sudo_cmd:+$sudo_cmd }ctr -n k8s.io"
    NODE_RUNTIME_TYPE="标准 containerd (k8s.io)"
  elif command -v crictl >/dev/null 2>&1; then
    NODE_CTR_CMD="${sudo_cmd:+$sudo_cmd }crictl"
    NODE_RUNTIME_TYPE="CRI (crictl)"
  fi
}

# ---- Docker 运行环境前置预检与场景化引导 ----
check_docker_environment_k8s() {
  # 若处于演练模式、查看帮助/列表或仅同步模板，无需依赖本地 Docker
  if [ "$DRY_RUN" = "true" ] || [ "$LIST_MODE" = "true" ] || [ "$SYNC_TEMPLATE" = "true" ]; then
    return 0
  fi

  # 1. 检查是否在容器内部或 K8s Pod 内部误执行
  if [ -f "/.dockerenv" ] || [ -n "${KUBERNETES_SERVICE_HOST:-}" ]; then
    printf "\n"
    log_error "检测到当前环境可能是容器 / K8s Pod 内部（存在 /.dockerenv 或 KUBERNETES_SERVICE_HOST）！"
    log_warn "本脚本需要在【能访问 Docker 引擎的宿主机/构建机】执行，不能在 NanZi 平台 Pod 内构建。"
    printf "────────────────────────────────────────────────────────────────────\n"
    printf "%b👉 解决方案：%b\n" "${C_BOLD}" "${C_RESET}"
    printf "  请在任意拥有 Docker 的外部宿主机/开发机上执行本脚本得到 tar 包，再拷贝至 K8s 节点导入。\n"
    printf "────────────────────────────────────────────────────────────────────\n\n"
    exit 1
  fi

  # 2. 检查 docker CLI 是否已安装
  if ! command -v docker >/dev/null 2>&1; then
    printf "\n"
    log_error "未检测到 Docker 命令行工具 (docker: command not found)"
    log_warn "K8s 沙箱网关镜像构建 (docker build) 与导出 (docker save) 依赖本地 Docker 引擎。"
    printf "────────────────────────────────────────────────────────────────────\n"
    printf "%b👉 操作与排障方案建议：%b\n" "${C_BOLD}" "${C_RESET}"
    printf "  %b【方案 A：当前机器作为构建机】%b 安装并启动 Docker：\n" "${C_YELLOW}" "${C_RESET}"
    printf "    • Linux 一键安装:   %bcurl -fsSL https://get.docker.com | bash%b\n" "${C_CYAN}" "${C_RESET}"
    printf "    • Ubuntu/Debian:    %bsudo apt-get update && sudo apt-get install -y docker.io%b\n" "${C_CYAN}" "${C_RESET}"
    printf "    • CentOS/RHEL:      %bsudo yum install -y docker && sudo systemctl enable --now docker%b\n" "${C_CYAN}" "${C_RESET}"
    printf "    • macOS / Windows:  请前往官网安装 Docker Desktop: https://www.docker.com/products/docker-desktop\n"
    printf "\n"
    printf "  %b【方案 B：当前机器是纯 containerd 的 K8s 生产节点（无需在本机装 Docker）】%b\n" "${C_YELLOW}" "${C_RESET}"
    printf "    1. 在任意有 Docker 的开发机/CI 构建机上运行本脚本打包（带 --no-import 参数）：\n"
    printf "       %b./build-k8s-sandbox-image.sh --no-import%b\n" "${C_CYAN}" "${C_RESET}"
    printf "    2. 将生成的产物包 %bnanzi-sandbox-k8s_%s.tar%b 拷贝至本 K8s 节点。\n" "${C_BOLD}" "$IMAGE_TAG" "${C_RESET}"
    printf "    3. 在本节点直接执行导入（仅需 containerd/K3s，无需 Docker）：\n"
    printf "       %bk3s ctr images import nanzi-sandbox-k8s_%s.tar%b           # K3s 集群\n" "${C_CYAN}" "$IMAGE_TAG" "${C_RESET}"
    printf "       %bctr -n k8s.io images import nanzi-sandbox-k8s_%s.tar%b    # 标准 containerd\n" "${C_CYAN}" "$IMAGE_TAG" "${C_RESET}"
    printf "\n"
    printf "  %b【演练模式】%b 仅预览 Dockerfile 与执行命令清单（无需 Docker 环境）：\n" "${C_YELLOW}" "${C_RESET}"
    printf "    %b./build-k8s-sandbox-image.sh --dry-run%b\n" "${C_CYAN}" "${C_RESET}"
    printf "────────────────────────────────────────────────────────────────────\n\n"
    exit 1
  fi

  # 提取 Docker Client 基础信息
  local cli_ver docker_ctx docker_endpoint
  cli_ver="$(docker version --format '{{.Client.Version}}' 2>/dev/null || echo "未知版本")"
  docker_ctx="$(docker context show 2>/dev/null || echo "default")"
  docker_endpoint="$(docker context inspect --format '{{.Endpoints.docker.Host}}' 2>/dev/null || echo "${DOCKER_HOST:-/var/run/docker.sock}")"

  # 3. 检查 Docker Daemon 守护进程是否处于运行状态与当前用户权限
  local docker_info_raw
  if ! docker_info_raw="$(docker info --format '{{.ServerVersion}}|{{.OperatingSystem}}|{{.Architecture}}|{{.NCPU}}|{{.MemTotal}}|{{.ContainersRunning}}|{{.Images}}' 2>&1)"; then
    printf "\n"
    log_error "本地 Docker Daemon 守护进程未启动或当前用户权限不足！"
    printf "======================================================================\n"
    printf "%b🐳 Docker 客户端环境检测信息：%b\n" "${C_CYAN}" "${C_RESET}"
    printf "   • 客户端版本 (CLI):     %b%s%b\n" "${C_BOLD}" "$cli_ver" "${C_RESET}"
    printf "   • 当前上下文 (Context): %b%s%b\n" "${C_BOLD}" "$docker_ctx" "${C_RESET}"
    printf "   • 目标端点 (Endpoint):  %b%s%b\n" "${C_BOLD}" "$docker_endpoint" "${C_RESET}"
    printf "   • 服务端状态 (Server):  %b🔴 未运行 / 无法连接%b\n" "${C_RED}" "${C_RESET}"
    printf "======================================================================\n"
    printf "%b👉 排障与解决引导：%b\n" "${C_BOLD}" "${C_RESET}"
    if [[ "$docker_info_raw" =~ [Pp]ermission\ denied ]]; then
      printf "  %b【原因：权限不足】%b 当前用户没有访问 Docker Socket 的权限：\n" "${C_YELLOW}" "${C_RESET}"
      printf "    1. 将当前用户加入 docker 组: %bsudo usermod -aG docker \$USER%b\n" "${C_CYAN}" "${C_RESET}"
      printf "    2. 刷新当前 Shell 组或重新登录:  %bnewgrp docker%b\n" "${C_CYAN}" "${C_RESET}"
      printf "    3. 或临时使用 sudo 运行此脚本:    %bsudo ./build-k8s-sandbox-image.sh [选项]%b\n" "${C_CYAN}" "${C_RESET}"
    elif [[ "$docker_ctx" == "colima" ]] || [[ "$docker_endpoint" =~ colima ]]; then
      printf "  %b【原因：Colima 虚拟机未启动】%b 检测到当前 Docker Context 使用的是 Colima：\n" "${C_YELLOW}" "${C_RESET}"
      printf "    👉 请在终端执行以下命令启动 Colima 虚拟机：\n"
      printf "       %bcolima start%b\n" "${C_CYAN}" "${C_RESET}"
    elif [[ "$docker_ctx" =~ (desktop|desktop-linux) ]] || [[ "$docker_endpoint" =~ docker\.desktop ]]; then
      printf "  %b【原因：Docker Desktop 未启动】%b 检测到当前使用的是 Docker Desktop：\n" "${C_YELLOW}" "${C_RESET}"
      printf "    👉 请启动 Docker Desktop 应用程序并等待就绪（状态图标变为绿色）。\n"
    else
      printf "  %b【原因：服务未运行】%b Docker 守护进程未启动：\n" "${C_YELLOW}" "${C_RESET}"
      printf "    • Linux 系统启动服务:      %bsudo systemctl start docker && sudo systemctl enable docker%b\n" "${C_CYAN}" "${C_RESET}"
      printf "    • macOS / Windows 系统:   请启动 Docker 运行时（Docker Desktop 或 Colima）。\n"
    fi
    printf "  • 演练预览生成的 Dockerfile（无需 Docker 环境）：\n"
    printf "    %b./build-k8s-sandbox-image.sh --dry-run%b\n" "${C_CYAN}" "${C_RESET}"
    printf "────────────────────────────────────────────────────────────────────\n\n"
    exit 1
  fi

  # 4. 格式化解析并打印 Docker 运行环境明细
  local server_ver server_os server_arch server_ncpu server_mem server_running server_images
  IFS='|' read -r server_ver server_os server_arch server_ncpu server_mem server_running server_images <<< "$docker_info_raw"
  local mem_formatted="未知"
  if [ -n "$server_mem" ] && [ "$server_mem" -gt 0 ] 2>/dev/null; then
    mem_formatted="$(awk "BEGIN {printf \"%.2f GiB\", $server_mem/1024/1024/1024}" 2>/dev/null || echo "$((server_mem / 1073741824)) GiB")"
  fi

  printf "\n======================================================================\n"
  printf "%b🐳 [Docker 运行环境预检通过] 检测到可用 Docker 引擎：%b\n" "${C_GREEN}" "${C_RESET}"
  printf "   • 客户端版本 (CLI):     %b%s%b\n" "${C_BOLD}" "$cli_ver" "${C_RESET}"
  printf "   • 服务端版本 (Server):  %b%s%b (%s, %s)\n" "${C_BOLD}" "$server_ver" "${C_RESET}" "$server_os" "$server_arch"
  printf "   • 当前上下文 (Context): %b%s%b\n" "${C_BOLD}" "$docker_ctx" "${C_RESET}"
  printf "   • 守护进程端点 (Host):   %b%s%b\n" "${C_BOLD}" "$docker_endpoint" "${C_RESET}"
  printf "   • 宿主分配规格 (Specs):  %b%s 核 CPU / %s 内存%b\n" "${C_BOLD}" "$server_ncpu" "$mem_formatted" "${C_RESET}"
  printf "   • 容器/镜像状态:        运行中容器: %s / 本地镜像数: %s\n" "$server_running" "$server_images"
  printf "======================================================================\n\n"
}

run_list_sandbox_images() {
  printf "\n"
  log_info "🔍 正在检索本地 Docker 与 K8s 节点的沙箱镜像..."
  printf "\n"

  local found_any=false

  # 1. 检查本地 Docker daemon
  if command -v docker >/dev/null 2>&1 && docker info >/dev/null 2>&1; then
    printf "%b[1/2] 本地 Docker 镜像库 (docker images):%b\n" "${C_BOLD}" "${C_RESET}"
    local docker_matches
    docker_matches="$(docker images --filter "reference=*${IMAGE_NAME}*" --format "table {{.Repository}}:{{.Tag}}\t{{.ID}}\t{{.CreatedAt}}\t{{.Size}}" 2>/dev/null || true)"
    if [ -n "$docker_matches" ] && [ "$(printf "%s\n" "$docker_matches" | wc -l)" -gt 1 ]; then
      printf "%s\n\n" "$docker_matches"
      found_any=true
    else
      printf "  %b未在本地 Docker 中检索到包含 %s 的镜像%b\n\n" "${C_YELLOW}" "$IMAGE_NAME" "${C_RESET}"
    fi
  else
    printf "%b[1/2] 本地 Docker 镜像库:%b %b未检测到可用的 Docker daemon%b\n\n" "${C_BOLD}" "${C_RESET}" "${C_YELLOW}" "${C_RESET}"
  fi

  # 2. 检查 K8s 节点容器运行时（containerd / K3s / crictl）
  resolve_node_container_tool
  if [ -n "$NODE_CTR_CMD" ]; then
    printf "%b[2/2] K8s 节点容器运行时 (%s):%b\n" "${C_BOLD}" "$NODE_RUNTIME_TYPE" "${C_RESET}"
    local node_matches=""
    if [[ "$NODE_CTR_CMD" == *"crictl"* ]]; then
      node_matches="$($NODE_CTR_CMD images 2>/dev/null | grep -E "${IMAGE_NAME}" || true)"
    else
      node_matches="$($NODE_CTR_CMD images list 2>/dev/null | grep -E "${IMAGE_NAME}" || true)"
    fi

    if [ -n "$node_matches" ]; then
      printf "  %b✔ 节点已就绪沙箱镜像列表：%b\n" "${C_GREEN}" "${C_RESET}"
      printf "%s\n\n" "$node_matches" | sed 's/^/  /'
      found_any=true
    else
      printf "  %b⚠ 节点运行时中暂无包含 %s 的就绪镜像%b\n\n" "${C_YELLOW}" "$IMAGE_NAME" "${C_RESET}"
    fi
  else
    printf "%b[2/2] K8s 节点容器运行时:%b %b当前主机未检测到 ctr / k3s / crictl 节点工具（若此机仅为构建机，可将 tar 拷贝至节点导入）%b\n\n" "${C_BOLD}" "${C_RESET}" "${C_YELLOW}" "${C_RESET}"
  fi

  # 3. 汇总指引
  printf "────────────────────────────────────────────────────────────────────\n"
  if [ "$found_any" = "true" ]; then
    log_success "沙箱镜像检索完成。"
    printf "👉 如需使用上述镜像加速沙箱冷启动，请前往平台 Web 端：\n"
    printf "   【系统设置】→【参数配置】→【沙箱配置】\n"
    printf "   找到 %bsandbox_k8s_image%b 项，填入镜像名并点击右上角【保存变更 (⌘S)】\n" "${C_BOLD}" "${C_RESET}"
  else
    log_info "未检索到已就绪的沙箱镜像。"
    printf "👉 如需构建并加速沙箱冷启动，请执行：\n"
    printf "   %b./build-k8s-sandbox-image.sh%b          # 交互式构建\n" "${C_CYAN}" "${C_RESET}"
    printf "   %b./build-k8s-sandbox-image.sh -y%b       # 免交互直接构建\n" "${C_CYAN}" "${C_RESET}"
  fi
  printf "────────────────────────────────────────────────────────────────────\n\n"
  exit 0
}

usage() {
  cat <<'EOF'
用法: ./build-k8s-sandbox-image.sh [选项]

构建 K8s 沙箱“网关预置”镜像（把网关 venv 和 gateway 脚本预置到镜像内，跳过 AgentScope Pod bootstrap 冷启动）。
构建完成后，在平台 Web 端【系统设置】→【参数配置】→【沙箱配置】中配置给 sandbox_k8s_image 项生效。

选项:
  -l, --list              探测本地 Docker 与 K8s/K3s 节点已存在的沙箱镜像
  -y, --yes               免确认直接按当前配置开始构建
  --build                 显式触发构建流程
  --base-image <img>      基础镜像，默认 python:3.11-slim
  --image-name <name>     产物镜像名，默认 nanzi-sandbox-k8s
  --version <ver>         产物 Tag，默认 latest
  --proxy <url>           构建网络代理，如 http://127.0.0.1:7890
  --agentscope-version    覆盖安装的 agentscope 版本（默认跟随本机平台 agentscope，无则最新）
  --no-import             构建+save 后不自动导入节点（打印导入命令）
  --dry-run               只生成 Dockerfile 与命令清单，不实际构建/导入
  --sync-template         从本机 agentscope 刷新 sandbox-image/_mcp_gateway_app.py
  -h, --help              帮助

常用示例:
  ./build-k8s-sandbox-image.sh --list             # 查看本地与 K8s 节点是否已有沙箱镜像
  ./build-k8s-sandbox-image.sh --dry-run          # 仅预览 Dockerfile 与执行命令（演练模式）
  ./build-k8s-sandbox-image.sh -y                 # 免交互直接按默认配置开始构建
  ./build-k8s-sandbox-image.sh --version 1.0.0    # 构建指定版本镜像并导入节点
EOF
}

ORIGINAL_ARGC=$#

while [ $# -gt 0 ]; do
  case "$1" in
    -l|--list|list|--check) LIST_MODE=true; shift ;;
    -y|--yes)     AUTO_CONFIRM=true; shift ;;
    build|--build) shift ;;
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

if [ "$LIST_MODE" = "true" ]; then
  run_list_sandbox_images
fi

# 前置检查 Docker 运行环境（在用户交互确认与构建前提前发现问题并引导）
check_docker_environment_k8s

# 无参数直接执行时的安全引导与交互式确认
if [ "$ORIGINAL_ARGC" -eq 0 ] && [ "$AUTO_CONFIRM" != "true" ]; then
  usage
  printf "\n"
  log_info "当前构建目标与平台生效路径："
  printf "  • 基础镜像:     %b%s%b\n" "${C_BOLD}" "$BASE_IMAGE" "${C_RESET}"
  printf "  • 产物镜像:     %b%s:%s%b\n" "${C_BOLD}" "$IMAGE_NAME" "$IMAGE_TAG" "${C_RESET}"
  printf "  • 自动导入节点: %b%s%b\n" "${C_BOLD}" "$DO_IMPORT" "${C_RESET}"
  printf "  • 平台配置路径: %b系统设置 → 参数配置 → 沙箱配置 → sandbox_k8s_image%b\n" "${C_CYAN}" "${C_RESET}"
  printf "\n"
  if [ -t 0 ] && [ -t 1 ]; then
    printf "%b💡 未指定参数，是否以默认配置 [%s:%s] 立即开始构建？[y/N]: %b" "${C_YELLOW}" "$IMAGE_NAME" "$IMAGE_TAG" "${C_RESET}"
    read -r confirm
    case "$confirm" in
      [yY]|[yY][eE][sS])
        log_info "已确认，开始执行构建流程..."
        ;;
      *)
        log_info "已取消构建。如需演练或指定参数，请参考上方帮助。"
        exit 0
        ;;
    esac
  else
    log_warn "未指定参数且当前非交互终端，已安全退出。如需免交互构建请添加 -y/--yes 参数。"
    exit 0
  fi
fi

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

# 系统依赖与常见排障/开发工具（包含 tree, telnet, net-tools, ping, dig, ps 等）
RUN apt-get update -qq \\
 && apt-get install -y --no-install-recommends \\
      curl ca-certificates ripgrep \\
      tree telnet net-tools iputils-ping dnsutils iproute2 procps \\
      git jq unzip wget less \\
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
  log_info "构建完成后的平台配置路径："
  printf "  👉 前往平台 Web 端：【系统设置】→【参数配置】→【沙箱配置】\n"
  printf "  👉 找到 sandbox_k8s_image 项，填入 %b%s%b 并点击右上角【保存变更】\n" "${C_BOLD}" "$FULL_IMAGE" "${C_RESET}"
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
  resolve_node_container_tool
  if [ -n "$NODE_CTR_CMD" ] && [[ "$NODE_CTR_CMD" != *"crictl"* ]]; then
    log_info "正在导入节点容器运行时（$NODE_CTR_CMD images import）..."
    if $NODE_CTR_CMD images import "$TAR_FILE" 2>/dev/null; then
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
✅ 镜像构建与导入完成！请前往 NanZi 平台 Web 端配置生效：

1. 确认节点容器运行时已导入该镜像：
   ./install.sh check-sandbox-image $FULL_IMAGE
   （或执行: ./install.sh images $IMAGE_NAME）

2. 登录平台 Web 控制台配置生效：
   👉 打开页面：【系统设置】→【参数配置】
   👉 展开分组：【沙箱配置】
   👉 找到配置项：sandbox_k8s_image（k8s 策略沙箱容器运行的基础镜像）
   👉 填写镜像名：$FULL_IMAGE
   👉 点击右上角：【保存变更 (⌘S)】保存生效

3. 生效说明：
   配置保存后，之后所有新建或重启的沙箱 Pod 将直接复用预置网关与排障环境，
   彻底跳过 Pod 冷启动下载安装依赖的过程，冷启动从数十秒降至秒级！
────────────────────────────────────────────────────────────────────
EOF
