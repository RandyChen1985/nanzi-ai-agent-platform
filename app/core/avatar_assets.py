"""头像图片的校验与落盘口径（全局 AI 头像 / 智能体头像共享）。

两类头像都写入 `data/branding` 下的静态目录（由 `app/main.py` 挂载到 `/branding`），
类型白名单、大小上限与「时间戳唯一文件名」约定必须完全一致，因此集中在这里，
避免两处拷贝各自漂移。

目录约定（**不可混用**）：

- `data/branding/avatars/`       全局 AI 头像；上传成功时会清空该目录下的 `agent_avatar*`
- `data/branding/agent-avatars/` 智能体头像；清理时**只允许**删自己 `{key}_*` 前缀的文件

智能体头像文件分两类前缀，同样不可混用：

- `{agent_id}_*` 已存在智能体的正式头像
- `pending_*`    新建流程中「尚未产生 agent_id」时先落盘的头像（见 `purge_stale_pending_avatars`）

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

# 「待绑定」头像前缀：新建智能体流程中还没有 agent_id，先以该前缀落盘，
# 保存成功后 URL 直接写进 avatar_url，无需再搬动文件。
PENDING_AVATAR_PREFIX = "pending"
# 待绑定头像的存活时长：新建表单中途放弃会留下孤儿文件，超过该时长即回收。
PENDING_AVATAR_TTL_SECONDS = 24 * 60 * 60

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


def purge_stale_pending_avatars(
    directory: Optional[str] = None,
    ttl_seconds: Optional[int] = None,
) -> int:
    """回收超期未绑定的「待绑定」头像，返回删除数量。

    只匹配 `pending_*`：**不得**触碰 `{agent_id}_*` 的正式头像，否则会把已保存
    智能体的头像误删。因此正式保存智能体时必须先调用 `adopt_pending_avatar`
    把待绑定文件转正，否则这里会把一个正在使用的头像回收掉。

    默认值在**运行时**解析（而非写成默认参数），这样测试或部署侧改动能即时生效。
    """
    directory = directory or AGENT_AVATAR_DIR
    ttl_seconds = PENDING_AVATAR_TTL_SECONDS if ttl_seconds is None else ttl_seconds
    cutoff = time.time() - ttl_seconds
    removed = 0
    for path in glob.glob(os.path.join(directory, f"{PENDING_AVATAR_PREFIX}_*")):
        try:
            if os.path.isfile(path) and os.path.getmtime(path) < cutoff:
                os.remove(path)
                removed += 1
        except OSError:
            pass
    return removed


def adopt_pending_avatar(
    avatar_url: Optional[str],
    agent_id: str,
    directory: Optional[str] = None,
) -> Optional[str]:
    """把「待绑定」头像转正为 `{agent_id}_{ts}{ext}`，返回新的 URL。

    新建流程先传头像再建智能体，文件名是 `pending_*`，而 `pending_*` 会被
    `purge_stale_pending_avatars` 按 TTL 回收。一旦该 URL 被写进
    `ai_agents.avatar_url`，它就已经是正式引用，必须立刻改名到该智能体自己的
    前缀下，否则 24 小时后的清理会删掉一个正在使用的头像。

    非待绑定 URL（外部链接、已有正式头像、空值）与源文件缺失时原样返回。
    """
    directory = directory or AGENT_AVATAR_DIR
    url = str(avatar_url or "").strip()
    if not url:
        # 保持 None / 空串的「未设置」语义，不要凭空造值。
        return avatar_url

    public_prefix = f"{AGENT_AVATAR_URL}/"
    if not url.startswith(public_prefix):
        return avatar_url

    filename = url[len(public_prefix):]
    if "/" in filename or not filename.startswith(f"{PENDING_AVATAR_PREFIX}_"):
        return avatar_url

    source = os.path.join(directory, filename)
    if not os.path.isfile(source):
        return avatar_url

    target_name = f"{sanitize_asset_key(agent_id)}_{int(time.time())}{os.path.splitext(filename)[1]}"
    try:
        os.replace(source, os.path.join(directory, target_name))
    except OSError:
        return avatar_url
    return f"{AGENT_AVATAR_URL}/{target_name}"
