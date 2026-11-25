"""从用户自然语言中解析并匹配可用技能（口头指定 + 关键词扫描）。"""
from __future__ import annotations

from collections import OrderedDict
import os
import re
import logging
import threading
import time
from typing import Any, Dict, Iterable, List, Optional, Set

from app.utils.skill_metadata import (
    clip_skill_text,
    extract_plain_user_query,
    format_skill_resource_tree,
    is_skill_enabled,
)

logger = logging.getLogger(__name__)

SCOPE_GLOBAL = "global"
SCOPE_PERSONAL = "personal"

MAIN_GENERAL_AGENT_ID = "sys-agent-chat"
# 与 RouterService.FALLBACK_AGENT_NAMES 保持一致（主助手 slug 别名）
MAIN_GENERAL_AGENT_SLUGS = frozenset({"assistant", "main", "general-chat"})

# 扫描时从用户问题中剔除的泛化词，避免「查一下」「帮我」误命中
_SCAN_STOP_TERMS = frozenset({
    "一下", "帮我", "帮忙", "麻烦", "请问", "请", "查询", "查一下", "查下", "查",
    "看看", "看一下", "看下", "了解", "知道", "怎么", "如何", "怎样", "什么", "哪些",
    "有没有", "能否", "可以", "给我", "告诉我", "吗", "呢", "啊", "吧",
    "的", "了", "是", "在", "有", "和", "与", "或", "及", "把", "给", "我", "你",
    "the", "and", "for", "with", "this", "that", "what", "how", "please", "help",
})
# 中文长句没有空格分隔，先把高泛化功能词从连续汉字中切开，再对剩余实词抽 2-gram。
_CJK_STOP_SPLIT_TERMS = sorted(
    (
        "帮我", "帮忙", "麻烦", "请问", "查一下", "看一下", "看下", "查下",
        "查询", "看看", "一下", "了解", "知道", "怎么", "如何", "怎样",
        "什么", "哪些", "有没有", "能否", "可以", "给我", "告诉我",
        "请", "查", "的", "了", "吗", "呢", "啊", "吧", "是", "在", "有",
        "和", "与", "或", "及", "把", "给", "我", "你",
    ),
    key=len,
    reverse=True,
)
_CJK_STOP_SPLIT_RE = re.compile("|".join(re.escape(term) for term in _CJK_STOP_SPLIT_TERMS))
_CJK_RUN_RE = re.compile(r"[\u4e00-\u9fff]+")
_ALNUM_WORD_RE = re.compile(r"[a-zA-Z][a-zA-Z0-9_-]{1,}")

_SKILL_USE_PATTERNS = [
    re.compile(r"使用(?:一下|下)?[「\"']?(.+?)[」\"']?(?:的)?(?:技能|skill)", re.I),
    re.compile(r"用(?:一下|下)?[「\"']?(.+?)[」\"']?(?:的)?(?:技能|skill)", re.I),
    re.compile(r"(?:调用|按|按照|执行|运行|触发|加载|套用)(?:一下|下)?[「\"']?(.+?)[」\"']?(?:的)?(?:技能|skill)", re.I),
    re.compile(r"(?:技能|skill)[「\"']?(.+?)[」\"']?(?:执行|运行|查|查询|处理|分析|生成)", re.I),
    re.compile(r"\b(?:use|run|invoke|call|execute)\s+([a-zA-Z0-9_\u4e00-\u9fff -]{2,36}?)\s+skill\b", re.I),
]

_SKILL_META_CACHE_MAX_ENTRIES = 128
_SKILL_META_CACHE_TTL_SECONDS = 60.0
_skill_meta_cache: OrderedDict[
    tuple[str, str, tuple[Any, ...]],
    tuple[float, tuple[Dict[str, str], ...]],
] = OrderedDict()
_skill_meta_cache_lock = threading.RLock()


def _normalize(text: str) -> str:
    return re.sub(r"\s+", "", (text or "").lower())


def _validate_skill_id(skill_id: str) -> bool:
    if not skill_id or "/" in skill_id or "\\" in skill_id or ".." in skill_id:
        return False
    return bool(re.match(r"^[a-zA-Z0-9_-]+$", skill_id))


def _parse_skill_frontmatter(skill_id: str, skill_md_path: str) -> Dict[str, str]:
    from app.utils.skill_metadata import parse_skill_frontmatter

    return parse_skill_frontmatter(skill_id, skill_md_path)


