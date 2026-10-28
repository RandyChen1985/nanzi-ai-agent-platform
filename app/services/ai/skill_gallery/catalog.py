"""技能广场目录的加载与校验。

目录数据是**代码资产**：``catalog.json`` 存卡片元数据，``docs/<id>.md`` 存介绍正文，
随代码一起评审、一起发布，不进数据库。

两条设计约束：

1. **不复制第三方正文**。收录的技能多数是 ``Proprietary`` 许可（例如
   ``anthropics/skills`` 明确禁止把材料复制到服务之外再分发），所以这里只存
   元数据与我们自己撰写的介绍，正文由 ``repository_url`` / ``source_url`` 外链出去。
   要新增技能包（而不是介绍），走平台既有的技能上传/发布流程。
2. **目录坏了不能拖垮服务**。``catalog.json`` 是手写文件，谁都会写错一个逗号。因此
   加载层单条容错：坏掉的条目记 warning 后跳过，其余照常展示；另有
   ``validate_catalog`` 供测试与运维做严格校验（一次性报出全部问题）。
"""

from __future__ import annotations

import json
import logging
import os
from typing import Any, Dict, List, Optional

logger = logging.getLogger(__name__)

CATALOG_DIR = os.path.dirname(os.path.abspath(__file__))
CATALOG_PATH = os.path.join(CATALOG_DIR, "catalog.json")

#: 卡片列表与详情共用的必填字段。``intro_doc`` 不是必填——没有介绍文档时详情页
#: 只展示 summary 与 highlights，不至于整张卡片不可用。
_REQUIRED_FIELDS = (
    "id",
    "name",
    "slug",
    "summary",
    "category",
    "install_command",
    "install_prompt",
)

#: 目录缓存。文件按代码资产对待，运行期不会变，所以只在进程内缓存一次；
#: 测试可以直接调用 ``_load_catalog(use_cache=False)`` 绕过。
_CACHE: Optional[Dict[str, Any]] = None


def _read_raw() -> Dict[str, Any]:
    with open(CATALOG_PATH, "r", encoding="utf-8") as handle:
        return json.load(handle)


def _validate_skill_id(value: Any) -> Optional[str]:
    """技能 ID 必须与平台技能 ID 同口径（英文/数字/中划线/下划线）。"""
    text = str(value or "").strip()
    if not text:
        return "id 不能为空"
    allowed = set("abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789-_")
    if any(char not in allowed for char in text):
        return f"id「{text}」只能包含英文、数字、中划线、下划线"
    return None


def _normalize_entry(raw: Dict[str, Any]) -> Dict[str, Any]:
    """把一条原始记录规整成前端可用的结构（缺省字段补空，不抛异常）。"""
    entry = dict(raw)
    entry["id"] = str(entry.get("id") or "").strip()
    entry["name"] = str(entry.get("name") or entry["id"]).strip()
    entry["slug"] = str(entry.get("slug") or entry["id"]).strip()
    entry["summary"] = str(entry.get("summary") or "").strip()
    entry["category"] = str(entry.get("category") or "其他").strip()
    entry["highlights"] = [
        str(item).strip() for item in (entry.get("highlights") or []) if str(item).strip()
    ]
    entry["tags"] = [
        str(item).strip() for item in (entry.get("tags") or []) if str(item).strip()
    ]
    entry["icon"] = str(entry.get("icon") or "🧩").strip()
    entry["official"] = bool(entry.get("official", False))
    entry["author"] = str(entry.get("author") or "").strip()
    entry["repository"] = str(entry.get("repository") or "").strip()
    entry["repository_url"] = str(entry.get("repository_url") or "").strip()
    entry["source_url"] = str(entry.get("source_url") or "").strip()
    entry["license"] = str(entry.get("license") or "").strip()
    entry["version"] = str(entry.get("version") or "").strip()
    entry["install_command"] = str(entry.get("install_command") or "").strip()
    entry["install_prompt"] = str(entry.get("install_prompt") or "").strip()
    # **不回退**到个人版：那段文案写的是「收进我的个人技能库」，回退后管理员点
    # 「安装到平台」拿到的却是一份只装个人库的提示词，按钮与内容对不上且没有任何报错。
    # 缺省时留空，由接口按权限决定下发，前端会提示「未获取到平台安装提示词」。
    entry["install_prompt_global"] = str(entry.get("install_prompt_global") or "").strip()
    entry["updated_at"] = str(entry.get("updated_at") or "").strip()
    entry["intro_doc"] = str(entry.get("intro_doc") or "").strip()
    entry.pop("_intro", None)
    return entry


