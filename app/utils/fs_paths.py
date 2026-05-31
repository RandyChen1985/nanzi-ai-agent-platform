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
    normalized_target = os.path.normpath(os.path.abspath(path))
    if not normalized_target.startswith(normalized_base):
        return None
    return normalized_target
