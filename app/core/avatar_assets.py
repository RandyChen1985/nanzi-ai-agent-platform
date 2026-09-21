"""头像图片的校验与落盘口径（全局 AI 头像 / 智能体头像共享）。

两类头像都写入 `data/branding` 下的静态目录（由 `app/main.py` 挂载到 `/branding`），
类型白名单、大小上限与「时间戳唯一文件名」约定必须完全一致，因此集中在这里，
避免两处拷贝各自漂移。

目录约定（**不可混用**）：

- `data/branding/avatars/`       全局 AI 头像；上传成功时会清空该目录下的 `agent_avatar*`
- `data/branding/agent-avatars/` 智能体头像；清理时**只允许**删自己 `{key}_*` 前缀的文件

历史坑：全局头像上传用 `agent_avatar*` 通配清理旧文件。若智能体头像落在同一目录
或使用同前缀，上传任意一个智能体头像都会连带删除全局头像和其它智能体头像，
因此这里用独立子目录 + 独立前缀强制隔离。
"""
import glob
import os
import re
import time
from typing import Optional

# 单张头像上限 2MB：与历史口径一致，两类头像共用。
MAX_AVATAR_BYTES = 2 * 1024 * 1024

ALLOWED_AVATAR_TYPES = {
    "image/png": ".png",
    "image/jpeg": ".jpg",
    "image/jpg": ".jpg",
    "image/webp": ".webp",
    "image/svg+xml": ".svg",
    "image/gif": ".gif",
}

BRANDING_ROOT_DIR = "data/branding"
# 全局 AI 头像目录（保持历史值不变，portal-prefs 上传端点依赖它做整目录清理）。
BRANDING_AVATARS_DIR = os.path.join(BRANDING_ROOT_DIR, "avatars")
BRANDING_AVATARS_URL = "/branding/avatars"
# 智能体头像目录：必须与全局头像目录分离，见模块头部的历史坑说明。
AGENT_AVATAR_DIR = os.path.join(BRANDING_ROOT_DIR, "agent-avatars")
AGENT_AVATAR_URL = "/branding/agent-avatars"

_UNSAFE_FILENAME_CHARS = re.compile(r"[^A-Za-z0-9_-]")


def resolve_avatar_extension(content_type: Optional[str], filename: Optional[str]) -> Optional[str]:
    """解析允许的图片后缀：MIME 优先，文件名后缀兜底；不支持则返回 None。"""
    ext = ALLOWED_AVATAR_TYPES.get((content_type or "").lower())
    if ext:
        return ext
    filename_lower = (filename or "").lower()
    for allowed_ext in ALLOWED_AVATAR_TYPES.values():
        if filename_lower.endswith(allowed_ext):
            return allowed_ext
    # .jpeg 不单独登记在 values 中（统一归一为 .jpg），这里补一次兜底匹配。
    if filename_lower.endswith(".jpeg"):
        return ".jpg"
    return None


def sanitize_asset_key(raw: object) -> str:
    """把智能体 ID 之类的标识清洗为安全文件名片段，杜绝路径穿越。"""
    cleaned = _UNSAFE_FILENAME_CHARS.sub("", str(raw or ""))[:64]
    return cleaned or "unknown"


def save_avatar_file(
    data: bytes,
    ext: str,
    *,
    directory: str,
    public_prefix: str,
    prefix: str,
    cleanup_glob: Optional[str] = None,
) -> str:
    """落盘头像并返回可公开访问的 URL 路径。

    - 文件名带秒级时间戳，保证 URL 绝对唯一，彻底规避浏览器静态强缓存
    - `public_prefix` 与物理目录**分开传入**：目录可被测试替换为临时路径，
      公开 URL 必须始终是 `/branding/...` 形态
    - `cleanup_glob` 只传本主体自己的通配模式；**不要**传跨主体的模式
    """
    os.makedirs(directory, exist_ok=True)
    if cleanup_glob:
        for old_file in glob.glob(os.path.join(directory, cleanup_glob)):
            try:
                if os.path.isfile(old_file):
                    os.remove(old_file)
            except OSError:
                pass

    filename = f"{prefix}_{int(time.time())}{ext}"
    with open(os.path.join(directory, filename), "wb") as handle:
        handle.write(data)

    return f"{public_prefix.rstrip('/')}/{filename}"
