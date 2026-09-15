#!/bin/bash
# ==============================================================================
# NanZi AI 开源智能体平台 · Docker 安全沙箱镜像预构建运维脚本
# 用法:
#   ./sandbox/docker/build-docker-sandbox-image.sh                    # 交互式构建 (默认 python:3.11-slim)
#   ./sandbox/docker/build-docker-sandbox-image.sh -y                 # 免交互直接构建
#   ./sandbox/docker/build-docker-sandbox-image.sh -n / --dry-run     # 演练预览生成的 Dockerfile 与上下文
#   ./sandbox/docker/build-docker-sandbox-image.sh -l / --list        # 探测本地所有已构建的沙箱镜像
#   ./sandbox/docker/build-docker-sandbox-image.sh --status           # 检查当前基础镜像预构建状态
#   ./sandbox/docker/build-docker-sandbox-image.sh --force            # 强制重新构建（忽略缓存）
#   ./sandbox/docker/build-docker-sandbox-image.sh --proxy <URL>      # 配置构建 HTTP/HTTPS 代理
#   ./sandbox/docker/build-docker-sandbox-image.sh --base-image <IMG> # 指定基础镜像
# ==============================================================================
set -e

# 颜色定义
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
BLUE='\033[0;34m'
CYAN='\033[0;36m'
RED='\033[0;31m'
BOLD='\033[1m'
NC='\033[0m' # No Color

# 脚本所在目录及项目根目录
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ROOT_DIR="$(cd "$SCRIPT_DIR/../.." && pwd)"
cd "$ROOT_DIR"