def get_user_personal_skills_dir(user_info: Optional[Dict[str, Any]]) -> Optional[str]:
    """推导当前用户的个人技能目录路径（agent_workspaces/{user_key}/skills/）。"""
    if not user_info:
        return None
    try:
        from app.services.ai.runtime.agentscope.workspace import (
            default_workspace_root,
            extract_workspace_identity,
            resolve_workspace_user_key,
        )

        user_id, user_name = extract_workspace_identity(user_info=user_info)
        if user_id is None:
            return None
        user_key = resolve_workspace_user_key(user_id=user_id, user_name=user_name)
        return os.path.join(default_workspace_root(), user_key, "skills")
    except Exception as e:
        logger.debug("[Skills] Failed to resolve personal skills dir: %s", e)
        return None


PERSONAL_SKILL_SEED_DIR_NAME = ".seed"


def platform_skill_id_set() -> set[str]:
    """公共技能库当前的技能 ID 集合（目录名即 ID，与 ``_scan_skill_dir`` 口径一致）。

    用途：判定历史遗留的 ``<user>/skills/.seed/`` 里**哪些是公共技能副本**——那个时期
    技能根恰好是个人技能源目录，AgentScope 把 ``skill_paths``（公共 + 个人）整体打包，
    目录名沿用源目录名。**只在 ``.seed`` 语境下使用**：平铺位置出现同 ID 是平台支持的
    「个人覆盖公共」，不能据此判为副本。
    """
    try:
        from app.core.config import settings

        root = getattr(settings, "SKILLS_DIR", None)
    except Exception:
        return set()
    if not root or not os.path.isdir(root):
        return set()
    try:
        return {
            name
            for name in os.listdir(root)
            if not name.startswith(".") and os.path.isdir(os.path.join(root, name))
        }
    except OSError:
        return set()


def iter_personal_skill_dirs(
    personal_root: str,
    *,
    exclude_platform_copies: bool = True,
) -> List[tuple[str, str]]:
    """列出个人技能根下的 ``(skill_id, skill_dir)``，兼容 AgentScope 的 seed 布局。

    历史背景：K8s 沙箱在补齐技能子挂载之前只挂载了整个用户工作区，AgentScope 的技能根
    ``${workdir}/skills`` 恰好就是**个人技能源目录**；它 ``initialize()`` 时的
    ``_migrate_skill_layout`` 会把平铺技能**搬移**（``shutil.move``）进
    ``<root>/.seed/<skill_id>/``。之后所有「跳过点目录」的主机侧枚举（技能列表、个人技能
    中心、工作台计数、发布快照……）就再也看不到这些技能——它们并未丢失，只是隐形了。

    平台的规范布局仍是平铺 ``<root>/<skill_id>/``（新建技能也写在这里），所以这里
    **平铺优先、``.seed`` 回退**；两处同名时以平铺那份为准。

    另外，``.seed`` 里**与公共技能同 ID** 的目录会被排除（``exclude_platform_copies``）：
    那批 ``.seed`` 是 AgentScope 把 ``skill_paths``（公共 + 个人）**整体打包**的产物，
    公共技能副本的目录名就是公共技能 ID；若不排除，个人技能会因「同 ID 个人优先」把公共
    技能**冻结**成该用户的私有旧版，公共技能后续更新对他永久失效。

    注意**平铺**位置的同 ID 目录不受影响：平台设计上就支持「个人技能与公共技能同名、
    个人覆盖公共」（见 ``list_skill_metas`` 与 ``app.utils.skill_metadata``），用户主动
    创建的个人技能也只会落在平铺位置，因此不能用「同 ID」一概判为副本。
    """
    if not personal_root or not os.path.isdir(personal_root):
        return []
    platform_ids = platform_skill_id_set() if exclude_platform_copies else set()
    found: Dict[str, str] = {}
    seed_dir = os.path.join(personal_root, PERSONAL_SKILL_SEED_DIR_NAME)
    for base, from_seed in ((personal_root, False), (seed_dir, True)):
        try:
            names = sorted(os.listdir(base))
        except OSError:
            continue
        for name in names:
            if name.startswith(".") or not _validate_skill_id(name):
                continue
            if from_seed and name in platform_ids:
                continue
            skill_dir = os.path.join(base, name)
            if not os.path.isdir(skill_dir):
                continue
            found.setdefault(name, skill_dir)
    return sorted(found.items())


