import os
import os.path
import logging
import asyncio
import subprocess
import psutil
import json
import re
import fnmatch
from typing import Optional
from app.services.ai.tools.tool_compat import tool
from app.services.ai.code_execution_service import run_shell_command_capture
from app.services.ai.runtime.shell_deletion_policy import assess_shell_deletion
from app.services.skill_rename_service import SkillRenameError, validate_skill_id
from app.utils.skill_metadata import is_skill_enabled, parse_skill_frontmatter

logger = logging.getLogger(__name__)

def validate_safe_path(path: str) -> str:
    """
    安全校验：限制物理读写仅允许在合法的 data 安全目录下，防止路径穿越 (..)。
    """
    abs_path = os.path.abspath(path)
    
    # 获得当前项目的绝对路径和典型的 docker 挂载路径
    project_root = os.path.abspath(os.path.dirname(os.path.dirname(os.path.dirname(os.path.dirname(os.path.dirname(__file__))))))
    
    # 合法的目录前缀列表
    allowed_prefixes = [
        os.path.join(project_root, "data"),
        "/app/data"
    ]
    
    # 额外兼容：支持直接操作相对路径的 data 目录
    allowed_prefixes.append(os.path.abspath("data"))
    
    # 额外允许技能物理目录，以便 create_skills 等工具能正常操作宿主机 ~/.agent/skills 或容器 /app/data/skills
    try:
        from app.core.config import settings
        if getattr(settings, "SKILLS_DIR", None):
            allowed_prefixes.append(os.path.abspath(settings.SKILLS_DIR))
    except Exception:
        pass
    
    is_safe = False
    for prefix in allowed_prefixes:
        try:
            if os.path.commonpath([abs_path, prefix]) == prefix:
                is_safe = True
                break
        except ValueError:
            continue
            
    if not is_safe:
        raise ValueError(f"安全拦截：路径越界！仅允许访问 data 及其子目录下的文件。当前请求路径为 {path}")
        
    return abs_path


def _validate_skill_id(skill_id: str) -> str:
    """校验技能 ID（格式、长度 1-128、平台保留字），口径收敛到 skill_rename_service。

    此前这里是第二份正则：没有长度上限、也不认保留字，于是模型能创建出一个叫
    ``stats``/``export`` 的技能——它会命中更早注册的字面路由，技能永远打不开。
    """
    try:
        return validate_skill_id(skill_id)
    except SkillRenameError as exc:
        raise ValueError(str(exc)) from exc


def _parse_skill_frontmatter(skill_id: str, skill_md_path: str) -> dict:
    """读取 SKILL.md 元数据；解析实现只有 :func:`app.utils.skill_metadata.parse_skill_frontmatter` 一份。

    此前这里是第三份 frontmatter 解析（只认 name/description 两行、默认描述
    「暂无技能描述」），与列表页、运行时的结果可能不一致。统一后默认描述为空字符串；
    需要展示兜底文案的调用方自己补。
    """
    return parse_skill_frontmatter(skill_id, skill_md_path)


#: 技能文件单次读取上限（SKILL.md 与技能包内资源同一口径）
SKILL_RESOURCE_MAX_BYTES = 262144
#: 技能资源清单的条目 / 深度上限：技能包导入上限是 2000 个文件，清单无上限会写爆上下文
_SKILL_RESOURCE_LIST_MAX_ENTRIES = 200
_SKILL_RESOURCE_LIST_MAX_DEPTH = 4


def _skill_allowed_roots(user_info: Optional[dict]) -> list[str]:
    """读取技能文件允许的物理根：平台技能库 + 当前用户个人技能目录。

    ``abspath`` 与 ``realpath`` 两种形式都放进去：部署里 ``SKILLS_DIR`` 自身可能就是
    软链接，只留一种会把合法技能判成"路径越界"；任一命中即通过。
    """
    roots: list[str] = []

    def _add(raw: Optional[str]) -> None:
        if not raw:
            return
        for candidate in (os.path.abspath(raw), os.path.realpath(os.path.abspath(raw))):
            if candidate and candidate not in roots:
                roots.append(candidate)

    try:
        from app.core.config import settings

        _add(getattr(settings, "SKILLS_DIR", None))
    except Exception:
        pass
    if user_info:
        try:
            from app.services.ai.skill_resolver import get_user_personal_skills_dir

            _add(get_user_personal_skills_dir(user_info))
        except Exception:
            pass
    return roots


def _path_within_roots(path: str, roots: list[str]) -> bool:
    """路径是否落在任一允许根内（不解析软链接，调用方自行决定传不传 realpath）。"""
    for root in roots:
        if not root:
            continue
        try:
            if os.path.commonpath([os.path.abspath(path), root]) == root:
                return True
        except (ValueError, TypeError):
            continue
    return False


def _decode_skill_resource(blob: bytes) -> Optional[str]:
    """技能资源按 UTF-8 文本解码；二进制（含 NUL 或非 UTF-8）返回 ``None``。"""
    if b"\x00" in blob:
        return None
    try:
        return blob.decode("utf-8")
    except UnicodeDecodeError:
        return None


def _format_skill_resource_tree(skill_dir: str) -> str:
    """技能包内资源清单（路径相对技能目录，排除 SKILL.md 与隐藏项）。

    这是"有哪些资源可读"的提示，不是目录树工具：条目数与深度都有上限，超限在末尾
    显式说明，避免大技能包把上下文写爆。没有任何附带资源时返回空串。
    """
    entries: list[str] = []
    truncated = False
    root = os.path.abspath(skill_dir)
    for current, dirnames, filenames in os.walk(root, followlinks=False):
        depth = 0 if current == root else os.path.relpath(current, root).count(os.sep) + 1
        dirnames[:] = sorted(
            name for name in dirnames if not name.startswith(".") and name != "__pycache__"
        )
        if depth >= _SKILL_RESOURCE_LIST_MAX_DEPTH:
            if dirnames:
                truncated = True
            dirnames[:] = []
        rel_dir = os.path.relpath(current, root)
        for filename in sorted(filenames):
            if filename.startswith(".") or filename == "SKILL.md":
                continue
            if len(entries) >= _SKILL_RESOURCE_LIST_MAX_ENTRIES:
                truncated = True
                break
            rel = filename if rel_dir == os.curdir else os.path.join(rel_dir, filename)
            entries.append(rel.replace(os.sep, "/"))
        if len(entries) >= _SKILL_RESOURCE_LIST_MAX_ENTRIES:
            break
    if not entries and not truncated:
        return ""
    lines = [f"- {entry}" for entry in entries]
    if truncated:
        lines.append(
            f"- …（清单已截断：最多列 {_SKILL_RESOURCE_LIST_MAX_ENTRIES} 条 / "
            f"{_SKILL_RESOURCE_LIST_MAX_DEPTH} 层；需要完整目录请用 directory_tree_navigator 或 Bash）"
        )
    return "\n".join(lines)