# Docker 运行环境前置预检与引导函数
check_docker_environment() {
    # 帮助、演练模式或通过 HTTP API 触发时，无需依赖本地 Docker Daemon
    for arg in "$@"; do
        case "$arg" in
            -h|--help|-n|--dry-run|--api-url)
                return 0
                ;;
        esac
    done

    # 1. 检查 docker CLI 是否已安装
    if ! command -v docker &>/dev/null; then
        echo -e "${RED}❌ 未检测到 Docker 命令行工具 (docker: command not found)${NC}"
        echo -e "${YELLOW}💡 NanZi AI Docker 安全沙箱镜像构建与运行时依赖本地 Docker 引擎。${NC}"
        echo -e "────────────────────────────────────────────────────────────────────"
        echo -e "${BOLD}👉 安装与排障建议：${NC}"
        echo -e "   • Linux (Ubuntu/Debian/CentOS) 自动化安装："
        echo -e "     ${CYAN}curl -fsSL https://get.docker.com | bash${NC}"
        echo -e "   • 或使用发行版包管理器："
        echo -e "     ${CYAN}sudo apt-get update && sudo apt-get install -y docker.io${NC}  # Ubuntu/Debian"
        echo -e "     ${CYAN}sudo yum install -y docker${NC}                              # CentOS/RHEL"
        echo -e "   • macOS / Windows："
        echo -e "     请前往官网下载安装 Docker Desktop: https://www.docker.com/products/docker-desktop"
        echo -e "   • 演练预览生成的 Dockerfile（无需 Docker 环境）："
        echo -e "     ${CYAN}./sandbox/docker/build-docker-sandbox-image.sh --dry-run${NC}"
        echo -e "────────────────────────────────────────────────────────────────────\n"
        exit 1
    fi

    # 提取 Docker Client 基础信息
    local cli_ver docker_ctx docker_endpoint
    cli_ver="$(docker version --format '{{.Client.Version}}' 2>/dev/null || echo "未知版本")"
    docker_ctx="$(docker context show 2>/dev/null || echo "default")"
    docker_endpoint="$(docker context inspect --format '{{.Endpoints.docker.Host}}' 2>/dev/null || echo "${DOCKER_HOST:-/var/run/docker.sock}")"

    # 2. 检查 Docker Daemon 守护进程是否处于运行状态与权限
    local docker_info_raw
    if ! docker_info_raw="$(docker info --format '{{.ServerVersion}}|{{.OperatingSystem}}|{{.Architecture}}|{{.NCPU}}|{{.MemTotal}}|{{.ContainersRunning}}|{{.Images}}' 2>&1)"; then
        echo -e "${RED}❌ 本地 Docker Daemon 守护进程未启动或当前用户权限不足！${NC}"
        echo -e "======================================================================"
        echo -e "${CYAN}🐳 Docker 客户端环境检测信息：${NC}"
        echo -e "   • 客户端版本 (CLI):     ${BOLD}${cli_ver}${NC}"
        echo -e "   • 当前上下文 (Context): ${BOLD}${docker_ctx}${NC}"
        echo -e "   • 目标端点 (Endpoint):  ${BOLD}${docker_endpoint}${NC}"
        echo -e "   • 服务端状态 (Server):  ${RED}🔴 未运行 / 无法连接${NC}"
        echo -e "======================================================================"
        echo -e "${BOLD}👉 排障与解决引导：${NC}"
        if [[ "$docker_info_raw" =~ [Pp]ermission\ denied ]]; then
            echo -e "   ${YELLOW}【原因：权限不足】${NC} 当前用户没有访问 Docker Socket 的权限："
            echo -e "   1. 将当前用户加入 docker 用户组："
            echo -e "      ${CYAN}sudo usermod -aG docker \$USER${NC}"
            echo -e "   2. 刷新当前 Shell 用户组或重新登录："
            echo -e "      ${CYAN}newgrp docker${NC}"
            echo -e "   3. 或临时使用 sudo 运行此构建脚本："
            echo -e "      ${CYAN}sudo ./sandbox/docker/build-docker-sandbox-image.sh [选项]${NC}"
        elif [[ "$docker_ctx" == "colima" ]] || [[ "$docker_endpoint" =~ colima ]]; then
            echo -e "   ${YELLOW}【原因：Colima 虚拟机未启动】${NC} 检测到当前 Docker Context 使用的是 Colima："
            echo -e "   👉 请在终端执行以下命令启动 Colima 虚拟机："
            echo -e "      ${CYAN}colima start${NC}"
        elif [[ "$docker_ctx" =~ (desktop|desktop-linux) ]] || [[ "$docker_endpoint" =~ docker\.desktop ]]; then
            echo -e "   ${YELLOW}【原因：Docker Desktop 未启动】${NC} 检测到当前使用的是 Docker Desktop："
            echo -e "   👉 请启动 Docker Desktop 应用程序并等待就绪（状态图标变为绿色）。"
        else
            echo -e "   ${YELLOW}【原因：服务未运行】${NC} Docker 守护进程未启动："
            echo -e "   • Linux 系统启动服务并设为开机自启："
            echo -e "      ${CYAN}sudo systemctl start docker && sudo systemctl enable docker${NC}"
            echo -e "   • macOS / Windows 系统："
            echo -e "      请启动 Docker 运行时（Docker Desktop 或 Colima）。"
        fi
        echo -e "   • 演练预览生成的 Dockerfile（无需 Docker 环境）："
        echo -e "      ${CYAN}./sandbox/docker/build-docker-sandbox-image.sh --dry-run${NC}"
        echo -e "────────────────────────────────────────────────────────────────────\n"
        exit 1
    fi

    # 3. 格式化解析并打印 Docker 运行环境明细
    local server_ver server_os server_arch server_ncpu server_mem server_running server_images
    IFS='|' read -r server_ver server_os server_arch server_ncpu server_mem server_running server_images <<< "$docker_info_raw"
    local mem_formatted="未知"
    if [ -n "$server_mem" ] && [ "$server_mem" -gt 0 ] 2>/dev/null; then
        mem_formatted="$(awk "BEGIN {printf \"%.2f GiB\", $server_mem/1024/1024/1024}" 2>/dev/null || echo "$((server_mem / 1073741824)) GiB")"
    fi

    echo -e "\n======================================================================"
    echo -e "${GREEN}🐳 [Docker 运行环境预检通过] 检测到可用 Docker 引擎：${NC}"
    echo -e "   • 客户端版本 (CLI):     ${BOLD}${cli_ver}${NC}"
    echo -e "   • 服务端版本 (Server):  ${BOLD}${server_ver}${NC} (${server_os}, ${server_arch})"
    echo -e "   • 当前上下文 (Context): ${BOLD}${docker_ctx}${NC}"
    echo -e "   • 守护进程端点 (Host):   ${BOLD}${docker_endpoint}${NC}"
    echo -e "   • 宿主分配规格 (Specs):  ${BOLD}${server_ncpu} 核 CPU / ${mem_formatted} 内存${NC}"
    echo -e "   • 容器/镜像状态:        运行中容器: ${server_running} / 本地镜像数: ${server_images}"
    echo -e "======================================================================\n"
}

# 寻找 Python 解释器（优先使用项目根目录的 .venv / venv）
PYTHON_BIN=""
if [ -f "$ROOT_DIR/.venv/bin/python" ]; then
    PYTHON_BIN="$ROOT_DIR/.venv/bin/python"
elif [ -f "$ROOT_DIR/venv/bin/python" ]; then
    PYTHON_BIN="$ROOT_DIR/venv/bin/python"
elif command -v python3 &>/dev/null; then
    PYTHON_BIN="python3"
elif command -v python &>/dev/null; then
    PYTHON_BIN="python"
else
    echo -e "${RED}❌ 未找到可用的 Python 解释器，请先安装 Python 3.11+ 或配置虚拟环境！${NC}"
    exit 1
fi

# 执行前置环境预检
check_docker_environment "$@"

# 执行 Python 运维脚本并透传所有参数
exec "$PYTHON_BIN" "$SCRIPT_DIR/prebuild_docker_sandbox.py" "$@"
