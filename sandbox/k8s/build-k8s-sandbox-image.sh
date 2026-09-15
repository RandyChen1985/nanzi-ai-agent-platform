#!/usr/bin/env bash
# ==============================================================================
# NanZi AI 开源智能体平台 · K8s 沙箱预置镜像构建入口
# 实际脚本与模板位于: k8s_deploy/build-k8s-sandbox-image.sh
# ==============================================================================
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
TARGET_SCRIPT="$(cd "$SCRIPT_DIR/../../k8s_deploy" && pwd)/build-k8s-sandbox-image.sh"

if [ ! -f "$TARGET_SCRIPT" ]; then
  echo "❌ 未找到目标脚本: $TARGET_SCRIPT" >&2
  exit 1
fi

exec "$TARGET_SCRIPT" "$@"
