"""知识库的**写**操作工具：建库、上传文档。

与 ``knowledge_tool.py``（检索，只读）分开的原因不是体量，而是失败语义：
检索失败可以降级成"没找到"，建库/上传失败必须让模型停下来问人 —— 混在一起
迟早会把两类错误都处理成一句含糊的"操作失败"。

两条硬约束（都是产品决策，不是实现细节）：

1. **只能建个人私有库**。工具签名里根本没有 ``visibility``，模型无法"试着传
   team"；``owner_user_id`` 恒为调用者，杜绝替别人建库。
2. **上传前必须有内容级写权限**（``content_writable``），与详情页那个
   「可编辑」徽标同一口径。少用一级会出现"页面显示可编辑、让智能体传却被拒"。
"""

from __future__ import annotations

import json
import logging
import os
from pathlib import Path
from typing import Any

from app.core.context import get_current_agent_context
from app.core.orm import AsyncSessionLocal
from app.services.ai.knowledge_utils import (
    KNOWLEDGE_BASE_DISABLED_TOOL_ERROR,
    is_knowledge_base_enabled,
)
from app.services.ai.ragflow_client import RagFlowClient, clear_dataset_list_cache
from app.services.ai.tools.document_paths import (
    DocumentPathError,
    resolve_context_user_name,
    resolve_document_input_path,
)
from app.services.ai.tools.tool_compat import tool
from app.services.knowledge_audit_service import KnowledgeAuditService
from app.services.knowledge_base_service import KnowledgeBaseMetadataService
from app.services.knowledge_quota_service import KnowledgeQuotaService, QuotaExceeded
from app.services.knowledge_upload_policy import (
    ALLOWED_EXTENSIONS,
    DEFAULT_MAX_BYTES,
    KnowledgeUploadRejected,
    validate_upload,
)
from app.services.permission_service import PermissionService

logger = logging.getLogger(__name__)

VISIBILITY_PRIVATE = "private"

#: RAGFlow 对 dataset 名称长度有限制，超过会在上游报一个不可读的错误。
#: 在入口挡住，模型才能拿到"请缩短名称"这种可执行提示。
NAME_MAX_LEN = 64

_PERM_CREATE = "element:knowledge:create"
_PERM_UPLOAD = "element:knowledge:upload_document"


def _fail(code: str, message: str, **extra: Any) -> str:
    """失败一律返回结构化 JSON：模型据此分支处理，message 可直接转述给用户。

    ``extra`` 用来带**可执行线索**（例如「你能编辑的库」清单）。只报"库不存在"
    会让模型干等用户说出 ID；带上候选它就能直接问「是 A 还是 B」。
    """
    payload = {"ok": False, "error": code, "message": message}
    payload.update({k: v for k, v in extra.items() if v not in (None, [], "")})
    return json.dumps(payload, ensure_ascii=False)


def _ok(**payload: Any) -> str:
    payload = {"ok": True, **payload}
    return json.dumps(payload, ensure_ascii=False)


def _normalize_name(raw: Any) -> tuple[str, str]:
    """返回 ``(清洗后的名称, 错误文案)``，错误文案为空表示通过。"""
    name = str(raw or "").strip()
    if not name:
        return "", "知识库名称不能为空。"
    if len(name) > NAME_MAX_LEN:
        return "", f"知识库名称最多 {NAME_MAX_LEN} 个字符，当前 {len(name)} 个。"
    if any(ch in name for ch in "\r\n\t"):
        return "", "知识库名称不能包含换行或制表符。"
    return name, ""


async def _resolve_input_file(ctx, file_path: str, *, user_id: int, user_name: str | None):
    """解析待上传文件；路径不合法时按文件名在本轮附件里兜一次。

    模型手里通常只有文件名（``session_status`` 回的是 filenames，不带完整路径），
    让它猜 ``data/uploads/<name>`` 十有八九对，但一旦落在别的工作目录就会
     报「文件不在允许目录」。既然附件本来就受 ``authorized_attachment_paths``
    约束，按 basename 命中是安全的，且能显著降低"文件明明在却传不上去"。
    """
    kwargs = dict(
        allowed_attachment_paths=getattr(ctx, "authorized_attachment_paths", None) or (),
        user_id=user_id,
        conversation_id=getattr(ctx, "conversation_id", None),
        allowed_extensions=ALLOWED_EXTENSIONS,
        user_name=user_name,
        max_bytes=DEFAULT_MAX_BYTES,
    )
    try:
        return await resolve_document_input_path(file_path, **kwargs)
    except DocumentPathError:
        base = os.path.basename(str(file_path or "").replace("\\", "/"))
        if base:
            for candidate in getattr(ctx, "authorized_attachment_paths", None) or ():
                if os.path.basename(str(candidate)) == base:
                    return await resolve_document_input_path(str(candidate), **kwargs)
        raise


