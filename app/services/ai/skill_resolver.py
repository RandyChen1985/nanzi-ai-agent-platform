"""从用户自然语言中解析并匹配可用技能（口头指定 + 关键词扫描）。"""
from __future__ import annotations

import os
import re
import logging
from typing import Any, Dict, List, Optional, Set

logger = logging.getLogger(__name__)

SCOPE_GLOBAL = "global"
SCOPE_PERSONAL = "personal"

MAIN_GENERAL_AGENT_ID = "sys-agent-chat"
# 与 RouterService.FALLBACK_AGENT_NAMES 保持一致（主助手 slug 别名）
MAIN_GENERAL_AGENT_SLUGS = frozenset({"assistant", "main", "general-chat"})

# 扫描时从用户问题中剔除的泛化词，避免「查一下」「帮我」误命中
_SCAN_STOP_TERMS = frozenset({
    "一下", "帮我", "帮忙", "请", "查询", "查", "看看", "看下", "了解", "知道",
    "怎么", "如何", "什么", "哪些", "有没有", "能否", "可以", "吗", "呢", "啊",
    "的", "了", "是", "在", "有", "和", "与", "或", "及", "把", "给", "我", "你",
    "the", "and", "for", "with", "this", "that", "what", "how", "please", "help",
})

_SKILL_USE_PATTERNS = [
    re.compile(r"使用(?:一下|下)?[「\"']?(.+?)[」\"']?(?:技能|skill)", re.I),
    re.compile(r"用(?:一下|下)?[「\"']?(.+?)[」\"']?(?:技能|skill)", re.I),
    re.compile(r"(?:执行|运行|触发|加载|套用|按照)(?:一下|下)?[「\"']?(.+?)[」\"']?(?:技能|skill)", re.I),
    re.compile(r"(?:技能|skill)[「\"']?(.+?)[」\"']?(?:执行|运行|查|查询)", re.I),
]


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


def _scan_skill_dir(skills_dir: str, scope: str) -> List[Dict[str, str]]:
    """扫描单个技能根目录，返回带 scope 的 meta 列表。"""
    metas: List[Dict[str, str]] = []
    if not skills_dir or not os.path.exists(skills_dir):
        return metas
    for item in sorted(os.listdir(skills_dir)):
        if item.startswith("."):
            continue
        item_path = os.path.join(skills_dir, item)
        if not os.path.isdir(item_path):
            continue
        if not _validate_skill_id(item):
            continue
        skill_md_path = os.path.join(item_path, "SKILL.md")
        meta = _parse_skill_frontmatter(item, skill_md_path)
        meta["skill_md_path"] = skill_md_path
        meta["scope"] = scope
        metas.append(meta)
    return metas


def list_skill_metas(
    user_info: Optional[Dict[str, Any]] = None,
    *,
    skills_custom: bool = False,
    allowed_global_skills: Optional[List[str]] = None,
) -> List[Dict[str, str]]:
    """扫描技能目录，返回 id/name/description/scope 摘要列表。

    合并顺序：全局平台技能（scope=global）+ 当前用户个人技能（scope=personal）。
    若 ID 冲突，个人技能优先覆盖全局同 ID 技能。

    skills_custom=True 时，全局技能仅保留 allowed_global_skills 白名单中的项；
    个人技能始终合并。
    """
    try:
        from app.core.config import settings

        skills_dir = settings.SKILLS_DIR
    except Exception:
        return []

    global_metas = _scan_skill_dir(skills_dir, SCOPE_GLOBAL)
    if skills_custom:
        allowlist = {str(s).strip() for s in (allowed_global_skills or []) if str(s).strip()}
        global_metas = [m for m in global_metas if m.get("id") in allowlist]

    personal_metas: List[Dict[str, str]] = []
    personal_dir = get_user_personal_skills_dir(user_info)
    if personal_dir:
        personal_metas = _scan_skill_dir(personal_dir, SCOPE_PERSONAL)

    # 合并：个人技能优先（同 ID 覆盖全局）
    merged: Dict[str, Dict[str, str]] = {m["id"]: m for m in global_metas}
    for m in personal_metas:
        merged[m["id"]] = m

    return list(merged.values())


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


