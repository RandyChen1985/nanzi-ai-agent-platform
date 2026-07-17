#!/bin/bash

# 南孜智能体平台 - Docker 停止脚本

set -e

SCRIPT_DIR="$( cd "$( dirname "${BASH_SOURCE[0]}" )" && pwd )"
cd "$SCRIPT_DIR"

COMPOSE_FILE="docker-compose.ai-agent.yml"
CONTAINER_NAME="nanzi-ai-agent"

GREEN='\033[0;32m'
YELLOW='\033[1;33m'
RED='\033[0;31m'
NC='\033[0m'

echo -e "${YELLOW}=== 南孜智能体平台 Docker 停止 ===${NC}"

if docker compose version >/dev/null 2>&1; then
    docker compose -f "$COMPOSE_FILE" down 2>/dev/null || true
else
    docker-compose -f "$COMPOSE_FILE" down 2>/dev/null || true
fi

if [ "$(docker ps -aq -f name=${CONTAINER_NAME})" ]; then
    echo -e "${YELLOW}停止容器 ${CONTAINER_NAME}...${NC}"
    docker stop "$CONTAINER_NAME" 2>/dev/null || true
    docker rm "$CONTAINER_NAME" 2>/dev/null || true
fi

if [ "$(docker ps -aq -f name=${CONTAINER_NAME})" ]; then
    echo -e "${RED}✗ 停止失败，请检查: docker ps -a | grep ${CONTAINER_NAME}${NC}"
    exit 1
fi

echo -e "${GREEN}✓ 服务已停止${NC}"
