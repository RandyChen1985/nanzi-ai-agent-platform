"""工具包（tool bundle）的导出/导入逻辑。

设计文档：``docs/superpowers/specs/2026-10-30-tool-bundle-json-sharing-design.md``

这里只放**可单测的纯逻辑**与薄薄的落库动作，路由在
``app/api/portal/endpoints/tool_bundles.py``。两条硬约束：

1. 凭据永不导出：header 名命中凭据规则时值只写 ``${CREDENTIAL:<name>}``；
   签名密钥与 ``signing_key_id`` 完全不进文件。
2. 覆盖不能冲掉对方正在用的东西：导入的 header 值是占位符而目标行已有真实值时，
   保留目标值；签名密钥同样保留。
"""

from __future__ import annotations

import json
import uuid
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Dict, Iterable, List, Mapping, Optional, Sequence, Tuple

from pydantic import ValidationError
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.mcp import McpToolCache
from app.models.script_tool import SysScriptTool
from app.models.tool import SysApiTool
from app.schemas.script_tool import SysScriptToolCreate
from app.schemas.tool import SysApiToolCreate
from app.services.ai.tools.registry import ToolRegistry
from app.services.ai.tools.script_tool_builtins import get_builtin_script_tool
from app.services.ai.tools.script_tool_factory import EffectiveScriptTool
from app.services.ai.tools.tool_credentials import (
    credential_placeholder,
    is_credential_header,
    is_credential_placeholder,
    mask_headers,
    unresolved_credential_headers,
)

# --------------------------------------------------------------------- 常量

BUNDLE_SCHEMA = "nanzi.tool.bundle"
BUNDLE_SCHEMA_VERSION = 1
MAX_BUNDLE_BYTES = 8 * 1024 * 1024

TOOL_KINDS = ("api", "script")


class ToolBundleError(ValueError):
    """文件级错误（结构不对、schema 不符、版本过高）——整份拒绝，不做逐条容错。"""


# --------------------------------------------------------------------- 脱敏
# 规则实现见 ``app/services/ai/tools/tool_credentials.py``：运行时兜底与前端徽标
# 判断的是同一个前缀，规则只能有一份。


def merge_headers_for_import(
    incoming: Optional[Mapping[str, Any]],
    existing: Optional[Mapping[str, Any]],
) -> Tuple[Dict[str, str], List[str]]:
    """覆盖导入时的 header 合并。

    导入值仍是占位符、而目标行已有同名**真实**值 → 保留目标值。否则一次覆盖就会把
    对方正在使用的 token 换成占位符，工具当场失效。
    """
    existing_map = {str(k): "" if v is None else str(v) for k, v in (existing or {}).items()}
    merged: Dict[str, str] = {}
    for raw_name, raw_value in (incoming or {}).items():
        name = str(raw_name)
        value = "" if raw_value is None else str(raw_value)
        if is_credential_placeholder(value):
            kept = existing_map.get(name)
            if kept and not is_credential_placeholder(kept):
                value = kept
        merged[name] = value
    return merged, unresolved_credential_headers(merged)


# --------------------------------------------------------------------- 构建文件


def _json_dict(value: Any) -> Dict[str, Any]:
    if isinstance(value, str):
        try:
            parsed = json.loads(value) if value else {}
        except ValueError:
            return {}
        return parsed if isinstance(parsed, dict) else {}
    return dict(value) if isinstance(value, dict) else {}


def _json_list(value: Any) -> List[str]:
    if isinstance(value, str):
        try:
            parsed = json.loads(value) if value else []
        except ValueError:
            return []
        value = parsed
    if not isinstance(value, (list, tuple)):
        return []
    return [str(item) for item in value if str(item).strip()]


def build_api_item(row: SysApiTool) -> Dict[str, Any]:
    headers = _json_dict(row.headers)
    masked, requires = mask_headers(headers)
    return {
        "kind": "api",
        "name": row.name,
        "description": row.description,
        "method": row.method,
        "url_template": row.url_template,
        "headers": masked,
        "parameter_schema": _json_dict(row.parameter_schema),
        "signing_enabled": bool(row.signing_enabled),
        "signing_algorithm": row.signing_algorithm or "hmac-sha256",
        "group_name": row.group_name,
        "is_active": bool(row.is_active),
        "requires_credentials": requires,
    }


