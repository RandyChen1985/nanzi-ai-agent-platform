#!/bin/bash
set -e

# 颜色定义
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
BLUE='\033[0;34m'
RED='\033[0;31m'
NC='\033[0m' # No Color

echo -e "${BLUE}==================================================${NC}"
echo -e "${BLUE}   南孜智能体平台 · 极速重部署 (Skip TypeCheck)   ${NC}"
echo -e "${BLUE}==================================================${NC}"

# 1. 编译前端 (Fast Mode)
echo -e "\n${YELLOW}🚀 [1/3] 正在编译前端 (Building Frontend - Fast Mode)...${NC}"
cd frontend
# 直接调用 vite build，跳过 vue-tsc 类型检查
if npx vite build; then
    echo -e "${GREEN}✅ 前端编译成功！(已跳过类型检查)${NC}"
else
    echo -e "${RED}❌ 前端编译失败${NC}"
    exit 1
fi
cd ..

# 2. 停止旧服务
echo -e "\n${YELLOW}🛑 [2/3] 正在停止旧服务 (Stopping Port 8001)...${NC}"
PID=$(lsof -ti:8001 || true)
if [ -n "$PID" ]; then
    kill -9 $PID
    echo -e "${GREEN}✅ 已停止进程 (PID: $PID)${NC}"
else
    echo -e "${GREEN}✅ 端口 8001 空闲，无需停止${NC}"
fi

# 3. 启动新服务
echo -e "\n${YELLOW}🔥 [3/3] 正在启动后端服务 (Starting Backend)...${NC}"
# 确保使用 venv 环境下的 uvicorn
if [ -f "venv/bin/uvicorn" ]; then
    CMD="venv/bin/uvicorn"
else
    CMD="uvicorn"
fi

nohup $CMD app.main:app --host 0.0.0.0 --port 8001 > server.log 2>&1 &
NEW_PID=$!
echo -e "${GREEN}✅ 服务已在后台启动 (PID: $NEW_PID)${NC}"

# 4. 检查日志
echo -e "\n${BLUE}📜 正在检查启动状态 (Waiting for startup)...${NC}"
sleep 3
if ps -p $NEW_PID > /dev/null; then
    echo -e "${GREEN}✅ 服务运行正常${NC}"
    echo "------------------------------------------------"
    head -n 10 server.log
    echo "..."
    echo "------------------------------------------------"
    echo -e "${GREEN}🎉 部署完成！访问地址: http://localhost:8001${NC}"
else
    echo -e "${RED}❌ 服务启动失败，请查看日志：${NC}"
    cat server.log
    exit 1
fi
