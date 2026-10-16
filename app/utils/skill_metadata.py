"""SKILL.md Frontmatter 解析与技能附件隐式指令构建。"""
from __future__ import annotations

import logging
import os
import re
from typing import Any, Dict, List, Optional

logger = logging.getLogger(__name__)

USER_MESSAGE_CONTEXT_DIVIDER = "\n\n---\n\n"

# `enabled` 的真值白名单。与 SkillPolicy._parse_bool_config 同一语义：只认这些为「启用」。
_TRUTHY_FLAGS = frozenset({"1", "true", "yes", "on"})


def is_skill_enabled(meta: Optional[Dict[str, Any]], default: bool = True) -> bool:
    """技能是否启用——全平台**唯一**的判定来源。

    ``enabled`` 在 frontmatter / JSON 里本来是字符串（``"true"`` / ``"false"``），但人工编辑
    SKILL.md 时完全可能写成 ``False``、``TRUE``。此前有三套口径：运行时 ``skill_resolver``
    用大小写敏感的 ``!= "false"``、技能注入用白名单解析、而智能体作用域候选清单用大小写
    不敏感的 ``.lower() != "false"``。

    最直接的后果是「列不出来所以禁不掉」：SKILL.md 里手写 ``enabled: False`` 时运行时
    **照样加载**（敏感比较认不出），唯独候选清单把它剔除，于是管理员在弹框里看不到它，
    自然也无法禁用它。三套合并成一套后，这类口径漂移不会再出现。
    """
    if not meta:
        return default
    raw = meta.get("enabled")
    if raw is None:
        return default
    if isinstance(raw, bool):
        return raw
    return str(raw).strip().lower() in _TRUTHY_FLAGS


def parse_skill_frontmatter(skill_id: str, skill_md_path: str) -> Dict[str, str]:
    """从 SKILL.md 顶部 YAML Frontmatter 提取 name / description 等元数据。"""
    meta: Dict[str, str] = {"id": skill_id, "name": skill_id, "description": "", "enabled": "true"}
    if not os.path.exists(skill_md_path):
        return meta
    try:
        with open(skill_md_path, "r", encoding="utf-8", errors="ignore") as f:
            content = f.read(8192)
        # 容忍 UTF-8 BOM：zip 导入不会剥掉它，而 ^--- 匹配不到 BOM 开头的文件，会让
        # enabled 静默回落成 "true"（技能永远显示启用）。
        content = content.lstrip("\ufeff")
        match = re.match(r"^---\s*\n(.*?)\n---\s*\n", content, re.DOTALL)
        if match:
            for line in match.group(1).splitlines():
                if ":" in line:
                    key, value = line.split(":", 1)
                    meta[key.strip().lower()] = value.strip().strip('"').strip("'")
    except Exception:
        pass
    if "enabled" not in meta:
        meta["enabled"] = "true"
    return meta


def toggle_skill_enabled_in_file(skill_md_path: str, enabled: bool) -> bool:
    """在 SKILL.md 的 YAML Frontmatter 中修改或插入 enabled 状态。"""
    if not os.path.exists(skill_md_path):
        return False
    try:
        with open(skill_md_path, "r", encoding="utf-8", errors="ignore") as f:
            content = f.read()
        # 与 parse_skill_frontmatter 同样的 BOM 容忍：这里若匹配不到 frontmatter 会
        # 直接 return False，调用方（toggle 端点）就把「禁不掉」报成 500。
        content = content.lstrip("\ufeff")

        match = re.match(r"^---\s*\n(.*?)\n---\s*\n", content, re.DOTALL)
        if match:
            yaml_block = match.group(1)
            rest_content = content[match.end():]
            
            new_lines = []
            has_enabled = False
            for line in yaml_block.splitlines():
                if line.strip().lower().startswith("enabled:"):
                    new_lines.append(f"enabled: {str(enabled).lower()}")
                    has_enabled = True
                else:
                    new_lines.append(line)
            
            if not has_enabled:
                new_lines.append(f"enabled: {str(enabled).lower()}")
                
            new_yaml_block = "\n".join(new_lines)
            new_content = f"---\n{new_yaml_block}\n---\n{rest_content}"
            
            with open(skill_md_path, "w", encoding="utf-8") as f:
                f.write(new_content)
            return True
    except Exception:
        pass
    return False