async def _writable_dataset_candidates(
    db, ctx, *, user_id: int, user_name: str | None, limit: int = 10
) -> list[dict[str, Any]]:
    """当前用户**有编辑权**的知识库（名称 + ID），用于让模型反问而不是瞎猜。

    只取 active、最多 ``limit`` 条：候选太多时模型反而更难选，超过的部分让人去
    页面上筛。查询失败一律返回空列表——候选只是辅助信息，不能让它把主流程带崩。
    """
    try:
        from sqlalchemy import select

        from app.models.knowledge import KnowledgeBaseMetadata

        access = await PermissionService(db).get_knowledge_base_access(user_id, user_name)
        allowed: set[str] | None = None
        if not access.get("is_admin"):
            allowed = set(access.get("content_writable_ids") or ())
            if not allowed:
                return []

        stmt = (
            select(KnowledgeBaseMetadata.ragflow_dataset_id, KnowledgeBaseMetadata.name)
            .where(KnowledgeBaseMetadata.status != "deleted")
            .order_by(KnowledgeBaseMetadata.updated_at.desc())
            .limit(limit)
        )
        if allowed is not None:
            stmt = stmt.where(
                KnowledgeBaseMetadata.ragflow_dataset_id.in_(list(allowed))
            )
        rows = (await db.execute(stmt)).all()
        return [
            {"dataset_id": str(row[0]), "name": str(row[1] or "")}
            for row in rows
            if row[0]
        ]
    except Exception as exc:  # pragma: no cover - 辅助信息，失败不影响主流程
        logger.debug("[knowledge_admin_tools] 候选库查询失败: %s", exc)
        return []


async def _has_element_permission(db, ctx, permission_id: str) -> bool:
    if bool(getattr(ctx, "is_admin", False)):
        return True
    return bool(
        await PermissionService(db).check_permission(
            int(ctx.user_id), "element", permission_id
        )
    )


def _audit_user(ctx) -> dict[str, Any]:
    return {"user_id": int(ctx.user_id), "user_name": resolve_context_user_name(ctx)}


async def _audit(action: str, ctx, detail: dict[str, Any], *, status_code: int = 200,
                 error_message: str | None = None) -> None:
    """工具层没有 FastAPI Request，直接用审计服务（它自己开 session、失败不抛）。

    ``source=agent_tool`` 是关键：事后看操作日志时，能区分"人在页面上传的"
    与"智能体代传的"，否则排查会被两条来源混在一起。
    """
    try:
        await KnowledgeAuditService.log(
            dataset_ids=[detail.get("dataset_id")] if detail.get("dataset_id") else [],
            action=action,
            user=_audit_user(ctx),
            status_code=status_code,
            detail={**detail, "source": "agent_tool"},
            client_ip=None,
            error_message=error_message,
        )
    except Exception as exc:  # pragma: no cover - 审计是旁路，不能反过来打断业务
        logger.warning("[knowledge_admin_tools] 审计写入跳过: %s", exc)