def _read_skill_resource_file(
    *,
    skill_id: str,
    skill_dir: str,
    rel_path: str,
    scope: str,
    allowed_roots: list[str],
) -> str:
    """读取技能包内单个资源文件（相对技能目录）；所有越界情形一律拒绝。"""
    raw = str(rel_path or "").strip().replace("\\", "/")
    if not raw:
        return "错误：path 不能为空；如需列出技能资源，直接调用 read_skill_instruction(skill_id)。"
    # 控制字符一律拒绝：技能包是用户可上传内容，带 \x1b 等转义序列的文件名会被原样回显到
    # 日志与模型上下文里（终端控制序列注入）。这里只拦控制字符，不学窄字符白名单——那会把
    # 中文名（references/边界规则.md）与带空格、括号的合法资源名一起挡掉。
    if any(ord(char) < 0x20 or ord(char) == 0x7F for char in raw):
        return "错误：path 含控制字符，技能资源路径只允许可打印字符。"
    if raw.startswith("/") or os.path.isabs(raw):
        return "错误：path 必须是技能目录内的相对路径（例如 references/calc-rules.md）。"
    parts = [part for part in raw.split("/") if part not in ("", os.curdir)]
    if not parts or any(part == os.pardir for part in parts):
        return "错误：path 不允许包含 .. 越界。"

    real_dir = os.path.realpath(skill_dir)
    try:
        target = os.path.abspath(os.path.join(real_dir, *parts))
    except (OSError, ValueError):
        return f"错误：非法的技能资源路径 {raw}。"
    # 技能包导入时 symlink/hardlink 一律不落盘（safe_extract_archive），这里保持一致口径：
    # 只读真实文件，避免软链接把读取引到同根下的其他技能、或绕过下面的越界判定。
    if os.path.islink(target):
        return f"错误：技能资源 {raw} 是软链接，技能入口只读取技能目录内的真实文件。"
    # 双重判定：解析软链接后仍在技能目录内，且技能目录本身仍在允许的技能根下。
    real_target = os.path.realpath(target)
    if not _path_within_roots(real_target, [real_dir]) or not _path_within_roots(
        real_target, allowed_roots
    ):
        return "错误：技能资源路径越界。"
    if os.path.isdir(real_target):
        return f"错误：{raw} 是目录，不是文件；不传 path 可列出该技能的资源清单。"
    if not os.path.isfile(real_target):
        return f"错误：技能 {skill_id} 中没有资源文件 {raw}。"

    try:
        with open(real_target, "rb") as handle:
            blob = handle.read(SKILL_RESOURCE_MAX_BYTES + 1)
    except OSError as exc:
        return f"错误：读取技能资源失败，原因: {exc}"

    truncated = len(blob) > SKILL_RESOURCE_MAX_BYTES
    text = _decode_skill_resource(blob[:SKILL_RESOURCE_MAX_BYTES])
    if text is None:
        return (
            f"错误：技能资源 {raw} 不是文本文件（二进制或非 UTF-8），本工具只读文本资源；"
            "如需在技能目录内处理二进制，请使用 Bash 或代码执行类工具。"
        )
    header = f"[技能读取成功: {skill_id}/{raw} · scope={scope}]"
    if truncated:
        header += f"\n[已截断至 {SKILL_RESOURCE_MAX_BYTES} 字节]"
    return f"{header}\n{text}"


def _is_forbidden_shell_command(command: str) -> str | None:
    """
    Best-effort high-risk command blocker.
    Returns a human-readable reason string if forbidden, otherwise None.
    """
    cmd = (command or "").strip()
    if not cmd:
        return None

    # Normalize whitespace for more stable matching.
    normalized = re.sub(r"\s+", " ", cmd).strip().lower()

    # NOTE: We intentionally keep this conservative (block obvious destructive classes).
    patterns: list[tuple[str, str]] = [
        # Root filesystem destructive deletes
        (r"(^|[;&|]\s*)rm(\s+[-\w,=]+)*\s+/\s*($|[;&|])", "禁止删除根目录 /"),
        (r"(^|[;&|]\s*)rm(\s+[-\w,=]+)*\s+/\*\s*($|[;&|])", "禁止删除根目录下所有内容 /*"),
        (r"(^|[;&|]\s*)rm(\s+[-\w,=]+)*\s+/(\.\.\s*/)+\s*($|[;&|])", "禁止路径穿越删除"),
        (r"(^|[;&|]\s*)rm(\s+[-\w,=]+)*\s+~\s*($|[;&|])", "禁止删除用户主目录 ~"),
        (r"(^|[;&|]\s*)rm(\s+[-\w,=]+)*\s+\$home\b", "禁止删除 HOME 目录"),
        (r"(^|[;&|]\s*)rm(\s+[-\w,=]+)*\s+\$\(pwd\)\b", "禁止删除当前工作目录"),

        # Disk / filesystem destructive operations
        (r"\bmkfs(\.\w+)?\b", "禁止格式化文件系统 mkfs"),
        (r"\b(mount|umount)\b\s+/(dev|proc|sys|boot|etc)\b", "禁止对关键系统挂载点执行 mount/umount"),
        (r"\b(fdisk|sfdisk|cfdisk|parted|gdisk)\b", "禁止磁盘分区操作"),
        (r"\b(pvcreate|vgcreate|lvcreate|lvremove|vgremove|pvremove)\b", "禁止 LVM 破坏性操作"),
        (r"\b(zpool\s+destroy|zfs\s+destroy)\b", "禁止 ZFS 销毁操作"),

        # Raw disk writes / wipes
        (r"\bdd\b.*\bof=/dev/", "禁止 dd 写入块设备"),
        (r"\bdd\b.*\bif=/dev/(zero|random|urandom)\b", "禁止 dd 读取随机设备进行写盘"),
        (r"\bwipefs\b", "禁止 wipefs 擦除文件系统签名"),
        (r"\b(shred|blkdiscard)\b", "禁止 shred/blkdiscard 擦盘"),

        # System power / critical service disruption
        (r"\b(shutdown|reboot|poweroff|halt|init\s+[06])\b", "禁止关机/重启/停机"),

        # Kill critical processes (common footguns)
        # Allow normal process management (kill/pkill/killall), but protect PID 1 and a few core daemons
        (r"\bkill(\s+-\S+)*\s+1\b", "禁止对 PID 1 执行 kill"),
        (r"\bkillall\b\s+-9\s+(systemd|init|sshd|dockerd)\b", "禁止 killall -9 强杀关键系统进程"),
        (r"\bpkill\b\s+-9\b.*\b(systemd|init|sshd|dockerd)\b", "禁止 pkill -9 强杀关键系统进程"),

        # Fork bomb
        (r":\(\)\s*\{\s*:\s*\|\s*:\s*&\s*\}\s*;\s*:", "禁止 fork bomb"),
    ]

    for pat, reason in patterns:
        if re.search(pat, normalized, flags=re.IGNORECASE):
            return reason

    return None