def format_skill_meta_text(meta: Dict[str, str]) -> str:
    """将 Frontmatter 格式化为隐式指令中的 meta 摘要。"""
    name = (meta.get("name") or meta.get("id") or "").strip()
    desc = (meta.get("description") or "").strip()
    parts: List[str] = []
    if name:
        parts.append(f"name: {name}")
    if desc:
        parts.append(f"description: {desc}")
    return ", ".join(parts) if parts else "（无 SKILL.md Frontmatter 元数据）"


# 挂载提示的固定开头：新文案、历史升级与清洗侧的身份识别都以它为锚点。
SKILL_HINT_PREFIX = "用户本轮已调用生态技能工作流："
# 历史格式的后半句：`…，对应的物理描述文件绝对路径是：/app/data/skills/<id>/SKILL.md。`
_LEGACY_HINT_TAIL = "，对应的物理描述文件绝对路径是："


def _skill_directory_hint(skill_dir: Optional[str] = None) -> str:
    """技能目录怎么找：调用方确知真实目录就给路径，否则指向技能资源读取工具。"""
    if skill_dir:
        return f"该技能目录的真实路径是：{skill_dir}"
    return (
        "如需读取该技能附带的脚本或资源（references/、scripts/ 等），"
        "请调用 read_skill_instruction 获取该技能的资源清单，再用 path 参数按需读取"
        "（例如 path=\"references/rules.md\"）；技能目录布局不保证可由技能 ID 推导，不要猜测固定路径"
    )


def _skill_hint_headline(skill_id: str, display_name: str, directory_hint: str) -> str:
    """挂载提示的说明行（不含 meta 行）——新文案与历史升级共用这一份口径。"""
    return (
        f"{SKILL_HINT_PREFIX}{display_name}（技能 ID：{skill_id}）。"
        f"其 SKILL.md 正文已随本轮上下文预载，本轮直接按预载指令执行，无需再读取技能文件；"
        f"{directory_hint}。"
    )


def build_skill_attachment_hint(
    skill_id: str,
    *,
    skill_name: Optional[str] = None,
    meta_override: Optional[Dict[str, Any]] = None,
    skill_dir: Optional[str] = None,
) -> str:
    """构建技能挂载隐式指令（技能 id、Frontmatter meta 与「正文已预载」说明）。

    **刻意不再拼 SKILL.md 的绝对路径。** 曾经写死的
    ``/app/data/skills/<技能 id>/SKILL.md`` 只在「平台容器 + 公共技能根」这一种组合下成立：

    - 个人技能的真身在 ``agent_workspaces/<user_key>/skills/<id>/``（见
      ``skill_resolver.get_user_personal_skills_dir``）；
    - Docker/K8s 沙箱里连 ``/app/data`` 都没有，只有挂到 ``/workspace`` 的用户工作区。

    模型照抄这条路径去 Read 只会拿到 ``File does not exist``——而技能正文本来就由
    ``SkillInjector`` 预载进上下文，这次读取纯属多余。只有需要读技能附带的脚本/资源时才
    需要目录，那时用平台内置的 ``list_accessible_directories`` 取真实位置；
    ``skill_dir`` 是给「调用方确知真实目录」场景的显式覆盖（不给就退回清单工具指引）。
    """
    from app.core.config import settings

    skill_md_path = os.path.join(settings.SKILLS_DIR, skill_id, "SKILL.md")
    if meta_override:
        meta = {
            "id": skill_id,
            "name": str(meta_override.get("name") or skill_id),
            "description": str(meta_override.get("description") or ""),
        }
    else:
        meta = parse_skill_frontmatter(skill_id, skill_md_path)

    display_name = (skill_name or meta.get("name") or skill_id).replace(" (技能)", "")
    meta_text = format_skill_meta_text(meta)
    headline = _skill_hint_headline(skill_id, display_name, _skill_directory_hint(skill_dir))
    return f"{headline}\nskills meta 为：{meta_text}"