def load_skill_md_content(skill_id: str, max_bytes: int = 262144) -> Optional[str]:
    """读取技能 SKILL.md 全文；失败返回 None。"""
    if not _validate_skill_id(skill_id):
        return None
    try:
        from app.core.config import settings

        skills_dir = os.path.abspath(settings.SKILLS_DIR)
        skill_md_path = os.path.abspath(os.path.join(skills_dir, skill_id, "SKILL.md"))
        if os.path.commonpath([skills_dir, skill_md_path]) != skills_dir:
            return None
        if not os.path.exists(skill_md_path):
            return None
        with open(skill_md_path, "r", encoding="utf-8", errors="ignore") as f:
            return f.read(max_bytes)
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
    """从文本提取可用于匹配的中文片段与英文词。"""
    terms: List[str] = []
    for match in re.finditer(r"[\u4e00-\u9fff]{2,}|[a-zA-Z][a-zA-Z0-9_-]{1,}", text or ""):
        token = match.group(0).lower()
        if token in _SCAN_STOP_TERMS:
            continue
        terms.append(token)
    return terms


def _term_hits(query_term: str, skill_term: str) -> bool:
    if len(query_term) < 2 or len(skill_term) < 2:
        return False
    return query_term in skill_term or skill_term in query_term


def lexical_relevance_score(user_query: str, meta: Dict[str, str]) -> float:
    """按 name/description/id 与用户问题的关键词重叠打分（无向量、无 LLM）。"""
    query = (user_query or "").strip()
    if not query:
        return 0.0

    name = (meta.get("name") or "").strip()
    description = (meta.get("description") or "").strip()
    skill_id = (meta.get("id") or "").strip()
    query_n = _normalize(query)
    name_n = _normalize(name)

    if len(name_n) >= 2 and name_n in query_n:
        return 0.92
    if len(name_n) >= 2 and query_n in name_n:
        return 0.88

    # 技能名去掉「查询/技能」等后缀后的核心词出现在问题中（如「用户列表」⊂「查一下用户列表」）
    name_core = re.sub(r"(查询|查数|技能|工具|助手|服务)$", "", name_n)
    if len(name_core) >= 2 and name_core in query_n:
        return 0.9

    query_terms = _significant_terms(query)
    if not query_terms:
        return 0.0

    name_terms = _significant_terms(name)
    desc_terms = _significant_terms(description)
    id_terms = [t for t in re.split(r"[_-]+", skill_id.lower()) if len(t) >= 2]
    skill_terms = list(dict.fromkeys(name_terms + desc_terms + id_terms))
    if not skill_terms:
        return 0.0

    matched = 0
    name_matched = 0
    for query_term in query_terms:
        if any(_term_hits(query_term, skill_term) for skill_term in skill_terms):
            matched += 1
        if any(_term_hits(query_term, name_term) for name_term in name_terms):
            name_matched += 1

    if matched == 0:
        return 0.0

    score = matched / len(query_terms)
    if name_matched > 0:
        score = min(1.0, score + 0.2 * name_matched)
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
    query = (user_query or "").strip()
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
) -> List[Dict[str, Any]]:
    """扫描技能库，按关键词相关度返回候选技能（流程化自动匹配，非语义向量）。

    在每轮用户提问后、挂载/口头解析均未命中时由 AgentService 调用。
    个人技能（scope=personal）相关度加权 +0.05，优先被匹配。
    """
    query = (user_query or "").strip()
    if not query or not should_scan_skills_for_query(query):
        return []

    metas = list_skill_metas(
        user_info=user_info,
        skills_custom=skills_custom,
        allowed_global_skills=allowed_global_skills,
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
        if meta.get("enabled", "true") == "false":
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
) -> List[Dict[str, Any]]:
    """从用户问题中解析技能引用并按 name/id 匹配可用技能。

    例如「使用用户列表查询技能查询一次」→ 匹配 name 含「用户列表查询」的技能。
    """
    query = (user_query or "").strip()
    if not query:
        return []

    hints = _extract_skill_hints(query)
    if not hints and not any(token in query.lower() for token in ("技能", "skill")):
        return []

    metas = list_skill_metas(
        user_info=user_info,
        skills_custom=skills_custom,
        allowed_global_skills=allowed_global_skills,
    )
    if not metas:
        return []

    scored: List[tuple[float, Dict[str, str]]] = []
    for hint in hints:
        for meta in metas:
            if meta.get("enabled", "true") == "false":
                continue
            score = _score_skill_match(hint, meta)
            if score >= 0.7:
                scored.append((score, meta))

    # 兜底：用户直接在句子里提到技能 display_name
    if not scored:
        for meta in metas:
            if meta.get("enabled", "true") == "false":
                continue
            name = (meta.get("name") or "").strip()
            if len(name) >= 2 and name in query:
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