@tool
async def list_available_skills() -> str:
    """
    列出当前会话可用的技能摘要（已启用），供智能体按 name/description 判断是否需要读取具体技能。
    规则与运行时一致：未自定义时=全部已启用公共技能+当前用户个人技能；
    自定义开启时=智能体勾选的公共技能+当前用户个人技能；
    两种情况都还要再扣掉权限侧禁用（forbidden_skills）的公共技能。
    只返回技能 ID、名称、描述与 scope，不读取完整 SKILL.md。
    """
    try:
        from app.core.context import get_current_agent_context
        from app.utils.context import current_user_info
        from app.services.ai.skill_resolver import list_skill_metas
        from app.services.ai.skills.policy import get_forbidden_skill_ids

        user_info = current_user_info.get()
        ctx = get_current_agent_context()
        skills_custom = bool(getattr(ctx, "skills_custom", False)) if ctx else False
        allowed = list(getattr(ctx, "skills", None) or []) if ctx else []
        forbidden_skill_ids = await get_forbidden_skill_ids()

        metas = list_skill_metas(
            user_info=user_info,
            skills_custom=skills_custom,
            allowed_global_skills=allowed if skills_custom else None,
            excluded_skill_ids=forbidden_skill_ids,
        )
        skills = []
        for meta in metas:
            if not is_skill_enabled(meta):
                continue
            skills.append(
                {
                    "id": meta.get("id"),
                    "name": meta.get("name") or meta.get("id"),
                    "description": meta.get("description") or "",
                    "scope": meta.get("scope") or "global",
                }
            )
        return json.dumps(skills, ensure_ascii=False, indent=2)
    except Exception as e:
        return f"错误：列出技能失败，原因: {str(e)}"


@tool
async def read_skill_instruction(skill_id: str, path: Optional[str] = None) -> str:
    """
    读取技能指令与技能包内资源。调用前应先使用 list_available_skills 确认技能存在且适用。
    不传 path：读取该技能 SKILL.md 全文，并附上技能包内其他资源文件清单（references/、scripts/ 等）。
    传 path：读取技能包内的单个资源文件，如 path="references/calc-rules.md"、"scripts/run.py"。
    仅允许读取当前会话可见技能（含自定义白名单过滤后的公共技能与个人技能），
    被权限侧禁用（forbidden_skills）的公共技能一律拒绝；path 不可越出该技能目录。

    Args:
        skill_id: 技能唯一 ID，仅允许英文、数字、中划线及下划线。
        path: 可选。技能包内的相对路径（相对技能根目录）。不传则读 SKILL.md 并列出资源清单。
    """
    try:
        from app.core.context import get_current_agent_context
        from app.utils.context import current_user_info
        from app.services.ai.skill_resolver import list_skill_metas
        from app.services.ai.skills.policy import get_forbidden_skill_ids

        safe_skill_id = _validate_skill_id(skill_id)
        user_info = current_user_info.get()
        ctx = get_current_agent_context()
        skills_custom = bool(getattr(ctx, "skills_custom", False)) if ctx else False
        allowed = list(getattr(ctx, "skills", None) or []) if ctx else []
        forbidden_skill_ids = await get_forbidden_skill_ids()

        metas = list_skill_metas(
            user_info=user_info,
            skills_custom=skills_custom,
            allowed_global_skills=allowed if skills_custom else None,
            excluded_skill_ids=forbidden_skill_ids,
        )
        matched = next(
            (
                m
                for m in metas
                if m.get("id") == safe_skill_id and is_skill_enabled(m)
            ),
            None,
        )
        if not matched:
            return (
                f"错误：技能 {safe_skill_id} 不存在、已禁用，"
                "或不在当前智能体可用 Skills 范围内。"
            )

        skill_md_path = matched.get("skill_md_path")
        if not skill_md_path or not os.path.exists(skill_md_path):
            return f"错误：技能 {safe_skill_id} 缺少 SKILL.md。"

        # 路径安全：限制在全局 SKILLS_DIR 或个人 skills 根目录下
        abs_md = os.path.abspath(skill_md_path)
        allowed_roots = _skill_allowed_roots(user_info)
        if not _path_within_roots(abs_md, allowed_roots):
            return "错误：技能路径越界。"

        skill_dir = os.path.dirname(abs_md)
        scope = matched.get("scope") or "global"

        # 按需读技能包内资源：模型不必自己探索 .seed 布局，也不会绕开技能白名单
        if path is not None and str(path).strip():
            return _read_skill_resource_file(
                skill_id=safe_skill_id,
                skill_dir=skill_dir,
                rel_path=str(path),
                scope=scope,
                allowed_roots=allowed_roots,
            )

        with open(abs_md, "r", encoding="utf-8", errors="ignore") as f:
            content = f.read(SKILL_RESOURCE_MAX_BYTES)

        header = f"[技能读取成功: {safe_skill_id}/SKILL.md · scope={scope}]"
        resource_tree = _format_skill_resource_tree(skill_dir)
        if resource_tree:
            header += (
                "\n[技能自带资源（路径相对技能目录，可按需读取："
                f'read_skill_instruction(skill_id="{safe_skill_id}", path="<相对路径>")）]\n'
                f"{resource_tree}"
            )
        return f"{header}\n{content}"
    except ValueError as e:
        return f"错误：非法技能 ID，{str(e)}"
    except Exception as e:
        return f"错误：读取技能失败，原因: {str(e)}"