def enrich_messages_with_skill_meta(messages: List[Dict[str, Any]]) -> None:
    """若最后一条 user 消息挂载技能但隐式指令缺少 meta，则补全或重建技能上下文块。"""
    if not messages:
        return
    last = messages[-1]
    files = last.get("files") or []
    skill_files = [f for f in files if f.get("type") == "skill"]
    if not skill_files:
        return
    content = last.get("content") or ""
    if "skills meta 为：" in content:
        return

    hints = []
    for sf in skill_files:
        skill_id = sf.get("url")
        if not skill_id:
            continue
        skill_name = (sf.get("filename") or "").replace(" (技能)", "")
        meta_override = sf.get("skillMeta") or sf.get("skill_meta")
        hints.append(
            build_skill_attachment_hint(
                skill_id,
                skill_name=skill_name,
                meta_override=meta_override,
            )
        )
    if not hints:
        return

    context_block = "\n\n".join(hints)
    if USER_MESSAGE_CONTEXT_DIVIDER in content:
        user_part, _ctx = content.split(USER_MESSAGE_CONTEXT_DIVIDER, 1)
        user_part = user_part.strip()
        if user_part:
            last["content"] = f"{user_part}{USER_MESSAGE_CONTEXT_DIVIDER}{context_block}"
        else:
            last["content"] = f"{USER_MESSAGE_CONTEXT_DIVIDER}{context_block}"
    elif content.strip():
        last["content"] = f"{content.strip()}{USER_MESSAGE_CONTEXT_DIVIDER}{context_block}"
    else:
        last["content"] = f"{USER_MESSAGE_CONTEXT_DIVIDER}{context_block}"


# 隐式指令里的「这条提示指向哪个技能」有两种形状，清洗时必须都认：
#
# 1. **新格式**（本模块 build_skill_attachment_hint 与前端 useChatAttachments 同源生成）：
#    `（技能 ID：<技能 id>）`——提示里不再出现任何文件系统路径；
# 2. **历史格式**（禁用这个特性上线之前就已落库的老会话）：`/app/data/skills/<技能 id>/SKILL.md`。
#
# 只认新格式会让老会话的提示清洗失效（那段 content 会随上下文窗口进模型），
# 只认旧格式则新提示完全无法定位技能——两条都必须留着。
SKILL_ID_MARKER_RE = re.compile(r"技能 ID[:：]\s*([^\s，,。；;、）)]+)")
SKILL_MD_ABSOLUTE_PATH_RE = re.compile(r"/app/data/skills/([^/\s]+)/SKILL\.md")

# 上下文块内部各条附件提示之间的分隔（前端 contextLines.join("\n\n") 与本模块 hints 拼接同源）
_CONTEXT_HINT_SEPARATOR = "\n\n"

FORBIDDEN_SKILL_ATTACHMENT_NOTICE = (
    "用户本轮挂载的技能「{skill_id}」已被管理员禁用，不可使用；"
    "其技能描述文件不可读取，禁止尝试读取或检索该技能的任何文件。"
)


def forbidden_skill_attachment_notice(skill_id: str) -> str:
    """被禁技能的挂载说明：明确"不可用、不可读"，且**不带任何绝对路径**。"""
    return FORBIDDEN_SKILL_ATTACHMENT_NOTICE.format(skill_id=str(skill_id or "").strip())


def _skill_attachment_id(item: Any) -> str:
    if not isinstance(item, dict) or item.get("type") != "skill":
        return ""
    return str(item.get("url") or "").strip()


# 技能来源口径（scope）的唯一取值。前端技能 chip 与 ``resource_scope`` 用
# ``global``/``personal``（对应 ``skill_resolver.SCOPE_GLOBAL/SCOPE_PERSONAL``），
# 历史载荷与文档里还出现过同义的 ``public``，这里一并认。
SKILL_SCOPE_PERSONAL = "personal"
SKILL_SCOPE_PUBLIC = "public"
_PERSONAL_SCOPE_ALIASES = frozenset({"personal"})
_PUBLIC_SCOPE_ALIASES = frozenset({"public", "global", "platform"})


def normalize_skill_scope(raw: Any, *, source: str = "") -> Optional[str]:
    """技能来源标记的**唯一**解析入口：``"personal"`` / ``"public"`` / ``None``（未指定）。

    为什么需要这一层：
    - 前端 ``EmbedChat.handleSelectSkill`` 把 scope 同时写进附件与 ``skillMeta``，取值是
      ``global``/``personal``；聊天接口又用 ``msg.model_dump()`` 把它带回服务端；
    - 历史载荷/文档里还有 ``public`` 这种同义写法，个人侧只认 ``personal``；
    - scope 决定"这一份技能属于谁"（个人技能按个人库解析、且永不属于公共禁用集合），
      因此任何**未知取值都按未指定处理**（等同公共口径）而绝不抛异常：它来自客户端载荷，
      不能因为一个拼错的值让整轮对话 500。

    ``source`` 只用于日志定位（如 ``ChatFile.scope``）；未知取值记 warning，便于发现契约漂移。
    """
    if raw is None:
        return None
    text = str(raw).strip().lower()
    if not text:
        return None
    if text in _PERSONAL_SCOPE_ALIASES:
        return SKILL_SCOPE_PERSONAL
    if text in _PUBLIC_SCOPE_ALIASES:
        return SKILL_SCOPE_PUBLIC
    logger.warning(
        "[SkillScope] 未知技能 scope=%r（来源：%s），按未指定（公共口径）处理",
        raw,
        source or "unknown",
    )
    return None