def resolve_personal_skill_dir(
    personal_root: str,
    skill_id: str,
    *,
    exclude_platform_copies: bool = True,
) -> Optional[str]:
    """定位单个个人技能目录（平铺优先、``.seed`` 回退）；找不到返回 ``None``。

    读/写端点都应经由本函数定位技能目录：否则对 ``.seed`` 里的存量技能，编辑动作会
    在平铺位置**新建一份副本**，出现「同一技能两份」。

    ``.seed`` 里与公共技能同 ID 的目录视为 AgentScope 打包的公共副本、返回 ``None``；
    **平铺位置的同 ID 目录照常返回**——平台支持「个人技能与公共技能同名、个人覆盖公共」，
    真实用户创建的个人技能就在这里（详见 ``iter_personal_skill_dirs``）。
    """
    if not personal_root or not skill_id:
        return None
    flat = os.path.join(personal_root, skill_id)
    if os.path.isdir(flat):
        return flat
    seed_candidate = os.path.join(personal_root, PERSONAL_SKILL_SEED_DIR_NAME, skill_id)
    if os.path.isdir(seed_candidate):
        if exclude_platform_copies and skill_id in platform_skill_id_set():
            return None
        return seed_candidate
    return None


def _scan_skill_dir(
    skills_dir: str,
    scope: str,
    *,
    allow_seed: bool = False,
) -> List[Dict[str, str]]:
    """扫描单个技能根目录，返回带 scope 的 meta 列表。

    ``allow_seed=True`` 时额外认 ``<root>/.seed/<skill_id>/``，**仅供个人技能源**使用
    （见 ``iter_personal_skill_dirs``）。公共技能库由平台自己维护、布局规范，不接受 seed。
    """
    if allow_seed:
        entries = iter_personal_skill_dirs(skills_dir)
    else:
        entries = []
        if skills_dir and os.path.exists(skills_dir):
            for item in sorted(os.listdir(skills_dir)):
                if item.startswith("."):
                    continue
                item_path = os.path.join(skills_dir, item)
                if os.path.isdir(item_path) and _validate_skill_id(item):
                    entries.append((item, item_path))

    metas: List[Dict[str, str]] = []
    for skill_id, item_path in entries:
        skill_md_path = os.path.join(item_path, "SKILL.md")
        meta = _parse_skill_frontmatter(skill_id, skill_md_path)
        meta["skill_md_path"] = skill_md_path
        meta["scope"] = scope
        metas.append(meta)
    return metas


def _skill_dir_fingerprint(
    skills_dir: str,
    *,
    allow_seed: bool = False,
) -> tuple[Any, ...]:
    """生成技能目录轻量指纹，用于检测元数据文件变化。

    ``allow_seed`` 必须与 ``_scan_skill_dir`` 保持一致，否则 seed 布局下的技能变更
    （或技能被搬进 ``.seed``）不会让缓存失效。
    """
    abs_dir = os.path.abspath(os.fspath(skills_dir))
    try:
        root_stat = os.stat(abs_dir)
    except OSError:
        return (abs_dir, None, ())

    if allow_seed:
        candidates = iter_personal_skill_dirs(abs_dir)
    else:
        try:
            names = sorted(os.listdir(abs_dir))
        except OSError:
            return (abs_dir, root_stat.st_mtime_ns, root_stat.st_size, ())
        candidates = [
            (item, os.path.join(abs_dir, item))
            for item in names
            if not item.startswith(".")
            and _validate_skill_id(item)
            and os.path.isdir(os.path.join(abs_dir, item))
        ]

    entries: list[tuple[Any, ...]] = []
    for item, item_path in candidates:
        skill_md_path = os.path.join(item_path, "SKILL.md")
        # 布局（"x" 还是 ".seed/x"）必须进指纹：shutil.move 在同一文件系统内会保留
        # mtime/size，技能被搬进或搬出 .seed 时若指纹不变，60 秒 TTL 内会命中指向
        # 已不存在路径的旧缓存（同步消费者会报「技能 X 缺少 SKILL.md」）。
        try:
            layout = os.path.relpath(item_path, abs_dir)
        except ValueError:
            layout = item_path
        try:
            stat = os.stat(skill_md_path)
            entries.append((item, layout, stat.st_mtime_ns, stat.st_size))
        except OSError:
            entries.append((item, layout, None, None))
    return (abs_dir, root_stat.st_mtime_ns, root_stat.st_size, tuple(entries))


