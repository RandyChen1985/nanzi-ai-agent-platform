"""用户管理：Excel 批量导入用户（模板下载 / 预览 / 批量建号）。

三个端点都要求 ``element:user:edit``，与「创建用户」同一权限码——导入只是把
逐条建号换成批量，不应该因为入口多了一个就放宽权限。

安全与可恢复设计：

* 预览不写库，只回报逐行校验结果；能不能导入由管理员在确认页决定。
* 确认接口**逐行独立事务**：某一行撞唯一索引只让该行失败，已成功的账号保留并
  在结果页列出，避免 500 行里第 300 行出错就全部回滚、管理员还要重新对一遍。
* 明文密码只在确认响应（以及管理员显式开启的密码邮件）里出现一次，不写日志、
  不落库；库里只有 bcrypt 哈希。
"""

from __future__ import annotations

import asyncio
import logging
from typing import Any, Dict, List, Optional
from urllib.parse import quote

from fastapi import APIRouter, Depends, File, HTTPException, UploadFile
from fastapi.responses import Response
from pydantic import BaseModel, Field
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.dependencies import require_permission
from app.core.orm import get_db_session
from app.models.permission import Role
from app.models.user import User
from app.services.auth_service import AuthService
from app.services.email_delivery_service import (
    EmailDeliveryService,
    build_account_created_mail,
)
from app.services.user_import_service import (
    MAX_IMPORT_BYTES,
    MAX_IMPORT_ROWS,
    ParsedImportRow,
    UserImportError,
    build_template_workbook,
    find_default_role,
    generate_initial_password,
    match_role,
    parse_import_workbook,
    validate_fixed_password,
    validate_user_name,
)
logger = logging.getLogger(__name__)

router = APIRouter()

XLSX_MEDIA_TYPE = "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"


class ImportConfirmRow(BaseModel):
    user_name: str
    real_name: Optional[str] = None
    email: Optional[str] = None
    role_id: Optional[int] = None


class ImportConfirmRequest(BaseModel):
    rows: List[ImportConfirmRow] = Field(default_factory=list)
    # random：每个账号独立随机密码；fixed：所有账号共用 fixed_password
    password_mode: str = "random"
    fixed_password: Optional[str] = None
    # 仅管理员显式打开时才把账号与初始密码发到对应邮箱
    send_password_email: bool = False


def _role_payload(role: Any) -> Dict[str, Any]:
    return {
        "id": role.id,
        "code": role.code,
        "name": role.name,
        "description": role.description,
    }


async def _load_roles(db: AsyncSession) -> List[Role]:
    result = await db.execute(select(Role).order_by(Role.id.asc()))
    return list(result.scalars().all())


async def _existing_names(db: AsyncSession, names: List[str]) -> set[str]:
    lowered = sorted({name.strip().lower() for name in names if name.strip()})
    if not lowered:
        return set()
    result = await db.execute(
        select(func.lower(User.user_name)).where(func.lower(User.user_name).in_(lowered))
    )
    return {row[0] for row in result.all() if row[0]}


async def _existing_emails(db: AsyncSession, emails: List[str]) -> set[str]:
    lowered = sorted({email.strip().lower() for email in emails if email.strip()})
    if not lowered:
        return set()
    result = await db.execute(
        select(func.lower(User.email)).where(func.lower(User.email).in_(lowered))
    )
    return {row[0] for row in result.all() if row[0]}