@tool
async def create_knowledge_base(name: str, description: str = "") -> str:
    """创建一个**个人私有**知识库（仅你自己可见）。创建后不会自动被你检索到。

    使用规则：
    - 调用前先向用户确认库名；重名会直接失败，不要自己加后缀硬试。
    - 只能建个人私有库：不接受 TEAM 级 / 公开可见。需要共享时告诉用户去页面
      建库并配置可见范围与授权 —— 本工具做不到。
    - 需要"建完后让本智能体能查到"时，要提醒用户到智能体的知识库绑定里勾选
      这个新库；工具本身不会把它加进检索范围。

    Args:
        name: 知识库名称，1-64 个字符，不能含换行/制表符。
        description: 可选的一句话说明，方便以后在列表里认出来。
    """
    if not await is_knowledge_base_enabled():
        return _fail("feature_disabled", KNOWLEDGE_BASE_DISABLED_TOOL_ERROR)

    ctx = get_current_agent_context()
    if not ctx or not getattr(ctx, "user_id", None):
        return _fail("no_user_context", "无法识别当前用户，已拒绝建库。")

    user_id = int(ctx.user_id)
    user_name = resolve_context_user_name(ctx)

    clean_name, name_error = _normalize_name(name)
    if name_error:
        return _fail("invalid_name", name_error)

    client = RagFlowClient(config_prefix="knowledge_ragflow")

    async with AsyncSessionLocal() as db:
        if not await _has_element_permission(db, ctx, _PERM_CREATE):
            return _fail(
                "forbidden",
                "当前账号没有「创建知识库」的功能权限，请联系管理员开通后再试。",
            )

        try:
            await KnowledgeQuotaService.ensure_can_create_dataset(db, user_id)
        except QuotaExceeded as exc:
            return _fail("quota_exceeded", str(exc))

        metadata_service = KnowledgeBaseMetadataService(db)
        exists = await metadata_service.find_active_by_name(clean_name)
        if exists:
            return _fail(
                "name_taken",
                f"知识库名称「{clean_name}」已存在，请换一个名称后再建。",
            )

        try:
            created = await client.create_dataset(
                name=clean_name,
                description=description or "",
            )
        except Exception as exc:
            logger.error("[create_knowledge_base] RAGFlow 建库失败: %s", exc, exc_info=True)
            await _audit(
                "datasets:create",
                ctx,
                {"action": "create_dataset", "name": clean_name},
                status_code=502,
                error_message=str(exc),
            )
            return _fail("upstream_error", f"RAGFlow 建库失败：{exc}")

        dataset_id = str(created.get("id") or created.get("dataset_id") or "")
        if not dataset_id:
            # 与页面接口同口径：建库返回缺 id 时按名字反查，**唯一**才采纳。
            # 多个同名候选时宁可失败，也不能把元数据绑到别人的库上。
            matched = await client.list_datasets(name=clean_name)
            matched_ids = [str(item.get("id")) for item in matched if item.get("id")]
            if len(matched_ids) != 1:
                await _audit(
                    "datasets:create",
                    ctx,
                    {"action": "create_dataset", "name": clean_name},
                    status_code=502,
                    error_message="RAGFlow 创建成功但未能确定 Dataset ID",
                )
                return _fail(
                    "upstream_ambiguous",
                    f"RAGFlow 创建成功但无法确定唯一 Dataset ID（同名候选 {len(matched_ids)} 个），"
                    "为避免绑错库已中止。请到知识库页面确认后手动同步。",
                )
            dataset_id = matched_ids[0]

        try:
            await metadata_service.upsert_metadata(
                ragflow_dataset_id=dataset_id,
                name=clean_name,
                description=description or None,
                owner=user_name,
                owner_user_id=user_id,
                visibility=VISIBILITY_PRIVATE,
                user_name=user_name,
            )
        except Exception as exc:
            logger.error("[create_knowledge_base] 元数据写入失败: %s", exc, exc_info=True)
            return _fail(
                "metadata_write_failed",
                f"知识库在 RAGFlow 已创建（ID {dataset_id}），但平台元数据写入失败：{exc}。"
                "请到知识库页面点「同步」把它认领回来，不要重复建库。",
            )

        # 工具里必须显式提交：没有 get_db_session 依赖帮忙 commit
        await db.commit()

    clear_dataset_list_cache()
    await _audit(
        "datasets:create",
        ctx,
        {"action": "create_dataset", "dataset_id": dataset_id, "name": clean_name},
    )

    return _ok(
        dataset_id=dataset_id,
        name=clean_name,
        visibility=VISIBILITY_PRIVATE,
        message=f"已创建个人私有知识库「{clean_name}」。",
        note=(
            "这个库只有你自己可见；本智能体不会自动检索它——"
            "需要检索时请到智能体的知识库绑定里勾选它。"
        ),
    )


