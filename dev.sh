#!/bin/bash
set -e

# 颜色定义
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
BLUE='\033[0;34m'
RED='\033[0;31m'
NC='\033[0m' # No Color

echo -e "${BLUE}==================================================${NC}"
echo -e "${BLUE}       南孜智能体平台 · 本地开发启动工具         ${NC}"
echo -e "${BLUE}==================================================${NC}"

# 1. 停止旧服务
echo -e "\n${YELLOW}🛑 [1/3] 正在检查并停止旧服务 (Port 8001)...${NC}"
PID=$(lsof -ti:8001 || true)
if [ -n "$PID" ]; then
    kill -9 $PID
    echo -e "${GREEN}✅ 已停止旧进程 (PID: $PID)${NC}"
else
    echo -e "${GREEN}✅ 端口 8001 空闲，无需停止${NC}"
fi

# 2. 编译前端
echo -e "\n${YELLOW}🚀 [2/3] 正在编译前端 (Building Frontend)...${NC}"
if [ -d "frontend" ]; then
    cd frontend
    # 优先使用 vite build (快)，如果需要完整类型检查可改回 npm run build
    if NODE_OPTIONS="--max-old-space-size=4096" npx vite build; then
        echo -e "${GREEN}✅ 前端编译成功！${NC}"
    else
        echo -e "${RED}❌ 前端编译失败${NC}"
        exit 1
    fi
    cd ..
else
    echo -e "${RED}❌ 错误：未找到 frontend 目录${NC}"
    exit 1
fi

# 3. 前台启动后端
echo -e "\n${YELLOW}🔥 [3/3] 正在启动后端服务 (Starting Backend in Foreground)...${NC}"
echo -e "${BLUE}提示：您将在此看到实时运行日志，按 Ctrl+C 可停止服务。${NC}"
echo "------------------------------------------------"

# 确定 Python 环境
if [ -f "venv/bin/python" ]; then
    PYTHON_CMD="venv/bin/python"
else
    PYTHON_CMD="python3"
fi

# 前台启动 uvicorn
# Only watch source directories. Runtime workspaces under data/ are written during
# agent execution and must not trigger a reload that kills in-flight tasks.
RELOAD_ARGS=(--reload --reload-dir app)
if [ -d "architech" ]; then
    RELOAD_ARGS+=(--reload-dir architech)
fi

$PYTHON_CMD -m uvicorn app.main:app --host 0.0.0.0 --port 8001 "${RELOAD_ARGS[@]}"