async def _deliver_credential_mails(
    settings: Any,
    jobs: List[Dict[str, Any]],
    concurrency: int = 5,
) -> None:
    """把账号与初始密码并发发到各用户邮箱，结果写回 job["item"]。

    邮件是阻塞的 SMTP 调用，用线程池 + 信号量限并发：串行发 500 封会让请求
    挂几十分钟，无限并发又容易被邮件网关限流。单封失败只影响该行，不抛异常。
    """

    if not jobs:
        return

    semaphore = asyncio.Semaphore(concurrency)

    async def deliver(job: Dict[str, Any]) -> None:
        item = job["item"]
        async with semaphore:
            subject, body = build_account_created_mail(
                item["user_name"], job["password"], settings.sender_name
            )
            try:
                ok, message = await asyncio.to_thread(
                    EmailDeliveryService.send_mail,
                    settings,
                    [job["email"]],
                    subject,
                    body,
                )
            except Exception as exc:  # 邮件是尽力而为，不能影响已创建的账号
                ok, message = False, "邮件发送异常，请检查邮件服务配置"
                logger.warning(
                    "导入账号密码邮件发送异常: user=%s error=%s",
                    item["user_name"],
                    exc,
                )
            item["email_sent"] = bool(ok)
            if not ok:
                item["email_error"] = message or "邮件发送失败"
                logger.warning(
                    "导入账号密码邮件发送失败: user=%s reason=%s",
                    item["user_name"],
                    message,
                )

    await asyncio.gather(*(deliver(job) for job in jobs))


@router.get("/users/import/template", summary="下载用户导入模板")
async def download_import_template(
    admin: dict = Depends(require_permission("element", "element:user:edit")),
    db: AsyncSession = Depends(get_db_session),
):
    """生成并下载 .xlsx 模板，说明页附带当前全部业务角色编码与名称。"""

    roles = await _load_roles(db)
    content = build_template_workbook(roles)

    # 中文文件名走 RFC 5987；同时给一个 ASCII 兜底名，老浏览器也能保存成功
    filename = "用户导入模板.xlsx"
    disposition = (
        "attachment; filename=user_import_template.xlsx; "
        f"filename*=UTF-8''{quote(filename)}"
    )
    return Response(
        content=content,
        media_type=XLSX_MEDIA_TYPE,
        headers={"Content-Disposition": disposition},
    )


@router.post("/users/import/preview", summary="解析 Excel 并预览导入结果")
async def preview_import(
    file: UploadFile = File(...),
    admin: dict = Depends(require_permission("element", "element:user:edit")),
    db: AsyncSession = Depends(get_db_session),
):
    """解析上传文件，返回逐行可编辑的确认数据，不写库。"""

    filename = (file.filename or "").strip()
    if filename and not filename.lower().endswith(".xlsx"):
        raise HTTPException(status_code=400, detail="仅支持 .xlsx 格式的 Excel 文件")

    content = await file.read()
    if len(content) > MAX_IMPORT_BYTES:
        raise HTTPException(status_code=400, detail="文件超过 2MB，请拆分后分批导入")

    try:
        parsed_rows = parse_import_workbook(content)
    except UserImportError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc

    roles = await _load_roles(db)
    role_payloads = [_role_payload(role) for role in roles]
    default_role = find_default_role(roles)

    existing_names = await _existing_names(db, [row.user_name for row in parsed_rows])
    existing_emails = await _existing_emails(db, [row.email for row in parsed_rows])

    # 文件内部的重复账号：两行都标出来，只标后一行会让管理员以为修好了第一行
    name_counts: Dict[str, int] = {}
    for row in parsed_rows:
        key = row.user_name.strip().lower()
        if key:
            name_counts[key] = name_counts.get(key, 0) + 1

    items: List[Dict[str, Any]] = []
    for row in parsed_rows:
        items.append(
            _preview_item(
                row=row,
                roles=roles,
                default_role=default_role,
                existing_names=existing_names,
                existing_emails=existing_emails,
                name_counts=name_counts,
            )
        )

    return {
        "filename": filename or "上传文件.xlsx",
        "total": len(items),
        "roles": role_payloads,
        "default_role_id": default_role.id if default_role is not None else None,
        "email_service_available": (
            await EmailDeliveryService.load_global_smtp_settings()
        ) is not None,
        "items": items,
    }