def build_script_item(effective: EffectiveScriptTool) -> Dict[str, Any]:
    return {
        "kind": "script",
        "name": effective.name,
        "display_name": effective.display_name,
        "description": effective.description,
        # 内置脚本按自定义工具流通：对方平台不需要、也不该把它当成自带内置项
        "source": "custom",
        "language": effective.language or "python",
        "code": effective.code or "",
        "parameter_schema": _json_dict(effective.parameter_schema),
        "requirements": _json_list(effective.requirements),
        "timeout_seconds": effective.timeout_seconds,
        "group_name": effective.group_name,
        "is_active": bool(effective.is_active),
    }


def build_bundle(
    *,
    api_rows: Iterable[SysApiTool] = (),
    script_effectives: Iterable[EffectiveScriptTool] = (),
    exported_by: Optional[str] = None,
    now: Optional[datetime] = None,
) -> Dict[str, Any]:
    tools: List[Dict[str, Any]] = [build_api_item(row) for row in api_rows]
    tools.extend(build_script_item(item) for item in script_effectives)
    return {
        "schema": BUNDLE_SCHEMA,
        "schema_version": BUNDLE_SCHEMA_VERSION,
        "exported_at": (now or datetime.now().astimezone()).isoformat(timespec="seconds"),
        "exported_by": exported_by,
        "tools": tools,
    }


def bundle_filename(now: Optional[datetime] = None) -> str:
    return f"nanzi-tools-{(now or datetime.now()).strftime('%Y%m%d-%H%M')}.json"


# --------------------------------------------------------------------- 解析


@dataclass
class BundleItem:
    index: int
    kind: str
    name: str
    raw: Dict[str, Any]
    payload: Any = None  # SysApiToolCreate | SysScriptToolCreate
    error: Optional[str] = None

    @property
    def is_valid(self) -> bool:
        return self.payload is not None and not self.error


@dataclass
class ParsedBundle:
    items: List[BundleItem]
    exported_at: Optional[str] = None
    exported_by: Optional[str] = None


def _validation_message(exc: ValidationError) -> str:
    first = exc.errors()[0] if exc.errors() else {}
    location = ".".join(str(part) for part in first.get("loc", ()) if part != "__root__")
    message = str(first.get("msg") or "字段不合法")
    return f"{location}：{message}" if location else message


def parse_bundle(payload: Any) -> ParsedBundle:
    """把上传的 JSON 解析成可导入条目。

    文件级问题（schema/版本/结构）直接抛 ``ToolBundleError``；单条问题只记录在
    ``BundleItem.error`` 上，不拖累其他工具——分享场景里经常是「10 个里 1 个字段过时」。
    """
    if not isinstance(payload, Mapping):
        raise ToolBundleError("文件内容不是 JSON 对象")

    schema = payload.get("schema")
    if schema != BUNDLE_SCHEMA:
        raise ToolBundleError(
            f"不是工具包文件：schema 应为 {BUNDLE_SCHEMA}，实际为 {schema!r}"
        )

    version = payload.get("schema_version")
    if not isinstance(version, int) or isinstance(version, bool):
        raise ToolBundleError("schema_version 缺失或不是整数")
    if version > BUNDLE_SCHEMA_VERSION:
        raise ToolBundleError(
            f"文件由更新版本的平台导出（schema_version={version}，当前支持 {BUNDLE_SCHEMA_VERSION}）"
        )
    if version < 1:
        raise ToolBundleError(f"不支持的 schema_version：{version}")

    raw_tools = payload.get("tools")
    if not isinstance(raw_tools, list) or not raw_tools:
        raise ToolBundleError("文件里没有工具（tools 为空）")

    items: List[BundleItem] = []
    for index, raw in enumerate(raw_tools):
        if not isinstance(raw, Mapping):
            items.append(BundleItem(index=index, kind="", name="", raw={}, error="条目不是 JSON 对象"))
            continue
        raw_dict = dict(raw)
        kind = str(raw_dict.get("kind") or "").strip().lower()
        name = str(raw_dict.get("name") or "").strip()
        if kind not in TOOL_KINDS:
            items.append(
                BundleItem(
                    index=index,
                    kind=kind,
                    name=name,
                    raw=raw_dict,
                    error=f"未知的工具类型 kind={kind!r}",
                )
            )
            continue
        item = BundleItem(index=index, kind=kind, name=name, raw=raw_dict)
        try:
            item.payload = _validate_item(kind, raw_dict)
        except ValidationError as exc:
            item.error = _validation_message(exc)
        except ValueError as exc:
            item.error = str(exc)
        items.append(item)

    return ParsedBundle(
        items=items,
        exported_at=payload.get("exported_at"),
        exported_by=payload.get("exported_by"),
    )


