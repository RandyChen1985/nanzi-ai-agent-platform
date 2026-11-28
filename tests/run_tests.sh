#!/bin/bash
#
# 一键跑全量测试。
#
# 历史坑（已修）：`tests/` 各子目录原本不是完整的 Python 包（缺 __init__.py），
# pytest 的 prepend 导入模式下按文件名给模块命名，于是
# `tests/api/v1/test_chat_home_recommendations_contract.py` 与
# `tests/frontend/test_chat_home_recommendations_contract.py` 被当成同一个模块，
# 收集阶段直接 `import file mismatch` 中断 —— `pytest tests/` 根本跑不起来，
# 只能拆成「后端 --ignore=tests/frontend」+「前端 --confcutdir=tests/frontend」两段。
# 现在每个测试目录都补了 __init__.py，模块按 `tests.<path>.<name>` 全限定导入，
# 一次跑完与分段跑收集到的用例完全一致（16884 条）。
#
# 只想跑前端契约测试（纯静态断言、不碰数据库，约 3 分钟）时仍推荐分段跑：
#   .venv/bin/python -m pytest --confcutdir=tests/frontend tests/frontend

set -uo pipefail

# 获取脚本所在目录的绝对路径
SCRIPT_DIR="$( cd "$( dirname "${BASH_SOURCE[0]}" )" && pwd )"
# 进入项目根目录（脚本在 tests/ 目录中）
PROJECT_ROOT="$(dirname "$SCRIPT_DIR")"
cd "$PROJECT_ROOT"

echo "========================================"
echo "   Yovole NanZi API Test Runner"
echo "========================================"
echo "📂 Project root: $PROJECT_ROOT"

# 设置 PYTHONPATH 包含项目根目录，确保能找到 app 与 tests 包
export PYTHONPATH="$PROJECT_ROOT${PYTHONPATH:+:$PYTHONPATH}"
echo "✅ Environment configured (PYTHONPATH set)"

# 优先用项目虚拟环境：直接在系统 Python 上跑常常缺依赖，报错还指不到根因
if [ -x "$PROJECT_ROOT/.venv/bin/python" ]; then
    PYTEST=("$PROJECT_ROOT/.venv/bin/python" -m pytest)
    echo "🐍 Using .venv: $PROJECT_ROOT/.venv/bin/python"
elif command -v pytest &> /dev/null; then
    PYTEST=(pytest)
    echo "🚀 Using 'pytest' from PATH"
elif python3 -m pytest --version &> /dev/null; then
    PYTEST=(python3 -m pytest)
    echo "🚀 Using 'python3 -m pytest'"
else
    echo "❌ Error: 'pytest' not found."
    echo "Please install dependencies first:"
    echo "pip install -r requirements.txt"
    exit 1
fi

# 透传参数，便于只跑某一部分：./tests/run_tests.sh -k mcp
echo "🚀 Running: ${PYTEST[*]} tests/ $*"
"${PYTEST[@]}" tests/ "$@"
