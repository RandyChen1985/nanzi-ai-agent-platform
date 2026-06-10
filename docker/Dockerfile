# --- 阶段 1: 前端构建 ---
# PREBUILD_FRONTEND=1：宿主机已执行 vite build，仅 COPY dist（Mac 跨平台打 x86 包时推荐，避免容器 OOM）
ARG PREBUILD_FRONTEND=0
FROM node:20-slim AS frontend-builder
ARG PREBUILD_FRONTEND
WORKDIR /app/frontend

COPY frontend/package*.json ./
RUN if [ "$PREBUILD_FRONTEND" != "1" ]; then npm ci || npm install; fi

COPY frontend/ .
RUN if [ "$PREBUILD_FRONTEND" = "1" ]; then \
      test -f dist/index.html || (echo "ERROR: PREBUILD_FRONTEND=1 但未找到 frontend/dist/index.html，请先在宿主机执行前端构建" && exit 1); \
      echo "使用宿主机预构建的前端产物"; \
    else \
      NODE_OPTIONS="--max-old-space-size=2048" npx vite build; \
    fi

# --- 阶段 2: Python 环境构建 ---
FROM python:3.11-slim

WORKDIR /app

# 设置环境变量
ENV PYTHONDONTWRITEBYTECODE=1
ENV PYTHONUNBUFFERED=1

# 安装系统依赖 (Oracle Client 需要 libaio)
RUN apt-get update && apt-get install -y --no-install-recommends \
    libaio1 \
    || apt-get install -y --no-install-recommends libaio1t64 \
    && ln -s /usr/lib/x86_64-linux-gnu/libaio.so.1t64 /usr/lib/x86_64-linux-gnu/libaio.so.1 || true \
    && rm -rf /var/lib/apt/lists/*    

# 复制依赖文件
COPY requirements.txt .

# 安装 Python 依赖
RUN pip install --no-cache-dir --upgrade pip && \
    pip install --no-cache-dir -r requirements.txt

# 复制项目代码
COPY . .

# 从构建阶段复制前端产物
COPY --from=frontend-builder /app/frontend/dist /app/frontend/dist

# 暴露端口
EXPOSE 8001

# 启动命令
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8001"]