def _validate_item(kind: str, raw: Mapping[str, Any]) -> Any:
    """交给新建工具的那套 schema 校验——导入与手工新建必须同一口径。"""
    if not str(raw.get("name") or "").strip():
        raise ValueError("缺少工具名 name")
    if kind == "api":
        payload = _api_payload(raw)
        # 现有新建表单靠前端拦空地址；导入没有表单，必须在这里拦。
        # 空 URL 入库后每次调用都必然失败，属于无意义数据。
        if not str(payload.get("url_template") or "").strip():
            raise ValueError("url_template 不能为空")
        return SysApiToolCreate(**payload)
    payload = _script_payload(raw)
    return SysScriptToolCreate(**payload)


#: 文件里允许出现、但数据库行不需要的字段。多出来的键一律忽略而不是报错，
#: 这样以后加字段（例如 ``requires_credentials``）不会让旧平台导入失败。
_API_ITEM_KEYS = {
    "kind",
    "name",
    "description",
    "method",
    "url_template",
    "headers",
    "parameter_schema",
    "signing_enabled",
    "signing_algorithm",
    "group_name",
    "is_active",
    "requires_credentials",
}

_SCRIPT_ITEM_KEYS = {
    "kind",
    "name",
    "display_name",
    "description",
    "source",
    "language",
    "code",
    "parameter_schema",
    "requirements",
    "timeout_seconds",
    "group_name",
    "is_active",
}


def _api_payload(raw: Mapping[str, Any]) -> Dict[str, Any]:
    payload = {key: raw[key] for key in _API_ITEM_KEYS if key in raw and key != "kind"}
    # 导入端不接受文件里的签名密钥：它本来就不在契约里，出现即忽略
    if not isinstance(payload.get("headers"), (dict, type(None))):
        raise ValueError("headers 必须是对象")
    return payload


def _script_payload(raw: Mapping[str, Any]) -> Dict[str, Any]:
    payload = {key: raw[key] for key in _SCRIPT_ITEM_KEYS if key in raw and key != "kind"}
    payload["source"] = "custom"  # 文件里的 source 不作数：内置身份只由本平台代码库决定
    return payload


# --------------------------------------------------------------------- 逐条预检


@dataclass
class PrecheckItem:
    index: int
    kind: str
    name: str
    status: str  # new | conflict | invalid
    reason: str = ""
    can_overwrite: bool = False
    suggested_name: Optional[str] = None
    requires_credentials: List[str] = field(default_factory=list)

    def as_dict(self) -> Dict[str, Any]:
        return {
            "index": self.index,
            "kind": self.kind,
            "name": self.name,
            "status": self.status,
            "reason": self.reason,
            "can_overwrite": self.can_overwrite,
            "suggested_name": self.suggested_name,
            "requires_credentials": list(self.requires_credentials),
        }


async def find_api_tool(db: AsyncSession, name: str) -> Optional[SysApiTool]:
    return (await db.execute(select(SysApiTool).where(SysApiTool.name == name))).scalars().first()


async def find_script_tool(db: AsyncSession, name: str) -> Optional[SysScriptTool]:
    return (await db.execute(select(SysScriptTool).where(SysScriptTool.name == name))).scalars().first()


async def _mcp_has_tool(db: AsyncSession, name: str) -> bool:
    return (await db.execute(select(McpToolCache.id).where(McpToolCache.tool_name == name))).first() is not None


