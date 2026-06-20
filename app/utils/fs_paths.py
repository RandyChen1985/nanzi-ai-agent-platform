"""安全数据目录路径工具（文件浏览器、附件预览、多模态读图共用）。"""
from __future__ import annotations

import os
from typing import Optional


def get_data_base_dir() -> str:
    base = "/app/data"
    if not os.path.exists(base):
        base = os.path.abspath("data")
    os.makedirs(base, exist_ok=True)
    return os.path.normpath(base)

def normalize_under_base(path: str, base: Optional[str] = None) -> Optional[str]:
    """将路径规范化为绝对路径，且必须在 base 目录下，否则返回 None。"""
    if not path:
        return None
    base_dir = base or get_data_base_dir()
    normalized_base = os.path.normpath(base_dir)

    # 1. 兼容性：大模型在生成文件绝对路径时可能由于幻觉产生错误的工作空间名前缀（例如多写 yovole- ）
    # 只要绝对路径中包含 data/agent_workspaces/ 或 agent_workspaces/，我们强行将其前半截前缀重映射为真实的 data 基础物理前缀
    if "data/agent_workspaces/" in path:
        parts = path.split("data/agent_workspaces/")
        path = os.path.join(normalized_base, "agent_workspaces", parts[-1])
    elif "agent_workspaces/" in path:
        parts = path.split("agent_workspaces/")
        path = os.path.join(normalized_base, "agent_workspaces", parts[-1])

    # 2. 兼容性：若输入为 Docker 下的绝对路径 /app/data，但本地开发使用工作区 data，则将其重映射为本地路径
    if path.startswith("/app/data/"):
        path = os.path.join(normalized_base, path[len("/app/data/"):])
    elif path == "/app/data":
        path = normalized_base
        
    # 2. 兼容性：如果输入的是相对路径，尝试拼接在 base_dir 之下
    target_abs = os.path.abspath(path)
    if not os.path.normpath(target_abs).startswith(normalized_base):
        cleaned_path = path
        if cleaned_path.startswith("./"):
            cleaned_path = cleaned_path[2:]
        elif cleaned_path.startswith("../"):
            cleaned_path = cleaned_path[3:]
        
        target_relative = os.path.normpath(os.path.join(normalized_base, cleaned_path))
        if target_relative.startswith(normalized_base):
            target_abs = target_relative
            
    normalized_target = os.path.normpath(target_abs)
    if not normalized_target.startswith(normalized_base):
        return None
    return normalized_target