def _preview_item(
    *,
    row: ParsedImportRow,
    roles: List[Role],
    default_role: Optional[Role],
    existing_names: set[str],
    existing_emails: set[str],
    name_counts: Dict[str, int],
) -> Dict[str, Any]:
    name = row.user_name.strip()
    key = name.lower()
    real_name = row.real_name.strip()
    email_raw = row.email.strip()
    role_input = row.role_input.strip()

    issues: List[str] = []

    name_issue = validate_user_name(name)
    if name_issue:
        issues.append(name_issue)

    if len(real_name) > 50:
        issues.append("姓名长度不能超过 50 个字符")

    email: Optional[str] = None
    if not email_raw:
        issues.append("邮箱不能为空")
    else:
        try:
            email = AuthService.normalize_email(email_raw, strict=True)
        except ValueError:
            issues.append("邮箱格式不正确")

    role: Optional[Role] = None
    role_from_default = False
    if role_input:
        role = match_role(role_input, roles)
        if role is None:
            issues.append(f"角色「{role_input}」未匹配到内置角色，请在确认页选择")
    else:
        role = default_role
        role_from_default = role is not None
        if role is None:
            issues.append("系统里还没有默认角色 default，请先创建业务角色或手动选择")

    existed = bool(key) and key in existing_names
    if existed:
        issues.append("账号已存在，不会导入；请取消勾选或修改账号名")

    email_taken = bool(email) and email in existing_emails
    if email_taken:
        issues.append("邮箱已被其他账号占用，不会导入；请更换邮箱或取消勾选")

    if key and name_counts.get(key, 0) > 1:
        issues.append("文件内账号重复，请保留一行")

    return {
        "row": row.row_number,
        "user_name": name,
        "real_name": real_name,
        "email": email or email_raw,
        "role_input": role_input,
        "role_id": role.id if role is not None else None,
        "role_code": role.code if role is not None else None,
        "role_name": role.name if role is not None else None,
        "role_from_default": role_from_default,
        "role_matched": role is not None,
        "system_role": "user",
        "existed": existed,
        "email_taken": email_taken,
        "importable": not issues,
        "issues": issues,
    }