def _copy_skill_metas(metas: tuple[Dict[str, str], ...]) -> List[Dict[str, str]]:
    return [dict(meta) for meta in metas]


def _scan_skill_dir_cached(
    skills_dir: str,
    scope: str,
    *,
    allow_seed: bool = False,
) -> List[Dict[str, str]]:
    if not skills_dir:
        return []

    abs_dir = os.path.abspath(os.fspath(skills_dir))
    fingerprint = _skill_dir_fingerprint(abs_dir, allow_seed=allow_seed)
    cache_key = (abs_dir, scope, allow_seed, fingerprint)
    now = time.monotonic()
    with _skill_meta_cache_lock:
        cached = _skill_meta_cache.get(cache_key)
        if cached and now - cached[0] <= _SKILL_META_CACHE_TTL_SECONDS:
            _skill_meta_cache.move_to_end(cache_key)
            return _copy_skill_metas(cached[1])
        if cached:
            _skill_meta_cache.pop(cache_key, None)

    metas = _scan_skill_dir(abs_dir, scope, allow_seed=allow_seed)
    frozen_metas = tuple(dict(meta) for meta in metas)
    with _skill_meta_cache_lock:
        _skill_meta_cache[cache_key] = (now, frozen_metas)
        _skill_meta_cache.move_to_end(cache_key)
        while len(_skill_meta_cache) > _SKILL_META_CACHE_MAX_ENTRIES:
            _skill_meta_cache.popitem(last=False)
    return _copy_skill_metas(frozen_metas)


def scan_skill_dir_cached(
    skills_dir: str,
    scope: str,
    *,
    allow_seed: bool = False,
) -> List[Dict[str, str]]:
    """带目录指纹缓存的技能元数据扫描（公开入口，供挂载解析与列表复用）。"""
    return _scan_skill_dir_cached(skills_dir, scope, allow_seed=allow_seed)


def clear_skill_meta_cache() -> None:
    """清理技能元数据缓存，供技能变更流程和测试调用。"""
    with _skill_meta_cache_lock:
        _skill_meta_cache.clear()


def list_skill_metas(
    user_info: Optional[Dict[str, Any]] = None,
    *,
    skills_custom: bool = False,
    allowed_global_skills: Optional[List[str]] = None,
    excluded_skill_ids: Optional[Iterable[str]] = None,
) -> List[Dict[str, str]]:
    """扫描技能目录，返回 id/name/description/scope 摘要列表。

    合并顺序：全局平台技能（scope=global）+ 当前用户个人技能（scope=personal）。
    若 ID 冲突，个人技能优先覆盖全局同 ID 技能。

    skills_custom=True 时，全局技能仅保留 allowed_global_skills 白名单中的项；
    个人技能始终合并。

    excluded_skill_ids 是权限侧的禁用集合（forbidden_skills）：只作用于全局技能，且发生在
    与个人技能合并之前——所以「禁平台那一份」不会牵连用户自建的同名个人技能。
    """
    try:
        from app.core.config import settings

        skills_dir = settings.SKILLS_DIR
    except Exception:
        return []

    global_metas = _scan_skill_dir_cached(skills_dir, SCOPE_GLOBAL)
    if excluded_skill_ids:
        forbidden = {str(s).strip() for s in excluded_skill_ids if str(s).strip()}
        if forbidden:
            global_metas = [m for m in global_metas if m.get("id") not in forbidden]
    if skills_custom:
        allowlist = {str(s).strip() for s in (allowed_global_skills or []) if str(s).strip()}
        global_metas = [m for m in global_metas if m.get("id") in allowlist]

    personal_metas: List[Dict[str, str]] = []
    personal_dir = get_user_personal_skills_dir(user_info)
    if personal_dir:
        # 个人技能源需要兼容历史遗留的 .seed 布局（见 iter_personal_skill_dirs）
        personal_metas = _scan_skill_dir_cached(
            personal_dir, SCOPE_PERSONAL, allow_seed=True
        )

    # 合并：个人技能优先（同 ID 覆盖全局）
    merged: Dict[str, Dict[str, str]] = {m["id"]: m for m in global_metas}
    for m in personal_metas:
        merged[m["id"]] = m

    return list(merged.values())


