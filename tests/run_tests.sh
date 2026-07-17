#!/bin/bash

# 获取脚本所在目录的绝对路径
SCRIPT_DIR="$( cd "$( dirname "${BASH_SOURCE[0]}" )" && pwd )"
# 进入项目根目录（脚本在 tests/ 目录中）
PROJECT_ROOT="$(dirname "$SCRIPT_DIR")"
cd "$PROJECT_ROOT"

echo "========================================"
echo "   Yovole NanZi API Test Runner"
echo "========================================"
echo "📂 Project root: $PROJECT_ROOT"

# 设置 PYTHONPATH 包含项目根目录，确保能找到 app 模块
export PYTHONPATH=$PYTHONPATH:"$PROJECT_ROOT"
echo "✅ Environment configured (PYTHONPATH set)"

# 检查 pytest 是否可用
if command -v pytest &> /dev/null; then
    echo "🚀 Running tests with 'pytest'..."
    pytest tests/
elif python3 -m pytest --version &> /dev/null; then
    echo "🚀 Running tests with 'python3 -m pytest'..."
    python3 -m pytest tests/
else
    echo "❌ Error: 'pytest' not found."
    echo "Please install dependencies first:"
    echo "pip install -r requirements.txt"
    exit 1
fi