@tool
def read_file(path: str, offset: int = 0, limit: int = 262144, tail: bool = False) -> str:
    """
    读取本地安全沙箱文件。用户要求查看、打开、读取、检查文件内容、查看日志文件、tail 日志或分析已知文件路径时应触发本工具。
    以分页、截断、倒序（Tail）的方式读取，限制单次读取的最大字节数以防止大模型 Token 暴涨，只允许读取 data 目录下的安全沙箱文件。
    
    Args:
        path: 目标文件路径 (如 data/uploads/logs.txt)。
        offset: 分页读取的开始字节偏移量。默认为 0。
        limit: 单次读取的最大字节长度限制，默认 256KB。
        tail: 为 True 时，直接读取文件末尾 limit 长度的内容。
    """
    try:
        abs_path = validate_safe_path(path)
        if not os.path.exists(abs_path):
            return f"错误：文件 {path} 不存在。"
        if os.path.isdir(abs_path):
            return f"错误：{path} 是一个目录，请使用 directory_tree_navigator 工具进行查看。"

        file_size = os.path.getsize(abs_path)
        
        with open(abs_path, "r", encoding="utf-8", errors="ignore") as f:
            if tail:
                if file_size > limit:
                    f.seek(file_size - limit)
                content = f.read()
                return f"[Tail 读取成功 ({file_size} 字节中的最后 {len(content.encode('utf-8'))} 字节)]\n{content}"
            else:
                f.seek(offset)
                content = f.read(limit)
                is_eof = (offset + len(content.encode('utf-8')) >= file_size)
                return f"[分页读取成功 (文件总长 {file_size} 字节，本次读取偏移量 {offset}，读取 {len(content.encode('utf-8'))} 字节，EOF={is_eof})]\n{content}"
    except Exception as e:
        return f"读取文件失败: {str(e)}"


@tool
async def read_image(path: str, question: str = "") -> str:
    """
    读取并解析本地安全沙箱或工作区中的图片文件（PNG/JPEG/WEBP/GIF/BMP 等）。
    当用户或智能体需要查看、检查、识别本地图片、提取图中文字/表格、分析图表曲线、查看代码生成的图片产物或进行视觉问答时，应触发本工具。
    系统将使用配置的默认多模态视觉模型对图片进行深度理解。

    Args:
        path: 图片文件的物理或相对路径 (如 data/uploads/screenshot.png 或 data/agent_workspaces/.../chart.png)。
        question: 可选针对该图片的具体问题或提取指令 (如 '提取图中的表格数据'、'检查折线图是否有异常'；留空时提取完整图文描述)。
    """
    try:
        abs_path = validate_safe_path(path)
        if not os.path.exists(abs_path):
            return f"错误：图片文件 {path} 不存在。"
        if os.path.isdir(abs_path):
            return f"错误：{path} 是一个目录，不是图片文件。"

        ext = os.path.splitext(abs_path)[1].lower()
        if ext not in {".png", ".jpg", ".jpeg", ".webp", ".gif", ".bmp"}:
            return f"错误：文件 {path} 不是支持的图片格式（仅支持 .png, .jpg, .jpeg, .webp, .gif, .bmp）。"

        file_size = os.path.getsize(abs_path)
        if file_size > 20 * 1024 * 1024:
            return f"错误：图片文件过大（{file_size / (1024 * 1024):.1f}MB），最大支持 20MB。"

        from app.services.ai.multimodal_support import resolve_default_multimodal_model_name

        vision_model = await resolve_default_multimodal_model_name()
        if not vision_model:
            return "错误：系统未配置或未启用默认多模态模型（multimodal_model_name），无法执行图片解析。"

        import base64

        with open(abs_path, "rb") as f:
            raw_bytes = f.read()
        mime_subtype = ext.lstrip(".")
        if mime_subtype == "jpg":
            mime_subtype = "jpeg"
        data_url = f"data:image/{mime_subtype};base64,{base64.b64encode(raw_bytes).decode('utf-8')}"

        prompt_text = (
            f"请针对以下图片回答问题或提供详细分析。\n用户指令/问题：{question.strip()}"
            if question and question.strip()
            else "请详细描述此图片的内容，并提取其中的关键文字、图表数据及核心信息。"
        )

        from app.services.ai.config import AgentConfigProvider
        from app.services.ai.runtime.agentscope.compat import HumanMessage

        human = HumanMessage(
            content=[
                {"type": "text", "text": prompt_text},
                {"type": "image_url", "image_url": {"url": data_url}},
            ]
        )

        llm = await AgentConfigProvider.get_configured_llm(
            streaming=False,
            model_override=vision_model,
            temp_override=0.0,
        )
        response = await llm.ainvoke([human])

        res_content = getattr(response, "content", None)
        if isinstance(res_content, list):
            parts = [str(item.get("text") if isinstance(item, dict) else item) for item in res_content]
            text = "".join(parts).strip()
        else:
            text = str(res_content or response or "").strip()

        if not text:
            return f"提示：多模态模型 {vision_model} 未能识别出图片有效内容。"

        return f"[图片解析成功 ({os.path.basename(path)} · 模型: {vision_model})]\n{text}"
    except Exception as e:
        logger.warning("read_image execution failed for %s: %s", path, e, exc_info=True)
        return f"解析图片失败: {str(e)}"


@tool
async def write_file(
    path: str,
    content: str,
    save_artifact: bool = False,
    artifact_type: str = "document",
) -> str:
    """
    写入或覆盖本地安全沙箱文件。用户要求保存内容、生成文件、修改文件、写入配置或把结果落盘到 data/skills 等安全目录时应触发本工具。
    只允许写入安全沙箱 data 目录或已配置的 skills 目录，若父级目录不存在将自动创建。

    当且仅当用户要求把该文件作为可交付产物保存（如"把这段内容保存成文档给我/输出一份文档"）时，
    应传 save_artifact=True；此时若目标位于用户工作区内，将自动登记到 AI 产物产出并返回可下载链接，
    便于前端检索与下载。默认写入代码、技能、配置、缓存等过程文件时保持 save_artifact=False，不污染产出表。

    Args:
        path: 物理写入目标路径 (如 data/skills/my_skill.py 或 data/agent_workspaces/{user_key}/docs/xxx.md)。
        content: 写入文件的完整文本内容。
        save_artifact: 是否把该文件登记为 AI 产物（需位于用户工作区内才可登记）。默认 False。
        artifact_type: 产物类型，仅当 save_artifact=True 时生效（如 document/txt/markdown），默认 "document"。
    """
    try:
        abs_path = validate_safe_path(path)

        # 自动创建父级目录
        dir_name = os.path.dirname(abs_path)
        if dir_name:
            os.makedirs(dir_name, exist_ok=True)

        with open(abs_path, "w", encoding="utf-8") as f:
            f.write(content)

        size = len(content.encode("utf-8"))
        base_msg = f"物理写入成功！路径：{path}，写入大小：{size} 字节。"

        if not save_artifact:
            return base_msg

        # 需登记到 AI 产物产出：要求文件位于用户工作区内，且能拿到归属用户上下文。
        try:
            from app.core.context import get_current_agent_context
            from app.services.ai.tools.generated_file_service import register_artifact
            from pathlib import Path as _Path

            ctx = get_current_agent_context()
            owner_user_id = ctx.user_id if ctx else None
            source = _Path(abs_path).resolve()
            if owner_user_id is None:
                return base_msg + "（提示：当前会话无归属用户上下文，未能登记到 AI 产物，请直接使用文件路径访问。）"
            # register_artifact 内部会校验是否位于工作区内；不在工作区内会抛错。
            artifact = await register_artifact(
                source_path=source,
                filename=os.path.basename(abs_path),
                owner_user_id=owner_user_id,
                artifact_type=artifact_type or "document",
                conversation_id=ctx.conversation_id if ctx else None,
                trace_id=ctx.trace_id if ctx else None,
            )
            payload = artifact.to_tool_payload()
            return (
                base_msg
                + f"\n【已登记到 AI 产物】文件名:{payload['filename']}，"
                f"大小:{payload['size']} 字节，下载链接:{payload['download_url']}"
            )
        except ValueError as e:
            return base_msg + f"\n（提示：未能登记到 AI 产物，原因：{e}。文件已落盘，可直接访问路径。）"
        except Exception as e:
            return base_msg + f"\n（提示：产物登记失败，原因：{e}。文件已落盘，可直接访问路径。）"
    except Exception as e:
        return f"写入文件失败: {str(e)}"