def list_global_skill_metas() -> List[Dict[str, str]]:
    """只返回平台（全局）技能摘要，不含任何用户个人技能。

    配置页的技能候选需要它：管理员禁用技能时管理的是**平台技能**，个人技能属于用户自己的
    资源，不该出现在候选里；显式走全局扫描，避免依赖"没有 user_info 就没有个人技能"的隐式行为。
    """
    try:
        from app.core.config import settings

        skills_dir = settings.SKILLS_DIR
    except Exception:
        return []

    return _scan_skill_dir_cached(skills_dir, SCOPE_GLOBAL)


def count_enabled_global_skills() -> int:
    """Count enabled platform (global) skills currently on disk."""
    return sum(
        1
        for meta in list_skill_metas()
        if is_skill_enabled(meta)
    )


def skill_filter_kwargs_from_config(agent_config: Any = None) -> Dict[str, Any]:
    """从 ChatConfig / dict 提取 skills 过滤参数。"""
    if agent_config is None:
        return {"skills_custom": False, "allowed_global_skills": None}
    if isinstance(agent_config, dict):
        skills_custom = bool(agent_config.get("skills_custom") or False)
        skills = agent_config.get("skills") or []
    else:
        skills_custom = bool(getattr(agent_config, "skills_custom", False) or False)
        skills = getattr(agent_config, "skills", None) or []
    if not isinstance(skills, list):
        skills = []
    return {
        "skills_custom": skills_custom,
        "allowed_global_skills": [str(s).strip() for s in skills if str(s).strip()],
    }


def _is_path_under_root(path: str, root: str) -> bool:
    try:
        abs_path = os.path.abspath(path)
        abs_root = os.path.abspath(root)
        return os.path.commonpath([abs_root, abs_path]) == abs_root
    except Exception:
        return False


def _allowed_skill_md_roots(user_info: Optional[Dict[str, Any]] = None) -> List[str]:
    from app.core.config import settings

    roots: List[str] = [os.path.abspath(settings.SKILLS_DIR)]
    personal_dir = get_user_personal_skills_dir(user_info)
    if personal_dir:
        roots.append(os.path.abspath(personal_dir))
    return roots


def _read_skill_md_if_allowed(
    skill_md_path: str,
    *,
    allowed_roots: List[str],
    max_bytes: int,
    skill_id: Optional[str] = None,
    include_resource_tree: bool = False,
) -> Optional[str]:
    abs_path = os.path.abspath(skill_md_path)
    if not any(_is_path_under_root(abs_path, root) for root in allowed_roots):
        return None
    if not os.path.exists(abs_path):
        return None
    # 读取上限放宽以拿到文件末尾的红线规则 / 输出格式，再统一用 70% 头 + 30% 尾裁剪。
    read_cap = max(max_bytes * 4, 262144) if max_bytes > 0 else 262144
    with open(abs_path, "r", encoding="utf-8", errors="ignore") as f:
        raw_content = f.read(read_cap + 1)
    read_truncated = len(raw_content) > read_cap
    raw_content = raw_content[:read_cap]
    content, omitted = clip_skill_text(raw_content, max_chars=max_bytes)
    prefix_lines: list[str] = []
    if omitted:
        prefix_lines.append(
            f"[已截断：保留开头与结尾，中间省略 {omitted} 字符；"
            "完整内容可按需通过 read_skill_instruction 或分片资源读取]"
        )
    elif read_truncated:
        prefix_lines.append(f"[文件超过 {read_cap} 字符，仅读取前 {read_cap} 字符]")
    if include_resource_tree:
        resource_tree = format_skill_resource_tree(os.path.dirname(abs_path))
        if resource_tree:
            target_id = skill_id or os.path.basename(os.path.dirname(abs_path))
            prefix_lines.append(
                "[技能自带资源（路径相对技能目录，可按需读取："
                f'read_skill_instruction(skill_id="{target_id}", path="<相对路径>")）]\n'
                f"{resource_tree}"
            )
    if prefix_lines:
        return "\n".join(prefix_lines) + "\n\n" + content
    return content


