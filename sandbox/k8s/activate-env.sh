#!/bin/bash
# activate-env.sh —— 手动激活 dev.sh 创建的项目虚拟环境
#
# 用法（必须用 source 执行，否则激活不会影响当前 shell）：
#   source sandbox/k8s/activate-env.sh
#
# 激活后即可直接使用项目依赖（pip、python、uvicorn 等），
# 用于沙箱镜像构建时找不到包的排查与调试。

# 定位项目根目录（脚本所在的两级父目录）
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_ROOT="$(cd "${SCRIPT_DIR}/../.." && pwd)"
VENV_ACTIVATE="${PROJECT_ROOT}/.venv/bin/activate"

if [ ! -f "${VENV_ACTIVATE}" ]; then
    echo "❌ 虚拟环境不存在：${VENV_ACTIVATE}"
    echo "   请先在项目根目录执行 ./dev.sh 初始化环境后再试。"
    return 1 2>/dev/null || exit 1
fi

# shellcheck source=/dev/null
source "${VENV_ACTIVATE}"
echo "✅ 已激活虚拟环境：${VIRTUAL_ENV}"
echo "   Python: $(python --version)"