def _read_intro(entry: Dict[str, Any]) -> str:
    """读取介绍正文。路径限制在 catalog 目录内，防止手写路径穿越读别的文件。"""
    relative = entry.get("intro_doc") or ""
    if not relative:
        return ""
    candidate = os.path.normpath(os.path.join(CATALOG_DIR, relative))
    if not candidate.startswith(CATALOG_DIR + os.sep):
        logger.warning("[SkillGallery] 介绍文档路径越出目录，已忽略：%s", relative)
        return ""
    try:
        with open(candidate, "r", encoding="utf-8") as handle:
            return handle.read()
    except OSError as exc:
        logger.warning("[SkillGallery] 读取介绍文档失败 %s：%s", relative, exc)
        return ""


def validate_catalog() -> List[str]:
    """严格校验目录，返回全部问题（空列表 = 通过）。供测试与运维脚本使用。"""
    problems: List[str] = []
    try:
        raw = _read_raw()
    except Exception as exc:  # noqa: BLE001 - 文件坏了要报清楚而不是吞掉
        return [f"catalog.json 读取或解析失败：{exc}"]

    skills = raw.get("skills")
    if not isinstance(skills, list) or not skills:
        return ["catalog.json 的 skills 必须是至少含一条记录的非空数组"]

    seen: Dict[str, int] = {}
    for index, item in enumerate(skills):
        if not isinstance(item, dict):
            problems.append(f"第 {index} 条不是对象")
            continue
        label = item.get("id") or f"第 {index} 条"
        for field in _REQUIRED_FIELDS:
            if not str(item.get(field) or "").strip():
                problems.append(f"{label}：缺少必填字段 {field}")
        id_problem = _validate_skill_id(item.get("id"))
        if id_problem:
            problems.append(f"{label}：{id_problem}")
        highlights = item.get("highlights")
        if highlights is not None and not isinstance(highlights, list):
            problems.append(f"{label}：highlights 必须是数组")
        skill_id = str(item.get("id") or "").strip()
        if skill_id:
            if skill_id in seen:
                problems.append(f"{label}：id 与第 {seen[skill_id]} 条重复")
            else:
                seen[skill_id] = index
        intro = str(item.get("intro_doc") or "").strip()
        if intro:
            candidate = os.path.normpath(os.path.join(CATALOG_DIR, intro))
            if not candidate.startswith(CATALOG_DIR + os.sep):
                problems.append(f"{label}：intro_doc 越出 catalog 目录")
            elif not os.path.isfile(candidate):
                problems.append(f"{label}：intro_doc 文件不存在（{intro}）")
    return problems


def _load_catalog(use_cache: bool = True) -> Dict[str, Any]:
    global _CACHE
    if use_cache and _CACHE is not None:
        return _CACHE

    try:
        raw = _read_raw()
    except Exception as exc:  # noqa: BLE001
        logger.error("[SkillGallery] catalog.json 解析失败，技能广场将为空：%s", exc)
        catalog: Dict[str, Any] = {"version": 0, "skills": [], "categories": []}
        _CACHE = catalog
        return catalog

    entries: List[Dict[str, Any]] = []
    for item in raw.get("skills") or []:
        if not isinstance(item, dict):
            continue
        entry = _normalize_entry(item)
        id_problem = _validate_skill_id(entry["id"])
        if id_problem:
            logger.warning("[SkillGallery] 跳过非法技能条目：%s", id_problem)
            continue
        if not entry["summary"]:
            logger.warning("[SkillGallery] 技能 %s 缺少 summary，卡片将只有标题", entry["id"])
        entries.append(entry)

    categories: List[str] = []
    for entry in entries:
        if entry["category"] not in categories:
            categories.append(entry["category"])

    catalog = {
        "version": raw.get("version", 1),
        "source_note": str(raw.get("source_note") or "").strip(),
        "skills": entries,
        "categories": categories,
    }
    _CACHE = catalog
    return catalog


def list_skills() -> List[Dict[str, Any]]:
    """返回全部卡片（不含介绍正文，避免列表接口被正文撑大）。"""
    return [dict(entry) for entry in _load_catalog()["skills"]]


def list_categories() -> List[str]:
    return list(_load_catalog().get("categories") or [])


def source_note() -> str:
    return str(_load_catalog().get("source_note") or "")


def catalog_version() -> int:
    """目录版本号（``catalog.json`` 顶层的 ``version``）。供接口生成 ETag。"""
    try:
        return int(_load_catalog().get("version") or 0)
    except (TypeError, ValueError):
        return 0


def get_skill(skill_id: str) -> Optional[Dict[str, Any]]:
    """按 ID 取详情（含介绍正文 ``intro``）。找不到返回 ``None``。"""
    target = str(skill_id or "").strip()
    if not target:
        return None
    for entry in _load_catalog()["skills"]:
        if entry["id"] == target:
            detail = dict(entry)
            detail["intro"] = _read_intro(entry)
            return detail
    return None


__all__ = [
    "CATALOG_PATH",
    "get_skill",
    "catalog_version",
    "list_categories",
    "list_skills",
    "source_note",
    "validate_catalog",
]