def is_personal_skill_scope(raw: Any) -> bool:
    """scope 是否指向个人技能（唯一判定来源：:func:`normalize_skill_scope`）。

    只认 ``personal``：其余取值（``global``/``public``/未知/缺省）都是公共口径。
    这样即使载荷写错，方向也是"当成公共技能"（既有行为），不会误把公共技能当个人技能放行。
    """
    return normalize_skill_scope(raw) == SKILL_SCOPE_PERSONAL


def is_personal_skill_attachment(item: Any) -> bool:
    """挂载项是否指向**个人技能**（个人技能与公共技能可能同名）。

    公开出来给 `services.ai.skills.policy` 复用：两处过滤（上下文清洗、显式挂载丢弃）
    必须同口径——只禁公共技能，同名个人技能不连坐。曾经因为各写一份，显式挂载那条
    漏了 scope 判定，同名个人技能被静默丢弃。

    scope 可能出现在附件顶层（``ChatFile.scope``）或 ``skillMeta``/``skill_meta`` 里
    （前端两处都发，历史消息不一定），因此两处都看；判定统一走
    :func:`is_personal_skill_scope`，不在这里再写一份字符串比较。
    """
    if not isinstance(item, dict):
        return False
    scopes = [item.get("scope")]
    meta = item.get("skillMeta") or item.get("skill_meta")
    if isinstance(meta, dict):
        scopes.append(meta.get("scope"))
    return any(is_personal_skill_scope(scope) for scope in scopes)


def _legacy_hint_display_name(line: str, skill_id: str) -> str:
    """从历史提示行里取显示名（取不到就退回技能 id）。"""
    body = line[len(SKILL_HINT_PREFIX):] if line.startswith(SKILL_HINT_PREFIX) else line
    name = body.split(_LEGACY_HINT_TAIL, 1)[0].replace(" (技能)", "").strip()
    return name or skill_id


def upgrade_legacy_skill_mount_hints(messages: List[Dict[str, Any]]) -> int:
    """把历史消息里带 SKILL.md 死路径的挂载提示就地升级成新文案，返回改写的消息条数。

    老会话里那段提示带着 ``/app/data/skills/<id>/SKILL.md``——对个人技能与 Docker/K8s
    沙箱都不成立，模型每轮都可能照抄着读一次、失败一次再绕过。这里只改**内存里的这一份
    快照**，不回写数据库：历史是用户自己的记录，读取侧净化即可（与禁用清洗同一原则）。

    只重写说明行，紧随的 ``skills meta 为：`` 行原样保留——重建 meta 要反过来解析旧文本，
    稍有歧义就会把 description 弄丢。
    """
    if not messages:
        return 0
    upgraded = 0
    for message in messages:
        if not isinstance(message, dict):
            continue
        content = message.get("content")
        if not isinstance(content, str) or not content:
            continue
        # 绝大多数消息不含旧提示：先整串快速判断一次，避免每条消息都拆行
        if SKILL_MD_ABSOLUTE_PATH_RE.search(content) is None:
            continue
        lines = content.split("\n")
        changed = False
        for index, line in enumerate(lines):
            if not line.startswith(SKILL_HINT_PREFIX):
                continue
            matched = SKILL_MD_ABSOLUTE_PATH_RE.search(line)
            if matched is None:
                continue
            skill_id = matched.group(1)
            lines[index] = _skill_hint_headline(
                skill_id,
                _legacy_hint_display_name(line, skill_id),
                _skill_directory_hint(),
            )
            changed = True
        if changed:
            message["content"] = "\n".join(lines)
            upgraded += 1
    return upgraded


# 兼容旧调用点的私有别名（实现只有上面那一份）
_skill_attachment_is_personal = is_personal_skill_attachment


def _skill_ids_in_hint_line(line: str) -> set[str]:
    """提取一行挂载提示指向的技能 id（新格式标记 + 历史绝对路径两种都要认）。"""
    return {
        str(skill_id).strip()
        for skill_id in (
            list(SKILL_ID_MARKER_RE.findall(line))
            + list(SKILL_MD_ABSOLUTE_PATH_RE.findall(line))
        )
        if str(skill_id).strip()
    }