@tool
def search_text(
    pattern: str,
    path: str = "data",
    file_glob: str = None,
    case_sensitive: bool = False,
    context_lines: int = 2,
    max_results: int = 100,
) -> str:
    """
    在安全目录内搜索文本，等价于安全、受控的 grep/ripgrep。
    用户要求搜索、查找、grep、定位文本、查日志关键字、查代码引用、查配置项、找报错堆栈、找包含某字符串的文件时，应优先触发本工具。
    只允许搜索 data 目录或已配置的 skills 目录，返回结果会按数量和上下文行数截断以避免上下文过大。

    Args:
        pattern: 要搜索的文本或正则表达式。
        path: 搜索起点路径，必须在安全目录内，默认 data。
        file_glob: 可选文件名通配符过滤，如 *.log、*.txt、*.md。
        case_sensitive: 是否大小写敏感，默认 False。
        context_lines: 每条命中前后返回的上下文行数，默认 2。
        max_results: 最多返回的命中数量，默认 100。
    """
    try:
        if not pattern:
            return "错误：pattern 不能为空。"

        abs_path = validate_safe_path(path)
        if not os.path.exists(abs_path):
            return f"错误：路径 {path} 不存在。"

        context_lines = max(0, min(int(context_lines), 20))
        max_results = max(1, min(int(max_results), 500))

        flags = 0 if case_sensitive else re.IGNORECASE
        try:
            regex = re.compile(pattern, flags)
        except re.error:
            regex = re.compile(re.escape(pattern), flags)

        candidate_files = []
        if os.path.isfile(abs_path):
            candidate_files = [abs_path]
        else:
            for root, _, files in os.walk(abs_path):
                for filename in files:
                    if file_glob and not fnmatch.fnmatch(filename, file_glob):
                        continue
                    candidate_files.append(os.path.join(root, filename))

        matches = []
        scanned_files = 0
        for file_path in sorted(candidate_files):
            if len(matches) >= max_results:
                break
            try:
                with open(file_path, "r", encoding="utf-8", errors="ignore") as f:
                    lines = f.readlines()
                scanned_files += 1
            except (OSError, UnicodeError):
                continue

            for idx, line in enumerate(lines):
                if not regex.search(line):
                    continue
                start = max(0, idx - context_lines)
                end = min(len(lines), idx + context_lines + 1)
                rel_path = os.path.relpath(file_path, os.getcwd())
                snippet = []
                for line_idx in range(start, end):
                    marker = ">" if line_idx == idx else " "
                    snippet.append(f"{marker} {line_idx + 1}: {lines[line_idx].rstrip()}")
                matches.append(f"{rel_path}:{idx + 1}\n" + "\n".join(snippet))
                if len(matches) >= max_results:
                    break

        if not matches:
            return f"未找到匹配内容。扫描文件数: {scanned_files}。"

        truncated = "，结果已截断" if len(matches) >= max_results else ""
        return (
            f"搜索完成。扫描文件数: {scanned_files}，命中数: {len(matches)}{truncated}。\n\n"
            + "\n\n".join(matches)
        )
    except Exception as e:
        return f"搜索文本失败: {str(e)}"


@tool
async def exec_command(command: str) -> str:
    """
    执行 shell 命令以获取真实系统状态或完成明确的系统操作。
    用户询问系统负载、CPU、内存、磁盘、进程、端口、网络连通性、服务状态、日志 tail、当前目录文件或要求执行命令时，应优先触发本工具后再回答。
    亦可用于执行通用的命令行程序（如 `git` 进行版本控制及拉取代码、`curl` 访问网络、`pip/npm` 安装依赖或构建、`python/node` 运行脚本等）。
    查看负载建议使用非交互命令，例如 uptime、top -b -n 1、ps aux --sort=-%cpu | head、df -h、free -h、ss -tulnp。
    本工具强制施加默认 30 秒超时限制与标准输出长度截断。
    
    Args:
        command: 要执行的 Shell 命令。
    """
    try:
        deletion_assessment = assess_shell_deletion(command, cwd=os.getcwd())
        if deletion_assessment.action == "deny":
            return f"安全拦截：{deletion_assessment.reason}。该删除命令被禁止执行。"
        if deletion_assessment.action == "ask":
            return f"需要用户确认：{deletion_assessment.reason}。该删除命令未执行。"

        forbidden_reason = _is_forbidden_shell_command(command)
        if forbidden_reason:
            return f"安全拦截：{forbidden_reason}。该命令被禁止执行。"

        capture = await run_shell_command_capture(command, cwd=os.getcwd(), timeout_seconds=30.0)
        if capture.timed_out:
            return f"错误：命令执行超时（最大限制 30 秒）。已强制终止命令进程。"

        stdout_str = capture.stdout
        stderr_str = capture.stderr
        truncated = capture.truncated
        if truncated:
            stdout_str += "\n...[部分输出已截断]..."
            
        result = []
        if stdout_str:
            result.append(f"--- [stdout] ---\n{stdout_str}")
        if stderr_str:
            result.append(f"--- [stderr] ---\n{stderr_str}")
            
        if not result:
            return f"命令执行成功，无任何控制台输出。ExitCode={capture.exit_code}"
            
        return "\n".join(result) + (f"\nExitCode={capture.exit_code}" if not truncated else "")
    except Exception as e:
        return f"命令执行异常: {str(e)}"