@router.post("/users/import/confirm", summary="确认并批量创建导入账号")
async def confirm_import(
    request: ImportConfirmRequest,
    admin: dict = Depends(require_permission("element", "element:user:edit")),
    db: AsyncSession = Depends(get_db_session),
):
    """逐行创建账号：生成 API Key、初始密码并绑定业务角色，可选发送密码邮件。"""

    if not request.rows:
        raise HTTPException(status_code=400, detail="没有需要导入的账号")
    if len(request.rows) > MAX_IMPORT_ROWS:
        raise HTTPException(
            status_code=400,
            detail=f"单次最多导入 {MAX_IMPORT_ROWS} 行，请拆分后分批导入",
        )

    password_mode = (request.password_mode or "random").strip().lower()
    if password_mode not in ("random", "fixed"):
        raise HTTPException(status_code=400, detail="密码方式只能是随机密码或固定密码")

    fixed_password: Optional[str] = None
    if password_mode == "fixed":
        fixed_password = request.fixed_password or ""
        if not fixed_password:
            raise HTTPException(status_code=400, detail="请填写固定密码")
        # 建号前对全部行统一校验：固定密码包含某个账号名时，不能建到一半才失败
        password_issue = validate_fixed_password(
            fixed_password, [row.user_name for row in request.rows]
        )
        if password_issue:
            raise HTTPException(status_code=400, detail=password_issue)

    mail_settings = None
    if request.send_password_email:
        mail_settings = await EmailDeliveryService.load_global_smtp_settings()

    roles = await _load_roles(db)
    role_map = {role.id: role for role in roles}

    existing_names = await _existing_names(db, [row.user_name for row in request.rows])
    existing_emails = await _existing_emails(db, [row.email or "" for row in request.rows])

    seen_names: set[str] = set()
    created: List[Dict[str, Any]] = []
    failed: List[Dict[str, Any]] = []
    email_jobs: List[Dict[str, Any]] = []

    for row in request.rows:
        name = (row.user_name or "").strip()
        real_name = (row.real_name or "").strip()
        email = (row.email or "").strip()
        try:
            name_issue = validate_user_name(name)
            if name_issue:
                raise UserImportError(name_issue)

            if len(real_name) > 50:
                raise UserImportError("姓名长度不能超过 50 个字符")

            key = name.lower()
            if key in seen_names:
                raise UserImportError("本次提交中账号重复，请只保留一行")
            seen_names.add(key)

            if key in existing_names:
                raise UserImportError("账号已存在，未导入")

            if not email:
                raise UserImportError("邮箱不能为空")
            try:
                normalized_email = AuthService.normalize_email(email, strict=True)
            except ValueError as exc:
                raise UserImportError("邮箱格式不正确") from exc
            if not normalized_email:
                raise UserImportError("邮箱不能为空")
            if normalized_email in existing_emails:
                raise UserImportError("邮箱已被其他账号占用，未导入")

            # 导入账号一律是普通用户：确认页不再提供身份切换，也不接受客户端传
            # admin，避免「批量导入」成为绕过单人建号流程的提权入口。
            system_role = "user"

            role: Optional[Role] = None
            if row.role_id is not None:
                role = role_map.get(row.role_id)
                if role is None:
                    raise UserImportError("所选角色不存在，请刷新后重新选择")

            password = fixed_password if password_mode == "fixed" else generate_initial_password(name)

            # 复用 AuthService 的建号唯一入口：API Key、密码哈希与角色关系一次提交，
            # 避免出现第二个「直接构造 User」的实现而绕开后续的建号约束。
            await AuthService.generate_api_key(
                name,
                role=system_role,
                real_name=real_name or None,
                email=normalized_email,
                remark="Excel 批量导入",
                db=db,
                password=password,
                business_role_ids=[role.id] if role is not None else [],
            )

            saved = await db.execute(select(User).where(User.user_name == name))
            user = saved.scalar_one()

            item = {
                "user_id": user.id,
                "user_name": user.user_name,
                "real_name": user.real_name,
                "email": user.email,
                "system_role": system_role,
                "role_name": role.name if role is not None else None,
                "password": password,
                "email_sent": False,
                "email_error": None,
            }
            created.append(item)
            if request.send_password_email:
                email_jobs.append(
                    {"item": item, "email": normalized_email, "password": password}
                )
            existing_names.add(key)
            existing_emails.add(normalized_email)
        except Exception as exc:  # 单行失败不影响其余行
            await db.rollback()
            if isinstance(exc, UserImportError):
                error = str(exc)
            else:
                logger.warning("Excel 导入创建账号失败: %s", exc, exc_info=True)
                error = "创建失败，请检查账号或邮箱是否已被占用后重试"
            failed.append(
                {
                    "user_name": name,
                    "email": email,
                    "error": error,
                }
            )

    email_failed_count = 0
    if request.send_password_email:
        if mail_settings is None:
            # 账号已经创建成功，不能因为邮件服务没配置就回滚；逐行标注失败原因，
            # 让管理员在结果页改用复制/下载自行分发。
            for job in email_jobs:
                job["item"]["email_error"] = "平台邮件服务未启用或配置不完整"
            email_failed_count = len(email_jobs)
        else:
            await _deliver_credential_mails(mail_settings, email_jobs)
            email_failed_count = sum(1 for job in email_jobs if not job["item"]["email_sent"])

    email_sent_count = (
        sum(1 for job in email_jobs if job["item"]["email_sent"])
        if request.send_password_email
        else 0
    )

    return {
        "created": created,
        "failed": failed,
        "success_count": len(created),
        "failed_count": len(failed),
        "password_mode": password_mode,
        "send_password_email": request.send_password_email,
        "email_sent_count": email_sent_count,
        "email_failed_count": email_failed_count,
    }