def load_skill_md_content(
    skill_id: str,
    max_bytes: int = 262144,
    *,
    user_info: Optional[Dict[str, Any]] = None,
    scope: Optional[str] = None,
    skill_md_path: Optional[str] = None,
    include_resource_tree: bool = False,
) -> Optional[str]:
    """读取技能 SKILL.md 全文；支持平台与个人目录。失败返回 None。"""
    if not _validate_skill_id(skill_id):
        return None
    try:
        from app.core.config import settings

        allowed_roots = _allowed_skill_md_roots(user_info)
        candidates: List[str] = []
        if skill_md_path:
            candidates.append(str(skill_md_path))

        resolved_scope = (scope or "").strip().lower() or None
        if user_info is not None:
            for meta in list_skill_metas(user_info=user_info):
                if meta.get("id") != skill_id:
                    continue
                if resolved_scope and (meta.get("scope") or SCOPE_GLOBAL) != resolved_scope:
                    continue
                meta_path = meta.get("skill_md_path")
                if meta_path:
                    candidates.append(str(meta_path))
                break

        if resolved_scope != SCOPE_PERSONAL:
            candidates.append(
                os.path.join(os.path.abspath(settings.SKILLS_DIR), skill_id, "SKILL.md")
            )

        seen: Set[str] = set()
        for candidate in candidates:
            abs_candidate = os.path.abspath(candidate)
            if abs_candidate in seen:
                continue
            seen.add(abs_candidate)
            content = _read_skill_md_if_allowed(
                abs_candidate,
                allowed_roots=allowed_roots,
                max_bytes=max_bytes,
                skill_id=skill_id,
                include_resource_tree=include_resource_tree,
            )
            if content is not None:
                return content
        return None
    except Exception as e:
        logger.warning("[Skills] Failed to read SKILL.md for %s: %s", skill_id, e)
        return None


def _extract_skill_hints(user_query: str) -> List[str]:
    hints: List[str] = []
    for pattern in _SKILL_USE_PATTERNS:
        match = pattern.search(user_query)
        if not match:
            continue
        hint = (match.group(1) or "").strip("「」\"' \t，,。.")
        if hint and len(hint) >= 2:
            hints.append(hint)
    return hints


def _score_skill_match(hint: str, meta: Dict[str, str]) -> float:
    hint_n = _normalize(hint)
    if not hint_n:
        return 0.0
    name_n = _normalize(meta.get("name", ""))
    id_n = _normalize(meta.get("id", ""))
    desc_n = _normalize(meta.get("description", ""))

    if hint_n == name_n or hint_n == id_n:
        return 1.0
    if hint_n in name_n or name_n in hint_n:
        return 0.9
    if hint_n in id_n or id_n in hint_n:
        return 0.85
    if hint_n in desc_n:
        return 0.7
    return 0.0


def _significant_terms(text: str) -> List[str]:
    """从文本提取可用于匹配的中文 2-gram 信号与英文/数字词（先剥离泛化停用词）。"""
    raw = str(text or "")
    if not raw:
        return []
    terms: List[str] = []
    seen: Set[str] = set()

    def _add(token: str) -> None:
        if not token or token in _SCAN_STOP_TERMS or token in seen:
            return
        seen.add(token)
        terms.append(token)

    for match in _CJK_RUN_RE.finditer(raw):
        cleaned = _CJK_STOP_SPLIT_RE.sub(" ", match.group(0))
        for seg in cleaned.split():
            if len(seg) < 2:
                continue
            if len(seg) == 2:
                _add(seg)
            else:
                for i in range(len(seg) - 1):
                    _add(seg[i : i + 2])

    for match in _ALNUM_WORD_RE.finditer(raw):
        token = match.group(0).lower()
        if token in _SCAN_STOP_TERMS:
            continue
        _add(token)
        # 兼容连字符/下划线复合词拆分（如 pdf-helper -> pdf, helper）
        if "-" in token or "_" in token:
            for part in re.split(r"[_-]+", token):
                if len(part) >= 2:
                    _add(part)

    return terms


def _term_hits(query_term: str, skill_term: str) -> bool:
    if len(query_term) < 2 or len(skill_term) < 2:
        return False
    # 中文 2-gram 直接精确匹配，避免双字片段互相误包含；英文词保留子串兼容。
    if query_term.isascii() and skill_term.isascii():
        return query_term in skill_term or skill_term in query_term
    return query_term == skill_term