def _format_process_list() -> str:
    current_pid = os.getpid()
    parent_pids = []
    try:
        p = psutil.Process(current_pid)
        while p.parent():
            parent_pids.append(p.parent().pid)
            p = p.parent()
    except:
        pass

    protected_pids = set([current_pid, 1] + parent_pids)
    processes = []
    try:
        for proc in psutil.process_iter(['pid', 'name', 'username', 'cpu_percent', 'memory_info']):
            try:
                info = proc.info
                pid_val = info['pid']
                is_protected = pid_val in protected_pids
                processes.append({
                    "pid": pid_val,
                    "name": info['name'],
                    "username": info['username'],
                    "cpu": f"{proc.cpu_percent()}%",
                    "memory": f"{round(info['memory_info'].rss / (1024 * 1024), 2)} MB",
                    "status": "核心受保护" if is_protected else "可控"
                })
            except (psutil.NoSuchProcess, psutil.AccessDenied):
                continue
    except (PermissionError, psutil.AccessDenied) as e:
        current_proc = psutil.Process(current_pid)
        info = current_proc.as_dict(attrs=['pid', 'name', 'username', 'memory_info'])
        processes.append({
            "pid": info['pid'],
            "name": info['name'],
            "username": info.get('username') or "unknown",
            "cpu": "unknown",
            "memory": f"{round(info['memory_info'].rss / (1024 * 1024), 2)} MB",
            "status": f"核心受保护；全量进程受限: {str(e)}"
        })

    lines = [f"{'PID':<8} {'进程名':<25} {'CPU占用':<10} {'物理内存':<12} {'状态':<10}"]
    lines.append("-" * 70)
    for p in sorted(processes, key=lambda x: x['pid']):
        lines.append(f"{p['pid']:<8} {p['name']:<25} {p['cpu']:<10} {p['memory']:<12} {p['status']:<10}")
    return "\n".join(lines)


def _protected_process_ids() -> set[int]:
    current_pid = os.getpid()
    parent_pids = []
    try:
        p = psutil.Process(current_pid)
        while p.parent():
            parent_pids.append(p.parent().pid)
            p = p.parent()
    except:
        pass
    return set([current_pid, 1] + parent_pids)


@tool
def list_process() -> str:
    """
    列出当前系统进程及 CPU、内存占用。用户询问正在运行的进程、进程列表、哪个进程占资源、服务是否在跑时应触发本工具。
    如果用户还要求查看整体系统负载、磁盘、端口或执行 ps/top/grep 等组合命令，应使用 exec_command。
    """
    try:
        return _format_process_list()
    except Exception as e:
        return f"进程列表读取异常: {str(e)}"


@tool
def manage_process(action: str, pid: int = None) -> str:
    """
    管理系统进程。用户要求查看进程列表或终止/杀掉指定 PID 时应触发本工具；只查看列表时优先使用 list_process。
    防止误杀核心 Uvicorn/FastAPI 主进程以及开发守护进程。
    
    Args:
        action: 只能是 "list" (列出进程) 或 "kill" (终止进程)。
        pid: 当 action="kill" 时，必须提供指定的目标 PID。
    """
    try:
        protected_pids = _protected_process_ids()

        action = action.lower()
        if action == "list":
            return _format_process_list()
            
        elif action == "kill":
            if pid is None:
                return "错误：当 action='kill' 时，必须指定目标 pid。"
                
            if pid in protected_pids:
                return f"安全越权拦截：PID {pid} 是系统核心 Web 服务或守护进程，受到强制保护，禁止终止！"
                
            try:
                proc = psutil.Process(pid)
                proc.terminate()
                try:
                    proc.wait(timeout=2.0)
                except psutil.TimeoutExpired:
                    proc.kill()
                return f"成功终止进程 PID {pid} ({proc.name()})。"
            except psutil.NoSuchProcess:
                return f"错误：未查找到 PID {pid} 的进程。"
            except psutil.AccessDenied:
                return f"错误：权限不足，无法终止 PID {pid} 进程。"
        else:
            return "错误：不支持的 action 参数。仅支持 'list' 或 'kill'。"
    except Exception as e:
        return f"进程管理操作异常: {str(e)}"