async def name_conflict_reason(
    db: AsyncSession,
    name: str,
    *,
    exclude_id: Optional[str] = None,
) -> Optional[str]:
    """名字为什么不能用；可用时返回 ``None``。

    口径与脚本工具新建时一致（``_assert_name_available``）：名字在平台内是全局的，
    registry 按 name 解析，跨类型重名会出现「命中谁看运气」。
    """
    if name in ToolRegistry.known_static_tool_names():
        return f"工具名 {name} 与平台内置工具重名"
    if get_builtin_script_tool(name) is not None:
        return f"工具名 {name} 与内置脚本工具重名"

    stmt = select(SysScriptTool.id).where(SysScriptTool.name == name)
    if exclude_id:
        stmt = stmt.where(SysScriptTool.id != exclude_id)
    if (await db.execute(stmt)).first():
        return f"工具名 {name} 已被其他脚本工具使用"

    api_stmt = select(SysApiTool.id).where(SysApiTool.name == name)
    if exclude_id:
        api_stmt = api_stmt.where(SysApiTool.id != exclude_id)
    if (await db.execute(api_stmt)).first():
        return f"工具名 {name} 已被 API 工具使用"

    if await _mcp_has_tool(db, name):
        return f"工具名 {name} 已被 MCP 工具使用"
    return None


async def _suggest_available_name(db: AsyncSession, name: str, *, exclude_id: Optional[str] = None) -> str:
    base = name or "tool"
    for suffix in range(2, 52):
        candidate = f"{base}_{suffix}"
        if await name_conflict_reason(db, candidate, exclude_id=exclude_id) is None:
            return candidate
    return f"{base}_{uuid.uuid4().hex[:6]}"


async def precheck_bundle(db: AsyncSession, parsed: ParsedBundle) -> List[PrecheckItem]:
    results: List[PrecheckItem] = []
    for item in parsed.items:
        if not item.is_valid:
            results.append(
                PrecheckItem(
                    index=item.index,
                    kind=item.kind,
                    name=item.name,
                    status="invalid",
                    reason=item.error or "条目校验失败",
                )
            )
            continue

        api_row = await find_api_tool(db, item.name)
        script_row = await find_script_tool(db, item.name)
        requires: List[str] = []
        if item.kind == "api":
            requires = list(item.payload.headers or {})
            requires = [name for name in requires if is_credential_header(name)]

        same_kind_row = api_row if item.kind == "api" else script_row
        target_is_builtin = (
            item.kind == "script" and script_row is not None and (script_row.source or "") == "builtin"
        )

        if same_kind_row is None:
            reason = await name_conflict_reason(db, item.name)
            if reason is None:
                results.append(
                    PrecheckItem(
                        index=item.index,
                        kind=item.kind,
                        name=item.name,
                        status="new",
                        requires_credentials=requires,
                    )
                )
                continue
            # 名字被别的类型 / MCP / 内置占用：能导入，但必须先改名
            results.append(
                PrecheckItem(
                    index=item.index,
                    kind=item.kind,
                    name=item.name,
                    status="conflict",
                    reason=reason,
                    can_overwrite=False,
                    suggested_name=await _suggest_available_name(db, item.name),
                    requires_credentials=requires,
                )
            )
            continue

        if target_is_builtin:
            results.append(
                PrecheckItem(
                    index=item.index,
                    kind=item.kind,
                    name=item.name,
                    status="conflict",
                    reason="目标是平台内置脚本，不能被外部文件覆盖；可另存为新工具",
                    can_overwrite=False,
                    suggested_name=await _suggest_available_name(db, item.name, exclude_id=script_row.id),
                    requires_credentials=requires,
                )
            )
            continue

        results.append(
            PrecheckItem(
                index=item.index,
                kind=item.kind,
                name=item.name,
                status="conflict",
                reason="同名工具已存在",
                can_overwrite=True,
                suggested_name=await _suggest_available_name(
                    db, item.name, exclude_id=(same_kind_row.id if same_kind_row else None)
                ),
                requires_credentials=requires,
            )
        )
    return results


# --------------------------------------------------------------------- 决策与落库


@dataclass
class ImportDecision:
    index: int
    action: str  # skip | overwrite | rename
    rename_to: Optional[str] = None


ALLOWED_ACTIONS = ("create", "skip", "overwrite", "rename")


