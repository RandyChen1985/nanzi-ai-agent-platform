"""查询当前运行时工具可用的结构化凭据账号元数据。"""

from __future__ import annotations

import json

from app.core import orm
from app.services.ai.tools.credential_catalog_tools import _catalog_enabled
from app.services.ai.tools.session_status import mounted_mcp_tool_capability
from app.services.ai.tools.tool_compat import tool
from app.services.secrets import mcp_profiles, type_catalog
from app.services.secrets.credential_schema import account_key
from app.services.secrets.credential_approval import MCP_CREDENTIAL_REF
from app.services.secrets.mcp_capability import MCP_CREDENTIAL_INFO

_MAX_CANDIDATES = 20


def _unavailable(code: str, note: str) -> str:
    return json.dumps({"status": "unavailable", "success": False, "code": code, "note": note}, ensure_ascii=False)


@tool
async def list_mcp_credential_candidates(tool_name: str, account: str = "", keyword: str = "") -> str:
    """仅为 infer（模型推导账号）模式查找已挂载 MCP 工具的结构化凭据账号。

    只返回凭据中心名称、账号和类型。固定凭据由平台自动注入，直接调用业务工具即可，
    无需查询候选或填写 credential_account。规则绑定和交互选择也不使用本查询。
    本查询不用于判断固定凭据是否存在或业务工具能否执行。

    Args:
        tool_name: 当前运行时的实际 MCP 调用名，可在 get_runtime_capabilities 的 name 中找到。
        account: 可选的完整账号标识，精确匹配后用于填写 credential_account。
        keyword: 可选的账号或凭据名称片段，如 alice，用于识别完整账号。填写 account 时忽略。
    """
    user_id, capability = mounted_mcp_tool_capability(tool_name)
    if capability is None:
        return _unavailable("CREDENTIAL_TOOL_NOT_AVAILABLE", "当前用户的运行时未挂载该 MCP 工具")
    ref = capability.get(MCP_CREDENTIAL_REF)
    info = capability.get(MCP_CREDENTIAL_INFO) or {}
    mode = ref.get("selection_mode") if isinstance(ref, dict) else info.get("mode", "unknown")
    if mode != "infer":
        notes = {
            "fixed": "该工具使用固定凭据，由服务端按配置注入。请直接调用业务工具，无需查询候选或填写 credential_account。",
            "ask": "该工具由平台在实际调用时交互选择凭据。请直接调用业务工具，无需在模型侧查询候选或填写 credential_account。",
            "rule": "该工具由服务端按参数绑定规则处理凭据。请直接调用业务工具；如需选择，请根据执行结果提示处理。",
            "mixed": "该工具的固定凭据与规则绑定由服务端处理。请直接调用业务工具；如需选择，请根据执行结果提示处理。",
            "parameter_bindings": "该工具的逐参数凭据由服务端按绑定配置处理。请直接调用业务工具；本查询不判断绑定是否可用。",
            "none": "该工具没有模型账号选择参数。请直接调用业务工具；本查询不判断连接认证或凭据是否可用。",
        }
        return json.dumps({
            "status": "success", "code": "CREDENTIAL_CANDIDATES_NOT_REQUIRED",
            "credential_mode": mode, "candidate_lookup_required": False,
            "note": notes.get(mode, "该工具已挂载，但不使用本查询进行账号推导。请按公开参数直接调用业务工具；本查询不判断凭据是否可用。"),
        }, ensure_ascii=False)
    if not isinstance(ref, dict):
        return _unavailable("CREDENTIAL_PROFILE_CHANGED", "工具已挂载，但账号推导配置不可用，请重新加载工具配置")
    try:
        if not await _catalog_enabled(strict=True):
            return _unavailable("CREDENTIAL_CATALOG_DISABLED", "平台已关闭凭据目录")
    except Exception:
        return _unavailable("CREDENTIAL_QUERY_UNAVAILABLE", "凭据目录暂不可用")
    try:
        exact, digest = account_key(account) if account else ("", None)
        keyword = "" if digest else keyword.strip()
        if len(keyword) > 128:
            raise ValueError
    except (AttributeError, TypeError, ValueError):
        return _unavailable("CREDENTIAL_SELECTION_INVALID", "请输入完整账号或简短关键词")
    try:
        async with orm.AsyncSessionLocal() as db:
            profile, server, user = await mcp_profiles.pending_target(db, ref, user_id)
            credential_type = await type_catalog.get_type(db, profile.type_key)
            rows = await mcp_profiles.list_candidates(
                db, profile, server, user_id=user_id, dept_code=user.dept_code,
                digest=digest, keyword=keyword, limit=_MAX_CANDIDATES + 1,
            )
            if digest:
                rows = [row for row in rows if row.account == exact]
                if not rows and await mcp_profiles.has_visible_candidate(
                    db, profile, server, user_id=user_id, dept_code=user.dept_code, digest=digest,
                ):
                    return _unavailable("CREDENTIAL_SELECTION_INVALID", "该账号的凭据当前不可用，请检查凭据或改选")
            result = {
                "status": "success",
                "credential_type": {
                    "name": credential_type.name, "type_key": profile.type_key, "version": profile.type_version,
                },
                "items": [
                    {"name": row[0].name, "account": row.account, "owner_scope": row[0].owner_scope}
                    for row in rows[:_MAX_CANDIDATES]
                ],
                "truncated": len(rows) > _MAX_CANDIDATES,
            }
    except (mcp_profiles.CredentialInvocationError, type_catalog.CredentialTypeError) as exc:
        return _unavailable(exc.code, str(exc))
    except Exception:
        return _unavailable("CREDENTIAL_QUERY_UNAVAILABLE", "凭据查询暂不可用")
    return json.dumps(result, ensure_ascii=False)


list_mcp_credential_candidates.permission_scope = "read"
list_mcp_credential_candidates.is_read_only = True
