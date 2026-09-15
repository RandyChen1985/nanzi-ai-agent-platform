#!/bin/bash
# ==============================================================================
# NanZi AI 开源智能体平台 · Docker 安全沙箱镜像预构建运维脚本
# 用法:
#   ./sandbox/docker/prebuild-sandbox.sh                    # 交互式构建 (默认 python:3.11-slim)
#   ./sandbox/docker/prebuild-sandbox.sh -y                 # 免交互直接构建
#   ./sandbox/docker/prebuild-sandbox.sh -n / --dry-run     # 演练预览生成的 Dockerfile 与上下文
#   ./sandbox/docker/prebuild-sandbox.sh -l / --list        # 探测本地所有已构建的沙箱镜像
#   ./sandbox/docker/prebuild-sandbox.sh --status           # 检查当前基础镜像预构建状态
#   ./sandbox/docker/prebuild-sandbox.sh --force            # 强制重新构建（忽略缓存）
#   ./sandbox/docker/prebuild-sandbox.sh --proxy <URL>      # 配置构建 HTTP/HTTPS 代理
#   ./sandbox/docker/prebuild-sandbox.sh --base-image <IMG> # 指定基础镜像
# ==============================================================================
set -e

# 颜色定义
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
BLUE='\033[0;34m'
CYAN='\033[0;36m'
RED='\033[0;31m'
NC='\033[0m' # No Color

# 脚本所在目录及项目根目录
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ROOT_DIR="$(cd "$SCRIPT_DIR/../.." && pwd)"
cd "$ROOT_DIR"

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

# 执行 Python 运维脚本并透传所有参数
exec "$PYTHON_BIN" "$SCRIPT_DIR/prebuild_docker_sandbox.py" "$@"