@tool
async def upload_document_to_knowledge_base(
    dataset_id: str,
    file_path: str,
    auto_parse: bool = True,
    confirm: bool = False,
) -> str:
    """把一个本地文件上传到指定知识库（默认随后自动解析，解析完才能被检索到）。

    **这是两阶段操作，必须分两次调用**：

    1. 第一次 ``confirm=false``（默认）：只做只读校验，**不上传任何东西**，
       返回预览（目标库名称、文件名、大小、是否会自动解析）。
    2. 把预览复述给用户并得到**明确同意**后，再以 ``confirm=true`` 调用完成上传。

    不要跳过第 1 步直接传 ``confirm=true`` —— 传错库无法撤回：本工具没有
    删除或移动文档的能力，而且库里原有的其他人会立刻检索到这份文件。

    其他规则：
    - 只能传**本次对话上传的附件**或**当前用户工作区里的文件**，不能传任意
      服务器路径；单文件上限 256MB，支持 PDF/Word/Excel/PPT/Markdown/TXT/HTML/EPUB。
    - 目标库必须是用户能"编辑"的库；只读库会被拒绝（返回里会带上你能编辑的库清单）。
    - 同名文档已存在时直接失败并返回已有 document_id，不要改名重传 ——
      先问用户是要更新内容还是换个文件名。
    - 用户没明确说是哪个库、或有多个候选时，先用 ``list_accessible_knowledge_bases``
      列出候选，再向用户确认；错误返回里的 ``candidates`` 也可以直接拿来问。

    Args:
        dataset_id: 目标知识库的 dataset ID（32 位）。
            先用 list_accessible_knowledge_bases 查，并且**只从 can_upload=true 的库里选**；
            有多个可上传的库、而用户没指定时，直接把名字列出来问用户。
        file_path: 文件路径。本次对话的附件在 data/uploads/<原始文件名>，
            也可以只传文件名（如 manual.pdf），系统会按附件名匹配；
            不确定文件名时先用 session_status 查看 attachments.filenames。
        auto_parse: 是否上传后立即解析，默认 true。解析失败不影响上传结果。
        confirm: 是否真正执行上传。默认 false 只返回预览；用户明确同意后才传 true。
    """
    if not await is_knowledge_base_enabled():
        return _fail("feature_disabled", KNOWLEDGE_BASE_DISABLED_TOOL_ERROR)

    ctx = get_current_agent_context()
    if not ctx or not getattr(ctx, "user_id", None):
        return _fail("no_user_context", "无法识别当前用户，已拒绝上传。")

    target_id = str(dataset_id or "").strip()

    user_id = int(ctx.user_id)
    user_name = resolve_context_user_name(ctx)

    # 先校验文件（纯本地操作，快速失败，不占数据库连接）
    try:
        path = await _resolve_input_file(
            ctx, file_path, user_id=user_id, user_name=user_name
        )
    except DocumentPathError as exc:
        return _fail("file_not_allowed", f"{exc}。只能上传本次对话的附件或你的工作区文件。")

    file_name = path.name
    size = path.stat().st_size
    try:
        validate_upload(file_name, size)
    except KnowledgeUploadRejected as exc:
        return _fail(exc.code, str(exc))

    client = RagFlowClient(config_prefix="knowledge_ragflow")

    async with AsyncSessionLocal() as db:
        if not await _has_element_permission(db, ctx, _PERM_UPLOAD):
            return _fail(
                "forbidden",
                "当前账号没有「上传文档」的功能权限，请联系管理员开通后再试。",
            )

        if not target_id:
            candidates = await _writable_dataset_candidates(
                db, ctx, user_id=user_id, user_name=user_name
            )
            hint = (
                "请向用户确认要传进哪个库，再用它的 dataset_id 重新调用。"
                if candidates
                else "你没有可编辑的知识库，请先在知识库页面创建一个，或联系管理员给你编辑权限。"
            )
            return _fail(
                "invalid_argument",
                f"缺少目标知识库 ID。{hint}",
                candidates=candidates,
            )

        record = await KnowledgeBaseMetadataService(db).get_by_dataset_id(target_id)
        if record is None or str(getattr(record, "status", "") or "").lower() == "deleted":
            candidates = await _writable_dataset_candidates(
                db, ctx, user_id=user_id, user_name=user_name
            )
            return _fail(
                "dataset_not_found",
                f"知识库 {target_id} 不存在或已删除。"
                + ("请从 candidates 里选一个向用户确认。" if candidates else "请用 list_accessible_knowledge_bases 确认目标库。"),
                candidates=candidates,
            )

        access = await PermissionService(db).get_knowledge_base_access(user_id, user_name)
        record_name = str(getattr(record, "name", "") or "")
        if not access.get("is_admin"):
            writable = access.get("content_writable_ids") or set()
            if target_id not in writable:
                candidates = await _writable_dataset_candidates(
                    db, ctx, user_id=user_id, user_name=user_name
                )
                return _fail(
                    "forbidden_readonly",
                    f"你对知识库「{record_name or target_id}」只有查看权限，不能上传文档。"
                    "需要上传请联系库的归属人或管理员，或让他们把这个库设为允许你编辑。",
                    candidates=candidates,
                )

        try:
            await KnowledgeQuotaService.ensure_can_upload(client, target_id, incoming=1)
        except QuotaExceeded as exc:
            return _fail("quota_exceeded", str(exc))

        # 同名文档不重复传：模型重试、用户说两遍都会走到这里。
        # 覆盖是不可逆操作，交给人在页面上做。
        try:
            existing_docs = await client.list_documents(target_id, name=file_name)
        except Exception as exc:
            logger.warning("[upload_document_to_knowledge_base] 同名检查失败: %s", exc)
            existing_docs = []
        for doc in existing_docs or []:
            if str((doc or {}).get("name") or "") == file_name:
                return _fail(
                    "document_exists",
                    f"该知识库里已经有同名文档「{file_name}」（document_id={doc.get('id')}），"
                    "未重复上传。请先问用户：是要保留旧版、换个文件名，还是到页面上更新它。",
                )

        # 两阶段的闸门：默认只回预览，不产生任何副作用。
        # 传错库撤不回来（工具没有删除/移动能力，且库里原有的其他人会立刻检索到），
        # 所以"手滑直传"这条路在结构上就不该存在。
        if not confirm:
            size_mb = round(size / (1024 * 1024), 2)
            return _ok(
                stage="preview",
                dataset_id=target_id,
                dataset_name=record_name,
                file_name=file_name,
                size_bytes=size,
                size_mb=size_mb,
                will_parse=bool(auto_parse),
                message=(
                    f"预览：将把「{file_name}」（{size_mb}MB）上传到知识库"
                    f"「{record_name or target_id}」"
                    + ("，上传后自动解析。" if auto_parse else "，上传后不自动解析。")
                    + "尚未上传。请把目标库与文件名复述给用户，"
                    "得到明确同意后再以 confirm=true 重新调用。"
                ),
            )

        try:
            blob = path.read_bytes()
            uploaded = await client.upload_document(
                target_id,
                file_name,
                blob,
                "application/octet-stream",
            )
        except Exception as exc:
            logger.error("[upload_document_to_knowledge_base] 上传失败: %s", exc, exc_info=True)
            await _audit(
                "documents:upload",
                ctx,
                {"action": "upload_document", "dataset_id": target_id, "file_name": file_name},
                status_code=502,
                error_message=str(exc),
            )
            return _fail("upstream_error", f"上传到 RAGFlow 失败：{exc}")

    document_id = str(
        (uploaded or {}).get("id")
        or (uploaded or {}).get("document_id")
        or ""
    )

    parsed = False
    parse_error = ""
    if auto_parse and document_id:
        try:
            await client.parse_documents(target_id, [document_id])
            parsed = True
        except Exception as exc:
            # 解析失败不算上传失败：文件已经在库里，用户可以到页面重试解析。
            # 这里如果把整个调用报错，模型会以为要重传，产生重复文档。
            logger.warning(
                "[upload_document_to_knowledge_base] 触发解析失败 dataset=%s doc=%s: %s",
                target_id, document_id, exc,
            )
            parse_error = str(exc)

    await _audit(
        "documents:upload",
        ctx,
        {
            "action": "upload_document",
            "dataset_id": target_id,
            "file_name": file_name,
            "size": size,
        },
    )

    dataset_label = record_name or target_id
    message = (
        f"已上传「{file_name}」到知识库「{dataset_label}」，并已开始解析，稍后即可检索。"
        if parsed
        else (
            f"已上传「{file_name}」到知识库「{dataset_label}」，但自动解析没有触发成功，"
            "需要到页面点「解析」后才能检索。"
            if auto_parse
            else f"已上传「{file_name}」到知识库「{dataset_label}」，尚未解析，"
            "需要到页面点「解析」后才能检索。"
        )
    )

    return _ok(
        stage="uploaded",
        document_id=document_id,
        dataset_id=target_id,
        dataset_name=record_name,
        file_name=file_name,
        size=size,
        parsed=parsed,
        parse_error=parse_error or None,
        message=message,
    )