# 2 字泛化业务/通用名词：不能仅凭单个 2 字核心词出现在长句里就直接命中扫描，避免误触发
_GENERIC_TWO_CHAR_CORE_TERMS = frozenset({
    "数据", "代码", "系统", "文件", "文档", "信息", "内容", "问题", "助手", "工具",
    "服务", "管理", "分析", "处理", "查询", "报告", "记录", "列表", "测试", "项目",
    "任务", "用户", "平台", "接口", "脚本", "知识", "搜索", "流程", "配置", "监控",
})


def lexical_relevance_score(user_query: str, meta: Dict[str, str]) -> float:
    """按 name/description/id/tags 与用户问题的关键词重叠打分（无向量、无 LLM）。"""
    query = extract_plain_user_query(user_query)
    if not query:
        return 0.0

    name = (meta.get("name") or "").strip()
    description = (meta.get("description") or "").strip()
    skill_id = (meta.get("id") or "").strip()
    tags = str(meta.get("tags") or meta.get("keywords") or "").strip()
    query_n = _normalize(query)
    name_n = _normalize(name)

    if len(name_n) >= 2 and name_n in query_n:
        return 0.92
    if len(name_n) >= 2 and query_n in name_n:
        return 0.88

    # 技能名去掉「查询/技能/预报/流程」等后缀后的核心词出现在问题中（如「用户列表」⊂「查一下用户列表」）
    name_core = re.sub(r"(查询|查数|技能|工具|助手|服务|预报|流程)$", "", name_n)
    if len(name_core) >= 3 and name_core in query_n:
        return 0.9
    if (
        len(name_core) == 2
        and name_core not in _GENERIC_TWO_CHAR_CORE_TERMS
        and name_core in query_n
    ):
        # 2 字具体领域实词（如「天气」「发票」）稳定命中扫描，泛化词（如「代码」「数据」）不走捷径
        return 0.65

    query_terms = _significant_terms(query)
    if not query_terms:
        return 0.0

    name_terms = _significant_terms(name)
    desc_terms = _significant_terms(description)
    tag_terms = _significant_terms(tags)
    id_terms = [t for t in re.split(r"[_-]+", skill_id.lower()) if len(t) >= 2]
    skill_terms = list(dict.fromkeys(name_terms + desc_terms + tag_terms + id_terms))
    if not skill_terms:
        return 0.0

    skill_term_set = set(skill_terms)
    name_term_set = set(name_terms)
    ascii_skill_terms = [t for t in skill_terms if t.isascii()]
    ascii_name_terms = [t for t in name_terms if t.isascii()]

    matched = 0
    name_matched = 0
    for query_term in query_terms:
        hit_skill = query_term in skill_term_set or (
            query_term.isascii()
            and any(_term_hits(query_term, st) for st in ascii_skill_terms)
        )
        if hit_skill:
            matched += 1
        hit_name = query_term in name_term_set or (
            query_term.isascii()
            and any(_term_hits(query_term, nt) for nt in ascii_name_terms)
        )
        if hit_name:
            name_matched += 1

    if matched == 0:
        return 0.0

    query_cov = matched / len(query_terms)
    name_cov = (name_matched / len(name_terms)) if name_terms else 0.0
    # 长句中仅偶发撞上 1 个泛化名称 2-gram 时不虚高；多信号或较高查询覆盖时才融合 name_cov
    if name_matched >= 2 or query_cov >= 0.3:
        score = max(query_cov, 0.55 * name_cov)
    else:
        score = query_cov
    if name_matched > 0 and (name_matched >= 2 or query_cov >= 0.3):
        score = min(1.0, score + min(0.18, 0.15 * name_matched))
    elif matched >= 2:
        score = min(0.74, score + min(0.15, 0.05 * matched))
    return score


def is_main_general_agent(agent_config: Any) -> bool:
    """是否为主助手（通用对话兜底 Agent）；仅该 Agent 启用技能库自动扫描。"""
    if agent_config is None:
        return False

    agent_id = (getattr(agent_config, "agent_id", None) or "").strip()
    if agent_id == MAIN_GENERAL_AGENT_ID:
        return True

    slug_candidates = {
        (getattr(agent_config, "agent_name", None) or "").strip().lower(),
        agent_id.lower(),
    }
    return bool(slug_candidates & MAIN_GENERAL_AGENT_SLUGS)


def should_scan_skills_for_query(user_query: str) -> bool:
    """是否应对本轮用户问题执行技能库扫描（流程门禁）。"""
    query = extract_plain_user_query(user_query)
    if len(query) < 4:
        return False

    from app.services.ai.intent_service import looks_like_greeting, looks_like_meta_action

    if looks_like_greeting(query) or looks_like_meta_action(query):
        return False
    return True


