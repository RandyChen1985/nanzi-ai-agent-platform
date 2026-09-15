#!/usr/bin/env python3
"""Docker 沙箱镜像预构建运维脚本 (向后兼容入口).

核心实现已归集至: sandbox/docker/prebuild_docker_sandbox.py
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

ROOT_DIR = Path(__file__).resolve().parent.parent
TARGET_PY = ROOT_DIR / "sandbox" / "docker" / "prebuild_docker_sandbox.py"

if not TARGET_PY.exists():
    sys.stderr.write(f"❌ 未找到目标脚本: {TARGET_PY}\n")
    sys.exit(1)

# 直接以当前进程执行目标脚本
os.execv(sys.executable, [sys.executable, str(TARGET_PY)] + sys.argv[1:])