def normalize_decisions(raw: Optional[Sequence[Mapping[str, Any]]]) -> List[ImportDecision]:
    decisions: List[ImportDecision] = []
    for entry in raw or []:
        if not isinstance(entry, Mapping):
            raise ToolBundleError("decisions 的每一项必须是对象")
        index = entry.get("index")
        action = str(entry.get("action") or "").strip().lower()
        if not isinstance(index, int) or isinstance(index, bool):
            raise ToolBundleError("decisions.index 必须是整数")
        if action not in ALLOWED_ACTIONS:
            raise ToolBundleError(f"decisions.action 非法：{action!r}（可用 create/skip/overwrite/rename）")
        rename_to = entry.get("rename_to")
        decisions.append(
            ImportDecision(
                index=index,
                action=action,
                rename_to=str(rename_to).strip() if rename_to is not None else None,
            )
        )
    return decisions


def plan_import(
    parsed: ParsedBundle,
    precheck: Sequence[PrecheckItem],
    decisions: Sequence[ImportDecision],
) -> List[Tuple[BundleItem, PrecheckItem, ImportDecision]]:
    """把决策与预检对齐，并在写库之前把非法决策全部拦下（避免半套数据）。"""
    by_index = {item.index: item for item in precheck}
    decision_by_index: Dict[int, ImportDecision] = {}
    for decision in decisions:
        if decision.index in decision_by_index:
            raise ToolBundleError(f"decisions 里重复给出了第 {decision.index} 条")
        decision_by_index[decision.index] = decision

    plan: List[Tuple[BundleItem, PrecheckItem, ImportDecision]] = []
    for item in parsed.items:
        check = by_index.get(item.index)
        if check is None:
            raise ToolBundleError(f"第 {item.index} 条缺少预检结果")
        decision = decision_by_index.get(item.index) or ImportDecision(index=item.index, action="skip")

        if decision.action == "skip":
            continue

        if check.status == "invalid":
            raise ToolBundleError(f"第 {item.index} 条（{item.name}）校验未通过，不能导入：{check.reason}")

        if decision.action == "create":
            # 新条目按原名新建；名字被占用时不能走这条，必须让用户显式选覆盖或另存
            if check.status != "new":
                raise ToolBundleError(
                    f"第 {item.index} 条（{item.name}）同名已存在，不能直接新建；请选择覆盖或另存"
                )
            plan.append((item, check, decision))
            continue

        if decision.action == "overwrite":
            if not check.can_overwrite:
                raise ToolBundleError(
                    f"第 {item.index} 条（{item.name}）不能覆盖：{check.reason or '目标不可覆盖'}"
                )
            plan.append((item, check, decision))
            continue

        if decision.action == "rename":
            target = decision.rename_to or check.suggested_name
            if not target:
                raise ToolBundleError(f"第 {item.index} 条缺少另存名")
            if target == item.name:
                raise ToolBundleError(f"第 {item.index} 条的另存名与原名称相同")
            decision = ImportDecision(index=decision.index, action="rename", rename_to=target)
            plan.append((item, check, decision))

    return plan


async def validate_plan_names(db: AsyncSession, plan: Sequence[Tuple[BundleItem, PrecheckItem, ImportDecision]]) -> None:
    """另存名在提交前再校验一次：预检与提交之间可能已被别人占用。"""
    seen: set[str] = set()
    for item, check, decision in plan:
        if decision.action == "create":
            target = item.name
            if target in seen:
                raise ToolBundleError(f"工具名 {target} 在本次导入里重复出现")
            seen.add(target)
            reason = await name_conflict_reason(db, target)
            if reason is not None:
                raise ToolBundleError(f"工具名不可用：{reason}")
            continue
        if decision.action != "rename":
            continue
        target = decision.rename_to or ""
        if target in seen:
            raise ToolBundleError(f"另存名 {target} 在本次导入里重复出现")
        seen.add(target)
        reason = await name_conflict_reason(db, target)
        if reason is not None:
            raise ToolBundleError(f"另存名不可用：{reason}")


def _apply_api_create(payload: SysApiToolCreate, *, name: Optional[str] = None) -> SysApiTool:
    data = payload.model_dump()
    data.pop("signing_secret", None)
    data["name"] = name or payload.name
    if data.get("headers") is not None:
        data["headers"] = json.dumps(data["headers"], ensure_ascii=False)
    if data.get("parameter_schema") is not None:
        data["parameter_schema"] = json.dumps(data["parameter_schema"], ensure_ascii=False)
    return SysApiTool(id=str(uuid.uuid4()), **data)


