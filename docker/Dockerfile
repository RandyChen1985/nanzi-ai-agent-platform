# --- 阶段 1: 前端构建 ---
# PREBUILD_FRONTEND=1：宿主机已执行 vite build，仅 COPY dist（Mac 跨平台打 x86 包时推荐，避免容器 OOM）
#
# 容器内堆上限说明（3048 -> 4096）：
#   接入 @file-viewer 后，@file-viewer/vite-plugin 会自动把 preset-standard 注入 bundle，
#   它静态引入 word/pdf/spreadsheet/pptx/ofd/archive 等渲染器，产出约 4.4MB 的 chunk
#   （含 pdf.worker 1.0MB、pptx.worker 0.6MB），构建峰值内存随之超过原先的 3048MB，
#   在容器内表现为：
#     FATAL ERROR: Ineffective mark-compacts near heap limit
#     Allocation failed - JavaScript heap out of memory  (exit 134)
#   4096 与 docker/_build_common.sh 中宿主机预构建所用的 --max-old-space-size 保持一致，
#   避免「宿主机能过、容器内 OOM」这类两条路径行为不一致的问题。
#   若构建机内存紧张，优先改用 PREBUILD_FRONTEND=1 在宿主机完成前端构建。
ARG PREBUILD_FRONTEND=0
ARG APP_VERSION=dev
FROM node:20-slim AS frontend-builder
ARG PREBUILD_FRONTEND
ARG APP_VERSION
WORKDIR /app/frontend

COPY frontend/package*.json ./
RUN if [ "$PREBUILD_FRONTEND" != "1" ]; then npm ci || npm install; fi

COPY frontend/ .
RUN if [ "$PREBUILD_FRONTEND" = "1" ]; then \
      test -f dist/index.html || (echo "ERROR: PREBUILD_FRONTEND=1 但未找到 frontend/dist/index.html，请先在宿主机执行前端构建" && exit 1); \
      echo "使用宿主机预构建的前端产物"; \
    else \
      NODE_OPTIONS="--max-old-space-size=4096" VITE_APP_VERSION="$APP_VERSION" npx vite build; \
    fi

# --- 阶段 2: Python 环境构建 ---
FROM python:3.11-slim

WORKDIR /app

# 设置环境变量
ENV PYTHONDONTWRITEBYTECODE=1
ENV PYTHONUNBUFFERED=1
ENV DEBIAN_FRONTEND=noninteractive
ENV TZ=Asia/Shanghai
ENV PLATFORM_TIMEZONE=Asia/Shanghai

# 系统依赖：Oracle(libaio)、SQL Server(unixODBC + msodbcsql18)、时区数据
# openssh-client + sshpass：ssh 沙箱策略（sandbox_policy=ssh）依托平台主机的
# ssh CLI 连接远端沙箱主机；私钥认证只需 openssh-client，密码认证还需 sshpass。
# python:3.11-slim 基础镜像两者都不自带，缺失时会直接导致 ssh 策略连接测试失败。
RUN apt-get update && apt-get install -y --no-install-recommends \
    curl \
    ca-certificates \
    gnupg \
    nodejs \
    npm \
    telnet \
    net-tools \
    iputils-ping \
    dnsutils \
    procps \
    git \
    jq \
    unzip \
    tzdata \
    netcat-openbsd \
    openssh-client \
    sshpass \
    && ln -snf /usr/share/zoneinfo/$TZ /etc/localtime \
    && echo $TZ > /etc/timezone \
    && (apt-get install -y --no-install-recommends libaio1 \
        || apt-get install -y --no-install-recommends libaio1t64) \
    && ln -sf /usr/lib/x86_64-linux-gnu/libaio.so.1t64 /usr/lib/x86_64-linux-gnu/libaio.so.1 2>/dev/null || true \
    && curl -fsSL https://packages.microsoft.com/keys/microsoft.asc \
        | gpg --dearmor -o /usr/share/keyrings/microsoft-prod.gpg \
    && echo "deb [arch=amd64,arm64 signed-by=/usr/share/keyrings/microsoft-prod.gpg] https://packages.microsoft.com/debian/12/prod bookworm main" \
        > /etc/apt/sources.list.d/mssql-release.list \
    && apt-get update \
    && ACCEPT_EULA=Y apt-get install -y --no-install-recommends \
        unixodbc \
        unixodbc-dev \
        msodbcsql18 \
    && rm -rf /var/lib/apt/lists/*

# 复制依赖文件
COPY requirements.txt .

# 安装 Python 依赖（使用 BuildKit 缓存挂载以加速重新构建时的依赖下载）
RUN --mount=type=cache,target=/root/.cache/pip \
    pip install --upgrade pip && \
    pip install -r requirements.txt

# 安装 Playwright 浏览器内核及系统依赖（Chromium headless，约 260 MiB）
# 使用 --with-deps 选项，Playwright 会自动补全当前版本 Chromium 运行所需的全部底层 Linux 系统库，确保绝对完整
RUN playwright install --with-deps chromium

# 复制项目代码
COPY . .

# 从构建阶段复制前端产物
COPY --from=frontend-builder /app/frontend/dist /app/frontend/dist

# 暴露端口
EXPOSE 8001

# 启动命令
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8001"]