def _rewrite_forbidden_skill_hints(content: str, blocked_ids: set[str]) -> str:
    if not blocked_ids:
        return content
    parts = content.split(_CONTEXT_HINT_SEPARATOR)
    rewritten: list[str] = []
    changed = False
    for part in parts:
        hit_ids: set[str] = set()
        # 只摘掉**属于被禁技能的那些行**，绝不整段替换：分段是按空行切的，用户完全可能
        # 在同段里追加自己的话（"把 /app/data/skills/gamma/SKILL.md 的流程跑一遍"）。
        # 整段替换会连用户原文一起删掉——模型看不到原始问题、user_query 被换成禁令文案
        # 影响路由与技能扫描，而且改写结果会写进历史、不可恢复。
        kept_lines: list[str] = []
        # 挂载提示是「说明行 + skills meta 行」一组：说明行被摘掉后，紧跟的 meta 行
        # 同样属于该技能（描述也是要一起收口的内容），必须一起摘掉。
        drop_following_meta = False
        for line in part.split("\n"):
            line_ids = _skill_ids_in_hint_line(line) & blocked_ids
            if line_ids:
                hit_ids |= line_ids
                drop_following_meta = True
                continue
            if drop_following_meta and line.strip().startswith("skills meta 为："):
                drop_following_meta = False
                continue
            drop_following_meta = False
            kept_lines.append(line)
        if not hit_ids:
            rewritten.append(part)
            continue
        changed = True
        notice = "\n".join(
            forbidden_skill_attachment_notice(skill_id) for skill_id in sorted(hit_ids)
        )
        body = "\n".join(kept_lines).strip()
        rewritten.append(f"{body}\n{notice}" if body else notice)
    if not changed:
        return content
    return _CONTEXT_HINT_SEPARATOR.join(rewritten)


def sanitize_forbidden_skill_context(
    messages: List[Dict[str, Any]],
    forbidden_ids: Optional[Any],
) -> int:
    """就地清洗被禁技能的挂载项与提示文本，返回被移除的附件个数。

    必须在 ``memory_service.add_message``（落库）与 ``enrich_messages_with_skill_meta``
    （依据 files 重建提示）**之前**调用：挂载提示会点名技能（新格式是「技能 ID」，
    历史格式还带着 SKILL.md 绝对路径），落库之后这段文本会随上下文窗口反复进模型。

    两条处理，缺一不可：
    1. ``files`` 里被禁技能附件直接移除（模型看不到"已挂载"这件事）；
    2. 已生成的提示文本改写成明确的不可用说明——只删附件不改文本的话，历史里旧提示
       仍会让模型以为该技能可用（历史格式还会把绝对路径一并带进模型）。

    个人技能（scope=personal）不受影响：产品口径是"禁的是平台发布的那一份"，
    用户自建的同名技能照旧可用，因此同名 id 先按 scope 排除再算禁用集合。
    "哪些附件属于个人技能"依据 :func:`is_personal_skill_attachment`（顶层 ``scope`` 与
    ``skillMeta.scope`` 两处都看，取值由 :func:`normalize_skill_scope` 归一），
    与 `services.ai.skills.policy.drop_forbidden_skill_items` 同一判定——两处若各写一套，
    显式挂载那条就会漏掉 scope 判定、把同名个人技能静默丢掉。
    """
    forbidden = {
        str(skill_id).strip()
        for skill_id in (forbidden_ids or ())
        if str(skill_id).strip()
    }
    if not forbidden or not messages:
        return 0

    removed = 0
    for message in messages:
        if not isinstance(message, dict):
            continue
        files = message.get("files")
        skill_items = (
            [item for item in files if isinstance(item, dict) and item.get("type") == "skill"]
            if isinstance(files, list)
            else []
        )
        personal_ids = {
            _skill_attachment_id(item)
            for item in skill_items
            if _skill_attachment_is_personal(item)
        }
        blocked_ids = forbidden - personal_ids
        if not blocked_ids:
            continue

        if isinstance(files, list):
            kept = [
                item
                for item in files
                if not (
                    isinstance(item, dict)
                    and item.get("type") == "skill"
                    and _skill_attachment_id(item) in blocked_ids
                )
            ]
            if len(kept) != len(files):
                removed += len(files) - len(kept)
                message["files"] = kept

        content = message.get("content")
        if isinstance(content, str) and content:
            rewritten = _rewrite_forbidden_skill_hints(content, blocked_ids)
            if rewritten != content:
                message["content"] = rewritten
    return removed