@tool
def create_skills(
    skill_id: str,
    name: str,
    description: str,
    skill_md_content: str,
    scope: str = "personal",
    overwrite: bool = False,
) -> str:
    """
    根据当前的聊天会话内容与用户意图，按照 Anthropic Skills 生态规范在 skills 目录下物理创建或初始化一个智能体技能（Skill）。
    这会创建一个技能文件夹，并强制写入包含 Frontmatter 触发定义的 SKILL.md 规范守则文件。

    如果该技能 ID **已经存在**，默认什么都不会写入，只返回提示；只有在用户明确同意覆盖后，
    才带 overwrite=true 重新调用。这样避免「以为在新建、实际把已有技能整份替换掉」。

    Args:
        skill_id: 技能唯一英文标识 (如 translate-helper)，仅支持英文、数字、中划线及下划线。
        name: 技能的人类可读名称 (如 多语言智能翻译官)。
        description: 技能的核心应用场景与触发场景说明。
        skill_md_content: 该技能核心 SKILL.md 文件的完整 Markdown 文本。
                          必须符合规范：开头需包含 YAML Frontmatter 描述（以 --- 包裹，内含 name: <名称> 与 description: <描述> 属性），随后为 imperative 操作指令。
        scope: 技能的共享范围：'personal'（个人独享，默认）或 'global'（平台全局，仅管理员可创建）。
        overwrite: 是否允许覆盖同名已存在的技能。默认 false。为 false 且技能已存在时，
                   本工具不会修改磁盘上的任何内容，而是返回 NANZI_SKILL_EXISTS 提示；
                   必须先向用户确认「确实要整份替换现有技能」，再带 overwrite=true 重试。

    本工具只写 SKILL.md。若技能还需要脚本、模板、参考资料、图片等资源文件，
    建好技能后用 write_skill_file 逐个写入（否则 SKILL.md 里引用的 scripts/xxx.py
    在磁盘上并不存在，技能是半残的）。
    """
    try:
        # ID 校验与端点/重命名/上传共用同一份实现：格式、128 长度上限、平台保留字。
        # 此前这里绕过保留字与长度校验，模型可以造出叫 stats/export 的「幽灵技能」
        # （目录建成了，但详情路由被更早注册的字面路由遮蔽，用户永远打不开）。
        try:
            skill_id = validate_skill_id(skill_id)
        except SkillRenameError as exc:
            return f"错误：{exc}"

        # 获取当前运行上下文中的用户信息
        from app.utils.context import current_user_info
        user_info = current_user_info.get()

        is_admin = False
        if user_info:
            role = user_info.get("role", "")
            is_admin = (role == "admin" or "admin" in str(role).lower())

        # 决定写入目录
        resolved_scope = scope.lower()
        existing_personal_dir: str | None = None
        if resolved_scope == "global":
            if user_info and not is_admin:
                return "错误：权限不足，仅系统管理员有权创建或修改平台全局技能（global）。已自动拦截。"
            from app.core.config import settings
            skills_dir = settings.SKILLS_DIR
            if not skills_dir:
                return "错误：未配置全局 SKILLS_DIR 目录。"
        else:
            # 默认为个人技能
            if not user_info:
                return "错误：未在当前会话中检测到有效的用户身份，无法创建个人技能。"
            from app.services.ai.skill_resolver import get_user_personal_skills_dir
            from app.services.ai.skill_resolver import resolve_personal_skill_dir

            skills_dir = get_user_personal_skills_dir(user_info)
            if not skills_dir:
                return "错误：无法解析个人技能目录，请确认账号工作区已正确初始化。"
            # 存量技能可能位于历史遗留的 .seed 布局下：就地更新，否则会在平铺位置
            # 再造一份副本（同一技能两份，且公共技能同 ID 时还会覆盖公共的那份）
            existing_personal_dir = resolve_personal_skill_dir(skills_dir, skill_id)

        # 拼接物理路径
        skill_path = existing_personal_dir or os.path.join(skills_dir, skill_id)
        
        # 安全防御拦截
        validate_safe_path(skill_path)

        skill_md_path = os.path.join(skill_path, "SKILL.md")
        # 同名技能已存在时默认拒写。此前这里是静默 upsert：makedirs(exist_ok=True) 后直接
        # 整份覆盖，却返回「创建成功」、前端弹「已创建个人技能」横幅——用户以为新增了一个
        # 技能，实际把已有技能（可能已被发布、已被智能体挂载）的正文清空替换了。
        already_exists = os.path.exists(skill_md_path)
        if already_exists and not overwrite:
            return (
                f"技能 {skill_id} 已存在（{skill_md_path}），本次**未做任何修改**。\n"
                "如果用户确实要用新内容整份替换它，请在获得用户明确确认后，"
                "带 overwrite=true 重新调用本工具；如果只是想补充或修改其中部分内容，"
                "请改用技能文件编辑能力，不要整份覆盖。\n"
                + "NANZI_SKILL_EXISTS:"
                + json.dumps(
                    {
                        "skill_id": skill_id,
                        "scope": resolved_scope,
                        "name": name or skill_id,
                    },
                    ensure_ascii=False,
                    separators=(",", ":"),
                )
            )

        os.makedirs(skill_path, exist_ok=True)

        # 写入物理磁盘
        with open(skill_md_path, "w", encoding="utf-8") as f:
            f.write(skill_md_content)

        scope_desc = "平台全局" if resolved_scope == "global" else "个人专属"
        if already_exists:
            action_desc = "已覆盖更新"
            marker_name = "NANZI_SKILL_UPDATED"
        else:
            action_desc = "创建成功"
            marker_name = "NANZI_SKILL_CREATED"
        marker = (
            marker_name + ":"
            + json.dumps(
                {
                    "skill_id": skill_id,
                    "scope": resolved_scope,
                    "name": name or skill_id,
                },
                ensure_ascii=False,
                separators=(",", ":"),
            )
        )
        return (
            f"成功：{scope_desc}技能 {skill_id} {action_desc}。"
            f"SKILL.md 规范文件已物理写入路径 {skill_md_path}。\n{marker}"
        )
    except Exception as e:
        return f"错误：创建技能失败，原因: {str(e)}"


def _skill_dir_usage(skill_dir: str) -> tuple[int, int]:
    """统计技能目录当前的文件数与总字节数（用于写入预算，避免工具被循环调用灌爆目录）。"""
    files = 0
    total = 0
    for root, _dirs, names in os.walk(skill_dir):
        for name in names:
            files += 1
            try:
                total += os.path.getsize(os.path.join(root, name))
            except OSError:
                continue
    return files, total


def _resolve_skill_dir_for_asset(skill_id: str, scope: str, user_info) -> tuple[str, str]:
    """定位**已存在**的技能目录，返回 ``(skill_dir, resolved_scope)``。

    与 create_skills 的区别：这里不允许「顺手新建技能」——目录不存在就直接报错，
    免得模型把脚本写到一个不存在的技能里，产出「有文件没说明书」的孤儿目录。
    """
    resolved_scope = (scope or "personal").strip().lower()
    if resolved_scope not in ("personal", "global"):
        raise ValueError("scope 仅支持 personal 或 global")

    if resolved_scope == "global":
        if user_info:
            role = str(user_info.get("role", "") or "")
            if not (role == "admin" or "admin" in role.lower()):
                raise ValueError("权限不足：仅系统管理员可以写入平台全局技能（global）")
        from app.core.config import settings

        skills_dir = settings.SKILLS_DIR
        if not skills_dir:
            raise ValueError("未配置全局 SKILLS_DIR 目录")
        skill_dir = os.path.join(skills_dir, skill_id)
    else:
        if not user_info:
            raise ValueError("未在当前会话中检测到有效的用户身份，无法写入个人技能")
        from app.services.ai.skill_resolver import (
            get_user_personal_skills_dir,
            resolve_personal_skill_dir,
        )

        skills_dir = get_user_personal_skills_dir(user_info)
        if not skills_dir:
            raise ValueError("无法解析个人技能目录，请确认账号工作区已正确初始化")
        # 平铺优先、.seed 回退：否则更新 .seed 里的存量技能会在平铺处造出第二份副本
        skill_dir = resolve_personal_skill_dir(skills_dir, skill_id)
        if not skill_dir:
            raise ValueError(
                f"技能 {skill_id} 不存在。请先用 create_skills 创建该技能，再用本工具追加资源文件"
            )

    if not os.path.isfile(os.path.join(skill_dir, "SKILL.md")):
        raise ValueError(
            f"技能 {skill_id} 目录缺少 SKILL.md，不是有效技能。请先用 create_skills 创建"
        )
    return os.path.abspath(skill_dir), resolved_scope


def _normalize_skill_asset_path(path: str) -> str:
    """校验并规范化技能内的相对路径，返回 ``a/b/c.py`` 形式；非法则抛 ValueError。"""
    raw = str(path or "").strip().replace("\\", "/")
    if not raw:
        raise ValueError("path 不能为空")
    if raw.startswith("/") or os.path.isabs(raw):
        raise ValueError("path 必须是相对技能目录的路径（如 scripts/build.py），不能是绝对路径")
    parts = [part for part in raw.split("/") if part not in ("", ".")]
    if not parts:
        raise ValueError("path 不能为空")
    if any(part == ".." for part in parts):
        raise ValueError("path 不允许包含 ..（越权路径）")
    if any(part.startswith(".") for part in parts):
        raise ValueError("path 不允许包含以 . 开头的隐藏文件或目录")
    normalized = "/".join(parts)
    if normalized.lower() == "skill.md":
        raise ValueError(
            "SKILL.md 请用 create_skills 写入（覆盖需显式 overwrite=true），本工具只用于追加资源文件"
        )
    return normalized