def _apply_api_overwrite(row: SysApiTool, payload: SysApiToolCreate) -> List[str]:
    """覆盖已有 API 工具；返回仍待补的凭据 header 名。

    签名密钥与 ``signing_key_id`` 一律不动：文件里本来就没有它们，动一下等于把对方
    已经配好的签名弄坏。
    """
    headers, pending = merge_headers_for_import(payload.headers, _json_dict(row.headers))
    row.description = payload.description
    row.method = payload.method
    row.url_template = payload.url_template
    row.headers = json.dumps(headers, ensure_ascii=False)
    row.parameter_schema = json.dumps(payload.parameter_schema or {}, ensure_ascii=False)
    row.signing_enabled = bool(payload.signing_enabled)
    row.signing_algorithm = payload.signing_algorithm or "hmac-sha256"
    row.group_name = (payload.group_name or "").strip() or None
    row.is_active = bool(payload.is_active)
    return pending


def _apply_script_create(payload: SysScriptToolCreate, *, name: Optional[str] = None) -> SysScriptTool:
    return SysScriptTool(
        id=str(uuid.uuid4()),
        name=name or payload.name,
        display_name=payload.display_name,
        description=payload.description,
        source="custom",
        language=payload.language,
        code=payload.code,
        parameter_schema=json.dumps(payload.parameter_schema or {}, ensure_ascii=False),
        requirements=json.dumps(list(payload.requirements), ensure_ascii=False),
        timeout_seconds=payload.timeout_seconds,
        group_name=payload.group_name,
        is_active=bool(payload.is_active),
    )


def _apply_script_overwrite(row: SysScriptTool, payload: SysScriptToolCreate) -> None:
    row.display_name = payload.display_name
    row.description = payload.description
    row.language = payload.language
    row.code = payload.code
    row.parameter_schema = json.dumps(payload.parameter_schema or {}, ensure_ascii=False)
    row.requirements = json.dumps(list(payload.requirements), ensure_ascii=False)
    row.timeout_seconds = payload.timeout_seconds
    row.group_name = payload.group_name
    row.is_active = bool(payload.is_active)


async def apply_bundle_import(
    db: AsyncSession,
    parsed: ParsedBundle,
    plan: Sequence[Tuple[BundleItem, PrecheckItem, ImportDecision]],
) -> Dict[str, Any]:
    """执行导入。**不 commit**：由 ``get_db_session`` 统一提交/回滚，保证一条不落。"""
    created: List[Dict[str, Any]] = []
    updated: List[Dict[str, Any]] = []
    skipped = len(parsed.items) - len(plan)
    pending_credentials: Dict[str, List[str]] = {}

    for item, check, decision in plan:
        if item.kind == "api":
            payload = item.payload
            if decision.action == "overwrite":
                row = await find_api_tool(db, item.name)
                if row is None:
                    raise ToolBundleError(f"第 {item.index} 条（{item.name}）已不存在，请重新预检")
                pending = _apply_api_overwrite(row, payload)
                if pending:
                    pending_credentials[item.name] = pending
                db.add(row)
                updated.append({"index": item.index, "kind": "api", "name": row.name})
            else:
                name = decision.rename_to or payload.name
                row = _apply_api_create(payload, name=name)
                pending = unresolved_credential_headers(payload.headers or {})
                if pending:
                    pending_credentials[name] = pending
                db.add(row)
                created.append({"index": item.index, "kind": "api", "name": name})
        else:
            payload = item.payload
            if decision.action == "overwrite":
                row = await find_script_tool(db, item.name)
                if row is None:
                    raise ToolBundleError(f"第 {item.index} 条（{item.name}）已不存在，请重新预检")
                if (row.source or "") == "builtin":
                    raise ToolBundleError(f"第 {item.index} 条（{item.name}）是内置脚本，不能覆盖")
                _apply_script_overwrite(row, payload)
                db.add(row)
                updated.append({"index": item.index, "kind": "script", "name": row.name})
            else:
                name = decision.rename_to or payload.name
                db.add(_apply_script_create(payload, name=name))
                created.append({"index": item.index, "kind": "script", "name": name})

    await db.flush()
    if created or updated:
        ToolRegistry.clear_db_tool_cache()

    return {
        "ok": True,
        "created": len(created),
        "updated": len(updated),
        "skipped": max(skipped, 0),
        "created_items": created,
        "updated_items": updated,
        "pending_credentials": pending_credentials,
    }
