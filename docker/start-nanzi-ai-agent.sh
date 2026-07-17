#!/bin/bash

# 南孜智能体平台 - Docker 启动脚本
# 用途：启动 API 服务容器（依赖外部 MySQL/ClickHouse/Redis）

set -e

SCRIPT_DIR="$( cd "$( dirname "${BASH_SOURCE[0]}" )" && pwd )"
cd "$SCRIPT_DIR"

COMPOSE_FILE="docker-compose.ai-agent.yml"
CONTAINER_NAME="nanzi-ai-agent"

GREEN='\033[0;32m'
YELLOW='\033[1;33m'
RED='\033[0;31m'
NC='\033[0m'

echo -e "${GREEN}=== 南孜智能体平台 Docker 启动 ===${NC}"

if [ ! -f ".env" ]; then
    echo -e "${RED}错误: .env 文件不存在${NC}"
    echo "请先复制 .env 文件到 docker 目录"
    exit 1
fi

if ! docker images | grep -q "nanzi-ai-agent"; then
    echo -e "${YELLOW}警告: nanzi-ai-agent 镜像不存在${NC}"
    echo "请先构建镜像: ./build_linux_x86.sh"
    exit 1
fi

if [ "$(docker ps -aq -f name=${CONTAINER_NAME})" ]; then
    echo -e "${YELLOW}停止并删除旧容器...${NC}"
    docker stop "$CONTAINER_NAME" 2>/dev/null || true
    docker rm "$CONTAINER_NAME" 2>/dev/null || true
fi

echo -e "${GREEN}启动 AI Agent 容器...${NC}"
if docker compose version >/dev/null 2>&1; then
    docker compose -f "$COMPOSE_FILE" up -d
else
    docker-compose -f "$COMPOSE_FILE" up -d
fi

echo -e "${YELLOW}等待服务启动并检查依赖连接...${NC}"
sleep 5

echo -e "${YELLOW}检查数据库连接状态...${NC}"
docker logs "$CONTAINER_NAME" 2>&1 | grep -E "(Connecting to|connected successfully|Redis)" || true
echo ""

if [ "$(docker ps -q -f name=${CONTAINER_NAME})" ]; then
    echo -e "${GREEN}✓ 服务启动成功！${NC}"
    echo ""
    echo "服务信息:"
    echo "  - API 地址: http://localhost:8001"
    echo "  - API 文档: http://localhost:8001/docs"
    echo "  - 管理后台: http://localhost:8001/"
    echo ""
    echo "查看日志: docker logs -f ${CONTAINER_NAME}"
    echo "停止服务: ./stop-nanzi-ai-agent.sh"
else
    echo -e "${RED}✗ 服务启动失败${NC}"
    echo "查看日志: docker logs ${CONTAINER_NAME}"
    exit 1
fi