@tool
def write_skill_file(
    skill_id: str,
    path: str,
    content: str,
    encoding: str = "utf8",
    scope: str = "personal",
    overwrite: bool = False,
) -> str:
    """
    给**已存在**的技能追加或更新一个资源文件：脚本（scripts/*.py）、模板、参考资料（references/*.md）、
    图片等资产。SKILL.md 本身不归本工具管，请用 create_skills 写入。

    什么时候该用：用户要把某套流程沉淀成技能，而这个技能除了说明书还需要**可被引用的文件**
    （例如「按这个脚本跑」「用这个模板」「参考这份规范」）。只有说明书、却在里面写了
    `python scripts/build.py` 的技能是半残的——那个脚本并不存在。此时先用 create_skills
    建技能、再用本工具逐个写入资源文件。

    Args:
        skill_id: 目标技能 ID（必须已存在），仅支持英文、数字、中划线及下划线。
        path: 技能目录内的相对路径，如 scripts/build.py、references/rules.md、assets/logo.png。
              不允许绝对路径、不允许 ..、不允许以 . 开头的隐藏文件/目录。
        content: 文件内容。文本按 encoding="utf8" 直接写入；二进制（png/jpg/xlsx 等）
                 需先把字节做 Base64 编码，再用 encoding="base64" 传入。
        encoding: "utf8"（默认）或 "base64"。base64 时必须能正确解码，否则拒绝写入。
        scope: "personal"（个人技能，默认）或 "global"（平台全局，仅管理员）。
        overwrite: 是否允许覆盖已存在的同名文件。默认 false：目标已存在时**不写任何内容**，
                   返回 NANZI_SKILL_FILE_EXISTS，需先向用户确认再带 overwrite=true 重试。

    单个文件上限 10MB，技能目录总量上限 500MB / 20000 个文件（与解压上传共用同一份预算）。
    """
    try:
        try:
            skill_id = validate_skill_id(skill_id)
        except SkillRenameError as exc:
            return f"错误：{exc}"

        try:
            relative_path = _normalize_skill_asset_path(path)
        except ValueError as exc:
            return f"错误：{exc}"

        encoding_key = str(encoding or "utf8").strip().lower()
        if encoding_key in ("utf8", "utf-8", "text"):
            payload = str(content or "").encode("utf-8")
        elif encoding_key in ("base64", "b64"):
            import base64 as _base64

            compact = re.sub(r"\s+", "", str(content or ""))
            if not compact:
                return "错误：encoding=base64 时 content 不能为空"
            try:
                payload = _base64.b64decode(compact, validate=True)
            except Exception:
                return "错误：content 不是合法的 Base64 文本，无法解码为二进制"
        else:
            return "错误：encoding 仅支持 utf8 或 base64"

        from app.core.upload_limits import SKILL_ASSET_MAX_FILE_BYTES
        from app.core.upload_limits import SKILL_DIR_MAX_BYTES, SKILL_DIR_MAX_FILES

        if len(payload) > SKILL_ASSET_MAX_FILE_BYTES:
            return (
                f"错误：单个文件 {len(payload)} 字节，超过上限 "
                f"{SKILL_ASSET_MAX_FILE_BYTES // (1024 * 1024)}MB，请拆分或改用工作空间目录"
            )

        from app.utils.context import current_user_info

        user_info = current_user_info.get()
        try:
            skill_dir, resolved_scope = _resolve_skill_dir_for_asset(skill_id, scope, user_info)
        except ValueError as exc:
            return f"错误：{exc}"

        target_path = os.path.abspath(
            os.path.join(skill_dir, *relative_path.split("/"))
        )
        try:
            if os.path.commonpath([skill_dir, target_path]) != skill_dir:
                return "错误：目标路径越出技能目录，已拦截"
        except ValueError:
            return "错误：目标路径越出技能目录，已拦截"

        validate_safe_path(target_path)

        already_exists = os.path.exists(target_path)
        if already_exists and os.path.isdir(target_path):
            return f"错误：{relative_path} 已存在且是目录，无法作为文件写入"
        if already_exists and not overwrite:
            return (
                f"文件 {relative_path} 已存在于技能 {skill_id}，本次**未做任何修改**。\n"
                "如果需要整份替换该文件，请在获得用户明确确认后，带 overwrite=true 重新调用。\n"
                "NANZI_SKILL_FILE_EXISTS:"
                + json.dumps(
                    {"skill_id": skill_id, "scope": resolved_scope, "path": relative_path},
                    ensure_ascii=False,
                    separators=(",", ":"),
                )
            )

        existing_files, existing_bytes = _skill_dir_usage(skill_dir)
        incoming_files = 0 if already_exists else 1
        if existing_files + incoming_files > SKILL_DIR_MAX_FILES:
            return (
                f"错误：技能 {skill_id} 文件数已达上限（{SKILL_DIR_MAX_FILES} 个），"
                "请清理不再需要的资源后再写入"
            )
        if existing_bytes + len(payload) > SKILL_DIR_MAX_BYTES:
            return (
                f"错误：技能 {skill_id} 总占用将超过 "
                f"{SKILL_DIR_MAX_BYTES // (1024 * 1024)}MB 上限，请清理或拆分技能"
            )

        os.makedirs(os.path.dirname(target_path), exist_ok=True)
        with open(target_path, "wb") as f:
            f.write(payload)

        action_desc = "已覆盖更新" if already_exists else "写入成功"
        marker = (
            "NANZI_SKILL_FILE_WRITTEN:"
            + json.dumps(
                {
                    "skill_id": skill_id,
                    "scope": resolved_scope,
                    "path": relative_path,
                    "bytes": len(payload),
                    "encoding": "base64" if encoding_key in ("base64", "b64") else "utf8",
                    "overwritten": already_exists,
                },
                ensure_ascii=False,
                separators=(",", ":"),
            )
        )
        scope_desc = "平台全局" if resolved_scope == "global" else "个人专属"
        return (
            f"成功：{scope_desc}技能 {skill_id} 的资源文件 {relative_path} {action_desc}"
            f"（{len(payload)} 字节）。完整路径 {target_path}。\n{marker}"
        )
    except Exception as e:
        return f"错误：写入技能文件失败，原因: {str(e)}"


# Grounding 证据声明（双重保障）
from app.services.ai.grounding.models import EvidenceType

list_available_skills.evidence_types = frozenset({EvidenceType.RUNTIME_STATE})
list_available_skills.evidence_policy = "allow_empty_success"

read_skill_instruction.evidence_types = frozenset({EvidenceType.RUNTIME_STATE})
read_skill_instruction.evidence_policy = "allow_empty_success"