def scan_relevant_skills(
    user_query: str,
    *,
    user_info: Optional[Dict[str, Any]] = None,
    exclude_ids: Optional[Set[str]] = None,
    max_results: int = 1,
    min_score: float = 0.45,
    skills_custom: bool = False,
    allowed_global_skills: Optional[List[str]] = None,
    excluded_skill_ids: Optional[Iterable[str]] = None,
) -> List[Dict[str, Any]]:
    """扫描技能库，按关键词相关度返回候选技能（流程化自动匹配，非语义向量）。

    在每轮用户提问后、挂载/口头解析均未命中时由 AgentService 调用。
    个人技能（scope=personal）相关度加权 +0.05，优先被匹配。
    """
    query = extract_plain_user_query(user_query)
    if not query or not should_scan_skills_for_query(query):
        return []

    metas = list_skill_metas(
        user_info=user_info,
        skills_custom=skills_custom,
        allowed_global_skills=allowed_global_skills,
        excluded_skill_ids=excluded_skill_ids,
    )
    if not metas:
        return []

    excluded = exclude_ids or set()
    scored: List[tuple[float, Dict[str, str]]] = []
    for meta in metas:
        skill_id = meta.get("id")
        if not skill_id or skill_id in excluded:
            continue
        # 过滤未启用的技能，防止每次都全部加载
        if not is_skill_enabled(meta):
            continue
        score = lexical_relevance_score(query, meta)
        # 个人技能加权，鼓励优先匹配用户自定义技能
        if meta.get("scope") == SCOPE_PERSONAL and score > 0:
            score = min(1.0, score + 0.05)
        if score >= min_score:
            scored.append((score, meta))

    scored.sort(key=lambda item: item[0], reverse=True)
    results: List[Dict[str, Any]] = []
    for score, meta in scored:
        item = dict(meta)
        item["match_score"] = round(score, 3)
        item["match_source"] = "scan"
        results.append(item)
        if len(results) >= max_results:
            break
    return results


def resolve_skills_from_query(
    user_query: str,
    *,
    user_info: Optional[Dict[str, Any]] = None,
    max_results: int = 2,
    skills_custom: bool = False,
    allowed_global_skills: Optional[List[str]] = None,
    excluded_skill_ids: Optional[Iterable[str]] = None,
) -> List[Dict[str, Any]]:
    """从用户问题中解析技能引用并按 name/id 匹配可用技能。

    例如「使用用户列表查询技能查询一次」→ 匹配 name 含「用户列表查询」的技能。
    """
    query = extract_plain_user_query(user_query)
    if not query:
        return []

    hints = _extract_skill_hints(query)
    if not hints and not any(token in query.lower() for token in ("技能", "skill")):
        return []

    metas = list_skill_metas(
        user_info=user_info,
        skills_custom=skills_custom,
        allowed_global_skills=allowed_global_skills,
        excluded_skill_ids=excluded_skill_ids,
    )
    if not metas:
        return []

    scored: List[tuple[float, Dict[str, str]]] = []
    for hint in hints:
        for meta in metas:
            if not is_skill_enabled(meta):
                continue
            score = _score_skill_match(hint, meta)
            if score >= 0.7:
                scored.append((score, meta))

    # 兜底：用户直接在句子里提到技能 display_name 或 skill_id（不区分大小写/空格）
    if not scored:
        query_n = _normalize(query)
        for meta in metas:
            if not is_skill_enabled(meta):
                continue
            name_n = _normalize(meta.get("name") or "")
            id_n = _normalize(meta.get("id") or "")
            if (len(name_n) >= 2 and name_n in query_n) or (len(id_n) >= 2 and id_n in query_n):
                scored.append((0.75, meta))

    scored.sort(key=lambda item: item[0], reverse=True)
    seen_ids: set[str] = set()
    results: List[Dict[str, Any]] = []
    for score, meta in scored:
        skill_id = meta["id"]
        if skill_id in seen_ids:
            continue
        seen_ids.add(skill_id)
        item = dict(meta)
        item["match_source"] = "mention"
        item["match_score"] = round(score, 3)
        results.append(item)
        if len(results) >= max_results:
            break
    return results
